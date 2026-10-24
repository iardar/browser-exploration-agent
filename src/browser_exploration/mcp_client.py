"""One initialized Streamable HTTP MCP session for an entire exploration run."""

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import anyio
from langchain_core.tools import BaseTool, ToolException
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

from .browser_tools import BrowserSession, MCPError, discovered_tools
from .config import Settings

__all__ = ["BrowserSession", "MCPConnection", "MCPError", "check_connection"]


def _leaf_errors(error: BaseException) -> list[BaseException]:
    """Flatten grouped async errors so their individual causes can be inspected."""
    if isinstance(error, BaseExceptionGroup):
        return [leaf for child in error.exceptions for leaf in _leaf_errors(child)]
    return [error]


def _tool_error(error: ToolException) -> MCPError:
    """Translate known browser/server errors without copying raw server output."""
    text = str(error)
    if any(
        marker in text
        for marker in ("is not found at", "Executable doesn't exist", "shared libraries")
    ):
        return MCPError(
            "browser_unavailable",
            "Selected browser or its system libraries are missing on the MCP host.",
        )
    network_error = re.search(r"net::(ERR_[A-Z_]+)", text)
    if network_error:
        return MCPError("navigation_failed", f"Browser navigation failed: {network_error.group(1)}")
    if "Timeout" in text and "exceeded" in text:
        return MCPError("tool_timeout", "Playwright browser operation exceeded its timeout.")
    return MCPError("tool_execution_error", "Playwright MCP reported a tool error.")


@dataclass
class _ConnectedSession:
    """Keep discovered tool bindings attached to the session that created them."""

    tools: dict[str, BaseTool]
    capabilities: dict[str, str]

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        """Invoke one supported tool and translate transport/server failures safely."""
        tool = self.tools.get(name)
        if tool is None:
            raise MCPError("unsupported_tool", "Browser tool is not in the supported catalog.")
        try:
            # These tools close over the initialized ClientSession supplied to
            # load_mcp_tools; this does not create a session per invocation.
            return await tool.ainvoke(args)
        except ToolException as exc:
            raise _tool_error(exc) from None
        except (ConnectionError, anyio.BrokenResourceError, anyio.ClosedResourceError) as exc:
            raise MCPError("mcp_disconnected", "Playwright MCP session disconnected.") from exc
        except TimeoutError as exc:
            raise MCPError("tool_timeout", "Playwright MCP tool execution timed out.") from exc
        except Exception as exc:
            # Raw server errors can include full URLs, tokens and page content.
            raise MCPError(
                "tool_execution_error", "Browser tool failed; inspect the MCP server locally."
            ) from exc


class MCPConnection:
    """Open the browser tool server connection used for a whole exploration run.

    Tool calls share one initialized session so navigation and later snapshots use
    the same browser context. This adapter connects to an independently started
    MCP server; it does not start a browser or reconnect and replay failed actions.
    """

    def __init__(self, settings: Settings) -> None:
        """Remember the endpoint and timeout settings without connecting yet."""
        self.settings = settings

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[BrowserSession]:
        """Open, initialize, discover tools, and yield one usable browser session.

        The async context manager keeps the connection open while its caller runs
        the graph, then closes it on exit. Connection setup, calls, and cleanup
        stay in the same async task as required by the transport's cancel scopes.
        Errors are translated without exposing arbitrary server response text.
        """
        client = MultiServerMCPClient(
            {
                "playwright": {
                    "transport": "streamable_http",
                    "url": self.settings.mcp_url,
                    "timeout": timedelta(seconds=self.settings.tool_timeout_seconds),
                    "sse_read_timeout": timedelta(seconds=self.settings.tool_timeout_seconds),
                }
            }
        )
        entered = False
        body_error: BaseException | None = None
        try:
            # client.session defaults auto_initialize=True. Keep context entry,
            # invocations and exit in the same task (AnyIO cancellation scopes).
            async with client.session("playwright") as session:
                selected, capabilities = discovered_tools(await load_mcp_tools(session))
                entered = True
                try:
                    yield _ConnectedSession(selected, capabilities)
                except BaseException as exc:
                    body_error = exc
                    raise
        except MCPError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if body_error is not None:
                # Session cleanup can wrap a graph error; preserve the original cause.
                raise body_error from None
            for leaf in _leaf_errors(exc):
                if isinstance(leaf, MCPError):
                    raise leaf from None
                status = getattr(getattr(leaf, "response", None), "status_code", None)
                if isinstance(status, int):
                    raise MCPError(
                        "mcp_http_error",
                        f"MCP returned HTTP {status}; check endpoint, authentication "
                        "and allowed hosts.",
                    ) from None
            code = "mcp_disconnected" if entered else "mcp_unavailable"
            message = (
                "Playwright MCP session ended unexpectedly."
                if entered
                else "Cannot initialize Playwright MCP; check endpoint, server and transport."
            )
            raise MCPError(code, message) from exc


def _versions() -> dict[str, str | None]:
    """Report installed transport-package versions for connection diagnostics."""
    packages: dict[str, str | None] = {}
    for package in ("langchain-mcp-adapters", "mcp"):
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            packages[package] = None
    return packages


async def check_connection(settings: Settings) -> dict[str, Any]:
    """Check real connectivity and required tools, with no model or browser action."""
    try:
        async with asyncio.timeout(settings.tool_timeout_seconds):
            async with MCPConnection(settings).connect() as session:
                return {
                    "status": "connected",
                    "endpoint": settings.mcp_url,
                    "tools": sorted(session.tools),
                    "capabilities": session.capabilities,
                    "client_versions": _versions(),
                    "network_boundary_attested": settings.network_restricted,
                }
    except TimeoutError:
        return {
            "status": "failed",
            "error": {"code": "mcp_timeout", "message": "MCP connection check timed out."},
        }
    except MCPError as exc:
        return {"status": "failed", "error": {"code": exc.code, "message": str(exc)}}
