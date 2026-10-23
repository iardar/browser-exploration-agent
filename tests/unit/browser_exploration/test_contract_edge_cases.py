"""Boundary values, malformed task configuration and independent contract instances."""

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from browser_exploration.config import Settings
from browser_exploration.contracts import (
    BrowserExplorationResult,
    BrowserExplorationTask,
    BrowserFinding,
    BrowserObservation,
    ContextSnippet,
    ExecutionMetrics,
)

BASE_TASK = {"url": "https://example.com/", "objective": "Inspect page"}
LIMITS = [
    ("max_steps", 1, 1000),
    ("max_model_iterations", 1, 1000),
    ("max_duration_seconds", 1, 86400),
    ("max_recent_observations", 1, 100),
    ("max_observation_chars", 1, 1000000),
    ("max_context_chars", 4096, 2000000),
]


@pytest.mark.parametrize(("name", "minimum", "maximum"), LIMITS)
def test_task_and_environment_budgets_accept_inclusive_endpoints(
    name: str,
    minimum: int,
    maximum: int,
) -> None:
    for boundary in (minimum, maximum):
        task = BrowserExplorationTask.model_validate({**BASE_TASK, name: boundary})
        configured = Settings.model_validate({name: boundary})
        assert getattr(task, name) == getattr(configured, name) == boundary


@pytest.mark.parametrize(("name", "minimum", "maximum"), LIMITS)
def test_task_and_environment_budgets_reject_values_just_outside_limits(
    name: str,
    minimum: int,
    maximum: int,
) -> None:
    for invalid in (minimum - 1, maximum + 1):
        with pytest.raises(ValidationError):
            BrowserExplorationTask.model_validate({**BASE_TASK, name: invalid})
        with pytest.raises(ValidationError):
            Settings.model_validate({name: invalid})


@pytest.mark.parametrize(
    "url", ["https://example.com/\tpage", "https://example.com/\x00", "https://example.com\\page"]
)
def test_task_rejects_control_characters_and_browser_ambiguous_backslashes(url: str) -> None:
    with pytest.raises(ValidationError, match="control characters or backslashes"):
        BrowserExplorationTask.model_validate({**BASE_TASK, "url": url})


def test_domain_normalization_preserves_order_and_deduplicates_idna_equivalents() -> None:
    task = BrowserExplorationTask.model_validate(
        {
            **BASE_TASK,
            "allowed_domains": [
                " BÜCHER.example. ",
                "xn--bcher-kva.example",
                "EXAMPLE.COM",
                "example.com.",
            ],
        }
    )
    assert task.allowed_domains == ["xn--bcher-kva.example", "example.com"]


def test_explicit_and_default_ipv6_domains_use_hostnames_without_url_brackets() -> None:
    url = "https://[2606:4700:4700::1111]:443/article"
    implicit = BrowserExplorationTask(url=url, objective="Inspect")
    explicit = BrowserExplorationTask(
        url=url, objective="Inspect", allowed_domains=["2606:4700:4700::1111"]
    )
    assert implicit.allowed_domains == explicit.allowed_domains == ["2606:4700:4700::1111"]


@pytest.mark.parametrize("domain", ["\ud800.example", "x" * 64 + ".example"])
def test_invalid_idna_domain_encoding_is_a_contract_validation_error(domain: str) -> None:
    with pytest.raises(ValidationError, match="invalid allowed domain"):
        BrowserExplorationTask.model_validate({**BASE_TASK, "allowed_domains": [domain]})


@pytest.mark.parametrize("model", ["", " ", "\n\t"])
def test_empty_configured_model_is_rejected(model: str) -> None:
    with pytest.raises(ValidationError, match="OPENAI_MODEL must not be empty"):
        Settings(model=model)


def test_configuration_alias_precedence_and_explicit_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_MODEL", " primary-model ")
    monkeypatch.setenv("BROWSER_MODEL", "fallback-model")
    monkeypatch.setenv("PLAYWRIGHT_MCP_URL", "http://localhost:8931/mcp")
    monkeypatch.setenv("BROWSER_MCP_URL", "http://127.0.0.1:9000/mcp")
    assert Settings().model == "primary-model"
    assert Settings().mcp_url == "http://localhost:8931/mcp"
    assert Settings(model="explicit-model").model == "explicit-model"
    monkeypatch.delenv("OPENAI_MODEL")
    monkeypatch.delenv("PLAYWRIGHT_MCP_URL")
    assert Settings().model == "fallback-model"
    assert Settings().mcp_url == "http://127.0.0.1:9000/mcp"


@pytest.mark.parametrize("value", [0, -0.01, 3600.001, float("inf"), float("nan")])
def test_tool_timeout_rejects_invalid_and_non_finite_values(value: float) -> None:
    with pytest.raises(ValidationError):
        Settings(tool_timeout_seconds=value)


def test_tool_timeout_accepts_fractional_and_maximum_valid_seconds() -> None:
    assert Settings(tool_timeout_seconds=0.001).tool_timeout_seconds == 0.001
    assert Settings(tool_timeout_seconds=3600).tool_timeout_seconds == 3600


@pytest.mark.parametrize(
    "values",
    [
        {"source": "", "text": "context"},
        {"source": "repo", "text": ""},
        {"source": "x" * 1001, "text": "context"},
    ],
)
def test_context_snippets_reject_missing_meaningful_fields(values: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        ContextSnippet.model_validate(values)


def test_task_collections_are_isolated_between_instances() -> None:
    first = BrowserExplorationTask.model_validate(BASE_TASK)
    second = BrowserExplorationTask.model_validate(BASE_TASK)
    first.allowed_domains.append("additional.example")
    first.context_snippets.append(ContextSnippet(source="repo", text="Only for first task"))
    assert second.allowed_domains == ["example.com"]
    assert second.context_snippets == []


def test_result_collections_and_metrics_are_isolated_between_runs() -> None:
    values: dict[str, Any] = {
        "run_id": "one",
        "objective": "Inspect",
        "target_url": "https://example.com",
        "status": "completed",
        "started_at": datetime.now(UTC),
        "finished_at": datetime.now(UTC),
    }
    first = BrowserExplorationResult.model_validate(values)
    second = BrowserExplorationResult.model_validate({**values, "run_id": "two"})
    first.unresolved_questions.append("Only one run has this question")
    first.metrics.llm_calls = 2
    assert second.unresolved_questions == []
    assert second.metrics.llm_calls == 0


@pytest.mark.parametrize(
    "field",
    [
        "duration_seconds",
        "browser_tool_calls",
        "successful_browser_tool_calls",
        "failed_browser_tool_calls",
        "llm_calls",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "findings_count",
        "verified_behaviors_count",
    ],
)
def test_negative_execution_metrics_are_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        ExecutionMetrics.model_validate({field: -1})


@pytest.mark.parametrize("confidence", [-0.01, 1.01, float("nan"), float("inf")])
def test_finding_confidence_must_be_a_finite_probability(confidence: float) -> None:
    with pytest.raises(ValidationError):
        BrowserFinding(
            id="f",
            title="Hypothesis",
            description="Needs validation",
            expected_behavior="Visible",
            observed_behavior="Unknown",
            verification_status="potential",
            confidence=confidence,
        )


def test_observation_uses_a_timezone_aware_default_timestamp_and_independent_arguments() -> None:
    first = BrowserObservation(
        id="one", url="https://example.com", action_name="snapshot", summary="one"
    )
    second = BrowserObservation(
        id="two", url="https://example.com", action_name="snapshot", summary="two"
    )
    first.action_arguments["level"] = "warning"
    assert second.action_arguments == {}
    assert first.timestamp.tzinfo is not None
    assert first.timestamp.utcoffset() == datetime.now(UTC).utcoffset()
