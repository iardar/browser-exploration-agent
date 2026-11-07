"""Real model, real graph, synthetic MCP: small opt-in network-capture component checks."""

import json
from pathlib import Path

import pytest
from evals.network_capture.grade import grade_run
from tests.support.live_llm import BudgetedDecisionModel
from tests.support.network_capture import run_network_replay

pytestmark = pytest.mark.live_llm


@pytest.mark.parametrize("case_id", ["NET-001", "NET-002", "NET-003"])
async def test_real_model_captures_network_evidence_and_finishes(
    case_id: str, live_decision_model: BudgetedDecisionModel, live_artifact_dir: Path
) -> None:
    """Check that a real model captures each network case and stops after the capture.

    Arrange: Parametrize NET-001 (healthy), NET-002 (HTTP 500), and NET-003 (large listing
        with late failures). Use the budgeted real-model fixture and a synthetic MCP session
        that serves each case's fixed request listing; retain raw evidence in a private run dir.
    Act: Run the production exploration graph. It navigates and snapshots the fixture page,
        then asks the model to choose a static-inclusive network capture and completion.
        Grade the saved result, trajectory, configuration, and evidence against the case oracle.
    Assert: Require the offline grader to pass its completion, capture, evidence, summary,
        and budget checks, and require no more than two model calls for this case.
    """
    result, directory = await run_network_replay(
        live_artifact_dir,
        case_id,
        live_decision_model,
        configured_model=live_decision_model.budget.model,
    )
    report = grade_run(case_id, directory)
    assert report["passed"], json.dumps(report, indent=2)
    assert result.metrics.llm_calls <= 2
