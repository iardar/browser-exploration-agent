"""Check the separate detail corpus, fixture and grader without network or provider calls."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from evals.network_request_details.dataset import (
    ROOT,
    case_path,
    expected_examples,
    load_case,
    load_dataset,
    load_oracle,
    objective,
    target_path,
)
from evals.network_request_details.fixture import fixture_response
from evals.network_request_details.grade import grade_run
from evals.network_request_details.probe import ProbeError, probe_case

from browser_exploration.contracts import VerifiedBehavior
from browser_exploration.model import Action, Decision, DecisionResponse
from tests.support.network_request_details import (
    BASE_URL,
    DetailReplaySession,
    run_detail_replay,
    target_index,
)
from tests.unit.browser_exploration.fakes import FakeModel


def _action(name: str, arguments: dict[str, Any]) -> DecisionResponse:
    return DecisionResponse(Decision(action=Action(name=name, arguments=arguments)))


async def _record_case(directory: Path, case_id: str) -> Path:
    case = load_case(case_id)
    oracle = load_oracle()[case_id]
    index = target_index(case_id)
    binary = oracle.get("expected") == "blocked_binary"
    header_from_overview = case["part"] in {"request-headers", "response-headers"}
    exploratory_overviews = 1 if case.get("selector_header") else 0
    observation_number = 4 + exploratory_overviews + (not binary and not header_from_overview)
    evidence_id = f"evidence-obs-{observation_number:04d}"
    claim = (
        f"The response is binary ({oracle['content_type']}) and cannot be inspected safely."
        if binary
        else f"The observed value is {oracle['marker']}."
    )
    decisions: list[DecisionResponse | BaseException] = [
        _action("browser_network_requests", {"static": True}),
    ]
    if case.get("selector_header"):
        decisions.append(_action("browser_network_request", {"index": 3}))
    decisions.append(_action("browser_network_request", {"index": index}))
    if not binary and not header_from_overview:
        decisions.append(_action("browser_network_request", {"index": index, "part": case["part"]}))
    decisions.append(
        DecisionResponse(
            Decision(
                done=True,
                verified_behaviors=[
                    VerifiedBehavior(description=claim, evidence_ids=[evidence_id])
                ],
            )
        )
    )
    result, run_dir = await run_detail_replay(directory, case_id, FakeModel(decisions))
    assert result.status == "completed"
    return run_dir


def _rewrite(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_corpus_has_twenty_two_reviewable_cases_and_separate_oracle() -> None:
    """Validate coverage and prevent expected answers from leaking into task inputs.

    Arrange: Load the canonical cases, hidden oracle and remote example representation.
    Act: Compare IDs, inspected parts, statuses and case objectives.
    Assert: All 22 cases have unique routes, varied parts, errors, POSTs and ambiguity edges.
    """
    cases = load_dataset()["cases"]
    oracle = load_oracle()
    examples = expected_examples()
    ids = [case["id"] for case in cases]
    assert len(ids) == len(set(ids)) == 22
    assert set(ids) == set(oracle) == set(examples)
    assert len({case_path(case_id) for case_id in ids}) == 22
    assert {case["part"] for case in cases} == {
        "request-headers",
        "request-body",
        "response-headers",
        "response-body",
    }
    assert sum(case["method"] == "POST" for case in cases) == 4
    assert sum(oracle[case_id].get("expected") == "blocked_binary" for case_id in ids) == 2
    assert sum(bool(oracle[case_id].get("extra_requests")) for case_id in ids) == 4
    assert {oracle[case_id]["status"] for case_id in ids} >= {200, 422, 500}
    for case_id in ids:
        marker = oracle[case_id].get("marker")
        assert target_path(case_id) in objective(case_id)
        assert marker is None or marker not in objective(case_id)
        secret = oracle[case_id].get("secret")
        assert secret is None or secret not in json.dumps(examples[case_id])


def test_frozen_v1_inputs_match_the_published_baseline_hash() -> None:
    """Preserve the old 18-case dataset independently of the expanded v2 inputs.

    Arrange: Read the archived v1 case and oracle bytes under their original names.
    Act: Recompute the source digest used by the original LangSmith publication.
    Assert: Its hash matches the baseline experiment while the active dataset is v2.
    """
    digest = hashlib.sha256()
    for name in ("cases.json", "oracle.json"):
        digest.update(name.encode() + b"\0" + (ROOT / "v1" / name).read_bytes())
    assert digest.hexdigest() == "861e95cea25ca49a27e25067ea051642280153bf30def04a39220f6beddefcce"
    assert load_dataset()["dataset_id"] == "network-request-details-v2"


@pytest.mark.parametrize("case_id", [f"DET-{number:03d}" for number in range(1, 23)])
def test_fixture_serves_declared_page_decoy_and_target(case_id: str) -> None:
    """Check each fixture route and request body against its independent oracle.

    Arrange: Select one case's method, payload and expected response from the reviewed oracle.
    Act: Request its page, decoy script and target through the pure fixture function.
    Assert: The page blocks on the target, and the target status, MIME, headers and body match.
    """
    case = load_case(case_id)
    oracle = load_oracle()[case_id]
    page = fixture_response(case_path(case_id))
    assert page is not None and page.status == 200
    assert f"Network capture fixture {case_id}" in page.body.decode()
    assert "Network fixture ready" in page.body.decode()
    assert target_path(case_id) in page.body.decode()
    decoy = fixture_response(case_path(case_id) + "decoy.js")
    assert decoy is not None and decoy.status == 200
    response = fixture_response(
        target_path(case_id),
        method=case["method"],
        body=oracle.get("request_body", "").encode(),
        headers=oracle.get("request_headers", {}),
    )
    assert response is not None
    assert response.status == oracle["status"]
    assert response.content_type == oracle["content_type"]
    assert response.headers == oracle.get("response_headers", {})
    if "response_base64" in oracle:
        assert response.body.startswith(b"\x89PNG\r\n")
    else:
        assert response.body.startswith(oracle["response_body"].encode())
    for extra in oracle.get("extra_requests", []):
        decoy = fixture_response(
            case_path(case_id) + extra["path"],
            method=extra["method"],
            body=extra.get("request_body", "").encode(),
            headers=extra.get("request_headers", {}),
        )
        assert decoy is not None and decoy.status == extra["status"]
        assert extra["response_body"].encode() in decoy.body


def test_fixture_rejects_unknown_routes_and_wrong_post_payload() -> None:
    """Ensure a different route, method or POST payload cannot impersonate a reviewed response.

    Arrange: Choose one declared JSON POST case and malformed inputs.
    Act: Ask the pure fixture for unknown and mismatched target routes.
    Assert: Nothing returns the case's expected success payload.
    """
    unknown = fixture_response("/details/UNKNOWN/")
    assert unknown is not None and unknown.status == 404
    assert fixture_response("/not-a-detail/") is None
    wrong_method = fixture_response(target_path("DET-012"), method="GET")
    assert wrong_method is not None and wrong_method.status == 404
    wrong_body = fixture_response(target_path("DET-012"), method="POST", body=b"wrong")
    assert wrong_body is not None and wrong_body.status == 400


@pytest.mark.parametrize("case_id", [f"DET-{number:03d}" for number in range(1, 23)])
async def test_model_free_probe_checks_every_declared_part(case_id: str) -> None:
    """Confirm each corpus case can be probed without a model or LangSmith.

    Arrange: Create a synthetic MCP session with the reviewed listing and detail responses.
    Act: Probe the declared page, overview and permitted part directly.
    Assert: All cases validate; binary cases never request their response body.
    """
    session = DetailReplaySession(case_id)
    await probe_case(session, BASE_URL + case_path(case_id), case_id)
    detail_calls = [args for name, args in session.calls if name == "browser_network_request"]
    if load_oracle()[case_id].get("expected") == "blocked_binary":
        assert detail_calls == [{"index": target_index(case_id)}]
    else:
        assert detail_calls[-1]["index"] == target_index(case_id)
        assert detail_calls[-1]["part"] == load_case(case_id)["part"]


async def test_probe_rejects_a_part_without_expected_inline_content() -> None:
    """Fail preflight if the MCP returns a file link or unrelated body instead of inline detail.

    Arrange: Replace the synthetic part response with a server-side file reference.
    Act: Run the model-free compatibility probe.
    Assert: The probe fails without exposing the returned payload in its diagnostic.
    """
    session = DetailReplaySession("DET-001")
    original = session.call

    async def file_reference(name: str, args: dict[str, Any]) -> Any:
        if name == "browser_network_request" and args.get("part"):
            return {"content": [{"type": "text", "text": "[response](file.txt)"}]}
        return await original(name, args)

    session.call = file_reference  # type: ignore[method-assign]
    with pytest.raises(ProbeError, match="inline detail content is unavailable"):
        await probe_case(session, BASE_URL + case_path("DET-001"), "DET-001")


async def test_probe_rejects_selector_found_only_in_response_headers() -> None:
    """Keep same-URL candidate selection tied to a request header.

    Arrange: Move the selected header from the target request section to its response section.
    Act: Probe the ambiguous same-URL fixture using the modified overview.
    Assert: Preflight rejects the target, even though the selector text appears elsewhere.
    """
    case_id = "DET-022"
    session = DetailReplaySession(case_id)
    original = session.call

    async def moved_header(name: str, args: dict[str, Any]) -> Any:
        result = await original(name, args)
        if name == "browser_network_request" and args == {"index": target_index(case_id)}:
            text = result["content"][0]["text"]
            result["content"][0]["text"] = text.replace(
                "  Request headers\n    x-eval-attempt: chosen\n",
                "  Request headers\n  Response headers\n    x-eval-attempt: chosen\n",
            )
        return result

    session.call = moved_header  # type: ignore[method-assign]
    with pytest.raises(ProbeError, match="no overview matches"):
        await probe_case(session, BASE_URL + case_path(case_id), case_id)


@pytest.mark.parametrize("case_id", [f"DET-{number:03d}" for number in range(1, 23)])
async def test_grader_accepts_complete_graph_replay(tmp_path: Path, case_id: str) -> None:
    """Grade a graph run that lists, verifies and reports the selected request's detail.

    Arrange: Script listing, the target overview, useful parts and an evidence-cited report.
    Act: Run the production graph with a synthetic MCP session and grade saved artifacts.
    Assert: Every reviewed case passes, including binary stop and redacted canary cases.
    """
    run_dir = await _record_case(tmp_path, case_id)
    report = grade_run(case_id, run_dir)
    assert report["passed"] is True, report
    assert all(report["checks"].values())


@pytest.mark.parametrize("case_id", ["DET-010", "DET-011", "DET-016"])
async def test_header_value_in_verified_overview_fulfills_goal(
    tmp_path: Path, case_id: str
) -> None:
    """Accept the baseline header behavior when overview evidence already contains the value.

    Arrange: Replay a header case with a model that cites the verified overview and stops.
    Act: Grade its saved run and count explicit part calls.
    Assert: The observed header is sufficient; a redundant part call is unnecessary.
    """
    run_dir = await _record_case(tmp_path, case_id)
    rows = [json.loads(line) for line in (run_dir / "trajectory.jsonl").read_text().splitlines()]
    detail_args = [
        item["action_arguments"]
        for item in rows
        if item["action_name"] == "browser_network_request"
    ]
    assert detail_args == [{"index": 3}]
    assert grade_run(case_id, run_dir)["passed"] is True


@pytest.mark.parametrize(
    "case_id, change, failed_check",
    [
        ("DET-021", "status", "verified_overview"),
        ("DET-022", "selector", "verified_overview"),
        ("DET-022", "decoy_part", "no_wrong_request"),
    ],
)
async def test_ambiguous_cases_reject_wrong_disambiguation(
    tmp_path: Path, case_id: str, change: str, failed_check: str
) -> None:
    """Reject a wrong status, selector header or decoy-body selection in edge cases.

    Arrange: Record a passing ambiguous case and corrupt one decisive observation.
    Act: Grade the altered saved trace without rerunning the graph.
    Assert: The report fails the check tied to the incorrect target choice.
    """
    run_dir = await _record_case(tmp_path, case_id)
    if change == "decoy_part":
        path = run_dir / "trajectory.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[-1]["action_arguments"]["index"] = 3
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    else:
        path = run_dir / "evidence" / "obs-0004.json"
        if change == "selector":
            path = run_dir / "evidence" / "obs-0005.json"
        value = json.loads(path.read_text())
        value["text"] = value["text"].replace(
            "status: [503]" if change == "status" else "x-eval-attempt: chosen",
            "status: [200]" if change == "status" else "x-eval-attempt: decoy",
        )
        _rewrite(path, value)
    report = grade_run(case_id, run_dir)
    assert report["passed"] is False
    assert failed_check in report["failed_checks"], report


async def test_long_body_is_bounded_before_retention_and_context(tmp_path: Path) -> None:
    """Verify the large response exercises both the raw-detail and model-context limits.

    Arrange: Replay the 80,000-character response-body case with its marker at the start.
    Act: Read the retained detail artifact and trajectory after the graph completes.
    Assert: The artifact signals truncation, the model summary is bounded, and grading passes.
    """
    run_dir = await _record_case(tmp_path, "DET-009")
    raw = json.loads((run_dir / "evidence" / "obs-0005.json").read_text())
    rows = [json.loads(line) for line in (run_dir / "trajectory.jsonl").read_text().splitlines()]
    assert raw["truncated"] is True
    assert raw["text"].startswith("large-009")
    assert raw["text"].endswith("[detail truncated]")
    assert len(raw["text"]) < 66_000
    assert len(rows[-1]["summary"]) <= load_dataset()["task_defaults"]["max_observation_chars"]
    assert grade_run("DET-009", run_dir)["passed"] is True


@pytest.mark.parametrize(
    "fault, failed_check",
    [
        ("wrong_part", "correct_part_or_boundary"),
        ("wrong_index", "no_wrong_request"),
        ("missing_claim", "evidence_backed_report"),
        ("missing_permission", "detail_permission_only"),
        ("wrong_objective", "objective"),
        ("unretained_part", "invalid_artifacts"),
        ("wrong_overview", "verified_overview"),
        ("escaped_evidence_path", "invalid_artifacts"),
        ("filename_argument", "no_filename"),
        ("secret_leak", "secret_redacted"),
    ],
)
async def test_grader_rejects_corrupted_detail_runs(
    tmp_path: Path, fault: str, failed_check: str
) -> None:
    """Reject trace, report and privacy failures even when the run says completed.

    Arrange: Record a passing case and corrupt one saved field or evidence artifact.
    Act: Regrade the modified run without rerunning the agent.
    Assert: The specified check fails, so the artifact cannot pass by completion alone.
    """
    case_id = "DET-015" if fault == "secret_leak" else "DET-001"
    run_dir = await _record_case(tmp_path, case_id)
    if fault in {"wrong_part", "wrong_index", "filename_argument"}:
        path = run_dir / "trajectory.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        detail = rows[-1]
        if fault == "filename_argument":
            detail["action_arguments"]["filename"] = "server-file.txt"
        else:
            detail["action_arguments"]["part" if fault == "wrong_part" else "index"] = (
                "request-headers" if fault == "wrong_part" else 2
            )
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    elif fault in {"missing_claim", "wrong_objective"}:
        path = run_dir / "result.json"
        value = json.loads(path.read_text())
        if fault == "missing_claim":
            value["verified_behaviors"] = []
        else:
            value["objective"] = "Different task"
        _rewrite(path, value)
    elif fault == "missing_permission":
        path = run_dir / "run_config.json"
        value = json.loads(path.read_text())
        value["task"]["allow_network_details"] = False
        _rewrite(path, value)
    elif fault in {"unretained_part", "escaped_evidence_path"}:
        path = run_dir / "result.json"
        value = json.loads(path.read_text())
        if fault == "unretained_part":
            value["evidence"][-1]["retained"] = False
        else:
            value["evidence"][-1]["path"] = "../../outside.json"
        _rewrite(path, value)
    elif fault == "wrong_overview":
        path = run_dir / "evidence" / "obs-0004.json"
        value = json.loads(path.read_text())
        value["text"] = value["text"].replace(
            f"#3 [GET] {BASE_URL}{target_path('DET-001')}",
            f"#3 [GET] {BASE_URL}{case_path('DET-001')}decoy.js",
        )
        _rewrite(path, value)
    else:
        path = run_dir / "evidence" / "obs-0005.json"
        value = json.loads(path.read_text())
        value["text"] += " secret-canary-015"
        _rewrite(path, value)
    report = grade_run(case_id, run_dir)
    assert report["passed"] is False
    assert failed_check in report["failed_checks"], report


async def test_binary_case_rejects_body_proposal(tmp_path: Path) -> None:
    """Distinguish a safe binary stop from a model proposal denied by policy.

    Arrange: Record the passing PNG case and append a rejected response-body proposal.
    Act: Regrade with the same browser trajectory and completed result.
    Assert: The model's unsafe proposal fails the binary boundary check.
    """
    run_dir = await _record_case(tmp_path, "DET-018")
    path = run_dir / "decisions.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "event": "action_rejected",
                    "action": {
                        "name": "browser_network_request",
                        "arguments": {"index": 3, "part": "response-body"},
                    },
                }
            )
            + "\n"
        )
    report = grade_run("DET-018", run_dir)
    assert report["passed"] is False
    assert "correct_part_or_boundary" in report["failed_checks"]
