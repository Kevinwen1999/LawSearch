"""Scenario search: scenario -> ranked cases and legislation -> case briefs with verify links."""

import httpx
import streamlit as st

from brief_view import render_brief
from lawsearch_client import API, error_detail, fetch_cached_brief, generate_brief, post_json


@st.cache_data(ttl=3600)
def load_courts() -> list[dict]:
    return httpx.get(f"{API}/courts", timeout=30).json()


JURISDICTION_LABELS = {
    "federal": "Federal", "ontario": "Ontario", "other_province": "Other province", "unknown": "Unknown",
}


def render_fingerprint(fp: dict) -> None:
    with st.expander("What we understood from your scenario", expanded=False):
        st.markdown(f"**Jurisdiction:** {JURISDICTION_LABELS.get(fp['jurisdiction'], fp['jurisdiction'])}")
        for label, key in [
            ("Areas of law", "areas_of_law"), ("Issues", "issues"), ("Key facts", "key_facts"),
            ("Causes of action", "causes_of_action"), ("Candidate statutes", "candidate_statutes"),
            ("Search terms", "search_terms"),
        ]:
            if fp[key]:
                st.markdown(f"**{label}:** " + "; ".join(fp[key]))
        st.caption(f"Fingerprinted by {fp.get('model', fp['backend'])} via {fp['backend']}")


def section_heading(section: dict) -> str:
    note = f" — {section['marginal_note']}" if section["marginal_note"] else ""
    return f"{section['title']}, s. {section['section_label']}{note}"


def render_sections(sections: list[dict]) -> None:
    st.subheader("Relevant legislation")
    st.caption(
        "Federal legislation from Justice Laws (\"current to\" = its consolidation date); Ontario Acts "
        "from A2AJ and the Ontario regulations the case law cites from e-Laws (\"version of\" = the "
        "date of the version held here, i.e. its last amendment when loaded). Unofficial text: check "
        "the official version."
    )
    cited_by_section = st.session_state.setdefault("section_citations", {})
    for section in sections:
        with st.container(border=True):
            st.markdown(f"**{section_heading(section)}**")
            facts = [section["citation"] or section["code"]]
            if section["consolidation_date"]:
                # A2AJ gives Ontario Acts one date: the version of the Act it holds (its last
                # amendment), not a date the text was checked as current.
                if section.get("jurisdiction") == "ontario":
                    facts.append(f"version of {section['consolidation_date']}")
                else:
                    facts.append(f"current to {section['consolidation_date']}")
            if section["in_force_start"]:
                facts.append(f"in force since {section['in_force_start']}")
            facts.append(f"cited in {section['cited_by_count']:,} decisions")
            if section["citing_cases"]:
                facts.append(f"cited by {section['citing_cases']} of the top cases")
            if section["url"]:
                facts.append(f"[Official text]({section['url']})")
            st.caption(" · ".join(facts))
            with st.expander("Text"):
                st.text(section["text"].split("\n", 1)[-1])
            chunk_id = section["chunk_id"]
            if chunk_id in cited_by_section:
                with st.expander("Decisions citing this section", expanded=True):
                    for c in cited_by_section[chunk_id]:
                        st.markdown(f"- {c['citation']} · {c['court']} · {c['style_of_cause']}")
            elif section["cited_by_count"] and st.button("Decisions citing this section", key=f"citing-{chunk_id}"):
                cited_by_section[chunk_id] = httpx.get(f"{API}/sections/{chunk_id}", timeout=30).json()["citing_cases"]
                st.rerun()


def render_uncovered_regulations(regulations: list[dict]) -> None:
    st.markdown("**Cited regulations LawSearch doesn't cover**")
    st.caption("Ontario regulations that two or more of the cases below cite. Not searchable here: read them on e-Laws.")
    for r in regulations:
        st.markdown(f"- [{r['citation']}]({r['url']}) · cited by {len(r['cited_by'])} of the cases: {', '.join(r['cited_by'])}")


def render_case_statutes(case_id: str) -> None:
    statutes = st.session_state.setdefault("case_statutes", {})
    if case_id in statutes:
        with st.expander(f"Legislation cited in this decision ({len(statutes[case_id])})", expanded=True):
            if not statutes[case_id]:
                st.caption("No federal or Ontario statute sections recognized.")
            for section in statutes[case_id]:
                link = f" · [Official text]({section['url']})" if section["url"] else ""
                st.markdown(f"- {section_heading(section)}{link}")
    elif st.button("Legislation cited", key=f"statutes-{case_id}"):
        statutes[case_id] = httpx.get(f"{API}/cases/{case_id}/statutes", timeout=30).json()
        st.rerun()


def render_case(rank: int, case: dict) -> None:
    case_id = case["case_id"]
    briefs = st.session_state.setdefault("briefs", {})
    if case_id not in briefs:
        briefs[case_id] = fetch_cached_brief(case_id)

    with st.container(border=True):
        date = case["decision_date"] or "date unknown"
        st.markdown(f"#### {rank}. {case['style_of_cause']}")
        links = f"[Official text]({case['url']})" if case["url"] else "No official link"
        signals = [f"cited by {case['cited_by_count']:,} decisions in the corpus"]
        if case["citing_seeds"]:
            signals.append(f"cited by {case['citing_seeds']} of the top matches")
        st.caption(f"{case['citation']} · {case['court']} · {date} · {links} · {' · '.join(signals)}")

        with st.expander("Matching passages"):
            for passage in case["passages"]:
                if passage["para_no"] is None:
                    where = "unnumbered passage"
                elif passage["para_no"] == passage["para_end"]:
                    where = f"¶{passage['para_no']}"
                else:
                    where = f"¶{passage['para_no']}–{passage['para_end']}"
                how = " + ".join("found via citations" if m == "graph" else m for m in passage["matched_by"])
                st.markdown(f"**{where}** · {how}")
                st.text(passage["text"])

        render_case_statutes(case_id)

        brief = briefs[case_id]
        if brief:
            with st.expander("Case brief", expanded=True, icon=":material/gavel:"):
                render_brief(brief)
        elif st.button("Generate case brief", key=f"generate-{case_id}", icon=":material/gavel:"):
            with st.spinner("Reading the decision — long judgments can take a few minutes..."):
                brief, error = generate_brief(case_id)
            if brief:
                briefs[case_id] = brief
                st.rerun()
            else:
                st.error(error)
        st.page_link("app_pages/case_brief.py", label="Open on brief page", icon=":material/open_in_new:",
                     query_params={"case_id": case_id})


def render_case_groups(results: list[dict], groups: list[dict] | None, sections: list[dict] | None = None) -> None:
    """Best matches overall, then each issue's own best cases. A case already shown gets a
    one-line pointer instead of a second card; each issue also lists its own legislation."""
    by_id = {c["case_id"]: c for c in results}
    section_by_id = {x["chunk_id"]: x for x in sections or []}
    groups = groups or [{"issue": None, "case_ids": list(by_id)}]
    shown: dict[str, int] = {}
    issue_no = 0
    for n, group in enumerate(groups):
        if group["issue"] is not None:
            issue_no += 1
            st.markdown(f"##### Issue {issue_no}: {group['issue']}")
            links = []
            for chunk_id in group.get("section_ids", []):
                x = section_by_id[chunk_id]
                label = f"{x['title']} s. {x['section_label']}"
                links.append(f"[{label}]({x['url']})" if x["url"] else label)
            if links:
                st.caption("Legislation for this issue: " + "; ".join(links))
        elif n == 0 and len(groups) > 1:
            st.markdown("##### Best matches overall")
        elif n > 0:
            # The fingerprint's combined query, after the scenario text's own best matches.
            if not group["case_ids"]:
                continue
            st.markdown("##### More matches for the scenario as a whole")
        if not group["case_ids"]:
            st.caption("No decision in the corpus scored as clearly on point for this issue.")
        for case_id in group["case_ids"]:
            case = by_id[case_id]
            if case_id in shown:
                st.caption(f"↑ #{shown[case_id]} {case['style_of_cause']} ({case['citation']}), shown above")
            else:
                shown[case_id] = len(shown) + 1
                render_case(shown[case_id], case)


CANLII_NOTICES = {
    "partial": "CanLII stopped answering part-way, so some decisions below may lack details.",
    "no_seeds": "None of the top cases could be looked up on CanLII, so there was nothing to trace.",
    "disabled": "CanLII detection is off (no API key configured).",
    "budget_exhausted": "Today's CanLII query budget is used up; try again tomorrow.",
    "error": "CanLII lookup failed.",
}


def seed_case_ids(results: list[dict], groups: list[dict] | None) -> list[str]:
    """Cases to trace citations from on CanLII: round-robin across the issue groups (best overall
    first), so one dominant issue doesn't supply every seed."""
    lists = [g["case_ids"] for g in groups] if groups else [[c["case_id"] for c in results]]
    ordered: list[str] = []
    for rank in range(max((len(ids) for ids in lists), default=0)):
        for ids in lists:
            if rank < len(ids) and ids[rank] not in ordered:
                ordered.append(ids[rank])
    return ordered


def issue_labels(results: list[dict], groups: list[dict] | None) -> dict[str, str]:
    """citation -> "Issue 2, 5" for cases shown under specific issues."""
    by_id = {c["case_id"]: c for c in results}
    labels: dict[str, list[int]] = {}
    issues = [g for g in groups or [] if g["issue"] is not None]
    for n, g in enumerate(issues, 1):
        for case_id in g["case_ids"]:
            labels.setdefault(by_id[case_id]["citation"], []).append(n)
    return {c: "Issue " + ", ".join(map(str, ns)) for c, ns in labels.items()}


def render_cited(results: list[dict], groups: list[dict] | None) -> None:
    st.subheader("Frequently cited by the top cases")
    st.caption(
        "Authorities that two or more of the cases above cite but that aren't among the results, from "
        "CanLII's citator. \"Not in LawSearch\" means the decision isn't in this corpus (e.g. older "
        "Ontario trial decisions): read it on CanLII."
    )
    if st.session_state.get("cited") is None:
        with st.spinner("Checking what the top cases cite (CanLII)..."):
            st.session_state.cited = post_json(
                "/canlii/cited",
                {"seed_case_ids": seed_case_ids(results, groups),
                 "exclude_case_ids": [c["case_id"] for c in results], "k": 8},
                {"authorities": []},
            )
    body = st.session_state.cited
    if body["status"] != "ok":
        detail = f" ({body['message']})" if body.get("message") else ""
        st.info(CANLII_NOTICES.get(body["status"], "CanLII lookup failed.") + detail)
    if not body["authorities"]:
        if body["status"] in ("ok", "partial"):
            st.caption("No authority is cited by two or more of the top cases beyond those shown.")
        return
    for a in body["authorities"]:
        title = a["title"] or a["citation"]
        link = f"[{title}]({a['url']})" if a["url"] else title
        where = "in LawSearch, not in these results" if a["in_corpus"] else "not in LawSearch"
        cited_by = "; ".join(s["title"] or s["citation"] for s in a["cited_by"])
        st.markdown(f"**{link}**, {a['citation']} · *{where}*")
        st.caption(f"Cited by {len(a['cited_by'])} of the top cases: {cited_by}")


def render_canlii(query: str, results: list[dict], groups: list[dict] | None = None) -> None:
    st.subheader("Ontario Superior Court and tribunal decisions (CanLII)")
    st.caption(
        "These decisions aren't in LawSearch's corpus. They were found on CanLII because they cite "
        "the top cases above, then ranked by CanLII's own keywords for each decision. Link-out only: "
        "no text or FILAC brief here, and relevance is a lead to check, not a finding."
    )
    if st.session_state.get("canlii") is None:
        with st.spinner("Tracing citations on CanLII (up to ~20 s when nothing is cached)..."):
            try:
                response = httpx.post(
                    f"{API}/canlii/candidates",
                    json={"query": query, "seed_case_ids": seed_case_ids(results, groups), "k": 8},
                    timeout=300,
                )
                body = response.json() if response.status_code == 200 else {
                    "status": "error", "message": f"HTTP {response.status_code}", "candidates": [],
                }
            except httpx.HTTPError as exc:
                body = {"status": "error", "message": type(exc).__name__, "candidates": []}
        st.session_state.canlii = body
    body = st.session_state.canlii
    labels = issue_labels(results, groups)

    if body["status"] != "ok":
        notice = CANLII_NOTICES.get(body["status"], "CanLII lookup failed.")
        detail = f" ({body['message']})" if body.get("message") and body["status"] in ("error", "partial") else ""
        st.info(notice + detail)
    if not body["candidates"]:
        if body["status"] in ("ok", "partial"):
            st.caption("No sufficiently related Ontario decisions cite the top cases.")
        return
    for cand in body["candidates"]:
        with st.container(border=True):
            title = cand["title"] or cand["citation"] or "Untitled decision"
            st.markdown(f"**[{title}]({cand['url']})**" if cand["url"] else f"**{title}**")
            facts = [f for f in (cand["citation"], cand["court_name"], cand["decision_date"]) if f]
            cites = "; ".join(s["title"] or s["citation"] for s in cand["cites"])
            issues = sorted({labels[s["citation"]] for s in cand["cites"] if s["citation"] in labels})
            if issues:
                cites += f" ({'; '.join(issues)})"
            st.caption(" · ".join(facts) + f" · cites {cites}")
            if cand["topics"]:
                st.caption(f"Topics: {cand['topics']}")
            if cand["keywords"]:
                with st.expander("CanLII keywords"):
                    st.write(cand["keywords"])
    if body.get("queries_sent"):
        st.caption(f"Used {body['queries_sent']} CanLII queries ({body['queries_last_24h']:,}/{body['daily_limit']:,} in the last 24 h).")


with st.sidebar:
    st.header("Filters")
    try:
        courts = load_courts()
    except httpx.HTTPError:
        st.error(f"Cannot reach the LawSearch API at {API}. Start it with `uvicorn app.main:app`.")
        st.stop()
    court_names = {c["court"]: f"{c['court']} ({c['cases']:,})" for c in courts}
    selected_courts = st.multiselect("Courts and tribunals", list(court_names), format_func=court_names.get)
    k = st.slider("Best matches overall", 3, 30, 10)
    issue_k = st.slider("Cases per issue", 0, 5, 3, help="Scenario searches also search each issue found in the scenario separately.")
    st.divider()
    st.caption(
        "Coverage: federal courts and tribunals from A2AJ (Federal Court and FCA decisions start "
        "in 2001), plus ONCA (from 1998). Ontario Superior Court and tribunal decisions aren't in "
        "the corpus; for Ontario scenarios, ones that cite the top cases are listed as CanLII links."
    )

scenario = st.text_area(
    "Describe the situation",
    height=150,
    placeholder="e.g. My client was dismissed without cause and her bonus plan says she must be actively employed to receive it...",
)
uploaded_file = st.file_uploader("...or upload a scenario document", type=["pdf", "docx", "txt", "md"])
st.caption(
    "Either field works on its own; if both are filled the uploaded file is used. "
    "Courts/date filters above apply once results come back — the fingerprint step decides "
    "whether to search at all (e.g. other provinces aren't covered yet)."
)

if st.button("Search", type="primary", disabled=not (scenario.strip() or uploaded_file)):
    with st.spinner("Reading the scenario and searching..."):
        data = {"k": k, "issue_k": issue_k, "courts": selected_courts or []}
        if uploaded_file is not None:
            files = {"file": (uploaded_file.name, uploaded_file.getvalue())}
        else:
            files = None
            data["text"] = scenario
        response = httpx.post(f"{API}/scenarios", data=data, files=files, timeout=180)
    if response.status_code == 200:
        body = response.json()
        st.session_state.fingerprint = body["fingerprint"]
        st.session_state.gate = body["gate"]
        st.session_state.canlii = None
        st.session_state.cited = None
        if body["results"]:
            st.session_state.results = body["results"]["results"]
            st.session_state.sections = body["results"]["sections"]
            st.session_state.search_query = body["results"]["query"]
            st.session_state.groups = body["results"]["groups"]
            st.session_state.regulations = body.get("uncovered_regulations", [])
        else:
            st.session_state.results, st.session_state.sections = None, None
    else:
        st.session_state.fingerprint, st.session_state.gate = None, None
        st.session_state.results, st.session_state.sections = None, None
        st.error(f"Search failed: {error_detail(response)}")

fingerprint = st.session_state.get("fingerprint")
if fingerprint:
    render_fingerprint(fingerprint)

gate = st.session_state.get("gate")
if gate and gate["status"] == "unsupported_jurisdiction":
    st.warning(gate["message"])
elif gate and gate["status"] == "needs_clarification":
    st.info(f"Before searching, it would help to know: {gate['message']}")
elif gate and gate["message"]:
    st.caption(f"Worth confirming (the search went ahead without it): {gate['message']}")

results = st.session_state.get("results")
if results is not None:
    if st.session_state.get("sections"):
        groups = st.session_state.get("groups")
        overall = set(groups[0].get("section_ids", [])) if groups else None
        # Issue-specific sections are listed under each issue below, not as cards here.
        render_sections([x for x in st.session_state.sections if overall is None or x["chunk_id"] in overall])
    if st.session_state.get("regulations"):
        render_uncovered_regulations(st.session_state.regulations)
    st.subheader("Cases")
    if not results:
        st.info("No matching cases.")
    render_case_groups(results, st.session_state.get("groups"), st.session_state.get("sections"))
    if results:
        render_cited(results, st.session_state.get("groups"))
    if results and fingerprint and fingerprint["jurisdiction"] == "ontario":
        render_canlii(st.session_state.search_query, results, st.session_state.get("groups"))
