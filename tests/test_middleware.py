"""Tests for ConfigMiddleware's keychain-backed authentication gate."""

import pytest
from fastmcp.exceptions import ToolError

from garmin_connect_mcp.auth import save_tokens
from garmin_connect_mcp.middleware import ConfigMiddleware


class FakeFastMCPContext:
    """Minimal stand-in for fastmcp's Context — only implements set_state."""

    def __init__(self):
        self.state: dict[str, object] = {}

    async def set_state(self, key, value, serializable=True):
        self.state[key] = value


class FakeMiddlewareContext:
    """Minimal stand-in for fastmcp's MiddlewareContext."""

    def __init__(self):
        self.fastmcp_context = FakeFastMCPContext()


async def test_raises_tool_error_when_no_token_stored():
    middleware = ConfigMiddleware()
    context = FakeMiddlewareContext()

    async def call_next(ctx):
        raise AssertionError("call_next should not run without valid auth")

    with pytest.raises(ToolError, match="garmin-connect-mcp auth"):
        await middleware.on_call_tool(context, call_next)


async def test_injects_client_and_calls_next_when_token_valid(mocker):
    save_tokens("stored-token-blob")
    mocker.patch("garmin_connect_mcp.client.Garmin")

    middleware = ConfigMiddleware()
    context = FakeMiddlewareContext()
    call_next = mocker.AsyncMock(return_value="tool-result")

    result = await middleware.on_call_tool(context, call_next)

    assert result == "tool-result"
    call_next.assert_called_once_with(context)
    assert "client" in context.fastmcp_context.state
