"""Exercise real JUnit/API boundaries with an in-memory ReportPortal transport."""

import errno
import json
import socket
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from scripts import reportportal as rp

KEY = "reportportal-test-secret"
SETTINGS = rp.Settings("http://reportportal.example:8080", "system_a", KEY)
JUNIT = """<testsuites><testsuite timestamp="2026-10-25T12:00:00+00:00" time="2.5">
<testcase classname="tests.unit.example" name="passing" time="0.1" />
<testcase classname="tests.unit.example" name="failing" time="0.2">
 <failure message="Assertion failed">reportportal-test-secret</failure>
</testcase>
<testcase classname="tests.unit.example" name="setup_error" time="0.01">
 <error message="Fixture failed">fixture traceback</error>
</testcase>
<testcase classname="tests.unit.example" name="skipped" time="0">
 <skipped message="Opt in required" />
</testcase>
</testsuite></testsuites>"""


@pytest.fixture
def report(tmp_path: Path) -> rp.Report:
    path = tmp_path / "junit.xml"
    path.write_text(JUNIT)
    return rp.read_junit(path)


class FakePortal:
    """Return server-shaped responses and retain the actual HTTP requests."""

    def __init__(self, report: rp.Report, status: str | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.info = {
            "id": 42,
            "status": status or report.status,
            "endTime": report.end,
            "statistics": {"executions": report.counts},
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        path = request.url.path
        if request.method == "GET":
            return httpx.Response(200, json=self.info if "/uuid/" in path else {"content": []})
        if request.method == "POST":
            return httpx.Response(
                201, json={"id": "launch-uuid" if path.endswith("/launch") else "item-uuid"}
            )
        return httpx.Response(200, json={"message": "Finished"})


def test_junit_retains_actual_outcomes_and_durations(report: rp.Report) -> None:
    """Verify junit retains actual outcomes and durations.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for junit retains actual outcomes and durations.
    """
    assert report.counts == {"total": 4, "passed": 1, "failed": 2, "skipped": 1}
    assert report.status == "FAILED"
    assert report.end - report.start == 2500
    assert report.cases[1].end - report.cases[1].start == 200
    assert report.cases[2].status == "FAILED"
    assert report.cases[3].detail == "Opt in required"


@pytest.mark.parametrize(
    "contents",
    [
        "broken",
        "<testsuites/>",
        JUNIT.replace('time="0.1"', 'time="NaN"'),
        JUNIT.replace('time="0.1"', 'time="-1"'),
        JUNIT.replace('timestamp="2026-10-25T12:00:00+00:00"', 'timestamp="bad"'),
    ],
)
def test_bad_junit_is_rejected_before_publication(tmp_path: Path, contents: str) -> None:
    """Verify bad junit is rejected before publication.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for bad junit is rejected before publication.
    """
    path = tmp_path / "junit.xml"
    path.write_text(contents)
    with pytest.raises(rp.ReportingError, match="no launch was created"):
        rp.read_junit(path)


def test_environment_overrides_ignored_file_and_project_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify environment overrides ignored file and project defaults.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for environment overrides ignored file and project
            defaults."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\nrp_endpoint="http://default:8080"\nrp_project="default"\n'
    )
    (tmp_path / ".env.reportportal").write_text(
        f'RP_ENDPOINT="http://local:8080"\nRP_PROJECT="local"\nRP_API_KEY="{KEY}"\n'
    )
    monkeypatch.setenv("RP_ENDPOINT", "http://override:8080/")
    monkeypatch.delenv("RP_PROJECT", raising=False)
    monkeypatch.delenv("RP_API_KEY", raising=False)
    settings = rp.Settings.load(tmp_path)
    assert settings.endpoint == "http://override:8080"
    assert settings.project == "local"
    assert settings.api_key == KEY
    assert KEY not in repr(settings)


def test_effective_pytest_configuration_and_cli_are_honored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify effective pytest configuration and cli are honored.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for effective pytest configuration and cli are honored.
    """
    (tmp_path / ".env.reportportal").write_text(
        f"RP_API_KEY={KEY}\nRP_ENDPOINT=http://stale-file:8080\n"
    )
    monkeypatch.delenv("RP_ENDPOINT", raising=False)
    options = {
        "rp_endpoint": "http://ini:8080",
        "rp_project": "selected_project",
        "rp_launch": "Selected launch",
        "rp_launch_attributes": ["suite:selected"],
    }
    settings = rp.Settings.load(
        tmp_path, options=options, overrides={"rp_endpoint": "http://cli:8080"}
    )
    assert settings.endpoint == "http://cli:8080"
    assert settings.project == "selected_project" and settings.launch == "Selected launch"
    assert settings.attributes == ("suite:selected",)
    assert settings.api_key == KEY
    monkeypatch.setenv("RP_ENDPOINT", "http://environment:8080")
    assert rp.Settings.load(tmp_path, options=options).endpoint == "http://environment:8080"


@pytest.mark.parametrize(
    "endpoint",
    [
        "relative",
        "ftp://host",
        "http://user:secret@host",
        "http://host/?key=secret",
        "http://host/#secret",
    ],
)
def test_unsafe_endpoint_is_rejected_without_echoing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, endpoint: str
) -> None:
    """Verify unsafe endpoint is rejected without echoing it.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for unsafe endpoint is rejected without echoing it.
    """
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    monkeypatch.setenv("RP_ENDPOINT", endpoint)
    with pytest.raises(rp.ReportingError, match="base URL") as error:
        rp.Settings.load(tmp_path)
    assert "secret" not in str(error.value)


def test_complete_upload_verifies_remote_counts_and_redacts_key(report: rp.Report) -> None:
    """Verify complete upload verifies remote counts and redacts key.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for complete upload verifies remote counts and redacts key.
    """
    handler = FakePortal(report)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        portal = rp.Portal(SETTINGS, client)
        portal.check()
        receipt = portal.publish(report)
    assert receipt["verified"] is True
    assert receipt["counts"] == report.counts
    assert receipt["url"] == "http://reportportal.example:8080/ui/#system_a/launches/all/42"
    assert [
        json.loads(request.content)["status"]
        for request in handler.requests
        if request.method == "PUT" and "/item/" in request.url.path
    ] == [case.status for case in report.cases]
    assert KEY not in "\n".join(request.content.decode() for request in handler.requests)
    assert "[REDACTED]" in "\n".join(request.content.decode() for request in handler.requests)
    assert portal.finished and portal.active_item is None


def test_pytest_nonzero_exit_cannot_produce_a_passing_launch(report: rp.Report) -> None:
    """Verify pytest nonzero exit cannot produce a passing launch.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for pytest nonzero exit cannot produce a passing launch.
    """
    passing = rp.Report((report.cases[0],), report.start, report.end)
    handler = FakePortal(passing, status="FAILED")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        receipt = rp.Portal(SETTINGS, client).publish(passing, pytest_exit=2)
    assert receipt["status"] == "FAILED"
    assert receipt["counts"]["failed"] == 0
    assert json.loads(handler.requests[-2].content)["status"] == "FAILED"


@pytest.mark.parametrize("status", [301, 401, 403, 404, 500])
def test_api_rejection_cannot_be_reported_as_success(status: int) -> None:
    """Verify api rejection cannot be reported as success.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for api rejection cannot be reported as success.
    """

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=KEY, headers={"Location": "http://untrusted.example"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(rp.ReportingError, match=f"HTTP {status}") as error:
            rp.Portal(SETTINGS, client).check()
    assert KEY not in str(error.value)


def test_network_exception_does_not_expose_key_or_request_headers() -> None:
    """Verify network exception does not expose key or request headers.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for network exception does not expose key or request
            headers."""

    def respond(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(KEY, request=request)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(rp.ReportingError, match="Cannot reach") as error:
            rp.Portal(SETTINGS, client).check()
    assert KEY not in str(error.value)


@pytest.mark.parametrize(
    "cause, expected",
    [
        (PermissionError(errno.EPERM, "private"), "network access denied"),
        (ConnectionRefusedError(errno.ECONNREFUSED, "private"), "connection refused"),
        (socket.gaierror(-3, "private"), "DNS lookup failed"),
    ],
)
def test_connection_error_explains_cause_without_exposing_secrets(
    cause: OSError, expected: str
) -> None:
    """Verify connection error explains cause without exposing secrets.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for connection error explains cause without exposing
            secrets."""

    def respond(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(KEY, request=request) from cause

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(rp.ReportingError, match=expected) as error:
            rp.Portal(SETTINGS, client).check()
    assert KEY not in str(error.value) and "private" not in str(error.value)


def test_html_login_page_is_not_a_successful_api_response() -> None:
    """Verify html login page is not a successful api response.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for html login page is not a successful api response.
    """
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text="<html/>"))
    ) as client:
        with pytest.raises(rp.ReportingError, match="non-JSON"):
            rp.Portal(SETTINGS, client).check()


def test_partial_upload_attempts_cleanup_and_retains_launch_uuid(report: rp.Report) -> None:
    """Verify partial upload attempts cleanup and retains launch uuid.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for partial upload attempts cleanup and retains launch
            uuid."""
    handler = FakePortal(report)

    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT" and "/item/" in request.url.path:
            if json.loads(request.content)["status"] == "PASSED":
                return httpx.Response(500, json={"message": "Failed"})
        return handler(request)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        portal = rp.Portal(SETTINGS, client)
        with pytest.raises(rp.ReportingError, match="HTTP 500"):
            portal.publish(report)
    assert portal.launch_id == "launch-uuid"
    assert json.loads(handler.requests[-2].content)["status"] == "INTERRUPTED"
    assert json.loads(handler.requests[-1].content)["status"] == "FAILED"
    assert (
        sum(
            request.method == "POST" and request.url.path.endswith("/launch")
            for request in handler.requests
        )
        == 1
    )


def test_finished_status_with_wrong_counts_is_not_verified(
    report: rp.Report, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify finished status with wrong counts is not verified.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for finished status with wrong counts is not verified.
    """
    handler = FakePortal(report)
    handler.info["statistics"] = {"executions": {"total": 0}}
    ticks = iter([0.0, 0.1, 31.0, 32.0])
    monkeypatch.setattr(rp.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(rp.time, "sleep", lambda _: None)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        portal = rp.Portal(SETTINGS, client)
        portal.launch_id = "launch-uuid"
        with pytest.raises(rp.ReportingError, match="expected test counts"):
            portal.verify(report.counts, report.status)


def test_async_launch_read_polls_until_status_and_counts_match(
    report: rp.Report, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify async launch read polls until status and counts match.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for async launch read polls until status and counts match.
    """
    handler = FakePortal(report)
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(404, json={"message": "Not visible yet"})
        return handler(request)

    monkeypatch.setattr(rp.time, "sleep", lambda _: None)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        portal = rp.Portal(SETTINGS, client)
        portal.launch_id = "launch-uuid"
        assert portal.verify(report.counts, report.status)["verified"]
    assert calls == 2


@pytest.mark.parametrize(
    "statistics",
    [None, [], {"executions": None}, {"executions": {"total": True}}],
)
def test_malformed_remote_counts_cannot_be_verified(report: rp.Report, statistics: Any) -> None:
    """Verify malformed remote counts cannot be verified.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for malformed remote counts cannot be verified.
    """
    handler = FakePortal(report)
    handler.info["statistics"] = statistics
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        portal = rp.Portal(SETTINGS, client)
        with pytest.raises(rp.ReportingError, match="invalid"):
            portal.verify(report.counts, report.status)


def test_saved_junit_is_published_without_rerunning_tests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    report: rp.Report,
) -> None:
    """Verify saved junit is published without rerunning tests.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for saved junit is published without rerunning tests.
    """
    path = tmp_path / "junit.xml"
    path.write_text(JUNIT)
    handler = FakePortal(report)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(rp, "ROOT", tmp_path)
    monkeypatch.setattr(rp.Settings, "load", lambda: SETTINGS)
    monkeypatch.setattr(rp.httpx, "Client", lambda: client)
    monkeypatch.setattr(rp.subprocess, "run", lambda *args, **kwargs: pytest.fail("No rerun"))
    assert rp.main(["--junit", str(path)]) == 1
    receipt_path = next((tmp_path / "runs" / "reportportal").glob("*/reportportal.json"))
    receipt = json.loads(receipt_path.read_text())
    assert receipt["verified"] is True
    assert receipt["status"] == "FAILED"
    assert receipt["pytest_exit_code"] is None
    assert receipt["counts"] == report.counts


def test_test_process_has_no_reportportal_credentials_and_uses_canonical_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify test process has no reportportal credentials and uses canonical config.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for test process has no reportportal credentials and uses
            canonical config."""
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(command: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=1)

    monkeypatch.setenv("RP_API_KEY", KEY)
    monkeypatch.setattr(rp.subprocess, "run", run)
    assert rp.run_pytest(tmp_path / "junit.xml", ["-k", "network"]) == 1
    command, kwargs = calls[0]
    assert command[3:5] == ["-c", str(rp.ROOT / "pyproject.toml")]
    assert command[5:8] == ["tests/unit", "-n", "auto"]
    assert command[-2:] == ["-k", "network"]
    assert not any(key.startswith("RP_") for key in kwargs["env"])
    assert kwargs["env"]["QA_BROWSER_LIVE_MCP"] == "0"


@pytest.mark.parametrize("from_environment", [False, True])
def test_direct_plugin_reporting_is_rejected_before_unit_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, from_environment: bool
) -> None:
    """Verify direct plugin reporting is rejected before unit execution.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for direct plugin reporting is rejected before unit
            execution."""
    monkeypatch.setenv("PYTEST_ADDOPTS", "--reportportal" if from_environment else "")
    with pytest.raises(rp.ReportingError, match="Omit --reportportal"):
        rp.run_pytest(tmp_path / "junit.xml", [] if from_environment else ["--reportportal"])


def test_check_command_creates_private_receipt_without_a_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    report: rp.Report,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify check command creates private receipt without a launch.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for check command creates private receipt without a launch.
    """
    handler = FakePortal(report)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(rp, "ROOT", tmp_path)
    monkeypatch.setattr(rp.Settings, "load", lambda: SETTINGS)
    monkeypatch.setattr(rp.httpx, "Client", lambda: client)
    assert rp.main(["--check"]) == 0
    path = next((tmp_path / "runs" / "reportportal").glob("*/reportportal.json"))
    receipt = json.loads(path.read_text())
    assert receipt["stage"] == "access_checked"
    assert receipt["verified"] is False
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert len(handler.requests) == 1 and handler.requests[0].method == "GET"
    assert KEY not in path.read_text() + capsys.readouterr().out


def test_uploader_uses_frozen_controller_settings_without_reloading_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, report: rp.Report
) -> None:
    """Verify uploader uses frozen controller settings without reloading defaults.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for uploader uses frozen controller settings without
            reloading defaults."""
    handler = FakePortal(report)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(rp, "ROOT", tmp_path)
    monkeypatch.setattr(rp.Settings, "load", lambda: pytest.fail("Settings must stay frozen"))
    monkeypatch.setattr(rp.httpx, "Client", lambda: client)
    receipt: dict[str, Any] = {}
    assert rp.main(["--check"], settings=SETTINGS, receipt=receipt) == 0
    assert receipt["endpoint"] == SETTINGS.endpoint
    assert receipt["project"] == SETTINGS.project
    assert receipt["launches_url"] == "http://reportportal.example:8080/ui/#system_a/launches/all"


def test_unreachable_preflight_never_runs_pytest_or_claims_a_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify unreachable preflight never runs pytest or claims a launch.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for unreachable preflight never runs pytest or claims a
            launch."""

    def respond(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(KEY, request=request)

    client = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(rp, "ROOT", tmp_path)
    monkeypatch.setattr(rp.Settings, "load", lambda: SETTINGS)
    monkeypatch.setattr(rp.httpx, "Client", lambda: client)
    monkeypatch.setattr(
        rp.subprocess, "run", lambda *args, **kwargs: pytest.fail("Tests must not run")
    )
    assert rp.main([]) == 2
    path = next((tmp_path / "runs" / "reportportal").glob("*/reportportal.json"))
    receipt = json.loads(path.read_text())
    assert receipt["stage"] == "preflight" and receipt["verified"] is False
    assert receipt["launch_uuid"] is None
    assert KEY not in path.read_text() + str(capsys.readouterr())
