"""Replay controlled network traffic without a browser process or socket connection."""

from pathlib import Path
from typing import Any

from evals.network_capture.dataset import load_case, load_dataset, load_requests

from browser_exploration.browser_tools import discovered_tools
from browser_exploration.config import Settings
from browser_exploration.contracts import BrowserExplorationResult, BrowserExplorationTask
from browser_exploration.explorer import AutonomousBrowserExplorer
from browser_exploration.model import DecisionModel
from tests.unit.browser_exploration.fakes import FakeConnection, FakeSession, fake_tool, snapshot

BASE_URL = "https://fixture.example"


def network_listing(case_id: str) -> str:
    """Render an MCP listing from the reviewed request oracle, not a model response."""
    return "### Result\n" + "\n".join(
        f"{index}. [{item.method}] {BASE_URL}{item.path} => [{item.status}]"
        for index, item in enumerate(load_requests(case_id), 1)
    )


def network_task(case_id: str) -> BrowserExplorationTask:
    """Use the dataset's identical objective and fixed limits for every model variant."""
    dataset = load_dataset()
    return BrowserExplorationTask(
        url=BASE_URL + load_case(case_id)["path"],
        objective=dataset["objective"],
        **dataset["task_defaults"],
    )


class NetworkReplaySession(FakeSession):
    """Expose real tool schemas while serving only synthetic, case-specific responses."""

    def __init__(self, case_id: str) -> None:
        super().__init__()
        self.case_id = case_id
        self.url = network_task(case_id).url
        self.tools, self.capabilities = discovered_tools(
            [
                fake_tool("browser_navigate", {"url": {"type": "string"}}),
                fake_tool("browser_snapshot"),
                fake_tool("browser_network_requests", {"static": {"type": "boolean"}}),
            ]
        )

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        """Reject unexpected interactions instead of silently returning an unrelated snapshot."""
        self.calls.append((name, args))
        if name == "browser_navigate" and args.get("url") != self.url:
            raise AssertionError("The network-only task must stay on its fixture page")
        if name in {"browser_navigate", "browser_snapshot"}:
            return snapshot(self.url)
        if name == "browser_network_requests":
            if args.get("static") is not True:
                raise AssertionError("This evaluation requires static-resource capture")
            return {"content": [{"type": "text", "text": network_listing(self.case_id)}]}
        raise AssertionError("Unexpected replay browser action")


async def run_network_replay(
    output_dir: Path,
    case_id: str,
    model: DecisionModel,
    *,
    configured_model: str = "injected-model",
) -> tuple[BrowserExplorationResult, Path]:
    """Run the same graph in offline and live-model tests; browser traffic is always synthetic."""
    result = await AutonomousBrowserExplorer(
        Settings(
            model=configured_model,
            model_api="auto",
            reasoning_effort=None,
            output_dir=output_dir,
            network_restricted=True,  # This replay session cannot open sockets.
            retain_raw_evidence=True,
            langsmith_enabled=False,
        ),
        connection=FakeConnection(NetworkReplaySession(case_id)),
        model=model,
    ).explore(network_task(case_id))
    return result, output_dir / result.run_id
