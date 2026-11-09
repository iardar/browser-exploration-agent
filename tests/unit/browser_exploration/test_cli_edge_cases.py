"""Command boundaries, environment precedence, and safe structured diagnostics."""

import io
import json
import logging
import runpy
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from browser_exploration import cli
from browser_exploration.config import Settings
from browser_exploration.contracts import (
    BrowserExplorationResult,
    BrowserExplorationTask,
)
from browser_exploration.explorer import AutonomousBrowserExplorer
from tests.unit.browser_exploration.fakes import FakeConnection, FakeModel


@pytest.fixture(autouse=True)
def isolate_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    logger = logging.getLogger("browser_exploration")
    monkeypatch.setattr(logger, "handlers", [])
    monkeypatch.setattr(logger, "level", logger.level)
    monkeypatch.setattr(logger, "propagate", logger.propagate)


@pytest.mark.parametrize("with_details", [False, True])
def test_logging_emits_one_redacted_json_record(
    monkeypatch: pytest.MonkeyPatch, with_details: bool
) -> None:
    """Verify logging emits one redacted json record.

    Arrange: Use `monkeypatch`, `with_details` parameters.
    Act: Call `io.StringIO` to obtain `stream`.
    Assert: Check `len(records) == 1`; `payload['level'] == 'WARNING'`; `payload['event'] ==
            'Request failed: Bearer [REDACTED]'`; `set(payload) == {'level', 'event'}`.
    """
    stream = io.StringIO()
    monkeypatch.setattr(cli.sys, "stderr", stream)
    logger = logging.getLogger("browser_exploration")
    # Pytest attaches capture handlers at call time, after fixture setup, to
    # non-propagating loggers. Exercise configuration with no existing handler.
    monkeypatch.setattr(logger, "handlers", [])
    cli._logging()
    cli._logging()
    details = (
        {
            "run_id": "run-1",
            "action": "browser_snapshot",
            "error": {"message": "token=hidden-error", "api_key": "hidden-key"},
            "unrelated": "omit-this-field",
        }
        if with_details
        else {}
    )
    logger.warning("Request failed: Bearer hidden-bearer", extra=details)

    records = stream.getvalue().splitlines()
    assert len(records) == 1
    payload = json.loads(records[0])
    assert payload["level"] == "WARNING"
    assert payload["event"] == "Request failed: Bearer [REDACTED]"
    assert "hidden-" not in records[0]
    if with_details:
        assert payload["run_id"] == "run-1"
        assert payload["action"] == "browser_snapshot"
        assert payload["error"] == {
            "message": "token=[REDACTED]",
            "api_key": "[REDACTED]",
        }
        assert "unrelated" not in payload
    else:
        assert set(payload) == {"level", "event"}


@pytest.mark.parametrize("override", [False, True])
def test_environment_defaults_and_explicit_cli_overrides_reach_the_explorer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, override: bool
) -> None:
    """Verify environment defaults and explicit cli overrides reach the explorer.

    Arrange: Use `tmp_path`, `monkeypatch`, `override` parameters; prepare `captured_settings`,
             `captured_tasks`, `connection`, `arguments`.
    Act: Call `CliRunner.invoke` to obtain `response`.
    Assert: Check `response.exit_code == 0`; `len(captured_settings) == len(captured_tasks) == 1`;
            `settings.output_dir == tmp_path`; `connection.entries == connection.exits == 1`.
    """
    captured_settings: list[Settings] = []
    captured_tasks: list[BrowserExplorationTask] = []
    connection = FakeConnection()

    class CapturingExplorer:
        def __init__(self, settings: Settings) -> None:
            captured_settings.append(settings)
            self.delegate = AutonomousBrowserExplorer(
                settings, connection=connection, model=FakeModel()
            )

        async def explore(self, task: BrowserExplorationTask) -> BrowserExplorationResult:
            captured_tasks.append(task)
            return await self.delegate.explore(task)

    for name, value in {
        "BROWSER_OUTPUT_DIR": str(tmp_path),
        "BROWSER_NETWORK_RESTRICTED": "true",
        "BROWSER_RETAIN_RAW_EVIDENCE": "true",
        "BROWSER_MAX_STEPS": "9",
        "BROWSER_MAX_MODEL_ITERATIONS": "7",
        "BROWSER_MAX_DURATION_SECONDS": "60",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(cli, "AutonomousBrowserExplorer", CapturingExplorer)
    arguments = ["run", "--url", "https://example.com/", "--goal", "Explore Help"]
    if override:
        arguments += [
            "--max-steps",
            "3",
            "--max-model-iterations",
            "2",
            "--max-duration-seconds",
            "30",
            "--no-retain-raw-evidence",
            "--allow-mutations",
            "--allow-network-details",
            "--allowed-domain",
            "example.com",
            "--allowed-domain",
            "help.example.com",
            "--context",
            "First context",
            "--context",
            "Second context",
            "--requirement",
            "Help is available without authentication",
        ]
    response = CliRunner().invoke(cli.app, arguments)
    assert response.exit_code == 0, response.output
    assert len(captured_settings) == len(captured_tasks) == 1
    settings, task = captured_settings[0], captured_tasks[0]
    assert settings.output_dir == tmp_path
    assert settings.retain_raw_evidence is not override
    assert (task.max_steps, task.max_model_iterations, task.max_duration_seconds) == (
        (3, 2, 30) if override else (9, 7, 60)
    )
    assert task.allow_mutations is override
    assert task.allow_network_details is override
    assert [item.text for item in task.context_snippets] == (
        ["First context", "Second context"] if override else []
    )
    assert task.allowed_domains == (
        ["example.com", "help.example.com"] if override else ["example.com"]
    )
    assert task.requirement_text == (
        "Help is available without authentication" if override else None
    )
    result_path = Path(json.loads(response.stdout)["result"])
    assert (
        json.loads((result_path.parent / "run_config.json").read_text())["task"][
            "allow_network_details"
        ]
        is override
    )
    assert result_path.parent.parent == tmp_path
    result = BrowserExplorationResult.model_validate_json(result_path.read_text())
    assert result.status == "completed"
    assert all(item.retained is not override for item in result.evidence)
    assert connection.entries == connection.exits == 1


@pytest.mark.parametrize(
    "option,value",
    [
        ("--max-steps", "0"),
        ("--max-steps", "1001"),
        ("--max-model-iterations", "-1"),
        ("--max-model-iterations", "1001"),
        ("--max-duration-seconds", "0"),
        ("--max-duration-seconds", "86401"),
        ("--goal", "   "),
        ("--context", ""),
        ("--allowed-domain", "*.example.com"),
    ],
)
def test_invalid_cli_input_never_constructs_an_explorer_or_creates_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, option: str, value: str
) -> None:
    """Verify invalid cli input never constructs an explorer or creates artifacts.

    Arrange: Use `tmp_path`, `monkeypatch`, `option`, `value` parameters; prepare `arguments`.
    Act: Call `CliRunner.invoke` to obtain `response`.
    Assert: Check `response.exit_code == 2`; `not list(tmp_path.iterdir())`.
    """

    def forbidden_explorer(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Invalid input must be rejected before constructing the explorer")

    monkeypatch.setattr(cli, "AutonomousBrowserExplorer", forbidden_explorer)
    monkeypatch.setenv("BROWSER_OUTPUT_DIR", str(tmp_path))
    arguments = ["run", "--url", "https://example.com/"]
    if option != "--goal":
        arguments += ["--goal", "Explore Help"]
    response = CliRunner().invoke(cli.app, [*arguments, option, value])
    assert response.exit_code == 2, response.output
    assert not list(tmp_path.iterdir())


def test_model_option_overrides_environment_and_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify model option overrides environment and is reported.

    Arrange: Use `tmp_path`, `monkeypatch` parameters; prepare `captured`.
    Act: Call `CliRunner.invoke` to obtain `response`.
    Assert: Check `response.exit_code == 0`; `captured == ['cli-model']`; `result['model'] ==
            'cli-model'`; `config['configured_model'] == 'cli-model'`.
    """
    captured: list[str] = []

    class CapturingExplorer:
        def __init__(self, settings: Settings) -> None:
            captured.append(settings.model)
            self.delegate = AutonomousBrowserExplorer(
                settings, connection=FakeConnection(), model=FakeModel()
            )

        async def explore(self, task: BrowserExplorationTask) -> BrowserExplorationResult:
            return await self.delegate.explore(task)

    monkeypatch.setenv("OPENAI_MODEL", "environment-model")
    monkeypatch.setenv("BROWSER_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("BROWSER_NETWORK_RESTRICTED", "true")
    monkeypatch.setattr(cli, "AutonomousBrowserExplorer", CapturingExplorer)
    response = CliRunner().invoke(
        cli.app,
        ["run", "--url", "https://example.com/", "--goal", "Explore", "--model", "cli-model"],
    )
    assert response.exit_code == 0, response.output
    assert captured == ["cli-model"]
    result = json.loads(response.stdout)
    assert result["model"] == "cli-model"
    config = json.loads(Path(result["result"]).with_name("run_config.json").read_text())
    assert config["configured_model"] == "cli-model"


def test_module_entry_point_prints_help_without_services(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify module entry point prints help without services.

    Arrange: Use `monkeypatch`, `capsys` parameters.
    Act: Call `runpy.run_module` under `pytest.raises(SystemExit)`.
    Assert: Check `raises SystemExit`; `exc.value.code == 0`; `'Independent autonomous browser
            exploration' in capsys.readouterr().out`.
    """
    monkeypatch.setattr(sys, "argv", ["qa-browser", "--help"])
    # The package is already imported by the offline fixture; runpy warns before
    # executing the same public module as Python's -m entry point.
    with (
        pytest.warns(RuntimeWarning, match="found in sys.modules"),
        pytest.raises(SystemExit) as exc,
    ):
        runpy.run_module("browser_exploration.cli", run_name="__main__")
    assert exc.value.code == 0
    assert "Independent autonomous browser exploration" in capsys.readouterr().out
