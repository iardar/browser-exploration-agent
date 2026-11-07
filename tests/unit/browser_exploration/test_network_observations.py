"""Keep network failures in context while preserving the separate evidence payload."""

import json
from pathlib import Path

import pytest

from browser_exploration.browser_tools import discovered_tools
from browser_exploration.config import Settings
from browser_exploration.contracts import BrowserExplorationTask
from browser_exploration.explorer import AutonomousBrowserExplorer
from browser_exploration.model import Action, Decision, DecisionResponse
from browser_exploration.observations import summarize_network_observation
from browser_exploration.reporting import redact
from tests.unit.browser_exploration.fakes import (
    FakeConnection,
    FakeModel,
    FakeSession,
    fake_tool,
    snapshot,
)


def network_listing() -> str:
    """Place failures beyond the ordinary observation prefix to exercise selection."""
    healthy = [
        f"{index}. [GET] https://example.com/assets/healthy-resource-{index:04d}.js => [200] OK"
        for index in range(1, 201)
    ]
    assert len("\n".join(healthy)) > 6_000
    return "\n".join(
        [
            "### Result",
            *healthy,
            "201. [GET] https://example.com/api/articles?token=private-query => [500] Server Error",
            "202. [POST] https://example.com/api/login => [401] Unauthorized",
            "203. [GET] https://example.com/api/tags => [FAILED] net::ERR_CONNECTION_RESET",
            "204. [GET] https://example.com/api/pending",
        ]
    )


def test_failures_after_a_long_successful_prefix_are_counted_and_selected_first() -> None:
    """Verify failures after a long successful prefix are counted and selected first.

    Arrange: Start with `summarize_network_observation(network_listing(), 6000)`.
    Act: Evaluate `summary.index` in the assertion.
    Assert: Check `len(summary) <= 1000`; `'Network requests parsed: 204; HTTP errors: 2; failed: 1;
            unknown outcome: 1.' in summary`; `'Request details omitted: 199.' in summary`;
            `'private-query' not in summary`.
    """
    summary = summarize_network_observation(network_listing(), 6_000)

    assert len(summary) <= 1_000
    assert "Network requests parsed: 204; HTTP errors: 2; failed: 1; unknown outcome: 1." in summary
    assert "Request details omitted: 199." in summary
    assert summary.index("[500]") < summary.index("[401]") < summary.index("[FAILED]")
    assert summary.index("[FAILED]") < summary.index("[UNKNOWN]") < summary.index("[200]")
    assert "net::ERR_CONNECTION_RESET" in summary
    assert "private-query" not in summary


def test_numbered_requests_preserve_static_filter_scope_and_redact_queries() -> None:
    """Verify numbered requests preserve static filter scope and redact queries.

    Arrange: Start with `summarize_network_observation()`.
    Act: Call `summarize_network_observation` to obtain `summary`.
    Assert: Check `'Network requests parsed: 2; HTTP errors: 0; failed: 0; unknown outcome: 0.' in
            summary`; `'Static requests omitted by tool: 21.' in summary`; `'private-limit' not in
            summary`; `'REDACTED' in summary`.
    """
    summary = summarize_network_observation(
        "### Result\n"
        "18. [GET] https://example.com/api/articles?limit=private-limit => [200] \n"
        "19. [GET] https://example.com/api/tags => [200] \n\n"
        'Note: 21 static requests not shown, run with "static" option to see them.',
        1_000,
    )

    assert "Network requests parsed: 2; HTTP errors: 0; failed: 0; unknown outcome: 0." in summary
    assert "Static requests omitted by tool: 21." in summary
    assert "private-limit" not in summary
    assert "REDACTED" in summary


@pytest.mark.parametrize(
    ("request_line", "counts", "detail"),
    [
        (
            "[GET] https://example.com/ => [200] OK",
            "HTTP errors: 0; failed: 0; unknown outcome: 0",
            "[200] GET",
        ),
        (
            "1. [POST] https://example.com/ => [400] Bad Request",
            "HTTP errors: 1; failed: 0; unknown outcome: 0",
            "[400] POST",
        ),
        (
            "[GET] https://example.com/ => [599]",
            "HTTP errors: 1; failed: 0; unknown outcome: 0",
            "[599] GET",
        ),
        (
            "[GET] https://example.com/ => [302] Found",
            "HTTP errors: 0; failed: 0; unknown outcome: 0",
            "[302] GET",
        ),
        (
            "[GET] https://example.com/ => [FAILED] net::ERR_ABORTED",
            "HTTP errors: 0; failed: 1; unknown outcome: 0",
            "[FAILED] GET",
        ),
        (
            "[GET] https://example.com/",
            "HTTP errors: 0; failed: 0; unknown outcome: 1",
            "[UNKNOWN] GET",
        ),
        (
            "[GET] https://example.com/ => [700] Unsupported",
            "HTTP errors: 0; failed: 0; unknown outcome: 1",
            "[UNKNOWN] GET",
        ),
        (
            "[GET] https://example.com/ => pending",
            "HTTP errors: 0; failed: 0; unknown outcome: 1",
            "pending",
        ),
    ],
)
def test_request_outcomes_are_classified_without_assuming_success(
    request_line: str, counts: str, detail: str
) -> None:
    """Verify request outcomes are classified without assuming success.

    Arrange: Use `request_line`, `counts`, `detail` parameters.
    Act: Call `summarize_network_observation` to obtain `summary`.
    Assert: Check `counts in summary`; `detail in summary`.
    """
    summary = summarize_network_observation(request_line, 1_000)
    assert counts in summary
    assert detail in summary


@pytest.mark.parametrize("limit", [1, 5, 40, 180, 500, 1_000, 6_000])
def test_network_summary_respects_both_context_caps(limit: int) -> None:
    """Verify network summary respects both context caps.

    Arrange: Use `limit` parameter.
    Act: Call `summarize_network_observation` to obtain `summary`.
    Assert: Check `len(summary) <= min(limit, 1000)`.
    """
    summary = summarize_network_observation(network_listing(), limit)
    assert len(summary) <= min(limit, 1_000)


def test_long_urls_keep_the_status_and_make_omitted_details_explicit() -> None:
    """Verify long urls keep the status and make omitted details explicit.

    Arrange: Start with `summarize_network_observation()`.
    Act: Evaluate `summary.count` in the assertion.
    Assert: Check `'Network requests parsed: 8; HTTP errors: 8' in summary`; `'Request details
            omitted: 3.' in summary`; `summary.count('[503] GET') == 5`; `len(summary) <= 1000`.
    """
    summary = summarize_network_observation(
        "\n".join(
            f"[GET] https://example.com/{'x' * 500}/{index} => [503] Unavailable"
            for index in range(8)
        ),
        1_000,
    )
    assert "Network requests parsed: 8; HTTP errors: 8" in summary
    assert "Request details omitted: 3." in summary
    assert summary.count("[503] GET") == 5
    assert "[truncated]" in summary
    assert len(summary) <= 1_000


@pytest.mark.parametrize(
    "text",
    [
        "### Result\n",
        "Requests: []",
        '{"requests": [{"method": "GET", "status": 500}]}',
        "Unsupported response password=private-password",
    ],
)
def test_unrecognized_formats_keep_a_redacted_excerpt_and_unknown_counts(text: str) -> None:
    """Verify unrecognized formats keep a redacted excerpt and unknown counts.

    Arrange: Use `text` parameter.
    Act: Call `summarize_network_observation` to obtain `summary`.
    Assert: Check `'Network request counts unknown; no recognized request lines.' in summary`;
            `'HTTP errors: 0' not in summary`; `'private-password' not in summary`.
    """
    summary = summarize_network_observation(text, 1_000)
    assert "Network request counts unknown; no recognized request lines." in summary
    assert "HTTP errors: 0" not in summary
    assert "private-password" not in summary


def test_mixed_formats_disclose_unparsed_lines_instead_of_claiming_complete_counts() -> None:
    """Verify mixed formats disclose unparsed lines instead of claiming complete counts.

    Arrange: Start with `summarize_network_observation()`.
    Act: Call `summarize_network_observation` to obtain `summary`.
    Assert: Check `'Network requests parsed: 1' in summary`; `'Unrecognized lines: 2; additional
            request outcomes unknown.' in summary`.
    """
    summary = summarize_network_observation(
        "### Result\n[GET] https://example.com/ => [200] OK\n"
        "UNKNOWN REQUEST FORMAT\nAdditional requests were omitted upstream.",
        1_000,
    )
    assert "Network requests parsed: 1" in summary
    assert "Unrecognized lines: 2; additional request outcomes unknown." in summary


@pytest.mark.parametrize(
    "tool_name", ["browser_network_requests", "playwright_browser_network_requests"]
)
@pytest.mark.parametrize("retain_raw", [False, True])
async def test_graph_uses_compact_network_context_and_retains_the_original_redacted_response(
    tmp_path: Path, tool_name: str, retain_raw: bool
) -> None:
    """Verify graph uses compact network context and retains the original redacted response.

    Arrange: Use `tmp_path`, `tool_name`, `retain_raw` parameters; prepare `raw`, `session`,
             `model`.
    Act: Await `AutonomousBrowserExplorer.explore` to obtain `result`.
    Assert: Check `result.status == 'completed'`; `result.metrics.browser_tool_calls ==
            result.metrics.successful_browser_tool_calls == 3`; `session.calls[-1] == (tool_name,
            {'static': False})`; `not (directory / 'evidence').exists()`.
    """
    raw = {"content": [{"type": "text", "text": network_listing()}], "isError": False}
    session = FakeSession([snapshot(), snapshot(), raw])
    session.tools, session.capabilities = discovered_tools(
        [*session.tools.values(), fake_tool(tool_name, {"static": {"type": "boolean"}})]
    )
    model = FakeModel(
        [
            DecisionResponse(Decision(action=Action(name=tool_name, arguments={"static": False}))),
            DecisionResponse(Decision(done=True)),
        ]
    )
    result = await AutonomousBrowserExplorer(
        Settings(output_dir=tmp_path, network_restricted=True, retain_raw_evidence=retain_raw),
        connection=FakeConnection(session),
        model=model,
    ).explore(
        BrowserExplorationTask(url="https://example.com/", objective="Inspect network failures")
    )

    assert result.status == "completed"
    assert result.metrics.browser_tool_calls == result.metrics.successful_browser_tool_calls == 3
    assert session.calls[-1] == (tool_name, {"static": False})
    directory = tmp_path / result.run_id
    observation = json.loads((directory / "trajectory.jsonl").read_text().splitlines()[-1])
    assert observation["summary"] == summarize_network_observation(network_listing(), 6_000)
    assert observation["evidence_ref"] == result.evidence[-1].id == "evidence-obs-0003"
    messages = "\n".join(str(message.content) for message in model.requests[-1][0])
    assert "[500]" in messages and "[FAILED]" in messages and "evidence-obs-0003" in messages
    assert "healthy-resource-0200" not in messages
    assert "private-query" not in messages
    assert sum(len(str(message.content)) for message in model.requests[-1][0]) <= 18_000
    reference = result.evidence[-1]
    assert reference.retained is retain_raw
    if retain_raw:
        assert reference.path is not None
        saved_raw = json.loads((directory / reference.path).read_text())
        assert saved_raw == redact(raw)
        assert "healthy-resource-0200" in saved_raw["content"][0]["text"]
    else:
        assert reference.path is None
        assert not (directory / "evidence").exists()


async def test_network_tool_error_keeps_its_diagnostic_instead_of_becoming_a_request_summary(
    tmp_path: Path,
) -> None:
    """Verify network tool error keeps its diagnostic instead of becoming a request summary.

    Arrange: Use `tmp_path` parameter; prepare `raw`, `session`, `model`.
    Act: Await `AutonomousBrowserExplorer.explore` to obtain `result`.
    Assert: Check `result.status == 'failed'`; `result.errors[-1].code == 'mcp_tool_error'`;
            `observation['error'] is True`; `observation['summary'] == 'Network inspection
            unavailable'`.
    """
    raw = {"content": [{"type": "text", "text": "Network inspection unavailable"}], "isError": True}
    session = FakeSession([snapshot(), snapshot(), raw])
    session.tools, session.capabilities = discovered_tools(
        [*session.tools.values(), fake_tool("browser_network_requests")]
    )
    model = FakeModel(
        [DecisionResponse(Decision(action=Action(name="browser_network_requests")))],
    )
    result = await AutonomousBrowserExplorer(
        Settings(output_dir=tmp_path, network_restricted=True),
        connection=FakeConnection(session),
        model=model,
    ).explore(
        BrowserExplorationTask(url="https://example.com/", objective="Inspect network failures")
    )

    assert result.status == "failed"
    assert result.errors[-1].code == "mcp_tool_error"
    observation = json.loads(
        (tmp_path / result.run_id / "trajectory.jsonl").read_text().splitlines()[-1]
    )
    assert observation["error"] is True
    assert observation["summary"] == "Network inspection unavailable"
