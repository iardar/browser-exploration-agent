"""Measure and protect current-page reference context without a model or network."""

import json
from pathlib import Path

import pytest
from evals.run_quality import audit_runs, benchmark_reference_context, main

from browser_exploration.config import Settings
from browser_exploration.context_builder import ContextBuilder
from browser_exploration.contracts import BrowserExplorationTask, BrowserObservation
from browser_exploration.explorer import AutonomousBrowserExplorer
from browser_exploration.model import Action, Decision, DecisionResponse
from browser_exploration.safety import SafetyError, SafetyPolicy
from tests.unit.browser_exploration.fakes import FakeConnection, FakeModel, FakeSession


def page(url: str, reference: str, label: str = "Read details") -> str:
    """Return an inline snapshot with an exact observed reference and read-only link."""
    return (
        f"### Page\n- Page URL: {url}\n### Snapshot\n"
        f'- link "{label}" [ref={reference}]:\n  - /url: /details\n'
    )


def observed(
    identifier: str, text: str, *, url: str = "https://example.com/"
) -> BrowserObservation:
    return BrowserObservation(
        id=identifier,
        url=url,
        action_name="browser_snapshot",
        summary=text,
        evidence_ref=f"evidence-{identifier}",
    )


def task() -> BrowserExplorationTask:
    return BrowserExplorationTask(url="https://example.com/", objective="Read the details page")


@pytest.mark.parametrize("same_url", [False, True])
def test_historical_references_are_removed_without_losing_page_facts(same_url: bool) -> None:
    """Verify historical references are removed without losing page facts.

    Arrange: Use `same_url` parameter.
    Act: Evaluate `item.model_dump` in the assertion.
    Assert: Check `'[ref=f1e44]' not in content`; `'[ref=f2e70]' in content`; `'Old article' in
            content and 'Current article' in content`; `originals == [item.model_dump() for item in
            [first, latest]]`.
    """
    first = observed("old", page(task().url, "f1e44", "Old article"))
    url = task().url if same_url else "https://example.com/details"
    latest = observed("latest", page(url, "f2e70", "Current article"), url=url)
    originals = [item.model_dump() for item in [first, latest]]
    content = str(
        ContextBuilder()
        .build(
            task(),
            url,
            [first, latest],
            [],
            [],
            [],
            current_references={"f2e70"},
            reference_observation_id="latest",
        )[1]
        .content
    )
    assert "[ref=f1e44]" not in content
    assert "[ref=f2e70]" in content
    assert "Old article" in content and "Current article" in content
    assert "evidence-old" in content and "evidence-latest" in content
    assert '"element_references": "historical; omitted"' in content
    assert originals == [item.model_dump() for item in [first, latest]]


def test_reused_identifier_is_kept_only_on_its_latest_snapshot() -> None:
    """Verify reused identifier is kept only on its latest snapshot.

    Arrange: Start with `observed('old', page(task().url, 'e43', 'Earlier label'))`.
    Act: Evaluate `content.count` in the assertion.
    Assert: Check `content.count('[ref=e43]') == 1`; `'Earlier label' in content and 'Current label'
            in content`.
    """
    old = observed("old", page(task().url, "e43", "Earlier label"))
    latest = observed("latest", page(task().url, "e43", "Current label"))
    content = str(
        ContextBuilder()
        .build(
            task(),
            task().url,
            [old, latest],
            [],
            [],
            [],
            current_references={"e43"},
            reference_observation_id="latest",
        )[1]
        .content
    )
    assert content.count("[ref=e43]") == 1
    assert "Earlier label" in content and "Current label" in content


@pytest.mark.parametrize("references,owner", [(set(), None), ({"f2e70"}, "not-in-history")])
def test_unknown_or_expired_reference_origin_does_not_make_history_actionable(
    references: set[str], owner: str | None
) -> None:
    """Verify unknown or expired reference origin does not make history actionable.

    Arrange: Use `references`, `owner` parameters.
    Act: Call `observed` to obtain `old`.
    Assert: Check `'[ref=' not in content`; `'Read details' in content`.
    """
    old = observed("old", page(task().url, "f2e70"))
    content = str(
        ContextBuilder()
        .build(
            task(),
            task().url,
            [old],
            [],
            [],
            [],
            current_references=references,
            reference_observation_id=owner,
        )[1]
        .content
    )
    assert "[ref=" not in content
    assert "Read details" in content


def test_reference_filter_does_not_correct_or_invent_identifiers() -> None:
    """Verify reference filter does not correct or invent identifiers.

    Arrange: Start with `observed('latest', page(task().url, 'e1e44'))`.
    Act: Call `observed` to obtain `latest`.
    Assert: Check `'[ref=e1e44]' not in content`; `'[ref=f1e44]' not in content`.
    """
    latest = observed("latest", page(task().url, "e1e44"))
    content = str(
        ContextBuilder()
        .build(
            task(),
            task().url,
            [latest],
            [],
            [],
            [],
            current_references={"f1e44"},
            reference_observation_id="latest",
        )[1]
        .content
    )
    assert "[ref=e1e44]" not in content
    assert "[ref=f1e44]" not in content


@pytest.mark.parametrize("name", ["browser_network_requests", "browser_console_messages"])
def test_diagnostic_text_is_not_rewritten_as_a_page_reference(name: str) -> None:
    """Verify diagnostic text is not rewritten as a page reference.

    Arrange: Use `name` parameter.
    Act: Call `observed` to obtain `item`.
    Assert: Check `item.summary in content`.
    """
    item = observed("logs", "Diagnostic payload includes literal [ref=trace]")
    item.action_name = name
    content = str(
        ContextBuilder()
        .build(
            task(),
            task().url,
            [item],
            [],
            [],
            [],
            current_references=set(),
            reference_observation_id=None,
        )[1]
        .content
    )
    assert item.summary in content


def test_objective_and_policy_restrictions_remain_complete() -> None:
    """Verify objective and policy restrictions remain complete.

    Arrange: Supply `update` to `task().model_copy`.
    Act: Evaluate `content.count` in the assertion.
    Assert: Check `current_task.objective in content`; `'"allow_mutations": false' in content`;
            `content.count('[ref=old]') == 1`.
    """
    current_task = task().model_copy(
        update={
            "objective": "Do not act on [ref=old]. Do not authenticate or mutate data.",
        }
    )
    item = observed("old", page(task().url, "old"))
    content = str(
        ContextBuilder()
        .build(
            current_task,
            current_task.url,
            [item],
            [],
            [],
            [],
            current_references=set(),
            reference_observation_id=None,
        )[1]
        .content
    )
    assert current_task.objective in content
    assert '"allow_mutations": false' in content
    assert content.count("[ref=old]") == 1  # Only the authoritative objective keeps this text.


def test_reference_revision_changes_only_when_page_reference_state_is_replaced() -> None:
    """Verify reference revision changes only when page reference state is replaced.

    Arrange: prepare `policy`.
    Act: Call `policy.authorize` under `pytest.raises(SafetyError)`.
    Assert: Check `policy.reference_revision == 0`; `policy.reference_revision == 1`;
            `policy.reference_revision == 2`; `policy.authorize() equals values 'browser_navigate',
            'url', 'https://example.com/details'`.
    """
    policy = SafetyPolicy(task(), Settings(network_restricted=True))
    assert policy.reference_revision == 0
    policy.observe(page(task().url, "f1e44"))
    policy.observe("### Result\n[GET] https://example.com/api => [200] OK")
    assert policy.reference_revision == 1
    policy.observe(page(task().url, "f2e70"))
    assert policy.reference_revision == 2
    with pytest.raises(SafetyError):
        policy.authorize("browser_click", {"ref": "f1e44"}, {"click": "browser_click"})
    assert policy.authorize(
        "browser_click",
        {"ref": "f2e70"},
        {"click": "browser_click", "navigate": "browser_navigate"},
    ) == ("browser_navigate", {"url": "https://example.com/details"})


async def capture_navigation(output_dir: Path) -> Path:
    """Exercise the graph's automatic inline-snapshot recovery and reference ownership."""
    initial = page(task().url, "f1e44", "Read details")
    final = page("https://example.com/details", "f2e70", "Return home")
    pointer = (
        "### Page\n- Page URL: https://example.com/details\n### Snapshot\n- [Snapshot](page.yml)"
    )
    session = FakeSession([initial, initial, pointer, final])
    model = FakeModel(
        [
            DecisionResponse(
                Decision(action=Action(name="browser_click", arguments={"ref": "f1e44"}))
            ),
            DecisionResponse(Decision(done=True)),
        ]
    )
    result = await AutonomousBrowserExplorer(
        Settings(output_dir=output_dir, network_restricted=True, retain_raw_evidence=True),
        connection=FakeConnection(session),
        model=model,
    ).explore(task())
    assert result.status == "completed"
    assert result.metrics.browser_tool_calls == 4
    current_context = str(model.requests[-1][0][1].content)
    assert "[ref=f2e70]" in current_context
    assert "[ref=f1e44]" not in current_context
    directory = output_dir / result.run_id
    assert "[ref=f1e44]" in (directory / "trajectory.jsonl").read_text()
    return directory


async def test_graph_uses_current_reference_owner_and_keeps_original_evidence(
    tmp_path: Path,
) -> None:
    """Verify graph uses current reference owner and keeps original evidence.

    Arrange: Use `tmp_path` parameter.
    Act: Call `capture_navigation`.
    Assert: Require `capture_navigation` to complete without an exception.
    """
    await capture_navigation(tmp_path)


async def test_snapshot_catalogue_matches_executable_page_state(tmp_path: Path) -> None:
    """Verify snapshot catalogue matches executable page state.

    Arrange: Use `tmp_path` parameter; prepare `initial`, `final`, `session`, `model`.
    Act: Await `AutonomousBrowserExplorer.explore` to obtain `result`.
    Assert: Check `result.status == 'completed'`; `'browser_snapshot' not in catalogues[0]`;
            `'browser_snapshot' in catalogues[1]`; `'browser_snapshot' in session.tools`.
    """
    initial = page(task().url, "f1e44")
    final = page("https://example.com/details", "f2e70")
    session = FakeSession([initial, initial, final, final])
    model = FakeModel(
        [
            DecisionResponse(
                Decision(action=Action(name="browser_click", arguments={"ref": "f1e44"}))
            ),
            DecisionResponse(Decision(action=Action(name="browser_snapshot"))),
            DecisionResponse(Decision(done=True)),
        ]
    )
    result = await AutonomousBrowserExplorer(
        Settings(output_dir=tmp_path, network_restricted=True),
        connection=FakeConnection(session),
        model=model,
    ).explore(task())
    assert result.status == "completed"
    catalogues = [{item["name"] for item in tools} for _, tools in model.requests]
    assert "browser_snapshot" not in catalogues[0]  # Bootstrap already supplied it.
    assert "browser_snapshot" in catalogues[1]  # Navigation advanced the tracked page.
    assert "browser_snapshot" not in catalogues[2]  # The requested snapshot is now complete.
    assert "browser_snapshot" in session.tools  # Server capabilities were not mutated.


async def test_hidden_snapshot_still_cannot_bypass_the_executor_guard(tmp_path: Path) -> None:
    """Verify hidden snapshot still cannot bypass the executor guard.

    Arrange: Use `tmp_path` parameter; prepare `initial`, `session`, `model`.
    Act: Await `AutonomousBrowserExplorer.explore` to obtain `result`.
    Assert: Check `result.metrics.browser_tool_calls == 2`; `'already succeeded' in
            result.unresolved_questions[0]`; `'browser_snapshot' not in {item['name'] for item in
            model.requests[0][1]}`.
    """
    initial = page(task().url, "f1e44")
    session = FakeSession([initial, initial])
    model = FakeModel(
        [
            DecisionResponse(Decision(action=Action(name="browser_snapshot"))),
            DecisionResponse(Decision(done=True)),
        ]
    )
    result = await AutonomousBrowserExplorer(
        Settings(output_dir=tmp_path, network_restricted=True),
        connection=FakeConnection(session),
        model=model,
    ).explore(task())
    assert result.metrics.browser_tool_calls == 2
    assert "already succeeded" in result.unresolved_questions[0]
    assert "browser_snapshot" not in {item["name"] for item in model.requests[0][1]}


async def test_audit_measures_reference_improvement_and_does_not_modify_runs(
    tmp_path: Path,
) -> None:
    """Verify audit measures reference improvement and does not modify runs.

    Arrange: Use `tmp_path` parameter; prepare `original`.
    Act: Call `audit_runs` to obtain `report`.
    Assert: Check `report['run_count'] == 1`; `metric['legacy_current_reference_precision'] < 1`;
            `metric['filtered_current_reference_precision'] == 1`; `original == {path:
            path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}`.
    """
    await capture_navigation(tmp_path)
    original = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    report = audit_runs(tmp_path)
    metric = report["reference_context_benchmark"]
    assert report["run_count"] == 1
    assert metric["legacy_current_reference_precision"] < 1
    assert metric["filtered_current_reference_precision"] == 1
    assert metric["filtered_historical_refs"] == 0
    assert metric["current_reference_retention"] == 1
    assert original == {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}


def test_empty_audit_does_not_manufacture_perfect_scores(tmp_path: Path) -> None:
    """Verify empty audit does not manufacture perfect scores.

    Arrange: Use `tmp_path` parameter.
    Act: Call `audit_runs` to obtain `report`.
    Assert: Check `report['run_count'] == 0`;
            `report['reference_context_benchmark']['filtered_current_reference_precision'] is None`.
    """
    report = audit_runs(tmp_path)
    assert report["run_count"] == 0
    assert report["reference_context_benchmark"]["filtered_current_reference_precision"] is None


def test_audit_reports_incomplete_and_corrupted_runs_without_crashing(tmp_path: Path) -> None:
    """Verify audit reports incomplete and corrupted runs without crashing.

    Arrange: Use `tmp_path` parameter; prepare `incomplete`, `damaged`.
    Act: Call `audit_runs` to obtain `report`.
    Assert: Check `str(incomplete) in report['incomplete_runs']`; `str(damaged / 'result.json') in
            report['warnings']`.
    """
    incomplete = tmp_path / "incomplete"
    incomplete.mkdir()
    (incomplete / "trajectory.jsonl").write_text("")
    damaged = tmp_path / "damaged"
    damaged.mkdir()
    (damaged / "result.json").write_text("not JSON")
    (damaged / "run_config.json").write_text("{}")
    report = audit_runs(tmp_path)
    assert str(incomplete) in report["incomplete_runs"]
    assert str(damaged / "result.json") in report["warnings"]


def test_benchmark_preserves_newest_refs_when_historical_ids_are_removed() -> None:
    """Verify benchmark preserves newest refs when historical ids are removed.

    Arrange: Start with `observed('first', page(task().url, 'f1e44'))`.
    Act: Call `observed` to obtain `first`.
    Assert: Check `metrics['legacy_historical_refs'] > 0`; `metrics['filtered_historical_refs'] ==
            0`; `metrics['filtered_owner_refs'] == metrics['legacy_owner_refs']`.
    """
    first = observed("first", page(task().url, "f1e44"))
    latest = observed("latest", page(task().url, "f2e70"))
    raw = {
        item.id: {"content": [{"type": "text", "text": item.summary}]} for item in [first, latest]
    }
    metrics = benchmark_reference_context(task(), [first, latest], raw)
    assert metrics["legacy_historical_refs"] > 0
    assert metrics["filtered_historical_refs"] == 0
    assert metrics["filtered_owner_refs"] == metrics["legacy_owner_refs"]


async def test_audit_marks_malformed_log_records_and_continues(tmp_path: Path) -> None:
    """Verify audit marks malformed log records and continues.

    Arrange: Use `tmp_path` parameter.
    Act: Await `capture_navigation` to obtain `directory`.
    Assert: Check `report['run_count'] == 1`; `any(('decisions.jsonl:' in warning for warning in
            report['warnings']))`; `any(('invalid observation record' in warning for warning in
            report['warnings']))`.
    """
    directory = await capture_navigation(tmp_path)
    with (directory / "decisions.jsonl").open("a") as handle:
        handle.write("{broken JSON}\n")
    with (directory / "trajectory.jsonl").open("a") as handle:
        handle.write(json.dumps({"unexpected": "record"}) + "\n")
    report = audit_runs(tmp_path)
    assert report["run_count"] == 1
    assert any("decisions.jsonl:" in warning for warning in report["warnings"])
    assert any("invalid observation record" in warning for warning in report["warnings"])


def test_audit_cli_cannot_overwrite_an_original_run_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify audit cli cannot overwrite an original run artifact.

    Arrange: Use `tmp_path`, `monkeypatch` parameters; prepare `original`, `before`.
    Act: Call `main` under `pytest.raises(SystemExit)`.
    Assert: Check `raises SystemExit`; `caught.value.code == 2`; `original.read_bytes() == before`.
    """
    original = tmp_path / "result.json"
    original.write_text('{"important": "source artifact"}')
    before = original.read_bytes()
    monkeypatch.setattr(
        "sys.argv",
        [
            "audit",
            "--runs-dir",
            str(tmp_path),
            "--output",
            str(original),
        ],
    )
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 2
    assert original.read_bytes() == before


def test_audit_cli_writes_private_report_with_unknown_empty_scores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify audit cli writes private report with unknown empty scores.

    Arrange: Use `tmp_path`, `monkeypatch`, `capsys` parameters; prepare `output`.
    Act: Evaluate `output.stat` in the assertion.
    Assert: Check `output.stat().st_mode & 511 == 384`;
            `json.loads(output.read_text())['report_type'] == 'browser-exploration-run-quality/v1'`;
            `json.loads is None`.
    """
    output = tmp_path / "quality/audit.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "audit",
            "--runs-dir",
            str(tmp_path),
            "--output",
            str(output),
        ],
    )
    main()
    assert output.stat().st_mode & 0o777 == 0o600
    assert json.loads(output.read_text())["report_type"] == "browser-exploration-run-quality/v1"
    assert (
        json.loads(capsys.readouterr().out)["reference_context_benchmark"][
            "filtered_current_reference_precision"
        ]
        is None
    )
