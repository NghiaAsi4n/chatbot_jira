import base64

import pytest

from chatbot_jira.config import ConfigurationError, Settings


def test_basic_auth_header_uses_email_and_scoped_api_token() -> None:
    settings = Settings.from_mapping(
        {
            "ATLASSIAN_AUTH_MODE": "basic",
            "ATLASSIAN_EMAIL": "dev@example.com",
            "ATLASSIAN_API_TOKEN": "secret-token",
            "JIRA_PROJECT_KEY": "DEMO",
            "JIRA_ISSUE_TYPE": "Task",
        }
    )

    encoded = base64.b64encode(b"dev@example.com:secret-token").decode("ascii")
    assert settings.authorization_header == f"Basic {encoded}"
    assert settings.mcp_url == "https://mcp.atlassian.com/v2/mcp?tools=all"


def test_bearer_auth_header_uses_service_account_key() -> None:
    settings = Settings.from_mapping(
        {
            "ATLASSIAN_AUTH_MODE": "bearer",
            "ATLASSIAN_API_KEY": "service-key",
            "JIRA_PROJECT_KEY": "DEMO",
            "JIRA_ISSUE_TYPE": "Task",
        }
    )

    assert settings.authorization_header == "Bearer service-key"


def test_missing_basic_auth_values_are_reported_together() -> None:
    with pytest.raises(ConfigurationError) as error:
        Settings.from_mapping(
            {
                "ATLASSIAN_AUTH_MODE": "basic",
                "JIRA_PROJECT_KEY": "DEMO",
                "JIRA_ISSUE_TYPE": "Task",
            }
        )

    assert "ATLASSIAN_EMAIL" in str(error.value)
    assert "ATLASSIAN_API_TOKEN" in str(error.value)


def test_project_and_issue_type_are_required() -> None:
    with pytest.raises(ConfigurationError) as error:
        Settings.from_mapping(
            {
                "ATLASSIAN_AUTH_MODE": "bearer",
                "ATLASSIAN_API_KEY": "service-key",
            }
        )

    assert "JIRA_PROJECT_KEY" in str(error.value)
    assert "JIRA_ISSUE_TYPE" in str(error.value)


def test_site_url_valid_atlassian_net() -> None:
    settings = Settings.from_mapping(
        {
            "ATLASSIAN_AUTH_MODE": "bearer",
            "ATLASSIAN_API_KEY": "key",
            "JIRA_PROJECT_KEY": "DEMO",
            "JIRA_ISSUE_TYPE": "Task",
            "ATLASSIAN_SITE_URL": "https://mysite.atlassian.net",
        }
    )
    assert settings.site_url == "https://mysite.atlassian.net"


def test_site_url_with_trailing_slash_stripped() -> None:
    settings = Settings.from_mapping(
        {
            "ATLASSIAN_AUTH_MODE": "bearer",
            "ATLASSIAN_API_KEY": "key",
            "JIRA_PROJECT_KEY": "DEMO",
            "JIRA_ISSUE_TYPE": "Task",
            "ATLASSIAN_SITE_URL": "https://mysite.atlassian.net/",
        }
    )
    assert settings.site_url == "https://mysite.atlassian.net"


def test_site_url_invalid_domain_rejected() -> None:
    with pytest.raises(ConfigurationError) as error:
        Settings.from_mapping(
            {
                "ATLASSIAN_AUTH_MODE": "bearer",
                "ATLASSIAN_API_KEY": "key",
                "JIRA_PROJECT_KEY": "DEMO",
                "JIRA_ISSUE_TYPE": "Task",
                "ATLASSIAN_SITE_URL": "https://example.com",
            }
        )
    assert "ATLASSIAN_SITE_URL" in str(error.value)


def test_site_url_empty_is_none() -> None:
    settings = Settings.from_mapping(
        {
            "ATLASSIAN_AUTH_MODE": "bearer",
            "ATLASSIAN_API_KEY": "key",
            "JIRA_PROJECT_KEY": "DEMO",
            "JIRA_ISSUE_TYPE": "Task",
            "ATLASSIAN_SITE_URL": "",
        }
    )
    assert settings.site_url is None


def test_site_url_http_rejected() -> None:
    with pytest.raises(ConfigurationError):
        Settings.from_mapping(
            {
                "ATLASSIAN_AUTH_MODE": "bearer",
                "ATLASSIAN_API_KEY": "key",
                "JIRA_PROJECT_KEY": "DEMO",
                "JIRA_ISSUE_TYPE": "Task",
                "ATLASSIAN_SITE_URL": "http://mysite.atlassian.net",
            }
        )



def test_site_url_valid_atlassian_net():
    settings = Settings.from_mapping({
        'ATLASSIAN_AUTH_MODE': 'bearer', 'ATLASSIAN_API_KEY': 'key',
        'JIRA_PROJECT_KEY': 'DEMO', 'JIRA_ISSUE_TYPE': 'Task',
        'ATLASSIAN_SITE_URL': 'https://mysite.atlassian.net',
    })
    assert settings.site_url == 'https://mysite.atlassian.net'


def test_site_url_trailing_slash_stripped():
    settings = Settings.from_mapping({
        'ATLASSIAN_AUTH_MODE': 'bearer', 'ATLASSIAN_API_KEY': 'key',
        'JIRA_PROJECT_KEY': 'DEMO', 'JIRA_ISSUE_TYPE': 'Task',
        'ATLASSIAN_SITE_URL': 'https://mysite.atlassian.net/',
    })
    assert settings.site_url == 'https://mysite.atlassian.net'


def test_site_url_invalid_domain_rejected():
    with pytest.raises(ConfigurationError):
        Settings.from_mapping({
            'ATLASSIAN_AUTH_MODE': 'bearer', 'ATLASSIAN_API_KEY': 'key',
            'JIRA_PROJECT_KEY': 'DEMO', 'JIRA_ISSUE_TYPE': 'Task',
            'ATLASSIAN_SITE_URL': 'https://example.com',
        })


def test_site_url_empty_is_none():
    settings = Settings.from_mapping({
        'ATLASSIAN_AUTH_MODE': 'bearer', 'ATLASSIAN_API_KEY': 'key',
        'JIRA_PROJECT_KEY': 'DEMO', 'JIRA_ISSUE_TYPE': 'Task',
        'ATLASSIAN_SITE_URL': '',
    })
    assert settings.site_url is None


def test_site_url_http_rejected():
    with pytest.raises(ConfigurationError):
        Settings.from_mapping({
            'ATLASSIAN_AUTH_MODE': 'bearer', 'ATLASSIAN_API_KEY': 'key',
            'JIRA_PROJECT_KEY': 'DEMO', 'JIRA_ISSUE_TYPE': 'Task',
            'ATLASSIAN_SITE_URL': 'http://mysite.atlassian.net',
        })
