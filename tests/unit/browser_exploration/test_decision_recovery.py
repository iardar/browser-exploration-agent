"""Recover malformed decisions without repeating completed browser interactions."""

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from browser_exploration.explorer import AutonomousBrowserExplorer
from browser_exploration.model import Action, Decision, DecisionResponse, ModelError
from tests.unit.browser_exploration.fakes import FakeConnection, FakeModel, fake_tool
from tests.unit.browser_exploration.test_graph import settings, task
from tests.unit.browser_exploration.test_model import make_adapter


async def test_invalid_final_report_recovers_without_recapturing_screenshot(tmp_path: Path) -> None:
    connection = FakeConnection()
    connection.session.capabilities["screenshot"] = "browser_take_screenshot"
    connection.session.tools["browser_take_screenshot"] = fake_tool("browser_take_screenshot")
    invalid_usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    empty_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    model = FakeModel(
        [
            DecisionResponse(Decision(action=Action(name="browser_take_screenshot")), empty_usage),
            ModelError(
                "llm_validation_error",
                "Invalid decision",
                invalid_usage,
                diagnostics={"kind": "schema_validation"},
                provider_model="reported-model",
            ),
            DecisionResponse(Decision(done=True), empty_usage),
        ]
    )
    result = await AutonomousBrowserExplorer(
        settings(tmp_path), connection=connection, model=model
    ).explore(task(max_model_iterations=5))
    assert result.status == "completed"
    assert result.metrics.llm_calls == 3
    assert result.metrics.browser_tool_calls == 3
    assert result.observed_actions.count("browser_take_screenshot") == 1
    assert result.metrics.total_tokens == 15
    assert result.errors == []
    retry_context = str(model.requests[-1][0][1].content)
    assert "failed schema validation" in retry_context
    assert '"screenshot_captured": true' in retry_context
    assert "do not replay" in retry_context
    events = [
        json.loads(line)
        for line in (tmp_path / result.run_id / "decisions.jsonl").read_text().splitlines()
    ]
    assert events[1]["event"] == "model_error"
    assert events[1]["provider_model"] == "reported-model"


@pytest.mark.parametrize("budget,expected_calls", [(1, 1), (2, 2), (5, 3)])
async def test_invalid_decision_retries_respect_budgets_and_stop_after_two_retries(
    tmp_path: Path, budget: int, expected_calls: int
) -> None:
    model = FakeModel([ModelError("llm_validation_error", "Invalid decision") for _ in range(5)])
    result = await AutonomousBrowserExplorer(
        settings(tmp_path), connection=FakeConnection(), model=model
    ).explore(task(max_model_iterations=budget))
    assert result.status == "failed"
    assert result.errors[-1].code == "llm_validation_error"
    assert result.metrics.llm_calls == expected_calls
    assert result.metrics.browser_tool_calls == 2


async def test_schema_diagnostics_preserve_paths_and_types_without_values_or_exception_text() -> (
    None
):
    with pytest.raises(ValidationError) as failure:
        Decision.model_validate(
            {
                "done": True,
                "verified_behaviors": [{"description": "Check", "evidence_ids": "private-value"}],
            }
        )
    adapter, _, _ = make_adapter(
        {
            "raw": AIMessage(
                content="private-raw", response_metadata={"model_name": "actual-model"}
            ),
            "parsed": None,
            "parsing_error": failure.value,
        }
    )
    with pytest.raises(ModelError) as rejected:
        await adapter.decide([], [])
    assert rejected.value.diagnostics["issues"] == [
        {"location": ["verified_behaviors", 0, "evidence_ids"], "type": "list_type"}
    ]
    assert "private" not in json.dumps(rejected.value.diagnostics)
    assert rejected.value.provider_model == "actual-model"


async def test_valid_decision_retains_provider_model_metadata() -> None:
    adapter, _, _ = make_adapter(
        {
            "raw": AIMessage(content="", response_metadata={"model_name": "actual-model"}),
            "parsed": Decision(done=True),
        }
    )
    assert (await adapter.decide([], [])).provider_model == "actual-model"
