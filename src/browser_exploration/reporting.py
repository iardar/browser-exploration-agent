"""Private run artifacts with opt-in raw retention and best-effort secret redaction.

Redaction handles known credential patterns. It cannot classify arbitrary sensitive
page content; raw evidence is therefore disabled unless explicitly requested.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel

from .contracts import (
    BrowserExplorationResult,
    BrowserExplorationTask,
    BrowserFinding,
    BrowserObservation,
    EvidenceReference,
    ExecutionMetrics,
)

_REDACTED = "[REDACTED]"
_SAFE_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z")
_URL = re.compile(r"https?://[^\s<>\"']+")
_RELATIVE_SNAPSHOT_URL = re.compile(r"^(\s*-\s*/url:\s*)(/[^\r\n]*)$", re.MULTILINE)
_SECRET_KEY = re.compile(
    r"(?:password|passwd|pwd|secret|token|authorization|cookie|api[_-]?key|"
    r"credentials?|session[_-]?id)$",
    re.IGNORECASE,
)
_ASSIGNMENT = re.compile(
    r"((?:password|passwd|secret|(?:access[_-]?|refresh[_-]?)?token|"
    r"api[_-]?key|authorization|cookie|session[_-]?id)\s*[=:]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"\bBearer\s+[^\s,;\"']+", re.IGNORECASE)
_OPENAI_KEY = re.compile(r"\bsk-[a-zA-Z0-9_-]{12,}\b")
_JWT = re.compile(r"\beyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b")


def _redact_url(match: re.Match[str]) -> str:
    value = match.group()
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        port = parsed.port
        authority = f"{host}:{port}" if port is not None else host
        query = urlencode([(key, _REDACTED) for key, _ in parse_qsl(parsed.query)])
        return urlunsplit(
            (parsed.scheme, authority, parsed.path, query, _REDACTED if parsed.fragment else "")
        )
    except ValueError:
        return "[REDACTED_URL]"


def redact_text(value: str) -> str:
    """Redact familiar secrets and all URL query values, without claiming full DLP."""
    value = _BEARER.sub("Bearer " + _REDACTED, value)
    value = _ASSIGNMENT.sub(lambda match: match.group(1) + _REDACTED, value)
    value = _OPENAI_KEY.sub(_REDACTED, value)
    value = _JWT.sub(_REDACTED, value)
    value = _URL.sub(_redact_url, value)

    def relative_url(match: re.Match[str]) -> str:
        try:
            parsed = urlsplit(match.group(2))
            # Query values on relative snapshot links need the same handling as absolute URLs.
            query = urlencode([(key, _REDACTED) for key, _ in parse_qsl(parsed.query)])
            host = parsed.hostname or ""
            if ":" in host:
                host = f"[{host}]"
            authority = f"{host}:{parsed.port}" if parsed.port is not None else host
            target = urlunsplit(
                ("", authority, parsed.path, query, _REDACTED if parsed.fragment else "")
            )
            return match.group(1) + target
        except ValueError:
            return match.group(1) + "[REDACTED_URL]"

    return _RELATIVE_SNAPSHOT_URL.sub(relative_url, value)


def redact(value: Any) -> Any:
    """Return JSON-compatible data with recognized credentials removed recursively."""
    if isinstance(value, BaseModel):
        return redact(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {
            str(key): _REDACTED if _SECRET_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value))


def _write_private(path: Path, contents: bytes, *, append: bool = False) -> None:
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "ab" if append else "wb") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(contents)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


class ArtifactStore:
    def __init__(self, output_dir: Path, run_id: str, retain_raw_evidence: bool = False) -> None:
        if not _SAFE_ID.fullmatch(run_id):
            raise ValueError("run_id must be a safe filename component")
        self.directory = Path(output_dir) / run_id
        self.retain_raw_evidence = retain_raw_evidence
        if self.directory.is_symlink():
            raise ValueError("run directory must not be a symbolic link")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        _write_private(self.directory / "trajectory.jsonl", b"", append=True)

    def save_run_config(
        self,
        task: BrowserExplorationTask,
        configured_model: str,
        model_adapter: str,
        *,
        reasoning_effort: str | None = None,
        model_api: str = "chat_completions",
    ) -> None:
        """Persist only explicit run settings, never environment variables or API credentials."""
        fields = {
            "url",
            "objective",
            "allowed_domains",
            "allow_mutations",
            "allow_form_input",
            "allow_form_submission",
            "max_steps",
            "max_model_iterations",
            "max_duration_seconds",
            "max_recent_observations",
            "max_observation_chars",
            "max_context_chars",
        }
        config = {
            "configured_model": configured_model,
            "reasoning_effort": reasoning_effort,
            "model_api": model_api,
            "model_adapter": model_adapter,
            "retain_raw_evidence": self.retain_raw_evidence,
            "task": task.model_dump(mode="json", include=fields),
        }
        _write_private(self.directory / "run_config.json", _json_bytes(redact(config)))

    def record_decision_event(self, event: dict[str, Any]) -> None:
        """Keep every proposal and rejection, including retries absent from the trajectory."""
        payload = json.dumps(redact(event), ensure_ascii=False, allow_nan=False) + "\n"
        _write_private(self.directory / "decisions.jsonl", payload.encode(), append=True)

    def record(self, observation: BrowserObservation, raw: Any) -> EvidenceReference:
        """Persist bounded observation metadata and, only when enabled, raw evidence."""
        if not _SAFE_ID.fullmatch(observation.id):
            raise ValueError("observation id must be a safe filename component")
        evidence_id = f"evidence-{observation.id}"
        reference = EvidenceReference(
            id=evidence_id,
            observation_id=observation.id,
            kind="browser_observation",
            retained=self.retain_raw_evidence,
        )
        if self.retain_raw_evidence:
            evidence_dir = self.directory / "evidence"
            if evidence_dir.is_symlink():
                raise ValueError("evidence directory must not be a symbolic link")
            evidence_dir.mkdir(mode=0o700, exist_ok=True)
            reference.path = f"evidence/{observation.id}.json"
            raw_data = raw.model_dump(mode="json") if isinstance(raw, BaseModel) else raw
            extracted = self._extract_images(raw_data, observation.id, [0])
            _write_private(self.directory / reference.path, _json_bytes(redact(extracted)))
        observation.evidence_ref = evidence_id
        payload = json.dumps(redact(observation), ensure_ascii=False, allow_nan=False) + "\n"
        _write_private(self.directory / "trajectory.jsonl", payload.encode(), append=True)
        return reference

    def _extract_images(self, value: Any, observation_id: str, counter: list[int]) -> Any:
        if isinstance(value, dict):
            encoded: str | None = None
            mime_type = str(value.get("mime_type", value.get("mimeType", "image/png")))
            if value.get("type") == "image":
                # langchain-mcp-adapters normalizes MCP data/mimeType to the
                # standard LangChain base64/mime_type content-block fields.
                image_data = value.get("base64", value.get("data"))
                if isinstance(image_data, str):
                    encoded = image_data
            if value.get("type") == "image_url":
                image_url = value.get("image_url")
                url = image_url.get("url") if isinstance(image_url, dict) else image_url
                if isinstance(url, str) and url.startswith("data:image/") and ";base64," in url:
                    header, encoded = url.split(";base64,", 1)
                    mime_type = header.removeprefix("data:")
            if encoded is not None:
                counter[0] += 1
                extensions = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
                if mime_type not in extensions:
                    return {"type": "image_omitted", "reason": "unsupported image MIME type"}
                if len(encoded) > 44_739_244:
                    return {"type": "image_omitted", "reason": "image exceeds 32 MiB limit"}
                try:
                    decoded = base64.b64decode(encoded, validate=True)
                except (ValueError, binascii.Error):
                    return {"type": "image_omitted", "reason": "invalid base64 image"}
                path = f"evidence/{observation_id}-{counter[0]}.{extensions[mime_type]}"
                _write_private(self.directory / path, decoded)
                return {"type": "image_reference", "path": path, "mime_type": mime_type}
            return {
                str(key): self._extract_images(item, observation_id, counter)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self._extract_images(item, observation_id, counter) for item in value]
        return value

    def save_result(self, result: BrowserExplorationResult) -> Path:
        if result.run_id != self.directory.name:
            raise ValueError("result run_id does not match artifact directory")
        _write_private(self.directory / "result.json", _json_bytes(redact(result)))
        _write_private(self.directory / "metrics.json", _json_bytes(redact(result.metrics)))
        return self.directory


def write_schemas(directory: Path) -> None:
    """Export Pydantic JSON Schema documents for the version 1.0 artifact contracts."""
    directory.mkdir(parents=True, exist_ok=True)
    for model in (
        BrowserExplorationTask,
        BrowserObservation,
        BrowserFinding,
        BrowserExplorationResult,
        ExecutionMetrics,
    ):
        path = directory / f"{model.__name__}.schema.json"
        _write_private(path, _json_bytes(model.model_json_schema()))
