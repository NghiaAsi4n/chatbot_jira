"""
T07 tests: FastAPI /api/chat và /health với TestClient.

Dùng dependency injection (app.state mock) để test không cần MCP thật.
"""
import os
import sys
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

# Increase recursion limit for starlette TestClient + pytest-asyncio interaction
sys.setrecursionlimit(max(sys.getrecursionlimit(), 2000))

from chatbot_jira.api import app
from chatbot_jira.models import (
    ChatResult, ChatStatus, Draft, DraftAction, TicketLanguage
)
from chatbot_jira.llm import FakeLlmAdapter
from chatbot_jira.store import RequestStore
from chatbot_jira.service import TicketService


# Minimal env vars so Settings.from_env() succeeds during lifespan.
_TEST_ENV = {
    "ATLASSIAN_AUTH_MODE": "bearer",
    "ATLASSIAN_API_KEY": "test-key",
    "ATLASSIAN_CLOUD_ID": "cloud-test",
    "JIRA_PROJECT_KEY": "TST",
    "JIRA_ISSUE_TYPE": "Task",
    "SQLITE_DB_PATH": ":memory:",
}


@pytest.fixture(autouse=True)
def _set_test_env(tmp_path):
    """Ensure Settings.from_env() succeeds and use a temp DB."""
    old_env = {}
    for k, v in _TEST_ENV.items():
        old_env[k] = os.environ.get(k)
        os.environ[k] = v
    os.environ["SQLITE_DB_PATH"] = str(tmp_path / "test.db")

    # Reset module-level caches in api.py so lifespan re-reads env.
    import chatbot_jira.api as _api
    _api._settings = None
    _api._service = None

    yield

    # Restore original environment
    for k, v in old_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    os.environ.pop("SQLITE_DB_PATH", None)


def create_draft_obj(summary: str, lang: str = "vi") -> Draft:
    return Draft(
        action=DraftAction.CREATE,
        summary=summary,
        description="Description.",
        ticket_language=TicketLanguage(lang),
        questions=[],
    )


def clarify_draft_obj(q: list[str]) -> Draft:
    return Draft(
        action=DraftAction.CLARIFY,
        summary=None,
        description=None,
        ticket_language=None,
        questions=q,
    )


UNSUPPORTED = Draft(
    action=DraftAction.UNSUPPORTED,
    summary=None,
    description=None,
    ticket_language=None,
    questions=[],
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def test_health():
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# /api/chat happy path — mock the TicketService.handle
# ---------------------------------------------------------------------------

VALID_UUID = "12345678-1234-4000-8000-123456789abc"


def _mock_handle(result: ChatResult):
    async def _handle(req):
        return result
    return _handle


@patch("chatbot_jira.api.connected_client")
@patch("chatbot_jira.api.JiraMcpGateway")
def test_chat_created(mock_gateway_cls, mock_ctx):
    """POST /api/chat → 200 with status=created."""
    # Mock MCP context
    fake_client = MagicMock()
    mock_ctx.return_value.__aenter__ = AsyncMock(return_value=fake_client)
    mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_gateway_cls.create = AsyncMock(return_value=MagicMock())

    expected = ChatResult(
        status=ChatStatus.CREATED,
        request_id=VALID_UUID,
        issue_key="KAN-5",
        issue_url="https://example.atlassian.net/browse/KAN-5",
        conversation_id="conv-1",
    )

    with patch("chatbot_jira.api.TicketService") as MockService:
        instance = MagicMock()
        instance.handle = AsyncMock(return_value=expected)
        instance._conversations = {}
        MockService.return_value = instance

        with TestClient(app) as client:
            resp = client.post("/api/chat", json={
                "message": "Tạo task OTP lỗi",
                "request_id": VALID_UUID,
                "conversation_id": "conv-1",
            })

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "created"
    assert data["issue_key"] == "KAN-5"


@patch("chatbot_jira.api.connected_client")
@patch("chatbot_jira.api.JiraMcpGateway")
def test_chat_clarification(mock_gateway_cls, mock_ctx):
    fake_client = MagicMock()
    mock_ctx.return_value.__aenter__ = AsyncMock(return_value=fake_client)
    mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_gateway_cls.create = AsyncMock(return_value=MagicMock())

    expected = ChatResult(
        status=ChatStatus.CLARIFICATION_REQUIRED,
        request_id=VALID_UUID,
        questions=["Lỗi xảy ra ở đâu?"],
        message="Cần thêm thông tin:",
    )

    with patch("chatbot_jira.api.TicketService") as MockService:
        instance = MagicMock()
        instance.handle = AsyncMock(return_value=expected)
        instance._conversations = {}
        MockService.return_value = instance

        with TestClient(app) as client:
            resp = client.post("/api/chat", json={
                "message": "Tạo task sửa lỗi",
                "request_id": VALID_UUID,
            })

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "clarification_required"
    assert len(data["questions"]) == 1


def test_clarification_does_not_open_mcp():
    # Jira must only be contacted after the LLM chooses action=create.
    message = 'Create a task'
    fake_llm = FakeLlmAdapter({
        message: clarify_draft_obj(['Which screen has the problem?'])
    })

    with patch('chatbot_jira.api.llm_adapter_from_env', return_value=fake_llm), \
         patch('chatbot_jira.api.connected_client') as mock_ctx:
        with TestClient(app) as client:
            resp = client.post('/api/chat', json={
                'message': message,
                'request_id': VALID_UUID,
            })

    assert resp.status_code == 200
    assert resp.json()['status'] == 'clarification_required'
    mock_ctx.assert_not_called()


def test_chat_missing_message():
    with TestClient(app) as client:
        resp = client.post("/api/chat", json={
            "request_id": VALID_UUID,
        })
    # Either 422 (validation) or 400 (missing field)
    assert resp.status_code in (400, 422)


def test_chat_invalid_request_id():
    with TestClient(app) as client:
        resp = client.post("/api/chat", json={
            "message": "hello",
            "request_id": "not-a-uuid",
        })
    assert resp.status_code in (400, 422)


def test_chat_auto_assign_request_id():
    """If request_id is missing, server should assign one."""
    with patch("chatbot_jira.api.connected_client") as mock_ctx, \
         patch("chatbot_jira.api.JiraMcpGateway") as mock_gw_cls, \
         patch("chatbot_jira.api.TicketService") as MockService:

        fake_client = MagicMock()
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=fake_client)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_gw_cls.create = AsyncMock(return_value=MagicMock())

        instance = MagicMock()
        instance.handle = AsyncMock(return_value=ChatResult(
            status=ChatStatus.UNSUPPORTED,
            request_id="auto-generated",
            message="Out of scope.",
        ))
        instance._conversations = {}
        MockService.return_value = instance

        with TestClient(app) as client:
            resp = client.post("/api/chat", json={"message": "hi there"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "unsupported"
