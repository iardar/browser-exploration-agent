"""Select bounded snapshot and network text without changing retained evidence."""

import re
from collections import Counter
from dataclasses import dataclass

from .reporting import redact_text

_LINK_QUERY = re.compile(r"^(\s*-\s*/url:\s*[^\s?]+)\?[^\n]*", re.MULTILINE)
_NETWORK_REQUEST = re.compile(
    r"^\s*(?:(?P<index>[1-9]\d*)\.\s*)?\[(?P<method>[A-Z]+)\]\s+(?P<url>\S+)"
    r"(?:\s+=>\s*(?P<outcome>.*))?\s*$"
)
_NETWORK_OUTCOME = re.compile(r"^\[(?P<status>[1-5]\d{2}|FAILED)\](?:\s+(?P<detail>.*))?$")
_STATIC_OMITTED = re.compile(r"^Note: (\d+) static requests not shown\b")
_NETWORK_SUMMARY_CHARS = 1_000
_NETWORK_DETAIL_COUNT = 5
_VISIBLE_REQUEST_INDEX = re.compile(r"\[request #(\d+)\]$")
_DETAIL_IDENTITY = re.compile(r"^#(\d+) \[([A-Z]+)\] (\S+)\s*$", re.MULTILINE)
_DETAIL_MIME = re.compile(r"^[ \t]*mimeType:[ \t]*(\S+)[ \t]*$", re.MULTILINE | re.IGNORECASE)


@dataclass(frozen=True)
class _NetworkRequest:
    """Keep one display line and the category used to count and order it."""

    detail: str
    category: str
    index: int | None


def _clip(text: str, limit: int) -> str:
    """Fit text into a character allowance and mark omitted content when possible."""
    if len(text) <= limit:
        return text
    marker = " [truncated]"
    if limit <= len(marker):
        return text[:limit]
    return text[: limit - len(marker)] + marker


def _parse_network_request(line: str) -> _NetworkRequest | None:
    """Read a numbered or unnumbered Playwright request line without guessing its outcome."""
    match = _NETWORK_REQUEST.fullmatch(line)
    if match is None:
        return None
    outcome = (match["outcome"] or "").strip()
    status = _NETWORK_OUTCOME.fullmatch(outcome)
    if status is None:
        category = "unknown"
        label = "[UNKNOWN]"
        detail = outcome
    else:
        code = status["status"]
        if code == "FAILED":
            category = "failed"
        elif int(code) >= 400:
            category = "http_error"
        else:
            category = "other"
        label = f"[{code}]"
        detail = status["detail"] or ""
    # Put the outcome first so a long URL cannot hide the failure status.
    rendered = f"{label} {_clip(match['method'], 16)} {_clip(match['url'], 120)}"
    if detail:
        rendered += " " + _clip(detail, 60)
    number = match["index"]
    index = int(number) if number and len(number) <= 16 else None
    if index is not None and index > 9_007_199_254_740_991:
        index = None
    return _NetworkRequest(detail=rendered, category=category, index=index)


def _render_network_detail(request: _NetworkRequest, limit: int) -> str:
    """Keep a visible request index intact when the available detail budget permits it."""
    suffix = f" [request #{request.index}]" if request.index is not None else ""
    if suffix and limit > len(suffix) + 12:
        return _clip(request.detail, limit - len(suffix)) + suffix
    return _clip(request.detail, limit)


def visible_network_requests(text: str, summary: str) -> dict[int, tuple[str, str]]:
    """Map indices shown in a summary to unambiguous identities from the raw listing."""
    visible = {
        int(match[1])
        for line in summary.splitlines()
        if (match := _VISIBLE_REQUEST_INDEX.search(line)) and len(match[1]) <= 16
    }
    requests: dict[int, tuple[str, str]] = {}
    duplicates: set[int] = set()
    for line in text.splitlines():
        match = _NETWORK_REQUEST.fullmatch(line.strip())
        if match is None or match["index"] is None or len(match["index"]) > 16:
            continue
        index = int(match["index"])
        if index not in visible or index > 9_007_199_254_740_991:
            continue
        if index in requests:
            duplicates.add(index)
        requests[index] = (match["method"], match["url"])
    return {index: identity for index, identity in requests.items() if index not in duplicates}


def network_detail_mime(
    text: str, index: int, identity: tuple[str, str]
) -> tuple[bool, str | None]:
    """Verify an overview's index/method/URL and extract its advertised response MIME type."""
    matches = list(_DETAIL_IDENTITY.finditer(text))
    if len(matches) != 1:
        return False, None
    match = matches[0]
    if len(match[1]) > 16 or (int(match[1]), match[2], match[3]) != (index, *identity):
        return False, None
    mime = _DETAIL_MIME.search(text)
    response_headers = False
    content_types: list[str] = []
    for line in text.splitlines():
        if line.strip().lower() == "response headers":
            response_headers = True
        elif response_headers and line.startswith("    "):
            name, separator, value = line.strip().partition(":")
            if separator and name.lower() == "content-type":
                content_types.append(value.strip())
        elif response_headers and line.strip():
            break
    if len(content_types) != 1 or mime is None:
        return True, None
    content_type = content_types[0]
    if content_type.split(";", 1)[0].strip() != mime[1]:
        return True, None
    return True, content_type


def textual_response_mime(mime: str | None) -> bool:
    """Permit a conservative set of MIME types that the MCP server returns as text."""
    if mime is None:
        return False
    base, separator, parameters = mime.partition(";")
    if base != base.lower() or base != base.strip():
        return False
    if separator and not re.fullmatch(r"\s*charset=[A-Za-z0-9._-]+\s*", parameters):
        return False
    mime = base
    if mime.startswith("text/") and len(mime) > len("text/"):
        return True
    return mime in {
        "application/json",
        "application/javascript",
        "application/x-javascript",
        "application/xml",
        "application/ecmascript",
        "application/graphql",
        "application/x-www-form-urlencoded",
        "image/svg+xml",
        "image/svg",
    } or (mime.startswith("application/") and mime.endswith(("+json", "+xml")))


def network_detail_is_file_reference(text: str) -> bool:
    """Recognize a server-side file reference whose contents were not returned inline."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    lines = [line for line in lines if not line.startswith("### ")]
    if len(lines) != 1:
        return False
    return bool(
        re.fullmatch(r"-?\s*\[[^\]]+\]\([^)]+\)", lines[0])
        or re.fullmatch(r"(?:\.?\.?/|[\w.-]+/)[^\s]+", lines[0])
        or re.fullmatch(r"response-[\w.-]+\.[A-Za-z0-9]+", lines[0])
    )


def summarize_network_observation(text: str, limit: int) -> str:
    """Count returned request lines and show failures before other request details.

    This deterministic summary uses at most 1,000 characters and five request
    details, within the caller's allowance. Counts describe the returned listing,
    not all browser traffic. Unknown formats remain explicit. Raw evidence is
    saved separately by the artifact store, using the original tool response.
    """
    limit = min(limit, _NETWORK_SUMMARY_CHARS)
    redacted = redact_text(text)
    requests: list[_NetworkRequest] = []
    static_notes: list[str] = []
    unparsed = 0
    for line in redacted.splitlines():
        line = line.strip()
        if not line or line.startswith("### "):
            continue
        static = _STATIC_OMITTED.match(line)
        if static is not None:
            note = f"Static requests omitted by tool: {static[1]}."
            if note not in static_notes:
                static_notes.append(note)
            continue
        request = _parse_network_request(line)
        if request is None:
            unparsed += 1
        else:
            requests.append(request)
    if not requests:
        return _clip(
            "Network request counts unknown; no recognized request lines.\n" + redacted, limit
        )

    counts = Counter(request.category for request in requests)
    lines = [
        f"Network requests parsed: {len(requests)}; HTTP errors: {counts['http_error']}; "
        f"failed: {counts['failed']}; unknown outcome: {counts['unknown']}.",
        *static_notes,
    ]
    if unparsed:
        lines.append(f"Unrecognized lines: {unparsed}; additional request outcomes unknown.")
    priority = {"failed": 0, "http_error": 0, "unknown": 1, "other": 2}
    # Python's stable sort preserves source order inside each priority group.
    selected = sorted(requests, key=lambda request: priority[request.category])[
        :_NETWORK_DETAIL_COUNT
    ]
    if len(selected) < len(requests):
        lines.append(f"Request details omitted: {len(requests) - len(selected)}.")
    remaining = limit - len("\n".join(lines)) - 1
    detail_limit = min(200, max(1, remaining // len(selected) - 1))
    lines.extend(_render_network_detail(request, detail_limit) for request in selected)
    return _clip("\n".join(lines), limit)


def summarize_observation(text: str, limit: int) -> str:
    """Redact and shorten page text, removing verbose link queries first when useful.

    The model receives this bounded text, not a semantic summary of the full page.
    Raw payload retention is a separate, optional artifact-store decision.
    """
    summary = redact_text(text)
    if len(summary) > limit and "### Snapshot" in summary:
        summary = _LINK_QUERY.sub(r"\1 [query omitted]", summary)
    return _clip(summary, limit)
