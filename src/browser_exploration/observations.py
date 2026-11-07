"""Select bounded snapshot and network text without changing retained evidence."""

import re
from collections import Counter
from dataclasses import dataclass

from .reporting import redact_text

_LINK_QUERY = re.compile(r"^(\s*-\s*/url:\s*[^\s?]+)\?[^\n]*", re.MULTILINE)
_NETWORK_REQUEST = re.compile(
    r"^\s*(?:\d+\.\s*)?\[(?P<method>[A-Z]+)\]\s+(?P<url>\S+)"
    r"(?:\s+=>\s*(?P<outcome>.*))?\s*$"
)
_NETWORK_OUTCOME = re.compile(r"^\[(?P<status>[1-5]\d{2}|FAILED)\](?:\s+(?P<detail>.*))?$")
_STATIC_OMITTED = re.compile(r"^Note: (\d+) static requests not shown\b")
_NETWORK_SUMMARY_CHARS = 1_000
_NETWORK_DETAIL_COUNT = 5


@dataclass(frozen=True)
class _NetworkRequest:
    """Keep one display line and the category used to count and order it."""

    detail: str
    category: str


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
    return _NetworkRequest(detail=rendered, category=category)


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
    lines.extend(_clip(request.detail, detail_limit) for request in selected)
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
