"""Generate deterministic browser traffic for the request-detail corpus."""

from __future__ import annotations

import base64
import html
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from email.message import Message
from typing import Any

from .dataset import case_path, load_case, load_oracle, target_path


@dataclass(frozen=True)
class DetailResponse:
    """An HTTP response, including controlled headers used by detail cases."""

    status: int
    content_type: str
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)


def _xhr_script(name: str, method: str, path: str, spec: dict[str, Any]) -> str:
    """Issue one parser-blocking same-origin request before page readiness is announced."""
    script = (
        f"const request{name} = new XMLHttpRequest();"
        f"request{name}.open({json.dumps(method)}, {json.dumps(path)}, false);"
    )
    for header, value in spec.get("request_headers", {}).items():
        script += f"request{name}.setRequestHeader({json.dumps(header)}, {json.dumps(value)});"
    return script + f"request{name}.send({json.dumps(spec.get('request_body'))});"


def _has_headers(
    actual: Mapping[str, str] | Message[str, str] | None, expected: dict[str, str]
) -> bool:
    """Check fixture distinguishing headers without exposing their values in diagnostics."""
    return all(actual is not None and actual.get(name) == value for name, value in expected.items())


def _payload(spec: dict[str, Any]) -> bytes:
    """Return the declared response bytes, including the long-text fixture tail."""
    result: bytes = (
        base64.b64decode(spec["response_base64"])
        if "response_base64" in spec
        else spec["response_body"].encode()
    )
    return result + b"z" * int(spec.get("repeat", 0))


def fixture_response(
    path: str,
    *,
    method: str = "GET",
    body: bytes = b"",
    headers: Mapping[str, str] | Message[str, str] | None = None,
) -> DetailResponse | None:
    """Serve only declared case routes; None delegates to the other fixture routes."""
    if not path.startswith("/details/"):
        return None
    case_id = path.split("/", 3)[2]
    try:
        case = load_case(case_id)
    except ValueError:
        return DetailResponse(404, "text/plain", b"Unknown detail case.\n")
    oracle = load_oracle()[case_id]
    root = case_path(case_id)
    if path == root and method == "GET":
        script = "".join(
            _xhr_script(str(index), extra["method"], root + extra["path"], extra)
            for index, extra in enumerate(oracle.get("extra_requests", []))
        )
        script += _xhr_script("Target", case["method"], target_path(case_id), oracle)
        page = (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f"<title>Network capture fixture {html.escape(case_id)}</title></head><body>"
            f"<h1>Network capture fixture {html.escape(case_id)}</h1>"
            f'<script src="{root}decoy.js"></script><script>{script}</script>'
            '<h2 id="ready">Network fixture ready</h2></body></html>'
        )
        return DetailResponse(200, "text/html; charset=utf-8", page.encode())
    if path == root + "decoy.js" and method == "GET":
        return DetailResponse(200, "text/javascript; charset=utf-8", b"// Decoy request.\n")
    for extra in oracle.get("extra_requests", []):
        if (
            path == root + extra["path"]
            and method == extra["method"]
            and _has_headers(headers, extra.get("request_headers", {}))
            and body == extra.get("request_body", "").encode()
        ):
            return DetailResponse(
                extra["status"],
                extra["content_type"],
                _payload(extra),
                extra.get("response_headers", {}),
            )
    if path != target_path(case_id) or method != case["method"]:
        return DetailResponse(404, "text/plain", b"Unknown detail route.\n")
    if not _has_headers(headers, oracle.get("request_headers", {})):
        return DetailResponse(400, "text/plain", b"Unexpected request headers.\n")
    expected_body = oracle.get("request_body")
    if expected_body is not None and body != expected_body.encode():
        return DetailResponse(400, "text/plain", b"Unexpected request body.\n")
    if expected_body is None and body:
        return DetailResponse(400, "text/plain", b"Unexpected request body.\n")
    return DetailResponse(
        oracle["status"],
        oracle["content_type"],
        _payload(oracle),
        oracle.get("response_headers", {}),
    )
