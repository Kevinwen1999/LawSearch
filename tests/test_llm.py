import json
from types import SimpleNamespace

import httpx
import pytest

from app import llm
from app.llm import (
    AnthropicApiBackend,
    LLMError,
    LmStudioBackend,
    StructuredResult,
    extract_with_fallback,
    get_backend,
    parse_cli_output,
)

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["x"], "properties": {"x": {"type": "string"}}}


def cli_payload(**overrides) -> str:
    payload = {
        "type": "result", "subtype": "success", "is_error": False,
        "structured_output": {"x": "ok"}, "total_cost_usd": 0.08, "duration_ms": 2400,
        "modelUsage": {
            "claude-haiku-4-5-20251001": {"inputTokens": 984, "outputTokens": 14, "costUSD": 0.001},
            "claude-opus-5": {"inputTokens": 2, "cacheCreationInputTokens": 7239, "outputTokens": 191, "costUSD": 0.077},
        },
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_cli_output_uses_structured_output_and_main_model_usage():
    result = parse_cli_output(0, cli_payload(), "")

    assert result.data == {"x": "ok"}
    assert result.model == "claude-opus-5"
    assert result.usage["input_tokens"] == 7241
    assert result.usage["output_tokens"] == 191


@pytest.mark.parametrize("returncode, stdout, message", [
    (1, "not json", "without JSON"),
    (0, cli_payload(is_error=True, subtype="error_during_execution", result="boom"), "boom"),
    (0, cli_payload(structured_output=None), "no structured_output"),
])
def test_cli_failures_raise_llm_error(returncode, stdout, message):
    with pytest.raises(LLMError, match=message):
        parse_cli_output(returncode, stdout, "")


class FakeStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


def fake_client(message, captured: dict):
    def stream(**kwargs):
        captured.update(kwargs)
        return FakeStream(message)

    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))


def api_message(stop_reason="end_turn", text='{"x": "ok"}', stop_details=None):
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=stop_details,
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=5000, output_tokens=900),
        model="claude-opus-5",
    )


def test_api_backend_sends_schema_fallbacks_and_document():
    captured: dict = {}
    backend = AnthropicApiBackend(client=fake_client(api_message(), captured))

    result = backend.extract(
        system="sys", instruction="brief it", document="[1] text", schema=SCHEMA,
        model="claude-opus-5", effort="high",
    )

    assert result.data == {"x": "ok"}
    assert result.usage == {"input_tokens": 5000, "output_tokens": 900, "served_by": "claude-opus-5"}
    assert captured["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}
    assert captured["fallbacks"] == "default"
    assert captured["betas"] == ["server-side-fallback-2026-07-01"]
    assert captured["thinking"] == {"type": "adaptive"}
    assert [b["text"] for b in captured["messages"][0]["content"]] == ["[1] text", "brief it"]


def test_api_backend_raises_on_refusal_and_truncation():
    details = SimpleNamespace(category="violence", explanation="declined")
    for message, match in [
        (api_message(stop_reason="refusal", stop_details=details), "declined"),
        (api_message(stop_reason="max_tokens"), "cut off"),
    ]:
        backend = AnthropicApiBackend(client=fake_client(message, {}))
        with pytest.raises(LLMError, match=match):
            backend.extract(system="s", instruction="i", document="d", schema=SCHEMA, model="claude-opus-5", effort="high")


def lmstudio_response(**overrides) -> httpx.Response:
    body = {
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": json.dumps({"x": "ok"}), "reasoning_content": "thinking..."},
        }],
        "usage": {"prompt_tokens": 120, "completion_tokens": 40},
    }
    body.update(overrides)
    return httpx.Response(200, json=body, request=httpx.Request("POST", "http://x/chat/completions"))


def test_lmstudio_backend_sends_json_schema_and_parses_content(monkeypatch):
    captured: dict = {}

    def fake_post(url, *, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return lmstudio_response()

    monkeypatch.setattr(httpx, "post", fake_post)

    result = LmStudioBackend().extract(
        system="sys", instruction="brief it", document="[1] text", schema=SCHEMA,
        model="qwen/qwen3.8-27b", effort="high",
    )

    assert result.data == {"x": "ok"}
    assert result.backend == "lmstudio"
    assert result.usage == {"input_tokens": 120, "output_tokens": 40, "served_by": "qwen/qwen3.8-27b"}
    assert captured["url"].endswith("/chat/completions")
    assert captured["json"]["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "structured_output", "strict": True, "schema": SCHEMA},
    }
    assert captured["json"]["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "[1] text\n\nbrief it"},
    ]
    assert captured["json"]["reasoning_effort"] == "high"


def test_lmstudio_backend_maps_efforts_it_does_not_have(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(httpx, "post", lambda url, *, json, timeout: captured.update(json) or lmstudio_response())

    LmStudioBackend().extract(system="s", instruction="i", document="d", schema=SCHEMA, model="m", effort="max")

    assert captured["reasoning_effort"] == "high"


def test_lmstudio_backend_raises_on_length_cutoff(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: lmstudio_response(
        choices=[{"finish_reason": "length", "message": {"content": ""}}]
    ))

    with pytest.raises(LLMError, match="cut off"):
        LmStudioBackend().extract(
            system="s", instruction="i", document="d", schema=SCHEMA, model="m", effort="high"
        )


def test_lmstudio_backend_raises_when_unreachable(monkeypatch):
    def fake_post(*a, **k):
        raise httpx.ConnectError("refused", request=httpx.Request("POST", "http://x"))

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(LLMError, match="unreachable"):
        LmStudioBackend().extract(
            system="s", instruction="i", document="d", schema=SCHEMA, model="m", effort="high"
        )


def test_get_backend_selects_by_name():
    assert get_backend("lmstudio") is get_backend("lmstudio")
    assert isinstance(get_backend("lmstudio"), LmStudioBackend)


class FakeNamedBackend:
    """Records which (backend name, model) called it; raises for names in `fails`."""

    def __init__(self, fails: set[str] = frozenset()):
        self.fails = fails
        self.calls: list[tuple[str, str]] = []

    def make(self, name: str):
        def extract(**kwargs):
            self.calls.append((name, kwargs["model"]))
            if name in self.fails:
                raise LLMError(f"{name} is down")
            return StructuredResult({"x": "ok"}, name, kwargs["model"], {})

        return extract


def _install(monkeypatch, fake: FakeNamedBackend):
    monkeypatch.setattr(llm, "get_backend", lambda name: SimpleNamespace(extract=fake.make(name)))


def test_fallback_not_used_when_primary_succeeds(monkeypatch):
    fake = FakeNamedBackend()
    _install(monkeypatch, fake)

    result = extract_with_fallback(
        system="s", instruction="i", document="d", schema=SCHEMA,
        backend="lmstudio", model="local-model", effort="low",
        fallback_backend="claude-cli", fallback_model="claude-sonnet-5", fallback_effort="low",
    )

    assert result.data == {"x": "ok"}
    assert result.backend == "lmstudio"
    assert fake.calls == [("lmstudio", "local-model")]


def test_fallback_used_when_primary_raises(monkeypatch):
    fake = FakeNamedBackend(fails={"lmstudio"})
    _install(monkeypatch, fake)

    result = extract_with_fallback(
        system="s", instruction="i", document="d", schema=SCHEMA,
        backend="lmstudio", model="local-model", effort="low",
        fallback_backend="claude-cli", fallback_model="claude-sonnet-5", fallback_effort="low",
    )

    assert result.backend == "claude-cli"
    assert result.model == "claude-sonnet-5"
    assert fake.calls == [("lmstudio", "local-model"), ("claude-cli", "claude-sonnet-5")]


def test_fallback_reraises_when_both_fail(monkeypatch):
    fake = FakeNamedBackend(fails={"lmstudio", "claude-cli"})
    _install(monkeypatch, fake)

    with pytest.raises(LLMError, match="claude-cli is down"):
        extract_with_fallback(
            system="s", instruction="i", document="d", schema=SCHEMA,
            backend="lmstudio", model="local-model", effort="low",
            fallback_backend="claude-cli", fallback_model="claude-sonnet-5", fallback_effort="low",
        )

    assert fake.calls == [("lmstudio", "local-model"), ("claude-cli", "claude-sonnet-5")]


def test_fallback_skipped_when_identical_to_primary(monkeypatch):
    """FILAC's default fallback is the same backend+model as primary — a no-op, not a retry."""
    fake = FakeNamedBackend(fails={"claude-cli"})
    _install(monkeypatch, fake)

    with pytest.raises(LLMError, match="claude-cli is down"):
        extract_with_fallback(
            system="s", instruction="i", document="d", schema=SCHEMA,
            backend="claude-cli", model="claude-opus-5", effort="high",
            fallback_backend="claude-cli", fallback_model="claude-opus-5", fallback_effort="high",
        )

    assert fake.calls == [("claude-cli", "claude-opus-5")]
