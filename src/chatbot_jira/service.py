"""
T05/T06: TicketService — core xử lý hội thoại và tạo ticket.

Luồng:
1. Nhận message + conversation history (in-memory per conversation_id).
2. Gọi LLM → Draft.
3. Nếu action=clarify → trả câu hỏi.
4. Nếu action=unsupported → báo ngoài phạm vi.
5. Nếu action=create:
   a. Claim request_id trong SQLite (idempotency).
   b. Gọi JiraMcpGateway.create_and_verify_issue.
   c. Ghi kết quả vào store.
   d. Trả ChatResult với issue_key/link.

Conversation state (draft + history) được giữ trong bộ nhớ.
Restart bắt đầu phiên mới — UI phải thông báo rõ.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from .gateway import JiraMcpGateway, JiraToolError, WriteOutcomeUnknown
from .llm import LlmAdapter, LlmError
from .models import (
    ChatRequest,
    ChatResult,
    ChatStatus,
    Draft,
    DraftAction,
    TicketLanguage,
)
from .store import ConflictError, RequestStore, StoredRequest

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Conversation state (in-memory)
# ---------------------------------------------------------------------------

@dataclass
class ConversationState:
    """Per-conversation mutable state."""
    history: list[dict[str, str]] = field(default_factory=list)
    current_draft: Draft | None = None
    last_created_key: str | None = None  # prevents "ok" from re-creating

    def add_user(self, message: str) -> None:
        self.history.append({"role": "user", "content": message})

    def add_assistant(self, content: str) -> None:
        self.history.append({"role": "assistant", "content": content})


# ---------------------------------------------------------------------------
# TicketService
# ---------------------------------------------------------------------------

class TicketService:
    """Orchestrates conversation, LLM and Jira creation.

    gateway_factory: async callable () -> JiraMcpGateway (called lazily once per
    service instance; MCP session reused across requests).
    """

    def __init__(
        self,
        llm: LlmAdapter,
        store: RequestStore,
        gateway_factory,  # async () -> JiraMcpGateway
    ) -> None:
        self._llm = llm
        self._store = store
        self._gateway_factory = gateway_factory
        self._gateway: JiraMcpGateway | None = None
        self._gateway_lock = asyncio.Lock()
        self._conversations: dict[str, ConversationState] = {}

    async def _get_gateway(self) -> JiraMcpGateway:
        async with self._gateway_lock:
            if self._gateway is None:
                self._gateway = await self._gateway_factory()
            return self._gateway

    def _conv(self, conversation_id: str) -> ConversationState:
        if conversation_id not in self._conversations:
            self._conversations[conversation_id] = ConversationState()
        return self._conversations[conversation_id]

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def handle(self, req: ChatRequest) -> ChatResult:
        conv_id = req.conversation_id or req.request_id
        conv = self._conv(conv_id)

        # --- Check idempotency store first ---
        content_dict = {"message": req.message, "conversation_id": req.conversation_id}
        try:
            existing = self._store.claim(req.request_id, content_dict)
        except ConflictError:
            return ChatResult(
                status=ChatStatus.FAILED,
                request_id=req.request_id,
                conversation_id=conv_id,
                message="Conflict: same request_id was already used with different content.",
            )

        if existing is not None:
            return _stored_to_result(existing, req.request_id, conv_id)

        # --- Conversation turn ---
        conv.add_user(req.message)

        # --- Call LLM ---
        try:
            draft = await self._llm.complete(conv.history)
        except LlmError as exc:
            self._store.fail(req.request_id)
            conv.add_assistant("[error: LLM unavailable]")
            return ChatResult(
                status=ChatStatus.FAILED,
                request_id=req.request_id,
                conversation_id=conv_id,
                message=f"Could not reach the AI model: {exc}",
            )

        # --- Route by action ---
        if draft.action == DraftAction.UNSUPPORTED:
            self._store.fail(req.request_id)
            msg = _unsupported_message(conv)
            conv.add_assistant(msg)
            return ChatResult(
                status=ChatStatus.UNSUPPORTED,
                request_id=req.request_id,
                conversation_id=conv_id,
                message=msg,
            )

        if draft.action == DraftAction.CLARIFY:
            self._store.fail(req.request_id)
            # Update draft (carry over ticket_language if already chosen)
            if conv.current_draft and conv.current_draft.ticket_language and not draft.ticket_language:
                draft = Draft(
                    action=draft.action,
                    summary=draft.summary,
                    description=draft.description,
                    ticket_language=conv.current_draft.ticket_language,
                    questions=draft.questions,
                )
            conv.current_draft = draft
            qs = draft.questions
            conv.add_assistant("\n".join(f"- {q}" for q in qs))
            return ChatResult(
                status=ChatStatus.CLARIFICATION_REQUIRED,
                request_id=req.request_id,
                conversation_id=conv_id,
                questions=qs,
            )

        # action == CREATE
        assert draft.summary is not None
        assert draft.description is not None
        conv.current_draft = draft

        # --- Call JiraGateway ---
        try:
            gateway = await self._get_gateway()
        except Exception as exc:
            self._store.fail(req.request_id)
            logger.error("Gateway init failed: %s", exc)
            return ChatResult(
                status=ChatStatus.FAILED,
                request_id=req.request_id,
                conversation_id=conv_id,
                message="Could not connect to Jira. Check configuration.",
            )

        try:
            created = await gateway.create_and_verify_issue(draft.summary, draft.description)
        except WriteOutcomeUnknown as exc:
            self._store.mark_unknown(req.request_id)
            logger.warning("Unknown write outcome: %s", exc)
            return ChatResult(
                status=ChatStatus.UNKNOWN,
                request_id=req.request_id,
                conversation_id=conv_id,
                message=(
                    "The request was sent but the outcome is unclear. "
                    "Please check Jira before retrying."
                ),
            )
        except JiraToolError as exc:
            self._store.fail(req.request_id)
            return ChatResult(
                status=ChatStatus.FAILED,
                request_id=req.request_id,
                conversation_id=conv_id,
                message=f"Jira error: {exc}",
            )
        except Exception as exc:
            self._store.mark_unknown(req.request_id)
            logger.exception("Unexpected error during create: %s", exc)
            return ChatResult(
                status=ChatStatus.UNKNOWN,
                request_id=req.request_id,
                conversation_id=conv_id,
                message=(
                    "An unexpected error occurred. "
                    "Please check Jira before retrying."
                ),
            )

        # --- Success ---
        issue_url = _build_url(gateway, created.key)
        self._store.succeed(req.request_id, created.key, issue_url)
        conv.last_created_key = created.key
        success_msg = f"✅ Created {created.key}" + (f": {issue_url}" if issue_url else "")
        conv.add_assistant(success_msg)

        return ChatResult(
            status=ChatStatus.CREATED,
            request_id=req.request_id,
            conversation_id=conv_id,
            issue_key=created.key,
            issue_url=issue_url,
            message=success_msg,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stored_to_result(stored: StoredRequest, request_id: str, conv_id: str) -> ChatResult:
    from .store import STATUS_SUCCEEDED, STATUS_FAILED, STATUS_UNKNOWN
    if stored.status == STATUS_SUCCEEDED:
        return ChatResult(
            status=ChatStatus.CREATED,
            request_id=request_id,
            conversation_id=conv_id,
            issue_key=stored.issue_key,
            issue_url=stored.issue_url,
            message=f"Already created: {stored.issue_key}",
        )
    if stored.status == STATUS_UNKNOWN:
        return ChatResult(
            status=ChatStatus.UNKNOWN,
            request_id=request_id,
            conversation_id=conv_id,
            message="Previous outcome unknown — check Jira before retrying.",
        )
    # failed or other
    return ChatResult(
        status=ChatStatus.FAILED,
        request_id=request_id,
        conversation_id=conv_id,
        message="Previous attempt failed.",
    )


def _unsupported_message(conv: ConversationState) -> str:
    if conv.last_created_key:
        return (
            f"The ticket {conv.last_created_key} has already been created. "
            "Can I help you create another one?"
        )
    return (
        "I can only help you create Jira tickets. "
        "Please describe the task you'd like to create."
    )


def _build_url(gateway: JiraMcpGateway, key: str) -> str | None:
    """Build issue URL from the site URL stored in settings."""
    if gateway.settings.site_url:
        return f"{gateway.settings.site_url}/browse/{key}"
    return None
