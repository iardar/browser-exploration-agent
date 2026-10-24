"""Tool-selection contract and the configurable ChatOpenAI decision adapter."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .contracts import BrowserFinding, VerifiedBehavior


# A model's proposed tool name and arguments. This record does not execute the tool.
class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict, max_length=32)

    @model_validator(mode="after")
    def bounded_arguments(self) -> Action:
        """Require JSON-compatible arguments small enough to validate and record."""
        try:
            arguments = json.dumps(self.arguments, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("Action arguments must contain JSON-compatible values") from exc
        if len(arguments) > 8192:
            raise ValueError("Action arguments exceed the 8192 character limit")
        return self


# One model reply: choose an action or finish, optionally adding claims and questions.
# Use comments for model descriptions to avoid changing the provider-facing JSON schema.
class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Action | None = None
    done: bool = False
    verified_behaviors: list[VerifiedBehavior] = Field(default_factory=list, max_length=20)
    findings: list[BrowserFinding] = Field(default_factory=list, max_length=20)
    unresolved_questions: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def exactly_one_next_step(self) -> Decision:
        """Reject replies that choose both an action and completion, or neither."""
        if self.done == (self.action is not None):
            raise ValueError("Choose one action or set done=true, exclusively")
        if any(len(question) > 2000 for question in self.unresolved_questions):
            raise ValueError("Unresolved questions must be at most 2000 characters")
        return self


@dataclass(frozen=True)
class DecisionResponse:
    """Bundle a parsed decision with any token counts and model name the provider supplied."""

    decision: Decision
    usage: dict[str, int] | None = None
    provider_model: str | None = None


class ModelError(Exception):
    """Sanitized model failure with provider-reported usage when available."""

    def __init__(
        self,
        code: str,
        message: str,
        usage: dict[str, int] | None = None,
        *,
        diagnostics: dict[str, Any] | None = None,
        provider_model: str | None = None,
    ) -> None:
        """Keep safe diagnostics and available usage without storing raw provider output."""
        super().__init__(message)
        self.code = code
        self.usage = usage
        self.diagnostics = diagnostics or {}
        self.provider_model = provider_model


def validation_diagnostics(error: Any) -> dict[str, Any]:
    """Keep invalid field locations and error types without copying rejected values."""
    allowed_fields = set(Decision.model_fields) | set(Action.model_fields)
    allowed_fields |= set(BrowserFinding.model_fields) | set(VerifiedBehavior.model_fields)
    for _ in range(4):
        if isinstance(error, ValidationError):
            issues = []
            for item in error.errors(include_input=False, include_context=False, include_url=False)[
                :8
            ]:
                location = [
                    part if isinstance(part, int) or part in allowed_fields else "<field>"
                    for part in item["loc"]
                ]
                issues.append({"location": location, "type": item["type"]})
            return {"kind": "schema_validation", "issues": issues}
        if not isinstance(error, BaseException):
            break
        error = error.__cause__ or error.__context__
    return {"kind": "unparseable_decision"}


def _provider_model(raw: Any) -> str | None:
    """Read a bounded model name from provider response metadata when available."""
    if not isinstance(raw, AIMessage):
        return None
    name = raw.response_metadata.get("model_name", raw.response_metadata.get("model"))
    return name[:128] if isinstance(name, str) else None


def provider_error(error: Exception) -> ModelError:
    """Convert a provider exception into a safe message and selected diagnostic fields."""
    current = error
    for _ in range(4):
        if (
            isinstance(getattr(current, "status_code", None), int)
            or isinstance(current, (TimeoutError, ConnectionError))
            or type(current).__name__ in {"APIConnectionError", "APITimeoutError"}
        ):
            error = current
            break
        cause = current.__cause__ or current.__context__
        if not isinstance(cause, Exception):
            break
        current = cause
    details: dict[str, Any] = {"kind": "provider_request_failed"}
    status = getattr(error, "status_code", None)
    if isinstance(status, int) and 100 <= status <= 599:
        details["http_status"] = status
    # Copy only known diagnostic names; arbitrary exception fields may contain secrets.
    allowed_codes = {
        "model_not_found",
        "invalid_api_key",
        "insufficient_quota",
        "rate_limit_exceeded",
        "unsupported_parameter",
        "unsupported_value",
        "invalid_request_error",
        "missing_required_parameter",
        "context_length_exceeded",
        "invalid_json_schema",
        "invalid_function_parameters",
    }
    raw_code = getattr(error, "code", None)
    code = raw_code if isinstance(raw_code, str) and raw_code in allowed_codes else None
    if code is not None:
        details["provider_code"] = code
    allowed_parameters = {
        "model",
        "messages",
        "input",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "response_format",
        "temperature",
        "top_p",
        "max_tokens",
        "max_completion_tokens",
        "max_output_tokens",
        "reasoning_effort",
        "reasoning",
        "stream",
        "stop",
        "text",
        "presence_penalty",
        "frequency_penalty",
        "seed",
        "logprobs",
        "store",
        "service_tier",
    }
    parameter = getattr(error, "param", None)
    root = re.match(r"[a-z_]+", parameter) if isinstance(parameter, str) else None
    if root and root.group() in allowed_parameters:
        details["parameter"] = root.group()
    message = "LLM request failed; check provider configuration"
    if status == 404 or code == "model_not_found":
        message = (
            "Requested model or endpoint was not found, or this API project lacks model access"
        )
    elif status == 401 or code == "invalid_api_key":
        message = "Provider authentication failed; check OPENAI_API_KEY and the configured endpoint"
    elif status == 403:
        message = "Provider denied access; check project and model permissions"
    elif status == 429:
        message = "Provider quota or rate limit reached"
    elif code in {"unsupported_parameter", "unsupported_value"}:
        message = "Provider rejected a request parameter or value"
    elif isinstance(error, TimeoutError) or type(error).__name__ == "APITimeoutError":
        message = "LLM request timed out"
        details["kind"] = "provider_timeout"
    elif isinstance(error, ConnectionError) or type(error).__name__ == "APIConnectionError":
        message = "Cannot reach the LLM provider; check endpoint and network connectivity"
        details["kind"] = "provider_connection_error"
    if status == 400 and details.get("parameter") in {"reasoning_effort", "reasoning"}:
        message = (
            "Provider rejected reasoning configuration; check API compatibility and the "
            "selected model's supported reasoning efforts"
        )
    if "http_status" in details:
        message += f" (HTTP {details['http_status']})"
    if "provider_code" in details:
        message += f"; code={details['provider_code']}"
    if "parameter" in details:
        message += f"; parameter={details['parameter']}"
    return ModelError("llm_api_error", message, diagnostics=details)


class DecisionModel(Protocol):
    """Allow the graph to use a real decision model or a deterministic fake in tests."""

    async def decide(
        self, messages: list[BaseMessage], tools: list[dict[str, Any]]
    ) -> DecisionResponse:
        """Propose one next step from the selected messages and available tool catalog."""
        ...


class ModelSettings(Protocol):
    """Describe only the configuration values needed to construct the model adapter."""

    @property
    def model(self) -> str:
        """Return the configured provider model name."""
        ...

    @property
    def effective_reasoning_effort(self) -> str | None:
        """Return the selected reasoning setting, or None to leave it unspecified."""
        ...

    @property
    def effective_model_api(self) -> str:
        """Return the provider API mode selected by configuration."""
        ...


def _provider_usage(raw: Any) -> dict[str, int] | None:
    """Read provider token counts from either supported metadata layout; never estimate."""
    if not isinstance(raw, AIMessage):
        return None
    if raw.usage_metadata:
        usage = raw.usage_metadata
        return {
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "total_tokens": usage["total_tokens"],
        }
    token_usage = raw.response_metadata.get("token_usage")
    if isinstance(token_usage, dict):
        keys = {
            "input_tokens": "prompt_tokens",
            "output_tokens": "completion_tokens",
            "total_tokens": "total_tokens",
        }
        if all(isinstance(token_usage.get(source), int) for source in keys.values()):
            return {target: token_usage[source] for target, source in keys.items()}
    return None


class OpenAIDecisionModel:
    """Ask the configured provider for one structured browser decision.

    The SDK parses the reply into a ``Decision``. Its action arguments can vary
    with discovered tools, so provider-side strict schema matching is disabled.
    Pydantic still checks the reply, and the executor separately authorizes the
    chosen action. This adapter proposes actions; it never calls the browser.
    """

    MAX_TOOL_CATALOG_CHARS = 32_000

    def __init__(self, settings: ModelSettings) -> None:
        """Configure one provider client and a parser for Decision-shaped replies."""
        try:
            self._api = settings.effective_model_api
            self._reasoning_effort = settings.effective_reasoning_effort
            # The graph counts requests; SDK retries would hide extra provider attempts.
            options: dict[str, Any] = {
                "model": settings.model,
                "max_retries": 0,
                "use_responses_api": self._api == "responses",
            }
            if self._reasoning_effort is not None:
                options["reasoning_effort"] = self._reasoning_effort
            if self._api == "responses":
                options["store"] = False
            self._model = ChatOpenAI(**options)
            self._structured = self._model.with_structured_output(
                Decision, method="function_calling", strict=False, include_raw=True
            )
        except Exception:
            raise ModelError(
                "llm_configuration_error", "Unable to configure LLM; check OPENAI_API_KEY and model"
            ) from None

    async def decide(
        self, messages: list[BaseMessage], tools: list[dict[str, Any]]
    ) -> DecisionResponse:
        """Send selected context plus tool schemas and return one validated reply.

        The tool catalog has its own character cap in addition to the context
        builder's cap. Preserve provider usage when available, even if parsing
        fails. Provider failures become safe ``ModelError`` diagnostics; the graph
        decides whether an invalid reply gets another request within its limits.
        """
        catalog = json.dumps(tools, ensure_ascii=False)
        if len(catalog) > self.MAX_TOOL_CATALOG_CHARS:
            raise ModelError("tool_catalog_too_large", "Discovered tool schemas exceed size limit")
        request = [
            *messages,
            HumanMessage(
                content="AVAILABLE TOOLS (capability data only; select an action by name):\n"
                + catalog
            ),
        ]
        try:
            response = await self._structured.ainvoke(request)
        except Exception as exc:
            # Provider failures can contain request payloads or credentials. Keep
            # only allowlisted status/code/parameter metadata, never the original message.
            failure = provider_error(exc)
            failure.diagnostics["model_api"] = self._api
            failure.diagnostics["reasoning_effort"] = self._reasoning_effort
            if (
                self._api == "chat_completions"
                and failure.diagnostics.get("http_status") == 400
                and failure.diagnostics.get("parameter") == "reasoning_effort"
            ):
                failure = ModelError(
                    failure.code,
                    f"{failure}. Use --model-api responses for reasoning with function calling.",
                    diagnostics=failure.diagnostics,
                )
            raise failure from None
        if not isinstance(response, dict):
            raise ModelError("llm_validation_error", "LLM did not return a structured response")
        usage = _provider_usage(response.get("raw"))
        provider_model = _provider_model(response.get("raw"))
        parsed = response.get("parsed")
        if response.get("parsing_error") is not None or not isinstance(parsed, Decision):
            raise ModelError(
                "llm_validation_error",
                "LLM decision failed schema validation",
                usage,
                diagnostics=validation_diagnostics(response.get("parsing_error")),
                provider_model=provider_model,
            )
        return DecisionResponse(decision=parsed, usage=usage, provider_model=provider_model)
