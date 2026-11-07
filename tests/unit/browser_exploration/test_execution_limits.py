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
    """Verify browser budget stops even a model that always requests more.

    Arrange: Use `tmp_path`, `max_steps` parameters; prepare `model`, `connection`.
    Act: Await `AutonomousBrowserExplorer.explore` to obtain `result`.
    Assert: Check `result.status == 'step_limit'`; `result.metrics.browser_tool_calls ==
            len(connection.session.calls) == max_steps`; `result.metrics.llm_calls == max(0,
            max_steps - 2)`; `connection.entries == connection.exits == 1`.
    """
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
    """Verify model budget stops after allowed iterations.

    Arrange: Use `tmp_path` parameter; prepare `model`.
    Act: Await `AutonomousBrowserExplorer.explore` to obtain `result`.
    Assert: Check `result.status == 'model_limit'`; `result.metrics.llm_calls == len(model.requests)
            == 1`; `result.metrics.browser_tool_calls == 3`; `result.errors[-1].code ==
            'model_call_limit'`.
    """
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
    """Verify model can complete on its last allowed iteration.

    Arrange: Use `tmp_path` parameter.
    Act: Await `AutonomousBrowserExplorer.explore` to obtain `result`.
    Assert: Check `result.status == 'completed'`; `result.metrics.llm_calls == 1`.
    """
    result = await AutonomousBrowserExplorer(
        settings(tmp_path), connection=FakeConnection(), model=FakeModel()
    ).explore(task(max_model_iterations=1))
    assert result.status == "completed"
    assert result.metrics.llm_calls == 1


async def test_actual_tool_timeout_records_failed_observation_and_closes_session(
    tmp_path: Path,
) -> None:
    """Verify actual tool timeout records failed observation and closes session.

    Arrange: Use `tmp_path` parameter; prepare `connection`.
    Act: Await `AutonomousBrowserExplorer.explore` to obtain `result`.
    Assert: Check `result.status == 'timeout'`; `result.errors[-1].code == 'tool_timeout'`;
            `result.metrics.failed_browser_tool_calls == 1`; `'outcome is unknown' in
            observation['summary']`.
    """
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
    """Verify mcp transport timeout is a timeout result.

    Arrange: Use `tmp_path` parameter; prepare `connection`.
    Act: Await `AutonomousBrowserExplorer.explore` to obtain `result`.
    Assert: Check `result.status == 'timeout'`; `result.metrics.failed_browser_tool_calls == 1`.
    """
    connection = FakeConnection(FakeSession([MCPError("tool_timeout", "MCP timed out")]))
    result = await AutonomousBrowserExplorer(
        settings(tmp_path), connection=connection, model=FakeModel()
    ).explore(task())
    assert result.status == "timeout"
    assert result.metrics.failed_browser_tool_calls == 1


async def test_wall_clock_deadline_interrupts_model_and_preserves_completed_observations(
    tmp_path: Path,
) -> None:
    """Verify wall clock deadline interrupts model and preserves completed observations.

    Arrange: Use `tmp_path` parameter; prepare `connection`.
    Act: Await `AutonomousBrowserExplorer.explore` to obtain `result`.
    Assert: Check `result.status == 'timeout'`; `result.errors[-1].code == 'execution_timeout'`;
            `result.metrics.browser_tool_calls == 2`; `len(saved['evidence']) == 2`.
    """
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
    """Verify cancellation during tool returns persisted cancelled status.

    Arrange: Use `tmp_path` parameter; prepare `session`.
    Act: Start exploration with a blocked tool, cancel the task after the tool starts, and await
         the persisted result.
    Assert: Check `result.status == 'cancelled'`; `result.metrics.browser_tool_calls ==
            result.metrics.failed_browser_tool_calls == 1`;
            `result.metrics.successful_browser_tool_calls == 0`; `'interrupted' in
            observation['summary']`.
    """
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
    """Verify cancellation during model keeps browser evidence.

    Arrange: Use `tmp_path` parameter.
    Act: Start exploration with a blocked model, cancel after its request starts, and await the
         result with the earlier browser observations.
    Assert: Check `result.status == 'cancelled'`; `result.metrics.browser_tool_calls ==
            result.metrics.successful_browser_tool_calls == 2`; `result.metrics.llm_calls == 1`;
            `connection.exits == 1`.
    """
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
    """Verify cancellation during connection is persisted without actions.

    Arrange: Use `tmp_path` parameter.
    Act: Start exploration with a blocked connection, cancel before any browser action, and await
         the persisted result.
    Assert: Check `result.status == 'cancelled'`; `result.metrics.browser_tool_calls ==
            result.metrics.llm_calls == 0`; `connection.exits == 1`; `saved['status'] ==
            'cancelled'`.
    """
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
    """Verify wall clock limit includes connection setup.

    Arrange: Use `tmp_path` parameter; prepare `connection`.
    Act: Await `AutonomousBrowserExplorer.explore` to obtain `result`.
    Assert: Check `result.status == 'timeout'`; `result.metrics.browser_tool_calls == 0`;
            `connection.exits == 1`.
    """
    connection = FakeConnection(blocked=True)
    result = await AutonomousBrowserExplorer(
        settings(tmp_path), connection=connection, model=FakeModel()
    ).explore(task(max_duration_seconds=1))
    assert result.status == "timeout"
    assert result.metrics.browser_tool_calls == 0
    assert connection.exits == 1


async def test_caller_cannot_mutate_budgets_after_run_starts(tmp_path: Path) -> None:
    """Verify caller cannot mutate budgets after run starts.

    Arrange: Use `tmp_path` parameter.
    Act: Start a run with a two-step limit, raise the caller's task limit after it starts, then
         release the blocked connection and await the result.
    Assert: Check `result.status == 'step_limit'`; `result.metrics.browser_tool_calls == 2`.
    """
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
