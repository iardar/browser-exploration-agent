"""Cheap end-to-end and malformed-artifact regressions for network capture."""

import json
import random
import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from evals.network_capture.grade import grade_run
from langchain_core.messages import AIMessage

from browser_exploration.config import Settings
from browser_exploration.model import Action, Decision, DecisionResponse, OpenAIDecisionModel
from browser_exploration.observations import summarize_network_observation
from tests.support.network_capture import run_network_replay
from tests.unit.browser_exploration.fakes import FakeModel


@pytest.mark.parametrize("seed", range(8))
def test_summary_preserves_counts_and_failure_order_under_request_permutations(seed: int) -> None:
    """Verify summary preserves counts and failure order under request permutations.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for summary preserves counts and failure order under
            request permutations."""
    outcomes = ["200"] * 24 + ["500", "404", "429", "503", "401", "FAILED", None]
    requests = list(enumerate(outcomes))
    random.Random(seed).shuffle(requests)
    lines = [
        f"[GET] https://fixture.example/req/{index}" + (f" => [{code}]" if code else "")
        for index, code in requests
    ]
    summary = summarize_network_observation("\n".join(lines), 6_000)
    assert summary.startswith(
        "Network requests parsed: 31; HTTP errors: 5; failed: 1; unknown outcome: 1."
    )
    expected = [
        index for index, code in requests if code and (code == "FAILED" or int(code) >= 400)
    ][:5]
    shown = [
        int(index)
        for index in re.findall(
            r"^\[(?:[45][0-9]{2}|FAILED)\] GET https://fixture.example/req/(\d+)",
            summary,
            re.MULTILINE,
        )
    ]
    assert shown == expected
    assert "Request details omitted: 26." in summary
    assert len(summary) <= 1_000


def test_unknown_large_response_stays_bounded_and_does_not_claim_zero_failures() -> None:
    """Verify unknown large response stays bounded and does not claim zero failures.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for unknown large response stays bounded and does not claim
            zero failures."""
    text = "Unrecognized format password=private-canary\n" + "unknown payload\n" * 2_000
    summary = summarize_network_observation(text, 6_000)
    assert len(summary) <= 1_000
    assert "Network request counts unknown" in summary
    assert "HTTP errors: 0" not in summary
    assert "private-canary" not in summary
    assert "[truncated]" in summary


async def test_real_adapter_payload_contains_summary_while_raw_listing_stays_in_evidence(
    tmp_path: Path,
) -> None:
    """Verify real adapter payload contains summary while raw listing stays in evidence.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for real adapter payload contains summary while raw listing
            stays in evidence."""
    raw = AIMessage(
        content="", usage_metadata={"input_tokens": 10, "output_tokens": 3, "total_tokens": 13}
    )
    invoke = AsyncMock(
        side_effect=[
            {
                "raw": raw,
                "parsed": Decision(
                    action=Action(name="browser_network_requests", arguments={"static": True})
                ),
                "parsing_error": None,
            },
            {"raw": raw, "parsed": Decision(done=True), "parsing_error": None},
        ]
    )
    client = MagicMock()
    client.return_value.with_structured_output.return_value.ainvoke = invoke
    with patch("browser_exploration.model.ChatOpenAI", client):
        adapter = OpenAIDecisionModel(Settings(model="gpt-4.1-mini"))
    result, directory = await run_network_replay(tmp_path, "NET-003", adapter)
    assert grade_run("NET-003", directory)["passed"] is True
    assert invoke.await_count == 2
    request = invoke.call_args_list[1].args[0]
    payload = "\n".join(str(message.content) for message in request)
    assert "Network requests parsed: 203" in payload
    assert "[500] GET" in payload and "[404] GET" in payload
    assert "evidence-obs-0003" in payload
    assert "healthy-resource-0200.js" not in payload
    assert "healthy-resource-0200.js" in (directory / "evidence/obs-0003.json").read_text()
    assert result.metrics.input_tokens == 20
    assert result.metrics.output_tokens == 6
    assert result.metrics.total_tokens == 26


@pytest.fixture
async def valid_capture(tmp_path: Path) -> Path:
    model = FakeModel(
        [
            DecisionResponse(
                Decision(action=Action(name="browser_network_requests", arguments={"static": True}))
            ),
            DecisionResponse(Decision(done=True)),
        ]
    )
    _, directory = await run_network_replay(tmp_path, "NET-002", model)
    assert grade_run("NET-002", directory)["passed"] is True
    return directory


@pytest.mark.parametrize(
    "fault",
    [
        "negative_calls",
        "negative_duration",
        "invalid_status",
        "invalid_schema",
        "invalid_timestamp",
        "browser_count",
        "success_count",
        "action_ledger",
        "duplicate_observation",
        "duplicate_evidence",
        "model_count",
        "missing_decisions",
        "symlink_escape",
    ],
)
async def test_grader_cannot_pass_impossible_or_inconsistent_run_metadata(
    valid_capture: Path, fault: str, tmp_path: Path
) -> None:
    """Verify grader cannot pass impossible or inconsistent run metadata.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for grader cannot pass impossible or inconsistent run
            metadata."""
    result_path = valid_capture / "result.json"
    trajectory_path = valid_capture / "trajectory.jsonl"
    result = json.loads(result_path.read_text())
    observations = [json.loads(line) for line in trajectory_path.read_text().splitlines()]
    if fault == "negative_calls":
        result["metrics"]["browser_tool_calls"] = -1
    elif fault == "negative_duration":
        result["metrics"]["duration_seconds"] = -1
    elif fault == "invalid_status":
        result["status"] = "passed"
    elif fault == "invalid_schema":
        result["schema_version"] = "2.0"
    elif fault == "invalid_timestamp":
        observations[-1]["timestamp"] = "not-a-timestamp"
    elif fault == "browser_count":
        result["metrics"]["browser_tool_calls"] = 0
    elif fault == "success_count":
        result["metrics"]["successful_browser_tool_calls"] = 0
    elif fault == "action_ledger":
        result["observed_actions"] = ["browser_snapshot"]
    elif fault == "duplicate_observation":
        observations[0]["id"] = observations[-1]["id"]
    elif fault == "duplicate_evidence":
        result["evidence"].append(result["evidence"][-1])
    elif fault == "model_count":
        result["metrics"]["llm_calls"] = 0
    elif fault == "missing_decisions":
        (valid_capture / "decisions.jsonl").unlink()
    elif fault == "symlink_escape":
        outside = tmp_path / "outside.json"
        original = valid_capture / result["evidence"][-1]["path"]
        outside.write_bytes(original.read_bytes())
        link = valid_capture / "evidence/outside.json"
        link.symlink_to(outside)
        result["evidence"][-1]["path"] = "evidence/outside.json"
    result_path.write_text(json.dumps(result))
    trajectory_path.write_text("\n".join(json.dumps(item) for item in observations) + "\n")
    report = grade_run("NET-002", valid_capture)
    assert report["passed"] is False


@pytest.mark.parametrize("invalid", [[], None, {"status": "completed"}])
def test_grader_rejects_malformed_top_level_result(tmp_path: Path, invalid: Any) -> None:
    """Verify grader rejects malformed top level result.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for grader rejects malformed top level result.
    """
    (tmp_path / "result.json").write_text(json.dumps(invalid))
    (tmp_path / "run_config.json").write_text("{}")
    (tmp_path / "trajectory.jsonl").write_text("")
    assert grade_run("NET-001", tmp_path)["verdict"] == "invalid_artifacts"
