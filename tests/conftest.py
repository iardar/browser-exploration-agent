"""Explicit service opt-ins and shared controls; ordinary pytest loads no credentials."""

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from _pytest.terminal import TerminalReporter
from dotenv import load_dotenv

from browser_exploration.model import ModelError
from tests.support.live_llm import (
    BudgetedDecisionModel,
    LiveCallBudget,
    LiveLimits,
    build_live_adapter,
    validate_live_selection,
)

_REPORT = pytest.StashKey[Path]()
pytest_plugins = ["tests.support.pytest_reportportal"]


def pytest_addoption(parser: pytest.Parser) -> None:
    """Expose deliberate live execution with small, explicit budgets."""
    group = parser.getgroup("live-llm")
    group.addoption(
        "--live-llm", action="store_true", help="Enable paid real-model component tests"
    )
    group.addoption("--llm-model", default="gpt-4.1-mini")
    group.addoption("--llm-max-calls", type=int, default=6)
    group.addoption("--llm-max-output-tokens", type=int, default=384)
    group.addoption("--llm-timeout", type=float, default=20)


def _limits(config: pytest.Config) -> LiveLimits:
    """Validate command-line budgets before creating any provider clients."""
    return LiveLimits(
        max_calls=config.getoption("llm_max_calls"),
        output_tokens=config.getoption("llm_max_output_tokens"),
        timeout_seconds=config.getoption("llm_timeout"),
    )


def pytest_configure(config: pytest.Config) -> None:
    """Reject parallel paid runs in the controller before xdist starts any workers."""
    if config.getoption("live_llm"):
        try:
            validate_live_selection(config.getoption("numprocesses", default=0), 0, _limits(config))
        except ValueError as exc:
            raise pytest.UsageError(str(exc)) from None


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip live cases by default and reject configurations that could multiply costs."""
    live_root = Path(__file__).resolve().parent / "live_llm"
    for item in items:
        if item.path.is_relative_to(live_root) and not item.get_closest_marker("live_llm"):
            item.add_marker(pytest.mark.live_llm)
    live = [item for item in items if item.get_closest_marker("live_llm")]
    if not config.getoption("live_llm"):
        for item in live:
            item.add_marker(
                pytest.mark.skip(reason="Use --live-llm explicitly for real-model checks")
            )
    elif live:
        try:
            validate_live_selection(
                config.getoption("numprocesses", default=0), len(live), _limits(config)
            )
        except ValueError as exc:
            raise pytest.UsageError(str(exc)) from None


@pytest.fixture(scope="session")
def live_llm_budget(request: pytest.FixtureRequest) -> Iterator[LiveCallBudget]:
    """Read credentials only after explicit opt-in, then retain a private usage report."""
    if not request.config.getoption("live_llm"):
        pytest.skip("Real model tests require --live-llm")
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.fail("OPENAI_API_KEY is required for --live-llm", pytrace=False)
    for name in ("BROWSER_LANGSMITH_ENABLED", "LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"):
        os.environ[name] = "false"
    budget = LiveCallBudget(
        model=request.config.getoption("llm_model"), limits=_limits(request.config)
    )
    run_name = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:10]
    report = Path("runs/test-live-llm") / run_name / "session.json"
    request.config.stash[_REPORT] = report
    budget.save(report)
    try:
        yield budget
    finally:
        budget.save(report)


@pytest.fixture
def live_decision_model(
    request: pytest.FixtureRequest, live_llm_budget: LiveCallBudget
) -> BudgetedDecisionModel:
    """Use the real provider adapter with a fake browser supplied by each component test."""
    if live_llm_budget.infrastructure_error:
        pytest.skip("Provider failed earlier; remaining live requests are stopped")
    try:
        delegate = build_live_adapter(live_llm_budget.model, live_llm_budget.limits)
    except ModelError as exc:
        live_llm_budget.infrastructure_error = exc.code
        pytest.fail(f"Live model setup failed: {exc.code}", pytrace=False)
    return BudgetedDecisionModel(
        delegate,
        live_llm_budget,
        request.node.nodeid,
    )


@pytest.fixture
def live_artifact_dir(request: pytest.FixtureRequest, live_llm_budget: LiveCallBudget) -> Path:
    """Keep graph artifacts beside the matching session usage report."""
    return request.config.stash[_REPORT].parent / "artifacts"


def pytest_terminal_summary(terminalreporter: TerminalReporter) -> None:
    """Expose the usage report path without printing credentials or request bodies."""
    if _REPORT in terminalreporter.config.stash:
        terminalreporter.write_line(
            f"Live LLM usage report: {terminalreporter.config.stash[_REPORT]}"
        )
