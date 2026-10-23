"""Explicitly opt-in MCP smoke test against the bundled static demo, without an LLM."""

import asyncio
import json
import os
from urllib.parse import urljoin, urlsplit

import pytest

from browser_exploration.config import Settings
from browser_exploration.mcp_client import MCPConnection

pytestmark = [
    pytest.mark.live_mcp,
    pytest.mark.skipif(
        os.environ.get("QA_BROWSER_LIVE_MCP") != "1",
        reason="Set QA_BROWSER_LIVE_MCP=1 and start the controlled demo and Playwright MCP",
    ),
]


@pytest.mark.asyncio
async def test_controlled_demo_uses_persistent_mcp_session() -> None:
    """Navigation followed by snapshots retains browser state in the same MCP session."""
    target = os.environ.get("QA_BROWSER_LIVE_URL", "http://127.0.0.1:8765/")
    parsed = urlsplit(target)
    assert parsed.scheme == "http", "The live fixture must use the local static demo"
    assert parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    assert parsed.username is None and parsed.password is None
    settings = Settings()

    async with asyncio.timeout(60):
        async with MCPConnection(settings).connect() as session:
            navigate = session.capabilities["navigate"]
            snapshot = session.capabilities["snapshot"]
            await session.call(navigate, {"url": target})
            first = await session.call(snapshot, {})
            assert "Browser exploration controlled demo" in json.dumps(first, default=str)

            await session.call(navigate, {"url": urljoin(target, "/details.html")})
            second = await session.call(snapshot, {})
            assert "Controlled article details" in json.dumps(second, default=str)
            assert "Return to controlled demo" in json.dumps(second, default=str)
