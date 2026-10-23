"""Exercise the real LangGraph with offline model and browser dependencies."""

import json
from pathlib import Path
from typing import Any

import pytest

from browser_exploration.config import Settings
from browser_exploration.contracts import (
    BrowserExplorationResult,
    BrowserExplorationTask,
    BrowserFinding,
    VerifiedBehavior,
)
from browser_exploration.explorer import AutonomousBrowserExplorer
from browser_exploration.graph import ExplorationNodes, build_graph, observation_text
from browser_exploration.mcp_client import MCPError
from browser_exploration.model import Action, Decision, DecisionResponse, ModelError
from tests.unit.browser_exploration.fakes import FakeConnection, FakeModel, FakeSession, snapshot


def task(**updates: Any) -> BrowserExplorationTask:
    return BrowserExplorationTask(url="https://example.com/", objective="Explore Help", **updates)


def settings(path: Path, **updates: Any) -> Settings:
    return Settings(output_dir=path, network_restricted=True, **updates)


async def test_oversized_authoritative_context_is_blocked_before_connection(tmp_path: Path) -> None:
    connection = FakeConnection()
    result = await AutonomousBrowserExplorer(
        settings(tmp_path), connection=connection, model=FakeModel()
    ).explore(
        BrowserExplorationTask(
            url="https://example.com/",
            objective="Explore " * 10000 + "Never follow links.",
            max_context_chars=4096,
        )
    )
    assert result.status == "blocked"
    assert result.errors[-1].code == "context_limit"
    assert connection.entries == 0


async def test_snapshot_without_page_content_does_not_become_success(tmp_path: Path) -> None:
    session = FakeSession(
        [snapshot(), [{"type": "text", "text": "- Page URL: https://example.com/"}]]
    )
    result = await AutonomousBrowserExplorer(
        settings(tmp_path), connection=FakeConnection(session), model=FakeModel()
    ).explore(task())
    assert result.status == "failed"
    assert result.errors[-1].code == "insufficient_observation"
    assert result.metrics.llm_calls == 0


async def test_real_graph_completes_autonomous_action_and_persists_one_session(
    tmp_path: Path,
) -> None:
    connection = FakeConnection()
    model = FakeModel(
        [
            DecisionResponse(
                Decision(action=Action(name="browser_click", arguments={"ref": "e1"}))
            ),
            DecisionResponse(
                Decision(
                    done=True,
                    verified_behaviors=[
                        VerifiedBehavior(
                            description="Mock Help page loads", evidence_ids=["evidence-obs-0003"]
                        )
                    ],
                )
            ),
        ]
    )
    explorer = AutonomousBrowserExplorer(settings(tmp_path), connection=connection, model=model)
    result = await explorer.explore(task())

    assert result.status == "completed"
    assert connection.entries == connection.exits == 1
    assert connection.session.calls == [
        ("browser_navigate", {"url": "https://example.com/"}),
        ("browser_snapshot", {}),
        ("browser_navigate", {"url": "https://example.com/help"}),
    ]
    assert result.metrics.browser_tool_calls == result.metrics.successful_browser_tool_calls == 3
    assert result.metrics.failed_browser_tool_calls == 0
    assert result.metrics.llm_calls == 2
    assert result.metrics.verified_behaviors_count == 1
    assert result.metrics.input_tokens is None
    assert len(result.evidence) == 3
    directory = tmp_path / result.run_id
    saved = BrowserExplorationResult.model_validate_json((directory / "result.json").read_text())
    assert saved == result
    assert json.loads((directory / "metrics.json").read_text()) == result.metrics.model_dump()
    trajectory = [
        json.loads(line) for line in (directory / "trajectory.jsonl").read_text().splitlines()
    ]
    assert len(trajectory) == 3
    assert trajectory[-1]["url"] == "https://example.com/help"
    assert trajectory[-1]["evidence_ref"] == result.evidence[-1].id
    assert not (directory / "evidence").exists()


async def test_graph_has_three_stages_and_conditional_exploration_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[Any] = []

    def capture(nodes: ExplorationNodes) -> Any:
        graph = build_graph(nodes)
        captured.append(graph.get_graph())
        return graph

    monkeypatch.setattr("browser_exploration.explorer.build_graph", capture)
    result = await AutonomousBrowserExplorer(
        settings(tmp_path), connection=FakeConnection(), model=FakeModel()
    ).explore(task())
    assert result.status == "completed"
    graph = captured[0]
    assert set(graph.nodes) == {
        "__start__",
        "prepare_context",
        "explore_browser",
        "finalize_report",
        "__end__",
    }
    assert {(edge.source, edge.target) for edge in graph.edges} == {
        ("__start__", "prepare_context"),
        ("prepare_context", "explore_browser"),
        ("prepare_context", "finalize_report"),
        ("explore_browser", "explore_browser"),
        ("explore_browser", "finalize_report"),
        ("finalize_report", "__end__"),
    }


async def test_context_history_stays_bounded_but_trajectory_remains_complete(
    tmp_path: Path,
) -> None:
    model = FakeModel(
        [
            DecisionResponse(
                Decision(action=Action(name="browser_hover", arguments={"ref": "e2"}))
            ),
            DecisionResponse(Decision(action=Action(name="browser_snapshot"))),
            DecisionResponse(
                Decision(action=Action(name="browser_hover", arguments={"ref": "e2"}))
            ),
            DecisionResponse(Decision(action=Action(name="browser_snapshot"))),
            DecisionResponse(Decision(done=True)),
        ]
    )
    result = await AutonomousBrowserExplorer(
        settings(tmp_path), connection=FakeConnection(), model=model
    ).explore(task(max_recent_observations=2, max_observation_chars=400, max_context_chars=4096))
    assert result.status == "completed"
    assert result.metrics.browser_tool_calls == len(result.evidence) == 6
    assert all(len(messages) == 2 for messages, _ in model.requests)
    assert all(
        sum(len(str(message.content)) for message in messages) <= 4096
        for messages, _ in model.requests
    )
    latest = str(model.requests[-1][0][-1].content)
    assert "evidence-obs-0006" in latest
    assert "evidence-obs-0001" not in latest
    assert len((tmp_path / result.run_id / "trajectory.jsonl").read_text().splitlines()) == 6


async def test_confirmed_findings_require_known_successful_evidence_and_reproduction(
    tmp_path: Path,
) -> None:
    def finding(identifier: str, evidence_id: str, steps: list[str]) -> BrowserFinding:
        return BrowserFinding(
            id=identifier,
            title="Mock finding",
            description="Mock description",
            expected_behavior="Mock expectation",
            observed_behavior="Mock observation",
            evidence_ids=[evidence_id],
            reproduction_steps=steps,
            verification_status="confirmed",
        )

    model = FakeModel(
        [
            DecisionResponse(
                Decision(
                    done=True,
                    verified_behaviors=[
                        VerifiedBehavior(
                            description="Visible heading", evidence_ids=["evidence-obs-0002"]
                        ),
                        VerifiedBehavior(description="Invented check", evidence_ids=["invented"]),
                    ],
                    findings=[
                        finding("valid", "evidence-obs-0002", ["Open mock article"]),
                        finding("invented", "invented", ["Open mock article"]),
                        finding("no-reproduction", "evidence-obs-0002", []),
                    ],
                )
            )
        ]
    )
    result = await AutonomousBrowserExplorer(
        settings(tmp_path), connection=FakeConnection(), model=model
    ).explore(task())
    findings = {item.id: item for item in result.findings}
    assert findings["valid"].verification_status == "confirmed"
    assert findings["invented"].verification_status == "inconclusive"
    assert findings["invented"].evidence_ids == []
    assert findings["no-reproduction"].verification_status == "inconclusive"
    assert len(result.verified_behaviors) == 1
    assert result.unresolved_questions
    assert result.metrics.findings_count == 3


@pytest.mark.parametrize(
    "error",
    [
        MCPError("mcp_disconnected", "Session lost"),
        MCPError("tool_execution_error", "Navigation failed"),
        RuntimeError("Authorization: Bearer never-report-this-secret"),
    ],
)
async def test_tool_failure_is_recorded_and_does_not_become_a_finding(
    tmp_path: Path, error: Exception
) -> None:
    model = FakeModel()
    result = await AutonomousBrowserExplorer(
        settings(tmp_path), connection=FakeConnection(FakeSession([error])), model=model
    ).explore(task())
    assert result.status == "failed"
    assert result.metrics.browser_tool_calls == result.metrics.failed_browser_tool_calls == 1
    assert result.metrics.successful_browser_tool_calls == result.metrics.llm_calls == 0
    assert result.findings == result.verified_behaviors == []
    assert model.requests == []
    assert len(result.evidence) == 1
    saved = (tmp_path / result.run_id / "result.json").read_text()
    assert "never-report-this-secret" not in saved
    observation = json.loads((tmp_path / result.run_id / "trajectory.jsonl").read_text())
    assert observation["error"] is True


@pytest.mark.parametrize(
    "raw, status, code",
    [
        ([], "failed", "empty_observation"),
        (
            {"isError": True, "content": [{"type": "text", "text": "Navigation failed"}]},
            "failed",
            "mcp_tool_error",
        ),
        ([{"type": "text", "text": "No URL in this snapshot"}], "blocked", "unsafe_observation"),
        (snapshot("https://outside.example.org/"), "blocked", "unsafe_observation"),
    ],
)
async def test_unusable_or_unsafe_browser_observation_is_terminal(
    tmp_path: Path, raw: Any, status: str, code: str
) -> None:
    result = await AutonomousBrowserExplorer(
        settings(tmp_path), connection=FakeConnection(FakeSession([raw])), model=FakeModel()
    ).explore(task())
    assert result.status == status
    assert result.errors[-1].code == code
    assert result.metrics.failed_browser_tool_calls == 1
    assert result.verified_behaviors == []


async def test_mcp_unavailable_produces_artifacts_without_browser_calls(tmp_path: Path) -> None:
    result = await AutonomousBrowserExplorer(
        settings(tmp_path),
        connection=FakeConnection(error=MCPError("mcp_unavailable", "Cannot connect")),
        model=FakeModel(),
    ).explore(task())
    assert result.status == "failed"
    assert result.errors[0].code == "mcp_unavailable"
    assert result.metrics.browser_tool_calls == result.metrics.llm_calls == 0
    assert (tmp_path / result.run_id / "result.json").is_file()
    assert (tmp_path / result.run_id / "trajectory.jsonl").read_text() == ""


@pytest.mark.parametrize("code", ["llm_api_error", "llm_validation_error"])
async def test_model_failure_keeps_actual_status_and_provider_usage(
    tmp_path: Path, code: str
) -> None:
    result = await AutonomousBrowserExplorer(
        settings(tmp_path),
        connection=FakeConnection(),
        model=FakeModel(
            [
                ModelError(
                    code,
                    "Model failed",
                    {
                        "input_tokens": 30,
                        "output_tokens": 4,
                        "total_tokens": 34,
                    },
                )
            ]
        ),
    ).explore(task(max_model_iterations=1))
    assert result.status == "failed"
    assert result.errors[-1].code == code
    assert result.metrics.llm_calls == 1
    assert result.metrics.input_tokens == 30
    assert result.metrics.output_tokens == 4
    assert result.metrics.total_tokens == 34
    assert result.findings == []


async def test_policy_blocks_before_connection_and_retries_denied_model_actions(
    tmp_path: Path,
) -> None:
    connection = FakeConnection()
    result = await AutonomousBrowserExplorer(
        Settings(output_dir=tmp_path), connection=connection, model=FakeModel()
    ).explore(task())
    assert result.status == "blocked"
    assert connection.entries == 0
    assert result.metrics.browser_tool_calls == 0

    model = FakeModel(
        [
            DecisionResponse(
                Decision(
                    action=Action(name="browser_evaluate", arguments={"function": "() => alert(1)"})
                )
            ),
            DecisionResponse(Decision(done=True)),
        ]
    )
    result = await AutonomousBrowserExplorer(
        settings(tmp_path), connection=connection, model=model
    ).explore(task())
    assert result.status == "completed"
    assert result.metrics.browser_tool_calls == 2
    assert result.metrics.llm_calls == 2
    assert result.unresolved_questions
    assert "browser_evaluate" in result.unresolved_questions[0]
    assert result.errors == []
    assert all(name != "browser_evaluate" for name, _ in connection.session.calls)


def test_binary_evidence_is_never_in_model_observation_text() -> None:
    text = observation_text(
        [
            {"type": "text", "text": "Visible page"},
            {"type": "image", "data": "private-base64-bytes", "mimeType": "image/png"},
            {"type": "file", "data": "private-base64-bytes"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,private-base64-bytes"},
            },
        ]
    )
    assert "Visible page" in text
    assert "private-base64-bytes" not in text


async def test_malformed_injected_decision_is_validated_before_executing(tmp_path: Path) -> None:
    invalid = Decision.model_construct(done=True, action=Action(name="browser_snapshot"))
    result = await AutonomousBrowserExplorer(
        settings(tmp_path),
        connection=FakeConnection(),
        model=FakeModel([DecisionResponse(invalid)]),
    ).explore(task(max_model_iterations=1))
    assert result.status == "failed"
    assert result.errors[-1].code == "llm_validation_error"
    assert result.metrics.browser_tool_calls == 2
    assert result.metrics.llm_calls == 1


async def test_session_disconnect_during_cleanup_does_not_report_completion(tmp_path: Path) -> None:
    result = await AutonomousBrowserExplorer(
        settings(tmp_path),
        connection=FakeConnection(close_error=MCPError("mcp_disconnected", "Session closed")),
        model=FakeModel(),
    ).explore(task())
    assert result.status == "failed"
    assert result.errors[-1].code == "mcp_disconnected"
    saved = json.loads((tmp_path / result.run_id / "result.json").read_text())
    assert saved["status"] == "failed"


async def test_report_tokens_use_only_usage_from_all_decisions(tmp_path: Path) -> None:
    model = FakeModel(
        [
            DecisionResponse(
                Decision(action=Action(name="browser_snapshot")),
                {
                    "input_tokens": 12,
                    "output_tokens": 3,
                    "total_tokens": 15,
                },
            ),
            DecisionResponse(
                Decision(done=True),
                {
                    "input_tokens": 20,
                    "output_tokens": 4,
                    "total_tokens": 24,
                },
            ),
        ]
    )
    result = await AutonomousBrowserExplorer(
        settings(tmp_path), connection=FakeConnection(), model=model
    ).explore(task())
    assert result.metrics.input_tokens == 32
    assert result.metrics.output_tokens == 7
    assert result.metrics.total_tokens == 39


async def test_raw_evidence_opt_in_keeps_reproduction_payloads(tmp_path: Path) -> None:
    result = await AutonomousBrowserExplorer(
        settings(tmp_path, retain_raw_evidence=True),
        connection=FakeConnection(),
        model=FakeModel(),
    ).explore(task(max_observation_chars=30))
    assert result.status == "completed"
    assert all(reference.retained for reference in result.evidence)
    for reference in result.evidence:
        assert reference.path is not None
        assert "MOCK article" in (tmp_path / result.run_id / reference.path).read_text()
