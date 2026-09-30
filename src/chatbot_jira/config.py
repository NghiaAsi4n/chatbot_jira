from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import urlsplit


DEFAULT_MCP_URL = "https://mcp.atlassian.com/v2/mcp?tools=all"


class ConfigurationError(ValueError):
    """Raised when required M1 configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    auth_mode: str
    project_key: str
    issue_type: str
    mcp_url: str = DEFAULT_MCP_URL
    cloud_id: str | None = None
    callback_port: int = 8765
    atlassian_email: str | None = field(default=None, repr=False)
    atlassian_api_token: str | None = field(default=None, repr=False)
    atlassian_api_key: str | None = field(default=None, repr=False)
    site_url: str | None = None


    @classmethod
    def from_env(cls) -> "Settings":
        return cls.from_mapping(os.environ)

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "Settings":
        auth_mode = values.get("ATLASSIAN_AUTH_MODE", "oauth").strip().lower()
        project_key = values.get("JIRA_PROJECT_KEY", "").strip().upper()
        issue_type = values.get("JIRA_ISSUE_TYPE", "").strip()
        mcp_url = values.get("ATLASSIAN_MCP_URL", DEFAULT_MCP_URL).strip()
        site_url_raw = values.get("ATLASSIAN_SITE_URL", "").strip()
        site_url: str | None = None
        if site_url_raw:
            su = urlsplit(site_url_raw)
            if su.scheme != "https" or not su.hostname or not su.hostname.endswith(".atlassian.net"):
                raise ConfigurationError(
                    "ATLASSIAN_SITE_URL must be https://<site>.atlassian.net"
                )
            site_url = site_url_raw.rstrip("/")



        missing: list[str] = []
        if not project_key:
            missing.append("JIRA_PROJECT_KEY")
        if not issue_type:
            missing.append("JIRA_ISSUE_TYPE")

        email = values.get("ATLASSIAN_EMAIL", "").strip() or None
        api_token = values.get("ATLASSIAN_API_TOKEN", "").strip() or None
        api_key = values.get("ATLASSIAN_API_KEY", "").strip() or None

        if auth_mode == "basic":
            if not email:
                missing.append("ATLASSIAN_EMAIL")
            if not api_token:
                missing.append("ATLASSIAN_API_TOKEN")
        elif auth_mode == "bearer":
            if not api_key:
                missing.append("ATLASSIAN_API_KEY")
        elif auth_mode != "oauth":
            raise ConfigurationError(
                "ATLASSIAN_AUTH_MODE must be 'oauth', 'basic' or 'bearer'"
            )

        parsed = urlsplit(mcp_url)
        if (parsed.scheme != "https" or parsed.netloc != "mcp.atlassian.com"
                or parsed.path != "/v2/mcp" or parsed.fragment
                or parsed.query not in ("", "tools=all")):
            raise ConfigurationError(
                "ATLASSIAN_MCP_URL must be https://mcp.atlassian.com/v2/mcp "
                "(deferred mode) or https://mcp.atlassian.com/v2/mcp?tools=all (flat mode)"
            )
        if missing:
            raise ConfigurationError(
                "Missing required configuration: " + ", ".join(sorted(missing))
            )

        try:
            port = int(values.get("MCP_CALLBACK_PORT", "8765"))
            if not 1024 <= port <= 65535:
                raise ValueError
        except ValueError as exc:
            raise ConfigurationError("MCP_CALLBACK_PORT must be 1024..65535") from exc
        return cls(
            auth_mode=auth_mode,
            project_key=project_key,
            issue_type=issue_type,
            mcp_url=mcp_url,
            callback_port=port,
            cloud_id=values.get("ATLASSIAN_CLOUD_ID", "").strip() or None,
            atlassian_email=email,
            atlassian_api_token=api_token,
            atlassian_api_key=api_key,
            site_url=site_url,
        )

    @property
    def authorization_header(self) -> str:
        if self.auth_mode == "bearer":
            assert self.atlassian_api_key is not None
            return f"Bearer {self.atlassian_api_key}"

        assert self.atlassian_email is not None
        assert self.atlassian_api_token is not None
        raw = f"{self.atlassian_email}:{self.atlassian_api_token}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")
