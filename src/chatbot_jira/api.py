"""
T07: FastAPI application — /api/chat, /health và static web chat UI.

Điểm thiết kế:
- Một TicketService instance toàn cục (singleton per process).
- MCP client chỉ được khởi tạo khi LLM trả action create.
- Token/credential chỉ ở backend, không lộ ra frontend.
- Trạng thái xử lý thật từ backend (không fake timer).
- Restart cần kết nối MCP lại; phiên hội thoại mất — UI thông báo rõ.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .auth import connected_client
from .config import ConfigurationError, Settings
from .gateway import JiraMcpGateway
from .llm import llm_adapter_from_env
from .models import ChatRequest, ValidationError
from .service import TicketService
from .store import RequestStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App state
# ---------------------------------------------------------------------------

_service: TicketService | None = None
_settings: Settings | None = None
class _RequestScopedJiraGateway:
    # Opens Jira only when TicketService has a complete draft to create.
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def create_and_verify_issue(self, summary: str, description: str):
        async with connected_client(self.settings) as mcp_client:
            gateway = await JiraMcpGateway.create(mcp_client, self.settings)
            return await gateway.create_and_verify_issue(summary, description)

    async def transition_issue_to_done(self, issue_key: str):
        async with connected_client(self.settings) as mcp_client:
            gateway = await JiraMcpGateway.create(mcp_client, self.settings)
            return await gateway.transition_issue_to_done(issue_key)


def _get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def _get_service() -> TicketService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return _service


# ---------------------------------------------------------------------------
# Lifespan: init store + LLM; MCP opened lazily on first request
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _service, _settings

    try:
        settings = _get_settings()
    except ConfigurationError as exc:
        logger.critical("Configuration error: %s", exc)
        raise RuntimeError(f"Configuration error: {exc}") from exc

    db_path = os.environ.get("SQLITE_DB_PATH", "data/requests.db")
    store = RequestStore(Path(db_path))

    llm = llm_adapter_from_env(dict(os.environ))

    async def gateway_factory():
        return _RequestScopedJiraGateway(settings)

    _service = TicketService(llm=llm, store=store, gateway_factory=gateway_factory)

    logger.info("chatbot-jira started. Project=%s IssueType=%s",
                settings.project_key, settings.issue_type)
    yield
    logger.info("chatbot-jira shutting down.")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="chatbot-jira", lifespan=lifespan)

# Serve static files (web chat UI)
_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def root():
    """Serve the web chat UI."""
    html_path = Path(__file__).parent / "static" / "index.html"
    if html_path.exists():
        return Response(content=html_path.read_bytes(), media_type="text/html")
    return {"message": "chatbot-jira API. POST /api/chat to interact."}


@app.post("/api/chat")
async def chat(request: Request):
    """
    POST /api/chat
    Body: { "message": str, "request_id": str (UUID), "conversation_id"?: str }
    Response: ChatResult dict
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Assign a request_id if not provided (convenience for browser clients)
    if "request_id" not in body or not body.get("request_id"):
        body["request_id"] = str(uuid4())

    try:
        chat_req = ChatRequest.from_dict(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        result = await _get_service().handle(chat_req)
    except Exception as exc:
        logger.exception("Unexpected error in /api/chat: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error")

    return JSONResponse(content=result.to_dict())
