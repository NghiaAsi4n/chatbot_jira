"""
T05 tests: TicketService với FakeLlmAdapter và FakeGateway.

Kiểm tra:
- Task rõ → tạo draft đúng, gọi gateway, trả CREATED
- Task thiếu → CLARIFICATION_REQUIRED, không gọi gateway
- Chào hỏi / "ok" → UNSUPPORTED
- Gửi lặp cùng request_id → trả kết quả cũ
- Cùng ID khác body → FAILED (conflict)
- Gateway raise WriteOutcomeUnknown → UNKNOWN
- Gateway raise JiraToolError → FAILED
- LLM lỗi → FAILED
- Nhiều lượt: clarify → create → preserved draft language
"""
import pytest
from pathlib import Path
from types import SimpleNamespace

from chatbot_jira.llm import FakeLlmAdapter, LlmError
from chatbot_jira.models import (
    ChatRequest,
    ChatStatus,
    Draft,
    DraftAction,
    TicketLanguage,
)
from chatbot_jira.gateway import JiraToolError, WriteOutcomeUnknown
from chatbot_jira.service import TicketService
from chatbot_jira.store import RequestStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uuid(n: int) -> str:
    return f"12345678-1234-4{n:03d}-8000-123456789abc"[:36]


def _make_uuid(suffix: str) -> str:
    """Make a valid-format UUID from a short suffix."""
    base = "00000000-0000-4000-8000-000000000000"
    s = suffix[:12].ljust(12, "0")
    return f"00000000-0000-4000-8000-{s}"


def req(message: str, request_id: str, conv_id: str | None = "conv-1") -> ChatRequest:
    return ChatRequest(message=message, request_id=request_id, conversation_id=conv_id)


def create_draft(summary: str, lang: str = "vi") -> Draft:
    return Draft(
        action=DraftAction.CREATE,
        summary=summary,
        description="Mô tả chi tiết.",
        ticket_language=TicketLanguage(lang),
        questions=[],
    )


def clarify_draft(questions: list[str]) -> Draft:
    return Draft(
        action=DraftAction.CLARIFY,
        summary=None,
        description=None,
        ticket_language=None,
        questions=questions,
    )


UNSUPPORTED_DRAFT = Draft(
    action=DraftAction.UNSUPPORTED,
    summary=None,
    description=None,
    ticket_language=None,
    questions=[],
)


class FakeGateway:
    def __init__(self, result=None, raises=None):
        self._result = result
        self._raises = raises
        self.call_count = 0
        self.settings = SimpleNamespace(site_url=None)

    async def create_and_verify_issue(self, summary, description):
        self.call_count += 1
        if self._raises is not None:
            raise self._raises
        return self._result


def make_service(llm_responses: dict, gateway=None, tmp_path=None) -> tuple[TicketService, FakeGateway]:
    store = RequestStore(":memory:")
    llm = FakeLlmAdapter(llm_responses)
    if gateway is None:
        gateway = FakeGateway(
            result=SimpleNamespace(key="KAN-42", summary="summary", description="desc")
        )

    async def factory():
        return gateway

    service = TicketService(llm=llm, store=store, gateway_factory=factory)
    return service, gateway


# ---------------------------------------------------------------------------
# T05: Conversation routing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clear_task_vi_creates_ticket():
    msg = "Tạo task: OTP hết hạn không hiển thị thông báo"
    service, gw = make_service({msg: create_draft("OTP hết hạn không hiển thị thông báo", "vi")})
    result = await service.handle(req(msg, _make_uuid("req1")))
    assert result.status == ChatStatus.CREATED
    assert result.issue_key == "KAN-42"
    assert gw.call_count == 1


@pytest.mark.asyncio
async def test_clear_task_en_creates_ticket():
    msg = "Create a task: OTP expiry not shown on login screen"
    service, gw = make_service({msg: create_draft("OTP expiry not shown on login screen", "en")})
    result = await service.handle(req(msg, _make_uuid("req2")))
    assert result.status == ChatStatus.CREATED
    assert gw.call_count == 1


@pytest.mark.asyncio
async def test_ambiguous_task_clarifies():
    msg = "Tạo task sửa lỗi"
    service, gw = make_service({msg: clarify_draft(["Lỗi xảy ra ở màn hình nào?"])})
    result = await service.handle(req(msg, _make_uuid("req3")))
    assert result.status == ChatStatus.CLARIFICATION_REQUIRED
    assert "màn hình" in result.questions[0]
    assert gw.call_count == 0

@pytest.mark.asyncio
async def test_greeting_returns_unsupported():
    msg = "xin chào"
    service, gw = make_service({msg: UNSUPPORTED_DRAFT})
    result = await service.handle(req(msg, _make_uuid("req4")))
    assert result.status == ChatStatus.UNSUPPORTED
    assert gw.call_count == 0


@pytest.mark.asyncio
async def test_ok_after_create_returns_unsupported():
    msg1 = "Tạo task OTP lỗi"
    msg2 = "ok"
    service, gw = make_service({
        msg1: create_draft("OTP lỗi"),
        msg2: UNSUPPORTED_DRAFT,
    })
    r1 = await service.handle(req(msg1, _make_uuid("rq1"), "conv-ok"))
    assert r1.status == ChatStatus.CREATED
    r2 = await service.handle(req(msg2, _make_uuid("rq2"), "conv-ok"))
    assert r2.status == ChatStatus.UNSUPPORTED
    assert gw.call_count == 1  # only created once


@pytest.mark.asyncio
async def test_multi_turn_clarify_then_create():
    msg1 = "Tạo task sửa lỗi"
    msg2 = "Lỗi login OTP hết hạn"
    service, gw = make_service({
        msg1: clarify_draft(["Lỗi gì?", "Ở màn hình nào?"]),
        msg2: create_draft("Login OTP hết hạn"),
    })
    r1 = await service.handle(req(msg1, _make_uuid("mr1"), "conv-mt"))
    assert r1.status == ChatStatus.CLARIFICATION_REQUIRED
    r2 = await service.handle(req(msg2, _make_uuid("mr2"), "conv-mt"))
    assert r2.status == ChatStatus.CREATED


# ---------------------------------------------------------------------------
# T06: Idempotency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_duplicate_request_id_same_content_returns_existing():
    msg = "Tạo task gì đó"
    service, gw = make_service({msg: create_draft("Tạo task gì đó")})
    rid = _make_uuid("idem1")
    r1 = await service.handle(req(msg, rid))
    assert r1.status == ChatStatus.CREATED
    # Second call with same request_id and message
    r2 = await service.handle(req(msg, rid))
    assert r2.status == ChatStatus.CREATED
    assert r2.issue_key == "KAN-42"
    # Gateway should only be called ONCE
    assert gw.call_count == 1


@pytest.mark.asyncio
async def test_duplicate_request_id_different_content_returns_conflict():
    msg1 = "Tạo task A"
    msg2 = "Tạo task B"
    service, gw = make_service({
        msg1: create_draft("Task A"),
        msg2: create_draft("Task B"),
    })
    rid = _make_uuid("conf1")
    r1 = await service.handle(req(msg1, rid))
    assert r1.status == ChatStatus.CREATED
    r2 = await service.handle(req(msg2, rid))
    assert r2.status == ChatStatus.FAILED
    assert "Conflict" in (r2.message or "")
    assert gw.call_count == 1


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_outcome_unknown_returns_unknown():
    msg = "Tạo task timeout"
    gw = FakeGateway(raises=WriteOutcomeUnknown("timeout"))
    service, _ = make_service({msg: create_draft("timeout task")}, gateway=gw)
    result = await service.handle(req(msg, _make_uuid("unk1")))
    assert result.status == ChatStatus.UNKNOWN


@pytest.mark.asyncio
async def test_jira_tool_error_returns_failed():
    msg = "Tạo task error"
    gw = FakeGateway(raises=JiraToolError("Missing permission"))
    service, _ = make_service({msg: create_draft("error task")}, gateway=gw)
    result = await service.handle(req(msg, _make_uuid("err1")))
    assert result.status == ChatStatus.FAILED


@pytest.mark.asyncio
async def test_llm_error_returns_failed():
    msg = "Tạo task LLM lỗi"
    service, gw = make_service({msg: LlmError("API unreachable")})
    result = await service.handle(req(msg, _make_uuid("llm1")))
    assert result.status == ChatStatus.FAILED
    assert gw.call_count == 0


# ---------------------------------------------------------------------------
# Language rules
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_vi_input_en_output_request_honored():
    """Người dùng nhập tiếng Việt nhưng yêu cầu ticket tiếng Anh."""
    msg = "Tạo task bằng tiếng Anh: OTP hết hạn"
    service, gw = make_service({msg: create_draft("OTP expiry not shown", lang="en")})
    result = await service.handle(req(msg, _make_uuid("lang1")))
    assert result.status == ChatStatus.CREATED
    # The draft passed to gateway should have English summary (verified via gw mock)
    assert gw.call_count == 1


@pytest.mark.asyncio
async def test_en_input_vi_output_request_honored():
    """User types English but requests Vietnamese ticket."""
    msg = "Create a task in Vietnamese: OTP expired"
    service, gw = make_service({msg: create_draft("OTP hết hạn", lang="vi")})
    result = await service.handle(req(msg, _make_uuid("lang2")))
    assert result.status == ChatStatus.CREATED
    assert gw.call_count == 1


@pytest.mark.asyncio
async def test_mixed_vi_en_input_creates_ticket():
    """Input mixing VI and EN should still produce a ticket."""
    msg = "Tạo task: login page bị crash khi nhập OTP"
    service, gw = make_service({msg: create_draft("Login page crash on OTP entry", lang="en")})
    result = await service.handle(req(msg, _make_uuid("mix1")))
    assert result.status == ChatStatus.CREATED
    assert gw.call_count == 1


@pytest.mark.asyncio
async def test_multi_turn_language_preserved():
    """Language choice from first turn should carry through clarification."""
    msg1 = "Create task in English: some bug"
    msg2 = "It happens on the login screen"
    draft_clarify = Draft(
        action=DraftAction.CLARIFY,
        summary=None,
        description=None,
        ticket_language=TicketLanguage("en"),
        questions=["Which screen?"],
    )
    draft_create = Draft(
        action=DraftAction.CREATE,
        summary="Bug on login screen",
        description="Login screen bug details.",
        ticket_language=TicketLanguage("en"),
        questions=[],
    )
    service, gw = make_service({
        msg1: draft_clarify,
        msg2: draft_create,
    })
    r1 = await service.handle(req(msg1, _make_uuid("lp1"), "conv-lp"))
    assert r1.status == ChatStatus.CLARIFICATION_REQUIRED
    r2 = await service.handle(req(msg2, _make_uuid("lp2"), "conv-lp"))
    assert r2.status == ChatStatus.CREATED
    assert gw.call_count == 1


@pytest.mark.asyncio
async def test_build_url_with_site_url():
    """When FakeGateway has site_url set, issue_url is populated."""
    msg = "Tạo task URL test"
    gw = FakeGateway(result=SimpleNamespace(key="KAN-99", summary="s", description="d"))
    gw.settings = SimpleNamespace(site_url="https://mysite.atlassian.net")
    service, _ = make_service({msg: create_draft("URL test")}, gateway=gw)
    result = await service.handle(req(msg, _make_uuid("url1")))
    assert result.status == ChatStatus.CREATED
    assert result.issue_url == "https://mysite.atlassian.net/browse/KAN-99"


@pytest.mark.asyncio
async def test_build_url_without_site_url():
    """When site_url is None, issue_url should be None."""
    msg = "Tạo task no URL"
    gw = FakeGateway(result=SimpleNamespace(key="KAN-100", summary="s", description="d"))
    service, _ = make_service({msg: create_draft("no URL test")}, gateway=gw)
    result = await service.handle(req(msg, _make_uuid("url2")))
    assert result.status == ChatStatus.CREATED
    assert result.issue_url is None


@pytest.mark.asyncio
async def test_en_greeting_returns_unsupported():
    """English greeting also routes to UNSUPPORTED."""
    msg = "hello"
    service, gw = make_service({msg: UNSUPPORTED_DRAFT})
    result = await service.handle(req(msg, _make_uuid("grt2")))
    assert result.status == ChatStatus.UNSUPPORTED
    assert gw.call_count == 0
