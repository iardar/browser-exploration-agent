"""API routing, overrides, and single-request failures with fake HTTP transports."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from langchain_openai import ChatOpenAI
from typer.testing import CliRunner

from browser_exploration import cli
from browser_exploration.config import Settings
from browser_exploration.contracts import (
    BrowserExplorationResult,
    BrowserExplorationTask,
)
from browser_exploration.explorer import AutonomousBrowserExplorer
from browser_exploration.model import (
    Decision,
    DecisionResponse,
    ModelError,
    OpenAIDecisionModel,
)
from tests.unit.browser_exploration.fakes import FakeConnection, FakeModel


@pytest.mark.parametrize(
    "model,api",
    [
        ("gpt-5.6-sol", "responses"),
        ("gpt-5.6-luna", "responses"),
        ("gpt-5.6", "responses"),
        ("gpt-5.6-sol-2026-07-07", "responses"),
        ("gpt-4.1-mini", "chat_completions"),
        ("custom-model", "chat_completions"),
    ],
)
def test_model_defaults_to_the_expected_api_without_renaming_it(model: str, api: str) -> None:
    settings = Settings(model=model)
    assert settings.model_api == "auto"
    assert settings.effective_model_api == api
    assert settings.model == model


@pytest.mark.parametrize("api", ["responses", "chat_completions"])
def test_explicit_api_overrides_auto_selection(api: str) -> None:
    settings = Settings.model_validate({"model": "gpt-5.6-sol", "model_api": api})
    assert settings.effective_model_api == api


@pytest.mark.parametrize("override", [None, "responses"])
def test_model_check_uses_environment_or_cli_api(
    monkeypatch: pytest.MonkeyPatch, override: str | None
) -> None:
    selected: list[Settings] = []

    def factory(settings: Settings) -> FakeModel:
        selected.append(settings)
        return FakeModel([DecisionResponse(Decision(done=True))])

    monkeypatch.setenv("BROWSER_MODEL_API", "chat_completions")
    monkeypatch.setattr(cli, "OpenAIDecisionModel", factory)
    arguments = ["check-model", "--model", "gpt-5.6-sol"]
    if override is not None:
        arguments += ["--model-api", override]
    result = CliRunner().invoke(cli.app, arguments)
    assert result.exit_code == 0, result.output
    expected = override or "chat_completions"
    assert selected[0].effective_model_api == expected
    assert json.loads(result.stdout)["model_api"] == expected


def test_invalid_api_does_not_leak_rejected_values() -> None:
    result = CliRunner().invoke(cli.app, ["check-model", "--model-api", "private-value"])
    assert result.exit_code == 2
    assert "private-value" not in result.output
    assert json.loads(result.stderr)["fields"] == ["model_api"]


def test_run_reports_and_persists_the_explicit_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class CapturingExplorer:
        def __init__(self, settings: Settings) -> None:
            self.delegate = AutonomousBrowserExplorer(
                settings, connection=FakeConnection(), model=FakeModel()
            )

        async def explore(self, task: BrowserExplorationTask) -> BrowserExplorationResult:
            return await self.delegate.explore(task)

    monkeypatch.setenv("BROWSER_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("BROWSER_NETWORK_RESTRICTED", "true")
    monkeypatch.setattr(cli, "AutonomousBrowserExplorer", CapturingExplorer)
    result = CliRunner().invoke(
        cli.app,
        [
            "run",
            "--url",
            "https://example.com/",
            "--goal",
            "Explore",
            "--model",
            "gpt-5.6-sol",
            "--model-api",
            "responses",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["model_api"] == "responses"
    config = json.loads(Path(data["result"]).with_name("run_config.json").read_text())
    assert config["model_api"] == "responses"
    assert config["reasoning_effort"] == "medium"
    assert config["configured_model"] == "gpt-5.6-sol"


@pytest.mark.parametrize("api", ["chat_completions", "responses"])
async def test_provider_rejection_records_api_and_never_retries_or_switches_model(
    api: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[tuple[str, dict[str, Any]]] = []

    async def reject(request: httpx.Request) -> httpx.Response:
        captured.append((request.url.path, json.loads(request.content)))
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "private-provider-message",
                    "type": "invalid_request_error",
                    "param": "reasoning_effort"
                    if api == "chat_completions"
                    else "reasoning.effort",
                    "code": "unsupported_value",
                }
            },
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    settings = Settings.model_validate({"model": "gpt-5.6-sol", "model_api": api})
    async with httpx.AsyncClient(transport=httpx.MockTransport(reject)) as client:
        with patch(
            "browser_exploration.model.ChatOpenAI",
            side_effect=lambda **kwargs: ChatOpenAI(**kwargs, http_async_client=client),
        ):
            adapter = OpenAIDecisionModel(settings)
        with pytest.raises(ModelError) as failure:
            await adapter.decide([], [])
    assert len(captured) == 1
    path, payload = captured[0]
    assert path == ("/v1/responses" if api == "responses" else "/v1/chat/completions")
    assert payload["model"] == "gpt-5.6-sol"
    assert failure.value.diagnostics["http_status"] == 400
    assert failure.value.diagnostics["model_api"] == api
    assert failure.value.diagnostics["reasoning_effort"] == "medium"
    assert "private-provider-message" not in str(failure.value)
    assert "try medium" not in str(failure.value)
    if api == "chat_completions":
        assert "--model-api responses" in str(failure.value)
