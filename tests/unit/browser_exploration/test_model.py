"""Model contracts and provider usage parsing require no network or API key."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2 as httpx
import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from openai import APIConnectionError, APIStatusError
from pydantic import ValidationError

from browser_exploration.config import Settings
from browser_exploration.model import Action, Decision, ModelError, OpenAIDecisionModel


@pytest.mark.parametrize(
    "payload", [{}, {"done": True, "action": {"name": "browser_snapshot"}}, {"done": False}]
)
def test_decision_requires_exactly_one_action_or_completion(payload: dict[str, Any]) -> None:
    """Verify decision requires exactly one action or completion.

    Arrange: Use `payload` parameter.
    Act: Call `Decision.model_validate` under `pytest.raises(ValidationError)`.
    Assert: Check `raises ValidationError`.
    """
    with pytest.raises(ValidationError, match="Choose one action"):
        Decision.model_validate(payload)


def test_decision_rejects_unknown_fields_and_large_arguments() -> None:
    """Verify decision rejects unknown fields and large arguments.

    Arrange: Use the values passed to `pytest.raises`.
    Act: Call `Decision.model_validate` under `pytest.raises(ValidationError)`.
    Assert: Check `raises ValidationError`; `Decision(done=True).action is None`.
    """
    with pytest.raises(ValidationError):
        Decision.model_validate({"done": True, "new_max_steps": 1000})
    with pytest.raises(ValidationError, match="8192"):
        Action(name="browser_click", arguments={"element": "a" * 9000})
    with pytest.raises(ValidationError, match="JSON-compatible"):
        Action(name="browser_click", arguments={"element": object()})
    assert Decision(action=Action(name="browser_snapshot")).done is False
    assert Decision(done=True).action is None


def make_adapter(
    response: Any = None, error: BaseException | None = None
) -> tuple[OpenAIDecisionModel, AsyncMock, MagicMock]:
    invoke = AsyncMock(return_value=response, side_effect=error)
    chat = MagicMock()
    chat.return_value.with_structured_output.return_value.ainvoke = invoke
    with patch("browser_exploration.model.ChatOpenAI", chat):
        adapter = OpenAIDecisionModel(Settings(model="configured-model"))
    return adapter, invoke, chat


async def test_provider_usage_and_tools_are_forwarded_without_model_history_growth() -> None:
    """Verify provider usage and tools are forwarded without model history growth.

    Arrange: prepare `raw`, `adapter, invoke, chat`, `messages`, `tools`.
    Act: Await `adapter.decide` to obtain `response`.
    Assert: Check `response.usage == {'input_tokens': 123, 'output_tokens': 45, 'total_tokens':
            168}`; `response.decision.done is True`; `len(messages) == 1`; `'browser_snapshot' in
            request[1].content`.
    """
    raw = AIMessage(
        content="",
        usage_metadata={"input_tokens": 123, "output_tokens": 45, "total_tokens": 168},
    )
    adapter, invoke, chat = make_adapter(
        {"raw": raw, "parsed": Decision(done=True), "parsing_error": None}
    )
    messages: list[BaseMessage] = [HumanMessage(content="Explore")]
    tools = [{"name": "browser_snapshot", "args_schema": {"type": "object"}}]
    response = await adapter.decide(messages, tools)
    assert response.usage == {"input_tokens": 123, "output_tokens": 45, "total_tokens": 168}
    assert response.decision.done is True
    assert len(messages) == 1
    request = invoke.call_args.args[0]
    assert len(request) == 2
    assert "browser_snapshot" in request[1].content
    chat.assert_called_once_with(model="configured-model", max_retries=0, use_responses_api=False)
    chat.return_value.with_structured_output.assert_called_once_with(
        Decision, method="function_calling", strict=False, include_raw=True
    )


async def test_validation_failure_preserves_reported_usage() -> None:
    """Verify validation failure preserves reported usage.

    Arrange: prepare `adapter, _, _`.
    Act: Call `adapter.decide` under `pytest.raises(ModelError)`.
    Assert: Check `raises ModelError`; `failure.value.code == 'llm_validation_error'`;
            `failure.value.usage == {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}`;
            `'secret' not in str(failure.value)`.
    """
    adapter, _, _ = make_adapter(
        {
            "raw": AIMessage(
                content="invalid",
                usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            ),
            "parsed": None,
            "parsing_error": ValueError("invalid secret payload"),
        }
    )
    with pytest.raises(ModelError) as failure:
        await adapter.decide([], [])
    assert failure.value.code == "llm_validation_error"
    assert failure.value.usage == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    assert "secret" not in str(failure.value)


async def test_provider_api_failures_are_sanitized() -> None:
    """Verify provider api failures are sanitized.

    Arrange: prepare `adapter, _, _`.
    Act: Call `adapter.decide` under `pytest.raises(ModelError)`.
    Assert: Check `raises ModelError`; `failure.value.code == 'llm_api_error'`; `'secret' not in
            str(failure.value)`; `failure.value.usage is None`.
    """
    adapter, _, _ = make_adapter(error=RuntimeError("Authorization: Bearer secret"))
    with pytest.raises(ModelError) as failure:
        await adapter.decide([], [])
    assert failure.value.code == "llm_api_error"
    assert "secret" not in str(failure.value)
    assert failure.value.usage is None


@pytest.mark.parametrize(
    "status,code,parameter",
    [
        (404, "model_not_found", "model"),
        (401, "invalid_api_key", None),
        (400, "unsupported_parameter", "tool_choice"),
        (429, "insufficient_quota", None),
    ],
)
@pytest.mark.parametrize("wrapped", [False, True])
async def test_provider_rejection_retains_safe_status_code_and_parameter(
    status: int, code: str, parameter: str | None, wrapped: bool
) -> None:
    """Verify provider rejection retains safe status code and parameter.

    Arrange: Use `status`, `code`, `parameter`, `wrapped` parameters; prepare `request`, `error`.
    Act: Call `adapter.decide` under `pytest.raises(ModelError)`.
    Assert: Check `raises ModelError`; `failure.value.code == 'llm_api_error'`;
            `failure.value.diagnostics['http_status'] == status`;
            `failure.value.diagnostics['parameter'] == parameter`.
    """
    request = httpx.Request(
        "POST",
        "https://api.openai.com/v1/chat/completions",
        headers={"authorization": "Bearer private-header"},
    )
    error = APIStatusError(
        "private-exception-body",
        response=httpx.Response(status, request=request),
        body={"code": code, "param": parameter, "message": "private-request-content"},
    )
    if wrapped:
        wrapper = RuntimeError("private-wrapper-message")
        wrapper.__cause__ = error
        adapter, _, _ = make_adapter(error=wrapper)
    else:
        adapter, _, _ = make_adapter(error=error)
    with pytest.raises(ModelError) as failure:
        await adapter.decide([], [])
    assert failure.value.code == "llm_api_error"
    assert failure.value.diagnostics["http_status"] == status
    assert failure.value.diagnostics["provider_code"] == code
    if parameter:
        assert failure.value.diagnostics["parameter"] == parameter
    assert "private-" not in str(failure.value)
    assert "private-" not in str(failure.value.diagnostics)
    assert failure.value.__suppress_context__


async def test_wrapped_connection_error_is_diagnosed_without_the_original_message() -> None:
    """Verify wrapped connection error is diagnosed without the original message.

    Arrange: prepare `wrapper`, `adapter, _, _`.
    Act: Call `adapter.decide` under `pytest.raises(ModelError)`.
    Assert: Check `raises ModelError`; `failure.value.diagnostics['kind'] ==
            'provider_connection_error'`; `'Cannot reach' in str(failure.value)`; `'private-' not in
            str(failure.value)`.
    """
    wrapper = RuntimeError("private-wrapper-message")
    wrapper.__cause__ = APIConnectionError(
        message="private-provider-message",
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
    )
    adapter, _, _ = make_adapter(error=wrapper)
    with pytest.raises(ModelError) as failure:
        await adapter.decide([], [])
    assert failure.value.diagnostics["kind"] == "provider_connection_error"
    assert "Cannot reach" in str(failure.value)
    assert "private-" not in str(failure.value)


async def test_unmeasurable_usage_is_none() -> None:
    """Verify unmeasurable usage is none.

    Arrange: prepare `adapter, _, _`.
    Act: Await `adapter.decide` to obtain `result`.
    Assert: Check `result.usage is None`.
    """
    adapter, _, _ = make_adapter({"raw": AIMessage(content=""), "parsed": Decision(done=True)})
    result = await adapter.decide([], [])
    assert result.usage is None


async def test_legacy_provider_token_usage_is_supported() -> None:
    """Verify legacy provider token usage is supported.

    Arrange: prepare `adapter, _, _`.
    Act: Await `adapter.decide` to obtain `result`.
    Assert: Check `result.usage == {'input_tokens': 9, 'output_tokens': 2, 'total_tokens': 11}`.
    """
    adapter, _, _ = make_adapter(
        {
            "raw": AIMessage(
                content="",
                response_metadata={
                    "token_usage": {
                        "prompt_tokens": 9,
                        "completion_tokens": 2,
                        "total_tokens": 11,
                    }
                },
            ),
            "parsed": Decision(done=True),
        }
    )
    result = await adapter.decide([], [])
    assert result.usage == {"input_tokens": 9, "output_tokens": 2, "total_tokens": 11}


async def test_cancellation_propagates_to_the_run_owner() -> None:
    """Verify cancellation propagates to the run owner.

    Arrange: prepare `adapter, _, _`.
    Act: Call `adapter.decide` under `pytest.raises(asyncio.CancelledError)`.
    Assert: Check `raises asyncio.CancelledError`.
    """
    adapter, _, _ = make_adapter(error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await adapter.decide([], [])


async def test_oversized_tool_catalog_fails_before_provider_request() -> None:
    """Verify oversized tool catalog fails before provider request.

    Arrange: prepare `adapter, invoke, _`.
    Act: Call `adapter.decide` under `pytest.raises(ModelError)`.
    Assert: Check `raises ModelError`.
    """
    adapter, invoke, _ = make_adapter()
    with pytest.raises(ModelError, match="schemas exceed"):
        await adapter.decide([], [{"description": "x" * 33_000}])
    invoke.assert_not_called()


def test_constructor_configuration_errors_are_sanitized() -> None:
    """Verify constructor configuration errors are sanitized.

    Arrange: Use the values passed to `patch`.
    Act: Evaluate `str` in the assertion.
    Assert: Check `failure.value.code == 'llm_configuration_error'`; `'secret' not in
            str(failure.value)`; `failure.value.__suppress_context__ is True`; `raises ModelError`.
    """
    with patch(
        "browser_exploration.model.ChatOpenAI",
        side_effect=ValueError("secret credential value"),
    ):
        with pytest.raises(ModelError) as failure:
            OpenAIDecisionModel(Settings())
    assert failure.value.code == "llm_configuration_error"
    assert "secret" not in str(failure.value)
    assert failure.value.__suppress_context__ is True
