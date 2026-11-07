"""Explicit reasoning settings reach the SDK without changing the requested model."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import httpx2
import pytest
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from openai import APIStatusError
from pydantic import ValidationError
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
    OpenAIDecisionModel,
    provider_error,
)
from tests.unit.browser_exploration.fakes import FakeConnection, FakeModel


@pytest.mark.parametrize(
    "model,expected",
    [
        ("gpt-5.6-sol", "medium"),
        ("gpt-5.6-luna", "medium"),
        ("gpt-5.6", "medium"),
        ("gpt-5.6-sol-2026-07-07", "medium"),
        ("gpt-4.1-mini", None),
        ("gpt-5.6-chat-latest", None),
        ("custom-model", None),
    ],
)
def test_only_known_models_get_an_explicit_default(model: str, expected: str | None) -> None:
    """Verify only known models get an explicit default.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for only known models get an explicit default.
    """
    settings = Settings(model=model)
    assert settings.model == model
    assert settings.reasoning_effort is None
    assert settings.effective_reasoning_effort == expected


@pytest.mark.parametrize("effort", ["none", "low", "medium", "high", "xhigh", "max"])
def test_explicit_effort_overrides_the_model_default(effort: str) -> None:
    """Verify explicit effort overrides the model default.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for explicit effort overrides the model default.
    """
    settings = Settings.model_validate({"model": "gpt-5.6-sol", "reasoning_effort": effort})
    assert settings.effective_reasoning_effort == effort


def test_invalid_reasoning_configuration_is_rejected_without_leaking_input() -> None:
    """Verify invalid reasoning configuration is rejected without leaking input.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for invalid reasoning configuration is rejected without
            leaking input."""
    with pytest.raises(ValidationError):
        Settings.model_validate({"reasoning_effort": "unsupported"})
    result = CliRunner().invoke(
        cli.app, ["check-model", "--reasoning-effort", "private-invalid-value"]
    )
    assert result.exit_code == 2
    assert "private-invalid-value" not in result.output
    assert json.loads(result.stderr)["fields"] == ["reasoning_effort"]


@pytest.mark.parametrize("override", [None, "none"])
def test_model_check_honors_effort_environment_and_cli(
    monkeypatch: pytest.MonkeyPatch, override: str | None
) -> None:
    """Verify model check honors effort environment and cli.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for model check honors effort environment and cli.
    """
    selected: list[Settings] = []

    def model_factory(settings: Settings) -> FakeModel:
        selected.append(settings)
        return FakeModel([DecisionResponse(Decision(done=True))])

    monkeypatch.setenv("BROWSER_REASONING_EFFORT", "high")
    monkeypatch.setattr(cli, "OpenAIDecisionModel", model_factory)
    arguments = ["check-model", "--model", "gpt-5.6-sol"]
    if override is not None:
        arguments += ["--reasoning-effort", override]
    result = CliRunner().invoke(cli.app, arguments)
    assert result.exit_code == 0, result.output
    assert selected[0].effective_reasoning_effort == (override or "high")
    assert json.loads(result.stdout)["reasoning_effort"] == (override or "high")


@pytest.mark.parametrize("override", [None, "low"])
def test_exploration_logs_and_persists_effective_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, override: str | None
) -> None:
    """Verify exploration logs and persists effective effort.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for exploration logs and persists effective effort.
    """

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
    arguments = [
        "run",
        "--url",
        "https://example.com/",
        "--goal",
        "Explore",
        "--model",
        "gpt-5.6-sol",
    ]
    if override is not None:
        arguments += ["--reasoning-effort", override]
    result = CliRunner().invoke(cli.app, arguments)
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["reasoning_effort"] == (override or "medium")
    config = json.loads(Path(data["result"]).with_name("run_config.json").read_text())
    assert config["reasoning_effort"] == (override or "medium")
    assert config["configured_model"] == "gpt-5.6-sol"


@pytest.mark.parametrize(
    "model,effort,expected",
    [
        ("gpt-5.6-sol", None, "medium"),
        ("gpt-5.6-luna", None, "medium"),
        ("gpt-5.6-sol", "low", "low"),
        ("gpt-5.6-sol", "none", "none"),
        ("gpt-4.1-mini", None, None),
    ],
)
async def test_actual_sdk_request_contains_the_configured_effort(
    model: str, effort: str | None, expected: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify actual sdk request contains the configured effort.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for actual sdk request contains the configured effort.
    """
    captured: list[dict[str, Any]] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        captured.append(data)
        if settings.effective_model_api == "responses":
            assert request.url.path == "/v1/responses"
            assert "reasoning_effort" not in data
            assert "messages" not in data
            assert data["reasoning"] == {"effort": expected}
            assert data["store"] is False
            assert data["tool_choice"] == {"type": "function", "name": "Decision"}
            assert data["tools"][0]["strict"] is False
            assert data["tools"][0]["parameters"]["additionalProperties"] is False
            return httpx.Response(
                200,
                json={
                    "id": "resp_fake",
                    "object": "response",
                    "created_at": 0,
                    "status": "completed",
                    "model": model,
                    "error": None,
                    "output": [
                        {"id": "rs_fake", "type": "reasoning", "summary": []},
                        {
                            "id": "fc_fake",
                            "type": "function_call",
                            "call_id": "call_fake",
                            "name": "Decision",
                            "arguments": '{"done":true}',
                            "status": "completed",
                        },
                    ],
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "total_tokens": 15,
                        "input_tokens_details": {"cached_tokens": 0},
                        "output_tokens_details": {"reasoning_tokens": 1},
                    },
                },
            )
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(
            200,
            json={
                "id": "fake-completion",
                "object": "chat.completion",
                "created": 0,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "fake-call",
                                    "type": "function",
                                    "function": {"name": "Decision", "arguments": '{"done":true}'},
                                }
                            ],
                        },
                    }
                ],
            },
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    settings = Settings.model_validate({"model": model, "reasoning_effort": effort})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with patch(
            "browser_exploration.model.ChatOpenAI",
            side_effect=lambda **kwargs: ChatOpenAI(**kwargs, http_async_client=client),
        ):
            adapter = OpenAIDecisionModel(settings)
        result = await adapter.decide([HumanMessage("Return done=true")], [])
    assert result.decision.done
    assert len(captured) == 1
    assert captured[0]["model"] == model
    if settings.effective_model_api == "responses":
        assert result.provider_model == model
        assert result.usage == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    elif expected is None:
        assert "reasoning_effort" not in captured[0]
    else:
        assert captured[0]["reasoning_effort"] == expected


def test_reasoning_error_is_actionable_without_the_provider_body() -> None:
    """Verify reasoning error is actionable without the provider body.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for reasoning error is actionable without the provider
            body."""
    error = APIStatusError(
        "private-provider-body",
        response=httpx2.Response(400, request=httpx2.Request("POST", "https://example.com/")),
        body={"code": "missing_required_parameter", "param": "reasoning_effort"},
    )
    sanitized = provider_error(error)
    assert sanitized.diagnostics["provider_code"] == "missing_required_parameter"
    assert sanitized.diagnostics["parameter"] == "reasoning_effort"
    assert "check API compatibility" in str(sanitized)
    assert "try medium" not in str(sanitized)
    assert "private-provider-body" not in str(sanitized)
