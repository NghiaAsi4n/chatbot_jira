"""T04 tests: validate Draft, ChatRequest, ChatResult schema."""
import pytest
from chatbot_jira.models import (
    Draft, DraftAction, ChatRequest, ChatResult, ChatStatus,
    TicketLanguage, ValidationError, MAX_SUMMARY_LEN
)


# ---------------------------------------------------------------------------
# Draft.from_dict
# ---------------------------------------------------------------------------

def test_draft_create_vi_valid():
    d = Draft.from_dict({
        "action": "create",
        "summary": "OTP hết hạn không hiển thị thông báo",
        "description": "Khi OTP hết hạn, màn hình loading mãi.",
        "ticket_language": "vi",
        "questions": [],
    })
    assert d.action == DraftAction.CREATE
    assert d.ticket_language == TicketLanguage.VI
    assert d.questions == []


def test_draft_create_en_valid():
    d = Draft.from_dict({
        "action": "create",
        "summary": "OTP expiry not shown on login screen",
        "description": "Login keeps loading after OTP expires.",
        "ticket_language": "en",
        "questions": [],
    })
    assert d.action == DraftAction.CREATE
    assert d.ticket_language == TicketLanguage.EN


def test_draft_clarify_requires_questions():
    with pytest.raises(ValidationError, match="question"):
        Draft.from_dict({"action": "clarify", "questions": [], "ticket_language": None})


def test_draft_clarify_valid():
    d = Draft.from_dict({
        "action": "clarify",
        "questions": ["Lỗi xảy ra ở màn hình nào?"],
        "ticket_language": None,
    })
    assert d.action == DraftAction.CLARIFY
    assert len(d.questions) == 1


def test_draft_unsupported():
    d = Draft.from_dict({"action": "unsupported", "questions": [], "ticket_language": None})
    assert d.action == DraftAction.UNSUPPORTED


def test_draft_create_requires_ticket_language():
    with pytest.raises(ValidationError, match="ticket_language"):
        Draft.from_dict({
            "action": "create",
            "summary": "Some title",
            "description": "Some description",
            "ticket_language": None,
            "questions": [],
        })


def test_draft_create_summary_too_long():
    with pytest.raises(ValidationError, match="summary"):
        Draft.from_dict({
            "action": "create",
            "summary": "x" * (MAX_SUMMARY_LEN + 1),
            "description": "desc",
            "ticket_language": "en",
            "questions": [],
        })


def test_draft_create_missing_summary():
    with pytest.raises(ValidationError):
        Draft.from_dict({
            "action": "create",
            "description": "desc",
            "ticket_language": "en",
            "questions": [],
        })


def test_draft_create_empty_description():
    with pytest.raises(ValidationError, match="description"):
        Draft.from_dict({
            "action": "create",
            "summary": "title",
            "description": "   ",
            "ticket_language": "en",
            "questions": [],
        })


def test_draft_invalid_action():
    with pytest.raises(ValidationError, match="action"):
        Draft.from_dict({"action": "delete", "questions": [], "ticket_language": None})


def test_draft_invalid_language():
    with pytest.raises(ValidationError, match="ticket_language"):
        Draft.from_dict({
            "action": "create",
            "summary": "title",
            "description": "desc",
            "ticket_language": "fr",
            "questions": [],
        })


def test_draft_roundtrip():
    original = {
        "action": "create",
        "summary": "Tiêu đề",
        "description": "Mô tả",
        "ticket_language": "vi",
        "questions": [],
    }
    d = Draft.from_dict(original)
    assert d.to_dict() == original


# ---------------------------------------------------------------------------
# ChatRequest.from_dict
# ---------------------------------------------------------------------------

def test_chat_request_valid():
    r = ChatRequest.from_dict({
        "message": "Tạo task sửa lỗi OTP",
        "request_id": "12345678-1234-4234-8234-123456789abc",
        "conversation_id": "conv-001",
    })
    assert r.message == "Tạo task sửa lỗi OTP"
    assert r.conversation_id == "conv-001"


def test_chat_request_without_conversation_id():
    r = ChatRequest.from_dict({
        "message": "Create a ticket",
        "request_id": "12345678-1234-4234-8234-123456789abc",
    })
    assert r.conversation_id is None


def test_chat_request_invalid_uuid():
    with pytest.raises(ValidationError, match="UUID"):
        ChatRequest.from_dict({
            "message": "hello",
            "request_id": "not-a-uuid",
        })


def test_chat_request_empty_message():
    with pytest.raises(ValidationError, match="message"):
        ChatRequest.from_dict({
            "message": "   ",
            "request_id": "12345678-1234-4234-8234-123456789abc",
        })


# ---------------------------------------------------------------------------
# ChatResult.to_dict
# ---------------------------------------------------------------------------

def test_chat_result_created_serializes_correctly():
    r = ChatResult(
        status=ChatStatus.CREATED,
        request_id="req-1",
        issue_key="KAN-4",
        issue_url="https://example.atlassian.net/browse/KAN-4",
        conversation_id="conv-1",
    )
    d = r.to_dict()
    assert d["status"] == "created"
    assert d["issue_key"] == "KAN-4"
    assert d["issue_url"] == "https://example.atlassian.net/browse/KAN-4"
    assert "questions" not in d  # omitted when empty


def test_chat_result_clarification_serializes_correctly():
    r = ChatResult(
        status=ChatStatus.CLARIFICATION_REQUIRED,
        request_id="req-2",
        questions=["Lỗi này xảy ra ở đâu?"],
        message="Cần thêm thông tin:",
    )
    d = r.to_dict()
    assert d["status"] == "clarification_required"
    assert d["questions"] == ["Lỗi này xảy ra ở đâu?"]
    assert "issue_key" not in d
