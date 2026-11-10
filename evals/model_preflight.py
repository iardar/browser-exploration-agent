"""Check model connectivity and structured-output support before creating an experiment."""

from typing import Any

from langchain_core.messages import HumanMessage

from browser_exploration.config import Settings
from browser_exploration.model import ModelError, OpenAIDecisionModel


class ModelPreflightError(ValueError):
    """A safe provider validation failure that prevents experiment creation."""


async def validate_model(settings: Settings) -> dict[str, Any]:
    """Make one minimal structured decision call using the evaluation's model settings."""
    try:
        response = await OpenAIDecisionModel(settings).decide(
            [HumanMessage(content="Evaluation preflight. Return done=true with no claims.")],
            [],
        )
    except ModelError as exc:
        status = exc.diagnostics.get("http_status")
        suffix = f"; HTTP {status}" if isinstance(status, int) else ""
        raise ModelPreflightError(
            f"Model preflight failed ({exc.code}{suffix}); no LangSmith experiment was started."
        ) from None
    except Exception as exc:
        raise ModelPreflightError(
            f"Model preflight failed ({type(exc).__name__}); no LangSmith experiment was started."
        ) from None

    if not response.decision.done or response.decision.action is not None:
        raise ModelPreflightError(
            "Model preflight returned an unexpected decision; no LangSmith experiment was started."
        )
    return {
        "status": "ready",
        "model": settings.model,
        "model_api": settings.effective_model_api,
        "reasoning_effort": settings.effective_reasoning_effort,
        "provider_model": response.provider_model,
        "usage": response.usage,
    }
