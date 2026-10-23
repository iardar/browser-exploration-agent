"""Finite, schema-validated Playwright tool discovery.

Server-provided descriptions are untrusted. Capability selection uses exact known
names and compatible argument schemas; JavaScript and filesystem tools are never
part of the agent's catalog.
"""

from copy import deepcopy
from typing import Any, Protocol

from langchain_core.tools import BaseTool
from pydantic import BaseModel


class MCPError(Exception):
    """A diagnostic safe to include in a report (never raw transport output)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class BrowserSession(Protocol):
    tools: dict[str, BaseTool]
    capabilities: dict[str, str]

    async def call(self, name: str, args: dict[str, Any]) -> Any: ...


ALIASES: dict[str, tuple[str, ...]] = {
    "navigate": ("browser_navigate", "playwright_browser_navigate"),
    "snapshot": ("browser_snapshot", "playwright_browser_snapshot"),
    "click": ("browser_click", "playwright_browser_click"),
    "hover": ("browser_hover", "playwright_browser_hover"),
    "type": ("browser_type", "playwright_browser_type"),
    "fill_form": ("browser_fill_form", "playwright_browser_fill_form"),
    "screenshot": (
        "browser_take_screenshot",
        "playwright_browser_take_screenshot",
        "browser_screenshot",
    ),
    "console": ("browser_console_messages", "playwright_browser_console_messages"),
    "network": ("browser_network_requests", "playwright_browser_network_requests"),
}

ARGUMENTS: dict[str, frozenset[str]] = {
    "navigate": frozenset({"url"}),
    "snapshot": frozenset(),
    "click": frozenset({"ref", "target", "element", "doubleClick", "button", "modifiers"}),
    "hover": frozenset({"ref", "target", "element"}),
    "type": frozenset({"ref", "target", "element", "text", "submit", "slowly"}),
    "fill_form": frozenset({"fields"}),
    "screenshot": frozenset({"type", "fullPage", "ref", "target", "element", "scale"}),
    "console": frozenset({"level", "onlyErrors"}),
    "network": frozenset({"includeStatic", "static"}),
}

REFERENCE_DESCRIPTION = (
    "Bare element reference copied from the latest accessibility snapshot. For [ref=e43], "
    'pass "e43". Never pass "ref=e43", "#e43", a CSS/XPath selector, or an element label.'
)


def _describe_references(properties: dict[str, Any]) -> None:
    """Replace ambiguous server targeting descriptions with the executor contract."""
    for key in ("ref", "target"):
        if isinstance(properties.get(key), dict):
            properties[key]["description"] = REFERENCE_DESCRIPTION
            properties[key]["examples"] = ["e43"]
    fields = properties.get("fields", {})
    if isinstance(fields, dict) and isinstance(fields.get("items"), dict):
        nested = fields["items"].get("properties")
        if isinstance(nested, dict):
            _describe_references(nested)


def argument_schema(tool: BaseTool) -> dict[str, Any]:
    """Get the actual advertised schema, excluding execution-only LC fields."""
    schema = tool.args_schema
    if isinstance(schema, dict):
        return dict(schema)
    if schema is None:
        schema = tool.get_input_schema()
    if issubclass(schema, BaseModel):
        return dict(schema.model_json_schema())
    return dict(schema.schema())


def _compatible(tool: BaseTool, capability: str) -> bool:
    schema = argument_schema(tool)
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return False
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(isinstance(k, str) for k in required):
        return False
    if not set(required).issubset(ARGUMENTS[capability]):
        return False
    expected = {
        "navigate": ("url",),
        "click": ("ref", "target"),
        "hover": ("ref", "target"),
        "type": ("ref", "target"),
    }
    if capability in expected:
        if not any(
            isinstance(properties.get(key), dict) and properties[key].get("type") == "string"
            for key in expected[capability]
        ):
            return False
    if capability == "type":
        text = properties.get("text")
        if not isinstance(text, dict) or text.get("type") != "string":
            return False
    if capability == "fill_form":
        fields = properties.get("fields")
        if not isinstance(fields, dict) or fields.get("type") != "array":
            return False
        items = fields.get("items")
        if not isinstance(items, dict) or items.get("type") != "object":
            return False
        field_properties = items.get("properties")
        if not isinstance(field_properties, dict):
            return False
        required_fields = {"target", "name", "type", "value"}
        if not required_fields.issubset(field_properties):
            return False
        if not all(
            isinstance(field_properties[key], dict)
            and field_properties[key].get("type") == "string"
            for key in required_fields
        ):
            return False
    return True


def discovered_tools(
    tools: list[BaseTool],
) -> tuple[dict[str, BaseTool], dict[str, str]]:
    """Select supported tools, requiring navigation and accessibility snapshots."""
    by_name = {tool.name: tool for tool in tools}
    selected: dict[str, BaseTool] = {}
    capabilities: dict[str, str] = {}
    for capability, aliases in ALIASES.items():
        for name in aliases:
            tool = by_name.get(name)
            if tool is not None and _compatible(tool, capability):
                selected[name] = tool
                capabilities[capability] = name
                break
    missing = {"navigate", "snapshot"} - capabilities.keys()
    if missing:
        raise MCPError(
            "missing_browser_tools",
            "MCP server lacks compatible required capabilities: " + ", ".join(sorted(missing)),
        )
    return selected, capabilities


def tool_specs(session: BrowserSession) -> list[dict[str, Any]]:
    """Expose bounded argument schemas, with local trusted capability descriptions."""
    specs = []
    for capability, name in session.capabilities.items():
        schema = argument_schema(session.tools[name])
        allowed = ARGUMENTS[capability]
        properties = {
            key: deepcopy(value)
            for key, value in schema.get("properties", {}).items()
            if key in allowed
        }
        _describe_references(properties)
        specs.append(
            {
                "name": name,
                "capability": capability,
                "arguments": {
                    "type": "object",
                    "properties": properties,
                    "required": schema.get("required", []),
                    "additionalProperties": False,
                },
            }
        )
    return specs
