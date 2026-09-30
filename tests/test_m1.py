from types import SimpleNamespace
import pytest
from chatbot_jira.config import Settings, ConfigurationError
from chatbot_jira.gateway import ToolCatalog, JiraToolError

def test_oauth_needs_no_static_secret():
    s = Settings.from_mapping({"JIRA_PROJECT_KEY": "DEMO", "JIRA_ISSUE_TYPE": "Task"})
    assert s.auth_mode == "oauth"

def test_secret_is_not_in_settings_repr():
    s = Settings.from_mapping({"ATLASSIAN_AUTH_MODE": "bearer", "ATLASSIAN_API_KEY": "private-value",
                               "JIRA_PROJECT_KEY": "DEMO", "JIRA_ISSUE_TYPE": "Task"})
    assert "private-value" not in repr(s)

@pytest.mark.parametrize("url", ["https://mcp.atlassian.com.evil.test/v2/mcp", "https://mcp.atlassian.com/v2/mcp#x"])
def test_rejects_unexpected_endpoint(url):
    with pytest.raises(ConfigurationError):
        Settings.from_mapping({"ATLASSIAN_MCP_URL": url, "JIRA_PROJECT_KEY": "DEMO", "JIRA_ISSUE_TYPE": "Task"})

@pytest.mark.asyncio
async def test_repeated_pagination_cursor_is_rejected():
    class Client:
        async def list_tools(self, **kwargs):
            return SimpleNamespace(tools=[], next_cursor="same")
    with pytest.raises(JiraToolError):
        await ToolCatalog.load(Client())

@pytest.mark.asyncio
async def test_unknown_required_argument_stops_before_tool_call():
    class Client:
        async def list_tools(self, **kwargs):
            return SimpleNamespace(tools=[SimpleNamespace(name="createJiraIssue", input_schema={
                "type": "object", "properties": {"newRequired": {"type": "string"}},
                "required": ["newRequired"]})], next_cursor=None)
    catalog = await ToolCatalog.load(Client())
    with pytest.raises(JiraToolError):
        catalog.arguments("createJiraIssue", {"summary": "Title"})
