"""Transport faults, cleanup, and diagnostics with no network connections."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError
from typing import Any

import anyio
import httpx
import pytest
from langchain_core.tools import BaseTool

from browser_exploration import mcp_client
from browser_exploration.browser_tools import MCPError
from browser_exploration.config import Settings
from tests.unit.browser_exploration.test_mcp_client import FakeClient, tool


class LifecycleClient(FakeClient):
    """Model transport initialization/cleanup, including AnyIO exception grouping."""

    def __init__(
        self, *, enter_error: BaseException | None = None, exit_error: Exception | None = None
    ) -> None:
        super().__init__({})
        self.enter_error = enter_error
        self.exit_error = exit_error

    @asynccontextmanager
    async def session(self, name: str) -> AsyncIterator[object]:
        assert name == "playwright"
        self.enters += 1
        if self.enter_error is not None:
            raise self.enter_error
        try:
            yield self.session_identity
        except Exception as body_error:
            if self.exit_error is not None:
                raise ExceptionGroup("transport cleanup", [body_error, self.exit_error]) from None
            raise
        finally:
            self.exits += 1
        if self.exit_error is not None:
            raise self.exit_error


def install_client(monkeypatch: pytest.MonkeyPatch, client: LifecycleClient) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(mcp_client, "MultiServerMCPClient", lambda connections: client)

    async def load(session: object) -> list[BaseTool]:
        assert session is client.session_identity
        return [tool("browser_navigate", calls), tool("browser_snapshot", calls)]

    monkeypatch.setattr(mcp_client, "load_mcp_tools", load)
    return calls


async def test_unknown_tool_is_rejected_without_invoking_any_registered_tool() -> None:
    """Verify unknown tool is rejected without invoking any registered tool.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for unknown tool is rejected without invoking any
            registered tool."""
    calls: list[str] = []
    session = mcp_client._ConnectedSession(
        {"browser_snapshot": tool("browser_snapshot", calls)}, {"snapshot": "browser_snapshot"}
    )
    with pytest.raises(MCPError) as failure:
        await session.call("browser_evaluate", {"function": "() => document.cookie"})
    assert failure.value.code == "unsupported_tool"
    assert calls == []


@pytest.mark.parametrize("error", [anyio.BrokenResourceError(), anyio.ClosedResourceError()])
async def test_closed_transport_resources_are_classified_as_disconnects(error: Exception) -> None:
    """Verify closed transport resources are classified as disconnects.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for closed transport resources are classified as
            disconnects."""
    calls: list[str] = []
    session = mcp_client._ConnectedSession(
        {"browser_snapshot": tool("browser_snapshot", calls, failure=error)},
        {"snapshot": "browser_snapshot"},
    )
    with pytest.raises(MCPError) as failure:
        await session.call("browser_snapshot", {})
    assert failure.value.code == "mcp_disconnected"
    assert calls == ["browser_snapshot"]  # Never retry a browser operation implicitly.


async def test_unexpected_tool_failure_is_sanitized_and_not_retried() -> None:
    """Verify unexpected tool failure is sanitized and not retried.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for unexpected tool failure is sanitized and not retried.
    """
    calls: list[str] = []
    session = mcp_client._ConnectedSession(
        {"browser_snapshot": tool("browser_snapshot", calls, failure=ValueError("private-token"))},
        {"snapshot": "browser_snapshot"},
    )
    with pytest.raises(MCPError) as failure:
        await session.call("browser_snapshot", {})
    assert failure.value.code == "tool_execution_error"
    assert "private-token" not in str(failure.value)
    assert calls == ["browser_snapshot"]


@pytest.mark.parametrize("status", [401, 403, 404, 503])
@pytest.mark.parametrize("grouped", [False, True])
async def test_http_failures_preserve_status_without_leaking_headers_or_urls(
    monkeypatch: pytest.MonkeyPatch, status: int, grouped: bool
) -> None:
    """Verify http failures preserve status without leaking headers or urls.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for http failures preserve status without leaking headers
            or urls."""
    request = httpx.Request(
        "POST",
        "https://mcp.example/mcp?token=private-token",
        headers={"Authorization": "Bearer private-token"},
    )
    error = httpx.HTTPStatusError(
        "private-token", request=request, response=httpx.Response(status, request=request)
    )
    failure: Exception = (
        ExceptionGroup("outer", [ExceptionGroup("inner", [error])]) if grouped else error
    )
    client = LifecycleClient(enter_error=failure)
    calls = install_client(monkeypatch, client)
    result = await mcp_client.check_connection(Settings())
    assert result["status"] == "failed"
    assert result["error"]["code"] == "mcp_http_error"
    assert f"HTTP {status}" in result["error"]["message"]
    assert "private-token" not in str(result)
    assert "mcp.example" not in str(result)
    assert calls == []


async def test_cleanup_failure_prevents_successful_connection_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify cleanup failure prevents successful connection diagnostics.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for cleanup failure prevents successful connection
            diagnostics."""
    client = LifecycleClient(exit_error=ConnectionError("private-token"))
    calls = install_client(monkeypatch, client)
    result = await mcp_client.check_connection(Settings())
    assert result["status"] == "failed"
    assert result["error"]["code"] == "mcp_disconnected"
    assert "private-token" not in str(result)
    assert client.enters == client.exits == 1
    assert calls == []


async def test_body_failure_survives_an_additional_cleanup_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify body failure survives an additional cleanup exception.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for body failure survives an additional cleanup exception.
    """
    client = LifecycleClient(exit_error=ConnectionError("cleanup failed"))
    install_client(monkeypatch, client)
    original = ValueError("application failure")
    with pytest.raises(ValueError) as failure:
        async with mcp_client.MCPConnection(Settings()).connect():
            raise original
    assert failure.value is original
    assert client.exits == 1


async def test_connection_initialization_cancellation_is_not_a_failure_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify connection initialization cancellation is not a failure result.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for connection initialization cancellation is not a failure
            result."""
    client = LifecycleClient(enter_error=asyncio.CancelledError())
    calls = install_client(monkeypatch, client)
    with pytest.raises(asyncio.CancelledError):
        await mcp_client.check_connection(Settings())
    assert calls == []


async def test_cancellation_inside_initialized_session_closes_it_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify cancellation inside initialized session closes it once.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for cancellation inside initialized session closes it once.
    """
    client = LifecycleClient()
    install_client(monkeypatch, client)
    with pytest.raises(asyncio.CancelledError):
        async with mcp_client.MCPConnection(Settings()).connect():
            raise asyncio.CancelledError()
    assert client.enters == client.exits == 1


async def test_missing_distribution_metadata_does_not_make_connection_check_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify missing distribution metadata does not make connection check fail.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for missing distribution metadata does not make connection
            check fail."""
    client = LifecycleClient()
    install_client(monkeypatch, client)

    def version(package: str) -> str:
        if package == "mcp":
            raise PackageNotFoundError(package)
        return "adapter-version"

    monkeypatch.setattr(mcp_client, "version", version)
    result = await mcp_client.check_connection(Settings())
    assert result["status"] == "connected"
    assert result["client_versions"] == {"mcp": None, "langchain-mcp-adapters": "adapter-version"}


async def test_discovery_without_required_capabilities_closes_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify discovery without required capabilities closes session.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for discovery without required capabilities closes session.
    """
    client = LifecycleClient()
    install_client(monkeypatch, client)

    async def load(session: Any) -> list[BaseTool]:
        return []

    monkeypatch.setattr(mcp_client, "load_mcp_tools", load)
    result = await mcp_client.check_connection(Settings())
    assert result["error"]["code"] == "missing_browser_tools"
    assert "navigate" in result["error"]["message"]
    assert "snapshot" in result["error"]["message"]
    assert client.exits == 1
