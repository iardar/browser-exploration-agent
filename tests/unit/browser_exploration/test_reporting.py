import base64
import json
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from browser_exploration.contracts import (
    BrowserExplorationResult,
    BrowserExplorationTask,
    BrowserObservation,
)
from browser_exploration.reporting import (
    ArtifactStore,
    redact,
    redact_text,
    write_schemas,
)


def observation() -> BrowserObservation:
    return BrowserObservation(
        id="o1",
        url="https://example.com/page?token=secret-value",
        action_name="browser_snapshot",
        action_arguments={"password": "password-value", "ref": "e1"},
        summary="Loaded page",
    )


def test_default_retention_stores_only_redacted_metadata(tmp_path: Path) -> None:
    """Verify default retention stores only redacted metadata.

    Arrange: Use `tmp_path` parameter.
    Act: Evaluate `json.loads` in the assertion.
    Assert: Check `reference.retained is False`; `reference.path is None`; `reference.observation_id
            == observed.id`; `stat.S_IMODE((store.directory / 'trajectory.jsonl').stat().st_mode) ==
            384`.
    """
    store = ArtifactStore(tmp_path, "run1")
    observed = observation()
    reference = store.record(observed, {"private_page": "FULL-PAGE-SECRET"})
    assert reference.retained is False
    assert reference.path is None
    assert reference.observation_id == observed.id
    assert observed.evidence_ref == reference.id
    assert not (store.directory / "evidence").exists()
    trajectory = (store.directory / "trajectory.jsonl").read_text()
    assert "FULL-PAGE-SECRET" not in trajectory
    assert "secret-value" not in trajectory
    assert "password-value" not in trajectory
    assert json.loads(trajectory)["action_arguments"]["ref"] == "e1"
    assert stat.S_IMODE(store.directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((store.directory / "trajectory.jsonl").stat().st_mode) == 0o600


def test_opted_in_evidence_stores_images_outside_json(tmp_path: Path) -> None:
    """Verify opted in evidence stores images outside json.

    Arrange: Use `tmp_path` parameter.
    Act: Evaluate `image.read_bytes` in the assertion.
    Assert: Check `reference.retained is True`; `reference.path is not None`; `encoded not in
            evidence`; `json.loads(evidence)['content'][1]['path'] == 'evidence/o1-1.png'`.
    """
    store = ArtifactStore(tmp_path, "run2", retain_raw_evidence=True)
    encoded = base64.b64encode(b"example-image-bytes").decode()
    reference = store.record(
        observation(),
        {
            "content": [
                {"type": "text", "text": "Observed page"},
                {"type": "image", "data": encoded, "mimeType": "image/png"},
            ],
            "access_token": "sensitive-token",
        },
    )
    assert reference.retained is True
    assert reference.path is not None
    evidence = (store.directory / reference.path).read_text()
    assert encoded not in evidence
    assert "sensitive-token" not in evidence
    image = store.directory / "evidence/o1-1.png"
    assert image.read_bytes() == b"example-image-bytes"
    assert stat.S_IMODE(image.stat().st_mode) == 0o600
    assert json.loads(evidence)["content"][1]["path"] == "evidence/o1-1.png"


def test_normalized_adapter_images_are_extracted(tmp_path: Path) -> None:
    """Verify normalized adapter images are extracted.

    Arrange: Use `tmp_path` parameter.
    Act: Call `ArtifactStore` to obtain `store`.
    Assert: Check `reference.path is not None`; `encoded not in evidence`; `(store.directory /
            'evidence/o1-1.jpg').read_bytes() == b'adapter-jpeg-image'`.
    """
    store = ArtifactStore(tmp_path, "adapter-image", retain_raw_evidence=True)
    encoded = base64.b64encode(b"adapter-jpeg-image").decode()
    reference = store.record(
        observation(),
        [{"type": "image", "base64": encoded, "mime_type": "image/jpeg"}],
    )
    assert reference.path is not None
    evidence = (store.directory / reference.path).read_text()
    assert encoded not in evidence
    assert (store.directory / "evidence/o1-1.jpg").read_bytes() == b"adapter-jpeg-image"


def test_result_and_metrics_are_valid_json_with_actual_failure_status(tmp_path: Path) -> None:
    """Verify result and metrics are valid json with actual failure status.

    Arrange: Use `tmp_path` parameter; prepare `store`, `now`.
    Act: Call `BrowserExplorationResult` to obtain `result`.
    Assert: Check `parsed.status == 'cancelled'`; `json.loads((directory /
            'metrics.json').read_text())['input_tokens'] is None`.
    """
    store = ArtifactStore(tmp_path, "run3")
    now = datetime.now(UTC)
    result = BrowserExplorationResult(
        run_id="run3",
        objective="Inspect",
        target_url="https://example.com",
        status="cancelled",
        started_at=now,
        finished_at=now,
    )
    directory = store.save_result(result)
    parsed = BrowserExplorationResult.model_validate_json((directory / "result.json").read_text())
    assert parsed.status == "cancelled"
    assert json.loads((directory / "metrics.json").read_text())["input_tokens"] is None


def test_redaction_known_patterns() -> None:
    """Verify redaction known patterns.

    Arrange: Start with `redact()`.
    Act: Call `redact` to obtain `value`.
    Assert: Check `value['nested'][0]['input_tokens'] == 10`; `'private' not in
            redact_text('https://u:private@example.com/a?unknown=private#private')`; `secret not in
            encoded`.
    """
    value = redact(
        {
            "Authorization": "Bearer token-value",
            "nested": [{"api_key": "key-value", "input_tokens": 10}],
            "text": "Bearer abc123 password=pw123 sk-examplelongsecret1234",
        }
    )
    encoded = json.dumps(value)
    for secret in ("token-value", "key-value", "abc123", "pw123", "sk-examplelongsecret1234"):
        assert secret not in encoded
    assert value["nested"][0]["input_tokens"] == 10
    assert "private" not in redact_text("https://u:private@example.com/a?unknown=private#private")


def test_relative_snapshot_links_redact_query_values_and_fragments() -> None:
    """Verify relative snapshot links redact query values and fragments.

    Arrange: Start with `redact_text()`.
    Act: Call `redact_text` to obtain `redacted`.
    Assert: Check `'private-' not in redacted`; `'/search?q=' in redacted`; `'[REDACTED]' in
            redacted or '%5BREDACTED%5D' in redacted`.
    """
    redacted = redact_text(
        "  - /url: /search?q=private-search&tracking=private-code#private-fragment"
    )
    assert "private-" not in redacted
    assert "/search?q=" in redacted
    assert "[REDACTED]" in redacted or "%5BREDACTED%5D" in redacted


def test_path_traversal_and_symlink_rejected(tmp_path: Path) -> None:
    """Verify path traversal and symlink rejected.

    Arrange: Use `tmp_path` parameter.
    Act: Call `ArtifactStore` under `pytest.raises(ValueError)`.
    Assert: Check `raises ValueError`.
    """
    with pytest.raises(ValueError):
        ArtifactStore(tmp_path, "../outside")
    (tmp_path / "symlink").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        ArtifactStore(tmp_path, "symlink")
    store = ArtifactStore(tmp_path, "run4")
    observed = observation()
    observed.id = "../outside"
    with pytest.raises(ValueError):
        store.record(observed, {})


def test_schemas_are_exported(tmp_path: Path) -> None:
    """Verify schemas are exported.

    Arrange: Use `tmp_path` parameter.
    Act: Call `json.loads` to obtain `schema`.
    Assert: Check `schema['properties']['schema_version']['const'] == '1.0'`;
            `schema['additionalProperties'] is False`.
    """
    write_schemas(tmp_path)
    schema = json.loads((tmp_path / "BrowserExplorationResult.schema.json").read_text())
    assert schema["properties"]["schema_version"]["const"] == "1.0"
    assert schema["additionalProperties"] is False


def test_comparison_artifacts_preserve_model_and_redact_decision_secrets(tmp_path: Path) -> None:
    """Verify comparison artifacts preserve model and redact decision secrets.

    Arrange: Use `tmp_path` parameter; prepare `store`.
    Act: Call `BrowserExplorationTask` to obtain `task`.
    Assert: Check `config['configured_model'] == 'model-for-this-run'`; `'requirement_text' not in
            config['task']`; `len(decisions.splitlines()) == 2`; `stat.S_IMODE((store.directory /
            'decisions.jsonl').stat().st_mode) == 384`.
    """
    store = ArtifactStore(tmp_path, "model-comparison")
    task = BrowserExplorationTask(
        url="https://example.com", objective="Search", requirement_text="omitted-requirement"
    )
    store.save_run_config(task, "model-for-this-run", "OpenAIDecisionModel")
    for _ in range(2):
        store.record_decision_event(
            {"event": "action_rejected", "action": {"text": "password=secret-value"}}
        )
    config = json.loads((store.directory / "run_config.json").read_text())
    assert config["configured_model"] == "model-for-this-run"
    assert "requirement_text" not in config["task"]
    decisions = (store.directory / "decisions.jsonl").read_text()
    assert len(decisions.splitlines()) == 2
    assert "secret-value" not in decisions
    assert stat.S_IMODE((store.directory / "run_config.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((store.directory / "decisions.jsonl").stat().st_mode) == 0o600
