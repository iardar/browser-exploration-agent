"""Opt-in LangSmith dataset publication and isolated request-detail agent experiments."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import json
import os
import subprocess
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from dotenv import load_dotenv
from langsmith import Client
from langsmith.schemas import Dataset, Example

from browser_exploration.browser_tools import network_detail_parts
from browser_exploration.config import Settings
from browser_exploration.contracts import BrowserExplorationTask
from browser_exploration.explorer import AutonomousBrowserExplorer
from evals.network_capture.deployment import DEFAULT_CONTAINER, DEFAULT_MCP_URL, IsolationError
from evals.network_capture.fixture import fixture_fingerprint
from evals.network_capture.isolation import TrialConnection, TrialIsolation, open_session

from .dataset import ROOT, expected_examples, load_dataset, objective, source_hash
from .grade import GRADER_VERSION, grade_run
from .probe import ProbeError, probe_case

DATASET_NAME = "browser-network-request-details-native-v2"


class DetailEvaluationError(ValueError):
    """A safe setup failure, without raw MCP data or credentials."""


def _validate_dataset(client: Client) -> Dataset:
    """Require all remote examples and the source hash to match the reviewed local corpus."""
    try:
        dataset = client.read_dataset(dataset_name=DATASET_NAME)
        examples: list[Example] = list(client.list_examples(dataset_id=dataset.id, limit=None))
    except Exception as exc:
        raise DetailEvaluationError(
            f"Cannot read the detail dataset ({type(exc).__name__}); publish it first."
        ) from None
    if dataset.name != DATASET_NAME:
        raise DetailEvaluationError("The remote detail dataset name differs from local source.")
    if (dataset.metadata or {}).get("source_content_sha256") != source_hash():
        raise DetailEvaluationError("The remote detail dataset has a different source hash.")
    expected = expected_examples()
    actual: dict[str, Example] = {}
    for example in examples:
        case_id = example.inputs.get("case_id") if example.inputs else None
        if not isinstance(case_id, str) or case_id in actual:
            raise DetailEvaluationError("The detail dataset has a missing or duplicate case ID.")
        actual[case_id] = example
    if set(actual) != set(expected) or len(examples) != len(expected):
        raise DetailEvaluationError("The detail dataset has missing or extra examples.")
    for case_id, row in expected.items():
        if actual[case_id].inputs != row["inputs"] or actual[case_id].outputs != row["outputs"]:
            raise DetailEvaluationError(f"Remote detail case {case_id} differs from local source.")
    return cast(Dataset, dataset)


def publish_dataset(client: Client) -> Dataset:
    """Create the separate v2 dataset once; validate instead of altering an existing one."""
    try:
        exists = client.has_dataset(dataset_name=DATASET_NAME)
    except Exception as exc:
        raise DetailEvaluationError(
            f"Cannot check the detail dataset ({type(exc).__name__}); check connectivity."
        ) from None
    if exists:
        return _validate_dataset(client)
    try:
        dataset = client.create_dataset(
            dataset_name=DATASET_NAME,
            description="22 reviewed, isolated browser network request-detail cases.",
            metadata={
                "source_content_sha256": source_hash(),
                "local_dataset_id": load_dataset()["dataset_id"],
            },
        )
        client.create_examples(
            dataset_id=dataset.id,
            examples=list(expected_examples().values()),
        )
    except Exception as exc:
        raise DetailEvaluationError(
            f"Could not publish the detail dataset ({type(exc).__name__}); "
            "inspect the remote dataset before retrying."
        ) from None
    return _validate_dataset(client)


def _fixture_url(base_url: str, path: str, settings: Settings) -> str:
    """Join a reviewed route to a controlled origin, enforcing the private-target opt-in."""
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
        or not relative.path.startswith("/details/")
        or relative.query
        or relative.fragment
    ):
        raise DetailEvaluationError("Use a controlled HTTP(S) origin and a reviewed detail path.")
    hostname = base.hostname.casefold()
    private = hostname == "localhost"
    try:
        private = private or ipaddress.ip_address(hostname).is_private
    except ValueError:
        pass
    if private and not settings.allow_private_network:
        raise DetailEvaluationError("Local fixtures require BROWSER_ALLOW_PRIVATE_NETWORK=true.")
    return base_url.rstrip("/") + path


def _source_metadata() -> dict[str, Any]:
    """Fingerprint the agent, corpus and shared deployment used in this experiment."""
    repository = ROOT.parent.parent
    paths = sorted(
        [
            *(repository / "src/browser_exploration").glob("*.py"),
            *ROOT.glob("*.py"),
            ROOT / "cases.json",
            ROOT / "oracle.json",
            repository / "evals/network_capture/fixture.py",
            repository / "evals/network_capture/isolation.py",
            repository / "evals/network_capture/compose.yaml",
            repository / "pyproject.toml",
            repository / "uv.lock",
        ]
    )
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(repository).as_posix().encode() + b"\0")
        digest.update(path.read_bytes())
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    return {
        "source_revision": revision.stdout.strip() if revision.returncode == 0 else "unknown",
        "source_snapshot_sha256": digest.hexdigest(),
        "dataset_source_content_sha256": source_hash(),
        "fixture_sha256": fixture_fingerprint(),
        "grader_version": GRADER_VERSION,
    }


def score_detail(
    inputs: dict[str, Any], outputs: dict[str, Any], reference_outputs: dict[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    """Publish only compact checks after verifying the remote reference row."""
    case_id = inputs.get("case_id")
    expected = (
        expected_examples().get(case_id, {}).get("outputs") if isinstance(case_id, str) else None
    )
    consistent = expected is not None and reference_outputs == expected
    raw_grade = outputs.get("grade") if isinstance(outputs, dict) else None
    graded = isinstance(raw_grade, dict)
    grade: dict[str, Any] = raw_grade if isinstance(raw_grade, dict) else {}
    checks = grade.get("checks")
    checks = checks if isinstance(checks, dict) else {}
    names = (
        "target_listed_with_visible_index",
        "verified_overview",
        "correct_part_or_boundary",
        "evidence_backed_report",
        "no_wrong_request",
        "secret_redacted",
    )
    results: list[dict[str, Any]] = [
        {
            "key": "detail_pass",
            "score": float(grade.get("passed") is True) if consistent and graded else None,
        },
        {"key": "oracle_consistent", "score": float(consistent)},
    ]
    for name in names:
        value = checks.get(name)
        results.append(
            {
                "key": name,
                "score": float(value) if consistent and isinstance(value, bool) else None,
            }
        )
    return {"results": results}


async def _check_detail_capability(settings: Settings, timeout: float) -> list[str]:
    """Fail before any model call if the pinned MCP lacks all four inspected parts."""
    record: dict[str, Any] = {}
    try:
        async with open_session(settings, record, timeout) as session:
            name = session.capabilities.get("network_detail")
            if name is None:
                raise DetailEvaluationError("The MCP server lacks browser_network_request.")
            parts = network_detail_parts(session.tools[name])
            required = {"request-headers", "request-body", "response-headers", "response-body"}
            if not required <= parts:
                raise DetailEvaluationError("The MCP server lacks required request-detail parts.")
            return sorted(parts)
    except DetailEvaluationError:
        raise
    except Exception as exc:
        raise DetailEvaluationError(
            f"Cannot verify the MCP detail tool ({type(exc).__name__})."
        ) from None


async def _probe_corpus(
    settings: Settings, fixture_urls: dict[str, str], timeout: float
) -> list[str]:
    """Check every fixture/tool combination in disposable sessions before model calls."""
    verified: list[str] = []
    for case_id, url in fixture_urls.items():
        record: dict[str, Any] = {}
        try:
            async with open_session(settings, record, timeout) as session:
                await probe_case(session, url, case_id)
        except ProbeError as exc:
            raise DetailEvaluationError(str(exc)) from None
        except Exception as exc:
            raise DetailEvaluationError(
                f"{case_id}: live detail probe failed ({type(exc).__name__})."
            ) from None
        verified.append(case_id)
    return verified


async def run_evaluation(args: argparse.Namespace, client: Client) -> None:
    """Run serial, isolated trials and publish grades to a separate experiment family."""
    settings = Settings(
        mcp_url=args.mcp_url,
        output_dir=args.output_dir,
        retain_raw_evidence=True,
        langsmith_enabled=False,
    )
    if not settings.network_restricted:
        raise DetailEvaluationError("Set BROWSER_NETWORK_RESTRICTED=true after isolation exists.")
    dataset = _validate_dataset(client)
    rows = expected_examples()
    fixture_urls = {
        case_id: _fixture_url(args.fixture_base_url, row["inputs"]["path"], settings)
        for case_id, row in rows.items()
    }
    isolation = TrialIsolation(settings, args.fixture_base_url, args.deployment_container)
    try:
        await isolation.prepare()
        parts = await _check_detail_capability(settings, isolation.timeout)
        probed = await _probe_corpus(settings, fixture_urls, isolation.timeout)
    except IsolationError as exc:
        raise DetailEvaluationError(str(exc)) from None
    if args.check_only:
        print(
            json.dumps(
                {
                    "status": "ready",
                    "dataset_id": str(dataset.id),
                    "dataset_examples": len(rows),
                    "detail_parts": parts,
                    "fixture_cases_verified": len(probed),
                    "isolation_verified": True,
                    "model_calls": 0,
                },
                indent=2,
            )
        )
        return

    provenance = _source_metadata()

    async def target(inputs: dict[str, Any]) -> dict[str, Any]:
        """Run one reviewed input, returning only safe grade and usage fields."""
        case_id = inputs.get("case_id")
        connection: TrialConnection | None = None
        try:
            if not isinstance(case_id, str) or case_id not in rows:
                raise DetailEvaluationError("Unknown remote detail case.")
            if inputs != rows[case_id]["inputs"]:
                raise DetailEvaluationError("Remote detail task differs from local source.")
            task = BrowserExplorationTask(
                url=fixture_urls[case_id],
                objective=objective(case_id),
                allow_network_details=True,
                **load_dataset()["task_defaults"],
            )
            connection = isolation.connection(case_id, task.url)
            connection.record.update(provenance=provenance, task=task.model_dump(mode="json"))
            if isolation.failed:
                connection.record["status"] = "not_started"
                raise IsolationError("Further trials stopped after isolation failure.")
            result = await AutonomousBrowserExplorer(settings, connection=connection).explore(task)
            connection.record.update(run_id=result.run_id, result_status=result.status)
            if (
                connection.record["readiness"] != "ready"
                or connection.record["cleanup"] != "passed"
            ):
                isolation.failed = True
                raise IsolationError("Trial isolation or fixture readiness was not established.")
            report = grade_run(case_id, settings.output_dir / result.run_id)
            return {
                "case_id": case_id,
                "run_id": result.run_id,
                "run_status": result.status,
                "grade": {
                    "grader_version": report["grader_version"],
                    "passed": report["passed"],
                    "verdict": report["verdict"],
                    "failed_checks": report["failed_checks"],
                    "checks": report.get("checks", {}),
                },
                "execution": {
                    "duration_seconds": result.metrics.duration_seconds,
                    "browser_tool_calls": result.metrics.browser_tool_calls,
                    "llm_calls": result.metrics.llm_calls,
                    "input_tokens": result.metrics.input_tokens,
                    "output_tokens": result.metrics.output_tokens,
                },
            }
        except Exception as exc:
            if connection is not None:
                connection.record["runner_error_type"] = type(exc).__name__
                if connection.record.get("status") == "planned":
                    connection.record["status"] = "failed"
            return {
                "case_id": case_id,
                "run_status": "runner_error",
                "error_type": type(exc).__name__,
                "grade": None,
                "execution": None,
            }
        finally:
            if connection is not None:
                connection.save()

    results = await client.aevaluate(
        target,
        data=dataset.id,
        evaluators=[score_detail],
        metadata={
            "evaluation_layer": "live_browser",
            "dataset_id": load_dataset()["dataset_id"],
            "models": [settings.model],
            "planned_repetitions": args.num_repetitions,
            "deployment": isolation.deployment,
            "runtime_versions": isolation.versions,
            "detail_parts": parts,
            **provenance,
        },
        experiment_prefix=args.experiment_prefix,
        description="Selected request details on a controlled fixture; raw evidence remains local.",
        max_concurrency=1,
        num_repetitions=args.num_repetitions,
        error_handling="log",
    )
    await results.wait()
    if isolation.failed:
        raise DetailEvaluationError("Evaluation stopped after isolation failure; inspect records.")
    print(
        json.dumps(
            {
                "status": "completed",
                "dataset_id": str(dataset.id),
                "experiment_name": results.experiment_name,
                "experiment_id": str(results.experiment_id),
                "experiment_url": results.url,
                "result_rows": len(results),
            },
            indent=2,
        )
    )


def _arguments() -> argparse.Namespace:
    """Parse explicit external-publication, preflight and trial options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish-dataset", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--fixture-base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--mcp-url", default=DEFAULT_MCP_URL)
    parser.add_argument("--deployment-container", default=DEFAULT_CONTAINER)
    parser.add_argument("--experiment-prefix", default="network-request-details-agent")
    parser.add_argument("--num-repetitions", type=int, default=1)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("runs/evals/network-request-details-langsmith")
    )
    args = parser.parse_args()
    if args.num_repetitions < 1 or args.num_repetitions > 10:
        parser.error("--num-repetitions must be between 1 and 10")
    if args.publish_dataset and args.check_only:
        parser.error("--publish-dataset and --check-only are separate operations")
    return args


def main() -> None:
    """Publish/validate a remote dataset or run an explicitly requested live experiment."""
    load_dotenv(override=False)
    args = _arguments()
    if not os.getenv("LANGSMITH_API_KEY"):
        raise SystemExit("LANGSMITH_API_KEY is required; do not print its value.")
    if not args.publish_dataset and not args.check_only and not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is required for a live agent experiment.")
    try:
        client = Client()
        if args.publish_dataset:
            dataset = publish_dataset(client)
            print(json.dumps({"status": "ready", "dataset_id": str(dataset.id)}, indent=2))
        else:
            asyncio.run(run_evaluation(args, client))
    except DetailEvaluationError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
