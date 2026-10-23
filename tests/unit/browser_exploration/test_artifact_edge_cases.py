"""Offline retention, redaction, filesystem, and usage-accounting failure cases."""

import base64
import errno
import json
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pytest
from pydantic import BaseModel

from browser_exploration.contracts import BrowserExplorationResult, BrowserObservation
from browser_exploration.reporting import ArtifactStore, redact, redact_text
from browser_exploration.telemetry import TokenAccumulator


def observed(identifier: str = "observation-1") -> BrowserObservation:
    return BrowserObservation(
        id=identifier,
        url="https://example.com/article",
        action_name="browser_snapshot",
        summary="Read-only article observed",
    )


def result(run_id: str) -> BrowserExplorationResult:
    now = datetime.now(UTC)
    return BrowserExplorationResult(
        run_id=run_id,
        objective="Inspect the article",
        target_url="https://example.com/article",
        status="failed",
        started_at=now,
        finished_at=now,
    )


def stored_payload(store: ArtifactStore, observation: BrowserObservation, raw: Any) -> Any:
    reference = store.record(observation, raw)
    assert reference.path is not None
    return json.loads((store.directory / reference.path).read_text())


def test_disabled_raw_retention_never_serializes_an_unserializable_payload(tmp_path: Path) -> None:
    class UnserializablePayload:
        def __str__(self) -> str:
            raise AssertionError("raw payload must not be inspected when retention is disabled")

    store = ArtifactStore(tmp_path, "no-retention")
    reference = store.record(observed(), UnserializablePayload())
    trajectory = json.loads((store.directory / "trajectory.jsonl").read_text())
    assert reference.retained is False
    assert reference.path is None
    assert trajectory["evidence_ref"] == reference.id
    assert list(store.directory.iterdir()) == [store.directory / "trajectory.jsonl"]


@pytest.mark.parametrize("wrapped", [False, True], ids=["string-url", "url-object"])
def test_legacy_image_data_urls_become_separate_webp_files(tmp_path: Path, wrapped: bool) -> None:
    image_bytes = b"synthetic screenshot bytes"
    encoded = base64.b64encode(image_bytes).decode()
    image_url = "data:image/webp;base64," + encoded
    raw = {"type": "image_url", "image_url": {"url": image_url} if wrapped else image_url}
    store = ArtifactStore(tmp_path, "legacy-image", retain_raw_evidence=True)
    payload = stored_payload(store, observed(), raw)

    assert payload == {
        "type": "image_reference",
        "path": "evidence/observation-1-1.webp",
        "mime_type": "image/webp",
    }
    assert (store.directory / payload["path"]).read_bytes() == image_bytes
    assert encoded not in json.dumps(payload)


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (
            {"type": "image", "data": "eA==", "mimeType": "image/svg+xml"},
            "unsupported image MIME type",
        ),
        (
            {"type": "image", "base64": "%%%not-base64%%%", "mime_type": "image/png"},
            "invalid base64 image",
        ),
        (
            {"type": "image_url", "image_url": "data:image/png;base64,not-padded"},
            "invalid base64 image",
        ),
    ],
    ids=["unsupported-mime", "malformed-adapter-base64", "malformed-legacy-base64"],
)
def test_invalid_screenshots_are_recorded_as_omitted_without_binary_files(
    tmp_path: Path, raw: dict[str, Any], reason: str
) -> None:
    store = ArtifactStore(tmp_path, "invalid-image", retain_raw_evidence=True)
    payload = stored_payload(store, observed(), raw)
    assert payload == {"type": "image_omitted", "reason": reason}
    assert [path.suffix for path in (store.directory / "evidence").iterdir()] == [".json"]
    assert len((store.directory / "trajectory.jsonl").read_text().splitlines()) == 1


def test_oversized_image_is_rejected_before_binary_decoding(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, "large-image", retain_raw_evidence=True)
    payload = stored_payload(
        store,
        observed(),
        {"type": "image", "data": "A" * 44_739_245, "mimeType": "image/png"},
    )
    assert payload == {"type": "image_omitted", "reason": "image exceeds 32 MiB limit"}
    evidence_files = list((store.directory / "evidence").iterdir())
    assert len(evidence_files) == 1
    assert evidence_files[0].stat().st_size < 200


@pytest.mark.parametrize(
    "raw",
    [
        {"type": "image", "data": None, "mimeType": "image/png"},
        {"type": "image_url", "image_url": None},
        {"type": "image_url", "image_url": {"url": "https://images.example.com/screenshot.png"}},
        {"type": "image_url", "image_url": "data:image/png,unencoded-image"},
    ],
    ids=["null-data", "null-url", "remote-url", "non-base64-url"],
)
def test_nonembedded_image_metadata_is_not_mistaken_for_a_binary_payload(
    tmp_path: Path, raw: dict[str, Any]
) -> None:
    store = ArtifactStore(tmp_path, "metadata-image", retain_raw_evidence=True)
    payload = stored_payload(store, observed(), raw)
    assert payload == raw
    assert list((store.directory / "evidence").glob("*.png")) == []


def test_nested_pydantic_evidence_extracts_each_image_with_unique_reference(tmp_path: Path) -> None:
    class MCPResponse(BaseModel):
        content: list[dict[str, Any]]

    first = base64.b64encode(b"first image").decode()
    second = base64.b64encode(b"second image").decode()
    raw = MCPResponse(
        content=[
            {"type": "text", "text": "Visible article"},
            {"type": "image", "data": first, "mimeType": "image/png"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + second}},
        ]
    )
    store = ArtifactStore(tmp_path, "nested-images", retain_raw_evidence=True)
    payload = stored_payload(store, observed(), raw)
    assert payload["content"][0]["text"] == "Visible article"
    images = payload["content"][1:]
    assert images[0]["path"] != images[1]["path"]
    assert [(store.directory / image["path"]).read_bytes() for image in images] == [
        b"first image",
        b"second image",
    ]
    assert all(
        stat.S_IMODE((store.directory / image["path"]).stat().st_mode) == 0o600 for image in images
    )


def test_evidence_directory_symlink_cannot_redirect_retained_data(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    store = ArtifactStore(tmp_path, "symlink-evidence", retain_raw_evidence=True)
    (store.directory / "evidence").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="evidence directory"):
        store.record(observed(), {"text": "private page data"})
    assert list(outside.iterdir()) == []
    assert (store.directory / "trajectory.jsonl").read_text() == ""


@pytest.mark.parametrize("filename", ["result.json", "trajectory.jsonl"])
def test_existing_artifact_symlink_does_not_overwrite_an_external_file(
    tmp_path: Path, filename: str
) -> None:
    store = ArtifactStore(tmp_path, "file-symlink")
    outside = tmp_path / "existing-document.txt"
    outside.write_text("keep this document")
    destination = store.directory / filename
    destination.unlink(missing_ok=True)
    destination.symlink_to(outside)
    with pytest.raises(OSError) as failure:
        if filename == "result.json":
            store.save_result(result("file-symlink"))
        else:
            store.record(observed(), None)
    assert failure.value.errno == errno.ELOOP
    assert outside.read_text() == "keep this document"


def test_result_for_another_run_is_rejected_without_creating_reports(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, "this-run")
    with pytest.raises(ValueError, match="does not match"):
        store.save_result(result("other-run"))
    assert not (store.directory / "result.json").exists()
    assert not (store.directory / "metrics.json").exists()


@pytest.mark.parametrize(
    "identifier", ["", ".hidden", "run/name", "run\\name", "a" * 129, "run\x00"]
)
def test_unsafe_or_oversized_run_ids_do_not_create_directories(
    tmp_path: Path, identifier: str
) -> None:
    with pytest.raises(ValueError, match="safe filename"):
        ArtifactStore(tmp_path, identifier)
    assert list(tmp_path.iterdir()) == []


def test_reopening_a_run_preserves_its_existing_trajectory(tmp_path: Path) -> None:
    first_store = ArtifactStore(tmp_path, "reopened")
    first_store.record(observed("first"), None)
    second_store = ArtifactStore(tmp_path, "reopened")
    second_store.record(observed("second"), None)
    entries = [
        json.loads(line)
        for line in (second_store.directory / "trajectory.jsonl").read_text().splitlines()
    ]
    assert [entry["id"] for entry in entries] == ["first", "second"]
    assert [entry["evidence_ref"] for entry in entries] == ["evidence-first", "evidence-second"]


def test_ipv6_url_redaction_preserves_destination_and_removes_credentials(tmp_path: Path) -> None:
    original = "https://user:private@[2001:db8::1]:8443/article?search=private&search=other#private"
    redacted = redact_text(original)
    parsed = urlsplit(redacted)
    assert parsed.hostname == "2001:db8::1"
    assert parsed.port == 8443
    assert parsed.path == "/article"
    assert parsed.username is None and parsed.password is None
    assert parse_qsl(parsed.query) == [("search", "[REDACTED]"), ("search", "[REDACTED]")]
    assert parsed.fragment == "[REDACTED]"
    assert "private" not in redacted


@pytest.mark.parametrize("url", ["https://example.com:invalid/path", "http://[broken/path"])
def test_malformed_urls_are_replaced_without_exposing_their_payload(url: str) -> None:
    assert redact_text("Observed " + url) == "Observed [REDACTED_URL]"


def test_nested_model_tuple_and_custom_object_redaction_returns_safe_json_values() -> None:
    class Metadata(BaseModel):
        session_id: str
        valid: bool
        count: int

    class Diagnostic:
        def __str__(self) -> str:
            return "Authorization=Bearer sensitive_value"

    output = redact(
        {
            "metadata": Metadata(session_id="private-session", valid=True, count=0),
            "items": (None, 0, False, 1.5, {"refresh_token": "private-refresh"}),
            "diagnostic": Diagnostic(),
        }
    )
    serialized = json.dumps(output, allow_nan=False)
    assert "private-session" not in serialized
    assert "private-refresh" not in serialized
    assert "sensitive_value" not in serialized
    assert output["metadata"] == {"session_id": "[REDACTED]", "valid": True, "count": 0}
    assert output["items"] == [None, 0, False, 1.5, {"refresh_token": "[REDACTED]"}]
    assert isinstance(output["diagnostic"], str)


@pytest.mark.parametrize("invalid", [True, False, -1, 1.5, "20", {}, []])
def test_invalid_provider_usage_poison_only_the_affected_total(invalid: Any) -> None:
    usage = TokenAccumulator()
    usage.add({"input_tokens": invalid, "output_tokens": 0, "total_tokens": 99})
    usage.add({"input_tokens": 3, "output_tokens": 2, "total_tokens": 5})
    assert usage.calls == 2
    assert usage.counts() == {"input_tokens": None, "output_tokens": 2, "total_tokens": 104}


def test_provider_totals_are_not_reconstructed_from_component_values() -> None:
    usage = TokenAccumulator()
    usage.add({"input_tokens": 0, "output_tokens": 0, "total_tokens": 7})
    assert usage.counts() == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 7}


def test_token_results_and_separate_runs_do_not_share_mutable_accounting() -> None:
    first = TokenAccumulator()
    second = TokenAccumulator()
    first.add({"input_tokens": 1, "output_tokens": 2, "total_tokens": 3})
    returned = first.counts()
    returned["total_tokens"] = 500
    assert first.counts()["total_tokens"] == 3
    assert second.calls == 0
    assert second.counts() == {"input_tokens": None, "output_tokens": None, "total_tokens": None}
