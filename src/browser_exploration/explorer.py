"""Independent application service with one persistent MCP session per run."""

from __future__ import annotations

import asyncio
import logging
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from langsmith import tracing_context
from pydantic import ValidationError

from .config import Settings
from .contracts import BrowserExplorationResult, BrowserExplorationTask, ExecutionMetrics
from .graph import ExplorationNodes, build_graph
from .mcp_client import BrowserSession, MCPConnection, MCPError
from .model import DecisionModel
from .reporting import ArtifactStore
from .state import ExplorationState

logger = logging.getLogger(__name__)


class BrowserConnection(Protocol):
    def connect(self) -> AbstractAsyncContextManager[BrowserSession]: ...


class AutonomousBrowserExplorer:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        connection: BrowserConnection | None = None,
        model: DecisionModel | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.connection = connection or MCPConnection(self.settings)
        self.model = model

    async def explore(self, task: BrowserExplorationTask) -> BrowserExplorationResult:
        # Copy task: neither caller nor model can change this run's budgets in place.
        task = BrowserExplorationTask.model_validate(task.model_dump())
        run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid4().hex[:12]}"
        store = ArtifactStore(self.settings.output_dir, run_id, self.settings.retain_raw_evidence)
        store.save_run_config(
            task,
            self.settings.model,
            type(self.model).__name__ if self.model is not None else "OpenAIDecisionModel",
            reasoning_effort=self.settings.effective_reasoning_effort,
            model_api=self.settings.effective_model_api,
        )
        logger.info(
            "browser_run_started",
            extra={
                "run_id": run_id,
                "model": self.settings.model,
                "reasoning_effort": self.settings.effective_reasoning_effort,
                "model_api": self.settings.effective_model_api,
            },
        )
        state: ExplorationState = {
            "task": task,
            "run_id": run_id,
            "started_at": datetime.now(UTC),
            "current_url": "",
            "messages": [],
            "observations": [],
            "verified_behaviors": [],
            "findings": [],
            "unresolved_questions": [],
            "evidence": [],
            "metrics": ExecutionMetrics(),
            "status": None,
            "errors": [],
            "observed_actions": [],
            "successful_actions": [],
            "action_feedback": None,
            "bootstrap_stage": 0,
            "result": None,
        }
        nodes = ExplorationNodes(self.settings, store, state, self.model)
        graph = build_graph(nodes)
        # Validate before connecting, and again in the observable prepare_context node.
        nodes.prepare_context(state)
        try:
            if state["status"] is None:
                with tracing_context(enabled=self.settings.langsmith_enabled):
                    async with asyncio.timeout(task.max_duration_seconds):
                        async with self.connection.connect() as session:
                            nodes.session = session
                            await graph.ainvoke(
                                state,
                                config={
                                    "recursion_limit": task.max_steps
                                    + task.max_model_iterations
                                    + 8,
                                    "run_name": "browser_exploration",
                                },
                            )
        except TimeoutError:
            nodes.stop("timeout", "execution_timeout", "Maximum run duration reached")
        except asyncio.CancelledError:
            # Deliberate API contract: cancellation is a terminal result, persisted for the caller.
            nodes.stop(
                "cancelled", "cancelled", "Exploration was cancelled; actions may be partial"
            )
        except MCPError as exc:
            nodes.stop("failed", exc.code, str(exc))
        except ValidationError:
            nodes.stop("failed", "llm_validation_error", "Decision did not match its schema")
        except Exception as exc:
            # Persist operational failures, without raw exception text that may contain secrets.
            nodes.stop("failed", "unexpected_error", f"Exploration failed ({type(exc).__name__})")
        nodes.finalize_report(nodes.latest)
        result = nodes.latest["result"]
        assert result is not None
        store.save_result(result)
        return result
