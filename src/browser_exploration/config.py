"""Environment-based configuration, independent of the existing QA workflow."""

import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ReasoningEffort = Literal["none", "low", "medium", "high", "xhigh", "max"]
ModelAPI = Literal["chat_completions", "responses"]
_GPT56_MODELS = re.compile(r"gpt-5\.6(?:-(?:sol|luna))?(?:-\d{4}-\d{2}-\d{2})?")
_GPT6_MODELS = re.compile(r"gpt-(?:6-(?:astra|sol|luna)|6\.1-sol)(?:-\d{4}-\d{2}-\d{2})?")


# Deployment configuration loaded from environment variables or explicit constructor values.
# Task permissions and actual run budgets live in BrowserExplorationTask; the CLI copies
# the default budgets below into a task. Direct Python callers make that mapping themselves.
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BROWSER_", extra="ignore", populate_by_name=True)

    model: str = Field(
        default="gpt-4.1-mini", validation_alias=AliasChoices("OPENAI_MODEL", "BROWSER_MODEL")
    )
    reasoning_effort: ReasoningEffort | None = None
    model_api: Literal["auto", "chat_completions", "responses"] = "auto"
    mcp_url: str = Field(
        default="http://127.0.0.1:8931/mcp",
        validation_alias=AliasChoices("PLAYWRIGHT_MCP_URL", "BROWSER_MCP_URL"),
    )
    output_dir: Path = Path("runs")
    retain_raw_evidence: bool = False
    network_restricted: bool = False  # Operator attestation; this does not configure a firewall.
    allow_private_network: bool = False
    tool_timeout_seconds: float = Field(default=20, gt=0, le=3_600)
    max_steps: int = Field(default=30, ge=1, le=1_000)
    max_model_iterations: int = Field(default=20, ge=1, le=1_000)
    max_duration_seconds: int = Field(default=120, ge=1, le=86_400)
    max_recent_observations: int = Field(default=5, ge=1, le=100)
    max_observation_chars: int = Field(default=6_000, ge=1, le=1_000_000)
    max_context_chars: int = Field(default=18_000, ge=4_096, le=2_000_000)
    langsmith_enabled: bool = False

    @property
    def effective_reasoning_effort(self) -> ReasoningEffort | None:
        """Use an explicit effort first, then a supported model-specific default if defined."""
        if self.reasoning_effort is not None:
            return self.reasoning_effort
        # Pin the documented default for these models instead of relying on an
        # endpoint's implicit effort. Leave unrelated models' requests unchanged.
        if _GPT56_MODELS.fullmatch(self.model):
            return "medium"
        return None

    @property
    def effective_model_api(self) -> ModelAPI:
        """Use an explicit API mode, or choose one from the configured model name."""
        if self.model_api != "auto":
            return self.model_api
        # Route the supported GPT-5.6 and GPT-6 reasoning models to Responses.
        # GPT-6 Astra and GPT-6.1 Sol require it for function calling.
        if _GPT56_MODELS.fullmatch(self.model) or _GPT6_MODELS.fullmatch(self.model):
            return "responses"
        return "chat_completions"

    @field_validator("model")
    @classmethod
    def model_not_empty(cls, value: str) -> str:
        """Reject a blank model name and remove surrounding whitespace."""
        if not value.strip():
            raise ValueError("OPENAI_MODEL must not be empty")
        return value.strip()

    @field_validator("mcp_url")
    @classmethod
    def valid_mcp_endpoint(cls, value: str) -> str:
        """Require a plain MCP endpoint URL and HTTPS unless it points to loopback."""
        if any(ord(character) < 32 for character in value) or "\\" in value:
            raise ValueError("MCP endpoint must not contain control characters or backslashes")
        try:
            parsed = urlsplit(value)
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("invalid MCP endpoint") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("MCP endpoint must be an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None or parsed.query:
            raise ValueError("MCP endpoint must not embed credentials or query parameters")
        if parsed.fragment:
            raise ValueError("MCP endpoint must not contain a fragment")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("non-loopback MCP endpoints require HTTPS")
        return value
