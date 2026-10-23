"""Budgets and interruption use terminal, persisted statuses rather than success."""

import asyncio
import json
from pathlib import Path

import pytest

from browser_exploration.explorer import AutonomousBrowserExplorer
from browser_exploration.mcp_client import MCPError
from browser_exploration.model import Action, Decision, DecisionResponse
from tests.unit.browser_exploration.fakes import FakeConnection, FakeModel, FakeSession
from tests.unit.browser_exploration.test_graph import settings, task


@pytest.mark.parametrize("max_steps", [1, 2, 3])
async def test_browser_budget_stops_even_a_model_that_always_requests_more(
    tmp_path: Path, max_steps: int
) -> None:
    model = FakeModel(
        [
            DecisionResponse(Decision(action=Action(name="browser_hover", arguments={"ref": "e2"})))
            for _ in range(5)
        ]
    )
    connection = FakeConnection()
    result = await AutonomousBrowserExplorer(
        settings(tmp_path), connection=connection, model=model
    ).explore(task(max_steps=max_steps))
    assert result.status == "step_limit"
    assert result.metrics.browser_tool_calls == len(connection.session.calls) == max_steps
    assert result.metrics.llm_calls == max(0, max_steps - 2)
    assert result.errors[-1].code == "browser_call_limit"
    assert connection.entries == connection.exits == 1


async def test_model_budget_stops_after_allowed_iterations(tmp_path: Path) -> None:
    model = FakeModel(
        [
            DecisionResponse(Decision(action=Action(name="browser_hover", arguments={"ref": "e2"})))
            for _ in range(5)
        ]
    )
    result = await AutonomousBrowserExplorer(
        settings(tmp_path), connection=FakeConnection(), model=model
    ).explore(task(max_model_iterations=1))
    assert result.status == "model_limit"
    assert result.metrics.llm_calls == len(model.requests) == 1
    assert result.metrics.browser_tool_calls == 3
    assert result.errors[-1].code == "model_call_limit"


async def test_model_can_complete_on_its_last_allowed_iteration(tmp_path: Path) -> None:
    result = await AutonomousBrowserExplorer(
        settings(tmp_path), connection=FakeConnection(), model=FakeModel()
    ).explore(task(max_model_iterations=1))
    assert result.status == "completed"
    assert result.metrics.llm_calls == 1


async def test_actual_tool_timeout_records_failed_observation_and_closes_session(
    tmp_path: Path,
) -> None:
    connection = FakeConnection(FakeSession(blocked=True))
    result = await AutonomousBrowserExplorer(
        settings(tmp_path, tool_timeout_seconds=0.01), connection=connection, model=FakeModel()
    ).explore(task())
    assert result.status == "timeout"
    assert result.errors[-1].code == "tool_timeout"
    assert result.metrics.failed_browser_tool_calls == 1
    assert connection.exits == 1
    observation = json.loads((tmp_path / result.run_id / "trajectory.jsonl").read_text())
    assert observation["error"] is True
    assert "outcome is unknown" in observation["summary"]


async def test_mcp_transport_timeout_is_a_timeout_result(tmp_path: Path) -> None:
    connection = FakeConnection(FakeSession([MCPError("tool_timeout", "MCP timed out")]))
    result = await AutonomousBrowserExplorer(
        settings(tmp_path), connection=connection, model=FakeModel()
    ).explore(task())
    assert result.status == "timeout"
    assert result.metrics.failed_browser_tool_calls == 1


async def test_wall_clock_deadline_interrupts_model_and_preserves_completed_observations(
    tmp_path: Path,
) -> None:
    connection = FakeConnection()
    result = await AutonomousBrowserExplorer(
        settings(tmp_path), connection=connection, model=FakeModel(blocked=True)
    ).explore(task(max_duration_seconds=1))
    assert result.status == "timeout"
    assert result.errors[-1].code == "execution_timeout"
    assert result.metrics.browser_tool_calls == 2
    assert result.metrics.llm_calls == 1
    assert result.metrics.total_tokens is None
    assert connection.exits == 1
    saved = json.loads((tmp_path / result.run_id / "result.json").read_text())
    assert saved["status"] == "timeout"
    assert len(saved["evidence"]) == 2


async def test_cancellation_during_tool_returns_persisted_cancelled_status(tmp_path: Path) -> None:
    session = FakeSession(blocked=True)
    connection = FakeConnection(session)
    explorer = AutonomousBrowserExplorer(
        settings(tmp_path), connection=connection, model=FakeModel()
    )
    running = asyncio.create_task(explorer.explore(task()))
    await asyncio.wait_for(session.started.wait(), timeout=5)
    running.cancel()
    result = await running
    assert result.status == "cancelled"
    assert result.metrics.browser_tool_calls == result.metrics.failed_browser_tool_calls == 1
    assert result.metrics.successful_browser_tool_calls == 0
    assert connection.exits == 1
    saved = json.loads((tmp_path / result.run_id / "result.json").read_text())
    assert saved["status"] == "cancelled"
    observation = json.loads((tmp_path / result.run_id / "trajectory.jsonl").read_text())
    assert observation["error"] is True
    assert "interrupted" in observation["summary"]


async def test_cancellation_during_model_keeps_browser_evidence(tmp_path: Path) -> None:
    model = FakeModel(blocked=True)
    connection = FakeConnection()
    explorer = AutonomousBrowserExplorer(settings(tmp_path), connection=connection, model=model)
    running = asyncio.create_task(explorer.explore(task()))
    await asyncio.wait_for(model.started.wait(), timeout=5)
    running.cancel()
    result = await running
    assert result.status == "cancelled"
    assert result.metrics.browser_tool_calls == result.metrics.successful_browser_tool_calls == 2
    assert result.metrics.llm_calls == 1
    assert result.metrics.total_tokens is None
    assert len(result.evidence) == 2
    assert connection.exits == 1


async def test_cancellation_during_connection_is_persisted_without_actions(tmp_path: Path) -> None:
    connection = FakeConnection(blocked=True)
    explorer = AutonomousBrowserExplorer(
        settings(tmp_path), connection=connection, model=FakeModel()
    )
    running = asyncio.create_task(explorer.explore(task()))
    await asyncio.wait_for(connection.started.wait(), timeout=5)
    running.cancel()
    result = await running
    assert result.status == "cancelled"
    assert result.metrics.browser_tool_calls == result.metrics.llm_calls == 0
    assert connection.exits == 1
    assert result.evidence == []
    saved = json.loads((tmp_path / result.run_id / "result.json").read_text())
    assert saved["status"] == "cancelled"


async def test_wall_clock_limit_includes_connection_setup(tmp_path: Path) -> None:
    connection = FakeConnection(blocked=True)
    result = await AutonomousBrowserExplorer(
        settings(tmp_path), connection=connection, model=FakeModel()
    ).explore(task(max_duration_seconds=1))
    assert result.status == "timeout"
    assert result.metrics.browser_tool_calls == 0
    assert connection.exits == 1


async def test_caller_cannot_mutate_budgets_after_run_starts(tmp_path: Path) -> None:
    connection = FakeConnection(blocked=True)
    original = task(max_steps=2)
    running = asyncio.create_task(
        AutonomousBrowserExplorer(
            settings(tmp_path), connection=connection, model=FakeModel()
        ).explore(original)
    )
    await asyncio.wait_for(connection.started.wait(), timeout=5)
    original.max_steps = 100
    connection.release.set()
    result = await running
    assert result.status == "step_limit"
    assert result.metrics.browser_tool_calls == 2
