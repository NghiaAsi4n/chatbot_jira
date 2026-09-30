"""MCP-only adapter. Tool schemas are discovered from the server, not REST payloads."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from jsonschema import Draft202012Validator
from .config import Settings


class JiraToolError(RuntimeError):
    pass


class WriteOutcomeUnknown(JiraToolError):
    """A write was sent, but its outcome could not be verified. Do not retry."""


def extract_payload(result: Any) -> Any:
    if result.is_error:
        message = " ".join(getattr(b, "text", "") for b in result.content)
        raise JiraToolError(message or "MCP tool reported an error")
    if result.structured_content is not None:
        return result.structured_content
    text = "\n".join(b.text for b in result.content if getattr(b, "type", None) == "text")
    try:
        return json.loads(text)
    except (ValueError, TypeError) as exc:
        raise JiraToolError("MCP response is not structured JSON") from exc


class ToolCatalog:
    def __init__(self, tools):
        self.tools = {t.name: t for t in tools}

    @property
    def names(self):
        return self.tools.keys()

    @classmethod
    async def load(cls, client):
        tools, seen, cursor = [], set(), None
        for _ in range(50):
            page = await client.list_tools(cursor=cursor)
            tools.extend(page.tools)
            cursor = page.next_cursor
            if not cursor:
                return cls(tools)
            if cursor in seen:
                raise JiraToolError("Repeated tool pagination cursor")
            seen.add(cursor)
        raise JiraToolError("Tool catalog exceeded pagination limit")

    def schema(self, name):
        if name not in self.tools:
            raise JiraToolError(
                f"Required tool unavailable: {name}. Use the official ?tools=all catalog."
            )
        return self.tools[name].input_schema

    def arguments(self, name, values):
        schema = self.schema(name)
        args = {k: v for k, v in values.items()
                if k in schema.get("properties", {}) and v is not None}
        errors = list(Draft202012Validator(schema).iter_errors(args))
        if errors:
            raise JiraToolError(f"Arguments do not match discovered schema for {name}")
        return args


@dataclass(frozen=True)
class CreatedIssue:
    key: str
    summary: str
    description: str


def description_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and value.get("type") == "doc":
        def node_text(node):
            if node.get("type") == "text":
                return node.get("text", "")
            if node.get("type") == "hardBreak":
                return "\n"
            return "".join(node_text(c) for c in node.get("content", []))
        return "\n".join(node_text(n) for n in value.get("content", []))
    raise JiraToolError("Unsupported description response format")


class JiraMcpGateway:
    # Primary tools available in deferred mode (https://mcp.atlassian.com/v2/mcp).
    # Deferred tools (listJiraProjects, listJiraProjectIssueTypesMetadata, etc.)
    # require executeRead/executeWrite and are not called directly in M1.
    ALLOWED = {
        "getAccessibleAtlassianResources",
        "atlassianUserInfo",
        "createJiraIssue",
        "getJiraIssue",
        "searchJiraIssuesUsingJql",
    }

    def __init__(self, client, settings, catalog):
        self.client, self.settings, self.catalog = client, settings, catalog

    @classmethod
    async def create(cls, client, settings):
        return cls(client, settings, await ToolCatalog.load(client))

    async def call(self, name, values):
        if name not in self.ALLOWED:
            raise JiraToolError("Tool is not allowed")
        args = self.catalog.arguments(name, values)
        return extract_payload(await self.client.call_tool(name, args))

    def target(self):
        if not self.settings.cloud_id:
            raise JiraToolError("ATLASSIAN_CLOUD_ID is required for targeted Jira calls")
        # Field names match the discovered createJiraIssue schema exactly.
        return {
            "cloudId": self.settings.cloud_id,
            "projectKey": self.settings.project_key,
            "issueType": self.settings.issue_type,
        }

    async def create_and_verify_issue(self, summary, description):
        if not summary.strip() or not description.strip() or len(summary) > 255:
            raise JiraToolError("Summary/description must be nonempty; summary limit is 255")
        # createJiraIssue accepts markdown string for description (confirmed from live schema).
        values = self.target() | {
            "summary": summary,
            "description": description,
            "contentFormat": "markdown",
        }
        # Validate against discovered schema before sending the write.
        args = self.catalog.arguments("createJiraIssue", values)
        if "description" not in args:
            raise JiraToolError("Discovered create schema does not support description")
        try:
            raw = await self.client.call_tool("createJiraIssue", args)
        except Exception as exc:
            raise WriteOutcomeUnknown("Create outcome unknown; inspect Jira before retry") from exc
        created = extract_payload(raw)
        # Response may be wrapped: {"data": {"key": "KAN-1", ...}} or {"key": "KAN-1", ...}
        if isinstance(created, dict) and "data" in created:
            created = created["data"]
        key = created.get("key") if isinstance(created, dict) else None
        if not isinstance(key, str) or not re.fullmatch(
            re.escape(self.settings.project_key) + r"-[0-9]+", key
        ):
            raise WriteOutcomeUnknown("Create returned no valid issue key for the selected project")
        # Read back to verify the two required fields.
        try:
            read_raw = await self.client.call_tool("getJiraIssue", self.catalog.arguments(
                "getJiraIssue", {"cloudId": self.settings.cloud_id, "issueIdOrKey": key,
                                 "view": "evidence"}
            ))
            read = extract_payload(read_raw)
            if isinstance(read, dict) and "data" in read:
                read = read["data"]
            # Support both flat and nested field shapes from the live response.
            fields = read.get("fields", read)
            actual_summary = fields.get("summary", read.get("summary", ""))
            raw_desc = fields.get("description", read.get("description", ""))
            actual_description = description_text(raw_desc) if raw_desc else ""
            read_key = read.get("key", "")
            if read_key != key or actual_summary != summary:
                raise JiraToolError(f"Read-back mismatch: key={read_key} summary={actual_summary!r}")
        except JiraToolError:
            raise
        except Exception as exc:
            raise WriteOutcomeUnknown(f"Issue {key} created; read-back verification failed") from exc
        return CreatedIssue(key, actual_summary, actual_description)
