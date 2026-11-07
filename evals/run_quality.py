"""Audit saved runs and compare reference context policies without browser or model calls."""

import argparse
import json
import os
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from browser_exploration.browser_tools import ALIASES
from browser_exploration.context_builder import ContextBuilder
from browser_exploration.contracts import (
    BrowserExplorationResult,
    BrowserExplorationTask,
    BrowserObservation,
)
from browser_exploration.reporting import redact
from browser_exploration.state import SuccessfulAction

from .network_capture.grade import (
    _SUMMARY_REQUEST,
    _artifact_path,
    _parse_requests,
    _text_content,
)

_REF = re.compile(r"\[ref=([^\]\s]+)\]")
_NETWORK_GOAL = re.compile(r"\b(?:network|nework)\b", re.IGNORECASE)
_OBSERVATION_MARKER = "\n\nRECENT OBSERVATION — UNTRUSTED DATA\n"


def _json(path: Path, warnings: list[str]) -> Any:
    """Read an artifact without repairing it or printing its contents on failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        warnings.append(str(path))
        return None


def _jsonl(path: Path, warnings: list[str]) -> list[dict[str, Any]]:
    """Preserve valid records in a damaged log and identify every unreadable line."""
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        warnings.append(str(path))
        return []
    rows = []
    for number, line in enumerate(lines, 1):
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("Expected an object")
            rows.append(row)
        except ValueError:
            warnings.append(f"{path}:{number}")
    return rows


def _raw(directory: Path, reference: dict[str, Any], warnings: list[str]) -> Any:
    """Open only evidence resolved beneath the authorized run directory."""
    if not reference.get("retained") or not reference.get("path"):
        return None
    try:
        path = _artifact_path(directory, reference["path"])
    except (TypeError, ValueError):
        warnings.append(f"{directory}: unsafe evidence path")
        return None
    return _json(path, warnings)


def _reference_proposal(event: dict[str, Any]) -> bool:
    """Count usable reference-bearing proposals without trusting damaged decision records."""
    decision = event.get("decision")
    if event.get("event") != "model_decision" or not isinstance(decision, dict):
        return False
    action = decision.get("action")
    arguments = action.get("arguments") if isinstance(action, dict) else None
    return isinstance(arguments, dict) and bool({"ref", "target"} & arguments.keys())


def _reference_counts(content: str, active_id: str, allowed: set[str]) -> tuple[int, int, int]:
    """Count reference tokens in selected observation excerpts, excluding progress prose."""
    current = historical = owner = 0
    for section in content.split(_OBSERVATION_MARKER)[1:]:
        header, _, body = section.partition("\n")
        try:
            metadata = json.loads(header)
        except ValueError:
            continue  # An allowance can truncate an observation before its metadata fits.
        body = body.partition("\naction_arguments:")[0]
        body = re.split(
            r"\n\n(?:COMPLETED CHECKS|FINDINGS ALREADY|UNRESOLVED|REQUIREMENT|SUPPLEMENTARY)", body
        )[0]
        refs = _REF.findall(body)
        valid = sum(value in allowed for value in refs)
        current += valid
        historical += len(refs) - valid
        if metadata.get("id") == active_id:
            owner += valid
    return current, historical, owner


def benchmark_reference_context(
    task: BrowserExplorationTask,
    observations: list[BrowserObservation],
    raw_by_id: dict[str, Any],
) -> dict[str, Any]:
    """Compare identical reconstructed snapshot checkpoints with and without stale-ref filtering.

    These are controlled reconstructions, not archived outgoing provider requests.
    Findings and questions are omitted; they have lower priority than observations.
    Retained raw snapshots supply the reference oracle independently of summaries.
    """
    totals = Counter[str]()
    actions: list[SuccessfulAction] = []
    for index, observed in enumerate(observations):
        if not observed.error:
            capability = next(
                (key for key, names in ALIASES.items() if observed.action_name in names), "other"
            )
            actions.append(
                {
                    "capability": capability,
                    "action_name": observed.action_name,
                    "arguments": observed.action_arguments,
                    "url": observed.url,
                    "observation_id": observed.id,
                    "evidence_id": observed.evidence_ref or "",
                }
            )
        if observed.error or observed.action_name not in ALIASES["snapshot"]:
            continue
        raw = raw_by_id.get(observed.id)
        if raw is None:
            totals["checkpoints_without_raw_evidence"] += 1
            continue
        allowed = {
            match[1] for line in _text_content(raw).splitlines() if (match := _REF.search(line))
        }
        if not allowed:
            continue
        kwargs: dict[str, Any] = {
            "task": task,
            "current_url": observed.url,
            "observations": observations[
                max(0, index + 1 - task.max_recent_observations) : index + 1
            ],
            "verified_behaviors": [],
            "findings": [],
            "unresolved_questions": [],
            "successful_actions": actions,
        }
        try:
            before = ContextBuilder().build(**kwargs)
            after = ContextBuilder().build(
                **kwargs, current_references=allowed, reference_observation_id=observed.id
            )
        except ValueError:
            totals["checkpoints_exceeding_mandatory_budget"] += 1
            continue
        before_current, before_old, before_owner = _reference_counts(
            str(before[1].content), observed.id, allowed
        )
        after_current, after_old, after_owner = _reference_counts(
            str(after[1].content), observed.id, allowed
        )
        totals.update(
            {
                "checkpoints": 1,
                "legacy_current_refs": before_current,
                "legacy_historical_refs": before_old,
                "filtered_current_refs": after_current,
                "filtered_historical_refs": after_old,
                "legacy_owner_refs": before_owner,
                "filtered_owner_refs": after_owner,
                "affected_checkpoints": int(before_old > 0),
                "legacy_message_chars": sum(len(str(item.content)) for item in before),
                "filtered_message_chars": sum(len(str(item.content)) for item in after),
            }
        )
    return dict(totals)


def _audit_run(path: Path, warnings: list[str]) -> dict[str, Any] | None:
    result = _json(path, warnings)
    config = _json(path.parent / "run_config.json", warnings)
    if not isinstance(result, dict) or not isinstance(config, dict):
        return None
    try:
        result = BrowserExplorationResult.model_validate(result).model_dump(mode="json")
    except ValueError:
        warnings.append(f"{path}: invalid result contract")
        return None
    observations = []
    for index, observed in enumerate(_jsonl(path.parent / "trajectory.jsonl", warnings), 1):
        try:
            observations.append(BrowserObservation.model_validate(observed).model_dump(mode="json"))
        except ValueError:
            warnings.append(f"{path.parent}/trajectory.jsonl: invalid observation record {index}")
    events = _jsonl(path.parent / "decisions.jsonl", warnings)
    references = {item["id"]: item for item in result.get("evidence", [])}
    raw_by_id = {}
    for observed in observations:
        raw_by_id[observed["id"]] = _raw(
            path.parent, references.get(observed.get("evidence_ref"), {}), warnings
        )
    captures = []
    for observed in observations:
        if observed["action_name"] not in ALIASES["network"]:
            continue
        raw = raw_by_id[observed["id"]]
        text = _text_content(raw)
        records, unparsed = _parse_requests(text)
        failed = {
            (item.method, item.url, item.outcome)
            for item in records
            if item.outcome == "FAILED" or item.outcome.isdigit() and int(item.outcome) >= 400
        }
        summary_records, _ = _parse_requests(observed["summary"])
        shown = {(item.method, item.url, item.outcome) for item in summary_records}
        shown.update(
            (match[2], match[3], match[1])
            for line in observed["summary"].splitlines()
            if (match := _SUMMARY_REQUEST.match(line))
        )
        captures.append(
            {
                "observation_id": observed["id"],
                "successful": not observed["error"],
                "readable_retained_evidence": raw is not None,
                "include_static": observed["action_arguments"].get(
                    "static", observed["action_arguments"].get("includeStatic")
                ),
                "parsed_requests": len(records),
                "unparsed_lines": unparsed,
                "failed_requests": sum(
                    item.outcome == "FAILED" or item.outcome.isdigit() and int(item.outcome) >= 400
                    for item in records
                ),
                "raw_text_chars": len(text),
                "summary_chars": len(observed["summary"]),
                "distinct_failures": len(failed),
                "visible_distinct_failures": len(failed & shown),
            }
        )
    benchmark: dict[str, Any] = {}
    try:
        task = BrowserExplorationTask.model_validate(config["task"])
        typed = [BrowserObservation.model_validate(item) for item in observations]
        benchmark = benchmark_reference_context(task, typed, raw_by_id)
    except (KeyError, TypeError, ValueError):
        warnings.append(f"{path.parent}: invalid task/trajectory for context benchmark")
    return {
        "scanned_at": datetime.now(UTC).isoformat(),
        "run_id": result["run_id"],
        "directory": str(path.parent),
        "adapter": config.get("model_adapter"),
        "model": config.get("configured_model"),
        "status": result["status"],
        "objective": result["objective"],
        "network_requested_explicitly": bool(_NETWORK_GOAL.search(result["objective"])),
        "error_codes": [item["code"] for item in result.get("errors", [])],
        "metrics": result["metrics"],
        "findings": len(result["findings"]),
        "verified_behaviors": len(result["verified_behaviors"]),
        "unresolved_questions": len(result["unresolved_questions"]),
        "captures": captures,
        "rejected_actions": sum(item.get("event") == "action_rejected" for item in events),
        "reference_rejections": sum(
            item.get("event") == "action_rejected"
            and "reference in the current snapshot" in item.get("reason", "")
            for item in events
        ),
        "reference_action_proposals": sum(_reference_proposal(item) for item in events),
        "rejection_reasons": dict(
            Counter(
                item.get("reason", "unknown")
                for item in events
                if item.get("event") == "action_rejected"
            )
        ),
        "schema_errors": sum(
            item.get("event") == "model_error" and item.get("code") == "llm_validation_error"
            for item in events
        ),
        "context_benchmark": benchmark,
    }


def audit_runs(root: Path) -> dict[str, Any]:
    """Inspect every completed run and report incomplete or damaged artifacts separately."""
    warnings: list[str] = []
    derived: list[Path] = []
    for manifest in sorted(root.rglob("evaluation_manifest.json")):
        try:
            metadata = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            warnings.append(str(manifest))
            continue
        if (
            isinstance(metadata, dict)
            and metadata.get("report_type") == "network-grader-derived-artifacts/v1"
        ):
            derived.append(manifest.parent)

    def is_derived(path: Path) -> bool:
        return any(path.is_relative_to(directory) for directory in derived)

    result_paths = sorted(root.rglob("result.json"))
    rows = [
        row for path in result_paths if not is_derived(path) and (row := _audit_run(path, warnings))
    ]
    totals = Counter[str]()
    for row in rows:
        totals.update(row["context_benchmark"])
    browser_network = [
        row
        for row in rows
        if row["adapter"] == "OpenAIDecisionModel" and row["network_requested_explicitly"]
    ]
    provider_blocked = [row for row in browser_network if "llm_api_error" in row["error_codes"]]
    captured = [
        row
        for row in browser_network
        if any(
            item["successful"] and item["readable_retained_evidence"] for item in row["captures"]
        )
    ]
    legacy_total = totals["legacy_current_refs"] + totals["legacy_historical_refs"]
    filtered_total = totals["filtered_current_refs"] + totals["filtered_historical_refs"]
    totals_data: dict[str, Any] = dict(totals)
    totals_data.update(
        legacy_current_reference_precision=totals["legacy_current_refs"] / legacy_total
        if legacy_total
        else None,
        filtered_current_reference_precision=totals["filtered_current_refs"] / filtered_total
        if filtered_total
        else None,
        current_reference_retention=totals["filtered_owner_refs"] / totals["legacy_owner_refs"]
        if totals["legacy_owner_refs"]
        else None,
    )
    reference_proposals = sum(row["reference_action_proposals"] for row in rows)
    reference_rejections = sum(row["reference_rejections"] for row in rows)
    captures = [capture for row in rows for capture in row["captures"]]
    return {
        "scanned_at": datetime.now(UTC).isoformat(),
        "report_type": "browser-exploration-run-quality/v1",
        "run_count": len(rows),
        "excluded_evaluation_directories": [str(path) for path in derived],
        "excluded_evaluation_results": sum(is_derived(path) for path in result_paths),
        "statuses": dict(Counter(row["status"] for row in rows)),
        "error_codes": dict(Counter(code for row in rows for code in row["error_codes"])),
        "rejected_actions": sum(row["rejected_actions"] for row in rows),
        "reference_rejections": reference_rejections,
        "reference_action_proposals": reference_proposals,
        "reference_rejection_rate": reference_rejections / reference_proposals
        if reference_proposals
        else None,
        "completed_with_unresolved_questions": sum(
            row["status"] == "completed" and row["unresolved_questions"] > 0 for row in rows
        ),
        "network": {
            "explicit_browser_goals": len(browser_network),
            "provider_blocked": len(provider_blocked),
            "with_readable_capture": len(captured),
            "execution_eligible": len(browser_network) - len(provider_blocked),
            "scope_includes_static": sum(
                any(c["include_static"] is True for c in row["captures"]) for row in captured
            ),
            "scope_excludes_static": sum(
                any(c["include_static"] is False for c in row["captures"]) for row in captured
            ),
            "distinct_recorded_failures": sum(c["distinct_failures"] for c in captures),
            "visible_recorded_failures": sum(c["visible_distinct_failures"] for c in captures),
        },
        "reference_context_benchmark": totals_data,
        "incomplete_runs": [
            str(path.parent)
            for path in sorted(root.rglob("trajectory.jsonl"))
            if not is_derived(path) and not (path.parent / "result.json").exists()
        ],
        "warnings": list(dict.fromkeys(warnings)),
        "runs": rows,
        "interpretation": (
            "Reference metrics compare reconstructed snapshot checkpoints, "
            "not live model outcomes. "
            "Network capture presence does not prove objective fulfillment or complete traffic "
            "coverage. Event counts exclude unreadable log records, which are listed in warnings."
        ),
    }


def main() -> None:
    """Write an owner-only audit report; original run artifacts remain untouched."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--output", type=Path, default=Path("runs/quality/audit.json"))
    args = parser.parse_args()
    if not args.runs_dir.is_dir():
        parser.error("The runs directory does not exist")
    if args.output.exists():
        existing = _json(args.output, [])
        if not isinstance(existing, dict) or not {
            "runs",
            "reference_context_benchmark",
            "run_count",
        }.issubset(existing):
            parser.error("Refusing to overwrite a file that is not a run-quality audit")
    report = audit_runs(args.runs_dir)
    args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(
        args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump(redact(report), handle, indent=2, allow_nan=False)
        handle.write("\n")
    print(json.dumps({key: value for key, value in report.items() if key != "runs"}, indent=2))


if __name__ == "__main__":
    main()
