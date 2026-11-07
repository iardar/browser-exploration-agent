"""Real-model checks for named LLM-evaluation dimensions.

These tests use a real provider decision model with a synthetic MCP session. The
offline grader supplies deterministic reference-based metrics for each saved run.
"""

import json
from pathlib import Path

import pytest
from evals.network_capture.grade import grade_run
from tests.support.live_llm import BudgetedDecisionModel
from tests.support.network_capture import run_network_replay

pytestmark = pytest.mark.live_llm


async def test_llm_tool_selection_precision_and_capture_recall(
    live_decision_model: BudgetedDecisionModel, live_artifact_dir: Path
) -> None:
    """Score tool choice, static-resource scope, oracle recall, and request precision."""
    result, directory = await run_network_replay(
        live_artifact_dir,
        "NET-001",
        live_decision_model,
        configured_model=live_decision_model.budget.model,
    )
    report = grade_run("NET-001", directory)

    assert result.status == "completed", json.dumps(report, indent=2)
    assert report["passed"], json.dumps(report, indent=2)
    assert report["checks"]["one_successful_network_capture"], json.dumps(report, indent=2)
    assert report["checks"]["static_resources_requested"], json.dumps(report, indent=2)
    assert report["checks"]["stayed_on_target"], json.dumps(report, indent=2)
    assert report["checks"]["all_expected_requests_retained"], json.dumps(report, indent=2)
    assert report["checks"]["no_unexpected_requests"], json.dumps(report, indent=2)
    assert report["expected_requests"] == report["matched_requests"] == 4
    assert report["capture_recall"] == 1.0, json.dumps(report, indent=2)
    assert 1 <= result.metrics.llm_calls <= 2


async def test_llm_evidence_grounding_and_known_failure_recall(
    live_decision_model: BudgetedDecisionModel, live_artifact_dir: Path
) -> None:
    """Score evidence traceability and whether the seeded HTTP 500 is preserved."""
    result, directory = await run_network_replay(
        live_artifact_dir,
        "NET-002",
        live_decision_model,
        configured_model=live_decision_model.budget.model,
    )
    report = grade_run("NET-002", directory)

    assert result.status == "completed", json.dumps(report, indent=2)
    assert report["passed"], json.dumps(report, indent=2)
    assert report["checks"]["linked_retained_evidence"], json.dumps(report, indent=2)
    assert report["checks"]["known_claim_evidence"], json.dumps(report, indent=2)
    assert report["checks"]["all_expected_requests_retained"], json.dumps(report, indent=2)
    assert report["checks"]["expected_failures_in_summary"], json.dumps(report, indent=2)
    assert report["expected_failures"] == 1
    assert report["failure_summary_recall"] == 1.0, json.dumps(report, indent=2)

    saved_result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    network_observation = next(
        item
        for item in (
            json.loads(line)
            for line in (directory / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()
        )
        if item["action_name"] == "browser_network_requests"
    )
    claims = [*saved_result["findings"], *saved_result["verified_behaviors"]]
    grounded_error_claims = [
        claim
        for claim in claims
        if network_observation["evidence_ref"] in claim["evidence_ids"]
        and "500" in json.dumps(claim)
        and "/resources/NET-002/report.js" in json.dumps(claim)
    ]
    assert grounded_error_claims, json.dumps(claims, indent=2)
    assert 1 <= result.metrics.llm_calls <= 2


async def test_llm_long_context_failure_recall_and_summary_budget(
    live_decision_model: BudgetedDecisionModel, live_artifact_dir: Path
) -> None:
    """Score recall of late failures in the 203-request edge case and summary compactness."""
    result, directory = await run_network_replay(
        live_artifact_dir,
        "NET-003",
        live_decision_model,
        configured_model=live_decision_model.budget.model,
    )
    report = grade_run("NET-003", directory)

    assert result.status == "completed", json.dumps(report, indent=2)
    assert report["passed"], json.dumps(report, indent=2)
    assert report["checks"]["all_expected_requests_retained"], json.dumps(report, indent=2)
    assert report["checks"]["expected_failures_in_summary"], json.dumps(report, indent=2)
    assert report["expected_requests"] == report["matched_requests"] == 203
    assert report["expected_failures"] == 2
    assert report["failure_summary_recall"] == 1.0, json.dumps(report, indent=2)
    assert report["checks"]["compact_summary"], json.dumps(report, indent=2)
    assert report["summary_chars"] <= report["summary_limit"] == 1_000
    assert 1 <= result.metrics.llm_calls <= 2
