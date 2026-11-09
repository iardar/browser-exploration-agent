"""Run exploration through three connected steps, called graph nodes.

``prepare_context`` checks prerequisites, ``explore_browser`` repeats one bounded
turn, and ``finalize_report`` builds the result. A shared state dictionary carries
progress between them. The other functions help these nodes do their work; they
are not additional graph nodes. See Section 6.2 of ``docs/architecture.md``.
"""

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

from .browser_tools import network_detail_parts, tool_specs
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
from .observations import (
    network_detail_is_file_reference,
    network_detail_mime,
    summarize_network_observation,
    summarize_observation,
    textual_response_mime,
    visible_network_requests,
)
from .reporting import ArtifactStore, redact, redact_text
from .safety import SafetyError, SafetyPolicy
from .state import ExplorationState
from .telemetry import TokenAccumulator

logger = logging.getLogger(__name__)
_MAX_NETWORK_DETAIL_CHARS = 65_536


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
    """Hold the three node methods and the helpers used during one run.

    The model proposes a next step; this coordinator applies policy and budgets
    before calling the browser. ``latest`` keeps the most recent working state
    available to the service if execution stops partway through. It is not a saved
    checkpoint and cannot resume a run after the process exits.
    """

    def __init__(
        self,
        settings: Settings,
        store: ArtifactStore,
        state: ExplorationState,
        model: DecisionModel | None,
    ) -> None:
        """Create fresh policy, context, usage counters, and progress tracking."""
        self.settings = settings
        self.store = store
        self.latest = state
        self.model = model
        self.session: BrowserSession | None = None
        self.policy = SafetyPolicy(state["task"], settings)
        self.context = ContextBuilder()
        self.tokens = TokenAccumulator()
        self.started_monotonic = time.monotonic()
        # Only successful, non-screenshot observations can support model claims.
        self.valid_evidence: set[str] = set()
        # This counter tracks interactions, not every possible change made by page scripts.
        self.page_revision = 0
        self.completed_inspections: set[tuple[int, str, str]] = set()
        self.needs_snapshot = False
        self.consecutive_invalid_decisions = 0
        self.reference_observation_id: str | None = None
        self.network_requests: dict[int, tuple[str, str]] = {}
        self.network_listing_evidence: str | None = None
        self.verified_network_details: dict[int, str | None] = {}

    def stop(self, status: RunStatus, code: str, message: str) -> None:
        """Set a terminal status and add a diagnostic with recognized secrets removed."""
        self.latest["status"] = status
        self.latest["errors"].append(RunError(code=code, message=redact_text(message)))

    def prepare_context(self, state: ExplorationState) -> ExplorationState:
        """Node 1: check the target and required context before exploring.

        Also require the operator's confirmation that browser network controls are
        configured. This flag does not install a firewall. A failed prerequisite
        sets ``blocked`` so graph routing goes straight to the report node.
        """
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
        """Stop if a time, browser-call, or model-request limit has been reached."""
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
        """Node 2: perform at most one browser call, then return control to the graph.

        Initial turns open the target and read its accessibility snapshot. Later
        turns ask the model for one action or a finish decision. A rejected model
        action can produce feedback for the next turn without calling the browser.
        The router repeats this node while the status remains unset.
        """
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
        """Choose setup navigation, a needed snapshot, or one model-proposed action.

        The first two browser calls need no model: open the URL, then read the page.
        Later model requests use freshly bounded context and consume their own
        budget, including requests to correct an invalid decision. Return ``None``
        when the model chooses to finish; execution and authorization happen later.
        """
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
            # A response containing only a snapshot-file link leaves no inline page to read.
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
            current_references=self.policy.element_labels,
            reference_observation_id=self.reference_observation_id,
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
            available_tools = tool_specs(self.session)
            if not state["task"].allow_network_details or not self.network_requests:
                available_tools = [
                    spec for spec in available_tools if spec["capability"] != "network_detail"
                ]
            snapshot_key = self.inspection_key("snapshot", {})
            if snapshot_key in self.completed_inspections:
                # Snapshot has no scope arguments: every proposal would be a
                # rejected repeat until an interaction changes the tracked page.
                available_tools = [
                    spec
                    for spec in available_tools
                    if spec["name"] != self.session.capabilities["snapshot"]
                ]
            response = await self.model.decide(state["messages"], available_tools)
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
            # This records the model's stopping choice, not proof that the objective passed.
            state["status"] = "completed"
            return None
        return decision.action

    def accept_findings(self, decision: Decision) -> None:
        """Merge model claims after checking their evidence IDs and reproduction steps.

        Unknown or failed evidence cannot support an accepted behavior. Findings
        with unsupported IDs or missing required steps become inconclusive. These
        checks trace claims to observations; they do not prove the wording is true.
        """
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
        """Authorize one proposal, call the browser, and record what happened.

        Policy may reject the proposal or turn a link click into URL navigation.
        Accepted attempts consume browser-call budget even when the call fails.
        The final block records an observation on success or failure, provided
        storage succeeds. A timeout can leave the remote action's outcome unknown.
        """
        state = self.latest
        assert self.session is not None
        name, arguments = self.policy.authorize(
            action.name, action.arguments, self.session.capabilities
        )
        capability = next(
            key for key, tool_name in self.session.capabilities.items() if tool_name == name
        )
        request_index: int | None = None
        request_identity: tuple[str, str] | None = None
        request_part: str | None = None
        listing_evidence = self.network_listing_evidence
        if capability == "network_detail":
            request_index = arguments["index"]
            request_identity = self.network_requests.get(request_index)
            if request_identity is None or listing_evidence is None:
                raise SafetyError("Select a request index shown in the latest network listing.")
            self.policy.validate_url(request_identity[1])
            request_part = arguments.get("part")
            if request_part is not None:
                if request_part not in network_detail_parts(self.session.tools[name]):
                    raise SafetyError("The MCP server does not advertise this detail part.")
                if request_index not in self.verified_network_details:
                    raise SafetyError(
                        "Inspect the request overview before selecting a detail part."
                    )
                if request_part == "response-body" and not textual_response_mime(
                    self.verified_network_details[request_index]
                ):
                    raise SafetyError("Response body requires a verified textual content type.")
        inspection_key = self.inspection_key(capability, arguments)
        if inspection_key is not None and inspection_key in self.completed_inspections:
            raise SafetyError(
                f"The {capability} action already succeeded for the current page state. "
                "Use its existing observation and finish or choose a different useful action."
            )
        if capability in {"navigate", "click", "hover", "type", "fill_form", "network"}:
            self.network_requests.clear()
            self.network_listing_evidence = None
            self.verified_network_details.clear()
            self.completed_inspections = {
                key for key in self.completed_inspections if key[1] != "network_detail"
            }
        # Policy rejections above do not count as attempted browser calls.
        metrics = state["metrics"]
        metrics.browser_tool_calls += 1
        state["observed_actions"].append(name)
        raw: Any = None
        text = ""
        error = False
        references_refreshed = False
        previous_reference_revision = self.policy.reference_revision
        try:
            async with asyncio.timeout(self.settings.tool_timeout_seconds):
                raw = await self.session.call(name, arguments)
            text = observation_text(raw)
            if isinstance(raw, dict) and raw.get("isError"):
                raise MCPError("mcp_tool_error", "Browser tool reported an error")
            if not text.strip() and capability == "network_detail":
                error = True
                text = "The request detail is empty or unavailable."
            elif not text.strip():
                raise MCPError("empty_observation", "Browser tool returned no usable observation")
            if (
                capability == "network_detail"
                and not error
                and request_part is not None
                and (
                    network_detail_is_file_reference(text)
                    or "[binary evidence omitted from model context]" in text
                )
            ):
                error = True
                text = "The MCP server returned non-text content; its contents were not inspected."
                raw = None
            if capability == "network_detail" and not error and request_part is None:
                assert request_index is not None and request_identity is not None
                matched, mime = network_detail_mime(text, request_index, request_identity)
                if not matched:
                    error = True
                    text = "Request overview did not match the selected network listing."
                    raw = None
                else:
                    self.verified_network_details[request_index] = mime
            requires_url = name in {
                self.session.capabilities["navigate"],
                self.session.capabilities["snapshot"],
            }
            if capability != "network_detail":
                state["current_url"] = self.policy.observe(text, require_url=requires_url)
            references_refreshed = self.policy.reference_revision != previous_reference_revision
            if (
                not error
                and name == self.session.capabilities["snapshot"]
                and not re.search(r"^\s*-\s+(?!Page (?:URL|Title):)\S", text, re.MULTILINE)
            ):
                raise MCPError(
                    "insufficient_observation", "Accessibility snapshot contains no page content"
                )
            if not error:
                metrics.successful_browser_tool_calls += 1
                state["action_feedback"] = None
            if not error and capability in {"navigate", "click", "hover", "type", "fill_form"}:
                self.page_revision += 1
                # Some MCP versions return a file link instead of inline page content.
                self.needs_snapshot = bool(
                    re.search(r"^\s*-\s+\[Snapshot\]\(", text, re.MULTILINE)
                    and not self.policy.element_labels
                )
            elif not error and inspection_key is not None:
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
            # Keep a record even if the call failed or cancellation interrupted it.
            if error:
                metrics.failed_browser_tool_calls += 1
            if capability == "network_detail":
                # Detail payloads can be large and contain secrets. Retain only bounded,
                # redacted text; server-side files are never read by this application.
                text = redact_text(text)
                truncated = len(text) > _MAX_NETWORK_DETAIL_CHARS
                if truncated:
                    text = text[:_MAX_NETWORK_DETAIL_CHARS] + "\n[detail truncated]"
                raw = {"type": "text", "text": text, "truncated": truncated}
            # Keep request failures visible without adding the full listing to context.
            # Tool errors retain the usual diagnostic text; raw evidence is unchanged.
            summarize = (
                summarize_network_observation
                if capability == "network" and not error
                else summarize_observation
            )
            summary_text = text
            if capability == "network_detail" and request_index is not None:
                summary_text = (
                    f"Request #{request_index}, {request_part or 'overview'}; "
                    f"listing evidence: {listing_evidence}.\n{text}"
                )
            observation = BrowserObservation(
                id=f"obs-{metrics.browser_tool_calls:04d}",
                url=redact_text(state["current_url"] or state["task"].url),
                action_name=name,
                action_arguments=redact(arguments),
                summary=summarize(summary_text, state["task"].max_observation_chars),
                error=error,
            )
            evidence = self.store.record(observation, raw)
            state["evidence"].append(evidence)
            if not error and capability == "network":
                self.network_requests = visible_network_requests(text, observation.summary)
                if self.network_requests:
                    self.network_listing_evidence = evidence.id
            if not error:
                if references_refreshed:
                    self.reference_observation_id = observation.id
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
                    # The model sees text only, so screenshot pixels cannot verify its claims.
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
        """Make a key to reject repeat inspections of the same tracked page revision."""
        if capability == "snapshot":
            scope: dict[str, Any] = {}
        elif capability == "screenshot":
            scope = {
                "target": arguments.get("ref", arguments.get("target")),
                "fullPage": bool(arguments.get("fullPage", False)),
            }
        elif capability in {"console", "network", "network_detail"}:
            scope = arguments
        else:
            return None
        return (
            self.page_revision,
            capability,
            json.dumps(scope, sort_keys=True, ensure_ascii=False, allow_nan=False),
        )

    def finalize_report(self, state: ExplorationState) -> ExplorationState:
        """Node 3: assemble a validated result from the observations and counters.

        This method builds and redacts the result; the service writes it to disk.
        The service calls it again after session cleanup so the returned report
        also includes any error raised while closing the connection.
        """
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
    """Connect the three nodes and compile the graph that the service will await.

    Flow: START -> prepare_context -> explore_browser (repeat) -> finalize_report
    -> END. A terminal status skips further exploration, including after preflight.
    """
    graph = StateGraph(ExplorationState)
    graph.add_node("prepare_context", nodes.prepare_context)
    graph.add_node("explore_browser", nodes.explore_browser)
    graph.add_node("finalize_report", nodes.finalize_report)
    graph.add_edge(START, "prepare_context")

    def route(state: ExplorationState) -> Literal["explore_browser", "finalize_report"]:
        """Keep exploring only while no terminal status has been set."""
        return "finalize_report" if state["status"] is not None else "explore_browser"

    graph.add_conditional_edges("prepare_context", route)
    graph.add_conditional_edges("explore_browser", route)
    graph.add_edge("finalize_report", END)
    return graph.compile()
