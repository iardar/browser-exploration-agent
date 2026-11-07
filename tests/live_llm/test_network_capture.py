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
    """Require real capture/completion decisions, then grade the saved artifacts."""
    result, directory = await run_network_replay(
        live_artifact_dir,
        case_id,
        live_decision_model,
        configured_model=live_decision_model.budget.model,
    )
    report = grade_run(case_id, directory)
    assert report["passed"], json.dumps(report, indent=2)
    assert result.metrics.llm_calls <= 2
