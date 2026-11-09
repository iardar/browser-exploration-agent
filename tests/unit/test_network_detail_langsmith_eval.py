"""Check detail dataset publication, validation and feedback with fake LangSmith clients."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from evals.network_request_details import langsmith_eval as evaluation
from evals.network_request_details.dataset import expected_examples, source_hash

from browser_exploration.config import Settings


def _client() -> Mock:
    """Build a complete in-memory remote dataset from the reviewed local examples."""
    client = Mock()
    client.has_dataset.return_value = True
    client.read_dataset.return_value = SimpleNamespace(
        id="detail-dataset-id",
        name=evaluation.DATASET_NAME,
        metadata={"source_content_sha256": source_hash()},
    )
    client.list_examples.return_value = [
        SimpleNamespace(inputs=row["inputs"], outputs=row["outputs"])
        for row in reversed(list(expected_examples().values()))
    ]
    return client


def test_existing_dataset_is_validated_without_mutation() -> None:
    """Confirm reusing the named dataset never edits existing examples.

    Arrange: Fake a remote dataset whose 22 examples are returned in reverse order.
    Act: Publish against a name that the fake client reports as already present.
    Assert: Readback passes and no create or update method is invoked.
    """
    client = _client()
    dataset = evaluation.publish_dataset(client)
    assert dataset.id == "detail-dataset-id"
    client.create_dataset.assert_not_called()
    client.create_examples.assert_not_called()
    client.list_examples.assert_called_once_with(dataset_id="detail-dataset-id", limit=None)


def test_new_dataset_is_created_with_exact_reviewed_rows() -> None:
    """Keep remote reference outputs compact and tied to the local source hash.

    Arrange: Make the fake client report that the named dataset is absent.
    Act: Publish the corpus and validate its readback.
    Assert: One dataset and exactly 22 compact examples are created.
    """
    client = _client()
    client.has_dataset.return_value = False
    evaluation.publish_dataset(client)
    client.create_dataset.assert_called_once()
    arguments = client.create_dataset.call_args.kwargs
    assert arguments["dataset_name"] == evaluation.DATASET_NAME
    assert arguments["metadata"]["source_content_sha256"] == source_hash()
    client.create_examples.assert_called_once()
    examples = client.create_examples.call_args.kwargs["examples"]
    assert len(examples) == 22
    assert examples == list(expected_examples().values())
    assert "secret-canary" not in str(examples)


def test_publication_connection_failure_has_a_safe_message() -> None:
    """Avoid exposing an SDK traceback when remote dataset lookup cannot connect.

    Arrange: Make the fake client's dataset lookup fail with a transport exception.
    Act: Attempt the opt-in dataset publication.
    Assert: The raised setup error identifies connectivity without remote response content.
    """
    client = _client()
    client.has_dataset.side_effect = ConnectionError("private transport detail")
    with pytest.raises(evaluation.DetailEvaluationError) as error:
        evaluation.publish_dataset(client)
    assert "connectivity" in str(error.value)
    assert "private transport detail" not in str(error.value)


@pytest.mark.parametrize("fault", ["hash", "missing", "duplicate", "changed", "name"])
def test_remote_dataset_drift_fails_closed(fault: str) -> None:
    """Reject stale, missing, duplicate or altered remote examples before a trial.

    Arrange: Corrupt one remote metadata or example field in an otherwise valid fake dataset.
    Act: Validate the remote dataset.
    Assert: Setup fails rather than running against changed evaluation definitions.
    """
    client = _client()
    dataset = client.read_dataset.return_value
    examples = client.list_examples.return_value
    if fault == "hash":
        dataset.metadata["source_content_sha256"] = "stale"
    elif fault == "missing":
        examples.pop()
    elif fault == "duplicate":
        examples[-1] = examples[0]
    elif fault == "changed":
        examples[0].outputs = {"marker": "invented"}
    else:
        dataset.name = "another-dataset"
    with pytest.raises(evaluation.DetailEvaluationError):
        evaluation._validate_dataset(client)


def test_feedback_is_null_on_oracle_mismatch_and_missing_grade() -> None:
    """Avoid publishing a false quality score when the reference or run grade is absent.

    Arrange: Choose one real case's input and compact reference output.
    Act: Score a passing run, a missing grade and a modified remote reference.
    Assert: The valid pass is numeric; unmeasurable scores are null.
    """
    row = expected_examples()["DET-001"]
    inputs, reference = row["inputs"], row["outputs"]
    outputs = {"grade": {"passed": True, "checks": {"verified_overview": True}}}
    valid = evaluation.score_detail(inputs, outputs, reference)["results"]
    assert {item["key"]: item["score"] for item in valid}["detail_pass"] == 1.0
    absent = evaluation.score_detail(inputs, {}, reference)["results"]
    assert {item["key"]: item["score"] for item in absent}["detail_pass"] is None
    changed = evaluation.score_detail(inputs, outputs, {"marker": "wrong"})["results"]
    assert {item["key"]: item["score"] for item in changed}["detail_pass"] is None
    assert {item["key"]: item["score"] for item in changed}["oracle_consistent"] == 0.0


def test_fixture_url_requires_controlled_origin_and_private_opt_in() -> None:
    """Do not accidentally point a detail trial at an unreviewed or private endpoint.

    Arrange: Build settings with and without the explicit private-network opt-in.
    Act: Join a case path to good, credentialed and non-origin fixture URLs.
    Assert: Only the reviewed route on an allowed origin is accepted.
    """
    path = expected_examples()["DET-001"]["inputs"]["path"]
    restricted = Settings(allow_private_network=False)
    allowed = Settings(allow_private_network=True)
    with pytest.raises(evaluation.DetailEvaluationError):
        evaluation._fixture_url("http://127.0.0.1:8766", path, restricted)
    assert evaluation._fixture_url("http://127.0.0.1:8766", path, allowed).endswith(path)
    for invalid in ("http://user:pass@127.0.0.1:8766", "http://127.0.0.1:8766/path"):
        with pytest.raises(evaluation.DetailEvaluationError):
            evaluation._fixture_url(invalid, path, allowed)
