"""Verify the provider probe without making external model requests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from evals import model_preflight

from browser_exploration.config import Settings
from browser_exploration.model import Action, Decision, DecisionResponse, ModelError


async def test_successful_model_preflight_returns_comparable_provider_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return the exact model configuration and provider usage after a valid probe.

    Arrange: Replace the provider adapter with a successful structured decision fake.
    Act: Validate a Responses API model configured with low reasoning effort.
    Assert: The result records configuration, provider identity and usage from the probe.
    """
    response = DecisionResponse(
        Decision(done=True),
        usage={"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
        provider_model="provider-model-version",
    )
    decide = AsyncMock(return_value=response)
    adapter = Mock(return_value=SimpleNamespace(decide=decide))
    monkeypatch.setattr(model_preflight, "OpenAIDecisionModel", adapter)
    settings = Settings(
        model="configured-model",
        model_api="responses",
        reasoning_effort="low",
    )

    result = await model_preflight.validate_model(settings)

    assert result == {
        "status": "ready",
        "model": "configured-model",
        "model_api": "responses",
        "reasoning_effort": "low",
        "provider_model": "provider-model-version",
        "usage": {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
    }
    adapter.assert_called_once_with(settings)
    decide.assert_awaited_once()
    call = decide.await_args
    assert call is not None
    messages, tools = call.args
    assert tools == []
    assert len(messages) == 1
    assert messages[0].content == "Evaluation preflight. Return done=true with no claims."


@pytest.mark.parametrize("status", [401, 429])
async def test_model_preflight_reports_http_failure_without_provider_details(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    """Sanitize authentication, quota and rate-limit failures before experiment setup.

    Arrange: Make the provider adapter raise a model error containing a private message.
    Act: Run the shared model validation probe.
    Assert: The safe error includes the HTTP status and excludes private provider text.
    """
    decide = AsyncMock(
        side_effect=ModelError(
            "llm_api_error",
            "private provider response",
            diagnostics={"http_status": status},
        )
    )
    monkeypatch.setattr(
        model_preflight,
        "OpenAIDecisionModel",
        Mock(return_value=SimpleNamespace(decide=decide)),
    )

    with pytest.raises(model_preflight.ModelPreflightError) as caught:
        await model_preflight.validate_model(Settings())

    assert f"HTTP {status}" in str(caught.value)
    assert "no LangSmith experiment was started" in str(caught.value)
    assert "private provider response" not in str(caught.value)


async def test_model_preflight_rejects_an_action_instead_of_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require the provider to follow the minimal structured completion instruction.

    Arrange: Return a schema-valid action even though the probe asks the model to finish.
    Act: Validate the provider response.
    Assert: Setup fails with an unexpected-decision error.
    """
    response = DecisionResponse(Decision(action=Action(name="browser_snapshot", arguments={})))
    monkeypatch.setattr(
        model_preflight,
        "OpenAIDecisionModel",
        Mock(return_value=SimpleNamespace(decide=AsyncMock(return_value=response))),
    )

    with pytest.raises(model_preflight.ModelPreflightError, match="unexpected decision"):
        await model_preflight.validate_model(Settings())
