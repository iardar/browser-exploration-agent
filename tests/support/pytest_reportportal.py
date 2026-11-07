"""Support the normal --reportportal flag without network access inside tests.

Only the pytest controller validates reporting credentials and publishes JUnit after
all workers finish. Disable the upstream streaming hooks before they create a client;
those hooks otherwise send logs while the offline unit fixture blocks TCP and DNS.
"""

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from _pytest.terminal import TerminalReporter
from scripts import reportportal

_OPTIONS = (
    "rp_endpoint",
    "rp_project",
    "rp_api_key",
    "rp_launch",
    "rp_launch_description",
    "rp_launch_attributes",
)


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """Keep the familiar flag; defer the actual upload to controller session finish."""
    if not config.getoption("rp_enabled", default=False):
        return
    config.option.rp_enabled = False
    if (
        hasattr(config, "workerinput")
        or config.getoption("collectonly", default=False)
        or config.getoption("setupplan", default=False)
    ):
        return
    try:
        settings = reportportal.Settings.load(
            config.rootpath,
            options={name: config.getini(name) for name in _OPTIONS},
            overrides={name: config.getoption(name, default=None) for name in _OPTIONS},
        )
    except (reportportal.ReportingError, OSError, ValueError) as error:
        message = (
            str(error)
            if isinstance(error, reportportal.ReportingError)
            else "Invalid reporting configuration."
        )
        raise pytest.UsageError(f"ReportPortal: {message}") from None
    # This runs before pytest's JUnit plugin reads xmlpath. An explicit user path
    # is preserved; otherwise, the generated XML lives in a private run directory.
    if not config.option.xmlpath:
        directory = config.rootpath / "runs" / "reportportal" / f"pytest-{uuid4().hex[:12]}"
        directory.mkdir(parents=True, mode=0o700)
        config.option.xmlpath = str(directory / "junit.xml")
    config.pluginmanager.register(
        DeferredReportPortal(Path(config.option.xmlpath).resolve(), settings),
        "browser_deferred_reportportal",
    )


class DeferredReportPortal:
    """Publish the finished XML once, after pytest's own JUnit writer has closed it."""

    def __init__(self, junit: Path, settings: reportportal.Settings) -> None:
        self.junit = junit
        self.settings = settings
        self.result: int | None = None
        self.receipt: dict[str, Any] = {}
        self.failed = False
        self.access_checked = False

    def pytest_report_header(self) -> str:
        return f"ReportPortal: {self.settings.endpoint} | project={self.settings.project}"

    @pytest.hookimpl(tryfirst=True)
    def pytest_sessionstart(self, session: pytest.Session) -> None:
        """Reject an unreachable target before spending time running the test suite."""
        access: dict[str, Any] = {}
        result = reportportal.main(["--check"], settings=self.settings, receipt=access)
        self.access_checked = result == 0 and access.get("access_checked", False)
        if not self.access_checked:
            raise pytest.UsageError(
                f"ReportPortal access check failed; no tests ran. {access.get('error', '')}"
            )

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        if not self.access_checked or exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED:
            return
        self.result = reportportal.main(
            ["--junit", str(self.junit), "--pytest-exit-code", str(int(exitstatus))],
            receipt=self.receipt,
            settings=self.settings,
        )
        self.failed = not self.receipt.get("verified", False)
        if self.failed:
            session.exitstatus = pytest.ExitCode.INTERRUPTED

    def pytest_terminal_summary(self, terminalreporter: TerminalReporter) -> None:
        if self.failed:
            terminalreporter.write_sep("!", "ReportPortal upload failed; see the receipt above")
            terminalreporter.write_line(
                str(self.receipt.get("error", "Launch verification failed"))
            )
        elif self.receipt.get("verified"):
            terminalreporter.write_sep("=", "ReportPortal launch verified")
            terminalreporter.write_line(str(self.receipt["url"]))
