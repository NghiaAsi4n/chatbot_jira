from types import SimpleNamespace

import pytest

from chatbot_jira.config import Settings
from chatbot_jira.gateway import JiraMcpGateway, JiraToolError, ToolCatalog, extract_payload


def tool(name: str, properties: dict[str, object], required: list[str]):
    return SimpleNamespace(
        name=name,
        input_schema={"type": "object", "properties": properties, "required": required},
    )


class FakeClient:
    def __init__(self, tools, responses):
        self._tools = tools
        self._responses = responses
        self.calls: list[tuple[str, dict]] = []

    async def list_tools(self, *, cursor=None):
        return SimpleNamespace(tools=self._tools, next_cursor=None)

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self._responses[name]


def ok_result(payload):
    return SimpleNamespace(is_error=False, structured_content=payload, content=[])


def error_result(message: str):
    return SimpleNamespace(
        is_error=True,
        structured_content=None,
        content=[SimpleNamespace(type="text", text=message)],
    )


def settings() -> Settings:
    return Settings.from_mapping(
        {
            "ATLASSIAN_AUTH_MODE": "bearer",
            "ATLASSIAN_API_KEY": "service-key",
            "ATLASSIAN_CLOUD_ID": "cloud-123",
            "JIRA_PROJECT_KEY": "DEMO",
            "JIRA_ISSUE_TYPE": "Task",
        }
    )


@pytest.mark.asyncio
async def test_catalog_reads_all_pages() -> None:
    first = SimpleNamespace(tools=[tool("first", {}, [])], next_cursor="page-2")
    second = SimpleNamespace(tools=[tool("second", {}, [])], next_cursor=None)

    class PaginatedClient:
        async def list_tools(self, *, cursor=None):
            return first if cursor is None else second

    catalog = await ToolCatalog.load(PaginatedClient())

    assert set(catalog.names) == {"first", "second"}


@pytest.mark.asyncio
async def test_create_issue_adapts_to_current_tool_schema_and_reads_it_back() -> None:
    tools = [
        tool(
            "createJiraIssue",
            {
                "cloudId": {"type": "string"},
                "projectKey": {"type": "string"},
                "issueType": {"type": "string"},
                "summary": {"type": "string"},
                "description": {"type": "string"},
                "contentFormat": {"type": "string"},
            },
            ["cloudId", "projectKey", "issueType", "summary"],
        ),
        tool(
            "getJiraIssue",
            {
                "cloudId": {"type": "string"},
                "issueIdOrKey": {"type": "string"},
                "fields": {"type": "array"},
            },
            ["cloudId", "issueIdOrKey"],
        ),
    ]
    client = FakeClient(
        tools,
        {
            "createJiraIssue": ok_result({"key": "DEMO-42"}),
            "getJiraIssue": ok_result(
                {
                    "key": "DEMO-42",
                    "fields": {
                        "summary": "OTP expired",
                        "description": "Show an expired message",
                    },
                }
            ),
        },
    )
    gateway = await JiraMcpGateway.create(client, settings())

    created = await gateway.create_and_verify_issue(
        summary="OTP expired", description="Show an expired message"
    )

    assert created.key == "DEMO-42"
    assert created.summary == "OTP expired"
    assert created.description == "Show an expired message"
    assert client.calls[0] == (
        "createJiraIssue",
        {
            "cloudId": "cloud-123",
            "projectKey": "DEMO",
            "issueType": "Task",
            "summary": "OTP expired",
            "description": "Show an expired message",
            "contentFormat": "markdown",
        },
    )


@pytest.mark.asyncio
async def test_tool_error_is_not_treated_as_success() -> None:
    tools = [
        tool(
            "createJiraIssue",
            {
                "cloudId": {"type": "string"},
                "projectKey": {"type": "string"},
                "issueType": {"type": "string"},
                "description": {"type": "string"},
                "summary": {"type": "string"},
            },
            ["cloudId", "projectKey", "issueType", "summary"],
        )
    ]
    client = FakeClient(
        tools, {"createJiraIssue": error_result("Missing Create Issues permission")}
    )
    gateway = await JiraMcpGateway.create(client, settings())

    with pytest.raises(JiraToolError, match="Create Issues permission"):
        await gateway.create_and_verify_issue("Title", "Description")


def test_text_result_is_parsed_as_json_when_structured_content_is_absent() -> None:
    result = SimpleNamespace(
        is_error=False,
        structured_content=None,
        content=[SimpleNamespace(type="text", text='{"key":"DEMO-7"}')],
    )

    assert extract_payload(result) == {"key": "DEMO-7"}
