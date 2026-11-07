"""Run offline unit tests, then publish and verify their results in ReportPortal.

The uploader runs after pytest exits. Test socket guards stay intact, and reporting
failures cannot silently turn into a successful local-only run. Use --junit to publish
an existing report without rerunning tests, or --check to check access without a launch.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import math
import os
import shlex
import socket
import subprocess
import sys
import time
import tomllib
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


class ReportingError(RuntimeError):
    """A configuration, API, or verification failure safe to show to the operator."""


@dataclass(frozen=True)
class Settings:
    """Use environment, pytest CLI, ignored local file, then selected config values."""

    endpoint: str
    project: str
    api_key: str = field(repr=False)
    launch: str = "Browser exploration unit tests"
    description: str = "Offline unit suite with injected browser and model transports."
    attributes: tuple[str, ...] = ()

    @classmethod
    def load(
        cls,
        root: Path = ROOT,
        *,
        options: Mapping[str, Any] | None = None,
        overrides: Mapping[str, Any] | None = None,
    ) -> Settings:
        if options is None:
            config = tomllib.loads((root / "pyproject.toml").read_text())
            options = config["tool"]["pytest"]["ini_options"]
        overrides = overrides or {}
        local = dotenv_values(root / ".env.reportportal", interpolate=False)

        def raw(name: str) -> Any:
            return (
                os.environ.get(name)
                or overrides.get(name.lower())
                or local.get(name)
                or options.get(name.lower())
            )

        def value(name: str) -> str:
            return str(raw(name) or "")

        endpoint = value("RP_ENDPOINT").rstrip("/")
        url = urlsplit(endpoint)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ReportingError("RP_ENDPOINT must be an HTTP(S) base URL without credentials.")
        project, key = value("RP_PROJECT"), value("RP_API_KEY")
        if not project or not key:
            raise ReportingError(
                "Set RP_PROJECT and RP_API_KEY in .env.reportportal or the environment."
            )
        attributes = raw("RP_LAUNCH_ATTRIBUTES") or []
        if isinstance(attributes, str):
            attributes = [item.strip() for item in attributes.split(";") if item.strip()]
        return cls(
            endpoint,
            project,
            key,
            value("RP_LAUNCH") or cls.launch,
            value("RP_LAUNCH_DESCRIPTION") or cls.description,
            tuple(attributes),
        )

    @property
    def launches_url(self) -> str:
        """Open the launch list for this exact server and project."""
        return f"{self.endpoint}/ui/#{quote(self.project.lower(), safe='')}/launches/all"


def _network_reason(error: BaseException) -> str:
    """Classify the cause without printing exception text or request credentials."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, socket.gaierror):
            return "DNS lookup failed"
        if isinstance(current, OSError):
            if current.errno in {errno.EPERM, errno.EACCES}:
                return "network access denied by the operating system"
            if current.errno == errno.ECONNREFUSED:
                return "connection refused; check the server and published port"
        current = current.__cause__ or current.__context__
    return type(error).__name__


@dataclass(frozen=True)
class Case:
    """One real pytest result read from JUnit, including setup/teardown errors."""

    name: str
    status: str
    start: int
    end: int
    detail: str


@dataclass(frozen=True)
class Report:
    cases: tuple[Case, ...]
    start: int
    end: int

    @property
    def counts(self) -> dict[str, int]:
        counts = Counter(case.status.lower() for case in self.cases)
        return {"total": len(self.cases), **{s: counts[s] for s in ("passed", "failed", "skipped")}}

    @property
    def status(self) -> str:
        if self.counts["failed"]:
            return "FAILED"
        return "PASSED" if self.counts["passed"] else "SKIPPED"


def read_junit(path: Path) -> Report:
    """Preserve pytest outcomes; an error is a failure, never a passing item."""
    try:
        document = ET.fromstring(path.read_bytes())
        suites = [document] if document.tag == "testsuite" else list(document.iter("testsuite"))
        cases: list[Case] = []
        starts, ends = [], []
        for suite in suites:
            start = int(datetime.fromisoformat(suite.attrib["timestamp"]).timestamp() * 1000)
            starts.append(start)
            ends.append(start + _duration(suite.get("time", "0")))
            for item in suite.findall("testcase"):
                problems = list(item.findall("failure")) + list(item.findall("error"))
                skipped = item.find("skipped")
                status = "FAILED" if problems else "SKIPPED" if skipped is not None else "PASSED"
                details = problems or ([skipped] if skipped is not None else [])
                detail = "\n".join(
                    (node.get("message", "") + "\n" + (node.text or "")).strip() for node in details
                )
                name = ".".join(filter(None, (item.get("classname"), item.attrib["name"])))
                end = start + max(1, _duration(item.get("time", "0")))
                cases.append(Case(name, status, start, end, detail))
                ends.append(end)
        if not cases:
            raise ValueError("No test cases")
        return Report(tuple(cases), min(starts), max(ends))
    except (OSError, ET.ParseError, KeyError, ValueError, OverflowError):
        raise ReportingError(
            "JUnit is missing, empty, or invalid; no launch was created."
        ) from None


def _duration(value: str) -> int:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("Invalid duration")
    return int(seconds * 1000)


class Portal:
    """Use the same v2 write / v1 read endpoints as reportportal-client 5.7.9.

    Writes are not retried: an ambiguous connection failure must not create duplicate
    launches. Reads poll briefly because ReportPortal processes v2 events asynchronously.
    The injected HTTP client lets unit tests exercise this boundary without a network.
    """

    def __init__(self, settings: Settings, client: httpx.Client) -> None:
        self.settings = settings
        self.client = client
        self.launch_id: str | None = None
        self.active_item: str | None = None
        self.finished = False

    def request(
        self,
        method: str,
        resource: str,
        payload: dict[str, Any] | None = None,
        *,
        version: int = 2,
        allow_missing: bool = False,
        timeout: float = 10,
    ) -> dict[str, Any]:
        project = quote(self.settings.project, safe="")
        url = f"{self.settings.endpoint}/api/v{version}/{project}/{resource}"
        try:
            response = self.client.request(
                method,
                url,
                json=payload,
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
                timeout=timeout,
                follow_redirects=False,
            )
        except httpx.RequestError as error:
            # Do not print headers, response bodies, or raw exception strings.
            raise ReportingError(
                f"Cannot reach {self.settings.endpoint}: {_network_reason(error)}. "
                "Check RP_ENDPOINT, DNS, the server, and network permissions."
            ) from None
        if response.status_code == 404 and allow_missing:
            return {}
        if not response.is_success:
            hints = {
                401: "check RP_API_KEY",
                403: "check project permissions",
                404: "check the URL/project",
            }
            hint = hints.get(response.status_code, "check the ReportPortal server")
            raise ReportingError(f"ReportPortal HTTP {response.status_code}: {hint}.")
        try:
            body = response.json()
        except ValueError:
            raise ReportingError(
                "ReportPortal returned non-JSON; check RP_ENDPOINT/proxy routing."
            ) from None
        if not isinstance(body, dict):
            raise ReportingError("ReportPortal returned an unexpected response shape.")
        return body

    def check(self) -> None:
        """Check authentication and project access without creating a launch."""
        body = self.request("GET", "launch?page.size=1", version=1)
        if not isinstance(body.get("content"), list):
            raise ReportingError("Launch listing is missing; check RP_ENDPOINT/proxy routing.")

    def publish(self, report: Report, pytest_exit: int = 0) -> dict[str, Any]:
        """Upload actual outcomes and confirm a finished launch with matching counts."""
        attributes = []
        for attribute in self.settings.attributes:
            key, separator, value = attribute.partition(":")
            attributes.append({"key": key, "value": value} if separator else {"value": key})
        result = self.request(
            "POST",
            "launch",
            {
                "name": self.settings.launch,
                "description": self.settings.description,
                "startTime": str(report.start),
                "mode": "DEFAULT",
                "attributes": attributes,
            },
        )
        self.launch_id = _identifier(result)
        try:
            for case in report.cases:
                result = self.request(
                    "POST",
                    "item",
                    {
                        "launchUuid": self.launch_id,
                        "name": case.name[:1024],
                        "codeRef": case.name,
                        "testCaseId": hashlib.sha256(case.name.encode()).hexdigest(),
                        "description": case.detail.replace(self.settings.api_key, "[REDACTED]")[
                            :8192
                        ],
                        "startTime": str(case.start),
                        "type": "STEP",
                        "hasStats": True,
                    },
                )
                self.active_item = _identifier(result)
                self._finish_item(case.end, case.status)
            status = "FAILED" if pytest_exit else report.status
            self._finish_launch(report.end, status)
            return self.verify(report.counts, status)
        except ReportingError:
            # A partial upload remains a failed launch, even if cleanup succeeds.
            # If the server is unreachable, the receipt retains its UUID for inspection.
            if not self.finished:
                try:
                    self._finish_item(report.end, "INTERRUPTED")
                    self._finish_launch(report.end, "FAILED")
                except ReportingError:
                    pass
            raise

    def _finish_item(self, end: int, status: str) -> None:
        if self.active_item:
            self.request(
                "PUT",
                f"item/{quote(self.active_item, safe='')}",
                {"launchUuid": self.launch_id, "endTime": str(end), "status": status},
            )
            self.active_item = None

    def _finish_launch(self, end: int, status: str) -> None:
        self.request(
            "PUT",
            f"launch/{quote(str(self.launch_id), safe='')}/finish",
            {"endTime": str(end), "status": status},
        )
        self.finished = True

    def verify(self, expected: dict[str, int], status: str, timeout: float = 30) -> dict[str, Any]:
        """A successful POST is insufficient: read back status and execution totals."""
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            body = self.request(
                "GET",
                f"launch/uuid/{quote(str(self.launch_id), safe='')}",
                version=1,
                allow_missing=True,
                timeout=min(10, remaining),
            )
            statistics = body.get("statistics", {})
            if not isinstance(statistics, dict):
                raise ReportingError("ReportPortal returned invalid launch statistics.")
            executions = statistics.get("executions", {})
            if not isinstance(executions, dict):
                raise ReportingError("ReportPortal returned invalid execution counts.")
            actual = {key: executions.get(key, 0) for key in expected}
            if any(type(value) is not int or value < 0 for value in actual.values()):
                raise ReportingError("ReportPortal returned invalid execution counts.")
            if body.get("status") == status and body.get("endTime") and actual == expected:
                ui_id = body.get("id")
                if not isinstance(ui_id, int):
                    raise ReportingError("Verified launch has no numeric UI ID.")
                project = quote(self.settings.project.lower(), safe="")
                return {
                    "verified": True,
                    "launch_uuid": self.launch_id,
                    "status": status,
                    "counts": actual,
                    "url": f"{self.settings.endpoint}/ui/#{project}/launches/all/{ui_id}",
                }
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))
        raise ReportingError(
            "Launch did not finish with the expected test counts within 30 seconds."
        )


def _identifier(body: dict[str, Any]) -> str:
    identifier = body.get("id")
    if not isinstance(identifier, str) or not identifier:
        raise ReportingError("ReportPortal did not acknowledge the new launch/item UUID.")
    return identifier


def run_pytest(junit: Path, arguments: list[str]) -> int:
    """The subprocess owns all test fixtures; uploading starts only after it exits."""
    if "--reportportal" in arguments + shlex.split(os.environ.get("PYTEST_ADDOPTS", "")):
        raise ReportingError("Omit --reportportal; this command uploads after pytest exits.")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("RP_")}
    environment.update(
        LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false", QA_BROWSER_LIVE_MCP="0"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(ROOT / "pyproject.toml"),
            "tests/unit",
            "-n",
            "auto",
            f"--junitxml={junit}",
            *arguments,
        ],
        cwd=ROOT,
        env=environment,
        check=False,
    )
    return result.returncode


def main(
    arguments: list[str] | None = None,
    *,
    receipt: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> int:
    """Run reporting and optionally expose its receipt to the pytest controller."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Check API access without running tests")
    mode.add_argument(
        "--junit", type=Path, help="Upload existing pytest JUnit without rerunning tests"
    )
    parser.add_argument(
        "--pytest-exit-code",
        type=int,
        choices=range(6),
        help="Original pytest exit code when uploading --junit (used by the pytest controller)",
    )
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER, help="Pytest arguments after --")
    args = parser.parse_args(arguments)
    pytest_args = args.pytest_args[1:] if args.pytest_args[:1] == ["--"] else args.pytest_args
    if (args.check or args.junit) and pytest_args:
        parser.error("Pytest arguments require a new test run, without --check/--junit.")
    if args.pytest_exit_code is not None and not args.junit:
        parser.error("--pytest-exit-code requires --junit.")
    directory = (
        ROOT
        / "runs"
        / "reportportal"
        / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8])
    )
    directory.mkdir(parents=True, mode=0o700)
    if receipt is None:
        receipt = {}
    receipt.update(verified=False, stage="configuration")
    portal: Portal | None = None
    previous_umask = os.umask(0o077)
    try:
        settings = settings or Settings.load()
        receipt.update(
            endpoint=settings.endpoint,
            project=settings.project,
            launch=settings.launch,
            launches_url=settings.launches_url,
        )
        print(
            f"ReportPortal target: {settings.endpoint} | project={settings.project} "
            f"| launch={settings.launch}",
            flush=True,
        )
        junit = args.junit.resolve() if args.junit else directory / "junit.xml"
        report = None
        if args.junit:
            receipt.update(junit=str(junit), stage="junit", pytest_exit_code=args.pytest_exit_code)
            report = read_junit(junit)
            receipt["counts"] = report.counts
        with httpx.Client() as client:
            portal = Portal(settings, client)
            receipt["stage"] = "preflight"
            portal.check()
            if args.check:
                receipt.update(stage="access_checked", access_checked=True)
                print(
                    "ReportPortal authentication and project access succeeded; no launch created."
                )
                print(f"Launch list: {settings.launches_url}")
                return 0
            receipt["junit"] = str(junit)
            receipt["stage"] = "pytest" if not args.junit else "junit"
            exit_code = (
                run_pytest(junit, pytest_args) if not args.junit else (args.pytest_exit_code or 0)
            )
            receipt["pytest_exit_code"] = exit_code if not args.junit else args.pytest_exit_code
            report = report or read_junit(junit)
            receipt.update(counts=report.counts, stage="upload")
            receipt.update(portal.publish(report, exit_code), stage="verified")
            print(f"Verified ReportPortal launch: {receipt['url']}")
            print(f"Test counts: {report.counts}")
            return exit_code if exit_code else int(report.status == "FAILED")
    except (ReportingError, OSError, ValueError) as error:
        message = (
            str(error)
            if isinstance(error, ReportingError)
            else f"Local setup failed ({type(error).__name__})."
        )
        receipt["error"] = message
        if portal:
            receipt["launch_uuid"] = portal.launch_id
        print(message, file=sys.stderr)
        return 2
    finally:
        destination = directory / "reportportal.json"
        receipt["receipt_path"] = str(destination)
        destination.write_text(json.dumps(receipt, indent=2) + "\n")
        os.umask(previous_umask)
        print(f"ReportPortal receipt: {destination}")


if __name__ == "__main__":
    raise SystemExit(main())
