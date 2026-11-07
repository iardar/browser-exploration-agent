"""CLI contracts run independently of any IDE, model service, or browser server."""

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from browser_exploration import cli
from browser_exploration.config import Settings
from browser_exploration.contracts import BrowserExplorationResult, BrowserFinding, VerifiedBehavior
from browser_exploration.explorer import AutonomousBrowserExplorer
from browser_exploration.model import Action, Decision, DecisionResponse
from tests.unit.browser_exploration.fakes import FakeConnection, FakeModel


@pytest.fixture(autouse=True)
def isolate_cli_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "load_dotenv", lambda **kwargs: False)
    monkeypatch.setattr(logging.getLogger("browser_exploration"), "handlers", [])
    for name in (
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "PLAYWRIGHT_MCP_URL",
        "BROWSER_MCP_URL",
        "BROWSER_NETWORK_RESTRICTED",
        "BROWSER_RETAIN_RAW_EVIDENCE",
        "BROWSER_MAX_STEPS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_check_does_not_require_or_construct_a_model(monkeypatch: pytest.MonkeyPatch) -> None:
    async def connected(settings: Settings) -> dict[str, Any]:
        assert settings.mcp_url == "http://127.0.0.1:8931/mcp"
        return {"status": "connected", "tools": ["browser_navigate", "browser_snapshot"]}

    def forbidden_model(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Connection check must not construct an explorer or model")

    monkeypatch.setattr(cli, "check_connection", connected)
    monkeypatch.setattr(cli, "AutonomousBrowserExplorer", forbidden_model)
    response = CliRunner().invoke(cli.app, ["check"])
    assert response.exit_code == 0
    assert json.loads(response.stdout)["status"] == "connected"


def test_check_failure_exits_nonzero_with_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unavailable(settings: Settings) -> dict[str, Any]:
        return {"status": "failed", "error": {"code": "mcp_unavailable", "message": "Offline"}}

    monkeypatch.setattr(cli, "check_connection", unavailable)
    response = CliRunner().invoke(cli.app, ["check"])
    assert response.exit_code == 1
    assert json.loads(response.stdout)["error"]["code"] == "mcp_unavailable"


def test_invalid_configuration_never_echoes_rejected_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLAYWRIGHT_MCP_URL", "http://user:private-password@localhost:8931/mcp")
    response = CliRunner().invoke(cli.app, ["check"])
    assert response.exit_code == 2
    assert "invalid_configuration" in response.output
    assert "private-password" not in response.output


def test_run_executes_real_graph_and_outputs_report_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = FakeConnection()
    behavior = VerifiedBehavior(
        description="The page contains a “Help” link pointing to /help.",
        evidence_ids=["evidence-obs-0002"],
    )
    model = FakeModel([DecisionResponse(Decision(done=True, verified_behaviors=[behavior]))])

    def offline_explorer(settings: Settings) -> AutonomousBrowserExplorer:
        return AutonomousBrowserExplorer(settings, connection=connection, model=model)

    monkeypatch.setenv("BROWSER_NETWORK_RESTRICTED", "true")
    monkeypatch.setattr(cli, "AutonomousBrowserExplorer", offline_explorer)
    response = CliRunner().invoke(
        cli.app,
        [
            "run",
            "--url",
            "https://example.com/",
            "--goal",
            "MOCK: Explore Help",
            "--allowed-domain",
            "example.com",
            "--max-steps",
            "5",
            "--max-model-iterations",
            "2",
            "--max-duration-seconds",
            "10",
            "--requirement",
            "Help opens without signing in",
            "--context",
            "Synthetic test data",
            "--output-dir",
            str(tmp_path),
            "--retain-raw-evidence",
        ],
    )
    assert response.exit_code == 0, response.output
    output = json.loads(response.stdout)
    result = BrowserExplorationResult.model_validate_json(Path(output["result"]).read_text())
    assert result.status == "completed"
    assert result.objective == "MOCK: Explore Help"
    assert result.metrics.browser_tool_calls == 2
    assert output["verified_behaviors"] == [behavior.model_dump(mode="json")]
    assert output["verified_behaviors"] == result.model_dump(mode="json")["verified_behaviors"]
    assert "“Help”" in response.stdout
    assert output["findings"] == output["unresolved_questions"] == []
    assert all(evidence.retained for evidence in result.evidence)
    assert connection.entries == connection.exits == 1


def test_run_with_missing_safety_boundary_persists_blocked_result(tmp_path: Path) -> None:
    response = CliRunner().invoke(
        cli.app,
        [
            "run",
            "--url",
            "https://example.com",
            "--goal",
            "Explore",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert response.exit_code == 1
    output = json.loads(response.stdout)
    assert output["status"] == "blocked"
    assert (
        output["verified_behaviors"] == output["findings"] == output["unresolved_questions"] == []
    )
    assert output["errors"][0]["code"] == "safety_policy"
    assert Path(output["result"]).is_file()


def test_completed_run_displays_policy_rejection_and_unresolved_goal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    limitation = "The login page could not be inspected because navigation was denied."
    model = FakeModel(
        [
            DecisionResponse(
                Decision(
                    action=Action(
                        name="browser_navigate", arguments={"url": "https://example.com/login"}
                    )
                )
            ),
            DecisionResponse(Decision(done=True, unresolved_questions=[limitation])),
        ]
    )
    connection = FakeConnection()
    monkeypatch.setenv("BROWSER_NETWORK_RESTRICTED", "true")
    monkeypatch.setattr(
        cli,
        "AutonomousBrowserExplorer",
        lambda settings: AutonomousBrowserExplorer(settings, connection=connection, model=model),
    )
    response = CliRunner().invoke(
        cli.app,
        [
            "run",
            "--url",
            "https://example.com/",
            "--goal",
            "Inspect login",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert response.exit_code == 0, response.output
    output = json.loads(response.stdout)
    saved = json.loads(Path(output["result"]).read_text())
    assert output["status"] == "completed" and output["errors"] == []
    assert output["unresolved_questions"] == saved["unresolved_questions"]
    assert len(output["unresolved_questions"]) == 2
    assert "Known authentication" in output["unresolved_questions"][0]
    assert output["unresolved_questions"][1] == limitation
    assert saved["metrics"]["browser_tool_calls"] == 2


@pytest.mark.parametrize("finish", [True, False])
def test_findings_and_limitations_preserve_details_and_redaction_in_terminal_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, finish: bool
) -> None:
    finding = BrowserFinding(
        id="finding-1",
        title="Potential Help content issue",
        description="Diagnostic text contains token=private-finding-value",
        expected_behavior="Help content should be available.",
        observed_behavior="The snapshot shows a Help link; its destination needs inspection.",
        reproduction_steps=["Open the starting page", "Inspect its navigation"],
        evidence_ids=["evidence-obs-0002"],
        verification_status="potential",
        severity="low",
        confidence=0.5,
    )
    model = FakeModel(
        [
            DecisionResponse(
                Decision(
                    done=finish,
                    action=None
                    if finish
                    else Action(
                        name="browser_navigate", arguments={"url": "https://example.com/help"}
                    ),
                    findings=[finding],
                    unresolved_questions=["Diagnostic token=private-question-value needs review."],
                )
            )
        ]
    )
    monkeypatch.setenv("BROWSER_NETWORK_RESTRICTED", "true")
    monkeypatch.setattr(
        cli,
        "AutonomousBrowserExplorer",
        lambda settings: AutonomousBrowserExplorer(
            settings, connection=FakeConnection(), model=model
        ),
    )
    response = CliRunner().invoke(
        cli.app,
        [
            "run",
            "--url",
            "https://example.com/",
            "--goal",
            "Inspect Help",
            "--max-model-iterations",
            "1",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert response.exit_code == (0 if finish else 1), response.output
    output = json.loads(response.stdout)
    saved = json.loads(Path(output["result"]).read_text())
    assert output["status"] == ("completed" if finish else "model_limit")
    assert output["findings"] == saved["findings"]
    assert output["unresolved_questions"] == saved["unresolved_questions"]
    expected = finding.model_dump(mode="json")
    expected["description"] = "Diagnostic text contains token=[REDACTED]"
    assert output["findings"] == [expected]
    assert output["unresolved_questions"] == ["Diagnostic token=[REDACTED] needs review."]
    assert "private-finding-value" not in response.stdout
    assert "private-question-value" not in response.stdout


def test_invalid_task_is_rejected_before_browser_execution(tmp_path: Path) -> None:
    response = CliRunner().invoke(
        cli.app,
        [
            "run",
            "--url",
            "file:///private-data",
            "--goal",
            "Explore",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert response.exit_code == 2
    assert "invalid_task" in response.output
    assert not list(tmp_path.iterdir())


def test_schema_export_needs_no_credentials(tmp_path: Path) -> None:
    response = CliRunner().invoke(cli.app, ["schemas", "--output-dir", str(tmp_path)])
    assert response.exit_code == 0
    schema = json.loads((tmp_path / "BrowserExplorationResult.schema.json").read_text())
    assert schema["properties"]["schema_version"]["const"] == "1.0"
