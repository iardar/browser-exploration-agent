from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from browser_exploration.contracts import (
    BrowserExplorationResult,
    BrowserExplorationTask,
    BrowserFinding,
    ExecutionMetrics,
    VerifiedBehavior,
)


def test_task_defaults_and_independent_contract() -> None:
    """Verify task defaults and independent contract.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for task defaults and independent contract.
    """
    task = BrowserExplorationTask(url="https://Example.com/page", objective=" Inspect page ")
    assert task.allowed_domains == ["example.com"]
    assert task.objective == "Inspect page"
    assert task.allow_mutations is False
    assert task.max_steps == 30
    assert task.model_validate_json(task.model_dump_json()) == task


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://u:p@example.com",
        "https://",
        "https://x:bad",
    ],
)
def test_task_rejects_invalid_urls(url: str) -> None:
    """Verify task rejects invalid urls.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for task rejects invalid urls.
    """
    with pytest.raises(ValidationError):
        BrowserExplorationTask(url=url, objective="Inspect")


@pytest.mark.parametrize(
    "changes",
    [
        {"max_steps": 0},
        {"max_model_iterations": -1},
        {"max_duration_seconds": 100_000},
        {"max_recent_observations": 0},
        {"max_observation_chars": 0},
        {"max_context_chars": 0},
        {"objective": "  "},
        {"unexpected": True},
        {"allowed_domains": ["*.example.com"]},
        {"allowed_domains": ["https://example.com"]},
    ],
)
def test_task_validates_limits_and_unknown_fields(changes: dict[str, object]) -> None:
    """Verify task validates limits and unknown fields.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for task validates limits and unknown fields.
    """
    with pytest.raises(ValidationError):
        BrowserExplorationTask.model_validate(
            {"url": "https://example.com", "objective": "Inspect", **changes}
        )


def test_confirmed_findings_and_behaviors_require_evidence() -> None:
    """Verify confirmed findings and behaviors require evidence.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for confirmed findings and behaviors require evidence.
    """
    values = {
        "id": "f1",
        "title": "Unexpected navigation",
        "description": "Observed mismatch",
        "expected_behavior": "Stay on page",
        "observed_behavior": "Left page",
        "verification_status": "confirmed",
    }
    with pytest.raises(ValidationError, match="require evidence"):
        BrowserFinding.model_validate(values)
    finding = BrowserFinding.model_validate({**values, "evidence_ids": ["evidence-o1"]})
    assert finding.verification_status == "confirmed"
    potential = BrowserFinding.model_validate({**values, "verification_status": "potential"})
    assert potential.evidence_ids == []
    with pytest.raises(ValidationError):
        VerifiedBehavior(description="Link works", evidence_ids=[])


def test_result_retains_partial_status_and_unknown_metrics() -> None:
    """Verify result retains partial status and unknown metrics.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for result retains partial status and unknown metrics.
    """
    now = datetime.now(UTC)
    result = BrowserExplorationResult(
        run_id="r1",
        objective="Inspect",
        target_url="https://example.com",
        status="timeout",
        started_at=now,
        finished_at=now,
    )
    assert result.status == "timeout"
    assert result.metrics.input_tokens is None
    assert result.metrics == ExecutionMetrics()
    assert BrowserExplorationResult.model_validate_json(result.model_dump_json()) == result
