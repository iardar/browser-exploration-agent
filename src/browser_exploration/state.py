"""Run-local LangGraph state; raw browser payloads never enter this state."""

from datetime import datetime
from typing import Any, TypedDict

from langchain_core.messages import BaseMessage

from .contracts import (
    BrowserExplorationResult,
    BrowserExplorationTask,
    BrowserFinding,
    BrowserObservation,
    EvidenceReference,
    ExecutionMetrics,
    RunError,
    RunStatus,
    VerifiedBehavior,
)


class SuccessfulAction(TypedDict):
    """Remember a successful call after its detailed observation leaves recent context."""

    capability: str
    action_name: str
    arguments: dict[str, Any]
    url: str
    observation_id: str
    evidence_id: str


class ExplorationState(TypedDict):
    """Describe the working dictionary shared by all three graph nodes in one run.

    ``TypedDict`` documents keys for type checking; public Pydantic models validate
    task and result data. This internal state is not the host application's state
    and is not saved as a resumable checkpoint.
    """

    task: BrowserExplorationTask
    run_id: str
    started_at: datetime
    current_url: str
    # Rebuilt for each decision; only a bounded set of recent observations is kept here.
    messages: list[BaseMessage]
    observations: list[BrowserObservation]
    verified_behaviors: list[VerifiedBehavior]
    findings: list[BrowserFinding]
    unresolved_questions: list[str]
    evidence: list[EvidenceReference]
    metrics: ExecutionMetrics
    # None means exploration can continue; any status sends the graph to finalization.
    status: RunStatus | None
    errors: list[RunError]
    observed_actions: list[str]
    # The full progress ledger survives replacement of recent observations and messages.
    successful_actions: list[SuccessfulAction]
    action_feedback: str | None
    bootstrap_stage: int  # 0: navigate next; 1: snapshot next; 2: setup calls selected.
    result: BrowserExplorationResult | None
