"""
T04: Schema contract cho chat/draft/result.

Draft LLM trả về:  action, summary?, description?, ticket_language, questions[].
ChatResult backend: status, issue_key?, issue_url?, message?, request_id.
"""
from __future__ import annotations

from enum import Enum
from typing import Any
import re


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class DraftAction(str, Enum):
    CREATE = "create"
    CLARIFY = "clarify"
    UNSUPPORTED = "unsupported"


class TicketLanguage(str, Enum):
    VI = "vi"
    EN = "en"


class ChatStatus(str, Enum):
    CLARIFICATION_REQUIRED = "clarification_required"
    CREATED = "created"
    FAILED = "failed"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

MAX_SUMMARY_LEN = 255
MAX_DESCRIPTION_LEN = 32_000  # safe limit before Jira API rejects


class ValidationError(ValueError):
    """Schema/content validation failure."""


def _check_str(value: Any, name: str, *, max_len: int | None = None,
               allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be a string, got {type(value).__name__}")
    if not allow_empty and not value.strip():
        raise ValidationError(f"{name} must not be empty")
    if max_len is not None and len(value) > max_len:
        raise ValidationError(f"{name} exceeds maximum length {max_len} (got {len(value)})")
    return value


# ---------------------------------------------------------------------------
# Draft (LLM output)
# ---------------------------------------------------------------------------

class Draft:
    """Validated output from the LLM prompt."""

    def __init__(
        self,
        action: DraftAction,
        summary: str | None,
        description: str | None,
        ticket_language: TicketLanguage | None,
        questions: list[str],
    ) -> None:
        self.action = action
        self.summary = summary
        self.description = description
        self.ticket_language = ticket_language
        self.questions = questions

    # ------------------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Draft":
        """Parse and validate a raw dict from the LLM response."""
        if not isinstance(data, dict):
            raise ValidationError("Draft must be a JSON object")

        raw_action = data.get("action")
        try:
            action = DraftAction(raw_action)
        except (ValueError, TypeError):
            raise ValidationError(
                f"action must be one of {[a.value for a in DraftAction]}, got {raw_action!r}"
            )

        # ticket_language: null allowed when action != create
        raw_lang = data.get("ticket_language")
        ticket_language: TicketLanguage | None = None
        if raw_lang is not None:
            try:
                ticket_language = TicketLanguage(raw_lang)
            except (ValueError, TypeError):
                raise ValidationError(
                    f"ticket_language must be 'vi', 'en' or null, got {raw_lang!r}"
                )

        questions: list[str] = data.get("questions", [])
        if not isinstance(questions, list):
            raise ValidationError("questions must be a list")
        for i, q in enumerate(questions):
            if not isinstance(q, str) or not q.strip():
                raise ValidationError(f"questions[{i}] must be a nonempty string")

        summary: str | None = None
        description: str | None = None

        if action == DraftAction.CREATE:
            raw_summary = data.get("summary")
            raw_desc = data.get("description")
            summary = _check_str(raw_summary, "summary", max_len=MAX_SUMMARY_LEN)
            description = _check_str(raw_desc, "description", max_len=MAX_DESCRIPTION_LEN)
            if ticket_language is None:
                raise ValidationError("ticket_language is required when action='create'")

        if action == DraftAction.CLARIFY and not questions:
            raise ValidationError("action='clarify' requires at least one question")

        return cls(action=action, summary=summary, description=description,
                   ticket_language=ticket_language, questions=questions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "summary": self.summary,
            "description": self.description,
            "ticket_language": self.ticket_language.value if self.ticket_language else None,
            "questions": self.questions,
        }


# ---------------------------------------------------------------------------
# ChatRequest (incoming from API)
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


class ChatRequest:
    """Validated inbound chat request."""

    def __init__(
        self,
        message: str,
        request_id: str,
        conversation_id: str | None,
    ) -> None:
        self.message = message
        self.request_id = request_id
        self.conversation_id = conversation_id

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChatRequest":
        if not isinstance(data, dict):
            raise ValidationError("Request must be a JSON object")
        message = _check_str(data.get("message"), "message", max_len=10_000)
        request_id = _check_str(data.get("request_id"), "request_id", max_len=64)
        if not _UUID_RE.match(request_id):
            raise ValidationError("request_id must be a UUID v4 string")
        conversation_id = data.get("conversation_id")
        if conversation_id is not None:
            conversation_id = _check_str(conversation_id, "conversation_id",
                                         max_len=64, allow_empty=False)
        return cls(message=message, request_id=request_id, conversation_id=conversation_id)


# ---------------------------------------------------------------------------
# ChatResult (outbound from API)
# ---------------------------------------------------------------------------

class ChatResult:
    """Outbound response to the browser."""

    def __init__(
        self,
        status: ChatStatus,
        request_id: str,
        message: str | None = None,
        questions: list[str] | None = None,
        issue_key: str | None = None,
        issue_url: str | None = None,
        conversation_id: str | None = None,
    ) -> None:
        self.status = status
        self.request_id = request_id
        self.message = message
        self.questions = questions or []
        self.issue_key = issue_key
        self.issue_url = issue_url
        self.conversation_id = conversation_id

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "status": self.status.value,
            "request_id": self.request_id,
        }
        if self.conversation_id is not None:
            d["conversation_id"] = self.conversation_id
        if self.message is not None:
            d["message"] = self.message
        if self.questions:
            d["questions"] = self.questions
        if self.issue_key is not None:
            d["issue_key"] = self.issue_key
        if self.issue_url is not None:
            d["issue_url"] = self.issue_url
        return d
