"""Evaluation-only session ownership, isolation probes, readiness and private setup records."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from browser_exploration.browser_tools import (
    BrowserSession,
    MCPError,
    argument_schema,
    discovered_tools,
)
from browser_exploration.config import Settings
from browser_exploration.graph import observation_text

from .deployment import IsolationError, deployment_versions, inspect_deployment
from .fixture import fixture_fingerprint
from .grade import _parse_requests


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    temporary.touch(mode=0o600, exist_ok=True)
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(path)


class Session:
    """Expose the finite agent catalog; browser closure stays with the evaluator."""

    def __init__(self, raw: ClientSession, tools: list[Any]) -> None:
        self.raw = raw
        self.tools, self.capabilities = discovered_tools(tools)
        if "network" not in self.capabilities:
            raise IsolationError("MCP server lacks network capture.")
        self.close_tool = next(
            (
                tool.name
                for tool in tools
                if tool.name in {"browser_close", "playwright_browser_close"}
            ),
            None,
        )
        if self.close_tool is None:
            raise IsolationError("MCP server lacks evaluator-owned browser closure.")

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        if name not in self.tools:
            raise MCPError("unsupported_tool", "Tool is outside the agent catalog.")
        try:
            return (await self.raw.call_tool(name, args)).model_dump(mode="json")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise MCPError(
                "tool_execution_error", f"Evaluation MCP call failed ({type(exc).__name__})."
            ) from None

    async def close_browser(self) -> None:
        assert self.close_tool is not None
        result = await self.raw.call_tool(self.close_tool, {})
        if result.isError:
            raise IsolationError("MCP browser closure failed.")


@asynccontextmanager
async def open_session(
    settings: Settings, record: dict[str, Any], timeout: float
) -> AsyncIterator[Session]:
    """Enter and exit SDK task groups in the same task, including cancellation paths."""
    stack = AsyncExitStack()
    session: Session | None = None
    cleanup_failed = False
    try:
        async with asyncio.timeout(timeout):
            client = await stack.enter_async_context(
                httpx.AsyncClient(timeout=settings.tool_timeout_seconds, trust_env=False)
            )
            read, write, session_id = await stack.enter_async_context(
                streamable_http_client(
                    settings.mcp_url, http_client=client, terminate_on_close=True
                )
            )
            raw = await stack.enter_async_context(
                ClientSession(
                    read,
                    write,
                    read_timeout_seconds=timedelta(seconds=settings.tool_timeout_seconds),
                )
            )
            initialized = await raw.initialize()
            record["server"] = initialized.serverInfo.model_dump(mode="json")
            identifier = session_id()
            record["session_sha256"] = (
                hashlib.sha256(identifier.encode()).hexdigest() if identifier else None
            )
            session = Session(raw, await load_mcp_tools(raw))
        yield session
    finally:
        started = time.monotonic()
        # asyncio deadlines do not introduce AnyIO cancel scopes around SDK scope exits.
        # In particular, do not shield aclose in a different asyncio task.
        record["cleanup"] = "running"
        if session is not None:
            try:
                async with asyncio.timeout(timeout):
                    await session.close_browser()
                record["browser_closed"] = True
            except BaseException as exc:
                cleanup_failed = True
                record["close_error_type"] = type(exc).__name__
        try:
            async with asyncio.timeout(timeout):
                await stack.aclose()
        except BaseException as exc:
            cleanup_failed = True
            record["transport_close_error_type"] = type(exc).__name__
        record["cleanup_seconds"] = time.monotonic() - started
        record["cleanup"] = "failed" if cleanup_failed else "passed"
        if cleanup_failed:
            raise IsolationError("Evaluation session cleanup failed; further trials are stopped.")


class ReadySession:
    """Check the agent's normal bootstrap responses, without adding browser actions."""

    def __init__(
        self, session: BrowserSession, url: str, case_id: str, record: dict[str, Any]
    ) -> None:
        self.session, self.url, self.case_id, self.record = session, url, case_id, record
        self.tools, self.capabilities = session.tools, session.capabilities

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        ready = self.record["readiness"] == "ready"
        if not ready and name not in {self.capabilities["navigate"], self.capabilities["snapshot"]}:
            raise MCPError("fixture_not_ready", "Fixture readiness has not been established.")
        if not ready and name == self.capabilities["navigate"] and args.get("url") != self.url:
            raise MCPError("fixture_not_ready", "Bootstrap navigation does not match the case.")
        raw = await self.session.call(name, args)
        if not ready:
            text = observation_text(raw)
            valid = (
                f"- Page URL: {self.url}" in text.splitlines()
                and f"Network capture fixture {self.case_id}" in text
                and "Network fixture ready" in text
                and not (isinstance(raw, dict) and raw.get("isError"))
            )
            if valid:
                self.record["readiness"] = "ready"
            elif name == self.capabilities["snapshot"] or (
                isinstance(raw, dict) and raw.get("isError")
            ):
                self.record["readiness"] = "failed"
                raise MCPError(
                    "fixture_not_ready", "Case snapshot does not confirm fixture readiness."
                )
        return raw


class TrialConnection:
    """One use of the core's BrowserConnection interface, with fail-closed setup records."""

    def __init__(self, owner: TrialIsolation, case_id: str, url: str) -> None:
        self.owner, self.case_id, self.url = owner, case_id, url
        self.used = False
        self.path = owner.directory / f"trial-{uuid4().hex}.json"
        self.record: dict[str, Any] = {
            "case_id": case_id,
            "status": "planned",
            "readiness": "pending",
            "cleanup": "not_started",
            "preflight": "preflight.json",
            "fixture_sha256": owner.report["fixture_sha256"],
            "deployment": owner.deployment,
            "versions": owner.versions,
            "model": owner.settings.model,
            "model_api": owner.settings.effective_model_api,
            "reasoning_effort": owner.settings.effective_reasoning_effort,
        }
        self.save()

    def save(self) -> None:
        _save(self.path, self.record)

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[BrowserSession]:
        if self.used or self.owner.active or self.owner.failed or not self.owner.verified:
            if not self.used:
                self.record["status"] = "not_started"
                self.save()
            raise MCPError(
                "isolation_unverified", "Trial isolation is unavailable or already used."
            )
        self.used = self.owner.active = True
        started = time.monotonic()
        self.record.update(status="setup", started_at=datetime.now(UTC).isoformat())
        self.save()
        try:
            await self.owner.verify_deployment()
            async with open_session(
                self.owner.settings, self.record, self.owner.timeout
            ) as session:
                self.record.update(status="running", setup_seconds=time.monotonic() - started)
                self.save()
                yield ReadySession(session, self.url, self.case_id, self.record)
            self.record["status"] = "finished"
        except asyncio.CancelledError:
            self.record["status"] = "cancelled"
            raise
        except Exception as exc:
            self.record.update(status="failed", error_type=type(exc).__name__)
            self.owner.failed = True
            raise MCPError(
                "isolation_failed", "Trial setup or cleanup failed; inspect its local record."
            ) from None
        finally:
            self.owner.active = False
            if self.record["cleanup"] != "passed" or self.record["readiness"] != "ready":
                self.owner.failed = True
            self.record.update(
                finished_at=datetime.now(UTC).isoformat(),
                elapsed_seconds=time.monotonic() - started,
            )
            self.save()


class TrialIsolation:
    """Verify a dedicated server before any model calls and own each measured session."""

    def __init__(
        self, settings: Settings, fixture_url: str, container: str, *, timeout: float = 20
    ) -> None:
        self.settings, self.fixture_url, self.container = (
            settings,
            fixture_url.rstrip("/"),
            container,
        )
        self.timeout = timeout
        self.directory = settings.output_dir / "_isolation" / uuid4().hex
        self.deployment: dict[str, Any] = {}
        self.versions: dict[str, Any] = {}
        self.verified = self.failed = self.active = False
        self.report: dict[str, Any] = {
            "status": "started",
            "probes": [],
            "fixture_sha256": fixture_fingerprint(),
        }

    async def verify_deployment(self) -> None:
        if not self.settings.network_restricted or not self.settings.allow_private_network:
            raise IsolationError(
                "Restricted networking and private fixture access must be enabled."
            )
        if fixture_fingerprint() != self.report["fixture_sha256"]:
            raise IsolationError("Fixture sources changed after preflight.")
        current = await asyncio.to_thread(
            inspect_deployment, self.container, self.settings.mcp_url, self.fixture_url
        )
        if self.deployment and current != self.deployment:
            raise IsolationError("Evaluation deployment changed after preflight.")
        self.deployment = current

    @asynccontextmanager
    async def _probe(self, label: str) -> AsyncIterator[Session]:
        record: dict[str, Any] = {"label": label, "cleanup": "not_started"}
        self.report["probes"].append(record)
        try:
            async with open_session(self.settings, record, self.timeout) as session:
                yield session
        finally:
            _save(self.directory / "preflight.json", self.report)

    async def _visit(self, session: Session, mode: str, token: str) -> None:
        url = f"{self.fixture_url}/__eval__/{mode}/{token}"
        expected = (
            f"Isolation {'seeded' if mode == 'seed' else 'clean'} {token} {fixture_fingerprint()}"
        )
        async with asyncio.timeout(self.timeout):
            response = await session.call(session.capabilities["navigate"], {"url": url})
            while expected not in observation_text(response):
                text = observation_text(response)
                if (
                    "Isolation dirty" in text
                    or "Isolation error" in text
                    or response.get("isError")
                ):
                    raise IsolationError("Browser storage isolation probe failed.")
                await asyncio.sleep(0.1)
                response = await session.call(session.capabilities["snapshot"], {})
            if f"- Page URL: {url}" not in observation_text(response).splitlines():
                raise IsolationError("Isolation probe returned the wrong page.")
            tool = session.tools[session.capabilities["network"]]
            properties = argument_schema(tool).get("properties", {})
            flag = next((key for key in ("static", "includeStatic") if key in properties), None)
            if flag is None:
                raise IsolationError("Network tool lacks a complete-traffic option.")
            payload = await session.call(session.capabilities["network"], {flag: True})
            records, unknown = _parse_requests(observation_text(payload))
            required = {("GET", url, "200")}
            if mode == "seed":
                required.add(("GET", f"{self.fixture_url}/__eval__/traffic/{token}", "200"))
            actual = {(item.method, item.url, item.outcome) for item in records}
            optional = {("GET", f"{self.fixture_url}/favicon.ico", "204")}
            if (
                payload.get("isError")
                or unknown
                or not required <= actual
                or actual - required - optional
            ):
                raise IsolationError("Network request history isolation probe failed.")

    async def prepare(self) -> None:
        """Prove live-session separation and separation after an interrupted probe."""
        if self.verified or self.failed or self.active:
            raise IsolationError("Create a new isolation owner for a new experiment.")
        started = time.monotonic()
        _save(self.directory / "preflight.json", self.report)
        try:
            await self.verify_deployment()
            self.versions = await asyncio.to_thread(
                deployment_versions, self.deployment["container_id"]
            )
            self.versions["python_clients"] = {
                name: version(name) for name in ("mcp", "langchain-mcp-adapters")
            }
            token = uuid4().hex
            async with self._probe("seed") as first:
                await self._visit(first, "seed", token)
                # Keep A alive: explicit browser_close cannot mask shared-context leakage.
                async with self._probe("concurrent_clean") as second:
                    await self._visit(second, "check", token)
            seeded = asyncio.Event()

            async def interrupted() -> None:
                async with self._probe("interrupted_seed") as session:
                    await self._visit(session, "seed", token)
                    seeded.set()
                    await asyncio.Event().wait()

            task = asyncio.create_task(interrupted())
            try:
                async with asyncio.timeout(self.timeout * 2):
                    while not seeded.is_set():
                        if task.done():
                            await task
                        await asyncio.sleep(0.05)
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            async with self._probe("after_interruption_clean") as last:
                await self._visit(last, "check", token)
            self.verified = True
            self.report["status"] = "passed"
        except BaseException as exc:
            self.failed = True
            self.report.update(status="failed", error_type=type(exc).__name__)
            if isinstance(exc, IsolationError):
                self.report["error"] = str(exc)
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise IsolationError(
                f"Isolation preflight failed; inspect {self.directory / 'preflight.json'}."
            ) from None
        finally:
            self.report.update(
                deployment=self.deployment,
                versions=self.versions,
                duration_seconds=time.monotonic() - started,
            )
            _save(self.directory / "preflight.json", self.report)

    def connection(self, case_id: str, url: str) -> TrialConnection:
        return TrialConnection(self, case_id, url)
