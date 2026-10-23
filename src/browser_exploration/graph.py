"""One explicit state machine with bounded model/tool iterations."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import UTC, datetime
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import ValidationError

from .browser_tools import tool_specs
from .config import Settings
from .context_builder import ContextBuilder, ContextLimitError
from .contracts import (
    BrowserExplorationResult,
    BrowserObservation,
    RunError,
    RunStatus,
)
from .mcp_client import BrowserSession, MCPError
from .model import (
    Action,
    Decision,
    DecisionModel,
    ModelError,
    OpenAIDecisionModel,
    validation_diagnostics,
)
from .observations import summarize_observation
from .reporting import ArtifactStore, redact, redact_text
from .safety import SafetyError, SafetyPolicy
from .state import ExplorationState
from .telemetry import TokenAccumulator

logger = logging.getLogger(__name__)


def observation_text(raw: Any) -> str:
    """Extract textual MCP content without putting base64 images into context."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        return "\n".join(observation_text(part) for part in raw)
    if isinstance(raw, dict):
        if raw.get("type") in {"image", "audio", "file", "image_url"}:
            return "[binary evidence omitted from model context]"
        if "text" in raw:
            return str(raw["text"])
        if "content" in raw:
            return observation_text(raw["content"])
        return json.dumps(redact(raw), ensure_ascii=False)
    return ""


class ExplorationNodes:
    """Dependencies and the last checkpoint belong to one run, never to a global."""

    def __init__(
        self,
        settings: Settings,
        store: ArtifactStore,
        state: ExplorationState,
        model: DecisionModel | None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.latest = state
        self.model = model
        self.session: BrowserSession | None = None
        self.policy = SafetyPolicy(state["task"], settings)
        self.context = ContextBuilder()
        self.tokens = TokenAccumulator()
        self.started_monotonic = time.monotonic()
        self.valid_evidence: set[str] = set()
        self.page_revision = 0
        self.completed_inspections: set[tuple[int, str, str]] = set()
        self.needs_snapshot = False
        self.consecutive_invalid_decisions = 0

    def stop(self, status: RunStatus, code: str, message: str) -> None:
        self.latest["status"] = status
        self.latest["errors"].append(RunError(code=code, message=redact_text(message)))

    def prepare_context(self, state: ExplorationState) -> ExplorationState:
        self.latest = state
        try:
            self.policy.validate_url(state["task"].url)
            state["messages"] = self.context.build(state["task"], None, [], [], [], [])
            if not self.settings.network_restricted:
                raise SafetyError(
                    "Browser network boundary is unverified. Configure a restricted, controlled "
                    "environment and set BROWSER_NETWORK_RESTRICTED=true."
                )
        except SafetyError as exc:
            self.stop("blocked", "safety_policy", str(exc))
        except ContextLimitError:
            self.stop(
                "blocked",
                "context_limit",
                "The full objective and restrictions must fit the configured context budget",
            )
        return state

    def budget_exhausted(self) -> bool:
        state = self.latest
        task = state["task"]
        if time.monotonic() - self.started_monotonic >= task.max_duration_seconds:
            self.stop("timeout", "execution_timeout", "Maximum run duration reached")
        elif state["metrics"].browser_tool_calls >= task.max_steps:
            self.stop("step_limit", "browser_call_limit", "Maximum browser tool calls reached")
        elif state["metrics"].llm_calls >= task.max_model_iterations:
            self.stop("model_limit", "model_call_limit", "Maximum model iterations reached")
        return state["status"] is not None

    async def explore_browser(self, state: ExplorationState) -> ExplorationState:
        self.latest = state
        if self.budget_exhausted():
            return state
        assert self.session is not None
        action: Action | None = None
        try:
            action = await self.next_action()
            if action is not None:
                await self.execute_action(action)
        except SafetyError as exc:
            if action is not None and state["metrics"].llm_calls > 0:
                self.record_rejected_action(action, exc)
            else:
                # Bootstrap actions are required to establish a trustworthy page state.
                self.stop("blocked", "safety_policy", str(exc))
        except ModelError as exc:
            self.record_model_error(exc)
        except ContextLimitError:
            self.stop("blocked", "context_limit", "Required context exceeds the configured budget")
        return state

    def record_model_error(self, error: ModelError) -> None:
        """Retry invalid decisions within existing budgets; never replay a browser action."""
        state = self.latest
        self.store.record_decision_event(
            {
                "event": "model_error",
                "iteration": state["metrics"].llm_calls,
                "code": error.code,
                "diagnostics": error.diagnostics,
                "usage": error.usage,
                "provider_model": error.provider_model,
            }
        )
        self.consecutive_invalid_decisions += 1
        if (
            error.code == "llm_validation_error"
            and self.consecutive_invalid_decisions <= 2
            and state["metrics"].llm_calls < state["task"].max_model_iterations
        ):
            state["action_feedback"] = (
                "Your last decision failed schema validation and executed no browser action. "
                'Return either {"done":true} or {"done":false,"action":{"name":'
                '"<available tool>","arguments":{...}}}. Never return both action and done=true. '
                "Use the existing successful actions; do not replay them. Schema diagnostics: "
                + json.dumps(error.diagnostics, ensure_ascii=False)
            )
            logger.info(
                "model_decision_rejected",
                extra={"run_id": state["run_id"], "error": True, "reason": error.code},
            )
        else:
            self.stop("failed", error.code, str(error))

    def record_rejected_action(self, action: Action, error: SafetyError) -> None:
        """Feed a denied model proposal back into the next bounded decision."""
        state = self.latest
        proposal = json.dumps(
            redact({"name": action.name, "arguments": action.arguments}),
            ensure_ascii=False,
            allow_nan=False,
        )
        if len(proposal) > 1_000:
            proposal = proposal[:988] + " [truncated]"
        message = (
            f"Executor rejected proposed browser action {proposal}. "
            f"Reason: {redact_text(str(error))} "
            "Choose a different permitted action or finish with this limitation unresolved."
        )
        if message not in state["unresolved_questions"]:
            state["unresolved_questions"].append(message)
        state["action_feedback"] = message
        self.store.record_decision_event(
            {
                "event": "action_rejected",
                "iteration": state["metrics"].llm_calls,
                "action": action.model_dump(),
                "reason": str(error),
            }
        )
        logger.info(
            "browser_action_rejected",
            extra={
                "run_id": state["run_id"],
                "action": action.name,
                "error": True,
                "reason": redact_text(str(error)),
            },
        )

    async def next_action(self) -> Action | None:
        state = self.latest
        assert self.session is not None
        if state["bootstrap_stage"] < 2:
            stage = state["bootstrap_stage"]
            state["bootstrap_stage"] += 1
            capability = "navigate" if stage == 0 else "snapshot"
            return Action(
                name=self.session.capabilities[capability],
                arguments={"url": state["task"].url} if stage == 0 else {},
            )
        if self.needs_snapshot:
            return Action(name=self.session.capabilities["snapshot"])
        state["messages"] = self.context.build(
            state["task"],
            state["current_url"],
            state["observations"],
            state["verified_behaviors"],
            state["findings"],
            state["unresolved_questions"],
            state["successful_actions"],
            state["action_feedback"],
        )
        if self.model is None:
            try:
                self.model = OpenAIDecisionModel(self.settings)
            except Exception as exc:
                raise ModelError(
                    "llm_configuration", "Cannot initialize ChatOpenAI; check OPENAI_API_KEY/model"
                ) from exc
        state["metrics"].llm_calls += 1
        usage: dict[str, int] | None = None
        try:
            response = await self.model.decide(state["messages"], tool_specs(self.session))
            usage = response.usage
        except ModelError as exc:
            usage = exc.usage
            raise
        finally:
            self.tokens.add(usage)
        # Revalidate injected as well as provider decisions at the execution boundary.
        try:
            decision: Decision = Decision.model_validate(response.decision.model_dump())
        except ValidationError as exc:
            raise ModelError(
                "llm_validation_error",
                "LLM decision failed schema validation",
                usage,
                diagnostics=validation_diagnostics(exc),
                provider_model=response.provider_model,
            ) from None
        self.consecutive_invalid_decisions = 0
        self.store.record_decision_event(
            {
                "event": "model_decision",
                "iteration": state["metrics"].llm_calls,
                "decision": decision.model_dump(),
                "usage": usage,
                "provider_model": response.provider_model,
            }
        )
        self.accept_findings(decision)
        if decision.done:
            state["status"] = "completed"
            return None
        return decision.action

    def accept_findings(self, decision: Decision) -> None:
        state = self.latest
        for behavior in decision.verified_behaviors:
            if set(behavior.evidence_ids).issubset(self.valid_evidence):
                if behavior not in state["verified_behaviors"]:
                    state["verified_behaviors"].append(behavior)
            else:
                state["unresolved_questions"].append(
                    "Model proposed a verified behavior without valid successful evidence."
                )
        existing = {finding.id: finding for finding in state["findings"]}
        for finding in decision.findings:
            valid_ids = [value for value in finding.evidence_ids if value in self.valid_evidence]
            if len(valid_ids) != len(finding.evidence_ids) or (
                finding.verification_status == "confirmed" and not finding.reproduction_steps
            ):
                finding = finding.model_copy(
                    update={"verification_status": "inconclusive", "evidence_ids": valid_ids}
                )
            existing[finding.id] = finding
        state["findings"] = list(existing.values())
        state["unresolved_questions"] = list(
            dict.fromkeys([*state["unresolved_questions"], *decision.unresolved_questions])
        )

    async def execute_action(self, action: Action) -> None:
        state = self.latest
        assert self.session is not None
        name, arguments = self.policy.authorize(
            action.name, action.arguments, self.session.capabilities
        )
        capability = next(
            key for key, tool_name in self.session.capabilities.items() if tool_name == name
        )
        inspection_key = self.inspection_key(capability, arguments)
        if inspection_key is not None and inspection_key in self.completed_inspections:
            raise SafetyError(
                f"The {capability} action already succeeded for the current page state. "
                "Use its existing observation and finish or choose a different useful action."
            )
        metrics = state["metrics"]
        metrics.browser_tool_calls += 1
        state["observed_actions"].append(name)
        raw: Any = None
        text = ""
        error = False
        try:
            async with asyncio.timeout(self.settings.tool_timeout_seconds):
                raw = await self.session.call(name, arguments)
            text = observation_text(raw)
            if isinstance(raw, dict) and raw.get("isError"):
                raise MCPError("mcp_tool_error", "Browser tool reported an error")
            if not text.strip():
                raise MCPError("empty_observation", "Browser tool returned no usable observation")
            requires_url = name in {
                self.session.capabilities["navigate"],
                self.session.capabilities["snapshot"],
            }
            state["current_url"] = self.policy.observe(text, require_url=requires_url)
            if name == self.session.capabilities["snapshot"] and not re.search(
                r"^\s*-\s+(?!Page (?:URL|Title):)\S", text, re.MULTILINE
            ):
                raise MCPError(
                    "insufficient_observation", "Accessibility snapshot contains no page content"
                )
            metrics.successful_browser_tool_calls += 1
            state["action_feedback"] = None
            if capability in {"navigate", "click", "hover", "type", "fill_form"}:
                self.page_revision += 1
                # Some MCP versions return a file link instead of inline page content.
                self.needs_snapshot = bool(
                    re.search(r"^\s*-\s+\[Snapshot\]\(", text, re.MULTILINE)
                    and not self.policy.element_labels
                )
            elif inspection_key is not None:
                self.completed_inspections.add(inspection_key)
                if capability == "snapshot":
                    self.needs_snapshot = False
        except asyncio.CancelledError:
            error = True
            text = "Browser action interrupted; execution outcome is unknown."
            raise
        except TimeoutError:
            error = True
            text = "Browser action timed out; execution outcome is unknown."
            self.stop("timeout", "tool_timeout", text)
        except SafetyError as exc:
            error = True
            self.stop("blocked", "unsafe_observation", str(exc))
        except MCPError as exc:
            error = True
            text = text or str(exc)
            status: RunStatus = "timeout" if exc.code == "tool_timeout" else "failed"
            self.stop(status, exc.code, str(exc))
        except Exception as exc:
            error = True
            text = f"Browser call failed ({type(exc).__name__}); outcome is unknown."
            self.stop("failed", "browser_execution_error", text)
        finally:
            if error:
                metrics.failed_browser_tool_calls += 1
            observation = BrowserObservation(
                id=f"obs-{metrics.browser_tool_calls:04d}",
                url=redact_text(state["current_url"] or state["task"].url),
                action_name=name,
                action_arguments=redact(arguments),
                summary=summarize_observation(text, state["task"].max_observation_chars),
                error=error,
            )
            evidence = self.store.record(observation, raw)
            state["evidence"].append(evidence)
            if not error:
                state["successful_actions"].append(
                    {
                        "capability": capability,
                        "action_name": name,
                        "arguments": redact(arguments),
                        "url": redact_text(state["current_url"] or state["task"].url),
                        "observation_id": observation.id,
                        "evidence_id": evidence.id,
                    }
                )
                if name != self.session.capabilities.get("screenshot"):
                    self.valid_evidence.add(evidence.id)
            state["observations"] = [*state["observations"], observation][
                -state["task"].max_recent_observations :
            ]
            logger.info(
                "browser_action",
                extra={"run_id": state["run_id"], "action": name, "error": error},
            )

    def inspection_key(
        self, capability: str, arguments: dict[str, Any]
    ) -> tuple[int, str, str] | None:
        """Identify observations that add no value when repeated on unchanged page state."""
        if capability == "snapshot":
            scope: dict[str, Any] = {}
        elif capability == "screenshot":
            scope = {
                "target": arguments.get("ref", arguments.get("target")),
                "fullPage": bool(arguments.get("fullPage", False)),
            }
        elif capability in {"console", "network"}:
            scope = arguments
        else:
            return None
        return (
            self.page_revision,
            capability,
            json.dumps(scope, sort_keys=True, ensure_ascii=False, allow_nan=False),
        )

    def finalize_report(self, state: ExplorationState) -> ExplorationState:
        self.latest = state
        metrics = state["metrics"]
        metrics.duration_seconds = max(0, time.monotonic() - self.started_monotonic)
        metrics.findings_count = len(state["findings"])
        metrics.verified_behaviors_count = len(state["verified_behaviors"])
        for key, count in self.tokens.counts().items():
            setattr(metrics, key, count)
        result = BrowserExplorationResult(
            run_id=state["run_id"],
            objective=state["task"].objective,
            target_url=state["task"].url,
            status=state["status"] or "failed",
            started_at=state["started_at"],
            finished_at=datetime.now(UTC),
            verified_behaviors=state["verified_behaviors"],
            findings=state["findings"],
            unresolved_questions=state["unresolved_questions"],
            evidence=state["evidence"],
            metrics=metrics,
            errors=state["errors"],
            observed_actions=state["observed_actions"],
        )
        state["result"] = BrowserExplorationResult.model_validate(
            redact(result.model_dump(mode="json"))
        )
        return state


def build_graph(
    nodes: ExplorationNodes,
) -> CompiledStateGraph[ExplorationState, None, ExplorationState, ExplorationState]:
    graph = StateGraph(ExplorationState)
    graph.add_node("prepare_context", nodes.prepare_context)
    graph.add_node("explore_browser", nodes.explore_browser)
    graph.add_node("finalize_report", nodes.finalize_report)
    graph.add_edge(START, "prepare_context")

    def route(state: ExplorationState) -> Literal["explore_browser", "finalize_report"]:
        return "finalize_report" if state["status"] is not None else "explore_browser"

    graph.add_conditional_edges("prepare_context", route)
    graph.add_conditional_edges("explore_browser", route)
    graph.add_edge("finalize_report", END)
    return graph.compile()
