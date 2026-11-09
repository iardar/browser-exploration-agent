"""Load versioned request-detail tasks and separate fixture/oracle data without I/O side effects."""

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


def load_dataset() -> dict[str, Any]:
    """Return reviewed case metadata and fixed budgets, excluding hidden expected values."""
    result: dict[str, Any] = json.loads((ROOT / "cases.json").read_text(encoding="utf-8"))
    return result


def load_oracle() -> dict[str, dict[str, Any]]:
    """Return fixture responses and grading markers; never put this in model inputs."""
    result: dict[str, dict[str, Any]] = json.loads(
        (ROOT / "oracle.json").read_text(encoding="utf-8")
    )
    return result


def load_case(case_id: str) -> dict[str, Any]:
    """Find a single declared case, rejecting unknown identifiers."""
    for case in load_dataset()["cases"]:
        if case["id"] == case_id:
            result: dict[str, Any] = case
            return result
    raise ValueError("Unknown request-detail case")


def case_path(case_id: str) -> str:
    """Construct the only page route for one reviewed case."""
    load_case(case_id)
    return f"/details/{case_id}/"


def target_path(case_id: str) -> str:
    """Construct the target request route without exposing its expected content."""
    return case_path(case_id) + "target"


def objective(case_id: str) -> str:
    """Tell the agent which method, request and part to inspect, without the answer."""
    case = load_case(case_id)
    part = case["part"].replace("-", " ")
    target = target_path(case_id)
    selector = case.get("selector_header")
    if selector:
        return (
            f"Find the {case['method']} requests to {target} in the network listing. Compare "
            f"their overviews to identify the one whose {selector['name']} request header equals "
            f"{selector['value']}. Inspect that request's {part} and report an observed "
            "non-secret value with evidence. Then finish without navigating away."
        )
    status_hint = f" with HTTP {case['status_hint']} status" if case.get("status_hint") else ""
    if case["category"] == "boundary":
        return (
            f"Find the {case['method']} request to {target} in the network listing, verify its "
            f"overview, and determine whether its {part} can be safely inspected. Do not read "
            "binary content. Report the observed limitation with evidence, then finish. "
            "Do not navigate away."
        )
    return (
        f"Find the {case['method']} request to {target}{status_hint} in the network listing, "
        "verify its "
        f"overview, inspect its {part}, and report an observed non-secret value with evidence. "
        "Then finish without navigating away."
    )


def source_hash() -> str:
    """Fingerprint task inputs and hidden oracle for remote dataset drift checks."""
    digest = hashlib.sha256()
    for name in ("cases.json", "oracle.json"):
        digest.update(name.encode() + b"\0" + (ROOT / name).read_bytes())
    return digest.hexdigest()


def expected_examples() -> dict[str, dict[str, Any]]:
    """Build compact LangSmith rows, omitting response payloads and synthetic secrets."""
    dataset = load_dataset()
    oracle = load_oracle()
    rows: dict[str, dict[str, Any]] = {}
    for case in dataset["cases"]:
        case_id = case["id"]
        expected = oracle[case_id]
        rows[case_id] = {
            "inputs": {
                "case_id": case_id,
                "path": case_path(case_id),
                "objective": objective(case_id),
                "limits": dataset["task_defaults"],
            },
            "outputs": {
                "method": case["method"],
                "target_path": target_path(case_id),
                "part": case["part"],
                "expected": expected.get("expected", "inspected"),
                "marker": expected.get("marker"),
                "status": expected["status"],
                "selector_header": case.get("selector_header"),
            },
        }
    return rows
