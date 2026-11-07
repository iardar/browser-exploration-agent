"""Isolation enforcement and cleanup checks without Docker, sockets or provider calls."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import pytest
from evals.network_capture import deployment, fixture, isolation
from evals.network_capture.fixture import fixture_fingerprint, fixture_response
from langchain_core.tools import BaseTool

from browser_exploration.browser_tools import MCPError
from browser_exploration.config import Settings
from browser_exploration.explorer import AutonomousBrowserExplorer
from browser_exploration.model import Action, Decision, DecisionResponse
from tests.support.network_capture import NetworkReplaySession, network_task
from tests.unit.browser_exploration.fakes import FakeModel, fake_tool


@pytest.fixture
def docker_data(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    services = json.loads(deployment.COMPOSE.read_text())["services"]
    data = {
        "mcp": {
            "Id": "container",
            "Image": "image-id",
            "RestartCount": 0,
            "State": {"Running": True, "StartedAt": "start"},
            "Config": {
                "Image": services["mcp"]["image"],
                "Env": [],
                "Entrypoint": services["mcp"]["entrypoint"],
                "Cmd": services["mcp"]["command"],
            },
            "HostConfig": {"ReadonlyRootfs": True},
            "Mounts": [],
            "NetworkSettings": {"Networks": {"internal": {"Aliases": ["mcp"]}}},
        },
        "gateway": {
            "Id": "gateway",
            "State": {"Running": True, "StartedAt": "start"},
            "Config": {
                "Image": services["gateway"]["image"],
                "Entrypoint": services["gateway"]["entrypoint"],
                "Cmd": services["gateway"]["command"],
            },
            "HostConfig": {"ReadonlyRootfs": True},
            "Mounts": [],
            "NetworkSettings": {
                "Networks": {"internal": {}, "ingress": {}},
                "Ports": {
                    f"{port}/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(port)}]
                    for port in (8932, 8766)
                },
            },
        },
        "fixture": {
            "Id": "fixture",
            "State": {"Running": True, "StartedAt": "start"},
            "Config": {
                "Image": services["fixture"]["image"],
                "Cmd": services["fixture"]["command"],
            },
            "HostConfig": {"NetworkMode": "container:container"},
        },
        "network": {"Internal": True, "Driver": "bridge", "Id": "network-id"},
    }

    def docker(*args: str) -> Any:
        if args[0] == "network":
            return [data["network"]]
        key = next((name for name in ("gateway", "fixture") if name in args[-1]), "mcp")
        return [data[key]]

    monkeypatch.setattr(deployment, "_docker", docker)
    return data


def inspect() -> dict[str, Any]:
    return deployment.inspect_deployment(
        deployment.DEFAULT_CONTAINER, deployment.DEFAULT_MCP_URL, "http://127.0.0.1:8766"
    )


def test_dedicated_deployment_records_verified_identity(docker_data: dict[str, Any]) -> None:
    """Verify dedicated deployment records verified identity.

    Arrange: Use `docker_data` parameter.
    Act: Call `inspect` to obtain `record`.
    Assert: Check `record['isolated'] is True and record['shared_context'] is False`;
            `record['image'].endswith()`; `record['gateway_id'] == 'gateway' and
            record['network_id'] == 'network-id'`.
    """
    record = inspect()
    assert record["isolated"] is True and record["shared_context"] is False
    assert record["image"].endswith(
        "690763cbb437ee7233f538a953bb5390bf03791eb5c965b471e59d17a6132fcb"
    )
    assert record["gateway_id"] == "gateway" and record["network_id"] == "network-id"


@pytest.mark.parametrize(
    "fault",
    [
        "image",
        "shared",
        "storage",
        "mount",
        "network",
        "stopped",
        "readonly",
        "endpoint",
        "proxy",
        "proxy_network",
        "fixture_network",
        "missing",
    ],
)
def test_unverified_deployments_fail_closed(docker_data: dict[str, Any], fault: str) -> None:
    """Verify unverified deployments fail closed.

    Arrange: Use `docker_data`, `fault` parameters; prepare `item`.
    Act: Call `inspect` under `pytest.raises(deployment.IsolationError)`.
    Assert: Check `raises deployment.IsolationError`.
    """
    item = docker_data["mcp"]
    if fault == "image":
        item["Config"]["Image"] = "mcp:latest"
    elif fault == "shared":
        item["Config"]["Cmd"] = [*item["Config"]["Cmd"], "--shared-browser-context"]
    elif fault == "storage":
        item["Config"]["Env"] = ["PLAYWRIGHT_MCP_STORAGE_STATE=/secret"]
    elif fault == "mount":
        item["Mounts"] = [{"Type": "bind"}]
    elif fault == "network":
        docker_data["network"]["Internal"] = False
    elif fault == "stopped":
        item["State"]["Running"] = False
    elif fault == "readonly":
        item["HostConfig"]["ReadonlyRootfs"] = False
    elif fault == "endpoint":
        docker_data["gateway"]["NetworkSettings"]["Ports"]["8932/tcp"] = []
    elif fault == "proxy":
        docker_data["gateway"]["Config"]["Cmd"] = ["unrestricted-proxy"]
    elif fault == "proxy_network":
        docker_data["gateway"]["NetworkSettings"]["Networks"] = {}
    elif fault == "fixture_network":
        docker_data["fixture"]["HostConfig"]["NetworkMode"] = "host"
    else:
        del item["State"]
    with pytest.raises(deployment.IsolationError):
        inspect()


def test_fixture_probe_routes_are_fixed_and_do_not_change_the_oracle() -> None:
    """Verify fixture probe routes are fixed and do not change the oracle.

    Arrange: Start with `'a' * 32`.
    Act: Evaluate `token.encode` in the assertion.
    Assert: Check `fixture_response(f'/__eval__/traffic/{token}')[2] == token.encode()`;
            `fixture_response('/__eval__/seed/<script>')[0] == 404`; `b'Isolation' not in
            fixture_response('/cases/NET-001/')[2]`; `b'localStorage' in body and b'indexedDB' in
            body and (b'caches' in body)`.
    """
    token = "a" * 32
    for mode in ("seed", "check"):
        code, mime, body = fixture_response(f"/__eval__/{mode}/{token}")
        assert code == 200 and "text/html" in mime
        assert token.encode() in body and fixture_fingerprint().encode() in body
        assert b"localStorage" in body and b"indexedDB" in body and b"caches" in body
    assert fixture_response(f"/__eval__/traffic/{token}")[2] == token.encode()
    assert fixture_response("/__eval__/seed/<script>")[0] == 404
    assert b"Isolation" not in fixture_response("/cases/NET-001/")[2]


def test_running_fixture_does_not_claim_to_have_reloaded_edited_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify running fixture does not claim to have reloaded edited sources.

    Arrange: Use `monkeypatch` parameter.
    Act: Evaluate `loaded.encode` in the assertion.
    Assert: Check `loaded.encode() in body and b'edited-on-disk' not in body`.
    """
    loaded = fixture_fingerprint()
    monkeypatch.setattr(fixture, "fixture_fingerprint", lambda: "edited-on-disk")
    body = fixture_response(f"/__eval__/check/{'a' * 32}")[2]
    assert loaded.encode() in body and b"edited-on-disk" not in body


class ProbeSession:
    def __init__(self, storage: dict[str, Any], *, leak_history: bool = False) -> None:
        tools: list[BaseTool] = [
            fake_tool("browser_navigate", {"url": {"type": "string"}}),
            fake_tool("browser_snapshot"),
            fake_tool("browser_network_requests", {"static": {"type": "boolean"}}),
        ]
        self.tools, self.capabilities = isolation.discovered_tools(tools)
        self.storage = storage
        self.leak_history = leak_history
        self.history: set[str] = set()
        self.text = ""
        self.calls: list[str] = []

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(name)
        if name == "browser_navigate":
            url = args["url"]
            mode, token = urlsplit(url).path.split("/")[-2:]
            self.history.add(url)
            label = "dirty" if self.storage else "clean"
            if mode == "seed":
                self.storage["marker"] = token
                self.history.add(url.replace("/seed/", "/traffic/"))
                label = "seeded"
            self.text = (
                f'- Page URL: {url}\n- heading "Isolation {label} {token} {fixture_fingerprint()}"'
            )
        if name == "browser_network_requests":
            history = self.history | (
                {"http://fixture.example/__eval__/seed/stale"} if self.leak_history else set()
            )
            return {
                "content": [
                    {"type": "text", "text": "\n".join(f"[GET] {url} => [200]" for url in history)}
                ]
            }
        return {"content": [{"type": "text", "text": self.text}]}


@pytest.fixture
def owner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> isolation.TrialIsolation:
    settings = Settings(
        mcp_url=deployment.DEFAULT_MCP_URL,
        output_dir=tmp_path,
        network_restricted=True,
        allow_private_network=True,
    )
    result = isolation.TrialIsolation(settings, "http://fixture.example", "container", timeout=0.2)
    monkeypatch.setattr(
        isolation, "inspect_deployment", lambda *args: {"container_id": "container"}
    )
    monkeypatch.setattr(
        isolation,
        "deployment_versions",
        lambda *args: {"mcp": "1", "browser": "2", "playwright": "3"},
    )
    return result


def install_probes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    shared: bool = False,
    leak_history: bool = False,
    close_failure: bool = False,
) -> list[ProbeSession]:
    sessions: list[ProbeSession] = []
    storage: dict[str, Any] = {}

    @asynccontextmanager
    async def open_session(
        settings: Settings, record: dict[str, Any], timeout: float
    ) -> AsyncIterator[ProbeSession]:
        session = ProbeSession(storage if shared else {}, leak_history=leak_history)
        sessions.append(session)
        try:
            yield session
        finally:
            record["cleanup"] = "failed" if close_failure else "passed"
            if close_failure:
                raise deployment.IsolationError("close failed")

    monkeypatch.setattr(isolation, "open_session", open_session)
    return sessions


async def test_probes_verify_normal_and_interrupted_sessions_with_private_records(
    monkeypatch: pytest.MonkeyPatch,
    owner: isolation.TrialIsolation,
) -> None:
    """Verify probes verify normal and interrupted sessions with private records.

    Arrange: Use `monkeypatch`, `owner` parameters; prepare `sessions`.
    Act: Call `json.loads` to obtain `report`.
    Assert: Check `owner.verified and (not owner.failed) and (len(sessions) == 4)`;
            `report['status'] == 'passed'`; `[p['label'] for p in report['probes']] equals values
            'seed', 'concurrent_clean', 'interrupted_seed'`; `(owner.directory /
            'preflight.json').stat().st_mode & 511 == 384`.
    """
    sessions = install_probes(monkeypatch)
    await owner.prepare()
    assert owner.verified and not owner.failed and len(sessions) == 4
    report = json.loads((owner.directory / "preflight.json").read_text())
    assert report["status"] == "passed"
    assert [p["label"] for p in report["probes"]] == [
        "seed",
        "concurrent_clean",
        "interrupted_seed",
        "after_interruption_clean",
    ]
    assert all(p["cleanup"] == "passed" for p in report["probes"])
    assert (owner.directory / "preflight.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("fault", ["storage", "history", "cleanup"])
async def test_probe_failure_prevents_trials(
    monkeypatch: pytest.MonkeyPatch, owner: isolation.TrialIsolation, fault: str
) -> None:
    """Verify probe failure prevents trials.

    Arrange: Use `monkeypatch`, `owner`, `fault` parameters.
    Act: Call `owner.prepare` under `pytest.raises(deployment.IsolationError)`.
    Assert: Check `raises deployment.IsolationError`; `owner.failed and (not owner.verified)`;
            `json.loads((owner.directory / 'preflight.json').read_text())['status'] == 'failed'`;
            `connection.record['status'] == 'not_started'`.
    """
    install_probes(
        monkeypatch,
        shared=fault == "storage",
        leak_history=fault == "history",
        close_failure=fault == "cleanup",
    )
    with pytest.raises(deployment.IsolationError):
        await owner.prepare()
    assert owner.failed and not owner.verified
    assert json.loads((owner.directory / "preflight.json").read_text())["status"] == "failed"
    connection = owner.connection("NET-001", network_task("NET-001").url)
    with pytest.raises(MCPError, match="unavailable"):
        async with connection.connect():
            pytest.fail("No unverified browser session should be yielded")
    assert connection.record["status"] == "not_started"


@pytest.mark.parametrize("fault", ["attestation", "private_access", "fixture_drift"])
async def test_prerequisite_failure_never_opens_a_browser(
    monkeypatch: pytest.MonkeyPatch, owner: isolation.TrialIsolation, fault: str
) -> None:
    """Verify prerequisite failure never opens a browser.

    Arrange: Use `monkeypatch`, `owner`, `fault` parameters; prepare `sessions`.
    Act: Call `owner.prepare` under `pytest.raises(deployment.IsolationError)`.
    Assert: Check `raises deployment.IsolationError`; `not sessions and owner.failed and (not
            owner.verified)`.
    """
    sessions = install_probes(monkeypatch)
    if fault == "attestation":
        owner.settings.network_restricted = False
    elif fault == "private_access":
        owner.settings.allow_private_network = False
    else:
        monkeypatch.setattr(isolation, "fixture_fingerprint", lambda: "changed")
    with pytest.raises(deployment.IsolationError):
        await owner.prepare()
    assert not sessions and owner.failed and not owner.verified


async def test_preflight_cannot_be_reused(
    monkeypatch: pytest.MonkeyPatch, owner: isolation.TrialIsolation
) -> None:
    """Verify preflight cannot be reused.

    Arrange: Use `monkeypatch`, `owner` parameters; prepare `sessions`, `saved`.
    Act: Call `owner.prepare` under `pytest.raises(deployment.IsolationError)`.
    Assert: Check `raises deployment.IsolationError`; `len(sessions) == 4 and (owner.directory /
            'preflight.json').read_text() == saved`.
    """
    sessions = install_probes(monkeypatch)
    await owner.prepare()
    saved = (owner.directory / "preflight.json").read_text()
    with pytest.raises(deployment.IsolationError, match="new isolation owner"):
        await owner.prepare()
    assert len(sessions) == 4 and (owner.directory / "preflight.json").read_text() == saved


class ReadyReplay(NetworkReplaySession):
    async def call(self, name: str, args: dict[str, Any]) -> Any:
        response = await super().call(name, args)
        if name in {"browser_navigate", "browser_snapshot"}:
            response[0]["text"] += (
                f'\n- heading "Network capture fixture {self.case_id}"'
                '\n- heading "Network fixture ready"'
            )
        return response


def install_trial(
    monkeypatch: pytest.MonkeyPatch, owner: isolation.TrialIsolation, *, close_failure: bool = False
) -> list[ReadyReplay]:
    owner.verified = True
    owner.deployment = {"container_id": "container"}
    sessions: list[ReadyReplay] = []

    @asynccontextmanager
    async def opened(
        settings: Settings, record: dict[str, Any], timeout: float
    ) -> AsyncIterator[ReadyReplay]:
        session = ReadyReplay("NET-001")
        sessions.append(session)
        try:
            yield session
        finally:
            record["cleanup"] = "failed" if close_failure else "passed"
            if close_failure:
                raise deployment.IsolationError("private-secret")

    monkeypatch.setattr(isolation, "open_session", opened)
    return sessions


async def test_trial_uses_only_agent_actions_and_saves_cleanup(
    monkeypatch: pytest.MonkeyPatch, owner: isolation.TrialIsolation
) -> None:
    """Verify trial uses only agent actions and saves cleanup.

    Arrange: Use `monkeypatch`, `owner` parameters; prepare `sessions`, `model`, `connection`.
    Act: Await `AutonomousBrowserExplorer.explore` to obtain `result`.
    Assert: Check `result.status == 'completed' and result.metrics.browser_tool_calls == 3`; `[name
            for name, _ in sessions[0].calls] equals values 'browser_navigate', 'browser_snapshot',
            'browser_network_requests'`; `connection.record['readiness'] == 'ready' and
            connection.record['cleanup'] == 'passed'`; `connection.path.read_text() == saved`.
    """
    sessions = install_trial(monkeypatch, owner)
    model = FakeModel(
        [
            DecisionResponse(
                Decision(action=Action(name="browser_network_requests", arguments={"static": True}))
            ),
            DecisionResponse(Decision(done=True)),
        ]
    )
    connection = owner.connection("NET-001", network_task("NET-001").url)
    result = await AutonomousBrowserExplorer(
        owner.settings, connection=connection, model=model
    ).explore(network_task("NET-001"))
    assert result.status == "completed" and result.metrics.browser_tool_calls == 3
    assert [name for name, _ in sessions[0].calls] == [
        "browser_navigate",
        "browser_snapshot",
        "browser_network_requests",
    ]
    assert connection.record["readiness"] == "ready" and connection.record["cleanup"] == "passed"
    saved = connection.path.read_text()
    with pytest.raises(MCPError):
        async with connection.connect():
            pytest.fail("Connection cannot be reused")
    assert connection.path.read_text() == saved


async def test_cleanup_failure_stops_later_trials(
    monkeypatch: pytest.MonkeyPatch, owner: isolation.TrialIsolation
) -> None:
    """Verify cleanup failure stops later trials.

    Arrange: Use `monkeypatch`, `owner` parameters; prepare `sessions`, `first`.
    Act: Await `AutonomousBrowserExplorer.explore` to obtain `result`.
    Assert: Check `result.status == 'failed' and owner.failed`; `raises MCPError`; `len(sessions) ==
            1 and 'private-secret' not in first.path.read_text()`.
    """
    sessions = install_trial(monkeypatch, owner, close_failure=True)
    first = owner.connection("NET-001", network_task("NET-001").url)
    result = await AutonomousBrowserExplorer(
        owner.settings, connection=first, model=FakeModel()
    ).explore(network_task("NET-001"))
    assert result.status == "failed" and owner.failed
    second = owner.connection("NET-001", network_task("NET-001").url)
    with pytest.raises(MCPError):
        async with second.connect():
            pytest.fail("No trial after failed cleanup")
    assert len(sessions) == 1 and "private-secret" not in first.path.read_text()


async def test_model_cancellation_cleans_trial_session(
    monkeypatch: pytest.MonkeyPatch, owner: isolation.TrialIsolation
) -> None:
    """Verify model cancellation cleans trial session.

    Arrange: Use `monkeypatch`, `owner` parameters.
    Act: Cancel the trial while its model is blocked, then await the run and inspect cleanup.
    Assert: Check `result.status == 'cancelled' and connection.record['cleanup'] == 'passed'`;
            `connection.record['status'] == 'cancelled' and (not owner.active)`.
    """
    install_trial(monkeypatch, owner)
    model = FakeModel(blocked=True)
    connection = owner.connection("NET-001", network_task("NET-001").url)
    work = asyncio.create_task(
        AutonomousBrowserExplorer(owner.settings, connection=connection, model=model).explore(
            network_task("NET-001")
        )
    )
    await model.started.wait()
    work.cancel()
    result = await work
    assert result.status == "cancelled" and connection.record["cleanup"] == "passed"
    assert connection.record["status"] == "cancelled" and not owner.active


async def test_deployment_restart_blocks_model_calls(
    monkeypatch: pytest.MonkeyPatch, owner: isolation.TrialIsolation
) -> None:
    """Verify deployment restart blocks model calls.

    Arrange: Use `monkeypatch`, `owner` parameters; prepare `sessions`, `model`, `connection`.
    Act: Await `AutonomousBrowserExplorer.explore` to obtain `result`.
    Assert: Check `result.status == 'failed' and (not model.requests) and (not sessions)`.
    """
    sessions = install_trial(monkeypatch, owner)
    monkeypatch.setattr(
        isolation, "inspect_deployment", lambda *args: {"container_id": "replacement"}
    )
    model = FakeModel()
    connection = owner.connection("NET-001", network_task("NET-001").url)
    result = await AutonomousBrowserExplorer(
        owner.settings, connection=connection, model=model
    ).explore(network_task("NET-001"))
    assert result.status == "failed" and not model.requests and not sessions


async def test_concurrent_trial_is_rejected_without_closing_the_active_one(
    monkeypatch: pytest.MonkeyPatch, owner: isolation.TrialIsolation
) -> None:
    """Verify concurrent trial is rejected without closing the active one.

    Arrange: Use `monkeypatch`, `owner` parameters.
    Act: Call `install_trial` to obtain `sessions`.
    Assert: Check `len(sessions) == 1 and first.record['cleanup'] == 'passed'`; `raises MCPError`;
            `owner.active and (not owner.failed)`.
    """
    sessions = install_trial(monkeypatch, owner)
    first = owner.connection("NET-001", network_task("NET-001").url)
    second = owner.connection("NET-001", network_task("NET-001").url)
    async with first.connect() as session:
        await session.call("browser_navigate", {"url": network_task("NET-001").url})
        with pytest.raises(MCPError):
            async with second.connect():
                pytest.fail("Concurrent trials must not share an evaluation owner")
        assert owner.active and not owner.failed
    assert len(sessions) == 1 and first.record["cleanup"] == "passed"


async def test_readiness_waits_for_normal_snapshot_without_hidden_calls() -> None:
    """Verify readiness waits for normal snapshot without hidden calls.

    Arrange: prepare `session`.
    Act: Await `session.call` to obtain `ready`.
    Assert: Check `await wrapped.call('browser_navigate', {'url': session.url}) == pending`;
            `record['readiness'] == 'pending'`; `await wrapped.call('browser_snapshot', {}) ==
            ready`; `record['readiness'] == 'ready' and session.call.call_count == 2`.
    """
    session = ReadyReplay("NET-001")
    ready = await session.call("browser_snapshot", {})
    pending = [{"type": "text", "text": f"- Page URL: {session.url}\nLoading"}]
    session.call = AsyncMock(side_effect=[pending, ready])  # type: ignore[method-assign]
    record = {"readiness": "pending"}
    wrapped = isolation.ReadySession(session, session.url, "NET-001", record)
    assert await wrapped.call("browser_navigate", {"url": session.url}) == pending
    assert record["readiness"] == "pending"
    assert await wrapped.call("browser_snapshot", {}) == ready
    assert record["readiness"] == "ready" and session.call.call_count == 2


@pytest.mark.parametrize("fault", ["marker", "url", "case", "tool_error"])
async def test_readiness_failure_prevents_capture(fault: str) -> None:
    """Verify readiness failure prevents capture.

    Arrange: Use `fault` parameter; prepare `session`, `raw`, `record`, `wrapped`.
    Act: Call `wrapped.call` under `pytest.raises(MCPError)`.
    Assert: Check `raises MCPError`; `record['readiness'] == 'failed'`; `session.call.call_count ==
            1`.
    """
    session = ReadyReplay("NET-001")
    raw = await session.call("browser_snapshot", {})
    if fault == "tool_error":
        raw = {"isError": True, "content": raw}
    else:
        source, target = {
            "marker": ("Network fixture ready", "pending"),
            "url": (session.url, "https://foreign.example"),
            "case": ("NET-001", "NET-002"),
        }[fault]
        raw[0]["text"] = raw[0]["text"].replace(source, target)
    session.call = AsyncMock(return_value=raw)  # type: ignore[method-assign]
    record = {"readiness": "pending"}
    wrapped = isolation.ReadySession(session, session.url, "NET-001", record)
    with pytest.raises(MCPError):
        await wrapped.call("browser_snapshot", {})
    assert record["readiness"] == "failed"
    with pytest.raises(MCPError):
        await wrapped.call("browser_network_requests", {"static": True})
    assert session.call.call_count == 1


@pytest.mark.parametrize(
    "failure", [None, "initialize", "close", "cancel", "timeout", "close_timeout", "transport"]
)
async def test_sdk_lifecycle_closes_in_same_task(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """Verify sdk lifecycle closes in same task.

    Arrange: Use `monkeypatch`, `failure` parameters; prepare `events`.
    Act: Evaluate `asyncio.current_task` in the assertion.
    Assert: Check `events[-2:] == ['session_exit', 'transport_exit']`; `record['cleanup'] equals
            values 'failed', 'passed', 'close'`; `('browser_close' in events) == (failure not in
            {'initialize', 'timeout'})`; `'browser_close' not in session.tools`.
    """
    events: list[str] = []
    creator = asyncio.current_task()

    @asynccontextmanager
    async def transport(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        events.append("transport_enter")
        try:
            yield None, None, lambda: "session-id"
        finally:
            assert asyncio.current_task() is creator
            events.append("transport_exit")
            if failure == "transport":
                raise RuntimeError("secret")

    class Raw:
        async def __aenter__(self) -> Any:
            events.append("session_enter")
            return self

        async def __aexit__(self, *args: Any) -> None:
            assert asyncio.current_task() is creator
            events.append("session_exit")

        async def initialize(self) -> Any:
            if failure == "initialize":
                raise RuntimeError("secret")
            if failure == "timeout":
                await asyncio.Event().wait()
            return SimpleNamespace(
                serverInfo=SimpleNamespace(
                    model_dump=lambda **kwargs: {"name": "test", "version": "1"}
                )
            )

        async def call_tool(self, name: str, args: Any) -> Any:
            assert name == "browser_close"
            events.append("browser_close")
            if failure == "close_timeout":
                await asyncio.Event().wait()
            return SimpleNamespace(isError=failure == "close")

    monkeypatch.setattr(isolation, "streamable_http_client", transport)
    monkeypatch.setattr(isolation, "ClientSession", lambda *args, **kwargs: Raw())
    monkeypatch.setattr(
        isolation,
        "load_mcp_tools",
        AsyncMock(
            return_value=[
                fake_tool("browser_navigate", {"url": {"type": "string"}}),
                fake_tool("browser_snapshot"),
                fake_tool("browser_network_requests", {"static": {"type": "boolean"}}),
                fake_tool("browser_close"),
            ]
        ),
    )
    record: dict[str, Any] = {}

    async def run() -> None:
        async with isolation.open_session(Settings(), record, 0.05) as session:
            assert "browser_close" not in session.tools
            if failure == "cancel":
                raise asyncio.CancelledError()

    if failure:
        expected = {
            "initialize": RuntimeError,
            "timeout": TimeoutError,
            "close": deployment.IsolationError,
            "cancel": asyncio.CancelledError,
            "close_timeout": deployment.IsolationError,
            "transport": deployment.IsolationError,
        }[failure]
        with pytest.raises(expected):
            await run()
    else:
        await run()
    assert events[-2:] == ["session_exit", "transport_exit"]
    assert record["cleanup"] == (
        "failed" if failure in {"close", "close_timeout", "transport"} else "passed"
    )
    assert ("browser_close" in events) == (failure not in {"initialize", "timeout"})
