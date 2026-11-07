import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from langchain_core.tools import BaseTool, StructuredTool, ToolException

from browser_exploration import mcp_client
from browser_exploration.browser_tools import MCPError
from browser_exploration.config import Settings
from browser_exploration.safety import SafetyError


def tool(name: str, calls: list[str], *, failure: Exception | None = None) -> BaseTool:
    async def invoke(**kwargs: Any) -> str:
        calls.append(name)
        if failure:
            raise failure
        return "- Page URL: https://example.com/"

    properties = {"url": {"type": "string"}} if name == "browser_navigate" else {}
    return StructuredTool(
        name=name,
        description="fake",
        args_schema={"type": "object", "properties": properties},
        coroutine=invoke,
    )


class FakeClient:
    def __init__(self, connections: dict[str, Any]) -> None:
        self.connections = connections
        self.enters = 0
        self.exits = 0
        self.session_identity = object()

    @asynccontextmanager
    async def session(self, name: str) -> AsyncIterator[object]:
        assert name == "playwright"
        self.enters += 1
        try:
            yield self.session_identity
        finally:
            self.exits += 1


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    fake = FakeClient({})

    def factory(connections: dict[str, Any]) -> FakeClient:
        fake.connections = connections
        return fake

    monkeypatch.setattr(mcp_client, "MultiServerMCPClient", factory)
    return fake


async def test_one_initialized_session_and_one_discovery_for_many_calls(
    monkeypatch: pytest.MonkeyPatch, client: FakeClient
) -> None:
    """Verify one initialized session and one discovery for many calls.

    Arrange: Use `monkeypatch`, `client` parameters; prepare `calls`, `loads`.
    Act: Open one MCP connection and make two tool calls through its initialized session.
    Assert: Check `client.exits == 1`; `loads == [client.session_identity]`; `calls ==
            ['browser_navigate', 'browser_snapshot']`; `client.enters == 1 and client.exits == 0`.
    """
    calls: list[str] = []
    loads: list[object] = []

    async def load(session: object) -> list[BaseTool]:
        loads.append(session)
        return [tool("browser_navigate", calls), tool("browser_snapshot", calls)]

    monkeypatch.setattr(mcp_client, "load_mcp_tools", load)
    settings = Settings()
    async with mcp_client.MCPConnection(settings).connect() as session:
        await session.call(session.capabilities["navigate"], {"url": "https://example.com/"})
        await session.call(session.capabilities["snapshot"], {})
        assert client.enters == 1 and client.exits == 0
    assert client.exits == 1
    assert loads == [client.session_identity]
    assert calls == ["browser_navigate", "browser_snapshot"]
    assert client.connections["playwright"]["transport"] == "streamable_http"
    assert client.connections["playwright"]["url"] == settings.mcp_url


async def test_check_no_api_key_and_no_browser_actions(
    monkeypatch: pytest.MonkeyPatch, client: FakeClient
) -> None:
    """Verify check no api key and no browser actions.

    Arrange: Use `monkeypatch`, `client` parameters; prepare `calls`.
    Act: Await `mcp_client.check_connection` to obtain `result`.
    Assert: Check `result['status'] == 'connected'`; `result['capabilities']['snapshot'] ==
            'browser_snapshot'`; `calls == []`; `client.exits == 1`.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    calls: list[str] = []

    async def load(session: object) -> list[BaseTool]:
        return [tool("browser_navigate", calls), tool("browser_snapshot", calls)]

    monkeypatch.setattr(mcp_client, "load_mcp_tools", load)
    result = await mcp_client.check_connection(Settings())
    assert result["status"] == "connected"
    assert result["capabilities"]["snapshot"] == "browser_snapshot"
    assert calls == []
    assert client.exits == 1


async def test_connection_failure_is_structured_and_sanitized(
    monkeypatch: pytest.MonkeyPatch, client: FakeClient
) -> None:
    """Verify connection failure is structured and sanitized.

    Arrange: Use `monkeypatch`, `client` parameters.
    Act: Await `mcp_client.check_connection` to obtain `result`.
    Assert: Check `result['status'] == 'failed'`; `result['error']['code'] == 'mcp_unavailable'`;
            `'secret' not in str(result)`.
    """

    async def fail(session: object) -> list[BaseTool]:
        raise ConnectionError("Authorization: Bearer secret")

    monkeypatch.setattr(mcp_client, "load_mcp_tools", fail)
    result = await mcp_client.check_connection(Settings())
    assert result["status"] == "failed"
    assert result["error"]["code"] == "mcp_unavailable"
    assert "secret" not in str(result)


async def test_check_times_out_and_closes_session(
    monkeypatch: pytest.MonkeyPatch, client: FakeClient
) -> None:
    """Verify check times out and closes session.

    Arrange: Use `monkeypatch`, `client` parameters.
    Act: Await `mcp_client.check_connection` to obtain `result`.
    Assert: Check `result['error']['code'] == 'mcp_timeout'`; `client.exits == 1`.
    """

    async def hang(session: object) -> list[BaseTool]:
        await asyncio.Event().wait()
        return []

    monkeypatch.setattr(mcp_client, "load_mcp_tools", hang)
    result = await mcp_client.check_connection(Settings(tool_timeout_seconds=0.01))
    assert result["error"]["code"] == "mcp_timeout"
    assert client.exits == 1


async def test_caller_policy_failures_are_not_relabelled_as_disconnects(
    monkeypatch: pytest.MonkeyPatch, client: FakeClient
) -> None:
    """Verify caller policy failures are not relabelled as disconnects.

    Arrange: Use `monkeypatch`, `client` parameters.
    Act: Call `monkeypatch.setattr`.
    Assert: Check `raises SafetyError`; `client.exits == 1`.
    """

    async def load(session: object) -> list[BaseTool]:
        return [tool("browser_navigate", []), tool("browser_snapshot", [])]

    monkeypatch.setattr(mcp_client, "load_mcp_tools", load)
    with pytest.raises(SafetyError, match="policy"):
        async with mcp_client.MCPConnection(Settings()).connect():
            raise SafetyError("policy")
    assert client.exits == 1


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (ToolException("secret"), "tool_execution_error"),
        (ConnectionError("secret"), "mcp_disconnected"),
        (TimeoutError("secret"), "tool_timeout"),
    ],
)
async def test_tool_failures_have_safe_codes(failure: Exception, code: str) -> None:
    """Verify tool failures have safe codes.

    Arrange: Use `failure`, `code` parameters; prepare `session`.
    Act: Call `session.call` under `pytest.raises(MCPError)`.
    Assert: Check `raises MCPError`; `caught.value.code == code`; `'secret' not in
            str(caught.value)`.
    """
    session = mcp_client._ConnectedSession(
        {"browser_snapshot": tool("browser_snapshot", [], failure=failure)},
        {"snapshot": "browser_snapshot"},
    )
    with pytest.raises(MCPError) as caught:
        await session.call("browser_snapshot", {})
    assert caught.value.code == code
    assert "secret" not in str(caught.value)


async def test_cancelled_call_propagates_cancellation() -> None:
    """Verify cancelled call propagates cancellation.

    Arrange: prepare `fake`, `session`.
    Act: Call `session.call` under `pytest.raises(asyncio.CancelledError)`.
    Assert: Check `raises asyncio.CancelledError`.
    """

    async def cancelled(**kwargs: Any) -> str:
        raise asyncio.CancelledError()

    fake = StructuredTool(
        name="browser_snapshot", description="fake", args_schema={}, coroutine=cancelled
    )
    session = mcp_client._ConnectedSession({fake.name: fake}, {"snapshot": fake.name})
    with pytest.raises(asyncio.CancelledError):
        await session.call(fake.name, {})


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("Chromium distribution 'chrome' is not found at /private/path", "browser_unavailable"),
        (
            "page.goto: net::ERR_CONNECTION_REFUSED at https://private.example?token=secret",
            "navigation_failed",
        ),
        ("Timeout 30000ms exceeded", "tool_timeout"),
    ],
)
async def test_browser_failure_causes_are_preserved_without_private_payloads(
    message: str, code: str
) -> None:
    """Verify browser failure causes are preserved without private payloads.

    Arrange: Use `message`, `code` parameters; prepare `session`.
    Act: Call `session.call` under `pytest.raises(MCPError)`.
    Assert: Check `raises MCPError`; `caught.value.code == code`; `'private' not in
            str(caught.value)`; `'secret' not in str(caught.value)`.
    """
    session = mcp_client._ConnectedSession(
        {"browser_snapshot": tool("browser_snapshot", [], failure=ToolException(message))},
        {"snapshot": "browser_snapshot"},
    )
    with pytest.raises(MCPError) as caught:
        await session.call("browser_snapshot", {})
    assert caught.value.code == code
    assert "private" not in str(caught.value)
    assert "secret" not in str(caught.value)


async def test_missing_tools_inside_transport_exception_group_keeps_capability_error(
    monkeypatch: pytest.MonkeyPatch, client: FakeClient
) -> None:
    """Verify missing tools inside transport exception group keeps capability error.

    Arrange: Use `monkeypatch`, `client` parameters.
    Act: Await `mcp_client.check_connection` to obtain `result`.
    Assert: Check `result['error']['code'] == 'missing_browser_tools'`.
    """

    async def load(session: object) -> list[BaseTool]:
        raise ExceptionGroup(
            "transport cleanup", [MCPError("missing_browser_tools", "Missing snapshot")]
        )

    monkeypatch.setattr(mcp_client, "load_mcp_tools", load)
    result = await mcp_client.check_connection(Settings())
    assert result["error"]["code"] == "missing_browser_tools"
