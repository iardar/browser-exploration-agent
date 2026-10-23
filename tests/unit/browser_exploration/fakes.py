"""Deterministic test doubles; these never open a browser or contact a model."""

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langchain_core.messages import BaseMessage
from langchain_core.tools import StructuredTool

from browser_exploration.browser_tools import BrowserSession, discovered_tools
from browser_exploration.model import Decision, DecisionResponse


def snapshot(url: str = "https://example.com/") -> list[dict[str, str]]:
    return [
        {
            "type": "text",
            "text": (
                f"### Page\n- Page URL: {url}\n### Snapshot\n"
                '- heading "MOCK article" [ref=e0]\n'
                '- link "Help" [ref=e1]:\n  - /url: /help\n'
                '- button "Open menu" [ref=e2]\n'
            ),
        }
    ]


def fake_tool(name: str, properties: dict[str, Any] | None = None) -> StructuredTool:
    return StructuredTool.from_function(
        func=lambda **kwargs: "unused",
        name=name,
        description="Offline test double",
        args_schema={"type": "object", "properties": properties or {}},
    )


class FakeSession:
    def __init__(self, responses: list[Any] | None = None, *, blocked: bool = False) -> None:
        self.tools, self.capabilities = discovered_tools(
            [
                fake_tool("browser_navigate", {"url": {"type": "string"}}),
                fake_tool("browser_snapshot"),
                fake_tool("browser_hover", {"ref": {"type": "string"}}),
                fake_tool("browser_click", {"ref": {"type": "string"}}),
            ]
        )
        self.responses = deque(responses or [])
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.url = "https://example.com/"
        self.blocked = blocked
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        self.calls.append((name, args))
        self.started.set()
        if self.blocked:
            await self.release.wait()
        if self.responses:
            response = self.responses.popleft()
            if isinstance(response, BaseException):
                raise response
            return response
        if name == "browser_navigate":
            self.url = args["url"]
        return snapshot(self.url)


class FakeConnection:
    def __init__(
        self,
        session: FakeSession | None = None,
        error: Exception | None = None,
        *,
        blocked: bool = False,
        close_error: Exception | None = None,
    ) -> None:
        self.session = session or FakeSession()
        self.error = error
        self.entries = 0
        self.exits = 0
        self.blocked = blocked
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.close_error = close_error

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[BrowserSession]:
        self.entries += 1
        if self.error is not None:
            raise self.error
        try:
            self.started.set()
            if self.blocked:
                await self.release.wait()
            yield self.session
        finally:
            self.exits += 1
            if self.close_error is not None:
                raise self.close_error


class FakeModel:
    def __init__(
        self,
        responses: list[DecisionResponse | BaseException] | None = None,
        *,
        blocked: bool = False,
    ) -> None:
        self.responses = deque(responses or [DecisionResponse(Decision(done=True))])
        self.requests: list[tuple[list[BaseMessage], list[dict[str, Any]]]] = []
        self.blocked = blocked
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def decide(
        self, messages: list[BaseMessage], tools: list[dict[str, Any]]
    ) -> DecisionResponse:
        self.requests.append((messages, tools))
        self.started.set()
        if self.blocked:
            await self.release.wait()
        response = self.responses.popleft()
        if isinstance(response, BaseException):
            raise response
        return response
