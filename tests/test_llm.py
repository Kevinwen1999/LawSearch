import json
from types import SimpleNamespace

import pytest

from app.llm import AnthropicApiBackend, LLMError, parse_cli_output

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

    result = backend.extract(system="sys", instruction="brief it", document="[1] text", schema=SCHEMA)

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
            backend.extract(system="s", instruction="i", document="d", schema=SCHEMA)
