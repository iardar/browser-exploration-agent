"""Challenge the artifact grader offline; this never runs an agent or opens a socket.

Supply a passing saved run as a control. Each scenario copies it into a new private
directory before changing one property. The original run is never rewritten.
"""

import argparse
import hashlib
import inspect
import json
import re
import shutil
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .dataset import ROOT, load_case, load_requests
from .grade import _text_content, grade_run


def load_challenges() -> list[dict[str, Any]]:
    """Load the reviewed labels, keeping them separate from producer artifacts."""
    cases: list[dict[str, Any]] = json.loads((ROOT / "grader_cases.json").read_text())["cases"]
    identifiers = [item["id"] for item in cases]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Challenge IDs must be unique")
    for case in cases:
        load_case(case["base_case"])
        if not re.fullmatch(r"G-\d{3}", case["id"]) or type(case["expected_pass"]) is not bool:
            raise ValueError("Invalid challenge label")
        if not case["expected_pass"] and not case.get("expected_check"):
            raise ValueError("A negative challenge must name its expected failing check")
    return cases


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    path.chmod(0o600)


def _fingerprint(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        digest.update(str(path.relative_to(directory)).encode() + b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(65_536), b""):
                digest.update(chunk)
    return digest.hexdigest()


def copy_variant(source: Path, destination: Path, scenario: dict[str, Any]) -> Path:
    """Copy a control run and apply the named mutation, preserving source evidence."""
    source, destination = source.resolve(), destination.absolute()
    if destination.resolve().is_relative_to(source) or source.is_relative_to(destination.resolve()):
        raise ValueError("Control and challenge directories must not contain each other")
    if destination.exists() or destination.is_symlink():
        raise ValueError("Challenge output already exists")
    shutil.copytree(source, destination, symlinks=True)
    destination.chmod(0o700)
    result = json.loads((destination / "result.json").read_text())
    config = json.loads((destination / "run_config.json").read_text())
    trajectory = destination / "trajectory.jsonl"
    observations = [json.loads(line) for line in trajectory.read_text().splitlines()]
    network = next(
        item for item in observations if item["action_name"].endswith("browser_network_requests")
    )
    reference = next(item for item in result["evidence"] if item["id"] == network["evidence_ref"])
    raw_path = (destination / reference["path"]).resolve()
    if not raw_path.is_relative_to(destination.resolve()):
        raise ValueError("Control evidence escapes its directory")
    raw = json.loads(raw_path.read_text())
    text = _text_content(raw)
    target = urlsplit(result["target_url"])
    origin = urlunsplit((target.scheme, target.netloc, "", "", ""))
    mutation = scenario["mutation"]

    def set_text(value: str) -> None:
        nonlocal raw
        raw = {"content": [{"type": "text", "text": value}]}

    def increment_counts() -> None:
        network["summary"] = re.sub(
            r"(Network requests parsed: )(\d+)",
            lambda match: match[1] + str(int(match[2]) + 1),
            network["summary"],
        )
        network["summary"] = re.sub(
            r"(Request details omitted: )(\d+)",
            lambda match: match[1] + str(int(match[2]) + 1),
            network["summary"],
        )

    if mutation == "identity":
        pass
    elif mutation == "plain_listing":
        set_text(re.sub(r"^\d+\.\s*", "", text, flags=re.MULTILINE))
    elif mutation == "reverse_listing":
        set_text("\n".join(reversed(text.splitlines())))
    elif mutation in {"favicon", "repeated_request"}:
        line = (
            f"[GET] {origin}/favicon.ico => [204]"
            if mutation == "favicon"
            else next(line for line in text.splitlines() if "[GET]" in line)
        )
        set_text(text + "\n" + line)
        increment_counts()
    elif mutation == "counts_only":
        network["summary"] = network["summary"].splitlines()[0]
    elif mutation == "scope_alias":
        network["action_arguments"] = {"includeStatic": True}
    elif mutation == "wrong_objective":
        config["task"]["objective"] = "Only inspect the page title."
    elif mutation == "static_disabled":
        network["action_arguments"] = {"static": False}
    elif mutation == "scope_missing":
        network["action_arguments"] = {}
    elif mutation == "scope_conflict":
        network["action_arguments"] = {"static": True, "includeStatic": False}
    elif mutation == "static_omissions":
        set_text(text + "\nNote: 5 static requests not shown")
    elif mutation == "summary_foreign_origin":
        network["summary"] = network["summary"].replace(origin, "https://fabricated.example")
    elif mutation == "wrong_evidence_kind":
        reference["kind"] = "screenshot"
    elif mutation == "wrong_evidence_owner":
        result["evidence"][0]["observation_id"] = network["id"]
    elif mutation == "reversed_result_time":
        result["finished_at"] = (
            datetime.fromisoformat(result["started_at"]) - timedelta(seconds=1)
        ).isoformat()
    elif mutation == "reordered_observations":
        observations[0]["timestamp"] = result["finished_at"]
        observations[1]["timestamp"] = result["started_at"]
    elif mutation == "duplicate_evidence_path":
        result["evidence"][0]["path"] = reference["path"]
    elif mutation == "retention_config":
        config["retain_raw_evidence"] = False
    elif mutation == "unknown_claim_evidence":
        result["verified_behaviors"] = [
            {"description": "Capture complete", "evidence_ids": ["missing-evidence"]}
        ]
        result["metrics"]["verified_behaviors_count"] = 1
    elif mutation == "wrong_result_counts":
        result["metrics"]["findings_count"] += 1
    elif mutation == "missing_request":
        path = load_requests(scenario["base_case"])[-1].path
        set_text("\n".join(line for line in text.splitlines() if path not in line))
    elif mutation == "wrong_summary_counts":
        network["summary"] = re.sub(r"HTTP errors: \d+", "HTTP errors: 9999", network["summary"])
    elif mutation == "foreign_raw_origin":
        set_text(text.replace(origin, "https://foreign.example"))
    elif mutation == "not_retained":
        reference["retained"] = False
    elif mutation == "unexpected_request":
        set_text(text + f"\n[GET] {origin}/unexpected.js => [200]")
    elif mutation == "unfinished":
        result["status"] = "model_limit"
    elif mutation == "unknown_format":
        set_text('{"unknown_listing": true}')
    elif mutation == "raw_tool_error":
        raw = {"isError": True, "content": [{"type": "text", "text": text}]}
    elif mutation != "corrupt_json":
        raise ValueError("Unknown challenge mutation")

    _write(destination / "result.json", result)
    _write(destination / "run_config.json", config)
    _write(raw_path, raw)
    if mutation == "corrupt_json":
        raw_path.write_text("not JSON\n")
    trajectory.write_text("\n".join(json.dumps(item) for item in observations) + "\n")
    trajectory.chmod(0o600)
    return destination


def run_benchmark(
    controls: dict[str, Path],
    output: Path,
    *,
    grader: Callable[[str, Path], dict[str, Any]] = grade_run,
) -> dict[str, Any]:
    """Measure valid acceptance and corrupted-artifact rejection; no agent is executed."""
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    _write(
        output / "evaluation_manifest.json",
        {
            "report_type": "network-grader-derived-artifacts/v1",
            "purpose": "Derived grader controls; exclude from agent execution statistics.",
        },
    )
    for case_id, directory in controls.items():
        if not grader(case_id, directory).get("passed"):
            raise ValueError("Every supplied control must pass the selected grader")
    fingerprints = {case_id: _fingerprint(path) for case_id, path in controls.items()}
    try:
        source = inspect.getsourcefile(grader)
        source_hash = hashlib.sha256(Path(source).read_bytes()).hexdigest() if source else None
    except (TypeError, OSError):
        source_hash = None
    rows = []
    for scenario in load_challenges():
        if scenario["base_case"] not in controls:
            continue
        directory = copy_variant(controls[scenario["base_case"]], output / scenario["id"], scenario)
        grade = grader(scenario["base_case"], directory)
        failed = grade.get(
            "failed_checks", [key for key, value in grade.get("checks", {}).items() if not value]
        )
        if grade.get("verdict") == "invalid_artifacts":
            failed = ["invalid_artifacts"]
        label_matches = grade["passed"] == scenario["expected_pass"]
        rows.append(
            {
                "id": scenario["id"],
                "base_case": scenario["base_case"],
                "mutation": scenario["mutation"],
                "expected_pass": scenario["expected_pass"],
                "passed": grade["passed"],
                "grader_version": grade.get("grader_version", "legacy"),
                "failed_checks": failed,
                "label_matches": label_matches,
                "expected_check_detected": scenario["expected_pass"]
                or scenario["expected_check"] in failed,
            }
        )
    positives = [row for row in rows if row["expected_pass"]]
    negatives = [row for row in rows if not row["expected_pass"]]
    accepted = sum(row["passed"] for row in positives)
    rejected = sum(not row["passed"] for row in negatives)
    unchanged = all(
        _fingerprint(path) == fingerprints[case_id] for case_id, path in controls.items()
    )
    report = {
        "report_type": "network-capture-grader-benchmark/v1",
        "dataset_id": "network-grader-challenges-v1",
        "corpus_sha256": hashlib.sha256((ROOT / "grader_cases.json").read_bytes()).hexdigest(),
        "grader_source_sha256": source_hash,
        "control_fingerprints": fingerprints,
        "controls_unchanged": unchanged,
        "scenario_count": len(rows),
        "positive_controls": len(positives),
        "valid_accepted": accepted,
        "negative_challenges": len(negatives),
        "corruptions_rejected": rejected,
        "valid_acceptance_rate": accepted / len(positives) if positives else None,
        "corruption_rejection_recall": rejected / len(negatives) if negatives else None,
        "false_acceptance_rate": (len(negatives) - rejected) / len(negatives)
        if negatives
        else None,
        "all_expectations_met": unchanged
        and bool(rows)
        and all(row["label_matches"] and row["expected_check_detected"] for row in rows),
        "scenarios": rows,
        "interpretation": (
            "Measures this reviewed grader challenge corpus, not agent or live-browser reliability."
        ),
    }
    _write(output / "benchmark.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--control", action="append", required=True, help="CASE_ID=path to a passing saved run"
    )
    parser.add_argument(
        "--output-dir", type=Path, required=True, help="New private directory; never overwritten"
    )
    args = parser.parse_args()
    controls = {}
    for item in args.control:
        case_id, separator, path = item.partition("=")
        if not separator:
            parser.error("--control requires CASE_ID=path")
        load_case(case_id)
        if case_id in controls:
            parser.error("Duplicate control case")
        controls[case_id] = Path(path)
    report = run_benchmark(controls, args.output_dir)
    print(json.dumps({key: value for key, value in report.items() if key != "scenarios"}, indent=2))
    raise SystemExit(0 if report["all_expectations_met"] else 1)


if __name__ == "__main__":
    main()
