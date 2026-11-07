"""Cheap tests prove paid-test controls without opening a provider connection."""

import asyncio
import json
import stat
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import HumanMessage

from browser_exploration.model import Decision, DecisionResponse, ModelError
from tests.support.live_llm import (
    BudgetedDecisionModel,
    LiveCallBudget,
    LiveLimits,
    build_live_adapter,
    validate_live_selection,
)
from tests.unit.browser_exploration.fakes import FakeModel


@pytest.mark.parametrize(
    "updates",
    [
        {"max_calls": 0},
        {"max_calls": 101},
        {"calls_per_case": 0},
        {"calls_per_case": 7},
        {"output_tokens": 15},
        {"output_tokens": 4_097},
        {"input_chars": 0},
        {"input_chars": 100_001},
        {"timeout_seconds": 0},
        {"timeout_seconds": 121},
        {"timeout_seconds": float("nan")},
    ],
)
def test_invalid_live_limits_fail_before_client_creation(updates: dict[str, Any]) -> None:
    """Verify invalid live limits fail before client creation.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for invalid live limits fail before client creation.
    """
    with pytest.raises(ValueError):
        replace(LiveLimits(), **updates)


@pytest.mark.parametrize("workers", [1, 2, "auto", "logical"])
def test_parallel_live_execution_is_rejected(workers: Any) -> None:
    """Verify parallel live execution is rejected.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for parallel live execution is rejected.
    """
    with pytest.raises(ValueError, match="-n 0"):
        validate_live_selection(workers, 3, LiveLimits())


def test_selected_cases_must_fit_the_shared_budget() -> None:
    """Verify selected cases must fit the shared budget.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for selected cases must fit the shared budget.
    """
    validate_live_selection(0, 3, LiveLimits())
    with pytest.raises(ValueError, match="select fewer"):
        validate_live_selection(0, 4, LiveLimits())


def test_real_adapter_factory_enforces_sdk_limits_without_changing_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify real adapter factory enforces sdk limits without changing the model.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for real adapter factory enforces sdk limits without
            changing the model."""
    client = MagicMock()
    monkeypatch.setattr("tests.support.live_llm.ChatOpenAI", client)
    monkeypatch.setenv("OPENAI_MODEL", "unrelated-expensive-model")
    build_live_adapter("gpt-4.1-mini", LiveLimits())
    options = client.call_args.kwargs
    assert options["model"] == "gpt-4.1-mini"
    assert options["max_retries"] == 0
    assert options["max_completion_tokens"] == 384
    assert options["timeout"] == 20
    assert options["store"] is False


async def test_per_case_limit_blocks_the_delegate_before_an_extra_request() -> None:
    """Verify per case limit blocks the delegate before an extra request.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for per case limit blocks the delegate before an extra
            request."""
    delegate = FakeModel([DecisionResponse(Decision(done=True))] * 3)
    budget = LiveCallBudget("fake-model")
    model = BudgetedDecisionModel(delegate, budget, "case-1")
    await model.decide([], [])
    await model.decide([], [])
    with pytest.raises(ModelError) as caught:
        await model.decide([], [])
    assert caught.value.code == "llm_test_call_limit"
    assert len(delegate.requests) == len(budget.attempts) == 2


async def test_session_limit_is_shared_across_cases() -> None:
    """Verify session limit is shared across cases.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for session limit is shared across cases.
    """
    delegate = FakeModel([DecisionResponse(Decision(done=True))] * 3)
    budget = LiveCallBudget("fake", LiveLimits(max_calls=2, calls_per_case=1))
    for case_id in ["a", "b"]:
        await BudgetedDecisionModel(delegate, budget, case_id).decide([], [])
    with pytest.raises(ModelError, match="allowance exhausted"):
        await BudgetedDecisionModel(delegate, budget, "c").decide([], [])
    assert len(delegate.requests) == 2


async def test_input_limit_is_checked_before_any_request() -> None:
    """Verify input limit is checked before any request.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for input limit is checked before any request.
    """
    delegate = FakeModel()
    budget = LiveCallBudget("fake", LiveLimits(input_chars=1))
    with pytest.raises(ModelError) as caught:
        await BudgetedDecisionModel(delegate, budget, "case").decide([HumanMessage("large")], [])
    assert caught.value.code == "llm_test_input_limit"
    assert not delegate.requests and not budget.attempts


async def test_failed_provider_attempt_is_counted_and_stops_later_calls() -> None:
    """Verify failed provider attempt is counted and stops later calls.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for failed provider attempt is counted and stops later
            calls."""
    delegate = FakeModel([ModelError("llm_api_error", "Provider unavailable")])
    budget = LiveCallBudget("fake")
    model = BudgetedDecisionModel(delegate, budget, "case")
    with pytest.raises(ModelError):
        await model.decide([], [])
    with pytest.raises(ModelError) as caught:
        await model.decide([], [])
    assert caught.value.code == "llm_test_unavailable"
    assert len(delegate.requests) == len(budget.attempts) == 1
    assert budget.tokens.counts()["total_tokens"] is None


async def test_validation_failure_preserves_usage_without_disabling_the_provider() -> None:
    """Verify validation failure preserves usage without disabling the provider.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for validation failure preserves usage without disabling
            the provider."""
    usage = {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}
    delegate = FakeModel([ModelError("llm_validation_error", "Invalid decision", usage)])
    budget = LiveCallBudget("fake")
    with pytest.raises(ModelError):
        await BudgetedDecisionModel(delegate, budget, "case").decide([], [])
    assert budget.tokens.counts() == usage
    assert budget.infrastructure_error is None


async def test_deadline_cancels_the_provider_and_counts_the_attempt() -> None:
    """Verify deadline cancels the provider and counts the attempt.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for deadline cancels the provider and counts the attempt.
    """
    delegate = FakeModel(blocked=True)
    budget = LiveCallBudget("fake", LiveLimits(timeout_seconds=0.01))
    with pytest.raises(ModelError) as caught:
        await BudgetedDecisionModel(delegate, budget, "case").decide([], [])
    assert caught.value.code == "llm_test_timeout"
    assert len(delegate.requests) == len(budget.attempts) == 1
    assert budget.infrastructure_error == "llm_test_timeout"


async def test_cancelled_request_does_not_refund_the_budget() -> None:
    """Verify cancelled request does not refund the budget.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for cancelled request does not refund the budget.
    """
    delegate = FakeModel(blocked=True)
    budget = LiveCallBudget("fake")
    task = asyncio.create_task(BudgetedDecisionModel(delegate, budget, "case").decide([], []))
    await delegate.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert budget.attempts[0]["status"] == "cancelled"
    assert budget.infrastructure_error == "cancelled"


async def test_concurrent_reservations_cannot_exceed_a_single_call() -> None:
    """Verify concurrent reservations cannot exceed a single call.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for concurrent reservations cannot exceed a single call.
    """
    delegate = FakeModel(blocked=True)
    budget = LiveCallBudget("fake", LiveLimits(max_calls=1, calls_per_case=1))
    first = asyncio.create_task(BudgetedDecisionModel(delegate, budget, "a").decide([], []))
    await delegate.started.wait()
    with pytest.raises(ModelError, match="allowance exhausted"):
        await BudgetedDecisionModel(delegate, budget, "b").decide([], [])
    delegate.release.set()
    await first
    assert len(delegate.requests) == len(budget.attempts) == 1


async def test_usage_report_is_private_and_omits_prompts_and_secrets(tmp_path: Path) -> None:
    """Verify usage report is private and omits prompts and secrets.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for usage report is private and omits prompts and secrets.
    """
    usage = {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}
    delegate = FakeModel([DecisionResponse(Decision(done=True), usage, "reported-provider-model")])
    budget = LiveCallBudget("selected-model")
    await BudgetedDecisionModel(delegate, budget, "case").decide(
        [HumanMessage("private-prompt-canary")], []
    )
    path = tmp_path / "private/session.json"
    budget.save(path)
    saved = json.loads(path.read_text())
    assert saved["usage"] == usage
    assert saved["attempted_calls"] == 1
    assert saved["attempts"][0]["provider_model"] == "reported-provider-model"
    assert "private-prompt-canary" not in path.read_text()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


async def test_unexpected_errors_do_not_expose_the_original_exception() -> None:
    """Verify unexpected errors do not expose the original exception.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for unexpected errors do not expose the original exception.
    """
    budget = LiveCallBudget("fake")
    delegate = FakeModel([RuntimeError("Authorization: Bearer private-secret")])
    with pytest.raises(ModelError) as caught:
        await BudgetedDecisionModel(delegate, budget, "case").decide([], [])
    assert caught.value.code == "llm_test_error"
    assert "private-secret" not in str(caught.value)
    assert "private-secret" not in json.dumps(budget.attempts)
