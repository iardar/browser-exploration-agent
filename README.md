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
uv run pytest tests/unit -n auto
uv build
```

If this restricted sandbox misses asyncio selector wakeups, use the optional test-only workaround:
`uv run pytest -p tests.sandbox_polling tests/unit tests/integration -q`. It does not change runtime
code or relax test network restrictions. See the extraction record for the actual validation scope.

The Python Playwright package is not a client dependency. Install/run the compatible browser on
the MCP server. The `qa-browser` command name is retained from Agentic QA; use a dedicated virtual
environment when both distributions are installed on one machine.

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

## Contracts and design

- [Browser boundary investigation](docs/browser_exploration/boundary_review.md)
- [Detailed V1 integration contract](docs/contracts/browser_exploration_v1.md)
- [Reusable node contract template](docs/templates/node_contract_template.md)
- [Adapter-first integration design](docs/adapter_design.md)
- [Extraction and validation record](docs/extraction.md)
- [Original plan review findings](docs/plan_review_findings.md)

## Current correctness limits

`completed` means the model terminated the loop; it does not prove objective completion. Evidence
checks establish provenance, not that every claim follows from its cited observation. Screenshot
pixels are retained only when requested and are not supplied to the decision model. The host must
keep observed behavior separate from approved expected behavior.

Client policy does not enforce browser egress or prove that an interaction is read-only. Storage
and validation exceptions can escape the async service. The contract documents these limits and
defines how an adapter should handle them.
