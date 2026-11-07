"""Run the controlled agent-capture dataset through LangSmith, serially and with local evidence."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import json
import os
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from dotenv import load_dotenv
from langsmith import Client
from langsmith.schemas import Example

from browser_exploration.config import Settings
from browser_exploration.contracts import BrowserExplorationTask
from browser_exploration.explorer import AutonomousBrowserExplorer
from browser_exploration.mcp_client import check_connection
from evals.network_capture.dataset import ROOT, load_case, load_dataset, load_requests
from evals.network_capture.grade import GRADER_VERSION, grade_run

DATASET_NAME = "browser-network-capture-native-v1"
DATASET_ID = "2ed1a341-fd4d-4b95-b123-09bf46d29420"
_ORACLE_HASH_INPUTS = (ROOT / "cases.json", ROOT / "requests.csv")


class EvaluationSetupError(ValueError):
    """Describe a preflight failure without including secrets or remote response bodies."""


def _dataset_source_hash() -> str:
    """Fingerprint the local reviewed inputs and oracle used by this dataset."""
    digest = hashlib.sha256()
    digest.update(_ORACLE_HASH_INPUTS[0].read_bytes())
    digest.update(b"\0")
    digest.update(_ORACLE_HASH_INPUTS[1].read_bytes())
    return digest.hexdigest()


def _expected_examples() -> dict[str, dict[str, Any]]:
    """Build local copies of the expected dataset rows for read-back validation."""
    dataset = load_dataset()
    expected: dict[str, dict[str, Any]] = {}
    for case in dataset["cases"]:
        case_id = case["id"]
        expected[case_id] = {
            "inputs": {
                "case_id": case_id,
                "path": case["path"],
                "objective": dataset["objective"],
                "limits": dataset["task_defaults"],
            },
            "outputs": {
                "expected_requests": [
                    {"method": item.method, "path": item.path, "status": item.status}
                    for item in load_requests(case_id)
                ]
            },
        }
    return expected


def _validate_dataset(client: Client, dataset_id: str) -> list[Example]:
    """Require the remote dataset to match the current local cases and oracle exactly."""
    try:
        dataset = client.read_dataset(dataset_id=dataset_id)
        examples = list(client.list_examples(dataset_id=dataset_id, limit=100))
    except Exception as exc:
        raise EvaluationSetupError(
            f"Cannot read LangSmith dataset ({type(exc).__name__}); "
            "check credentials and workspace."
        ) from None

    if dataset.name != DATASET_NAME:
        raise EvaluationSetupError("The selected LangSmith dataset name does not match.")
    metadata: dict[str, Any] = dataset.metadata or {}
    if metadata.get("source_content_sha256") != _dataset_source_hash():
        raise EvaluationSetupError("The LangSmith dataset was built from different local inputs.")

    expected = _expected_examples()
    actual: dict[str, Example] = {}
    for example in examples:
        if example.inputs is None:
            raise EvaluationSetupError("A LangSmith example has no task input.")
        case_id = example.inputs.get("case_id")
        if not isinstance(case_id, str) or case_id in actual:
            raise EvaluationSetupError("The LangSmith dataset has an invalid or duplicate case ID.")
        actual[case_id] = example
    if len(examples) != len(expected) or set(actual) != set(expected):
        raise EvaluationSetupError("The LangSmith dataset has missing, duplicate, or extra cases.")
    for case_id, row in expected.items():
        example = actual[case_id]
        if example.inputs != row["inputs"] or example.outputs != row["outputs"]:
            raise EvaluationSetupError(
                f"LangSmith example {case_id} differs from the local oracle."
            )
    return examples


def _fixture_url(base_url: str, path: str, settings: Settings) -> str:
    """Join a reviewed fixture path to a deployment URL and enforce local-target opt-in."""
    base = urlsplit(base_url)
    relative = urlsplit(path)
    if (
        base.scheme not in {"http", "https"}
        or not base.hostname
        or base.username is not None
        or base.password is not None
        or base.query
        or base.fragment
        or base.path not in {"", "/"}
        or relative.scheme
        or relative.netloc
        or not relative.path.startswith("/")
        or relative.query
        or relative.fragment
    ):
        raise EvaluationSetupError("Use an HTTP(S) fixture origin and a reviewed case path.")

    hostname = base.hostname.casefold()
    private_host = hostname == "localhost"
    try:
        private_host = private_host or ipaddress.ip_address(hostname).is_private
    except ValueError:
        pass
    if private_host and not settings.allow_private_network:
        raise EvaluationSetupError("A local fixture requires BROWSER_ALLOW_PRIVATE_NETWORK=true.")
    return base_url.rstrip("/") + path


def _oracle_matches_reference(case_id: str, reference_outputs: Any) -> bool:
    """Confirm the LangSmith reference row still matches the reviewed local oracle."""
    if not isinstance(reference_outputs, dict):
        return False
    reference = reference_outputs.get("expected_requests")
    if not isinstance(reference, list):
        return False
    try:
        local = [
            {"method": item.method, "path": item.path, "status": item.status}
            for item in load_requests(case_id)
        ]
    except ValueError:
        return False
    return reference == local


def score_capture(
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    reference_outputs: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Map a saved local grade to LangSmith feedback without uploading raw browser evidence."""
    case_id = str(inputs.get("case_id", ""))
    oracle_consistent = _oracle_matches_reference(case_id, reference_outputs)
    grade = outputs.get("grade") if isinstance(outputs, dict) else None
    grade = grade if isinstance(grade, dict) else {}
    checks = grade.get("checks", {})
    checks = checks if isinstance(checks, dict) else {}
    unexpected_check = checks.get("no_unexpected_requests")
    evidence_check = checks.get("linked_retained_evidence")

    capture_pass = float(grade.get("passed") is True) if oracle_consistent else None
    recall = grade.get("capture_recall") if oracle_consistent else None
    failure_recall = grade.get("failure_summary_recall") if oracle_consistent else None
    results: list[dict[str, Any]] = [
        {
            "key": "capture_pass",
            "score": capture_pass,
            "comment": "Strict deterministic grade against the reviewed request oracle.",
        },
        {
            "key": "oracle_consistent",
            "score": float(oracle_consistent),
            "comment": "LangSmith reference requests match the local reviewed oracle.",
        },
        {
            "key": "capture_recall",
            "score": recall,
            "comment": "Matched unique expected requests divided by expected requests.",
        },
        {
            "key": "failure_summary_recall",
            "score": failure_recall,
            "comment": "Expected seeded failures preserved in the summary; null when unmeasured.",
        },
        {
            "key": "no_unexpected_requests",
            "score": (
                float(unexpected_check)
                if oracle_consistent and isinstance(unexpected_check, bool)
                else None
            ),
        },
        {
            "key": "evidence_retained",
            "score": (
                float(evidence_check)
                if oracle_consistent and isinstance(evidence_check, bool)
                else None
            ),
        },
    ]
    if not oracle_consistent:
        results[0]["comment"] = "Oracle mismatch; capture quality scores are not comparable."
    return {"results": results}


def _source_metadata() -> dict[str, Any]:
    """Record a source revision and content fingerprint without uploading a diff."""
    repo_root = ROOT.parent.parent
    digest = hashlib.sha256()
    source_files = sorted(
        [
            *(repo_root / "src/browser_exploration").glob("*.py"),
            *ROOT.glob("*.py"),
            *_ORACLE_HASH_INPUTS,
            repo_root / "pyproject.toml",
            repo_root / "uv.lock",
        ]
    )
    for path in source_files:
        relative = path.relative_to(repo_root).as_posix()
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(path.read_bytes())

    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    return {
        "source_revision": revision.stdout.strip() if revision.returncode == 0 else "unknown",
        "source_dirty": bool(status.stdout.strip()) if status.returncode == 0 else None,
        "source_snapshot_sha256": digest.hexdigest(),
    }


async def run_evaluation(args: argparse.Namespace) -> None:
    """Run the native dataset serially and print its LangSmith experiment details."""
    settings = Settings(
        output_dir=args.output_dir,
        retain_raw_evidence=True,
        langsmith_enabled=False,
    )
    if not settings.network_restricted:
        raise EvaluationSetupError(
            "Set BROWSER_NETWORK_RESTRICTED=true only after external network controls are active."
        )

    client = Client()
    _validate_dataset(client, args.dataset_id)
    fixture_urls = {
        case["id"]: _fixture_url(args.fixture_base_url, case["path"], settings)
        for case in load_dataset()["cases"]
    }

    connection = await check_connection(settings)
    if connection.get("status") != "connected":
        error = connection.get("error", {}).get("code", "mcp_unavailable")
        raise EvaluationSetupError(f"MCP preflight failed ({error}).")
    if "network" not in connection.get("capabilities", {}):
        raise EvaluationSetupError("The configured MCP server lacks network-capture capability.")

    if args.check_only:
        print(
            json.dumps(
                {
                    "status": "ready",
                    "dataset_id": args.dataset_id,
                    "dataset_examples": len(load_dataset()["cases"]),
                    "mcp_status": "connected",
                    "network_capability": True,
                    "model_calls": 0,
                },
                indent=2,
            )
        )
        return

    async def target(inputs: dict[str, Any]) -> dict[str, Any]:
        """Run one dataset case and return only compact, reviewed result fields."""
        case_id = str(inputs.get("case_id", ""))
        try:
            case = load_case(case_id)
            local_dataset = load_dataset()
            if inputs.get("path") != case["path"]:
                raise EvaluationSetupError("Dataset path differs from the reviewed case.")
            if inputs.get("objective") != local_dataset["objective"]:
                raise EvaluationSetupError("Dataset objective differs from the reviewed task.")
            if inputs.get("limits") != local_dataset["task_defaults"]:
                raise EvaluationSetupError("Dataset budgets differ from the reviewed task.")

            task = BrowserExplorationTask(
                url=fixture_urls[case_id],
                objective=inputs["objective"],
                **inputs["limits"],
            )
            explorer = AutonomousBrowserExplorer(settings)
            result = await explorer.explore(task)
            report = grade_run(case_id, settings.output_dir / result.run_id)
            checks = report.get("checks", {})
            check_names = (
                "linked_retained_evidence",
                "no_unexpected_requests",
                "accurate_summary_counts",
                "expected_failures_in_summary",
                "compact_summary",
            )
            return {
                "case_id": case_id,
                "run_id": result.run_id,
                "run_status": result.status,
                "run_error_codes": [error.code for error in result.errors],
                "grade": {
                    "passed": report.get("passed", False),
                    "verdict": report.get("verdict", "invalid_artifacts"),
                    "failure_category": report.get("failure_category", "artifacts"),
                    "failed_checks": report.get("failed_checks", []),
                    "expected_requests": report.get("expected_requests"),
                    "matched_requests": report.get("matched_requests"),
                    "capture_recall": report.get("capture_recall"),
                    "expected_failures": report.get("expected_failures"),
                    "failure_summary_recall": report.get("failure_summary_recall"),
                    "summary_chars": report.get("summary_chars"),
                    "checks": {name: checks.get(name) for name in check_names},
                },
                "execution": {
                    "duration_seconds": result.metrics.duration_seconds,
                    "browser_tool_calls": result.metrics.browser_tool_calls,
                    "successful_browser_tool_calls": result.metrics.successful_browser_tool_calls,
                    "failed_browser_tool_calls": result.metrics.failed_browser_tool_calls,
                    "llm_calls": result.metrics.llm_calls,
                    "input_tokens": result.metrics.input_tokens,
                    "output_tokens": result.metrics.output_tokens,
                },
            }
        except Exception as exc:
            return {
                "case_id": case_id,
                "run_status": "runner_error",
                "error_type": type(exc).__name__,
                "grade": None,
                "execution": None,
            }

    metadata = {
        "evaluation_layer": "live_browser",
        "dataset_id": args.dataset_id,
        "dataset_source_content_sha256": _dataset_source_hash(),
        "fixture_version": load_dataset()["fixture_version"],
        "grader_version": GRADER_VERSION,
        "models": [settings.model],
        "planned_repetitions": args.num_repetitions,
        "network_boundary_attested": settings.network_restricted,
        **_source_metadata(),
    }
    results = await client.aevaluate(
        target,
        data=args.dataset_id,
        evaluators=[score_capture],
        metadata=metadata,
        experiment_prefix=args.experiment_prefix,
        description=(
            "Agent network capture on the controlled fixture. Raw browser evidence stays local."
        ),
        max_concurrency=1,
        num_repetitions=args.num_repetitions,
        error_handling="log",
    )
    await results.wait()
    print(
        json.dumps(
            {
                "status": "completed",
                "experiment_name": results.experiment_name,
                "experiment_id": str(results.experiment_id),
                "experiment_url": results.url,
                "dataset_id": args.dataset_id,
                "result_rows": len(results),
            },
            indent=2,
        )
    )


def _arguments() -> argparse.Namespace:
    """Parse explicit fixture, dataset, repetition and artifact options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture-base-url",
        default=os.getenv("BROWSER_EVAL_FIXTURE_BASE_URL", "http://127.0.0.1:8766"),
        help="Fixture origin reachable by the MCP browser (default: %(default)s)",
    )
    parser.add_argument("--dataset-id", default=DATASET_ID)
    parser.add_argument("--experiment-prefix", default="network-capture-agent")
    parser.add_argument("--num-repetitions", type=int, default=1)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("runs/evals/network-capture-langsmith")
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help=(
            "Check credentials, dataset consistency, MCP and fixture settings without model calls."
        ),
    )
    args = parser.parse_args()
    if args.num_repetitions < 1 or args.num_repetitions > 20:
        parser.error("--num-repetitions must be between 1 and 20")
    return args


def main() -> None:
    """Load operator configuration and execute the opt-in dataset evaluation."""
    load_dotenv(override=False)
    args = _arguments()
    if not os.getenv("LANGSMITH_API_KEY"):
        raise SystemExit("LANGSMITH_API_KEY is required; configure it without printing it.")
    if not args.check_only and not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is required to run the agent evaluation.")
    try:
        asyncio.run(run_evaluation(args))
    except EvaluationSetupError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
