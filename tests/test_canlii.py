from contextlib import contextmanager

import httpx
import pytest

from app.canlii import BudgetExhausted, CanLIIClient, CanLIIError, NotConfigured, Response
from app.canlii_detect import MIN_RERANK, Seed, detect, pool, to_seed

KEY = "secret-key"
DATABASES = {
    "caseDatabases": [
        {"databaseId": "onsc", "jurisdiction": "on", "name": "Superior Court of Justice"},
        {"databaseId": "onscdc", "jurisdiction": "on", "name": "Divisional Court"},
        {"databaseId": "onca", "jurisdiction": "on", "name": "Court of Appeal for Ontario"},
        {"databaseId": "bcsc", "jurisdiction": "bc", "name": "Supreme Court of British Columbia"},
        {"databaseId": "csc-scc", "jurisdiction": "ca", "name": "Supreme Court of Canada"},
    ]
}


class FakeStore:
    def __init__(self, daily_limit_used: int = 0):
        self.cache: dict[str, Response] = {}
        self.used = daily_limit_used

    def cached(self, path, max_age):
        return self.cache.get(path)

    def save(self, path, response):
        self.cache[path] = response

    @contextmanager
    def request_slot(self):
        def wait_turn(min_interval, daily_limit):
            if self.used >= daily_limit:
                raise BudgetExhausted("used up")
            self.used += 1

        yield wait_turn

    def used_today(self):
        return self.used


def client(handler, store=None, **kw) -> CanLIIClient:
    return CanLIIClient(
        store or FakeStore(), kw.pop("api_key", KEY), http=httpx.Client(transport=httpx.MockTransport(handler)),
        min_interval=0, daily_limit=kw.pop("daily_limit", 100), sleep=lambda s: None,
    )


def citing(*entries):
    return {"citingCases": [
        {"databaseId": db, "caseId": {"en": cid}, "title": f"T {cid}", "citation": cid,
         "longUrl": "https://example", "aiContentId": {"en": cid}}
        for db, cid in entries
    ]}


def test_responses_are_cached_and_citator_entries_trimmed():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=citing(("onsc", "2021onsc1")))

    c = client(handler)
    first = c.citing_cases("onca", "2020onca391")
    assert c.citing_cases("onca", "2020onca391") == first
    assert first == [{"databaseId": "onsc", "caseId": "2021onsc1", "title": "T 2021onsc1", "citation": "2021onsc1"}]
    assert calls == ["/v1/caseCitator/en/onca/2020onca391/citingCases"]
    assert c.queries_sent == 1


def test_unknown_case_is_cached_as_missing():
    c = client(lambda r: httpx.Response(404, json=[{"error": "MISSING"}]))
    assert c.case_metadata("onca", "2019onca99999") is None
    assert c.store.cache["/caseBrowse/en/onca/2019onca99999/"].status == 404


def test_throttled_request_is_retried_and_every_attempt_counted():
    replies = iter([httpx.Response(429, text='{"error": THROTTLED}'), httpx.Response(200, json={"title": "X"})])
    store = FakeStore()
    c = client(lambda r: next(replies), store)
    assert c.case_metadata("onsc", "2021onsc1") == {"title": "X"}
    assert store.used == 2 and c.queries_sent == 2


def test_budget_is_enforced_before_sending():
    c = client(lambda r: pytest.fail("request sent over budget"), FakeStore(daily_limit_used=5), daily_limit=5)
    with pytest.raises(BudgetExhausted):
        c.case_metadata("onsc", "2021onsc1")


def test_errors_never_leak_the_api_key():
    c = client(lambda r: httpx.Response(500, text=f"boom {r.url}"))
    with pytest.raises(CanLIIError) as exc:
        c.case_metadata("onsc", "2021onsc1")
    assert KEY not in str(exc.value)


def test_missing_key_only_matters_on_a_cache_miss():
    store = FakeStore()
    store.cache["/caseBrowse/en/onsc/2021onsc1/"] = Response(200, {"title": "cached"})
    c = client(lambda r: pytest.fail("no key, no request"), store, api_key="")
    assert c.case_metadata("onsc", "2021onsc1") == {"title": "cached"}
    with pytest.raises(NotConfigured):
        c.case_metadata("onsc", "2021onsc2")


@pytest.mark.parametrize(
    "citation, court, expected",
    [
        ("2019 SCC 65", "SCC", ("csc-scc", "2019scc65")),
        ("2020 ONCA 391", "ONCA", ("onca", "2020onca391")),
        ("2015 FC 12", "FC", ("fct", "2015fc12")),
        ("[1992] 1 SCR 986", "SCC", None),   # report citation: CanLII id not derivable
        ("C36847", "ONCA", None),            # docket number only
        ("2020 ONCA 391", "SCC", None),      # court mismatch
        ("2020 RAD 5", "RAD", None),         # not a seed court
    ],
)
def test_seed_mapping(citation, court, expected):
    seed = to_seed(citation, court, "A v B")
    assert (seed and (seed.database_id, seed.case_id)) == expected


def seed(n: int) -> Seed:
    return Seed(f"20{n:02d} ONCA {n}", f"Seed {n}", "onca", f"20{n:02d}onca{n}")


def entry(db, cid):
    return {"databaseId": db, "caseId": cid, "title": cid, "citation": cid}


def test_pool_prefers_co_citation_and_discounts_generic_seeds():
    specific, generic = seed(1), seed(2)
    citing_by_seed = [
        (specific, [entry("onsc", "2021onsc1"), entry("onsc", "2021onsc2"), entry("bcsc", "2021bcsc1")]),
        (generic, [entry("onsc", "2021onsc2")] + [entry("onscdc", f"2022onsc{i}") for i in range(100, 200)]),
    ]
    ranked = pool(citing_by_seed, {"onsc", "onscdc"}, size=3)
    assert [c.case_id for c in ranked] == ["2021onsc2", "2021onsc1", "2022onsc100"]
    assert [s.citation for s in ranked[0].cites] == [specific.citation, generic.citation]
    assert all(c.database_id != "bcsc" for c in ranked)


def fake_api(citator: dict, metadata: dict, *, fail_metadata_after: int | None = None):
    sent = {"metadata": 0}

    def handler(request):
        path = request.url.path
        if path == "/v1/caseBrowse/en/":
            return httpx.Response(200, json=DATABASES)
        if "/caseCitator/" in path:
            case_id = path.split("/")[-2]
            return httpx.Response(200, json=citing(*citator[case_id])) if case_id in citator else httpx.Response(404, json=[])
        sent["metadata"] += 1
        if fail_metadata_after is not None and sent["metadata"] > fail_metadata_after:
            return httpx.Response(503)
        case_id = path.rstrip("/").split("/")[-1]
        return httpx.Response(200, json=metadata[case_id])

    return handler


def meta(case_id, keywords):
    return {"title": f"Title {case_id}", "citation": f"{case_id} (CanLII)", "url": f"https://canlii.ca/t/{case_id}",
            "decisionDate": "2021-05-01", "keywords": keywords, "topics": "Labour and employment"}


def keyword_score(query, texts):
    return [5.0 if "termination" in t.lower() else MIN_RERANK - 1 for t in texts]


def test_detect_links_out_to_ontario_candidates_above_the_floor():
    handler = fake_api(
        {"2020onca391": [("onsc", "2021onsc1"), ("onsc", "2021onsc2"), ("onca", "2022onca9"), ("bcsc", "2021bcsc1")]},
        {"2021onsc1": meta("2021onsc1", "Termination clause — ESA"), "2021onsc2": meta("2021onsc2", "Charter — s. 24(2)")},
    )
    seeds = [to_seed("2020 ONCA 391", "ONCA", "Waksdale"), to_seed("2025 ONCA 1", "ONCA", "Not on CanLII")]
    result = detect(client(handler), "termination clause", seeds, keyword_score)
    assert result.status == "ok"
    assert [c.case_id for c in result.candidates] == ["2021onsc1"]
    top = result.candidates[0]
    assert top.url == "https://canlii.ca/t/2021onsc1"
    assert top.court_name == "Superior Court of Justice"
    assert [s.citation for s in top.cites] == ["2020 ONCA 391"]


def test_detect_returns_partial_results_when_metadata_fails_midway():
    handler = fake_api(
        {"2020onca391": [("onsc", "2021onsc1"), ("onsc", "2021onsc2")]},
        {"2021onsc1": meta("2021onsc1", "Termination clause"), "2021onsc2": meta("2021onsc2", "Termination too")},
        fail_metadata_after=1,
    )
    result = detect(client(handler), "q", [to_seed("2020 ONCA 391", "ONCA", "Waksdale")],
                    lambda q, texts: [0.0] * len(texts))
    assert result.status == "partial" and "HTTP 503" in result.message
    by_id = {c.case_id: c for c in result.candidates}
    assert set(by_id) == {"2021onsc1", "2021onsc2"}
    # No metadata for the second one, but it still links out.
    assert by_id["2021onsc2"].url == "https://www.canlii.org/en/on/onsc/doc/2021/2021onsc2/2021onsc2.html"


def test_detect_without_key_or_seeds():
    no_key = client(lambda r: pytest.fail("no request expected"), api_key="")
    assert detect(no_key, "q", [seed(1)], keyword_score).status == "disabled"
    assert detect(client(lambda r: pytest.fail("no request")), "q", [], keyword_score).status == "no_seeds"
