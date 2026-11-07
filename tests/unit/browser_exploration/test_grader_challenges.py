"""Measure false acceptance with curated valid and corrupted artifact variants."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from evals.network_capture.challenges import copy_variant, load_challenges, main, run_benchmark
from evals.network_capture.grade import grade_run
from evals.run_quality import audit_runs

from browser_exploration.model import Action, Decision, DecisionResponse, ModelError
from tests.support.network_capture import run_network_replay
from tests.unit.browser_exploration.fakes import FakeModel

SCENARIOS = load_challenges()


async def control(output: Path, case_id: str) -> Path:
    model = FakeModel(
        [
            DecisionResponse(
                Decision(action=Action(name="browser_network_requests", arguments={"static": True}))
            ),
            DecisionResponse(Decision(done=True)),
        ]
    )
    _, directory = await run_network_replay(output, case_id, model)
    assert grade_run(case_id, directory)["passed"] is True
    return directory


def fingerprints(directory: Path) -> dict[str, str]:
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in directory.rglob("*")
        if path.is_file()
    }


def test_corpus_contains_reviewed_positive_and_negative_labels() -> None:
    assert len(SCENARIOS) == 30
    assert sum(item["expected_pass"] for item in SCENARIOS) == 7
    assert len({item["id"] for item in SCENARIOS}) == len(SCENARIOS)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[item["id"] for item in SCENARIOS])
async def test_challenge_outcome_and_named_check_are_correct(
    tmp_path: Path, scenario: dict[str, Any]
) -> None:
    source = await control(tmp_path / "control", scenario["base_case"])
    before = fingerprints(source)
    variant = copy_variant(source, tmp_path / scenario["id"], scenario)
    report = grade_run(scenario["base_case"], variant)
    assert report["passed"] is scenario["expected_pass"], report
    if not scenario["expected_pass"]:
        assert scenario["expected_check"] in report["failed_checks"], report
    assert fingerprints(source) == before


async def test_benchmark_measures_rejection_without_rejecting_valid_controls(
    tmp_path: Path,
) -> None:
    controls = {
        case_id: await control(tmp_path / "controls", case_id)
        for case_id in ("NET-001", "NET-002", "NET-003")
    }
    output = tmp_path / "benchmark"
    report = run_benchmark(controls, output)
    assert report["scenario_count"] == 30
    assert report["valid_accepted"] == report["positive_controls"] == 7
    assert report["corruptions_rejected"] == report["negative_challenges"] == 23
    assert report["false_acceptance_rate"] == 0
    assert report["all_expectations_met"] is True
    assert report["controls_unchanged"] is True
    assert set(report["control_fingerprints"]) == set(controls)
    assert json.loads((output / "benchmark.json").read_text()) == report
    assert (output / "benchmark.json").stat().st_mode & 0o777 == 0o600
    assert output.stat().st_mode & 0o777 == 0o700


async def test_existing_control_and_output_cannot_be_overwritten(tmp_path: Path) -> None:
    source = await control(tmp_path / "control", "NET-003")
    before = fingerprints(source)
    with pytest.raises(ValueError, match="contain each other"):
        copy_variant(source, source / "recursive-copy", SCENARIOS[0])
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "sentinel").write_text("keep")
    with pytest.raises(ValueError, match="already exists"):
        copy_variant(source, existing, SCENARIOS[0])
    assert (existing / "sentinel").read_text() == "keep"
    assert fingerprints(source) == before


def test_invalid_control_cannot_generate_a_passing_benchmark(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="control must pass"):
        run_benchmark({"NET-001": tmp_path / "missing"}, tmp_path / "output")


async def test_benchmark_cli_is_offline_and_emits_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = await control(tmp_path / "control", "NET-001")
    monkeypatch.setattr(
        "sys.argv",
        [
            "challenges",
            "--control",
            f"NET-001={source}",
            "--output-dir",
            str(tmp_path / "benchmark"),
        ],
    )
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["scenario_count"] == 2
    assert report["negative_challenges"] == 0
    assert report["corruption_rejection_recall"] is None


async def test_provider_unavailability_is_visible_and_separate_from_behavior(
    tmp_path: Path,
) -> None:
    model = FakeModel([ModelError("llm_api_error", "Provider unavailable")])
    _, directory = await run_network_replay(tmp_path, "NET-001", model)
    report = grade_run("NET-001", directory)
    assert report["passed"] is False and report["verdict"] == "fail"
    assert report["failure_category"] == "infrastructure"
    assert "llm_api_error" in report["run_error_codes"]
    assert "capture_recall" not in report


async def test_summary_can_omit_redacted_query_values(tmp_path: Path) -> None:
    source = await control(tmp_path, "NET-001")
    result = json.loads((source / "result.json").read_text())
    raw_path = source / result["evidence"][-1]["path"]
    raw = json.loads(raw_path.read_text())
    raw["content"][0]["text"] = raw["content"][0]["text"].replace(" =>", "?token=[redacted] =>")
    raw_path.write_text(json.dumps(raw))
    assert grade_run("NET-001", source)["passed"] is True


async def test_challenge_copies_do_not_pollute_execution_quality_statistics(tmp_path: Path) -> None:
    source = await control(tmp_path / "original", "NET-003")
    report = run_benchmark({"NET-003": source}, tmp_path / "derived")
    audit = audit_runs(tmp_path)
    assert audit["run_count"] == 1
    assert audit["excluded_evaluation_results"] == report["scenario_count"]
    assert str(tmp_path / "derived") in audit["excluded_evaluation_directories"]
    assert not audit["incomplete_runs"]


async def test_unknown_manifest_does_not_hide_original_execution(tmp_path: Path) -> None:
    source = await control(tmp_path / "original", "NET-001")
    (source / "evaluation_manifest.json").write_text('{"report_type": "unknown"}')
    audit = audit_runs(tmp_path)
    assert audit["run_count"] == 1
    assert audit["excluded_evaluation_results"] == 0
