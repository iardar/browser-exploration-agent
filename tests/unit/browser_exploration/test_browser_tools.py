from typing import Any

import pytest
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel

from browser_exploration.browser_tools import (
    MCPError,
    argument_schema,
    discovered_tools,
    tool_specs,
)
from browser_exploration.mcp_client import _ConnectedSession


def fake_tool(name: str, properties: dict[str, Any] | None = None) -> BaseTool:
    async def invoke(**kwargs: Any) -> str:
        return "done"

    return StructuredTool(
        name=name,
        description="Untrusted server description",
        args_schema={"type": "object", "properties": properties or {}},
        coroutine=invoke,
    )


def test_dynamic_discovery_only_exposes_known_compatible_browser_tools() -> None:
    """Verify dynamic discovery only exposes known compatible browser tools.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for dynamic discovery only exposes known compatible browser
            tools."""
    tools = [
        fake_tool("browser_navigate", {"url": {"type": "string"}}),
        fake_tool("browser_snapshot"),
        fake_tool("browser_click", {"target": {"type": "string"}}),
        fake_tool("browser_hover", {"ref": {"type": "string"}}),
        fake_tool("browser_evaluate", {"function": {"type": "string"}}),
        fake_tool("browser_run_code"),
        fake_tool("browser_file_upload"),
        fake_tool("browser_tabs"),
        fake_tool("unknown_navigate", {"url": {"type": "string"}}),
    ]
    selected, capabilities = discovered_tools(tools)
    assert set(selected) == {
        "browser_navigate",
        "browser_snapshot",
        "browser_click",
        "browser_hover",
    }
    assert capabilities["click"] == "browser_click"


def test_incompatible_required_schema_fails_closed() -> None:
    """Verify incompatible required schema fails closed.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for incompatible required schema fails closed.
    """
    with pytest.raises(MCPError, match="navigate"):
        discovered_tools(
            [
                fake_tool("browser_navigate", {"url": {"type": "number"}}),
                fake_tool("browser_snapshot"),
            ]
        )
    tool = fake_tool("browser_snapshot")
    tool.args_schema = {"properties": {}, "required": ["filename"]}
    with pytest.raises(MCPError, match="snapshot"):
        discovered_tools([fake_tool("browser_navigate", {"url": {"type": "string"}}), tool])


def test_supported_alias_and_agent_catalog_hide_filesystem_arguments() -> None:
    """Verify supported alias and agent catalog hide filesystem arguments.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for supported alias and agent catalog hide filesystem
            arguments."""
    selected, capabilities = discovered_tools(
        [
            fake_tool("playwright_browser_navigate", {"url": {"type": "string"}}),
            fake_tool("playwright_browser_snapshot", {"filename": {"type": "string"}}),
        ]
    )
    specs = tool_specs(_ConnectedSession(selected, capabilities))
    assert capabilities["navigate"] == "playwright_browser_navigate"
    assert specs[1]["arguments"]["properties"] == {}
    assert "Untrusted" not in str(specs)


def test_pydantic_argument_schemas_are_supported() -> None:
    """Verify pydantic argument schemas are supported.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for pydantic argument schemas are supported.
    """

    class Input(BaseModel):
        url: str

    tool = fake_tool("browser_navigate")
    tool.args_schema = Input
    assert argument_schema(tool)["properties"]["url"]["type"] == "string"


def test_current_microsoft_screenshot_and_network_schemas_are_discovered() -> None:
    """Verify current microsoft screenshot and network schemas are discovered.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for current microsoft screenshot and network schemas are
            discovered."""
    screenshot = fake_tool("browser_take_screenshot")
    screenshot.args_schema = {
        "type": "object",
        "properties": {"scale": {"type": "string", "enum": ["css", "device"]}},
        "required": ["scale"],
    }
    network = fake_tool("browser_network_requests")
    network.args_schema = {
        "type": "object",
        "properties": {"static": {"type": "boolean"}},
        "required": ["static"],
    }
    selected, capabilities = discovered_tools(
        [
            fake_tool("browser_navigate", {"url": {"type": "string"}}),
            fake_tool("browser_snapshot"),
            screenshot,
            network,
        ]
    )
    assert capabilities["screenshot"] == screenshot.name
    assert capabilities["network"] == network.name
    assert len(tool_specs(_ConnectedSession(selected, capabilities))) == 4


def test_typing_catalog_explains_bare_references_without_changing_the_server_schema() -> None:
    """Verify typing catalog explains bare references without changing the server schema.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for typing catalog explains bare references without
            changing the server schema."""
    typing = fake_tool(
        "browser_type",
        {
            "target": {"type": "string", "description": "CSS selector"},
            "text": {"type": "string"},
        },
    )
    selected, capabilities = discovered_tools(
        [
            fake_tool("browser_navigate", {"url": {"type": "string"}}),
            fake_tool("browser_snapshot"),
            typing,
        ]
    )
    catalog = tool_specs(_ConnectedSession(selected, capabilities))
    target = catalog[-1]["arguments"]["properties"]["target"]
    assert 'pass "e43"' in target["description"]
    assert target["examples"] == ["e43"]
    assert argument_schema(typing)["properties"]["target"]["description"] == "CSS selector"
