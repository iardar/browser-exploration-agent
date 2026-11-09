"""Grade saved request-detail runs against the separate, reviewed fixture oracle."""

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from browser_exploration.contracts import (
    BrowserExplorationResult,
    BrowserExplorationTask,
    BrowserObservation,
)

from .dataset import case_path, load_case, load_dataset, load_oracle, objective, target_path

GRADER_VERSION = "2"
_NETWORK = {"browser_network_requests", "playwright_browser_network_requests"}
_DETAIL = {"browser_network_request", "playwright_browser_network_request"}
_LISTING_LINE = re.compile(
    r"^(?P<index>[1-9]\d*)\. \[(?P<method>[A-Z]+)\] (?P<url>\S+) "
    r"=> \[(?P<status>[1-5]\d{2}|FAILED)\](?:\s|$)"
)
_OVERVIEW = re.compile(
    r"^#(?P<index>[1-9]\d*) \[(?P<method>[A-Z]+)\] "
    r"(?P<url>\S+)\s*$",
    re.MULTILINE,
)
_MIME = re.compile(r"^\s*mimeType:\s*(\S+)\s*$", re.MULTILINE | re.IGNORECASE)
_STATUS = re.compile(r"^\s*status:\s*\[([1-5]\d{2})\]", re.MULTILINE)


def _read_json(path: Path) -> Any:
    """Read a saved JSON artifact, letting the caller classify malformed inputs."""
    return json.loads(path.read_text(encoding="utf-8"))


def _read_lines(path: Path) -> list[dict[str, Any]]:
    """Read JSONL records while preserving their recorded sequence."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _text(value: Any) -> str:
    """Extract inline text from MCP or redacted detail evidence without reading files."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_text(item) for item in value)
    if isinstance(value, dict):
        if value.get("type") in {"image", "image_reference", "file", "audio"}:
            return ""
        text_value = value.get("text")
        if isinstance(text_value, str):
            return text_value
        if "content" in value:
            return _text(value["content"])
    return ""


def _retained_text(directory: Path, observation: dict[str, Any], result: dict[str, Any]) -> str:
    """Follow exactly one evidence link, confined to the run directory."""
    references = [
        item for item in result["evidence"] if item["id"] == observation.get("evidence_ref")
    ]
    if len(references) != 1 or not references[0]["retained"]:
        raise ValueError("Missing retained detail evidence")
    reference = references[0]
    if reference["observation_id"] != observation["id"] or not reference.get("path"):
        raise ValueError("Mismatched detail evidence owner")
    relative = Path(reference["path"])
    if relative.is_absolute():
        raise ValueError("Absolute evidence path")
    path = (directory / relative).resolve()
    if not path.is_relative_to(directory.resolve()):
        raise ValueError("Evidence path escapes run directory")
    return _text(_read_json(path))


def _claim_has(result: dict[str, Any], value: str, evidence_id: str) -> bool:
    """Require the model's report to cite the observation containing its asserted value."""
    claims = [*result["verified_behaviors"], *result["findings"]]
    return any(
        value.casefold() in json.dumps(claim, ensure_ascii=False).casefold()
        and evidence_id in claim["evidence_ids"]
        for claim in claims
    )


def _header_in_overview(text: str, section: str, name: str, value: str) -> bool:
    """Require an exact header line inside the named overview section."""
    active = False
    for line in text.splitlines():
        if line.strip() == section:
            active = True
        elif active and line.startswith("    "):
            key, separator, found = line.strip().partition(":")
            if separator and key.casefold() == name.casefold() and found.strip() == value:
                return True
        elif active and line.strip():
            break
    return False


def _grade(case_id: str, directory: Path) -> dict[str, Any]:
    """Measure actual listing, overview, part and report behavior, including safety boundaries."""
    case = load_case(case_id)
    oracle = load_oracle()[case_id]
    dataset = load_dataset()
    result = _read_json(directory / "result.json")
    config = _read_json(directory / "run_config.json")
    observations = _read_lines(directory / "trajectory.jsonl")
    decisions = _read_lines(directory / "decisions.jsonl")
    BrowserExplorationResult.model_validate(result)
    BrowserExplorationTask.model_validate(config["task"])
    [BrowserObservation.model_validate(item) for item in observations]
    task = config["task"]
    target = urlsplit(result["target_url"])
    expected_url = f"{target.scheme}://{target.netloc}{target_path(case_id)}"
    checks: dict[str, bool] = {
        "case_url": (task["url"] == result["target_url"] and target.path == case_path(case_id)),
        "objective": task["objective"] == result["objective"] == objective(case_id),
        "fixed_budgets": all(
            task.get(key) == value for key, value in dataset["task_defaults"].items()
        ),
        "detail_permission_only": task.get("allow_network_details") is True
        and not any(
            task.get(key)
            for key in ("allow_mutations", "allow_form_input", "allow_form_submission")
        ),
        "retention": config.get("retain_raw_evidence") is True,
        "completed": result["status"] == "completed",
        "within_budgets": (
            result["metrics"]["browser_tool_calls"] <= dataset["task_defaults"]["max_steps"]
            and result["metrics"]["llm_calls"] <= dataset["task_defaults"]["max_model_iterations"]
            and result["metrics"]["duration_seconds"]
            <= dataset["task_defaults"]["max_duration_seconds"]
        ),
        "consistent_actions": (
            result["observed_actions"] == [item["action_name"] for item in observations]
            and result["metrics"]["browser_tool_calls"] == len(observations)
            and result["metrics"]["successful_browser_tool_calls"]
            == sum(not item["error"] for item in observations)
            and result["metrics"]["failed_browser_tool_calls"]
            == sum(item["error"] for item in observations)
        ),
        "stayed_on_case": all(
            item["url"] == result["target_url"]
            and (
                item["action_name"] not in {"browser_navigate", "playwright_browser_navigate"}
                or item["action_arguments"].get("url") == result["target_url"]
            )
            for item in observations
        ),
        "no_filename": all("filename" not in item["action_arguments"] for item in observations)
        and all(
            "filename" not in (item.get("action") or {}).get("arguments", {})
            for item in decisions
            if item.get("event") == "action_rejected"
        ),
        "consistent_evidence": (
            len({item["id"] for item in observations}) == len(observations)
            and len({item["id"] for item in result["evidence"]}) == len(result["evidence"])
            and {item["evidence_ref"] for item in observations}
            == {item["id"] for item in result["evidence"]}
        ),
    }
    if oracle.get("secret"):
        checks["secret_redacted"] = all(
            oracle["secret"] not in path.read_text(encoding="utf-8")
            for path in directory.rglob("*")
            if path.is_file() and path.suffix in {".json", ".jsonl"}
        )

    selector = case.get("selector_header")
    target_listing: tuple[set[int], dict[str, Any]] | None = None
    for position, item in enumerate(observations):
        if item["action_name"] not in _NETWORK or item["error"]:
            continue
        raw = _retained_text(directory, item, result)
        matches = [
            match
            for line in raw.splitlines()
            if (match := _LISTING_LINE.match(line))
            and match["method"] == case["method"]
            and match["url"] == expected_url
            and match["status"] == str(oracle["status"])
        ]
        enough = len(matches) >= 2 if selector else len(matches) == 1
        indices = {int(match["index"]) for match in matches}
        if (
            enough
            and len(indices) == len(matches)
            and all(f"[request #{index}]" in item["summary"] for index in indices)
        ):
            target_listing = (indices, item | {"position": position})
    checks["target_listed_with_visible_index"] = target_listing is not None
    checks["detail_tool_used"] = any(item["action_name"] in _DETAIL for item in observations)

    if target_listing is None:
        checks["verified_overview"] = False
        checks["correct_part_or_boundary"] = False
        checks["evidence_backed_report"] = False
    else:
        indices, listing = target_listing
        detail_actions = [
            (position, item)
            for position, item in enumerate(observations)
            if item["action_name"] in _DETAIL
        ]
        checks["no_wrong_request"] = all(
            item["action_arguments"].get("index") in indices for _, item in detail_actions
        )
        overviews = [
            (position, item)
            for position, item in detail_actions
            if position > listing["position"]
            and set(item["action_arguments"]) == {"index"}
            and item["action_arguments"]["index"] in indices
            and not item["error"]
        ]
        overview: tuple[int, dict[str, Any]] | None = None
        for position, item in overviews:
            raw = _retained_text(directory, item, result)
            identities = list(_OVERVIEW.finditer(raw))
            status = _STATUS.search(raw)
            index = item["action_arguments"]["index"]
            if (
                len(identities) == 1
                and status is not None
                and status[1] == str(oracle["status"])
                and (
                    int(identities[0]["index"]),
                    identities[0]["method"],
                    identities[0]["url"],
                )
                == (index, case["method"], expected_url)
                and (
                    selector is None
                    or _header_in_overview(
                        raw, "Request headers", selector["name"], selector["value"]
                    )
                )
            ):
                overview = (position, item)
        checks["verified_overview"] = overview is not None
        if overview is None:
            checks["correct_part_or_boundary"] = False
            checks["evidence_backed_report"] = False
        elif oracle.get("expected") == "blocked_binary":
            index = overview[1]["action_arguments"]["index"]
            checks["no_wrong_request"] = checks["no_wrong_request"] and all(
                item["action_arguments"].get("index") == index
                for _, item in detail_actions
                if "part" in item["action_arguments"]
            )
            raw = _retained_text(directory, overview[1], result)
            mime = _MIME.search(raw)
            checks["correct_part_or_boundary"] = (
                mime is not None
                and mime[1] == oracle["content_type"]
                and not any(
                    item["action_arguments"].get("part") == "response-body"
                    for _, item in detail_actions
                )
                and not any(
                    item.get("event") == "action_rejected"
                    and item.get("action", {}).get("name") in _DETAIL
                    and item.get("action", {}).get("arguments", {}).get("part") == "response-body"
                    for item in decisions
                )
            )
            checks["evidence_backed_report"] = _claim_has(
                result, "binary", overview[1]["evidence_ref"]
            ) or _claim_has(result, oracle["content_type"], overview[1]["evidence_ref"])
        else:
            index = overview[1]["action_arguments"]["index"]
            checks["no_wrong_request"] = checks["no_wrong_request"] and all(
                item["action_arguments"].get("index") == index
                for _, item in detail_actions
                if "part" in item["action_arguments"]
            )
            parts = [
                item
                for position, item in detail_actions
                if position > overview[0]
                and item["action_arguments"] == {"index": index, "part": case["part"]}
                and not item["error"]
            ]
            matched = [
                item
                for item in parts
                if oracle["marker"] in _retained_text(directory, item, result)
            ]
            if case["part"] in {"request-headers", "response-headers"}:
                section = (
                    "Request headers" if case["part"] == "request-headers" else "Response headers"
                )
                header_values = oracle[case["part"].replace("-", "_")]
                overview_text = _retained_text(directory, overview[1], result)
                if any(
                    oracle["marker"] in value
                    and _header_in_overview(overview_text, section, name, value)
                    for name, value in header_values.items()
                ):
                    matched.append(overview[1])
            checks["correct_part_or_boundary"] = bool(matched)
            checks["evidence_backed_report"] = any(
                _claim_has(result, oracle["marker"], item["evidence_ref"]) for item in matched
            )

    report: dict[str, Any] = {
        "dataset_id": dataset["dataset_id"],
        "grader_version": GRADER_VERSION,
        "case_id": case_id,
        "run_id": result["run_id"],
        "run_status": result["status"],
        "checks": checks,
    }
    return report


def grade_run(case_id: str, directory: Path) -> dict[str, Any]:
    """Return a safe offline verdict; invalid or missing artifacts cannot pass."""
    load_case(case_id)
    try:
        report = _grade(case_id, directory)
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        return {
            "dataset_id": load_dataset()["dataset_id"],
            "grader_version": GRADER_VERSION,
            "case_id": case_id,
            "verdict": "invalid_artifacts",
            "passed": False,
            "failed_checks": ["invalid_artifacts"],
            "artifact_error_type": type(exc).__name__,
        }
    report["passed"] = all(report["checks"].values())
    report["verdict"] = "pass" if report["passed"] else "fail"
    report["failed_checks"] = [name for name, value in report["checks"].items() if not value]
    return report


def main() -> None:
    """Grade one saved run locally without a model, browser or LangSmith call."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    report = grade_run(args.case_id, args.run_dir)
    print(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
