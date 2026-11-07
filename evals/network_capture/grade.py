"""Grade saved network artifacts offline against fixed fixture requests, without an LLM."""

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from browser_exploration.contracts import (
    BrowserExplorationResult,
    BrowserExplorationTask,
    BrowserObservation,
)

from .dataset import load_case, load_dataset, load_requests

_TOOLS = {"browser_network_requests", "playwright_browser_network_requests"}
GRADER_VERSION = "2"
_STATIC_KEYS = ("static", "includeStatic", "include_static")
_REQUEST = re.compile(r"^(?:\d+\.\s*)?\[([A-Z]+)\]\s+(\S+)(?:\s+=>\s*(.*))?$")
_OUTCOME = re.compile(r"^\[([1-5][0-9]{2}|FAILED)\](?:\s|$)")
_SUMMARY_REQUEST = re.compile(r"^\[([1-5][0-9]{2}|FAILED|UNKNOWN)\]\s+([A-Z]+)\s+(\S+)")
_COUNTS = re.compile(
    r"^Network requests parsed: (\d+); HTTP errors: (\d+); "
    r"failed: (\d+); unknown outcome: (\d+)\.$"
)


@dataclass(frozen=True)
class CapturedRequest:
    """A request parsed from saved evidence, independently of the production summarizer."""

    method: str
    url: str
    outcome: str


def _text_content(value: Any) -> str:
    """Read text blocks from MCP or adapter JSON without reading binary evidence."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_text_content(item) for item in value)
    if isinstance(value, dict):
        if value.get("type") in {"image", "image_reference", "image_url", "file", "audio"}:
            return ""
        if "text" in value:
            return str(value["text"])
        if "content" in value:
            return _text_content(value["content"])
    return ""


def _parse_requests(text: str) -> tuple[list[CapturedRequest], int]:
    """Parse known listing lines and count unsupported lines instead of ignoring them."""
    records: list[CapturedRequest] = []
    unknown_lines = 0
    for line in text.splitlines():
        line = line.strip()
        if (
            not line
            or line.startswith("### ")
            or re.match(r"Note: \d+ static requests not shown", line)
        ):
            continue
        match = _REQUEST.fullmatch(line)
        if match is None:
            unknown_lines += 1
            continue
        outcome = _OUTCOME.match((match[3] or "").strip())
        records.append(CapturedRequest(match[1], match[2], outcome[1] if outcome else "UNKNOWN"))
    return records, unknown_lines


def _artifact_path(directory: Path, relative: str) -> Path:
    """Resolve evidence only inside this run, including when a path contains a symlink."""
    path = Path(relative)
    if path.is_absolute():
        raise ValueError("Evidence path must be relative")
    resolved = (directory / path).resolve()
    if not resolved.is_relative_to(directory.resolve()):
        raise ValueError("Evidence path escapes the run directory")
    return resolved


def _grade(case_id: str, directory: Path) -> dict[str, Any]:
    """Assess objective checks independently of the model's completion or finding wording."""
    dataset = load_dataset()
    case = load_case(case_id)
    expected = load_requests(case_id)
    result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    config = json.loads((directory / "run_config.json").read_text(encoding="utf-8"))
    observations = [
        json.loads(line)
        for line in (directory / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    # The public contracts reject malformed artifacts before scoring their claims.
    validated_result = BrowserExplorationResult.model_validate(result)
    validated_task = BrowserExplorationTask.model_validate(config["task"])
    validated_observations = [BrowserObservation.model_validate(item) for item in observations]
    decisions_path = directory / "decisions.jsonl"
    decisions = (
        [json.loads(line) for line in decisions_path.read_text(encoding="utf-8").splitlines()]
        if decisions_path.exists()
        else []
    )
    checks: dict[str, bool] = {}
    task = config["task"]
    target = urlsplit(result["target_url"])
    checks["correct_case"] = target.path == case["path"] and task["url"] == result["target_url"]
    checks["correct_objective"] = (
        result["objective"] == validated_task.objective == dataset["objective"]
    )
    checks["fixed_budgets"] = all(
        task.get(key) == value for key, value in dataset["task_defaults"].items()
    )
    checks["readonly_permissions"] = not any(
        task.get(key) for key in ("allow_mutations", "allow_form_input", "allow_form_submission")
    )
    checks["stayed_on_target"] = all(
        item["url"] == result["target_url"]
        and (
            item["action_name"] not in {"browser_navigate", "playwright_browser_navigate"}
            or item["action_arguments"].get("url") == result["target_url"]
        )
        for item in observations
    )
    checks["finished"] = result["status"] == "completed"
    checks["retention_enabled"] = config.get("retain_raw_evidence") is True
    start, end = validated_result.started_at, validated_result.finished_at
    times = [item.timestamp for item in validated_observations]
    checks["ordered_timestamps"] = (
        all(item.tzinfo is not None for item in [start, end, *times])
        and start <= end
        and all(start <= item <= end for item in times)
        and times == sorted(times)
    )
    metrics = result["metrics"]
    checks["consistent_browser_counts"] = (
        metrics["browser_tool_calls"] == len(observations)
        and metrics["successful_browser_tool_calls"]
        == sum(not item["error"] for item in observations)
        and metrics["failed_browser_tool_calls"] == sum(item["error"] for item in observations)
        and result["observed_actions"] == [item["action_name"] for item in observations]
    )
    checks["consistent_model_count"] = metrics["llm_calls"] == sum(
        item.get("event") in {"model_decision", "model_error"} for item in decisions
    )
    observation_ids = [item["id"] for item in observations]
    evidence_ids = [item["id"] for item in result["evidence"]]
    checks["consistent_evidence_ids"] = (
        len(observation_ids) == len(set(observation_ids))
        and len(evidence_ids) == len(set(evidence_ids))
        and set(evidence_ids) == {item.get("evidence_ref") for item in observations}
    )
    owners = {item["id"]: item for item in observations}
    checks["consistent_evidence_owners"] = all(
        reference["observation_id"] in owners
        and owners[reference["observation_id"]]["evidence_ref"] == reference["id"]
        for reference in result["evidence"]
    )
    checks["correct_evidence_kind"] = all(
        reference["kind"] == "browser_observation" for reference in result["evidence"]
    )
    paths = [
        _artifact_path(directory, reference["path"])
        for reference in result["evidence"]
        if reference["retained"] and reference.get("path")
    ]
    checks["unique_evidence_paths"] = len(paths) == len(set(paths))
    known_ids = set(evidence_ids)
    checks["known_claim_evidence"] = all(
        set(claim["evidence_ids"]).issubset(known_ids)
        for claim in [*result["findings"], *result["verified_behaviors"]]
    )
    checks["consistent_result_counts"] = metrics["findings_count"] == len(
        result["findings"]
    ) and metrics["verified_behaviors_count"] == len(result["verified_behaviors"])
    checks["within_budgets"] = (
        metrics["browser_tool_calls"] <= dataset["task_defaults"]["max_steps"]
        and metrics["llm_calls"] <= dataset["task_defaults"]["max_model_iterations"]
        and metrics["duration_seconds"] <= dataset["task_defaults"]["max_duration_seconds"]
    )
    network = [item for item in observations if item["action_name"] in _TOOLS]
    checks["one_successful_network_capture"] = len(network) == 1 and not network[0]["error"]
    report: dict[str, Any] = {
        "dataset_id": dataset["dataset_id"],
        "grader_version": GRADER_VERSION,
        "fixture_version": dataset["fixture_version"],
        "case_id": case_id,
        "run_id": result["run_id"],
        "run_status": result["status"],
        "run_error_codes": [item["code"] for item in result["errors"]],
        "configured_model": config.get("configured_model"),
        "checks": checks,
        "metrics": metrics,
    }
    if not checks["one_successful_network_capture"]:
        return report

    observation = network[0]
    scope = [
        observation["action_arguments"][key]
        for key in _STATIC_KEYS
        if key in observation["action_arguments"]
    ]
    checks["static_resources_requested"] = bool(scope) and all(value is True for value in scope)
    references = [
        item for item in result["evidence"] if item["id"] == observation.get("evidence_ref")
    ]
    checks["linked_retained_evidence"] = (
        len(references) == 1
        and references[0]["observation_id"] == observation["id"]
        and references[0]["retained"] is True
        and bool(references[0].get("path"))
    )
    if not checks["linked_retained_evidence"]:
        return report
    raw = json.loads(_artifact_path(directory, references[0]["path"]).read_text(encoding="utf-8"))
    checks["tool_response_succeeded"] = not (isinstance(raw, dict) and raw.get("isError"))
    raw_text = _text_content(raw)
    checks["no_static_omissions"] = not any(
        int(count) > 0
        for count in re.findall(r"^Note: (\d+) static requests not shown", raw_text, re.MULTILINE)
    )
    records, unknown_lines = _parse_requests(raw_text)
    checks["readable_listing"] = bool(records) and unknown_lines == 0
    checks["same_origin_capture"] = all(
        (urlsplit(record.url).scheme, urlsplit(record.url).netloc) == (target.scheme, target.netloc)
        for record in records
    )
    captured = {(item.method, urlsplit(item.url).path, item.outcome) for item in records}
    oracle = {(item.method, item.path, str(item.status)) for item in expected}
    missing = oracle - captured
    unexpected = captured - oracle - {("GET", "/favicon.ico", "204")}
    checks["all_expected_requests_retained"] = not missing
    checks["no_unexpected_requests"] = not unexpected

    summary = observation["summary"]
    checks["compact_summary"] = 0 < len(summary) <= min(1_000, task["max_observation_chars"])
    actual_counts = (
        len(records),
        sum(item.outcome.isdigit() and int(item.outcome) >= 400 for item in records),
        sum(item.outcome == "FAILED" for item in records),
        sum(item.outcome == "UNKNOWN" for item in records),
    )
    counts = _COUNTS.fullmatch(summary.splitlines()[0]) if summary else None
    checks["accurate_summary_counts"] = (
        counts is not None and tuple(map(int, counts.groups())) == actual_counts
    )
    # Keep the origin in summary identities. A copied path on another host is not
    # evidence of a captured request. Query values remain excluded for redaction.
    summary_records = {
        (
            match[2],
            urlsplit(match[3]).scheme,
            urlsplit(match[3]).netloc,
            urlsplit(match[3]).path,
            match[1],
        )
        for line in summary.splitlines()
        if (match := _SUMMARY_REQUEST.match(line))
    }
    failures = {
        (method, target.scheme, target.netloc, path, status)
        for method, path, status in oracle
        if int(status) >= 400
    }
    checks["expected_failures_in_summary"] = failures.issubset(summary_records)
    captured_summary_records = {
        (
            item.method,
            urlsplit(item.url).scheme,
            urlsplit(item.url).netloc,
            urlsplit(item.url).path,
            item.outcome,
        )
        for item in records
    }
    checks["summary_adds_no_requests"] = summary_records.issubset(captured_summary_records)
    report.update(
        expected_requests=len(oracle),
        matched_requests=len(oracle - missing),
        capture_recall=len(oracle - missing) / len(oracle),
        missing_requests=sorted(missing),
        unexpected_requests=sorted(unexpected),
        expected_failures=len(failures),
        failure_summary_recall=(
            len(failures & summary_records) / len(failures) if failures else None
        ),
        raw_text_chars=len(raw_text),
        summary_chars=len(summary),
        summary_limit=min(1_000, task["max_observation_chars"]),
    )
    return report


def grade_run(case_id: str, directory: Path) -> dict[str, Any]:
    """Return an offline verdict; malformed or missing artifacts cannot pass."""
    load_case(case_id)
    try:
        report = _grade(case_id, directory)
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError) as error:
        return {
            "case_id": case_id,
            "grader_version": GRADER_VERSION,
            "verdict": "invalid_artifacts",
            "passed": False,
            "failure_category": "artifacts",
            "failed_checks": ["invalid_artifacts"],
            "artifact_error_type": type(error).__name__,
        }
    report["passed"] = all(report["checks"].values())
    report["verdict"] = "pass" if report["passed"] else "fail"
    report["failed_checks"] = [key for key, value in report["checks"].items() if not value]
    infrastructure = (
        report["run_status"] == "failed"
        and bool(set(report["run_error_codes"]) & {"llm_api_error", "mcp_unavailable"})
        and not report["checks"]["one_successful_network_capture"]
    )
    report["failure_category"] = (
        None if report["passed"] else "infrastructure" if infrastructure else "evaluation"
    )
    return report


def main() -> None:
    """Print a JSON score for one saved run; this command never calls a model or browser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case-id", choices=[case["id"] for case in load_dataset()["cases"]], required=True
    )
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    report = grade_run(args.case_id, args.run_dir)
    print(json.dumps(report, indent=2, allow_nan=False))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
