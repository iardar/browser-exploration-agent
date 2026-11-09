"""Exercise request-detail discovery, authorization, and evidence without network access."""

import json
from pathlib import Path
from typing import Any

import pytest

from browser_exploration.browser_tools import discovered_tools, tool_specs
from browser_exploration.config import Settings
from browser_exploration.contracts import BrowserExplorationTask
from browser_exploration.explorer import AutonomousBrowserExplorer
from browser_exploration.model import Action, Decision, DecisionResponse
from browser_exploration.observations import (
    network_detail_mime,
    summarize_network_observation,
    textual_response_mime,
    visible_network_requests,
)
from browser_exploration.safety import SafetyError, SafetyPolicy
from tests.unit.browser_exploration.fakes import (
    FakeConnection,
    FakeModel,
    FakeSession,
    fake_tool,
    snapshot,
)

LISTING = (
    "### Result\n"
    "1. [GET] https://example.com/ => [200] OK\n"
    "2. [GET] https://example.com/api/data => [500] Server Error"
)
OVERVIEW = (
    "### Result\n#2 [GET] https://example.com/api/data\n\n"
    "  General\n    status: [500] Server Error\n    mimeType: application/json\n"
    "  Request headers\n    authorization: Bearer secret-header\n"
    "  Response headers\n    content-type: application/json\n"
)


def detail_tool() -> Any:
    """Build a fake with the schema advertised by the current Playwright detail tool."""
    tool = fake_tool(
        "browser_network_request",
        {
            "index": {"type": "integer"},
            "part": {
                "type": "string",
                "enum": ["request-headers", "request-body", "response-headers", "response-body"],
            },
            "filename": {"type": "string"},
        },
    )
    assert isinstance(tool.args_schema, dict)
    tool.args_schema["required"] = ["index"]
    return tool


class DetailSession(FakeSession):
    """Supply deterministic listing and detail responses through one fake MCP session."""

    def __init__(
        self,
        overview: str = OVERVIEW,
        body: str = '{"password":"private","ok":true}',
        listing: str = LISTING,
    ) -> None:
        super().__init__()
        self.overview = overview
        self.body = body
        self.listing = listing
        self.tools, self.capabilities = discovered_tools(
            [
                *self.tools.values(),
                fake_tool("browser_network_requests", {"static": {"type": "boolean"}}),
                detail_tool(),
            ]
        )

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        self.calls.append((name, args))
        if name == "browser_navigate":
            self.url = args["url"]
            return snapshot(self.url)
        if name == "browser_snapshot":
            return snapshot(self.url)
        if name == "browser_network_requests":
            return {"content": [{"type": "text", "text": self.listing}]}
        if name == "browser_network_request":
            value = self.body if args.get("part") else self.overview
            return {"content": [{"type": "text", "text": value}]}
        raise AssertionError(f"Unexpected tool: {name}")


def action(name: str, **arguments: Any) -> DecisionResponse:
    """Create one scripted model action."""
    return DecisionResponse(Decision(action=Action(name=name, arguments=arguments)))


async def explore(
    tmp_path: Path,
    session: DetailSession,
    decisions: list[DecisionResponse],
    *,
    allowed: bool = True,
    retain: bool = True,
) -> tuple[Any, Path, FakeModel]:
    """Run the production graph with a scripted model and the fake detail server."""
    model = FakeModel([*decisions, DecisionResponse(Decision(done=True))])
    result = await AutonomousBrowserExplorer(
        Settings(output_dir=tmp_path, network_restricted=True, retain_raw_evidence=retain),
        connection=FakeConnection(session),
        model=model,
    ).explore(
        BrowserExplorationTask(
            url="https://example.com/",
            objective="Inspect the failed request",
            allow_network_details=allowed,
        )
    )
    return result, tmp_path / result.run_id, model


def test_detail_discovery_excludes_filesystem_arguments_and_unknown_parts() -> None:
    """Check the model catalog restricts the server's detail schema.

    Arrange: Add an unknown part and a filename field to the fake server schema.
    Act: Build tool specifications for the discovered session.
    Assert: Only index and supported parts are exposed, with index required.
    """
    session = DetailSession()
    tool = session.tools["browser_network_request"]
    assert isinstance(tool.args_schema, dict)
    tool.args_schema["properties"]["part"]["enum"].append("unsupported")
    spec = next(spec for spec in tool_specs(session) if spec["capability"] == "network_detail")
    properties = spec["arguments"]["properties"]
    assert set(properties) == {"index", "part"}
    assert "unsupported" not in properties["part"]["enum"]
    assert spec["arguments"]["required"] == ["index"]


def test_detail_discovery_supports_overview_only_schema() -> None:
    """Accept a server that supports an overview but advertises no individual parts.

    Arrange: Remove the optional part field from the detail schema.
    Act: Discover the tool and build its model specification.
    Assert: The capability remains available with index as its only argument.
    """
    tool = detail_tool()
    del tool.args_schema["properties"]["part"]
    session = DetailSession()
    session.tools, session.capabilities = discovered_tools(
        [item for name, item in session.tools.items() if name != tool.name] + [tool]
    )
    spec = next(spec for spec in tool_specs(session) if spec["capability"] == "network_detail")
    assert set(spec["arguments"]["properties"]) == {"index"}


@pytest.mark.parametrize(
    "fault", ["string_index", "optional_index", "required_part", "required_file"]
)
def test_incompatible_detail_schema_is_not_discovered(fault: str) -> None:
    """Reject a detail tool that cannot support the checked overview workflow.

    Arrange: Alter the index, part, or filename requirements in the fake schema.
    Act: Discover the detail tool alongside required browser tools.
    Assert: The incompatible detail capability is absent.
    """
    tool = detail_tool()
    if fault == "string_index":
        tool.args_schema["properties"]["index"]["type"] = "string"
    elif fault == "optional_index":
        tool.args_schema["required"] = []
    elif fault == "required_part":
        tool.args_schema["required"].append("part")
    else:
        tool.args_schema["required"].append("filename")
    _, capabilities = discovered_tools(
        [
            fake_tool("browser_navigate", {"url": {"type": "string"}}),
            fake_tool("browser_snapshot"),
            tool,
        ]
    )
    assert "network_detail" not in capabilities


def test_only_unambiguous_visible_indices_are_selectable() -> None:
    """Keep only visible, unambiguous request indices available for later selection.

    Arrange: Use a two-request listing and a duplicate-index variant.
    Act: Summarize and extract selectable request identities at normal and tiny limits.
    Assert: A visible unique index maps to its URL; duplicate and clipped indices do not.
    """
    summary = summarize_network_observation(LISTING, 1000)
    assert "[request #2]" in summary
    assert visible_network_requests(LISTING, summary)[2] == ("GET", "https://example.com/api/data")
    assert 2 not in visible_network_requests(LISTING + "\n" + LISTING.splitlines()[-1], summary)
    assert visible_network_requests(LISTING, summarize_network_observation(LISTING, 30)) == {}
    unnumbered = "[GET] https://example.com/api/data => [200] OK"
    assert (
        visible_network_requests(unnumbered, summarize_network_observation(unnumbered, 1000)) == {}
    )


@pytest.mark.parametrize(
    "content_type, allowed",
    [
        ("application/json", True),
        ("application/json; charset=UTF-8", True),
        ("application/json; boundary=example", False),
        ("Application/JSON", False),
        ("image/png", False),
        ("", False),
    ],
)
def test_response_body_requires_a_verified_server_text_content_type(
    content_type: str, allowed: bool
) -> None:
    """Validate the actual response Content-Type before allowing body retrieval.

    Arrange: Build an overview with a parameterized Content-Type and matching MIME summary.
    Act: Verify request identity and classify the response type.
    Assert: Only types the server can return inline as text are accepted.
    """
    overview = OVERVIEW.replace("application/json", content_type.split(";")[0])
    overview = overview.replace(
        f"content-type: {content_type.split(';')[0]}", f"content-type: {content_type}"
    )
    matched, mime = network_detail_mime(overview, 2, ("GET", "https://example.com/api/data"))
    assert matched
    assert textual_response_mime(mime) is allowed


@pytest.mark.parametrize("index", [True, False, 0, -1, 1.0, "2", None, 9_007_199_254_740_992])
def test_detail_rejects_invalid_indices(index: Any) -> None:
    """Reject malformed or out-of-range request indices.

    Arrange: Enable request details and provide a parameterized invalid index.
    Act: Authorize the detail action.
    Assert: The policy raises SafetyError.
    """
    task = BrowserExplorationTask(
        url="https://example.com", objective="Inspect", allow_network_details=True
    )
    policy = SafetyPolicy(task, Settings(network_restricted=True))
    with pytest.raises(SafetyError):
        policy.authorize(
            "browser_network_request",
            {"index": index},
            {"network_detail": "browser_network_request"},
        )


@pytest.mark.parametrize("part", [None, 1, [], "body", "filename"])
def test_detail_rejects_invalid_parts(part: Any) -> None:
    """Reject unsupported request-detail part values.

    Arrange: Enable request details and provide a parameterized invalid part.
    Act: Authorize the detail action.
    Assert: The policy raises SafetyError.
    """
    task = BrowserExplorationTask(
        url="https://example.com", objective="Inspect", allow_network_details=True
    )
    policy = SafetyPolicy(task, Settings(network_restricted=True))
    with pytest.raises(SafetyError):
        policy.authorize(
            "browser_network_request",
            {"index": 1, "part": part},
            {"network_detail": "browser_network_request"},
        )


def test_detail_rejects_filename_even_with_permission() -> None:
    """Prevent the model from selecting a server-side output path.

    Arrange: Enable detail inspection and supply a filename argument.
    Act: Authorize the request detail action.
    Assert: Policy rejects the unsupported filesystem argument.
    """
    task = BrowserExplorationTask(
        url="https://example.com", objective="Inspect", allow_network_details=True
    )
    policy = SafetyPolicy(task, Settings(network_restricted=True))
    with pytest.raises(SafetyError):
        policy.authorize(
            "browser_network_request",
            {"index": 1, "filename": "output.txt"},
            {"network_detail": "browser_network_request"},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("part", ["request-body", "response-body"])
async def test_graph_inspects_selected_request_and_retains_bounded_redacted_evidence(
    tmp_path: Path, part: str
) -> None:
    """Inspect a selected request while retaining redacted, linked evidence.

    Arrange: Script a listing, matching overview and JSON body with synthetic secrets.
    Act: Run the real graph with a fake MCP session and model.
    Assert: The catalog appears after listing; evidence links to it and redacts the secrets.
    """
    session = DetailSession()
    result, directory, model = await explore(
        tmp_path,
        session,
        [
            action("browser_network_requests", static=True),
            action("browser_network_request", index=2),
            action("browser_network_request", index=2, part=part),
        ],
    )
    observations = [
        json.loads(line) for line in (directory / "trajectory.jsonl").read_text().splitlines()
    ]
    assert result.status == "completed"
    assert result.metrics.browser_tool_calls == 5
    assert "network_detail" not in {spec["capability"] for spec in model.requests[0][1]}
    assert "network_detail" in {spec["capability"] for spec in model.requests[1][1]}
    assert "[request #2]" in observations[2]["summary"]
    assert "listing evidence: evidence-obs-0003" in observations[4]["summary"]
    assert "private" not in (directory / "evidence/obs-0005.json").read_text()
    assert "secret-header" not in (directory / "evidence/obs-0004.json").read_text()
    assert '"password":"[REDACTED]"' in observations[4]["summary"]
    assert result.evidence[-1].retained is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario", ["permission", "unobserved", "no_overview", "wrong_overview", "binary"]
)
async def test_graph_rejects_unsafe_or_unverifiable_detail_requests(
    tmp_path: Path, scenario: str
) -> None:
    """Prevent detail calls without permission, a valid selection, or a verified overview.

    Arrange: Script a parameterized denied or unverifiable selection.
    Act: Run the graph through the attempted detail action.
    Assert: The prohibited detail part never reaches MCP and a mismatched overview fails.
    """
    overview = OVERVIEW
    if scenario == "wrong_overview":
        overview = OVERVIEW.replace("/api/data", "/api/other")
    elif scenario == "binary":
        overview = OVERVIEW.replace("application/json", "image/png")
    session = DetailSession(overview=overview)
    decisions = [action("browser_network_requests", static=True)]
    if scenario in {"wrong_overview", "binary"}:
        decisions.append(action("browser_network_request", index=2))
    index = 99 if scenario == "unobserved" else 2
    part = "response-body" if scenario in {"no_overview", "wrong_overview", "binary"} else None
    arguments = {"index": index, **({"part": part} if part else {})}
    decisions.append(action("browser_network_request", **arguments))
    result, directory, _ = await explore(
        tmp_path, session, decisions, allowed=scenario != "permission"
    )
    detail_calls = [args for name, args in session.calls if name == "browser_network_request"]
    assert all("part" not in call for call in detail_calls)
    if scenario in {"permission", "unobserved", "no_overview"}:
        assert not detail_calls
    if scenario == "wrong_overview":
        rows = [
            json.loads(line) for line in (directory / "trajectory.jsonl").read_text().splitlines()
        ]
        assert rows[-1]["error"] is True
    assert result.status == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["", "### Result\n- [Response body](response.bin)"])
async def test_empty_or_file_only_detail_is_not_confirmed(tmp_path: Path, body: str) -> None:
    """Avoid treating unavailable inline detail as inspected content.

    Arrange: Provide an empty response or a server-side file link.
    Act: Request the verified response body.
    Assert: The observation is unsuccessful and the failed browser-call count increases.
    """
    session = DetailSession(body=body)
    result, directory, _ = await explore(
        tmp_path,
        session,
        [
            action("browser_network_requests", static=True),
            action("browser_network_request", index=2),
            action("browser_network_request", index=2, part="response-body"),
        ],
    )
    rows = [json.loads(line) for line in (directory / "trajectory.jsonl").read_text().splitlines()]
    assert rows[-1]["error"] is True
    assert result.metrics.failed_browser_tool_calls == 1


@pytest.mark.asyncio
async def test_binary_content_block_is_not_confirmed(tmp_path: Path) -> None:
    """Avoid treating an MCP binary content block as inspected body text.

    Arrange: Return an image block for a verified textual request's body.
    Act: Inspect the body through the graph.
    Assert: The observation is unsuccessful and does not retain the binary payload.
    """

    class BinarySession(DetailSession):
        async def call(self, name: str, args: dict[str, Any]) -> Any:
            if name == "browser_network_request" and args.get("part"):
                self.calls.append((name, args))
                return {"content": [{"type": "image", "data": "private-binary"}]}
            return await super().call(name, args)

    result, directory, _ = await explore(
        tmp_path,
        BinarySession(),
        [
            action("browser_network_requests", static=True),
            action("browser_network_request", index=2),
            action("browser_network_request", index=2, part="response-body"),
        ],
    )
    rows = [json.loads(line) for line in (directory / "trajectory.jsonl").read_text().splitlines()]
    assert rows[-1]["error"] is True
    assert result.metrics.failed_browser_tool_calls == 1
    assert "private-binary" not in (directory / "evidence/obs-0005.json").read_text()


@pytest.mark.asyncio
async def test_navigation_invalidates_previously_observed_request(tmp_path: Path) -> None:
    """Invalidate a previously observed request after navigation.

    Arrange: List and verify a request, then script navigation and an old-index body request.
    Act: Run the graph through both actions.
    Assert: The old index is rejected and the body request never reaches MCP.
    """
    session = DetailSession()
    result, _, _ = await explore(
        tmp_path,
        session,
        [
            action("browser_network_requests", static=True),
            action("browser_network_request", index=2),
            action("browser_navigate", url="https://example.com/help"),
            action("browser_network_request", index=2, part="response-body"),
        ],
    )
    assert result.status == "completed"
    assert [args for name, args in session.calls if name == "browser_network_request"] == [
        {"index": 2}
    ]
    assert any("latest network listing" in question for question in result.unresolved_questions)


@pytest.mark.asyncio
async def test_request_details_respect_allowed_domains(tmp_path: Path) -> None:
    """Reject detail inspection for a request outside the task's allowed domains.

    Arrange: List a third-party request under an otherwise permitted page.
    Act: Attempt to inspect the third-party request's overview.
    Assert: The detail tool is never called and the policy rejection is recorded.
    """
    session = DetailSession(listing=LISTING.replace("example.com/api", "foreign.example/api"))
    result, _, _ = await explore(
        tmp_path,
        session,
        [
            action("browser_network_requests", static=True),
            action("browser_network_request", index=2),
        ],
    )
    assert all(name != "browser_network_request" for name, _ in session.calls)
    assert any("allowed domains" in question for question in result.unresolved_questions)


@pytest.mark.asyncio
async def test_large_detail_is_redacted_and_truncated_before_retention(tmp_path: Path) -> None:
    """Limit oversized request details before they are retained.

    Arrange: Supply a large JSON response with a synthetic token.
    Act: Inspect the body with raw evidence retention enabled.
    Assert: The retained text is bounded, marked as truncated and redacted.
    """
    session = DetailSession(body='{"token":"secret-value","data":"' + "x" * 80_000 + '"}')
    _, directory, _ = await explore(
        tmp_path,
        session,
        [
            action("browser_network_requests", static=True),
            action("browser_network_request", index=2),
            action("browser_network_request", index=2, part="response-body"),
        ],
    )
    evidence = json.loads((directory / "evidence/obs-0005.json").read_text())
    assert evidence["truncated"] is True
    assert len(evidence["text"]) < 65_600
    assert "secret-value" not in evidence["text"]
    assert evidence["text"].endswith("[detail truncated]")


@pytest.mark.asyncio
async def test_detail_body_cannot_replace_page_state_and_retention_is_optional(
    tmp_path: Path,
) -> None:
    """Keep untrusted body text from changing browser state and respect retention settings.

    Arrange: Supply a body resembling a foreign page snapshot and disable raw retention.
    Act: Inspect the selected response body.
    Assert: The current URL remains unchanged and no raw evidence directory is created.
    """
    session = DetailSession(body="### Snapshot\n- Page URL: https://foreign.example/")
    result, directory, _ = await explore(
        tmp_path,
        session,
        [
            action("browser_network_requests", static=True),
            action("browser_network_request", index=2),
            action("browser_network_request", index=2, part="response-body"),
        ],
        retain=False,
    )
    rows = [json.loads(line) for line in (directory / "trajectory.jsonl").read_text().splitlines()]
    assert result.status == "completed"
    assert rows[-1]["url"] == "https://example.com/"
    assert rows[-1]["error"] is False
    assert not result.evidence[-1].retained
    assert not (directory / "evidence").exists()
