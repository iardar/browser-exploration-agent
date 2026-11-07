"""Check coverage snapshot preservation, failure reporting, and weighted comparisons."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from scripts import coverage_report


def sample_report() -> dict[str, Any]:
    return {
        "files": {
            "src/browser_exploration/example.py": {
                "summary": {
                    "covered_lines": 3,
                    "num_statements": 4,
                    "covered_branches": 1,
                    "num_branches": 2,
                    "percent_covered": 100 * 4 / 6,
                },
                "missing_lines": [10],
                "missing_branches": [[9, 10]],
            }
        }
    }


def test_totals_weight_counts_and_handle_files_without_branches() -> None:
    files = sample_report()["files"]
    files["scripts/large.py"] = {
        "summary": {
            "covered_lines": 90,
            "num_statements": 100,
            "covered_branches": 0,
            "num_branches": 0,
        }
    }
    total = coverage_report.coverage_totals(files)
    assert total["lines_percent"] == pytest.approx(100 * 93 / 104)
    assert total["branches_percent"] == 50
    assert coverage_report.coverage_totals({})["branches_percent"] == 100


def test_comparison_preserves_previous_snapshot_and_discloses_changed_scope(tmp_path: Path) -> None:
    before, after = tmp_path / "before", tmp_path / "after"
    before.mkdir()
    after.mkdir()
    old = sample_report()
    old["files"]["scripts/removed.py"] = old["files"]["src/browser_exploration/example.py"]
    path = before / "coverage.json"
    path.write_text(json.dumps(old))
    original = path.read_bytes()
    new = sample_report()
    new["files"]["src/browser_exploration/example.py"]["summary"]["percent_covered"] = 100
    new["files"]["scripts/added.py"] = new["files"]["src/browser_exploration/example.py"]
    coverage_report.write_summary(after, new, before)
    comparison = (after / "comparison.md").read_text()
    assert "66.67% | 100.00% | +33.33" in comparison
    assert "scripts/removed.py | present | absent | scope changed" in comparison
    assert "scripts/added.py | absent | present | scope changed" in comparison
    assert "missing lines [10]" in (after / "summary.md").read_text()
    assert path.read_bytes() == original


@pytest.mark.parametrize("exit_code,has_report", [(0, True), (1, True), (2, False)])
def test_runner_preserves_failures_and_records_only_offline_commands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    exit_code: int,
    has_report: bool,
) -> None:
    monkeypatch.setattr(coverage_report, "REPORTS", tmp_path / "reports")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--live-llm --reportportal")
    monkeypatch.setenv("LANGSMITH_API_KEY", "private-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "private-secret")
    monkeypatch.setenv("RP_API_KEY", "private-secret")
    monkeypatch.setenv("QA_BROWSER_LIVE_MCP", "1")
    monkeypatch.setenv("COVERAGE_PROCESS_START", "/unwanted/config")
    directory = tmp_path / "reports" / "trial"

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert "tests/unit" in command and "tests/live_llm" not in command
        assert "--cov-branch" in command and "--cov-context=test" in command
        assert command[-2:] == ["-p", "tests.sandbox_polling"]
        environment = kwargs["env"]
        assert environment["QA_BROWSER_LIVE_MCP"] == "0"
        assert environment["LANGSMITH_TRACING"] == "false"
        assert (
            not {
                "PYTEST_ADDOPTS",
                "OPENAI_API_KEY",
                "LANGSMITH_API_KEY",
                "RP_API_KEY",
                "COVERAGE_PROCESS_START",
            }
            & environment.keys()
        )
        assert environment["COVERAGE_FILE"] == str(directory / ".coverage")
        if has_report:
            (directory / "coverage.json").write_text(json.dumps(sample_report()))
        kwargs["stdout"].write("retained test diagnostics\n")
        return subprocess.CompletedProcess(command, exit_code)

    monkeypatch.setattr(coverage_report.subprocess, "run", run)
    assert coverage_report.main(["--label", "trial", "--sandbox-polling"]) == exit_code
    metadata = json.loads((directory / "metadata.json").read_text())
    assert metadata["pytest_exit_code"] == exit_code
    assert metadata["source_sha256"]
    assert (directory / "summary.md").exists() == has_report
    assert "retained test diagnostics" in (directory / "pytest.log").read_text()
    assert "private-secret" not in (directory / "metadata.json").read_text()
    original = (directory / "pytest.log").read_bytes()
    with pytest.raises(SystemExit) as caught:
        coverage_report.main(["--label", "trial"])
    assert caught.value.code == 2
    assert (directory / "pytest.log").read_bytes() == original


@pytest.mark.parametrize(
    "arguments", [["--label", "../escape"], ["--workers", "-1"], ["--compare", "missing-snapshot"]]
)
def test_invalid_options_do_not_create_a_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    arguments: list[str],
) -> None:
    reports = tmp_path / "reports"
    monkeypatch.setattr(coverage_report, "REPORTS", reports)
    with pytest.raises(SystemExit) as caught:
        coverage_report.main(arguments)
    assert caught.value.code == 2
    assert not reports.exists()
