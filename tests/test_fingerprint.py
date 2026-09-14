from types import SimpleNamespace

from app import fingerprint, llm
from app.fingerprint import Fingerprint, check_jurisdiction, search_query
from app.llm import StructuredResult


def make_fp(**overrides) -> Fingerprint:
    data = {
        "jurisdiction": "federal",
        "areas_of_law": ["immigration"],
        "issues": ["whether the officer's decision was reasonable"],
        "key_facts": ["application refused for insufficient evidence of funds"],
        "causes_of_action": [],
        "candidate_statutes": ["Immigration and Refugee Protection Act"],
        "search_terms": ["reasonableness review", "study permit refusal"],
        "needs_clarification": [],
    }
    data.update(overrides)
    return Fingerprint(**data, model="m", backend="b", usage={})


def test_generate_calls_backend_with_settings_and_builds_fingerprint(monkeypatch):
    captured = {}

    class FakeBackend:
        def extract(self, **kwargs):
            captured.update(kwargs)
            return StructuredResult(
                data={
                    "jurisdiction": "federal", "areas_of_law": ["tax"], "issues": ["issue"],
                    "key_facts": ["fact"], "causes_of_action": [], "candidate_statutes": [],
                    "search_terms": ["term"], "needs_clarification": [],
                },
                backend="lmstudio", model="qwen/qwen3.8-27b", usage={"input_tokens": 1, "output_tokens": 2},
            )

    monkeypatch.setattr(llm, "get_backend", lambda name: FakeBackend())

    fp = fingerprint.generate("a tenant's roof leaked for six months")

    assert captured["document"] == "a tenant's roof leaked for six months"
    assert captured["schema"] == fingerprint.FINGERPRINT_SCHEMA
    assert captured["model"] == fingerprint.settings.fingerprint_model
    assert captured["effort"] == fingerprint.settings.fingerprint_effort
    assert fp.jurisdiction == "federal"
    assert fp.areas_of_law == ["tax"]


def test_generate_falls_back_to_cloud_when_local_backend_fails(monkeypatch):
    calls = []

    def make_backend(name):
        def extract(**kwargs):
            calls.append(name)
            if name == fingerprint.settings.fingerprint_backend:
                raise llm.LLMError("LM Studio unreachable")
            return StructuredResult(
                data={
                    "jurisdiction": "federal", "areas_of_law": [], "issues": [], "key_facts": [],
                    "causes_of_action": [], "candidate_statutes": [], "search_terms": [],
                    "needs_clarification": [],
                },
                backend=name, model=kwargs["model"], usage={},
            )
        return SimpleNamespace(extract=extract)

    monkeypatch.setattr(llm, "get_backend", make_backend)

    fp = fingerprint.generate("a scenario")

    assert calls == [fingerprint.settings.fingerprint_backend, fingerprint.settings.fingerprint_fallback_backend]
    assert fp.backend == fingerprint.settings.fingerprint_fallback_backend
    assert fp.model == fingerprint.settings.fingerprint_fallback_model


def test_search_query_puts_precise_terms_before_prose():
    fp = make_fp()

    query = search_query(fp)

    assert query == (
        "reasonableness review; study permit refusal; Immigration and Refugee Protection Act; "
        "whether the officer's decision was reasonable; "
        "application refused for insufficient evidence of funds"
    )


def test_search_query_falls_back_to_areas_of_law_when_nothing_else_present():
    fp = make_fp(search_terms=[], candidate_statutes=[], issues=[], key_facts=[])

    assert search_query(fp) == "immigration"


def test_gate_ok_for_federal_with_no_clarification_needed():
    fp = make_fp(jurisdiction="federal", needs_clarification=[])

    gate = check_jurisdiction(fp)

    assert gate.status == "ok"
    assert gate.message is None


def test_gate_flags_unsupported_ontario_jurisdiction():
    fp = make_fp(jurisdiction="ontario")

    gate = check_jurisdiction(fp)

    assert gate.status == "unsupported_jurisdiction"
    assert "ontario" in gate.message.lower()


def test_gate_flags_unsupported_other_province_jurisdiction():
    fp = make_fp(jurisdiction="other_province")

    gate = check_jurisdiction(fp)

    assert gate.status == "unsupported_jurisdiction"


def test_gate_asks_for_clarification_when_jurisdiction_unstated_and_matters():
    fp = make_fp(jurisdiction="unknown", needs_clarification=["province not stated"])

    gate = check_jurisdiction(fp)

    assert gate.status == "needs_clarification"
    assert gate.message == "province not stated"


def test_gate_prefers_unsupported_jurisdiction_over_clarification():
    fp = make_fp(jurisdiction="ontario", needs_clarification=["something else"])

    gate = check_jurisdiction(fp)

    assert gate.status == "unsupported_jurisdiction"


def test_gate_ok_when_jurisdiction_unknown_but_no_clarification_flagged():
    fp = make_fp(jurisdiction="unknown", needs_clarification=[])

    gate = check_jurisdiction(fp)

    assert gate.status == "ok"
