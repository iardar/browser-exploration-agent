"""Protect tool discovery and form permissions at malformed-input and size boundaries."""

import copy
from typing import Any

import pytest

from browser_exploration.browser_tools import argument_schema, discovered_tools, tool_specs
from browser_exploration.config import Settings
from browser_exploration.contracts import BrowserExplorationTask
from browser_exploration.mcp_client import _ConnectedSession
from browser_exploration.observations import summarize_network_observation
from browser_exploration.safety import SafetyError, SafetyPolicy
from tests.unit.browser_exploration.test_browser_tools import fake_tool
from tests.unit.browser_exploration.test_tool_discovery_edge_cases import required_tools

CAPABILITIES = {
    "navigate": "browser_navigate",
    "type": "browser_type",
    "fill_form": "browser_fill_form",
    "click": "browser_click",
}


def form_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {key: {"type": "string"} for key in ("target", "name", "type", "value")},
        },
    }


@pytest.mark.parametrize(
    "schema",
    [
        None,
        {},
        {"type": "string"},
        {"type": "array"},
        {"type": "array", "items": {"type": "string"}},
        {"type": "array", "items": {"type": "object", "properties": []}},
        {
            "type": "array",
            "items": {"type": "object", "properties": {"target": {"type": "string"}}},
        },
    ],
)
def test_malformed_form_schemas_are_excluded_from_discovery(schema: Any) -> None:
    """Verify malformed form schemas are excluded from discovery.

    Arrange: Use `schema` parameter.
    Act: Call `fake_tool` to obtain `form`.
    Assert: Check `'fill_form' not in capabilities and form.name not in selected`.
    """
    form = fake_tool("browser_fill_form", {"fields": schema})
    selected, capabilities = discovered_tools([*required_tools(), form])
    assert "fill_form" not in capabilities and form.name not in selected


@pytest.mark.parametrize("field", ["target", "name", "type", "value"])
@pytest.mark.parametrize("bad_type", [None, {"type": "integer"}])
def test_each_form_property_requires_a_string_schema(field: str, bad_type: Any) -> None:
    """Verify each form property requires a string schema.

    Arrange: Use `field`, `bad_type` parameters.
    Act: Call `form_schema` to obtain `schema`.
    Assert: Check `'fill_form' not in capabilities`.
    """
    schema = form_schema()
    schema["items"]["properties"][field] = bad_type
    _, capabilities = discovered_tools(
        [*required_tools(), fake_tool("browser_fill_form", {"fields": schema})]
    )
    assert "fill_form" not in capabilities


def test_form_catalog_describes_nested_targets_without_modifying_server_schema() -> None:
    """Verify form catalog describes nested targets without modifying server schema.

    Arrange: Start with `form_schema()`.
    Act: Call `form_schema` to obtain `schema`.
    Assert: Check `target['examples'] == ['e43']`; `'latest accessibility snapshot' in
            target['description']`; `argument_schema(form)['properties']['fields'] == original`.
    """
    schema = form_schema()
    schema["items"]["properties"]["target"]["description"] = "CSS selector"
    original = copy.deepcopy(schema)
    form = fake_tool("browser_fill_form", {"fields": schema})
    tools, capabilities = discovered_tools([*required_tools(), form])
    spec = next(
        item
        for item in tool_specs(_ConnectedSession(tools, capabilities))
        if item["capability"] == "fill_form"
    )
    target = spec["arguments"]["properties"]["fields"]["items"]["properties"]["target"]
    assert target["examples"] == ["e43"]
    assert "latest accessibility snapshot" in target["description"]
    assert argument_schema(form)["properties"]["fields"] == original


@pytest.mark.parametrize("text_schema", [None, {"type": "integer"}])
def test_typing_tool_needs_text_and_reference_strings(text_schema: Any) -> None:
    """Verify typing tool needs text and reference strings.

    Arrange: Use `text_schema` parameter.
    Act: Call `fake_tool` to obtain `tool`.
    Assert: Check `'type' not in capabilities`.
    """
    tool = fake_tool("browser_type", {"ref": {"type": "string"}, "text": text_schema})
    _, capabilities = discovered_tools([*required_tools(), tool])
    assert "type" not in capabilities


def policy(*, input_allowed: bool = True, submit: bool = False) -> SafetyPolicy:
    result = SafetyPolicy(
        BrowserExplorationTask(
            url="https://example.com",
            objective="Search the catalogue",
            allow_form_input=input_allowed,
            allow_form_submission=submit,
            allow_mutations=True,
        ),
        Settings(network_restricted=True),
    )
    result.observe(
        "- Page URL: https://example.com\n### Snapshot\n"
        '- textbox "Search catalogue" [ref=q]\n'
        '- textbox "Password" [ref=secret]\n- button "Search" [ref=go]'
    )
    return result


@pytest.mark.parametrize(
    "capability,arguments", [("type", {"ref": "q", "text": "query"}), ("fill_form", {"fields": []})]
)
def test_mutation_permission_does_not_implicitly_allow_form_input(
    capability: str,
    arguments: dict[str, Any],
) -> None:
    """Verify mutation permission does not implicitly allow form input.

    Arrange: Use `capability`, `arguments` parameters.
    Act: Call `policy.authorize` under `pytest.raises(SafetyError)`.
    Assert: Check `raises SafetyError`.
    """
    with pytest.raises(SafetyError, match="allow_form_input"):
        policy(input_allowed=False).authorize(CAPABILITIES[capability], arguments, CAPABILITIES)


@pytest.mark.parametrize(
    "fields", [None, {}, [], [None], [{"target": "q", "file": "secret"}], [{}] * 21]
)
def test_form_field_container_and_keys_are_validated(fields: Any) -> None:
    """Verify form field container and keys are validated.

    Arrange: Use `fields` parameter.
    Act: Call `policy.authorize` under `pytest.raises(SafetyError)`.
    Assert: Check `raises SafetyError`.
    """
    with pytest.raises(SafetyError, match="fields|arguments"):
        policy().authorize("browser_fill_form", {"fields": fields}, CAPABILITIES)


@pytest.mark.parametrize(
    "field,reason",
    [
        ({"target": "secret", "type": "textbox", "value": "value"}, "Sensitive"),
        ({"type": "password"}, "type and value"),
        ({"value": 123}, "type and value"),
        ({"value": "x" * 2001}, "bounded text"),
        ({"value": "abc\x00def"}, "bounded text"),
        ({"type": "checkbox", "value": "yes"}, "true or false"),
    ],
)
def test_form_values_reject_sensitive_invalid_and_oversized_input(
    field: dict[str, Any],
    reason: str,
) -> None:
    """Verify form values reject sensitive invalid and oversized input.

    Arrange: Use `field`, `reason` parameters; prepare `fields`, `original`.
    Act: Call `policy.authorize` under `pytest.raises(SafetyError)`.
    Assert: Check `raises SafetyError`; `fields == original`.
    """
    fields = [{"target": "q", "type": "textbox", "value": "query", **field}]
    original = copy.deepcopy(fields)
    with pytest.raises(SafetyError, match=reason):
        policy().authorize("browser_fill_form", {"fields": fields}, CAPABILITIES)
    assert fields == original


def test_maximum_form_size_preserves_inputs_and_uses_observed_names() -> None:
    """Verify maximum form size preserves inputs and uses observed names.

    Arrange: prepare `fields`.
    Act: Call `copy.deepcopy` to obtain `original`.
    Assert: Check `name == 'browser_fill_form' and len(authorized['fields']) == 20`; `all()`;
            `fields == original`.
    """
    fields = [
        {
            "target": "q",
            "name": "Invented",
            "element": "Invented",
            "type": "textbox",
            "value": "x" * 2000,
        }
        for _ in range(20)
    ]
    original = copy.deepcopy(fields)
    name, authorized = policy(input_allowed=False, submit=True).authorize(
        "browser_fill_form",
        {"fields": fields},
        CAPABILITIES,
    )
    assert name == "browser_fill_form" and len(authorized["fields"]) == 20
    assert all(
        "Search catalogue" in item["name"] and item["name"] == item["element"]
        for item in authorized["fields"]
    )
    assert fields == original


@pytest.mark.parametrize(
    "options,reason",
    [
        ({"text": None}, "bounded text"),
        ({"text": "x" * 2001}, "bounded text"),
        ({"text": "\x00"}, "bounded text"),
        ({"slowly": "true"}, "boolean"),
        ({"submit": 1}, "boolean"),
        ({"submit": True}, "allow_form_submission"),
        ({"ref": "secret"}, "sensitive"),
    ],
)
def test_typing_keeps_value_and_submission_restrictions(
    options: dict[str, Any], reason: str
) -> None:
    """Verify typing keeps value and submission restrictions.

    Arrange: Use `options`, `reason` parameters.
    Act: Call `policy.authorize` under `pytest.raises(SafetyError)`.
    Assert: Check `raises SafetyError`.
    """
    with pytest.raises(SafetyError, match=reason):
        policy().authorize("browser_type", {"ref": "q", "text": "query", **options}, CAPABILITIES)


def test_modified_clicks_are_rejected_even_with_mutation_permission() -> None:
    """Verify modified clicks are rejected even with mutation permission.

    Arrange: Use the values passed to `pytest.raises`.
    Act: Call `policy.authorize` under `pytest.raises(SafetyError)`.
    Assert: Check `raises SafetyError`.
    """
    with pytest.raises(SafetyError, match="Modified clicks"):
        policy().authorize("browser_click", {"ref": "go", "modifiers": ["Control"]}, CAPABILITIES)


def test_repeated_static_omission_notes_are_not_double_counted() -> None:
    """Verify repeated static omission notes are not double counted.

    Arrange: prepare `note`.
    Act: Evaluate `summary.count` in the assertion.
    Assert: Check `summary.count('Static requests omitted by tool: 21.') == 1`; `'Network requests
            parsed: 1' in summary and 'Unrecognized lines' not in summary`.
    """
    note = "Note: 21 static requests not shown"
    summary = summarize_network_observation(
        f"[GET] https://example.com/ => [200]\n{note}\n{note}",
        1000,
    )
    assert summary.count("Static requests omitted by tool: 21.") == 1
    assert "Network requests parsed: 1" in summary and "Unrecognized lines" not in summary
