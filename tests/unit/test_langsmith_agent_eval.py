"""Exercise dataset validation and evaluation orchestration with no external services."""

import argparse
import asyncio
import copy
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from evals.network_capture import langsmith_agent_eval as evaluation

from browser_exploration.config import Settings
from browser_exploration.model import Action, Decision, DecisionResponse
from tests.support.network_capture import BASE_URL, run_network_replay
from tests.unit.browser_exploration.fakes import FakeModel


@pytest.fixture
def client() -> Mock:
    """Supply independent remote objects that tests can corrupt without changing local data."""
    remote = Mock()
    remote.read_dataset.return_value = SimpleNamespace(
        name=evaluation.DATASET_NAME,
        metadata={"source_content_sha256": evaluation._dataset_source_hash()},
    )
    remote.list_examples.return_value = [
        SimpleNamespace(**copy.deepcopy(row)) for row in evaluation._expected_examples().values()
    ]
    remote.aevaluate = AsyncMock()
    return remote


def test_dataset_readback_accepts_all_cases_in_any_order(client: Mock) -> None:
    client.list_examples.return_value.reverse()
    examples = evaluation._validate_dataset(client, evaluation.DATASET_ID)
    assert {example.inputs["case_id"] for example in examples if example.inputs} == {
        "NET-001",
        "NET-002",
        "NET-003",
    }
    client.read_dataset.assert_called_once_with(dataset_id=evaluation.DATASET_ID)
    client.list_examples.assert_called_once_with(dataset_id=evaluation.DATASET_ID, limit=100)


@pytest.mark.parametrize(
    "corruption",
    [
        "name",
        "hash",
        "metadata",
        "missing",
        "extra",
        "duplicate",
        "no_input",
        "bad_id",
        "path",
        "objective",
        "limits",
        "oracle",
    ],
)
def test_dataset_drift_prevents_evaluation(client: Mock, corruption: str) -> None:
    dataset = client.read_dataset.return_value
    examples = client.list_examples.return_value
    if corruption == "name":
        dataset.name = "different-dataset"
    elif corruption == "hash":
        dataset.metadata["source_content_sha256"] = "stale"
    elif corruption == "metadata":
        dataset.metadata = None
    elif corruption == "missing":
        examples.pop()
    elif corruption == "extra":
        extra = copy.deepcopy(examples[0])
        extra.inputs["case_id"] = "NET-999"
        examples.append(extra)
    elif corruption == "duplicate":
        examples.append(copy.deepcopy(examples[0]))
    elif corruption == "no_input":
        examples[0].inputs = None
    elif corruption == "bad_id":
        examples[0].inputs["case_id"] = 1
    elif corruption == "oracle":
        examples[0].outputs["expected_requests"][0]["status"] = 500
    else:
        examples[0].inputs[corruption] = "changed"
    with pytest.raises(evaluation.EvaluationSetupError):
        evaluation._validate_dataset(client, evaluation.DATASET_ID)
    client.aevaluate.assert_not_called()


@pytest.mark.parametrize("method", ["read_dataset", "list_examples"])
def test_remote_errors_do_not_expose_response_text(client: Mock, method: str) -> None:
    getattr(client, method).side_effect = RuntimeError("Bearer private-secret")
    with pytest.raises(evaluation.EvaluationSetupError, match="RuntimeError") as caught:
        evaluation._validate_dataset(client, evaluation.DATASET_ID)
    assert "private-secret" not in str(caught.value)


@pytest.mark.parametrize(
    "base", ["http://localhost:8766", "http://127.0.0.1:8766", "http://[::1]:8766"]
)
def test_local_fixture_requires_explicit_permission(base: str) -> None:
    with pytest.raises(evaluation.EvaluationSetupError, match="ALLOW_PRIVATE_NETWORK"):
        evaluation._fixture_url(base, "/case", Settings())
    assert evaluation._fixture_url(base + "/", "/case", Settings(allow_private_network=True)) == (
        base + "/case"
    )


@pytest.mark.parametrize(
    "base,path",
    [
        ("file:///tmp/fixture", "/case"),
        ("https:///", "/case"),
        ("https://user:password@fixture.example", "/case"),
        (BASE_URL + "/nested", "/case"),
        (BASE_URL + "?key=value", "/case"),
        (BASE_URL + "#fragment", "/case"),
        (BASE_URL, "relative"),
        (BASE_URL, "https://foreign.example/case"),
        (BASE_URL, "//foreign.example/case"),
        (BASE_URL, "/case?query=yes"),
        (BASE_URL, "/case#fragment"),
    ],
)
def test_fixture_origin_and_case_path_cannot_redirect_the_target(base: str, path: str) -> None:
    with pytest.raises(evaluation.EvaluationSetupError):
        evaluation._fixture_url(base, path, Settings())


def scores(outputs: Any, reference: Any, case_id: str = "NET-001") -> dict[str, Any]:
    return {
        item["key"]: item["score"]
        for item in evaluation.score_capture({"case_id": case_id}, outputs, reference)["results"]
    }


def test_feedback_preserves_failure_zero_and_unmeasured_values(client: Mock) -> None:
    reference = client.list_examples.return_value[0].outputs
    assert scores(
        {
            "grade": {
                "passed": True,
                "capture_recall": 1,
                "checks": {"linked_retained_evidence": True, "no_unexpected_requests": False},
            }
        },
        reference,
    ) == {
        "capture_pass": 1.0,
        "oracle_consistent": 1.0,
        "capture_recall": 1,
        "failure_summary_recall": None,
        "no_unexpected_requests": 0.0,
        "evidence_retained": 1.0,
    }


@pytest.mark.parametrize(
    "output",
    [
        None,
        {},
        {"grade": None},
        {"grade": "invalid"},
        {"grade": {"checks": None}},
        {
            "grade": {
                "passed": "true",
                "checks": {"no_unexpected_requests": 1, "linked_retained_evidence": "true"},
            }
        },
    ],
)
def test_missing_or_malformed_grade_cannot_pass(client: Mock, output: Any) -> None:
    feedback = scores(output, client.list_examples.return_value[0].outputs)
    assert feedback == {
        "capture_pass": 0.0,
        "oracle_consistent": 1.0,
        "capture_recall": None,
        "failure_summary_recall": None,
        "no_unexpected_requests": None,
        "evidence_retained": None,
    }


@pytest.mark.parametrize(
    "reference", [None, {}, {"expected_requests": None}, {"expected_requests": []}]
)
def test_oracle_mismatch_invalidates_all_quality_scores(reference: Any) -> None:
    feedback = scores({"grade": {"passed": True, "capture_recall": 1}}, reference)
    assert feedback.pop("oracle_consistent") == 0
    assert all(value is None for value in feedback.values())


def test_unknown_case_cannot_match_a_reference(client: Mock) -> None:
    feedback = scores({}, client.list_examples.return_value[0].outputs, "NET-999")
    assert feedback["oracle_consistent"] == 0 and feedback["capture_pass"] is None


@pytest.mark.parametrize("git_available", [True, False])
def test_source_metadata_fingerprints_local_inputs_even_without_a_git_revision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    git_available: bool,
) -> None:
    root = tmp_path / "evals" / "network_capture"
    root.mkdir(parents=True)
    inputs = (root / "cases.json", root / "requests.csv")
    for path in (*inputs, tmp_path / "pyproject.toml", tmp_path / "uv.lock"):
        path.write_text("version-one")
    monkeypatch.setattr(evaluation, "ROOT", root)
    monkeypatch.setattr(evaluation, "_ORACLE_HASH_INPUTS", inputs)

    def git(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        output = "revision\n" if command[1] == "rev-parse" else " M tracked.py\n"
        return subprocess.CompletedProcess(command, 0 if git_available else 1, stdout=output)

    monkeypatch.setattr(evaluation.subprocess, "run", git)
    before = evaluation._source_metadata()
    assert before["source_revision"] == ("revision" if git_available else "unknown")
    assert before["source_dirty"] is (True if git_available else None)
    assert before == evaluation._source_metadata()
    inputs[1].write_text("version-two")
    assert (
        evaluation._source_metadata()["source_snapshot_sha256"] != before["source_snapshot_sha256"]
    )


@pytest.fixture
def setup_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, client: Mock
) -> argparse.Namespace:
    monkeypatch.setenv("BROWSER_NETWORK_RESTRICTED", "true")
    monkeypatch.setattr(evaluation, "Client", lambda: client)
    monkeypatch.setattr(
        evaluation, "_source_metadata", lambda: {"source_revision": "test-revision"}
    )
    monkeypatch.setattr(
        evaluation,
        "check_connection",
        AsyncMock(
            return_value={
                "status": "connected",
                "capabilities": {"network": "browser_network_requests"},
            }
        ),
    )
    return argparse.Namespace(
        output_dir=tmp_path,
        fixture_base_url=BASE_URL,
        dataset_id=evaluation.DATASET_ID,
        check_only=False,
        experiment_prefix="unit-test",
        num_repetitions=2,
    )


async def test_preflight_never_runs_an_agent(
    monkeypatch: pytest.MonkeyPatch,
    client: Mock,
    setup_runner: argparse.Namespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    explorer = Mock(side_effect=AssertionError("Agent must not start during check-only"))
    monkeypatch.setattr(evaluation, "AutonomousBrowserExplorer", explorer)
    setup_runner.check_only = True
    await evaluation.run_evaluation(setup_runner)
    status = json.loads(capsys.readouterr().out)
    assert status["status"] == "ready" and status["model_calls"] == 0
    assert status["dataset_examples"] == 3
    explorer.assert_not_called()
    client.aevaluate.assert_not_called()


@pytest.mark.parametrize("failure", ["network_boundary", "mcp_failed", "no_network_tool"])
async def test_preflight_failure_prevents_experiment_creation(
    monkeypatch: pytest.MonkeyPatch,
    client: Mock,
    setup_runner: argparse.Namespace,
    failure: str,
) -> None:
    if failure == "network_boundary":
        monkeypatch.delenv("BROWSER_NETWORK_RESTRICTED")
    else:
        response = {"status": "failed"} if failure == "mcp_failed" else {"status": "connected"}
        monkeypatch.setattr(evaluation, "check_connection", AsyncMock(return_value=response))
    with pytest.raises(evaluation.EvaluationSetupError):
        await evaluation.run_evaluation(setup_runner)
    client.aevaluate.assert_not_called()


async def test_runner_grades_real_local_artifacts_and_uploads_only_compact_fields(
    monkeypatch: pytest.MonkeyPatch,
    client: Mock,
    setup_runner: argparse.Namespace,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    model = FakeModel(
        [
            DecisionResponse(
                Decision(action=Action(name="browser_network_requests", arguments={"static": True}))
            ),
            DecisionResponse(Decision(done=True)),
        ]
    )
    result, directory = await run_network_replay(tmp_path, "NET-002", model)
    explorer = SimpleNamespace(explore=AsyncMock(return_value=result))
    monkeypatch.setattr(evaluation, "AutonomousBrowserExplorer", lambda settings: explorer)
    finished = Mock(
        experiment_name="experiment", experiment_id="id", url="https://example.com/experiment"
    )
    finished.wait = AsyncMock()
    finished.__len__ = Mock(return_value=1)

    async def evaluate(target: Any, **kwargs: Any) -> Any:
        assert kwargs["max_concurrency"] == 1 and kwargs["num_repetitions"] == 2
        assert kwargs["data"] == evaluation.DATASET_ID
        assert kwargs["evaluators"] == [evaluation.score_capture]
        assert kwargs["metadata"]["source_revision"] == "test-revision"
        row = client.list_examples.return_value[1]
        output = await target(row.inputs)
        assert output["grade"]["passed"] is True
        assert output["grade"]["capture_recall"] == 1
        assert output["grade"]["failure_summary_recall"] == 1
        assert set(output) == {
            "case_id",
            "run_id",
            "run_status",
            "run_error_codes",
            "grade",
            "execution",
        }
        assert output["execution"]["browser_tool_calls"] == 3
        assert str(directory) not in json.dumps(output)
        assert "evidence" not in output and "trajectory" not in output
        assert scores(output, row.outputs, "NET-002")["capture_pass"] == 1
        return finished

    client.aevaluate.side_effect = evaluate
    await evaluation.run_evaluation(setup_runner)
    finished.wait.assert_awaited_once()
    assert json.loads(capsys.readouterr().out)["result_rows"] == 1
    submitted_task = explorer.explore.call_args.args[0]
    assert submitted_task.url == BASE_URL + evaluation.load_case("NET-002")["path"]
    assert submitted_task.objective == evaluation.load_dataset()["objective"]


@pytest.mark.parametrize(
    "failure", ["case_id", "path", "objective", "limits", "explorer", "cancel"]
)
async def test_target_handles_changed_inputs_and_sanitizes_runtime_failures(
    monkeypatch: pytest.MonkeyPatch,
    client: Mock,
    setup_runner: argparse.Namespace,
    failure: str,
) -> None:
    explore = AsyncMock(
        side_effect=asyncio.CancelledError()
        if failure == "cancel"
        else RuntimeError("private-secret")
    )
    monkeypatch.setattr(
        evaluation, "AutonomousBrowserExplorer", lambda settings: SimpleNamespace(explore=explore)
    )

    async def evaluate(target: Any, **kwargs: Any) -> Any:
        inputs = copy.deepcopy(client.list_examples.return_value[0].inputs)
        if failure not in {"explorer", "cancel"}:
            inputs[failure] = "changed"
        if failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await target(inputs)
        else:
            output = await target(inputs)
            assert output["run_status"] == "runner_error"
            assert output["grade"] is None and output["execution"] is None
            assert "private-secret" not in json.dumps(output)
        if failure not in {"explorer", "cancel"}:
            explore.assert_not_called()
        raise StopAsyncIteration

    client.aevaluate.side_effect = evaluate
    with pytest.raises(StopAsyncIteration):
        await evaluation.run_evaluation(setup_runner)


@pytest.mark.parametrize("repetitions", ["0", "21", "-1"])
def test_cli_rejects_unbounded_repetitions(
    monkeypatch: pytest.MonkeyPatch, repetitions: str
) -> None:
    monkeypatch.setattr("sys.argv", ["eval", "--num-repetitions", repetitions])
    with pytest.raises(SystemExit) as caught:
        evaluation._arguments()
    assert caught.value.code == 2


@pytest.mark.parametrize(
    "check_only,key,error",
    [
        (False, None, "LANGSMITH_API_KEY"),
        (False, "fake", "OPENAI_API_KEY"),
        (True, "fake", "preflight refused"),
    ],
)
def test_cli_configuration_failures_are_actionable(
    monkeypatch: pytest.MonkeyPatch,
    check_only: bool,
    key: str | None,
    error: str,
) -> None:
    monkeypatch.setattr(evaluation, "load_dotenv", lambda **kwargs: False)
    monkeypatch.setattr("sys.argv", ["eval", *(["--check-only"] if check_only else [])])
    if key:
        monkeypatch.setenv("LANGSMITH_API_KEY", key)
    monkeypatch.setattr(
        evaluation,
        "run_evaluation",
        AsyncMock(side_effect=evaluation.EvaluationSetupError("preflight refused")),
    )
    with pytest.raises(SystemExit, match=error):
        evaluation.main()
