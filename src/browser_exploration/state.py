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
    """Compact executor-owned progress retained across bounded model contexts."""

    capability: str
    action_name: str
    arguments: dict[str, Any]
    url: str
    observation_id: str
    evidence_id: str


class ExplorationState(TypedDict):
    task: BrowserExplorationTask
    run_id: str
    started_at: datetime
    current_url: str
    messages: list[BaseMessage]
    observations: list[BrowserObservation]
    verified_behaviors: list[VerifiedBehavior]
    findings: list[BrowserFinding]
    unresolved_questions: list[str]
    evidence: list[EvidenceReference]
    metrics: ExecutionMetrics
    status: RunStatus | None
    errors: list[RunError]
    observed_actions: list[str]
    successful_actions: list[SuccessfulAction]
    action_feedback: str | None
    bootstrap_stage: int
    result: BrowserExplorationResult | None
