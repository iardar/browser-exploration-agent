"""Test-only limits around the production adapter; importing this makes no API calls."""

import asyncio
import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI

from browser_exploration.config import Settings
from browser_exploration.model import (
    Decision,
    DecisionModel,
    DecisionResponse,
    ModelError,
    OpenAIDecisionModel,
)
from browser_exploration.reporting import redact
from browser_exploration.telemetry import TokenAccumulator


@dataclass(frozen=True)
class LiveLimits:
    """Bound requests before sending them; token usage remains provider-reported."""

    max_calls: int = 6
    calls_per_case: int = 2
    output_tokens: int = 384
    input_chars: int = 20_000
    timeout_seconds: float = 20

    def __post_init__(self) -> None:
        if not 1 <= self.max_calls <= 100 or not 1 <= self.calls_per_case <= self.max_calls:
            raise ValueError("Live call limits must be positive and fit the session budget")
        if not 16 <= self.output_tokens <= 4_096:
            raise ValueError("Live output allowance must be between 16 and 4096 tokens")
        if not 1 <= self.input_chars <= 100_000 or not 0 < self.timeout_seconds <= 120:
            raise ValueError("Live input/deadline limits are out of range")


def validate_live_selection(workers: Any, selected: int, limits: LiveLimits) -> None:
    """Require serial execution so parallel workers cannot each spend a session budget."""
    if workers not in (None, 0, "0"):
        raise ValueError("Real LLM tests require -n 0; parallel live requests are disabled")
    if selected * limits.calls_per_case > limits.max_calls:
        raise ValueError("Selected live cases exceed --llm-max-calls; select fewer cases with -k")


@dataclass
class LiveCallBudget:
    """Count attempts, including failures, and stop after provider connectivity errors."""

    model: str
    limits: LiveLimits = field(default_factory=LiveLimits)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    per_case: dict[str, int] = field(default_factory=dict)
    infrastructure_error: str | None = None
    tokens: TokenAccumulator = field(default_factory=TokenAccumulator)

    def reserve(self, case_id: str, input_chars: int) -> dict[str, Any]:
        """Reserve synchronously before any await, so concurrent callers cannot overspend."""
        if self.infrastructure_error:
            raise ModelError("llm_test_unavailable", "A previous provider attempt failed")
        if input_chars > self.limits.input_chars:
            raise ModelError("llm_test_input_limit", "Live input exceeds the character allowance")
        if (
            len(self.attempts) >= self.limits.max_calls
            or self.per_case.get(case_id, 0) >= self.limits.calls_per_case
        ):
            raise ModelError("llm_test_call_limit", "Live model request allowance exhausted")
        self.per_case[case_id] = self.per_case.get(case_id, 0) + 1
        attempt = {"case_id": case_id, "input_chars": input_chars, "status": "started"}
        self.attempts.append(attempt)
        return attempt

    def save(self, path: Path) -> None:
        """Write measured usage and safe diagnostics, without prompts or environment secrets."""
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        report = {
            "model": self.model,
            "limits": asdict(self.limits),
            "attempted_calls": len(self.attempts),
            "infrastructure_error": self.infrastructure_error,
            "usage": self.tokens.counts(),
            "attempts": self.attempts,
        }
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(redact(report), handle, indent=2, allow_nan=False)
            handle.write("\n")


class BudgetedDecisionModel:
    """Apply per-case deadlines and session-wide limits to a real or injected decision model."""

    def __init__(self, delegate: DecisionModel, budget: LiveCallBudget, case_id: str) -> None:
        self.delegate = delegate
        self.budget = budget
        self.case_id = case_id

    async def decide(
        self, messages: list[BaseMessage], tools: list[dict[str, Any]]
    ) -> DecisionResponse:
        """Make at most one delegated call, recording safe metadata even when it fails."""
        input_chars = sum(len(str(message.content)) for message in messages)
        input_chars += len(json.dumps(tools)) + len(json.dumps(Decision.model_json_schema()))
        attempt = self.budget.reserve(self.case_id, input_chars)
        started = time.monotonic()
        usage: dict[str, int] | None = None
        try:
            async with asyncio.timeout(self.budget.limits.timeout_seconds):
                response = await self.delegate.decide(messages, tools)
            usage = response.usage
            attempt.update(status="succeeded", provider_model=response.provider_model)
            return response
        except TimeoutError:
            self.budget.infrastructure_error = "llm_test_timeout"
            attempt.update(status="failed", code="llm_test_timeout")
            raise ModelError(
                "llm_test_timeout", "Live model request exceeded its deadline"
            ) from None
        except ModelError as exc:
            usage = exc.usage
            attempt.update(status="failed", code=exc.code, diagnostics=exc.diagnostics)
            if exc.code in {"llm_api_error", "llm_configuration_error"}:
                self.budget.infrastructure_error = exc.code
            raise
        except asyncio.CancelledError:
            attempt.update(status="cancelled")
            self.budget.infrastructure_error = "cancelled"
            raise
        except Exception:
            attempt.update(status="failed", code="unexpected_error")
            self.budget.infrastructure_error = "unexpected_error"
            raise ModelError("llm_test_error", "Unexpected live model failure") from None
        finally:
            attempt["duration_seconds"] = time.monotonic() - started
            attempt["usage"] = usage
            self.budget.tokens.add(usage)


def build_live_adapter(model: str, limits: LiveLimits) -> OpenAIDecisionModel:
    """Construct the real app adapter with capped SDK settings, without making a request."""
    factory: Callable[..., Any] = ChatOpenAI

    def bounded_client(**options: Any) -> Any:
        return factory(
            **{
                **options,
                "max_retries": 0,
                "max_completion_tokens": limits.output_tokens,
                "timeout": limits.timeout_seconds,
                "store": False,
            }
        )

    with patch("browser_exploration.model.ChatOpenAI", side_effect=bounded_client):
        return OpenAIDecisionModel(
            Settings(model=model, model_api="auto", reasoning_effort=None, langsmith_enabled=False)
        )
