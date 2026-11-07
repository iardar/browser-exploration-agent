"""Prove direct reporting works with offline fixtures and one xdist controller."""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from scripts import reportportal

from tests.support import pytest_reportportal as plugin

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = reportportal.Settings("http://reportportal.example", "system_a", "fake-key")


def make_config(tmp_path: Path, **options: Any) -> pytest.Config:
    config = MagicMock(spec=pytest.Config)
    config.getoption.side_effect = lambda name, default=False: options.get(name, default)
    config.rootpath = tmp_path
    config.option = SimpleNamespace(xmlpath=None, rp_enabled=options.get("rp_enabled", False))
    config.pluginmanager = MagicMock(spec=pytest.PytestPluginManager)
    return cast(pytest.Config, config)


@pytest.mark.parametrize("mode", ["ordinary", "worker", "collectonly", "setupplan"])
def test_inactive_and_worker_processes_load_no_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    config = make_config(tmp_path, rp_enabled=mode != "ordinary", **{mode: True})
    if mode == "worker":
        config.workerinput = {}  # type: ignore[attr-defined]
    load = MagicMock(side_effect=AssertionError("Credentials must stay unread"))
    monkeypatch.setattr(reportportal.Settings, "load", load)
    plugin.pytest_configure(config)
    assert config.option.rp_enabled is False
    load.assert_not_called()
    cast(MagicMock, config.pluginmanager.register).assert_not_called()


def test_missing_credentials_are_a_usage_error_before_native_client_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path, rp_enabled=True)
    monkeypatch.setattr(
        reportportal.Settings,
        "load",
        MagicMock(side_effect=reportportal.ReportingError("Set RP_API_KEY")),
    )
    with pytest.raises(pytest.UsageError, match="RP_API_KEY"):
        plugin.pytest_configure(config)
    assert config.option.rp_enabled is False


@pytest.mark.parametrize("explicit_xml", [False, True])
def test_controller_registers_one_deferred_uploader_and_preserves_explicit_xml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, explicit_xml: bool
) -> None:
    config = make_config(tmp_path, rp_enabled=True)
    if explicit_xml:
        config.option.xmlpath = str(tmp_path / "chosen.xml")
    monkeypatch.setattr(reportportal.Settings, "load", MagicMock(return_value=SETTINGS))
    plugin.pytest_configure(config)
    assert config.option.rp_enabled is False
    register = cast(MagicMock, config.pluginmanager.register)
    register.assert_called_once()
    uploader, name = register.call_args.args
    assert name == "browser_deferred_reportportal"
    assert uploader.junit == Path(config.option.xmlpath)
    assert uploader.settings is SETTINGS
    if explicit_xml:
        assert uploader.junit == tmp_path / "chosen.xml"
    else:
        assert uploader.junit.parent.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("verified,exit_code", [(True, 0), (True, 1), (True, 2), (False, 2)])
def test_upload_failure_is_distinct_from_a_verified_failing_pytest_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verified: bool, exit_code: int
) -> None:
    def publish(
        arguments: list[str], *, receipt: dict[str, Any], settings: reportportal.Settings
    ) -> int:
        assert arguments[-2:] == ["--pytest-exit-code", str(exit_code)]
        assert settings is SETTINGS
        receipt["verified"] = verified
        return exit_code

    monkeypatch.setattr(reportportal, "main", publish)
    session = MagicMock(spec=pytest.Session)
    session.exitstatus = exit_code if verified else 0
    uploader = plugin.DeferredReportPortal(tmp_path / "junit.xml", SETTINGS)
    uploader.access_checked = True
    uploader.pytest_sessionfinish(session, exit_code)
    assert uploader.failed is not verified
    assert session.exitstatus == (exit_code if verified else pytest.ExitCode.INTERRUPTED)


def test_inaccessible_target_fails_before_test_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def check(
        arguments: list[str], *, receipt: dict[str, Any], settings: reportportal.Settings
    ) -> int:
        assert arguments == ["--check"] and settings is SETTINGS
        receipt.update(error="Connection refused", access_checked=False)
        return 2

    monkeypatch.setattr(reportportal, "main", check)
    uploader = plugin.DeferredReportPortal(tmp_path / "junit.xml", SETTINGS)
    session = MagicMock(spec=pytest.Session)
    with pytest.raises(pytest.UsageError, match="no tests ran"):
        uploader.pytest_sessionstart(session)
    assert uploader.access_checked is False
    uploader.pytest_sessionfinish(session, 4)
    assert uploader.result is None


def test_successful_access_check_does_not_claim_a_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def check(
        arguments: list[str], *, receipt: dict[str, Any], settings: reportportal.Settings
    ) -> int:
        assert arguments == ["--check"] and settings is SETTINGS
        receipt.update(access_checked=True, verified=False)
        return 0

    monkeypatch.setattr(reportportal, "main", check)
    uploader = plugin.DeferredReportPortal(tmp_path / "junit.xml", SETTINGS)
    uploader.pytest_sessionstart(MagicMock(spec=pytest.Session))
    assert uploader.access_checked is True
    assert uploader.receipt.get("verified") is None


@pytest.mark.parametrize("workers,denied", [(0, False), (2, False), (2, True)])
def test_real_pytest_direct_flag_reports_once_after_offline_tests(
    tmp_path: Path, workers: int, denied: bool
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts=["--strict-config"]\n'
        'rp_endpoint="http://reportportal.example"\nrp_project="system_a"\n'
    )
    (tmp_path / ".env.reportportal").write_text("RP_API_KEY=synthetic-reportportal-key\n")
    conftest = """
        import json
        import os
        from pathlib import Path
        from collections import Counter
        import httpx
        from scripts import reportportal as rp
        from tests.unit.conftest import offline_agent_environment

        HERE = Path(__file__).parent
        rp.ROOT = HERE
        original_load = rp.Settings.load
        def load(root=HERE, **kwargs):
            with (HERE / "loads.jsonl").open("a") as stream:
                stream.write(json.dumps({"pid": os.getpid()}) + "\\n")
            return original_load(HERE, **kwargs)
        rp.Settings.load = load
        requests = []
        counts = Counter()
        launch_status = None
        def respond(request):
            global launch_status
            assert request.headers["Authorization"] == "Bearer synthetic-reportportal-key"
            requests.append({"pid": os.getpid(), "method": request.method,
                "path": request.url.path, "host": request.url.host})
            (HERE / "requests.json").write_text(json.dumps(requests))
            if os.environ.get("REPORTING_TEST_DENY"):
                return httpx.Response(403, json={"message": "Access denied"})
            if request.method == "GET":
                if "/uuid/" not in request.url.path:
                    return httpx.Response(200, json={"content": []})
                executions = {"total": sum(counts.values()), **counts}
                return httpx.Response(200, json={"id": 42, "status": launch_status,
                    "endTime": 123, "statistics": {"executions": executions}})
            body = json.loads(request.content)
            if request.method == "POST":
                return httpx.Response(201, json={"id": "launch-uuid"
                    if request.url.path.endswith("/launch") else "item-uuid"})
            if "/item/" in request.url.path:
                counts[body["status"].lower()] += 1
            else:
                launch_status = body["status"]
            return httpx.Response(200, json={"message": "Finished"})
        original_client = httpx.Client
        rp.httpx.Client = lambda: original_client(transport=httpx.MockTransport(respond))

        def pytest_sessionstart(session):
            if not hasattr(session.config, "workerinput"):
                (HERE / "controller.pid").write_text(str(os.getpid()))
    """
    (tmp_path / "conftest.py").write_text(textwrap.dedent(conftest))
    (tmp_path / "test_cases.py").write_text(
        textwrap.dedent("""
        import os
        import socket
        from pathlib import Path
        import pytest

        def test_offline():
            import json
            (Path(__file__).parent / "test-ran").touch()
            assert socket.getaddrinfo.__module__ == "tests.unit.conftest"
            assert not os.environ.get("RP_API_KEY")
            requests = json.loads((Path(__file__).parent / "requests.json").read_text())
            assert len(requests) == 1 and requests[0]["method"] == "GET"

        def test_failure():
            assert False, "Intentional failure must reach the report"

        @pytest.mark.skip(reason="Intentional skip must reach the report")
        def test_skip():
            pass
    """)
    )
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("RP_") and name != "PYTEST_ADDOPTS"
    }
    environment["PYTHONPATH"] = os.pathsep.join((str(ROOT), str(ROOT / "src")))
    if denied:
        environment["REPORTING_TEST_DENY"] = "1"
    overrides = (
        [
            "-o",
            "rp_endpoint=http://selected.example:8080",
            "--rp-project",
            "selected_project",
            "--rp-launch",
            "Selected launch",
        ]
        if workers
        else []
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(tmp_path / "pyproject.toml"),
            "-p",
            "tests.support.pytest_reportportal",
            "-n",
            str(workers),
            "--reportportal",
            "-q",
            *overrides,
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert "INTERNALERROR" not in result.stdout + result.stderr
    if denied:
        assert result.returncode == 4, result.stdout + result.stderr
        assert "no tests ran" in result.stdout + result.stderr
        assert not (tmp_path / "test-ran").exists()
        assert not list((tmp_path / "runs/reportportal").glob("pytest-*/junit.xml"))
        requests = json.loads((tmp_path / "requests.json").read_text())
        assert len(requests) == 1 and requests[0]["method"] == "GET"
        return
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Verified ReportPortal launch" in result.stdout
    assert "ReportPortal upload failed" not in result.stdout
    controller = int((tmp_path / "controller.pid").read_text())
    requests = json.loads((tmp_path / "requests.json").read_text())
    loads = [json.loads(line) for line in (tmp_path / "loads.jsonl").read_text().splitlines()]
    assert {request["pid"] for request in requests} == {controller}
    assert len(loads) == 1 and {entry["pid"] for entry in loads} == {controller}
    project = "selected_project" if workers else "system_a"
    host = "selected.example" if workers else "reportportal.example"
    assert all(f"/{project}/" in request["path"] for request in requests)
    assert all(request["host"] == host for request in requests)
    receipts = [
        json.loads(p.read_text())
        for p in (tmp_path / "runs/reportportal").glob("*/reportportal.json")
    ]
    receipt = next(item for item in receipts if item.get("verified"))
    assert receipt["verified"] is True
    assert receipt["status"] == "FAILED" and receipt["pytest_exit_code"] == 1
    assert receipt["launch"] == ("Selected launch" if workers else "Pytest Launch")
    assert receipt["counts"] == {"total": 3, "passed": 1, "failed": 1, "skipped": 1}
