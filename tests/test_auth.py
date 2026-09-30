import pytest
from chatbot_jira.auth import parse_callback, MemoryTokenStorage

def test_callback_preserves_state_and_issuer_for_sdk_validation():
    result = parse_callback("/callback?code=abc&state=xyz&iss=https%3A%2F%2Fexample.com")
    assert result.state == "xyz"
    assert result.iss == "https://example.com"

@pytest.mark.parametrize("target", ["/other?code=a&state=b", "/callback?code=a",
                                     "/callback?error=denied", "/callback?code=a&code=b&state=c"])
def test_callback_rejects_malformed_requests(target):
    with pytest.raises(ValueError):
        parse_callback(target)

@pytest.mark.asyncio
async def test_oauth_store_does_not_share_tokens_between_sessions():
    first, second = MemoryTokenStorage(), MemoryTokenStorage()
    await first.set_tokens(object())
    assert await first.get_tokens() is not None
    assert await second.get_tokens() is None
