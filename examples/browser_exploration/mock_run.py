"""Exercise the real graph using synthetic browser and model responses.

Run with: uv run python examples/browser_exploration/mock_run.py
This is an offline plumbing demonstration, not evidence about a real website.
"""

import argparse
import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage
from langchain_core.tools import BaseTool, StructuredTool

from browser_exploration.browser_tools import BrowserSession, discovered_tools
from browser_exploration.config import Settings
from browser_exploration.contracts import BrowserExplorationTask, VerifiedBehavior
from browser_exploration.explorer import AutonomousBrowserExplorer
from browser_exploration.model import Action, Decision, DecisionResponse


class MockBrowser:
    """No requests or browser execution; return explicitly synthetic snapshots."""

    def __init__(self) -> None:
        tools: list[BaseTool] = []
        for name, properties in (
            ("browser_navigate", {"url": {"type": "string"}}),
            ("browser_snapshot", {}),
            ("browser_click", {"ref": {"type": "string"}}),
        ):
            tools.append(
                StructuredTool.from_function(
                    func=lambda **kwargs: None,
                    name=name,
                    description="MOCK tool; no actual browser execution",
                    args_schema={"type": "object", "properties": properties},
                )
            )
        self.tools, self.capabilities = discovered_tools(tools)
        self.url = "https://example.com/"

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        if name == "browser_navigate":
            self.url = args["url"]
        return [
            {
                "type": "text",
                "text": (
                    f"### Page\n- Page URL: {self.url}\n### Snapshot\n"
                    '- heading "MOCK demonstration; no actual website" [ref=e0]\n'
                    '- link "MOCK Help" [ref=e1]:\n  - /url: /help\n'
                ),
            }
        ]


class MockConnection:
    @asynccontextmanager
    async def connect(self) -> AsyncIterator[BrowserSession]:
        yield MockBrowser()


class MockModel:
    """Two deterministic decisions stand in for the LLM, without fake token usage."""

    def __init__(self) -> None:
        self.decisions = 0

    async def decide(
        self, messages: list[BaseMessage], tools: list[dict[str, Any]]
    ) -> DecisionResponse:
        self.decisions += 1
        if self.decisions == 1:
            return DecisionResponse(
                Decision(
                    action=Action(name="browser_click", arguments={"ref": "e1"}),
                )
            )
        return DecisionResponse(
            Decision(
                done=True,
                verified_behaviors=[
                    VerifiedBehavior(
                        description="MOCK ONLY: the synthetic Help response was observed",
                        evidence_ids=["evidence-obs-0003"],
                    )
                ],
                unresolved_questions=[
                    "No live browser or LLM behavior was tested by this mock run."
                ],
            )
        )


async def demonstrate(output_dir: Path, max_duration_seconds: int = 10) -> None:
    # This attestation applies only to the in-process fake, which has no network access.
    explorer = AutonomousBrowserExplorer(
        Settings(output_dir=output_dir, network_restricted=True),
        connection=MockConnection(),
        model=MockModel(),
    )
    result = await explorer.explore(
        BrowserExplorationTask(
            url="https://example.com/",
            objective="MOCK ONLY: validate graph, evidence, termination and JSON reports",
            max_steps=5,
            max_model_iterations=3,
            max_duration_seconds=max_duration_seconds,
        )
    )
    print(
        json.dumps(
            {
                "demonstration": "offline mock; no real website or model was contacted",
                "status": result.status,
                "result": str(output_dir / result.run_id / "result.json"),
                "metrics": result.metrics.model_dump(),
            },
            indent=2,
        )
    )
    if result.status != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("runs/mock"))
    parser.add_argument(
        "--max-duration-seconds",
        type=int,
        default=10,
        help="Run duration budget; increase when stepping through the debugger",
    )
    args = parser.parse_args()
    asyncio.run(demonstrate(args.output_dir, args.max_duration_seconds))
