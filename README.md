# Browser Exploration Agent

An independent Python capability for bounded browser exploration using an LLM, LangGraph, and an
existing Microsoft Playwright MCP server. It returns structured observations, findings, evidence
references, metrics, and an explicit terminal status.

The public integration boundary is asynchronous:

```python
from browser_exploration import BrowserExplorationResult, BrowserExplorationTask, BrowserExplorer
from browser_exploration.config import Settings
from browser_exploration.explorer import AutonomousBrowserExplorer


async def explore(explorer: BrowserExplorer) -> BrowserExplorationResult:
    return await explorer.explore(
        BrowserExplorationTask(
            url="https://example.com/",
            objective="Inspect public navigation and report supported observations.",
            allowed_domains=["example.com"],
            max_steps=12,
            max_model_iterations=8,
            max_duration_seconds=90,
        )
    )


# Construct settings only after configuring the approved MCP/browser deployment.
explorer = AutonomousBrowserExplorer(Settings())
```

The consuming application owns an adapter around this interface. Its workflow models, requirement
analysis, retrieval, approvals, test generation, and execution stay in that application. The node's
own graph has `prepare_context`, `explore_browser`, and `finalize_report` stages.

## Development

```bash
uv sync --locked
uv run qa-browser --help
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -c pyproject.toml tests/unit -n auto
uv build
```

If this restricted sandbox misses asyncio selector wakeups, use the optional test-only workaround:
`PYTHONPATH=.:src uv run python -m pytest -c pyproject.toml tests/unit -n auto -p tests.sandbox_polling`.
It does not change runtime code or relax test network restrictions. See the
[testing guide](docs/browser_exploration/testing.md) for validation scope and coverage commands.

To publish the offline unit suite to ReportPortal, set the key in ignored `.env.reportportal`
and run `uv run python -m scripts.reportportal`, or pass `--reportportal` to pytest. Both entry
points verify the finished server launch and test counts. See
[ReportPortal setup](docs/browser_exploration/testing.md#reportportal-launches)
for endpoint overrides and uploading an existing JUnit report.

The Python Playwright package is not a client dependency. Install/run the compatible browser on
the MCP server. The `qa-browser` command name is retained from Agentic QA; use a dedicated virtual
environment when both distributions are installed on one machine.

VS Code includes three debug profiles: offline demonstration, live browser exploration, and unit
tests. See the [debugging guide](docs/debugging.md) for setup, prerequisites, and breakpoint locations.

## Run modes

```bash
# Offline synthetic demonstration; no model/browser service.
uv run python examples/browser_exploration/mock_run.py --output-dir runs/mock

# MCP connection/tool check; no model request.
uv run qa-browser check

# Explicit model request; no browser connection.
uv run qa-browser check-model --model YOUR_APPROVED_MODEL

# Export public V1 schemas; no network.
uv run qa-browser schemas --output-dir docs/browser_exploration/schemas
```

For a live exploration, set `OPENAI_API_KEY`, select a model available to your account, and provide
an independently restricted MCP/browser deployment. `.env.example` leaves the external network
attestation disabled. Set `BROWSER_NETWORK_RESTRICTED=true` only after those controls exist.
Read the [operating guide](docs/browser_exploration/README.md) before running against a target.

## Documentation

| Need | Maintained document |
| --- | --- |
| Understand a run and read the code | [How it works](docs/browser_exploration/how_it_works.md) |
| Understand components, graph nodes, and remaining limitations | [Architecture](docs/architecture.md) |
| Install, configure, and operate the agent | [Operating guide](docs/browser_exploration/README.md) |
| Integrate through the public task/result boundary | [V1 contract](docs/contracts/browser_exploration_v1.md) and [adapter design](docs/adapter_design.md) |
| Run offline tests, measure coverage, or opt into MCP/LLM checks | [Testing guide](docs/browser_exploration/testing.md) |
| Evaluate network capture against controlled request oracles | [Network-capture dataset](evals/network_capture/README.md) |
| Learn the evaluation framework and proposed LangSmith integration | [Evaluation handbook](docs/evaluation_handbook.md) |
| Inspect saved-run quality and reproduce context metrics | [Run-quality baseline](docs/quality_baseline.md) |
| Step through the application in VS Code | [Debugging guide](docs/debugging.md) |
| Draft another node's contract | [Reusable Markdown template](docs/templates/node_contract_template.md) |

[Extraction provenance](docs/extraction.md) and [historical research](docs/history/research_notes.md)
are dated records. Historical test counts, parent-workflow plans, and public-site experiments are
not current operating instructions or standalone release guarantees.

## Current correctness limits

`completed` means the model terminated the loop; it does not prove objective completion. Evidence
checks establish provenance, not that every claim follows from its cited observation. Screenshot
pixels are retained only when requested and are not supplied to the decision model. The host must
keep observed behavior separate from approved expected behavior.

Client policy does not enforce browser egress or prove that an interaction is read-only. Storage
and validation exceptions can escape the async service. The contract documents these limits and
defines how an adapter should handle them.
