"""Inspect the current MCP server's advertised tools without calling a model or browser tool."""

import argparse
import asyncio
import json
import sys
from datetime import timedelta
from typing import Any

from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient
from pydantic import ValidationError

from browser_exploration.config import Settings


async def observe_tools(settings: Settings, names: set[str]) -> dict[str, Any]:
    """Initialize one MCP session and read every page of its advertised tool catalog."""
    client = MultiServerMCPClient(
        {
            "playwright": {
                "transport": "streamable_http",
                "url": settings.mcp_url,
                "timeout": timedelta(seconds=settings.tool_timeout_seconds),
                "sse_read_timeout": timedelta(seconds=settings.tool_timeout_seconds),
            }
        }
    )
    advertised: dict[str, dict[str, Any]] = {}
    async with asyncio.timeout(settings.tool_timeout_seconds):
        async with client.session("playwright") as session:
            cursor: str | None = None
            while True:
                page = await session.list_tools(cursor=cursor)
                for tool in page.tools:
                    advertised[tool.name] = tool.inputSchema
                cursor = page.nextCursor
                if not cursor:
                    break
    result: dict[str, Any] = {
        "status": "connected",
        "endpoint": settings.mcp_url,
        "advertised_tool_count": len(advertised),
    }
    if names:
        result["tools"] = [
            {"name": name, "input_schema": advertised[name]}
            for name in sorted(names)
            if name in advertised
        ]
        result["missing_tools"] = sorted(names - advertised.keys())
    else:
        result["tool_names"] = sorted(advertised)
    return result


def error_types(error: BaseException) -> list[str]:
    """Report nested transport exception types without printing server response text."""
    if isinstance(error, BaseExceptionGroup):
        return sorted({name for child in error.exceptions for name in error_types(child)})
    return [type(error).__name__]


def main() -> int:
    """Print all tool names, or full input schemas for exact names passed with --tool."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="Override PLAYWRIGHT_MCP_URL for this probe")
    parser.add_argument("--tool", action="append", default=[], help="Show this tool's input schema")
    args = parser.parse_args()
    load_dotenv(override=False)
    try:
        settings = Settings(mcp_url=args.url) if args.url else Settings()
    except ValidationError as exc:
        fields = [".".join(map(str, error["loc"])) for error in exc.errors()]
        print(json.dumps({"status": "failed", "error": "invalid_configuration", "fields": fields}))
        return 2
    try:
        result = asyncio.run(observe_tools(settings, set(args.tool)))
    except Exception as exc:
        # Transport errors may contain server text or URLs with sensitive data.
        print(json.dumps({"status": "failed", "error_types": error_types(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
