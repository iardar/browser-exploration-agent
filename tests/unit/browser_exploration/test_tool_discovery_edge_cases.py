"""Tool-schema compatibility and safe discovery under schema/version variation."""

from typing import Any

import pytest
from langchain_core.tools import BaseTool, StructuredTool
from pydantic.v1 import BaseModel as LegacyBaseModel

from browser_exploration.browser_tools import (
    MCPError,
    argument_schema,
    discovered_tools,
    tool_specs,
)
from browser_exploration.mcp_client import _ConnectedSession
from tests.unit.browser_exploration.test_browser_tools import fake_tool


def required_tools() -> list[BaseTool]:
    return [
        fake_tool("browser_navigate", {"url": {"type": "string"}}),
        fake_tool("browser_snapshot"),
    ]


@pytest.mark.parametrize("properties", [None, [], "url", 3])
def test_malformed_optional_tool_properties_are_not_exposed(properties: Any) -> None:
    """Verify malformed optional tool properties are not exposed.

    Arrange: Use `properties` parameter.
    Act: Call `fake_tool` to obtain `hover`.
    Assert: Check `'hover' not in capabilities`; `hover.name not in selected`.
    """
    hover = fake_tool("browser_hover")
    hover.args_schema = {"properties": properties}
    selected, capabilities = discovered_tools([*required_tools(), hover])
    assert "hover" not in capabilities
    assert hover.name not in selected


@pytest.mark.parametrize("required", [None, "url", [1], {"url": True}])
def test_malformed_required_fields_cannot_claim_a_browser_capability(required: Any) -> None:
    """Verify malformed required fields cannot claim a browser capability.

    Arrange: Use `required` parameter; prepare `navigate`.
    Act: Call `discovered_tools` under `pytest.raises(MCPError)`.
    Assert: Check `raises MCPError`; `failure.value.code == 'missing_browser_tools'`.
    """
    navigate = fake_tool("browser_navigate", {"url": {"type": "string"}})
    navigate.args_schema = {"properties": {"url": {"type": "string"}}, "required": required}
    with pytest.raises(MCPError) as failure:
        discovered_tools([navigate, fake_tool("browser_snapshot")])
    assert failure.value.code == "missing_browser_tools"


def test_optional_tool_with_filesystem_requirement_is_omitted() -> None:
    """Verify optional tool with filesystem requirement is omitted.

    Arrange: Start with `fake_tool('browser_take_screenshot', {'filename': {'type': 'string'}})`.
    Act: Call `fake_tool` to obtain `screenshot`.
    Assert: Check `'screenshot' not in capabilities`; `screenshot.name not in selected`.
    """
    screenshot = fake_tool("browser_take_screenshot", {"filename": {"type": "string"}})
    screenshot.args_schema = {
        "properties": {"filename": {"type": "string"}},
        "required": ["filename"],
    }
    selected, capabilities = discovered_tools([*required_tools(), screenshot])
    assert "screenshot" not in capabilities
    assert screenshot.name not in selected


def test_compatible_alias_is_selected_when_preferred_name_has_incompatible_schema() -> None:
    """Verify compatible alias is selected when preferred name has incompatible schema.

    Arrange: Start with `fake_tool('playwright_browser_navigate', {'url': {'type': 'string'}})`.
    Act: Call `fake_tool` to obtain `alternate`.
    Assert: Check `capabilities['navigate'] == alternate.name`; `selected[alternate.name] is
            alternate`; `'browser_navigate' not in selected`.
    """
    alternate = fake_tool("playwright_browser_navigate", {"url": {"type": "string"}})
    selected, capabilities = discovered_tools(
        [
            fake_tool("browser_navigate", {"url": {"type": "integer"}}),
            alternate,
            fake_tool("browser_snapshot"),
        ]
    )
    assert capabilities["navigate"] == alternate.name
    assert selected[alternate.name] is alternate
    assert "browser_navigate" not in selected


def test_alias_preference_is_deterministic_independent_of_server_listing_order() -> None:
    """Verify alias preference is deterministic independent of server listing order.

    Arrange: Start with `fake_tool('browser_navigate', {'url': {'type': 'string'}})`.
    Act: Call `fake_tool` to obtain `preferred`.
    Assert: Check `capabilities['navigate'] == preferred.name`; `selected[preferred.name] is
            preferred`; `alternate.name not in selected`.
    """
    preferred = fake_tool("browser_navigate", {"url": {"type": "string"}})
    alternate = fake_tool("playwright_browser_navigate", {"url": {"type": "string"}})
    for tools in ([preferred, alternate], [alternate, preferred]):
        selected, capabilities = discovered_tools([*tools, fake_tool("browser_snapshot")])
        assert capabilities["navigate"] == preferred.name
        assert selected[preferred.name] is preferred
        assert alternate.name not in selected


def test_legacy_pydantic_schema_remains_compatible_without_execution() -> None:
    """Verify legacy pydantic schema remains compatible without execution.

    Arrange: Supply `name`, `description`, `args_schema`, `func` to `StructuredTool`.
    Act: Call `StructuredTool` to obtain `legacy`.
    Assert: Check `argument_schema(legacy)['required'] == ['url']`; `selected['browser_navigate'] is
            legacy`; `specs[0]['arguments']['properties']['url']['type'] == 'string'`.
    """

    class LegacyNavigation(LegacyBaseModel):
        url: str

    legacy = StructuredTool(
        name="browser_navigate",
        description="Legacy tool",
        args_schema=LegacyNavigation,
        func=lambda url: url,
    )
    assert argument_schema(legacy)["required"] == ["url"]
    selected, capabilities = discovered_tools([legacy, fake_tool("browser_snapshot")])
    assert selected["browser_navigate"] is legacy
    specs = tool_specs(_ConnectedSession(selected, capabilities))
    assert specs[0]["arguments"]["properties"]["url"]["type"] == "string"


def test_tool_without_explicit_schema_uses_its_real_inferred_input_schema() -> None:
    """Verify tool without explicit schema uses its real inferred input schema.

    Arrange: Start with `InferredNavigation()`.
    Act: Call `InferredNavigation` to obtain `inferred`.
    Assert: Check `inferred.args_schema is None`; `schema['properties']['url']['type'] == 'string'`;
            `capabilities['navigate'] == inferred.name`.
    """

    class InferredNavigation(BaseTool):
        name: str = "browser_navigate"
        description: str = "Navigation tool with inferred schema"

        def _run(self, url: str) -> str:
            raise AssertionError("Discovery must not execute a browser tool")

    inferred = InferredNavigation()
    assert inferred.args_schema is None
    schema = argument_schema(inferred)
    assert schema["properties"]["url"]["type"] == "string"
    _, capabilities = discovered_tools([inferred, fake_tool("browser_snapshot")])
    assert capabilities["navigate"] == inferred.name


def test_catalog_filtering_does_not_mutate_original_tool_schemas() -> None:
    """Verify catalog filtering does not mutate original tool schemas.

    Arrange: Start with `required_tools()`.
    Act: Call `required_tools` to obtain `tools`.
    Assert: Check `catalog[1]['arguments']['properties'] == {}`; `original_schema['properties'] ==
            {'filename': {'type': 'string'}}`.
    """
    tools = required_tools()
    original_schema = {
        "type": "object",
        "properties": {"filename": {"type": "string"}},
        "required": [],
    }
    tools[1].args_schema = original_schema
    selected, capabilities = discovered_tools(tools)
    catalog = tool_specs(_ConnectedSession(selected, capabilities))
    assert catalog[1]["arguments"]["properties"] == {}
    assert original_schema["properties"] == {"filename": {"type": "string"}}
