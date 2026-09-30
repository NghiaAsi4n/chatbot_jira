"""Local OAuth callback; tokens remain in memory for one smoke-test session."""
from __future__ import annotations
import asyncio
import sys
import webbrowser
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

import httpx2
from mcp import Client
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata


class MemoryTokenStorage:
    def __init__(self):
        self._tokens = None
        self._client_info = None

    async def get_tokens(self):
        return self._tokens

    async def set_tokens(self, tokens):
        self._tokens = tokens

    async def get_client_info(self):
        return self._client_info

    async def set_client_info(self, client_info):
        self._client_info = client_info


def parse_callback(target):
    parsed = urlsplit(target)
    params = parse_qs(parsed.query)
    if parsed.path != "/callback" or "error" in params:
        raise ValueError("OAuth callback rejected")
    if len(params.get("code", [])) != 1 or len(params.get("state", [])) != 1:
        raise ValueError("OAuth callback needs code and state")
    return AuthorizationCodeResult(
        code=params["code"][0], state=params["state"][0], iss=params.get("iss", [None])[0]
    )


@asynccontextmanager
async def connected_client(settings):
    callback_server = None
    async def redirect(url):
        print("Open this authorization URL in your local browser:\n" + url, file=sys.stderr)
        webbrowser.open(url)

    headers = {}
    auth = None
    if settings.auth_mode == "oauth":
        loop = asyncio.get_running_loop()
        callback = loop.create_future()

        async def handle(reader, writer):
            status, body = "400 Bad Request", b"Invalid callback."
            try:
                line = await asyncio.wait_for(reader.readline(), 5)
                method, target, _ = line.decode("ascii").strip().split(" ", 2)
                if method != "GET":
                    raise ValueError("GET required")
                result = parse_callback(target)
                if not callback.done():
                    callback.set_result(result)
                    status, body = "200 OK", b"Callback received. Check the terminal for the result."
            except (ValueError, UnicodeError, TimeoutError):
                pass
            finally:
                writer.write(
                    ("HTTP/1.1 " + status + "\r\nContent-Type: text/plain\r\n"
                     + "Cache-Control: no-store\r\nConnection: close\r\n"
                     + f"Content-Length: {len(body)}\r\n\r\n").encode() + body
                )
                await writer.drain()
                writer.close()
                await writer.wait_closed()

        callback_server = await asyncio.start_server(handle, "127.0.0.1", settings.callback_port)
        async def receive():
            # SDK validates state/issuer and performs the code exchange with PKCE.
            return await asyncio.wait_for(callback, timeout=300)

        auth = OAuthClientProvider(
            server_url=settings.mcp_url,
            client_metadata=OAuthClientMetadata(
                client_name="chatbot-jira M1",
                redirect_uris=[f"http://127.0.0.1:{settings.callback_port}/callback"],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                token_endpoint_auth_method="none",
            ),
            storage=MemoryTokenStorage(),
            redirect_handler=redirect,
            callback_handler=receive,
        )
    else:
        headers["Authorization"] = settings.authorization_header
    try:
        async with httpx2.AsyncClient(auth=auth, headers=headers, timeout=60) as http:
            async with Client(
                streamable_http_client(settings.mcp_url, http_client=http),
                read_timeout_seconds=90,
            ) as client:
                yield client
    finally:
        if callback_server is not None:
            callback_server.close()
            await callback_server.wait_closed()
