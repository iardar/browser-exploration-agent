"""Deterministic selection of snapshot text within the observation budget."""

import re

from .reporting import redact_text

_LINK_QUERY = re.compile(r"^(\s*-\s*/url:\s*[^\s?]+)\?[^\n]*", re.MULTILINE)


def summarize_observation(text: str, limit: int) -> str:
    """Redact and shorten page text, removing verbose link queries first when useful.

    The model receives this bounded text, not a semantic summary of the full page.
    Raw payload retention is a separate, optional artifact-store decision.
    """
    summary = redact_text(text)
    if len(summary) > limit and "### Snapshot" in summary:
        summary = _LINK_QUERY.sub(r"\1 [query omitted]", summary)
    if len(summary) <= limit:
        return summary
    marker = " [truncated]"
    if limit <= len(marker):
        return summary[:limit]
    return summary[: limit - len(marker)] + marker
