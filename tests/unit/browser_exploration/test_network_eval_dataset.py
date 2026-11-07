"""Verify the network dataset, fixture, and grader without model or network access."""

import json
import re
from pathlib import Path
from typing import Any

import pytest
from evals.network_capture.dataset import load_case, load_dataset, load_requests
from evals.network_capture.fixture import fixture_response
from evals.network_capture.grade import grade_run, main

from browser_exploration.model import Action, Decision, DecisionResponse
from browser_exploration.observations import summarize_network_observation
from tests.support.network_capture import (
    BASE_URL,
    network_listing,
    run_network_replay,
)
from tests.unit.browser_exploration.fakes import FakeModel

BASE = BASE_URL


def listing(case_id: str) -> str:
    """Render a synthetic MCP response directly from the independently declared oracle."""
    return network_listing(case_id)


async def record_run(directory: Path, case_id: str) -> Path:
    """Exercise the real graph and evidence store with a deterministic capture decision."""
    model = FakeModel(
        [
            DecisionResponse(
                Decision(action=Action(name="browser_network_requests", arguments={"static": True}))
            ),
            DecisionResponse(Decision(done=True)),
        ]
    )
    result, run_dir = await run_network_replay(directory, case_id, model)
    assert result.status == "completed"
    return run_dir


@pytest.mark.parametrize("case_id, count", [("NET-001", 4), ("NET-002", 4), ("NET-003", 203)])
def test_fixture_discovers_all_declared_requests_in_order(case_id: str, count: int) -> None:
    """Verify fixture discovers all declared requests in order.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for fixture discovers all declared requests in order.
    """
    expected = load_requests(case_id)
    assert len(expected) == count
    assert len({item.path for item in expected}) == count
    status, mime, body = fixture_response(load_case(case_id)["path"])
    assert status == 200 and mime.startswith("text/html")
    page = body.decode()
    assert "Network fixture ready" in page
    queue = re.findall(r'<script src="([^"]+)"', page)
    discovered = [load_case(case_id)["path"]]
    while queue:
        path = queue.pop(0)
        discovered.append(path)
        status, mime, body = fixture_response(path)
        assert mime.startswith("text/javascript")
        assert status == next(item.status for item in expected if item.path == path)
        script = body.decode()
        if status == 200 and script.startswith("document.write("):
            tags = json.loads(script[len("document.write(") : -3])
            queue = re.findall(r'<script src="([^"]+)"', tags) + queue
    assert discovered == [item.path for item in expected]


def test_fixture_handles_favicon_and_unknown_routes_without_external_assets() -> None:
    """Verify fixture handles favicon and unknown routes without external assets.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for fixture handles favicon and unknown routes without
            external assets."""
    assert fixture_response("/favicon.ico") == (204, "image/x-icon", b"")
    assert fixture_response("/unknown")[0] == 404
    assert [case["category"] for case in load_dataset()["cases"]] == [
        "positive",
        "positive",
        "edge",
    ]


def test_edge_oracle_places_both_failures_beyond_the_old_observation_prefix() -> None:
    """Verify edge oracle places both failures beyond the old observation prefix.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for edge oracle places both failures beyond the old
            observation prefix."""
    text = listing("NET-003")
    assert text.index("=> [500]") > 6_000
    assert text.index("=> [404]") > 6_000
    summary = summarize_network_observation(text, 6_000)
    assert "[500] GET" in summary and "[404] GET" in summary
    assert len(summary) <= 1_000


@pytest.mark.parametrize("case_id", ["NET-001", "NET-002", "NET-003"])
async def test_grader_passes_complete_real_graph_artifacts(tmp_path: Path, case_id: str) -> None:
    """Verify grader passes complete real graph artifacts.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for grader passes complete real graph artifacts.
    """
    directory = await record_run(tmp_path, case_id)
    report = grade_run(case_id, directory)
    assert report["passed"] is True, report
    assert report["capture_recall"] == 1
    assert report["summary_chars"] <= 1_000
    assert report["failure_summary_recall"] == (None if case_id == "NET-001" else 1)


@pytest.fixture
async def saved_run(tmp_path: Path) -> Path:
    return await record_run(tmp_path, "NET-003")


def rewrite_json(path: Path, value: Any) -> None:
    """Edit an evaluation artifact to test a specific false-positive grading risk."""
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.mark.parametrize(
    "fault",
    [
        "missing_network_call",
        "failed_network_call",
        "missing_request",
        "hidden_failure",
        "wrong_counts",
        "oversized_summary",
        "not_retained",
        "broken_link",
        "corrupt_json",
        "escape_path",
        "absolute_path",
        "foreign_origin",
        "wrong_budget",
        "unfinished_run",
        "unknown_format",
        "raw_tool_error",
        "navigated_away",
    ],
)
async def test_grader_rejects_incomplete_or_misleading_artifacts(
    saved_run: Path, fault: str
) -> None:
    """Verify grader rejects incomplete or misleading artifacts.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for grader rejects incomplete or misleading artifacts.
    """
    result_path = saved_run / "result.json"
    config_path = saved_run / "run_config.json"
    trajectory_path = saved_run / "trajectory.jsonl"
    result = json.loads(result_path.read_text())
    config = json.loads(config_path.read_text())
    observations = [json.loads(line) for line in trajectory_path.read_text().splitlines()]
    observation = observations[-1]
    reference = result["evidence"][-1]
    raw_path = saved_run / reference["path"]
    raw = json.loads(raw_path.read_text())
    if fault == "missing_network_call":
        observations.pop()
    elif fault == "failed_network_call":
        observation["error"] = True
    elif fault == "missing_request":
        raw["content"][0]["text"] = "\n".join(
            line for line in raw["content"][0]["text"].splitlines() if "report.js" not in line
        )
    elif fault == "hidden_failure":
        observation["summary"] = "\n".join(
            line for line in observation["summary"].splitlines() if not line.startswith("[500]")
        )
    elif fault == "wrong_counts":
        observation["summary"] = observation["summary"].replace("HTTP errors: 2", "HTTP errors: 0")
    elif fault == "oversized_summary":
        observation["summary"] += "x" * 1_001
    elif fault == "not_retained":
        reference["retained"] = False
    elif fault == "broken_link":
        reference["observation_id"] = "obs-does-not-exist"
    elif fault == "corrupt_json":
        raw_path.write_text("not JSON", encoding="utf-8")
    elif fault == "escape_path":
        reference["path"] = "../outside.json"
    elif fault == "absolute_path":
        reference["path"] = str(raw_path.resolve())
    elif fault == "foreign_origin":
        raw["content"][0]["text"] = raw["content"][0]["text"].replace(
            BASE, "https://foreign.example"
        )
    elif fault == "wrong_budget":
        config["task"]["max_steps"] = 30
    elif fault == "unfinished_run":
        result["status"] = "model_limit"
    elif fault == "unknown_format":
        raw["content"][0]["text"] = '{"unknown_listing": true}'
    elif fault == "raw_tool_error":
        raw["isError"] = True
    elif fault == "navigated_away":
        observations[0]["action_arguments"]["url"] = BASE + "/elsewhere"
    if fault != "corrupt_json":
        rewrite_json(raw_path, raw)
    rewrite_json(result_path, result)
    rewrite_json(config_path, config)
    trajectory_path.write_text("\n".join(json.dumps(item) for item in observations) + "\n")
    assert grade_run("NET-003", saved_run)["passed"] is False


async def test_optional_favicon_does_not_change_oracle_coverage(saved_run: Path) -> None:
    """Verify optional favicon does not change oracle coverage.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for optional favicon does not change oracle coverage.
    """
    result = json.loads((saved_run / "result.json").read_text())
    raw_path = saved_run / result["evidence"][-1]["path"]
    raw = json.loads(raw_path.read_text())
    raw["content"][0]["text"] += f"\n[GET] {BASE}/favicon.ico => [204]"
    rewrite_json(raw_path, raw)
    observations = [
        json.loads(line) for line in (saved_run / "trajectory.jsonl").read_text().splitlines()
    ]
    observations[-1]["summary"] = summarize_network_observation(raw["content"][0]["text"], 6_000)
    (saved_run / "trajectory.jsonl").write_text(
        "\n".join(json.dumps(item) for item in observations) + "\n"
    )
    report = grade_run("NET-003", saved_run)
    assert report["passed"] is True, report
    assert report["expected_requests"] == report["matched_requests"] == 203


async def test_grade_cli_emits_json_and_a_success_exit_code(
    saved_run: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify grade cli emits json and a success exit code.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for grade cli emits json and a success exit code.
    """
    monkeypatch.setattr("sys.argv", ["grade", "--case-id", "NET-003", "--run-dir", str(saved_run)])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "pass"


def test_missing_artifacts_are_not_a_passing_capture(tmp_path: Path) -> None:
    """Verify missing artifacts are not a passing capture.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for missing artifacts are not a passing capture.
    """
    assert grade_run("NET-001", tmp_path)["verdict"] == "invalid_artifacts"
