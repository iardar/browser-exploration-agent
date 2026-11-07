"""Opt-in real-browser isolation and dataset checks; no provider or LangSmith calls."""

import asyncio
import json
import os
from pathlib import Path

import pytest
from evals.network_capture.dataset import load_case, load_dataset
from evals.network_capture.deployment import DEFAULT_CONTAINER, DEFAULT_MCP_URL
from evals.network_capture.grade import grade_run
from evals.network_capture.isolation import TrialIsolation
from tests.unit.browser_exploration.fakes import FakeModel

from browser_exploration.config import Settings
from browser_exploration.contracts import BrowserExplorationTask
from browser_exploration.explorer import AutonomousBrowserExplorer
from browser_exploration.model import Action, Decision, DecisionResponse

pytestmark = [
    pytest.mark.live_mcp,
    pytest.mark.skipif(
        os.environ.get("QA_BROWSER_LIVE_MCP") != "1",
        reason="Set QA_BROWSER_LIVE_MCP=1 and start the dedicated evaluation Compose stack",
    ),
]


async def test_real_context_isolation_and_case_readiness(request: pytest.FixtureRequest) -> None:
    """Probe storage/history, cancel a measured trial, then grade all cases in fresh sessions.

    Arrange: Require serial execution and configure the dedicated fixture, isolation owner,
        dataset cases, and model-free browser connection.
    Act: Probe deployment isolation, cancel a blocked trial, then run and grade each dataset case
        in its own browser session.
    Assert: Preflight succeeds, cancellation cleans up its session, and every case is ready,
        captures the expected requests, passes grading, and cleans up its session.
    """
    assert request.config.getoption("numprocesses", default=0) == 0, "Run serially with -n 0"
    settings = Settings(
        model="scripted-isolation-check",
        mcp_url=DEFAULT_MCP_URL,
        output_dir=Path("runs/isolation-verification"),
        network_restricted=True,
        allow_private_network=True,
        retain_raw_evidence=True,
        langsmith_enabled=False,
    )
    owner = TrialIsolation(settings, "http://127.0.0.1:8766", DEFAULT_CONTAINER)
    dataset = load_dataset()

    def task(case_id: str) -> BrowserExplorationTask:
        return BrowserExplorationTask(
            url=owner.fixture_url + load_case(case_id)["path"],
            objective=dataset["objective"],
            **dataset["task_defaults"],
        )

    async with asyncio.timeout(180):
        await owner.prepare()
        assert owner.verified and not owner.failed
        blocked = FakeModel(blocked=True)
        connection = owner.connection("NET-001", task("NET-001").url)
        work = asyncio.create_task(
            AutonomousBrowserExplorer(settings, connection=connection, model=blocked).explore(
                task("NET-001")
            )
        )
        try:
            async with asyncio.timeout(30):
                await blocked.started.wait()
        finally:
            work.cancel()
            interrupted = await work
        connection.record.update(run_id=interrupted.run_id, result_status=interrupted.status)
        connection.save()
        assert interrupted.status == "cancelled"
        assert connection.record["cleanup"] == "passed" and not owner.failed

        for case in dataset["cases"]:
            trial = task(case["id"])
            connection = owner.connection(case["id"], trial.url)
            model = FakeModel(
                [
                    DecisionResponse(
                        Decision(
                            action=Action(
                                name="browser_network_requests", arguments={"static": True}
                            )
                        )
                    ),
                    DecisionResponse(Decision(done=True)),
                ]
            )
            result = await AutonomousBrowserExplorer(
                settings, connection=connection, model=model
            ).explore(trial)
            connection.record.update(run_id=result.run_id, result_status=result.status)
            report = grade_run(case["id"], settings.output_dir / result.run_id)
            connection.record["grade"] = report
            connection.save()
            assert result.metrics.browser_tool_calls == 3
            assert connection.record["readiness"] == "ready"
            assert connection.record["cleanup"] == "passed"
            assert report["passed"], json.dumps(report)
    print(f"Model-free isolation verification records: {owner.directory}")
