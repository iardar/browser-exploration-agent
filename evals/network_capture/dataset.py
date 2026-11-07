"""Load reviewed case inputs and request oracles without starting any services."""

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class ExpectedRequest:
    """One request the fixture must serve and the browser capture must contain."""

    case_id: str
    method: str
    path: str
    status: int
    resource: str


def load_dataset() -> dict[str, Any]:
    """Read the versioned objectives and budgets; expected requests stay in a separate CSV."""
    data: dict[str, Any] = json.loads((ROOT / "cases.json").read_text(encoding="utf-8"))
    return data


def load_requests(case_id: str) -> list[ExpectedRequest]:
    """Load the fixed request oracle for one case, in fixture discovery order."""
    with (ROOT / "requests.csv").open(encoding="utf-8", newline="") as handle:
        rows = [
            ExpectedRequest(
                case_id=row["case_id"],
                method=row["method"],
                path=row["path"],
                status=int(row["status"]),
                resource=row["resource"],
            )
            for row in csv.DictReader(handle)
        ]
    selected = [row for row in rows if row.case_id == case_id]
    if not selected:
        raise ValueError("Unknown dataset case")
    return selected


def load_case(case_id: str) -> dict[str, Any]:
    """Find one case without exposing its oracle in the agent's objective."""
    for case in load_dataset()["cases"]:
        if case["id"] == case_id:
            result: dict[str, Any] = case
            return result
    raise ValueError("Unknown dataset case")
