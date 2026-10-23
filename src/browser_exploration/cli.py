"""Standalone CLI. Connection checks never initialize an LLM."""

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from pydantic import ValidationError

from .config import Settings
from .contracts import BrowserExplorationTask, ContextSnippet
from .explorer import AutonomousBrowserExplorer
from .mcp_client import check_connection
from .model import ModelError, OpenAIDecisionModel
from .reporting import redact, write_schemas

app = typer.Typer(no_args_is_help=True, help="Independent autonomous browser exploration via MCP.")


class _JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {"level": record.levelname, "event": record.getMessage()}
        for key in (
            "run_id",
            "action",
            "error",
            "reason",
            "model",
            "reasoning_effort",
            "model_api",
        ):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(redact(payload))


def _logging() -> None:
    logger = logging.getLogger("browser_exploration")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(_JSONFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def _configuration(
    model_override: str | None = None,
    reasoning_effort_override: str | None = None,
    model_api_override: str | None = None,
) -> Settings:
    load_dotenv(override=False)
    overrides: dict[str, Any] = {}
    if model_override is not None:
        overrides["model"] = model_override
    if reasoning_effort_override is not None:
        overrides["reasoning_effort"] = reasoning_effort_override
    if model_api_override is not None:
        overrides["model_api"] = model_api_override
    try:
        return Settings(**overrides)
    except ValidationError as exc:
        # Never echo rejected input values (which may contain credentials).
        locations = [".".join(map(str, error["loc"])) for error in exc.errors()]
        typer.echo(json.dumps({"error": "invalid_configuration", "fields": locations}), err=True)
        raise typer.Exit(2) from None


@app.command()
def check() -> None:
    """Initialize MCP, list supported tools and validate required capabilities."""
    _logging()
    settings = _configuration()
    diagnostics = asyncio.run(check_connection(settings))
    typer.echo(json.dumps(redact(diagnostics), indent=2))
    if diagnostics["status"] != "connected":
        raise typer.Exit(1)


@app.command("check-model")
def check_model(
    model: Annotated[str | None, typer.Option(help="Override OPENAI_MODEL for this check")] = None,
    reasoning_effort: Annotated[
        str | None, typer.Option(help="Reasoning effort; overrides BROWSER_REASONING_EFFORT")
    ] = None,
    model_api: Annotated[
        str | None, typer.Option(help="Model API: auto, chat_completions, or responses")
    ] = None,
    timeout_seconds: Annotated[float, typer.Option(min=1, max=300)] = 30,
) -> None:
    """Make one small structured model request without connecting to a browser."""
    settings = _configuration(model, reasoning_effort, model_api)

    async def probe() -> dict[str, Any]:
        try:
            async with asyncio.timeout(timeout_seconds):
                response = await OpenAIDecisionModel(settings).decide(
                    [
                        HumanMessage(
                            content="Connectivity check: return done=true and action=null. "
                            "Keep findings, verified_behaviors and unresolved_questions empty."
                        )
                    ],
                    [],
                )
            return {
                "status": "connected",
                "model": settings.model,
                "provider_model": response.provider_model,
                "usage": response.usage,
            }
        except ModelError as exc:
            return {
                "status": "failed",
                "model": settings.model,
                "error": {"code": exc.code, "message": str(exc)},
                "diagnostics": exc.diagnostics,
            }
        except TimeoutError:
            return {
                "status": "failed",
                "model": settings.model,
                "error": {"code": "llm_timeout", "message": "Model check exceeded its deadline"},
            }

    result = asyncio.run(probe())
    result["reasoning_effort"] = settings.effective_reasoning_effort
    result["model_api"] = settings.effective_model_api
    typer.echo(json.dumps(redact(result), indent=2))
    if result["status"] != "connected":
        raise typer.Exit(1)


@app.command()
def run(
    url: Annotated[str, typer.Option(help="Initial HTTP(S) target URL")],
    goal: Annotated[str, typer.Option(help="Natural-language exploration objective")],
    model: Annotated[
        str | None, typer.Option(help="Decision model; overrides OPENAI_MODEL and BROWSER_MODEL")
    ] = None,
    reasoning_effort: Annotated[
        str | None, typer.Option(help="Reasoning effort; overrides BROWSER_REASONING_EFFORT")
    ] = None,
    model_api: Annotated[
        str | None, typer.Option(help="Model API: auto, chat_completions, or responses")
    ] = None,
    allowed_domain: Annotated[
        list[str] | None, typer.Option(help="Exact allowed host; repeatable")
    ] = None,
    requirement: Annotated[str | None, typer.Option(help="Optional requirement text")] = None,
    context: Annotated[
        list[str] | None, typer.Option(help="Supplementary context; repeatable")
    ] = None,
    max_steps: Annotated[int | None, typer.Option(min=1, help="Maximum browser tool calls")] = None,
    max_model_iterations: Annotated[int | None, typer.Option(min=1)] = None,
    max_duration_seconds: Annotated[int | None, typer.Option(min=1)] = None,
    output_dir: Annotated[Path | None, typer.Option()] = None,
    retain_raw_evidence: Annotated[bool | None, typer.Option()] = None,
    allow_mutations: Annotated[
        bool, typer.Option(help="Authorize generic clicks in a controlled app")
    ] = False,
    allow_form_input: Annotated[
        bool, typer.Option(help="Authorize text entry and filling observed form fields")
    ] = False,
    allow_form_submission: Annotated[
        bool, typer.Option(help="Authorize submitting an observed form after filling it")
    ] = False,
) -> None:
    """Run bounded exploration and write result.json, trajectory.jsonl and metrics.json."""
    _logging()
    settings = _configuration(model, reasoning_effort, model_api)
    updates: dict[str, Any] = {}
    if output_dir is not None:
        updates["output_dir"] = output_dir
    if retain_raw_evidence is not None:
        updates["retain_raw_evidence"] = retain_raw_evidence
    try:
        settings = Settings.model_validate({**settings.model_dump(), **updates})
    except ValidationError:
        typer.echo(json.dumps({"error": "invalid_configuration"}), err=True)
        raise typer.Exit(2) from None
    try:
        task = BrowserExplorationTask(
            url=url,
            objective=goal,
            requirement_text=requirement,
            context_snippets=[ContextSnippet(source="cli", text=value) for value in context or []],
            allowed_domains=allowed_domain or [],
            max_steps=max_steps if max_steps is not None else settings.max_steps,
            max_model_iterations=(
                max_model_iterations
                if max_model_iterations is not None
                else settings.max_model_iterations
            ),
            max_duration_seconds=(
                max_duration_seconds
                if max_duration_seconds is not None
                else settings.max_duration_seconds
            ),
            max_recent_observations=settings.max_recent_observations,
            max_observation_chars=settings.max_observation_chars,
            max_context_chars=settings.max_context_chars,
            allow_mutations=allow_mutations,
            allow_form_input=allow_form_input,
            allow_form_submission=allow_form_submission,
        )
    except ValidationError as exc:
        locations = [".".join(map(str, error["loc"])) for error in exc.errors()]
        typer.echo(json.dumps({"error": "invalid_task", "fields": locations}), err=True)
        raise typer.Exit(2) from None
    result = asyncio.run(AutonomousBrowserExplorer(settings).explore(task))
    typer.echo(
        json.dumps(
            {
                "run_id": result.run_id,
                "status": result.status,
                "model": settings.model,
                "reasoning_effort": settings.effective_reasoning_effort,
                "model_api": settings.effective_model_api,
                "result": str(settings.output_dir / result.run_id / "result.json"),
                "errors": [error.model_dump() for error in result.errors],
            },
            indent=2,
        )
    )
    if result.status != "completed":
        raise typer.Exit(1)


@app.command()
def schemas(
    output_dir: Annotated[Path, typer.Option()] = Path("docs/browser_exploration/schemas"),
) -> None:
    """Export versioned public JSON schemas without MCP or model credentials."""
    write_schemas(output_dir)
    typer.echo(str(output_dir))


if __name__ == "__main__":
    app()
