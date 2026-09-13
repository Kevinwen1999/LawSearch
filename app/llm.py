"""Structured-output LLM backends behind one interface.

- ClaudeCliBackend: `claude -p` headless, on whatever the Claude Code CLI is logged in with
  (e.g. a claude.ai subscription). Suited to low-volume local testing, not serving others.
- AnthropicApiBackend: the Anthropic SDK with ANTHROPIC_API_KEY.

Both take the same system prompt, instruction, document and JSON schema, so callers switch
with the FILAC_BACKEND setting alone.
"""

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from app.config import settings


class LLMError(RuntimeError):
    pass


@dataclass
class StructuredResult:
    data: dict
    backend: str
    model: str
    usage: dict


class StructuredBackend(Protocol):
    name: str

    def extract(self, *, system: str, instruction: str, document: str, schema: dict) -> StructuredResult: ...


class ClaudeCliBackend:
    name = "claude-cli"

    def extract(self, *, system: str, instruction: str, document: str, schema: dict) -> StructuredResult:
        cmd = [
            settings.claude_cli_path, "-p", instruction,
            "--model", settings.filac_model,
            "--effort", settings.filac_effort,
            "--system-prompt", system,
            "--json-schema", json.dumps(schema),
            "--output-format", "json",
            "--tools", "",
            "--setting-sources", "",
            "--no-session-persistence",
        ]
        # .env may define ANTHROPIC_API_KEY (even empty) for the API backend; the CLI would
        # prefer it over its own login, so strip it to keep this backend on the CLI's account.
        env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
        try:
            proc = subprocess.run(
                cmd,
                input=document,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=settings.llm_timeout_seconds,
                # Neutral working directory so no project CLAUDE.md or memory enters the prompt.
                cwd=tempfile.gettempdir(),
            )
        except FileNotFoundError as exc:
            raise LLMError(f"Claude Code CLI not found at {settings.claude_cli_path!r}") from exc
        except subprocess.TimeoutExpired as exc:
            raise LLMError(f"claude -p timed out after {settings.llm_timeout_seconds}s") from exc
        return parse_cli_output(proc.returncode, proc.stdout, proc.stderr)


def parse_cli_output(returncode: int, stdout: str, stderr: str) -> StructuredResult:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        raise LLMError(f"claude -p exited {returncode} without JSON output: {(stderr or stdout)[:500]}") from None

    if returncode != 0 or payload.get("is_error") or payload.get("subtype") != "success":
        detail = payload.get("result") or payload.get("api_error_status") or stderr
        raise LLMError(f"claude -p failed ({payload.get('subtype')}): {str(detail)[:500]}")

    data = payload.get("structured_output")
    if not isinstance(data, dict):
        raise LLMError("claude -p returned no structured_output")

    # The CLI also makes small side calls on other models; the main call is the costliest.
    model_usage = payload.get("modelUsage") or {}
    model, main = max(model_usage.items(), key=lambda kv: kv[1].get("costUSD", 0), default=("unknown", {}))
    usage = {
        "input_tokens": sum(main.get(k, 0) for k in ("inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")),
        "output_tokens": main.get("outputTokens", 0),
        "served_by": model,
        "equivalent_cost_usd": payload.get("total_cost_usd"),
        "duration_ms": payload.get("duration_ms"),
    }
    return StructuredResult(data, "claude-cli", model, usage)


class AnthropicApiBackend:
    name = "api"

    def __init__(self, client=None):
        import anthropic

        self._anthropic = anthropic
        self._client = client or anthropic.Anthropic(timeout=settings.llm_timeout_seconds)

    def extract(self, *, system: str, instruction: str, document: str, schema: dict) -> StructuredResult:
        # No prompt caching: each case is briefed once and stored, so cache writes would add
        # cost without later reads.
        try:
            with self._client.beta.messages.stream(**self.request(system, instruction, document, schema)) as stream:
                message = stream.get_final_message()
        except self._anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except self._anthropic.APIConnectionError as exc:
            raise LLMError(f"Anthropic API unreachable: {exc}") from exc

        if message.stop_reason == "refusal":
            details = message.stop_details
            reason = f"{details.category}: {details.explanation}" if details else "no details"
            raise LLMError(f"model declined the request ({reason})")
        if message.stop_reason == "max_tokens":
            raise LLMError("brief was cut off at max_tokens")

        text = next((block.text for block in message.content if block.type == "text"), None)
        if text is None:
            raise LLMError("API response had no text block")
        usage = {
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
            "served_by": message.model,
        }
        return StructuredResult(json.loads(text), "api", message.model, usage)

    @staticmethod
    def request(system: str, instruction: str, document: str, schema: dict) -> dict:
        return {
            "model": settings.filac_model,
            "max_tokens": 64000,
            # Judgments describe violence and crime; a server-side fallback keeps an occasional
            # classifier decline from failing the brief.
            "betas": ["server-side-fallback-2026-07-01"],
            "fallbacks": "default",
            "thinking": {"type": "adaptive"},
            "output_config": {
                "effort": settings.filac_effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            "system": system,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": document},
                    {"type": "text", "text": instruction},
                ],
            }],
        }


@lru_cache(maxsize=1)
def get_backend() -> StructuredBackend:
    if settings.filac_backend == "api":
        return AnthropicApiBackend()
    return ClaudeCliBackend()
