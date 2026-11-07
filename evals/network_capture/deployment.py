"""Inspect the dedicated evaluation deployment; never start or modify containers here."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

COMPOSE = Path(__file__).with_name("compose.yaml")  # JSON is also valid Compose YAML.
DEFAULT_CONTAINER = "network-capture-eval-mcp-1"
DEFAULT_MCP_URL = "http://127.0.0.1:8932/mcp"


class IsolationError(ValueError):
    """A safe evaluation setup error, never a raw Docker or MCP response."""


def _docker(*arguments: str) -> Any:
    try:
        result = subprocess.run(
            ["docker", *arguments],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise IsolationError(
            f"Cannot verify evaluation deployment ({type(exc).__name__})."
        ) from None


def inspect_deployment(container: str, mcp_url: str, fixture_url: str) -> dict[str, Any]:
    """Verify actual image, launch flags, endpoint and restricted Docker network."""
    services = json.loads(COMPOSE.read_text())["services"]
    spec = services["mcp"]
    try:
        item = _docker("inspect", "--type", "container", container)[0]
        config, host = item["Config"], item["HostConfig"]
        endpoint, fixture = urlsplit(mcp_url), urlsplit(fixture_url)
        if (
            not item["State"]["Running"]
            or config["Image"] != spec["image"]
            or "@sha256:" not in spec["image"]
            or config["Entrypoint"] != spec["entrypoint"]
            or config["Cmd"] != spec["command"]
            or not {"--isolated", "--block-service-workers"} <= set(config["Cmd"])
            or any(
                flag in config["Cmd"]
                for flag in (
                    "--shared-browser-context",
                    "--storage-state",
                    "--user-data-dir",
                    "--cdp-endpoint",
                    "--extension",
                    "--config",
                    "--init-script",
                )
            )
            or any(value.startswith("PLAYWRIGHT_MCP_") for value in config.get("Env", []))
            or any(mount["Type"] != "tmpfs" for mount in item.get("Mounts", []))
            or not host["ReadonlyRootfs"]
            or host.get("Privileged")
            or endpoint.scheme != "http"
            or endpoint.hostname != "127.0.0.1"
            or endpoint.port != 8932
            or endpoint.path != "/mcp"
            or fixture.scheme != "http"
            or fixture.hostname != "127.0.0.1"
            or fixture.port != 8766
            or fixture.path not in {"", "/"}
            or any(
                (url.username, url.password, url.query, url.fragment) != (None, None, "", "")
                for url in (endpoint, fixture)
            )
        ):
            raise IsolationError("Use the dedicated pinned evaluation Compose deployment.")
        gateway = _docker("inspect", "--type", "container", "network-capture-eval-gateway-1")[0]
        if (
            not gateway["State"]["Running"]
            or gateway["Config"]["Image"] != services["gateway"]["image"]
            or gateway["Config"]["Entrypoint"] != services["gateway"]["entrypoint"]
            or gateway["Config"]["Cmd"] != services["gateway"]["command"]
            or gateway.get("Mounts")
            or not gateway["HostConfig"]["ReadonlyRootfs"]
        ):
            raise IsolationError("Evaluation ingress proxy differs from the fixed deployment.")
        for port, published in (("8932/tcp", "8932"), ("8766/tcp", "8766")):
            if gateway["NetworkSettings"]["Ports"][port] != [
                {"HostIp": "127.0.0.1", "HostPort": published}
            ]:
                raise IsolationError("Evaluation endpoint does not match the inspected container.")
        networks = item["NetworkSettings"]["Networks"]
        if len(networks) != 1:
            raise IsolationError("Evaluation browser must have one internal network.")
        network = _docker("network", "inspect", next(iter(networks)))[0]
        if not network["Internal"] or network["Driver"] != "bridge":
            raise IsolationError("Evaluation browser network is not an internal bridge.")
        network_name = next(iter(networks))
        if (
            network_name not in gateway["NetworkSettings"]["Networks"]
            or "mcp" not in networks[network_name]["Aliases"]
        ):
            raise IsolationError("Evaluation proxy is not connected to the inspected browser.")
        fixture_container = _docker(
            "inspect", "--type", "container", "network-capture-eval-fixture-1"
        )[0]
        fixture_spec = services["fixture"]
        if (
            not fixture_container["State"]["Running"]
            or fixture_container["Config"]["Image"] != fixture_spec["image"]
            or fixture_container["Config"]["Cmd"] != fixture_spec["command"]
            or fixture_container["HostConfig"]["NetworkMode"] != f"container:{item['Id']}"
        ):
            raise IsolationError("Fixture is not the pinned service beside the browser.")
        return {
            "container_id": item["Id"],
            "image_id": item["Image"],
            "image": config["Image"],
            "started_at": item["State"]["StartedAt"],
            "restart_count": item["RestartCount"],
            "gateway_id": gateway["Id"],
            "gateway_started_at": gateway["State"]["StartedAt"],
            "fixture_id": fixture_container["Id"],
            "fixture_started_at": fixture_container["State"]["StartedAt"],
            "network_id": network["Id"],
            "isolated": True,
            "shared_context": False,
            "service_workers_blocked": True,
        }
    except (KeyError, IndexError, TypeError, StopIteration) as exc:
        raise IsolationError(f"Incomplete deployment inspection ({type(exc).__name__}).") from None


def deployment_versions(container_id: str) -> dict[str, str]:
    """Read package and browser binary versions without launching a browser or model."""
    script = (
        'const {execFileSync}=require("child_process");'
        'const {chromium}=require("playwright");'
        'console.log(JSON.stringify({mcp:require("/app/package.json").version,'
        'playwright:require("playwright/package.json").version,'
        'browser:execFileSync(chromium.executablePath(),["--version"],'
        '{encoding:"utf8"}).trim()}));'
    )
    result = _docker("exec", "--workdir", "/app", container_id, "node", "-e", script)
    if not isinstance(result, dict) or not all(
        isinstance(result.get(key), str) and result[key] for key in ("mcp", "playwright", "browser")
    ):
        raise IsolationError("MCP/browser version inspection is incomplete.")
    return {key: result[key] for key in ("mcp", "playwright", "browser")}
