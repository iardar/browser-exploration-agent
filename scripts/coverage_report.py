"""Save a reproducible offline unit coverage snapshot and optionally compare an earlier one."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "coverage-reports"
SOURCES = ("src/browser_exploration", "evals", "scripts")


def coverage_totals(files: dict[str, Any]) -> dict[str, int | float]:
    """Aggregate lines and branches using counts, rather than averaging percentages."""
    counts = {
        key: sum(item["summary"][key] for item in files.values())
        for key in ("covered_lines", "num_statements", "covered_branches", "num_branches")
    }
    for label, covered, total in (
        ("lines", "covered_lines", "num_statements"),
        ("branches", "covered_branches", "num_branches"),
    ):
        counts[f"{label}_percent"] = 100 * counts[covered] / counts[total] if counts[total] else 100
    return counts


def write_summary(directory: Path, data: dict[str, Any], previous: Path | None) -> None:
    """Write scoped totals, exact remaining gaps, and comparable module changes."""
    lines = [
        "# Unit coverage snapshot",
        "",
        "Scope: offline tests/unit; branch coverage enabled.",
        "",
    ]
    lines += ["| Scope | Lines | Branches |", "| --- | ---: | ---: |"]
    for scope in (*SOURCES, "all"):
        files = {
            name: item
            for name, item in data["files"].items()
            if scope == "all" or name.startswith(scope + "/")
        }
        totals = coverage_totals(files)
        lines.append(
            f"| {scope} | {totals['covered_lines']}/{totals['num_statements']} "
            f"({totals['lines_percent']:.2f}%) | "
            f"{totals['covered_branches']}/{totals['num_branches']} "
            f"({totals['branches_percent']:.2f}%) |"
        )
    lines += ["", "## Remaining gaps", ""]
    for name, item in data["files"].items():
        if item["missing_lines"] or item["missing_branches"]:
            lines.append(
                f"- `{name}`: missing lines {item['missing_lines']}; "
                f"missing branch arcs {item['missing_branches']}."
            )
    (directory / "summary.md").write_text("\n".join(lines) + "\n")
    if previous is None:
        return
    before = json.loads((previous / "coverage.json").read_text())
    comparison = [
        "# Coverage comparison",
        "",
        f"Before: `{previous}`. After: `{directory}`.",
        "",
        "Percentages below count lines and branch destinations together.",
        "",
        "| File | Before | After | Change (pp) |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name in sorted(before["files"].keys() | data["files"].keys()):
        old = before["files"].get(name)
        new = data["files"].get(name)
        if old is None or new is None:
            comparison.append(
                f"| {name} | {'present' if old else 'absent'} | "
                f"{'present' if new else 'absent'} | scope changed |"
            )
            continue
        old_percent = old["summary"]["percent_covered"]
        new_percent = new["summary"]["percent_covered"]
        comparison.append(
            f"| {name} | {old_percent:.2f}% | {new_percent:.2f}% | "
            f"{new_percent - old_percent:+.2f} |"
        )
    comparison += [
        "",
        "Line/branch denominators may change when source code changes; see each snapshot's "
        "coverage.json and metadata.json. Coverage measures execution, not assertion quality.",
    ]
    (directory / "comparison.md").write_text("\n".join(comparison) + "\n")


def main(argv: list[str] | None = None) -> int:
    """Run only offline unit tests, keeping every snapshot and returning pytest's exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default=datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ"))
    parser.add_argument("--workers", type=int, default=0, help="xdist workers; 0 runs serially")
    parser.add_argument("--compare", type=Path, help="Earlier snapshot directory")
    parser.add_argument(
        "--sandbox-polling", action="store_true", help="Use test selector workaround"
    )
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", args.label) or args.workers < 0:
        parser.error("Use a simple label and a nonnegative worker count.")
    if args.compare and not (args.compare / "coverage.json").is_file():
        parser.error("--compare must point to a snapshot containing coverage.json")
    directory = REPORTS / args.label
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error(f"Snapshot already exists: {directory}. Choose another --label.")
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-c",
        "pyproject.toml",
        "tests/unit",
        "-n",
        str(args.workers),
        "--cov-branch",
        "--cov-context=test",
        *(f"--cov={source}" for source in SOURCES),
        "--cov-report=term-missing",
        f"--cov-report=html:{directory / 'html'}",
        f"--cov-report=json:{directory / 'coverage.json'}",
        f"--cov-report=xml:{directory / 'coverage.xml'}",
        f"--junitxml={directory / 'junit.xml'}",
    ]
    if args.sandbox_polling:
        command += ["-p", "tests.sandbox_polling"]
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("BROWSER_", "OPENAI_", "LANGSMITH_", "LANGCHAIN_", "RP_"))
        and key not in {"PYTEST_ADDOPTS", "COVERAGE_PROCESS_START"}
    }
    environment.update(
        QA_BROWSER_LIVE_MCP="0",
        LANGSMITH_TRACING="false",
        LANGCHAIN_TRACING_V2="false",
        COVERAGE_FILE=str(directory / ".coverage"),
        PYTHONPATH=f"{ROOT}{os.pathsep}{ROOT / 'src'}",
    )
    metadata: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "command": command,
        "python": sys.version,
        "pytest": version("pytest"),
        "coverage": version("coverage"),
        "source_sha256": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for scope in (*SOURCES, "tests/unit")
            for path in sorted((ROOT / scope).rglob("*.py"))
        },
    }
    print(f"Running offline coverage; log: {directory / 'pytest.log'}", flush=True)
    with (directory / "pytest.log").open("w") as log:
        result = subprocess.run(
            command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT, check=False
        )
    metadata.update(finished_at=datetime.now(UTC).isoformat(), pytest_exit_code=result.returncode)
    (directory / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    report = directory / "coverage.json"
    if report.is_file():
        data = json.loads(report.read_text())
        write_summary(directory, data, args.compare)
        print(json.dumps(coverage_totals(data["files"]), indent=2))
    print(f"pytest exit code: {result.returncode}; snapshot: {directory}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
