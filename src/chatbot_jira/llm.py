"""
T05: LLM adapter — gọi Ollama/OpenAI-compatible API để soạn Draft.

Quy tắc:
- Nhận message + conversation history → trả Draft JSON.
- Backend kiểm soát prompt; LLM không tự chọn tool, URL hoặc token.
- Fake LLM (FakeLlmAdapter) dùng trong unit tests, không cần API thật.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Protocol

import httpx2

from .models import Draft, DraftAction, TicketLanguage, ValidationError


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a Jira ticket assistant. Your ONLY job is to help users create Jira tickets.

Given the conversation so far, output a single JSON object with these fields:
- "action": one of "create", "clarify", or "unsupported"
- "summary": (string, required if action=create) ticket title, max 255 chars
- "description": (string, required if action=create) ticket body in markdown
- "ticket_language": "vi" or "en" (required if action=create; null otherwise)
- "questions": list of strings (required if action=clarify; else empty list [])

Rules:
1. Use action=create when the user provides enough information to write a meaningful ticket.
2. Use action=clarify when essential content is missing (e.g. "fix a bug" with no details). Ask only what is strictly necessary. Do NOT ask for project, issue type, assignee, sprint, acceptance criteria unless the user explicitly mentions them.
3. Use action=unsupported for greetings, chitchat, requests to edit/delete/comment on existing tickets, or "ok"/"thanks" after a ticket was just created.
4. Detect ticket language: use the primary language of the task content. If the user explicitly says "write the ticket in English" (or Vietnamese), honour that instruction even if the input is in the other language. Preserve technical identifiers (API names, variable names, issue keys, error messages) as-is in both languages.
5. Do NOT invent: deadline, assignee, root cause, acceptance criteria, sprint, or business requirements that were not provided.
6. Clarification questions must use the same language the user is currently writing in.
7. For mixed Vietnamese-English input: pick the dominant language of the task content; if unclear, use the draft's existing ticket_language, or ask a short question before the first draft.
8. Once ticket_language is chosen, keep it through follow-up clarification turns unless the user explicitly requests a change.
9. After a ticket is created (user says "ok", "created", "done", etc.) — return action=unsupported.

Output ONLY the raw JSON object. No markdown fences, no explanation.
"""


# ---------------------------------------------------------------------------
# Protocol / interface
# ---------------------------------------------------------------------------

class LlmAdapter(Protocol):
    async def complete(
        self,
        messages: list[dict[str, str]],
    ) -> Draft:
        ...


# ---------------------------------------------------------------------------
# Ollama / OpenAI-compatible HTTP adapter
# ---------------------------------------------------------------------------

class HttpLlmAdapter:
    """Calls an OpenAI-compatible /chat/completions endpoint (Ollama, OpenAI, etc.)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout

    async def complete(self, messages: list[dict[str, str]]) -> Draft:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
            "stream": False,
        }

        async with httpx2.AsyncClient(timeout=self._timeout) as http:
            resp = await http.post(
                f"{self._base_url}/chat/completions",
                headers=headers,
                json=payload,
            )

        if resp.status_code == 401:
            raise LlmError("LLM authentication failed — check API key")
        if resp.status_code == 429:
            raise LlmError("LLM rate-limited — please retry later")
        if resp.status_code >= 500:
            raise LlmError(f"LLM server error: HTTP {resp.status_code}")
        if not resp.is_success:
            raise LlmError(f"LLM request failed: HTTP {resp.status_code}")

        try:
            body = resp.json()
            raw_text: str = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise LlmError("Unexpected LLM response shape") from exc

        return _parse_draft(raw_text)


# ---------------------------------------------------------------------------
# Fake adapter for unit tests
# ---------------------------------------------------------------------------

class FakeLlmAdapter:
    """Deterministic adapter for unit tests.  Responses keyed by last user message."""

    def __init__(self, responses: dict[str, Draft | Exception]) -> None:
        self._responses = responses

    async def complete(self, messages: list[dict[str, str]]) -> Draft:
        last = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"),
            "",
        )
        result = self._responses.get(last)
        if result is None:
            raise LlmError(f"FakeLlmAdapter: no response configured for message {last!r}")
        if isinstance(result, Exception):
            raise result
        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class LlmError(RuntimeError):
    """Any error communicating with or parsing LLM response."""


def _parse_draft(text: str) -> Draft:
    """Parse raw LLM text into a validated Draft."""
    text = text.strip()
    # Strip accidental markdown fences
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(
            line for line in lines if not line.startswith("```")
        ).strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise LlmError(f"LLM did not return valid JSON: {text[:200]!r}") from exc
    try:
        return Draft.from_dict(data)
    except ValidationError as exc:
        raise LlmError(f"LLM JSON failed schema validation: {exc}") from exc


# ---------------------------------------------------------------------------
# Factory from environment
# ---------------------------------------------------------------------------

def llm_adapter_from_env(env: dict[str, str]) -> HttpLlmAdapter:
    base_url = (
        env.get("LLM_BASE_URL")
        or env.get("OLLAMA_BASE_URL")
        or "http://localhost:11434/v1"
    ).strip()
    model = env.get("LLM_MODEL", "gemma4:31b").strip()
    api_key = (
        env.get("LLM_API_KEY")
        or env.get("OLLAMA_API_KEY")
        or ""
    ).strip() or None
    try:
        timeout = float(env.get("LLM_TIMEOUT_SECONDS", "60"))
    except ValueError:
        timeout = 60.0
    return HttpLlmAdapter(base_url=base_url, model=model, api_key=api_key, timeout=timeout)
