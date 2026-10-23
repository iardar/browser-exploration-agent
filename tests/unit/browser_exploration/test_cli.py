"""CLI contracts run independently of any IDE, model service, or browser server."""

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from browser_exploration import cli
from browser_exploration.config import Settings
from browser_exploration.contracts import BrowserExplorationResult
from browser_exploration.explorer import AutonomousBrowserExplorer
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

    def offline_explorer(settings: Settings) -> AutonomousBrowserExplorer:
        return AutonomousBrowserExplorer(settings, connection=connection, model=FakeModel())

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
    assert Path(output["result"]).is_file()


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
