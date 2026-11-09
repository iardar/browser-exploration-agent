"""Replay the reviewed request-detail oracle through the production graph without sockets."""

from pathlib import Path
from typing import Any

from evals.network_request_details.dataset import (
    case_path,
    load_case,
    load_dataset,
    load_oracle,
    objective,
    target_path,
)

from browser_exploration.browser_tools import discovered_tools
from browser_exploration.config import Settings
from browser_exploration.contracts import BrowserExplorationResult, BrowserExplorationTask
from browser_exploration.explorer import AutonomousBrowserExplorer
from browser_exploration.model import DecisionModel
from tests.unit.browser_exploration.fakes import FakeConnection, FakeSession, fake_tool

BASE_URL = "https://fixture.example"


def detail_snapshot(case_id: str, url: str) -> list[dict[str, str]]:
    """Expose the same readiness text expected from the controlled fixture page."""
    return [
        {
            "type": "text",
            "text": (
                f"### Page\n- Page URL: {url}\n"
                f"- Page Title: Network capture fixture {case_id}\n"
                f'### Snapshot\n- heading "Network fixture ready"'
            ),
        }
    ]


def detail_task(case_id: str) -> BrowserExplorationTask:
    """Build a fixed, detail-authorized task without putting expected values in the prompt."""
    return BrowserExplorationTask(
        url=BASE_URL + case_path(case_id),
        objective=objective(case_id),
        allow_network_details=True,
        **load_dataset()["task_defaults"],
    )


def target_index(case_id: str) -> int:
    """Return the index after the page, static decoy and any ambiguous API requests."""
    return 3 + len(load_oracle()[case_id].get("extra_requests", []))


def network_listing(case_id: str) -> str:
    """Render requests in the same deterministic discovery order as the real fixture."""
    case = load_case(case_id)
    oracle = load_oracle()[case_id]
    root = case_path(case_id)
    lines = [
        "### Result",
        f"1. [GET] {BASE_URL}{root} => [200] OK",
        f"2. [GET] {BASE_URL}{root}decoy.js => [200] OK",
    ]
    lines.extend(
        f"{index}. [{extra['method']}] {BASE_URL}{root}{extra['path']} => [{extra['status']}]"
        for index, extra in enumerate(oracle.get("extra_requests", []), 3)
    )
    lines.append(
        f"{target_index(case_id)}. [{case['method']}] "
        f"{BASE_URL}{target_path(case_id)} => [{oracle['status']}]"
    )
    return "\n".join(lines)


def detail_overview(case_id: str, index: int | None = None) -> str:
    """Render the advertised MCP overview, including body MIME and response Content-Type."""
    case = load_case(case_id)
    oracle = load_oracle()[case_id]
    selected = index if index is not None else target_index(case_id)
    if selected == target_index(case_id):
        spec = oracle
        method = case["method"]
        path = target_path(case_id)
    else:
        spec = oracle["extra_requests"][selected - 3]
        method = spec["method"]
        path = case_path(case_id) + spec["path"]
    content_type = spec["content_type"]
    request_headers = "".join(
        f"    {name}: {value}\n" for name, value in spec.get("request_headers", {}).items()
    )
    response_headers = "".join(
        f"    {name}: {value}\n" for name, value in spec.get("response_headers", {}).items()
    )
    return (
        f"### Result\n#{selected} [{method}] {BASE_URL}{path}\n\n"
        f"  General\n    status: [{spec['status']}]\n"
        f"    mimeType:  {content_type.split(';', 1)[0]}\n"
        f"  Request headers\n{request_headers}"
        f"  Response headers\n    content-type: {content_type}\n{response_headers}"
    )


def detail_part(case_id: str) -> str:
    """Return the part-specific value that a real MCP tool would expose."""
    case = load_case(case_id)
    oracle = load_oracle()[case_id]
    part = case["part"]
    if part == "request-headers":
        return "\n".join(f"{name}: {value}" for name, value in oracle["request_headers"].items())
    if part == "response-headers":
        return "\n".join(f"{name}: {value}" for name, value in oracle["response_headers"].items())
    if part == "request-body":
        return str(oracle["request_body"])
    return str(oracle.get("response_body", "")) + "z" * int(oracle.get("repeat", 0))


class DetailReplaySession(FakeSession):
    """Expose compatible tool schemas and return only reviewed fixture observations."""

    def __init__(self, case_id: str) -> None:
        super().__init__()
        self.case_id = case_id
        detail = fake_tool(
            "browser_network_request",
            {
                "index": {"type": "integer"},
                "part": {
                    "type": "string",
                    "enum": [
                        "request-headers",
                        "request-body",
                        "response-headers",
                        "response-body",
                    ],
                },
                "filename": {"type": "string"},
            },
        )
        assert isinstance(detail.args_schema, dict)
        detail.args_schema["required"] = ["index"]
        self.tools, self.capabilities = discovered_tools(
            [
                *self.tools.values(),
                fake_tool("browser_network_requests", {"static": {"type": "boolean"}}),
                detail,
            ]
        )

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        """Assert exact target selection so malformed scripted trials fail immediately."""
        self.calls.append((name, args))
        if name == "browser_navigate":
            self.url = args["url"]
            return detail_snapshot(self.case_id, self.url)
        if name == "browser_snapshot":
            return detail_snapshot(self.case_id, self.url)
        if name == "browser_network_requests":
            return {"content": [{"type": "text", "text": network_listing(self.case_id)}]}
        if name == "browser_network_request":
            index = args.get("index")
            if not isinstance(index, int) or not 3 <= index <= target_index(self.case_id):
                raise AssertionError("Request-detail replay received an unsupported selection")
            if "filename" in args:
                raise AssertionError("Request-detail replay cannot write a server file")
            if args.get("part") is None:
                value = detail_overview(self.case_id, index)
            else:
                if index != target_index(self.case_id):
                    raise AssertionError("Request-detail replay cannot inspect a decoy body")
                if args["part"] != load_case(self.case_id)["part"]:
                    raise AssertionError("Request-detail replay received a different part")
                value = detail_part(self.case_id)
            return {"content": [{"type": "text", "text": value}]}
        raise AssertionError(f"Unexpected replay browser action: {name}")


async def run_detail_replay(
    output_dir: Path, case_id: str, model: DecisionModel
) -> tuple[BrowserExplorationResult, Path]:
    """Run the real graph with a scripted model and synthetic request-detail MCP session."""
    result = await AutonomousBrowserExplorer(
        Settings(
            output_dir=output_dir,
            network_restricted=True,
            retain_raw_evidence=True,
            langsmith_enabled=False,
        ),
        connection=FakeConnection(DetailReplaySession(case_id)),
        model=model,
    ).explore(detail_task(case_id))
    return result, output_dir / result.run_id
