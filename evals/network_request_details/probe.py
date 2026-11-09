"""Model-free checks that the live MCP server exposes the corpus's declared detail parts."""

import re
from typing import Any

from browser_exploration.browser_tools import BrowserSession, argument_schema

from .dataset import load_case, load_oracle

_LISTING = re.compile(
    r"^(?P<index>[1-9]\d*)\. \[(?P<method>[A-Z]+)\] (?P<url>\S+) "
    r"=> \[(?P<status>[1-5]\d{2}|FAILED)\](?:\s|$)"
)
_IDENTITY = re.compile(
    r"^#(?P<index>[1-9]\d*) \[(?P<method>[A-Z]+)\] "
    r"(?P<url>\S+)\s*$",
    re.MULTILINE,
)
_MIME = re.compile(r"^[ \t]*mimeType:[ \t]*(\S+)[ \t]*$", re.MULTILINE | re.IGNORECASE)
_STATUS = re.compile(r"^[ \t]*status:[ \t]*\[([1-5]\d{2})\]", re.MULTILINE)


class ProbeError(ValueError):
    """Describe a case compatibility failure without revealing the returned payload."""


def _request_header_in_overview(text: str, name: str, value: str) -> bool:
    """Check the selector only inside the overview's request-header section."""
    active = False
    for line in text.splitlines():
        if line.strip() == "Request headers":
            active = True
        elif active and line.startswith("    "):
            key, separator, found = line.strip().partition(":")
            if separator and key.casefold() == name.casefold() and found.strip() == value:
                return True
        elif active and line.strip():
            break
    return False


def _text(value: Any) -> str:
    """Read inline MCP text only; file and image references are not inspected."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_text(item) for item in value)
    if isinstance(value, dict):
        if value.get("type") in {"file", "image", "image_reference", "audio"}:
            return ""
        text = value.get("text")
        if isinstance(text, str):
            return text
        if "content" in value:
            return _text(value["content"])
    return ""


async def probe_case(session: BrowserSession, url: str, case_id: str) -> None:
    """Verify the actual listing, matching overview and requested part in one disposable session."""
    case = load_case(case_id)
    oracle = load_oracle()[case_id]
    capabilities = session.capabilities
    detail_name = capabilities.get("network_detail")
    network_name = capabilities.get("network")
    if detail_name is None or network_name is None:
        raise ProbeError(f"{case_id}: MCP lacks a required network tool.")
    navigation = await session.call(capabilities["navigate"], {"url": url})
    snapshot = await session.call(capabilities["snapshot"], {})
    page = _text([navigation, snapshot])
    if (
        f"Network capture fixture {case_id}" not in page
        or "Network fixture ready" not in page
        or f"- Page URL: {url}" not in page
    ):
        raise ProbeError(f"{case_id}: fixture page is not ready.")
    properties = argument_schema(session.tools[network_name]).get("properties", {})
    static_key = next((key for key in ("static", "includeStatic") if key in properties), None)
    listing_args = {static_key: True} if static_key is not None else {}
    listing = await session.call(network_name, listing_args)
    if isinstance(listing, dict) and listing.get("isError"):
        raise ProbeError(f"{case_id}: network listing failed.")
    target = url.rstrip("/") + "/target"
    matches = [
        match
        for line in _text(listing).splitlines()
        if (match := _LISTING.match(line))
        and match["method"] == case["method"]
        and match["url"] == target
        and match["status"] == str(oracle["status"])
    ]
    selector = case.get("selector_header")
    if len(matches) < 2 if selector else len(matches) != 1:
        raise ProbeError(f"{case_id}: target request is absent or ambiguous.")
    selected: int | None = None
    for match in matches:
        index = int(match["index"])
        overview = await session.call(detail_name, {"index": index})
        if isinstance(overview, dict) and overview.get("isError"):
            raise ProbeError(f"{case_id}: request overview failed.")
        overview_text = _text(overview)
        identities = list(_IDENTITY.finditer(overview_text))
        status = _STATUS.search(overview_text)
        if (
            len(identities) != 1
            or (
                int(identities[0]["index"]),
                identities[0]["method"],
                identities[0]["url"],
            )
            != (index, case["method"], target)
            or status is None
            or status[1] != str(oracle["status"])
        ):
            raise ProbeError(f"{case_id}: request overview differs from listing.")
        if selector and not _request_header_in_overview(
            overview_text, selector["name"], selector["value"]
        ):
            continue
        mime = oracle["content_type"].split(";", 1)[0]
        advertised_mime = _MIME.search(overview_text)
        if advertised_mime is None or advertised_mime[1] != mime:
            raise ProbeError(f"{case_id}: request overview MIME differs from fixture.")
        if f"content-type: {oracle['content_type']}".casefold() not in overview_text.casefold():
            raise ProbeError(f"{case_id}: response Content-Type differs from fixture.")
        selected = index
        break
    if selected is None:
        raise ProbeError(f"{case_id}: no overview matches the requested header.")
    if oracle.get("expected") == "blocked_binary":
        return
    part = await session.call(detail_name, {"index": selected, "part": case["part"]})
    if isinstance(part, dict) and part.get("isError"):
        raise ProbeError(f"{case_id}: requested detail part failed.")
    if oracle["marker"] not in _text(part):
        raise ProbeError(f"{case_id}: expected inline detail content is unavailable.")
