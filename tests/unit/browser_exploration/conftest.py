"""Keep the browser agent unit suite independent of operator settings and services."""

import os
import socket
from collections.abc import Iterator
from typing import Any

import pytest

from browser_exploration import cli


@pytest.fixture(autouse=True)
def offline_agent_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in tuple(os.environ):
        if name.startswith("BROWSER_") or name in {
            "OPENAI_API_KEY",
            "OPENAI_MODEL",
            "PLAYWRIGHT_MCP_URL",
            "LANGSMITH_API_KEY",
            "LANGCHAIN_API_KEY",
        }:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setattr(cli, "load_dotenv", lambda **kwargs: False)

    # asyncio uses local socket pairs for thread wakeups. Permit these while
    # rejecting every IPv4/IPv6 connection and DNS lookup, including loopback.
    attempts: list[str] = []
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def connect(sock: socket.socket, address: Any) -> None:
        if sock.family in {socket.AF_INET, socket.AF_INET6}:
            attempts.append("connect")
            raise AssertionError("Unit tests must inject a fake browser/model transport")
        original_connect(sock, address)

    def connect_ex(sock: socket.socket, address: Any) -> int:
        if sock.family in {socket.AF_INET, socket.AF_INET6}:
            attempts.append("connect_ex")
            raise AssertionError("Unit tests must inject a fake browser/model transport")
        return original_connect_ex(sock, address)

    def resolve(*args: Any, **kwargs: Any) -> Any:
        attempts.append("DNS lookup")
        raise AssertionError("Unit tests must not resolve network destinations")

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    yield
    assert not attempts, f"Forbidden network operations attempted: {attempts}"
