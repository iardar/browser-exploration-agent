"""Malformed provider responses, usage metadata, and decision-size boundaries."""

import json
import traceback
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from browser_exploration.config import Settings
from browser_exploration.model import Action, Decision, ModelError, OpenAIDecisionModel
from tests.unit.browser_exploration.test_model import make_adapter


def test_structured_output_configuration_errors_are_sanitized() -> None:
    """Verify structured output configuration errors are sanitized.

    Arrange: Start with `MagicMock()`.
    Act: Evaluate `traceback.format_exception` in the assertion.
    Assert: Check `failure.value.code == 'llm_configuration_error'`;
            `failure.value.__suppress_context__ is True`; `'private-credential-value' not in
            ''.join(traceback.format_exception(failure.value))`; `raises ModelError`.
    """
    chat = MagicMock()
    chat.return_value.with_structured_output.side_effect = ValueError("private-credential-value")
    with patch("browser_exploration.model.ChatOpenAI", chat):
        with pytest.raises(ModelError) as failure:
            OpenAIDecisionModel(Settings(model="configured-model"))
    chat.assert_called_once_with(model="configured-model", max_retries=0, use_responses_api=False)
    chat.return_value.with_structured_output.assert_called_once_with(
        Decision, method="function_calling", strict=False, include_raw=True
    )
    assert failure.value.code == "llm_configuration_error"
    assert failure.value.__suppress_context__ is True
    assert "private-credential-value" not in "".join(traceback.format_exception(failure.value))


@pytest.mark.parametrize("response", [None, "not JSON", [], 1])
async def test_non_mapping_provider_response_is_a_structured_validation_failure(
    response: Any,
) -> None:
    """Verify non mapping provider response is a structured validation failure.

    Arrange: Use `response` parameter; prepare `adapter, invoke, _`.
    Act: Call `adapter.decide` under `pytest.raises(ModelError)`.
    Assert: Check `raises ModelError`; `failure.value.code == 'llm_validation_error'`;
            `failure.value.usage is None`.
    """
    adapter, invoke, _ = make_adapter(response)
    with pytest.raises(ModelError) as failure:
        await adapter.decide([], [])
    assert failure.value.code == "llm_validation_error"
    assert failure.value.usage is None
    invoke.assert_awaited_once()


@pytest.mark.parametrize("parsed", [None, {"done": True}, "private-token"])
async def test_non_decision_parsed_output_preserves_usage_but_not_provider_payload(
    parsed: Any,
) -> None:
    """Verify non decision parsed output preserves usage but not provider payload.

    Arrange: Use `parsed` parameter; prepare `usage`, `adapter, _, _`.
    Act: Call `adapter.decide` under `pytest.raises(ModelError)`.
    Assert: Check `raises ModelError`; `failure.value.code == 'llm_validation_error'`;
            `failure.value.usage == usage`; `'private-token' not in str(failure.value)`.
    """
    usage = {"input_tokens": 20, "output_tokens": 7, "total_tokens": 27}
    adapter, _, _ = make_adapter(
        {"parsed": parsed, "raw": AIMessage(content="", usage_metadata=usage)}
    )
    with pytest.raises(ModelError) as failure:
        await adapter.decide([], [])
    assert failure.value.code == "llm_validation_error"
    assert failure.value.usage == usage
    assert "private-token" not in str(failure.value)


async def test_non_ai_message_raw_output_does_not_invent_usage() -> None:
    """Verify non ai message raw output does not invent usage.

    Arrange: prepare `adapter, _, _`.
    Act: Await `adapter.decide` to obtain `result`.
    Assert: Check `result.decision.done`; `result.usage is None`.
    """
    adapter, _, _ = make_adapter({"parsed": Decision(done=True), "raw": {"total_tokens": 20}})
    result = await adapter.decide([], [])
    assert result.decision.done
    assert result.usage is None


@pytest.mark.parametrize(
    "usage",
    [
        {"prompt_tokens": 12, "completion_tokens": 3},
        {"prompt_tokens": "12", "completion_tokens": 3, "total_tokens": 15},
        {"prompt_tokens": 12, "completion_tokens": 3.5, "total_tokens": 15},
        {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": None},
    ],
)
async def test_incomplete_or_non_integer_legacy_usage_is_unknown(usage: dict[str, Any]) -> None:
    """Verify incomplete or non integer legacy usage is unknown.

    Arrange: Use `usage` parameter; prepare `adapter, _, _`.
    Act: Await `adapter.decide` to obtain `result`.
    Assert: Check `result.usage is None`.
    """
    adapter, _, _ = make_adapter(
        {
            "parsed": Decision(done=True),
            "raw": AIMessage(content="", response_metadata={"token_usage": usage}),
        }
    )
    result = await adapter.decide([], [])
    assert result.usage is None


async def test_normalized_usage_takes_precedence_over_legacy_metadata() -> None:
    """Verify normalized usage takes precedence over legacy metadata.

    Arrange: prepare `normalized`.
    Act: Evaluate `adapter.decide` in the assertion.
    Assert: Check `(await adapter.decide([], [])).usage == normalized`.
    """
    normalized = {"input_tokens": 8, "output_tokens": 2, "total_tokens": 10}
    adapter, _, _ = make_adapter(
        {
            "parsed": Decision(done=True),
            "raw": AIMessage(
                content="",
                usage_metadata=normalized,
                response_metadata={
                    "token_usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 50,
                        "total_tokens": 150,
                    }
                },
            ),
        }
    )
    assert (await adapter.decide([], [])).usage == normalized


def test_question_length_and_count_have_independent_upper_bounds() -> None:
    """Verify question length and count have independent upper bounds.

    Arrange: Use the values passed to `len`.
    Act: Call `Decision` under `pytest.raises(ValidationError)`.
    Assert: Check `len() equals values 30`; `raises ValidationError`.
    """
    assert (
        len(Decision(done=True, unresolved_questions=["x" * 2000] * 30).unresolved_questions) == 30
    )
    with pytest.raises(ValidationError, match="2000"):
        Decision(done=True, unresolved_questions=["x" * 2001])
    with pytest.raises(ValidationError, match="30"):
        Decision(done=True, unresolved_questions=["question"] * 31)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_action_values_are_not_valid_json(value: float) -> None:
    """Verify non finite action values are not valid json.

    Arrange: Use `value` parameter.
    Act: Call `Action` under `pytest.raises(ValidationError)`.
    Assert: Check `raises ValidationError`.
    """
    with pytest.raises(ValidationError, match="JSON-compatible"):
        Action(name="browser_navigate", arguments={"nested": {"value": value}})


def test_action_json_size_boundary_includes_structure_and_whitespace() -> None:
    """Verify action json size boundary includes structure and whitespace.

    Arrange: prepare `empty_size`, `arguments`.
    Act: Call `Action` under `pytest.raises(ValidationError)`.
    Assert: Check `len(json.dumps(Action(name='browser_navigate', arguments=arguments).arguments))
            == 8192`; `raises ValidationError`.
    """
    empty_size = len(json.dumps({"url": ""}))
    arguments = {"url": "x" * (8192 - empty_size)}
    assert len(json.dumps(Action(name="browser_navigate", arguments=arguments).arguments)) == 8192
    with pytest.raises(ValidationError, match="8192"):
        Action(name="browser_navigate", arguments={"url": arguments["url"] + "x"})


def test_action_argument_count_and_name_size_are_bounded_separately() -> None:
    """Verify action argument count and name size are bounded separately.

    Arrange: prepare `arguments`.
    Act: Call `Action` under `pytest.raises(ValidationError)`.
    Assert: Check `len(Action(name='x' * 100, arguments=arguments).arguments) == 32`; `raises
            ValidationError`.
    """
    arguments = {str(index): None for index in range(32)}
    assert len(Action(name="x" * 100, arguments=arguments).arguments) == 32
    with pytest.raises(ValidationError):
        Action(name="x" * 101, arguments={})
    with pytest.raises(ValidationError):
        Action(name="browser_snapshot", arguments={**arguments, "overflow": None})


async def test_tool_catalog_at_exact_limit_is_accepted_and_larger_is_rejected_before_request() -> (
    None
):
    """Verify tool catalog at exact limit is accepted and larger is rejected before request.

    Arrange: prepare `adapter, invoke, _`, `empty_size`, `tools`.
    Act: Call `adapter.decide` under `pytest.raises(ModelError)`.
    Assert: Check `(await adapter.decide([], tools)).decision.done`; `raises ModelError`;
            `failure.value.code == 'tool_catalog_too_large'`.
    """
    adapter, invoke, _ = make_adapter({"raw": AIMessage(content=""), "parsed": Decision(done=True)})
    empty_size = len(json.dumps([{"description": ""}], ensure_ascii=False))
    tools = [{"description": "x" * (adapter.MAX_TOOL_CATALOG_CHARS - empty_size)}]
    assert (await adapter.decide([], tools)).decision.done
    invoke.assert_awaited_once()
    tools[0]["description"] += "x"
    with pytest.raises(ModelError) as failure:
        await adapter.decide([], tools)
    assert failure.value.code == "tool_catalog_too_large"
    invoke.assert_awaited_once()
