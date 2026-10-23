"""Check model selection and request failures without MCP or a browser session."""

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from browser_exploration import cli
from browser_exploration.config import Settings
from browser_exploration.model import Decision, DecisionResponse, ModelError
from tests.unit.browser_exploration.fakes import FakeModel


@pytest.mark.parametrize("override", [None, "gpt-5.6-luna"])
def test_model_check_reports_selected_and_provider_models_without_a_browser(
    monkeypatch: pytest.MonkeyPatch, override: str | None
) -> None:
    selected: list[str] = []
    fake = FakeModel([DecisionResponse(Decision(done=True), provider_model="provider-model")])

    def model_factory(settings: Settings) -> FakeModel:
        selected.append(settings.model)
        return fake

    def forbidden_browser(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Model check must not initialize a browser")

    monkeypatch.setenv("OPENAI_MODEL", "environment-model")
    monkeypatch.setattr(cli, "OpenAIDecisionModel", model_factory)
    monkeypatch.setattr(cli, "AutonomousBrowserExplorer", forbidden_browser)
    monkeypatch.setattr(cli, "check_connection", forbidden_browser)
    arguments = ["check-model"] + (["--model", override] if override else [])
    result = CliRunner().invoke(cli.app, arguments)
    assert result.exit_code == 0, result.output
    assert selected == [override or "environment-model"]
    data = json.loads(result.stdout)
    assert data["model"] == selected[0]
    assert data["provider_model"] == "provider-model"
    assert len(fake.requests) == 1


def test_model_check_exposes_provider_diagnostics_and_does_not_fall_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected: list[str] = []

    def model_factory(settings: Settings) -> FakeModel:
        selected.append(settings.model)
        return FakeModel(
            [
                ModelError(
                    "llm_api_error",
                    "Selected model unavailable (HTTP 404)",
                    diagnostics={"http_status": 404, "provider_code": "model_not_found"},
                )
            ]
        )

    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1-mini")
    monkeypatch.setattr(cli, "OpenAIDecisionModel", model_factory)
    result = CliRunner().invoke(cli.app, ["check-model", "--model", "gpt-5.6-luna"])
    assert result.exit_code == 1
    assert selected == ["gpt-5.6-luna"]
    data = json.loads(result.stdout)
    assert data["model"] == "gpt-5.6-luna"
    assert data["diagnostics"]["http_status"] == 404
    assert data["diagnostics"]["provider_code"] == "model_not_found"


def test_model_check_deadline_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "OpenAIDecisionModel", lambda _: FakeModel(blocked=True))
    result = CliRunner().invoke(cli.app, ["check-model", "--timeout-seconds", "1"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "llm_timeout"
