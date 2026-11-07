"""Boundary failures exercise the real graph, persistence, and injected dependencies."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from browser_exploration import graph
from browser_exploration.config import Settings
from browser_exploration.contracts import (
    BrowserExplorationResult,
    BrowserFinding,
    VerifiedBehavior,
)
from browser_exploration.explorer import AutonomousBrowserExplorer
from browser_exploration.model import Action, Decision, DecisionResponse
from tests.unit.browser_exploration.fakes import (
    FakeConnection,
    FakeModel,
    FakeSession,
    fake_tool,
    snapshot,
)
from tests.unit.browser_exploration.test_graph import settings, task


@pytest.mark.parametrize("raw", [None, 42, False, object()])
def test_non_content_values_do_not_invent_observation_text(raw: Any) -> None:
    """Verify non content values do not invent observation text.

    Arrange: Use `raw` parameter.
    Act: Evaluate `graph.observation_text` in the assertion.
    Assert: Check `graph.observation_text(raw) == ''`.
    """
    assert graph.observation_text(raw) == ""


def test_plain_and_nested_text_preserve_order_but_fallback_metadata_is_redacted() -> None:
    """Verify plain and nested text preserve order but fallback metadata is redacted.

    Arrange: Start with `graph.observation_text()`.
    Act: Evaluate `text.splitlines` in the assertion.
    Assert: Check `text.splitlines()[:2] == ['First', 'Second']`; `json.loads(text.splitlines()[2])
            == {'token': '[REDACTED]'}`; `'private' not in text`.
    """
    text = graph.observation_text(
        ["First", {"content": [{"type": "text", "text": "Second"}]}, {"token": "private"}]
    )
    assert text.splitlines()[:2] == ["First", "Second"]
    assert json.loads(text.splitlines()[2]) == {"token": "[REDACTED]"}
    assert "private" not in text


async def test_context_overflow_after_navigation_stops_before_model_call(tmp_path: Path) -> None:
    """Verify context overflow after navigation stops before model call.

    Arrange: Use `tmp_path` parameter; prepare `redirected`, `session`, `connection`, `model`.
    Act: Await `AutonomousBrowserExplorer.explore` to obtain `result`.
    Assert: Check `result.status == 'blocked'`; `result.errors[-1].code == 'context_limit'`;
            `result.metrics.browser_tool_calls == result.metrics.successful_browser_tool_calls ==
            2`; `saved['status'] == 'blocked'`.
    """
    redirected = "https://example.com/" + "a" * 5_000
    session = FakeSession([snapshot(redirected), snapshot(redirected)])
    connection = FakeConnection(session)
    model = FakeModel()
    result = await AutonomousBrowserExplorer(
        settings(tmp_path), connection=connection, model=model
    ).explore(task(max_context_chars=4096))

    assert result.status == "blocked"
    assert result.errors[-1].code == "context_limit"
    assert result.metrics.browser_tool_calls == result.metrics.successful_browser_tool_calls == 2
    assert result.metrics.llm_calls == 0
    assert model.requests == []
    assert len(result.evidence) == 2
    assert connection.exits == 1
    saved = json.loads((tmp_path / result.run_id / "result.json").read_text())
    assert saved["status"] == "blocked"


async def test_exact_wall_clock_boundary_stops_before_the_next_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Replace only the graph's clock; changing the asyncio clock would hide deadline bugs.
    """Verify exact wall clock boundary stops before the next action.

    Arrange: Use `tmp_path`, `monkeypatch` parameters; prepare `clock`, `connection`, `model`.
    Act: Await `AutonomousBrowserExplorer.explore` to obtain `result`.
    Assert: Check `result.status == 'timeout'`; `result.errors[-1].code == 'execution_timeout'`;
            `result.metrics.duration_seconds == 10.0`; `len(result.evidence) == connection.exits ==
            1`.
    """
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(graph, "time", SimpleNamespace(monotonic=lambda: clock.now))

    class AdvancingSession(FakeSession):
        async def call(self, name: str, args: dict[str, Any]) -> Any:
            response = await super().call(name, args)
            clock.now += 10.0
            return response

    connection = FakeConnection(AdvancingSession())
    model = FakeModel()
    result = await AutonomousBrowserExplorer(
        settings(tmp_path), connection=connection, model=model
    ).explore(task(max_duration_seconds=10))

    assert result.status == "timeout"
    assert result.errors[-1].code == "execution_timeout"
    assert result.metrics.duration_seconds == 10.0
    assert result.observed_actions == ["browser_navigate"]
    assert result.metrics.browser_tool_calls == result.metrics.successful_browser_tool_calls == 1
    assert result.metrics.failed_browser_tool_calls == result.metrics.llm_calls == 0
    assert model.requests == []
    assert len(result.evidence) == connection.exits == 1


async def test_missing_model_credentials_fail_after_bootstrap_without_provider_calls(
    tmp_path: Path,
) -> None:
    """Verify missing model credentials fail after bootstrap without provider calls.

    Arrange: Use `tmp_path` parameter; prepare `connection`.
    Act: Await `AutonomousBrowserExplorer.explore` to obtain `result`.
    Assert: Check `result.status == 'failed'`; `result.errors[-1].code == 'llm_configuration'`;
            `result.metrics.browser_tool_calls == 2`; `connection.entries == connection.exits == 1`.
    """
    connection = FakeConnection()
    result = await AutonomousBrowserExplorer(settings(tmp_path), connection=connection).explore(
        task()
    )
    assert result.status == "failed"
    assert result.errors[-1].code == "llm_configuration"
    assert result.metrics.browser_tool_calls == 2
    assert result.metrics.llm_calls == 0
    assert result.metrics.total_tokens is None
    assert len(result.evidence) == 2
    assert connection.entries == connection.exits == 1


async def test_lazy_model_is_initialized_once_and_reused_across_decisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify lazy model is initialized once and reused across decisions.

    Arrange: Use `tmp_path`, `monkeypatch` parameters; prepare `connection`, `model`, `constructed`.
    Act: Await `AutonomousBrowserExplorer.explore` to obtain `result`.
    Assert: Check `result.status == 'completed'`; `len(constructed) == 1`; `len(model.requests) ==
            result.metrics.llm_calls == 2`; `len(connection.session.calls) == 2`.
    """
    connection = FakeConnection()
    model = FakeModel(
        [
            DecisionResponse(Decision(action=Action(name="browser_snapshot"))),
            DecisionResponse(Decision(done=True)),
        ]
    )
    constructed: list[Settings] = []

    def model_factory(configuration: Settings) -> FakeModel:
        assert len(connection.session.calls) == 2
        constructed.append(configuration)
        return model

    monkeypatch.setattr(graph, "OpenAIDecisionModel", model_factory)
    result = await AutonomousBrowserExplorer(settings(tmp_path), connection=connection).explore(
        task()
    )
    assert result.status == "completed"
    assert len(constructed) == 1
    assert len(model.requests) == result.metrics.llm_calls == 2
    assert result.metrics.browser_tool_calls == 2
    assert "already succeeded" in result.unresolved_questions[0]


@pytest.mark.parametrize("failure_stage", ["connect", "model", "cleanup"])
async def test_unexpected_dependency_errors_persist_actual_failure_without_secrets(
    tmp_path: Path, failure_stage: str
) -> None:
    """Verify unexpected dependency errors persist actual failure without secrets.

    Arrange: Use `tmp_path`, `failure_stage` parameters; prepare `error`, `connection`, `model`.
    Act: Await `AutonomousBrowserExplorer.explore` to obtain `result`.
    Assert: Check `result.status == 'failed'`; `result.errors[-1].code == 'unexpected_error'`;
            `'RuntimeError' in result.errors[-1].message`; `all()`.
    """
    error = RuntimeError("authorization: Bearer private-service-credential")
    connection = FakeConnection(
        error=error if failure_stage == "connect" else None,
        close_error=error if failure_stage == "cleanup" else None,
    )
    model = FakeModel([error] if failure_stage == "model" else None)
    result = await AutonomousBrowserExplorer(
        settings(tmp_path), connection=connection, model=model
    ).explore(task())

    assert result.status == "failed"
    assert result.errors[-1].code == "unexpected_error"
    assert "RuntimeError" in result.errors[-1].message
    expected_calls = 0 if failure_stage == "connect" else 2
    assert result.metrics.browser_tool_calls == len(result.evidence) == expected_calls
    assert result.metrics.llm_calls == (0 if failure_stage == "connect" else 1)
    assert result.metrics.total_tokens is None
    assert connection.entries == 1
    assert connection.exits == (0 if failure_stage == "connect" else 1)
    directory = tmp_path / result.run_id
    saved = BrowserExplorationResult.model_validate_json((directory / "result.json").read_text())
    assert saved == result
    assert len((directory / "trajectory.jsonl").read_text().splitlines()) == expected_calls
    assert all("private-service-credential" not in path.read_text() for path in directory.iterdir())


async def test_repeated_checks_are_deduplicated_and_findings_can_be_verified_later(
    tmp_path: Path,
) -> None:
    """Verify repeated checks are deduplicated and findings can be verified later.

    Arrange: Use `tmp_path` parameter; prepare `behavior`, `potential`, `confirmed`, `model`.
    Act: Await `AutonomousBrowserExplorer.explore` to obtain `result`.
    Assert: Check `result.status == 'completed'`; `result.verified_behaviors == [behavior]`;
            `result.findings == [confirmed]`; `result.metrics.llm_calls == 2`.
    """
    behavior = VerifiedBehavior(description="Heading visible", evidence_ids=["evidence-obs-0002"])
    potential = BrowserFinding(
        id="heading-check",
        title="Heading may be missing details",
        description="Compare the observed heading with the objective",
        expected_behavior="The heading includes details",
        observed_behavior="Details are absent",
        evidence_ids=["evidence-obs-0002"],
        verification_status="potential",
    )
    confirmed = BrowserFinding.model_validate(
        {
            **potential.model_dump(),
            "verification_status": "confirmed",
            "evidence_ids": ["evidence-obs-0003"],
            "reproduction_steps": ["Open the mock page", "Inspect the heading"],
        }
    )
    model = FakeModel(
        [
            DecisionResponse(
                Decision(
                    action=Action(name="browser_hover", arguments={"ref": "e2"}),
                    verified_behaviors=[behavior],
                    findings=[potential],
                    unresolved_questions=["Does the heading change after refresh?"],
                )
            ),
            DecisionResponse(
                Decision(
                    done=True,
                    verified_behaviors=[behavior],
                    findings=[confirmed],
                    unresolved_questions=["Does the heading change after refresh?"],
                )
            ),
        ]
    )
    result = await AutonomousBrowserExplorer(
        settings(tmp_path), connection=FakeConnection(), model=model
    ).explore(task())
    assert result.status == "completed"
    assert result.verified_behaviors == [behavior]
    assert result.findings == [confirmed]
    assert result.unresolved_questions == ["Does the heading change after refresh?"]
    assert result.metrics.verified_behaviors_count == result.metrics.findings_count == 1
    assert result.metrics.llm_calls == 2


async def test_unseen_screenshot_pixels_cannot_confirm_model_claims(tmp_path: Path) -> None:
    """Verify unseen screenshot pixels cannot confirm model claims.

    Arrange: Use `tmp_path` parameter; prepare `session`, `model`.
    Act: Await `AutonomousBrowserExplorer.explore` to obtain `result`.
    Assert: Check `result.status == 'completed'`; `result.metrics.successful_browser_tool_calls ==
            len(result.evidence) == 3`; `result.verified_behaviors == []`; `all(('cGl4ZWxz' not in
            path.read_text() for path in (tmp_path / result.run_id).iterdir()))`.
    """
    session = FakeSession(
        [snapshot(), snapshot(), [{"type": "image", "data": "cGl4ZWxz", "mimeType": "image/png"}]]
    )
    session.tools["browser_take_screenshot"] = fake_tool("browser_take_screenshot")
    session.capabilities["screenshot"] = "browser_take_screenshot"
    model = FakeModel(
        [
            DecisionResponse(Decision(action=Action(name="browser_take_screenshot"))),
            DecisionResponse(
                Decision(
                    done=True,
                    verified_behaviors=[
                        VerifiedBehavior(
                            description="Unread image claim", evidence_ids=["evidence-obs-0003"]
                        )
                    ],
                    findings=[
                        BrowserFinding(
                            id="visual-claim",
                            title="Unread image finding",
                            description="The model did not receive the screenshot pixels",
                            expected_behavior="Image matches the design",
                            observed_behavior="Unverified visual difference",
                            evidence_ids=["evidence-obs-0003"],
                            reproduction_steps=["Open the page", "Capture a screenshot"],
                            verification_status="confirmed",
                        )
                    ],
                )
            ),
        ]
    )
    result = await AutonomousBrowserExplorer(
        settings(tmp_path), connection=FakeConnection(session), model=model
    ).explore(task())
    assert result.status == "completed"
    assert result.metrics.successful_browser_tool_calls == len(result.evidence) == 3
    assert result.verified_behaviors == []
    assert result.findings[0].verification_status == "inconclusive"
    assert result.findings[0].evidence_ids == []
    assert result.unresolved_questions
    assert result.observed_actions[-1] == "browser_take_screenshot"
    assert all("cGl4ZWxz" not in str(messages) for messages, _ in model.requests)
    assert all("cGl4ZWxz" not in path.read_text() for path in (tmp_path / result.run_id).iterdir())
