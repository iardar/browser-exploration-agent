"""Versioned contracts for the independent browser exploration boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RunStatus = Literal[
    "completed", "step_limit", "model_limit", "timeout", "cancelled", "blocked", "failed"
]
VerificationStatus = Literal["confirmed", "potential", "inconclusive"]


class Contract(BaseModel):
    """Reject unknown fields rather than silently changing the application contract."""

    model_config = ConfigDict(extra="forbid")


class ContextSnippet(Contract):
    source: str = Field(min_length=1, max_length=1_000)
    text: str = Field(min_length=1, max_length=1_000_000)


class BrowserExplorationTask(Contract):
    url: str = Field(min_length=1, max_length=8_192)
    objective: str = Field(min_length=1, max_length=100_000)
    requirement_text: str | None = Field(default=None, max_length=1_000_000)
    context_snippets: list[ContextSnippet] = Field(default_factory=list, max_length=100)
    allowed_domains: list[str] = Field(default_factory=list, max_length=100)
    max_steps: int = Field(default=30, ge=1, le=1_000)
    max_model_iterations: int = Field(default=20, ge=1, le=1_000)
    max_duration_seconds: int = Field(default=120, ge=1, le=86_400)
    max_recent_observations: int = Field(default=5, ge=1, le=100)
    max_observation_chars: int = Field(default=6_000, ge=1, le=1_000_000)
    max_context_chars: int = Field(default=18_000, ge=4_096, le=2_000_000)
    allow_mutations: bool = False
    allow_form_input: bool = False
    allow_form_submission: bool = False

    @field_validator("objective")
    @classmethod
    def meaningful_objective(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("objective must contain non-whitespace text")
        return value.strip()

    @field_validator("url")
    @classmethod
    def http_url(cls, value: str) -> str:
        if any(ord(character) < 32 for character in value) or "\\" in value:
            raise ValueError("URL must not contain control characters or backslashes")
        try:
            parsed = urlsplit(value)
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("invalid target URL") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("target URL must be an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("target URL must not contain credentials")
        return value

    @field_validator("allowed_domains")
    @classmethod
    def exact_domains(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            domain = value.strip().lower().rstrip(".")
            if not domain or any(character in domain for character in "/@?#*\\ "):
                raise ValueError("allowed_domains must contain exact hostnames, without wildcards")
            # IPv6 addresses are accepted as exact hostnames; safety policy resolves IP ranges.
            if ":" not in domain:
                try:
                    domain = domain.encode("idna").decode("ascii")
                except UnicodeError as exc:
                    raise ValueError("invalid allowed domain") from exc
            if domain not in normalized:
                normalized.append(domain)
        return normalized

    @model_validator(mode="after")
    def normalize_permissions_and_target_domain(self) -> BrowserExplorationTask:
        if self.allow_form_submission:
            self.allow_form_input = True
        if not self.allowed_domains:
            host = urlsplit(self.url).hostname
            assert host is not None  # Enforced by http_url.
            self.allowed_domains = [host.lower().rstrip(".").encode("idna").decode("ascii")]
        return self


class BrowserObservation(Contract):
    id: str
    url: str
    action_name: str
    action_arguments: dict[str, Any] = Field(default_factory=dict)
    summary: str
    evidence_ref: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error: bool = False


class BrowserFinding(Contract):
    id: str
    title: str = Field(min_length=1)
    description: str
    expected_behavior: str
    observed_behavior: str
    reproduction_steps: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    verification_status: VerificationStatus
    severity: Literal["critical", "high", "medium", "low", "informational"] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def confirmed_requires_evidence(self) -> BrowserFinding:
        if self.verification_status == "confirmed" and not self.evidence_ids:
            raise ValueError("confirmed findings require evidence identifiers")
        return self


class VerifiedBehavior(Contract):
    description: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)


class EvidenceReference(Contract):
    id: str
    observation_id: str
    kind: str
    path: str | None = None
    retained: bool = False


class RunError(Contract):
    code: str
    message: str


class ExecutionMetrics(Contract):
    duration_seconds: float = Field(default=0, ge=0)
    browser_tool_calls: int = Field(default=0, ge=0)
    successful_browser_tool_calls: int = Field(default=0, ge=0)
    failed_browser_tool_calls: int = Field(default=0, ge=0)
    llm_calls: int = Field(default=0, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    findings_count: int = Field(default=0, ge=0)
    verified_behaviors_count: int = Field(default=0, ge=0)


class BrowserExplorationResult(Contract):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    objective: str
    target_url: str
    status: RunStatus
    started_at: datetime
    finished_at: datetime
    verified_behaviors: list[VerifiedBehavior] = Field(default_factory=list)
    findings: list[BrowserFinding] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    metrics: ExecutionMetrics = Field(default_factory=ExecutionMetrics)
    errors: list[RunError] = Field(default_factory=list)
    observed_actions: list[str] = Field(default_factory=list)


class BrowserExplorer(Protocol):
    async def explore(self, task: BrowserExplorationTask) -> BrowserExplorationResult:
        """Explore a task and return its actual terminal status and evidence."""
        ...
