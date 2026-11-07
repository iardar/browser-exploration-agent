"""Replay reference-format and missing-inline-snapshot failures from the Google runs."""

import json
from pathlib import Path
from typing import Any

import pytest

from browser_exploration.contracts import BrowserExplorationTask
from browser_exploration.explorer import AutonomousBrowserExplorer
from browser_exploration.model import Action, Decision, DecisionResponse
from browser_exploration.safety import SafetyError, SafetyPolicy
from tests.unit.browser_exploration.fakes import FakeConnection, FakeModel, FakeSession, fake_tool
from tests.unit.browser_exploration.test_graph import settings

SEARCH_PAGE = (
    "### Page\n- Page URL: https://www.google.com/\n### Snapshot\n"
    '- combobox "Գտնել" [active] [ref=e43]\n'
)
RESULTS_LINK = (
    "### Page\n- Page URL: https://www.google.com/search?q=Armavir\n"
    "- Page Title: Armavir - Google\n### Snapshot\n"
    "- [Snapshot](.playwright-mcp/results.yml)"
)
RESULTS_PAGE = (
    "### Page\n- Page URL: https://www.google.com/search?q=Armavir\n### Snapshot\n"
    '- heading "Armavir results" [ref=e90]\n'
)
CAPABILITIES = {"type": "browser_type", "fill_form": "browser_fill_form"}


def search_task(**updates: object) -> BrowserExplorationTask:
    values: dict[str, Any] = {
        "url": "https://www.google.com/",
        "objective": ("Search Armavir, inspect results, take a full-page screenshot, then finish."),
        "allow_form_submission": True,
    }
    values.update(updates)
    return BrowserExplorationTask(**values)


@pytest.mark.parametrize("reference", ["e43", "ref=e43", "[ref=e43]", "#e43"])
def test_reference_wrappers_authorize_only_the_exact_observed_control(
    tmp_path: Path, reference: str
) -> None:
    """Verify reference wrappers authorize only the exact observed control.

    Arrange: Use `tmp_path`, `reference` parameters.
    Act: Call `SafetyPolicy` to obtain `policy`.
    Assert: Check `name == 'browser_type'`; `args['target'] == 'e43'`.
    """
    policy = SafetyPolicy(search_task(), settings(tmp_path))
    policy.observe(SEARCH_PAGE)
    name, args = policy.authorize(
        "browser_type", {"target": reference, "text": "Armavir", "submit": True}, CAPABILITIES
    )
    assert name == "browser_type"
    assert args["target"] == "e43"


@pytest.mark.parametrize(
    "reference", ["ref=stale", "#e99", "#e43 input", "combobox[aria-label='Գտնել']"]
)
def test_selectors_and_unobserved_reference_wrappers_remain_rejected(
    tmp_path: Path, reference: str
) -> None:
    """Verify selectors and unobserved reference wrappers remain rejected.

    Arrange: Use `tmp_path`, `reference` parameters; prepare `policy`.
    Act: Call `policy.authorize` under `pytest.raises(SafetyError)`.
    Assert: Check `raises SafetyError`.
    """
    policy = SafetyPolicy(search_task(), settings(tmp_path))
    policy.observe(SEARCH_PAGE)
    with pytest.raises(SafetyError, match='bare string "e43"'):
        policy.authorize("browser_type", {"target": reference, "text": "Armavir"}, CAPABILITIES)


def test_reference_normalization_does_not_bypass_sensitive_field_policy(tmp_path: Path) -> None:
    """Verify reference normalization does not bypass sensitive field policy.

    Arrange: Use `tmp_path` parameter; prepare `policy`.
    Act: Call `policy.authorize` under `pytest.raises(SafetyError)`.
    Assert: Check `raises SafetyError`.
    """
    policy = SafetyPolicy(search_task(), settings(tmp_path))
    policy.observe(SEARCH_PAGE.replace('"Գտնել"', '"Password"'))
    with pytest.raises(SafetyError, match="sensitive"):
        policy.authorize("browser_type", {"target": "#e43", "text": "example"}, CAPABILITIES)


async def test_rejected_selector_recovers_and_results_are_inspected_before_completion(
    tmp_path: Path,
) -> None:
    """Verify rejected selector recovers and results are inspected before completion.

    Arrange: Use `tmp_path` parameter; prepare `session`, `model`.
    Act: Await `AutonomousBrowserExplorer.explore` to obtain `result`.
    Assert: Check `result.status == 'completed'`; `result.metrics.llm_calls == 4`;
            `result.metrics.browser_tool_calls == 5`; `'e43' in events[1]['reason']`.
    """
    session = FakeSession(
        [SEARCH_PAGE, SEARCH_PAGE, RESULTS_LINK, RESULTS_PAGE, "Screenshot of full page captured"]
    )
    for capability, tool in (("type", "browser_type"), ("screenshot", "browser_take_screenshot")):
        session.capabilities[capability] = tool
        session.tools[tool] = fake_tool(tool)
    model = FakeModel(
        [
            DecisionResponse(
                Decision(
                    action=Action(
                        name="browser_type",
                        arguments={"target": "combobox[aria-label='Գտնել']", "text": "Armavir"},
                    )
                )
            ),
            DecisionResponse(
                Decision(
                    action=Action(
                        name="browser_type",
                        arguments={"target": "ref=e43", "text": "Armavir", "submit": True},
                    )
                )
            ),
            DecisionResponse(
                Decision(
                    action=Action(name="browser_take_screenshot", arguments={"fullPage": True})
                )
            ),
            DecisionResponse(Decision(done=True)),
        ]
    )
    result = await AutonomousBrowserExplorer(
        settings(tmp_path, model="comparison-model"),
        connection=FakeConnection(session),
        model=model,
    ).explore(search_task(max_model_iterations=5))

    assert result.status == "completed"
    assert result.metrics.llm_calls == 4
    assert result.metrics.browser_tool_calls == 5
    assert session.calls[2] == (
        "browser_type",
        {"target": "e43", "text": "Armavir", "submit": True},
    )
    assert session.calls[3] == ("browser_snapshot", {})
    feedback_context = str(model.requests[1][0][1].content)
    assert feedback_context.index("LATEST EXECUTOR FEEDBACK") < feedback_context.index(
        "RECENT OBSERVATION"
    )
    assert 'bare string "e43"' in feedback_context
    assert "Armavir results" in str(model.requests[2][0][1].content)
    directory = tmp_path / result.run_id
    config = json.loads((directory / "run_config.json").read_text())
    assert config["configured_model"] == "comparison-model"
    assert config["model_adapter"] == "FakeModel"
    events = [json.loads(line) for line in (directory / "decisions.jsonl").read_text().splitlines()]
    assert [event["event"] for event in events].count("model_decision") == 4
    assert events[1]["event"] == "action_rejected"
    assert events[1]["iteration"] == 1
    assert "e43" in events[1]["reason"]


def test_form_filling_uses_the_same_reference_normalization(tmp_path: Path) -> None:
    """Verify form filling uses the same reference normalization.

    Arrange: Use `tmp_path` parameter.
    Act: Call `SafetyPolicy` to obtain `policy`.
    Assert: Check `args['fields'][0]['target'] == 'e43'`.
    """
    policy = SafetyPolicy(search_task(), settings(tmp_path))
    policy.observe(SEARCH_PAGE)
    _, args = policy.authorize(
        "browser_fill_form",
        {
            "fields": [
                {"target": "[ref=e43]", "name": "Search", "type": "textbox", "value": "Armavir"}
            ]
        },
        CAPABILITIES,
    )
    assert args["fields"][0]["target"] == "e43"
