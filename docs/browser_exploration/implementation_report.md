# Browser Exploration Agent V1 implementation report

Historical implementation report from the original Agentic QA repository. Package paths in this
copy were updated for extraction; historical validation results are not new standalone checks.
See [the extraction record](../extraction.md) for current validation.

For the subsequent unit-test expansion and current coverage results, see [testing.md](testing.md).

Validated on 2026-10-20 against repository commit `fea633c`.

## Delivered architecture

The independent package is `src/browser_exploration/`. It exposes validated
Pydantic v2 task/result contracts and the asynchronous `BrowserExplorer` Protocol. The
application service is `AutonomousBrowserExplorer`; it does not import or call the existing
workflow. All existing source, tests, and saved evaluation results are unchanged. Every
pre-existing dependency version in `uv.lock` was preserved.

The actual LangGraph nodes and edges are:

```text
START -> prepare_context
prepare_context -> explore_browser | finalize_report
explore_browser -> explore_browser | finalize_report
finalize_report -> END
```

Initial navigation and a snapshot consume two browser calls. Subsequent cycles ask ChatOpenAI
for one validated action or completion, apply the action policy, execute the discovered MCP
tool, and record the observation. There is no nested agent framework. Browser-call, model-call,
and wall-clock limits terminate the loop independently. Cancellation, timeout, failed checks,
and session cleanup failures retain their actual terminal statuses in artifacts.

`MultiServerMCPClient.session()` opens one initialized Streamable HTTP session per exploration.
`load_mcp_tools(session)` binds the discovered tools to that session for its full lifetime.
Discovery accepts known compatible schemas and excludes code execution and filesystem tools.
The key-free `qa-browser check` command exercises this same connection path.

## Contracts, context, and evidence

- `BrowserExplorationTask`: URL, objective, optional requirement/context snippets, exact allowed
  domains, mutation authorization, and execution/context budgets.
- `BrowserObservation`: action, redacted arguments, URL, bounded text, UTC timestamp, error flag,
  and evidence reference.
- `BrowserFinding`: expected/observed behavior, reproduction, evidence IDs, and explicit
  `confirmed`, `potential`, or `inconclusive` status. Invalid evidence references cannot support
  confirmation. Screenshot placeholders cannot establish visual findings.
- `BrowserExplorationResult`: real terminal status, verified behaviors, findings, unresolved
  questions, evidence, timestamps, actions, measured counters, provider token usage, and errors.

Each model decision receives newly selected context, not a growing transcript. The complete
objective and restrictions are retained or the run is blocked before connection. Recent
observations are selected and truncated deterministically. The separate tool catalog is capped
at 32,000 characters. Page data cannot authorize tools, destinations, or budget changes.

Each run writes `result.json`, `trajectory.jsonl`, and `metrics.json`. Raw evidence retention is
off by default; explicit opt-in stores separate text and binary screenshot files. Known secrets
are redacted, and artifacts use private permissions. Missing provider token usage stays `null`.
The five published JSON schemas are in [schemas/](schemas/).

## Files

New source modules: `__init__.py`, `contracts.py`, `config.py`, `state.py`, `graph.py`,
`explorer.py`, `context_builder.py`, `model.py`, `prompts.py`, `browser_tools.py`, `mcp_client.py`,
`safety.py`, `telemetry.py`, `reporting.py`, and `cli.py`.

New tests cover contracts/configuration, context/model validation, discovery, safety, session
lifecycle, graph transitions, budgets, cancellation, reporting, telemetry, and CLI behavior in
`tests/unit/browser_exploration/`. The separate opt-in transport/browser test is
`tests/integration/browser_exploration/test_live_mcp.py`.

New documentation includes this report, [setup and usage](README.md), [architecture](architecture.md),
[future integration](future_integration.md), and JSON schemas. `examples/browser_exploration/`
contains a static two-page fixture, an independent Compose example, and `mock_run.py`.

Only four previously tracked files changed: `pyproject.toml` (dependencies, CLI, async-test marker),
`uv.lock` (additive dependencies), `.env.example` (independent settings), and `.gitignore` (runs).
No existing Docker services or CI workflows were changed.

## Validation results

| Verification | Actual result |
| --- | --- |
| `uv run pytest` | **159 passed, 1 skipped**; opt-in live test skipped by default |
| New-package Ruff, including new tests/examples | **Passed** |
| New-package mypy, including new tests/examples | **Passed**, 31 Python files |
| New-package formatting | **Passed** |
| `uv run ruff check .` | 13 pre-existing findings, identical to untouched checkout |
| `uv run mypy .` | 2 pre-existing evaluator callback errors, identical to untouched checkout |
| `uv run ruff format --check .` | 27 pre-existing files need formatting, identical to untouched checkout |
| `git diff --check` | **Passed** |
| `uv run qa-browser check` at default endpoint | **Passed** against Microsoft Playwright MCP 0.0.83 |
| Opt-in real browser/MCP test | **1 passed**; navigation and snapshots across both fixture pages in one session |
| Offline graph demonstration | **Completed**, 3 synthetic browser calls, 2 synthetic decisions, no invented token usage |
| Live ChatOpenAI exploration / account-specific model availability | **Unverified**: no OpenAI API key configured |
| External RealWorld article exploration | **Blocked** by absent deployment network attestation; external navigation and findings unverified |
| Docker runtime example | YAML/configuration checked; container execution **unverified** |

Runtime versions: Python 3.12.14, LangGraph 1.2.11, LangChain Core 1.6.1,
`langchain-openai` 1.6.0, `langchain-mcp-adapters` 0.2.2, MCP SDK 1.30.0, Ruff 0.16.5,
mypy 2.3.1, pytest 9.1.1. MCP SDK 2.x was incompatible with the adapter's imports, so the
new dependency boundary constrains MCP to `<2`. The live test emits an upstream adapter warning
about the SDK's deprecated `streamablehttp_client` alias; it does not fail the test.

The managed execution sandbox needed local socket access for asyncio thread wakeups even in
offline tests. Tests still used only fake model/browser dependencies and made no internet calls.

The real smoke test initially exposed server-host validation, a missing browser, and a read-only
home-cache directory. These were resolved with explicit `localhost:8931,127.0.0.1:8931` allowed
hosts, downloaded Chrome Headless Shell 155.0.8059.12 (Playwright revision 1247), and temporary
cache paths. The static server was restarted after its old output transport disconnected.
These failures were not counted as browser successes.

Local artifacts from validation (ignored by Git):

- `runs/mock/20261020T200559-e387fa36fc6e/`: explicitly labeled mock result, trajectory, and metrics.
- `runs/20261020T194022-af2f3882b3f6/`: actual blocked external demonstration result.

## Reproduction

```bash
uv sync --locked
uv run qa-browser check
uv run python examples/browser_exploration/mock_run.py --output-dir runs/mock
```

Start the controlled fixture and MCP with a compatible installed browser in separate terminals:

```bash
python -m http.server 8765 --bind 127.0.0.1 --directory examples/browser_exploration

npx --yes @playwright/mcp@0.0.83 --headless --isolated \
  --host 127.0.0.1 --port 8931 \
  --allowed-hosts localhost:8931,127.0.0.1:8931
```

If Chrome is not installed, install the selected browser on the MCP host or pass
`--executable-path` to a compatible Playwright Chromium executable. On a restricted host set
`XDG_CACHE_HOME` and `PLAYWRIGHT_BROWSERS_PATH` to writable directories. The smoke environment
used a downloaded headless shell with `--no-sandbox`; that exception was limited to the
controlled static fixture. Keep browser sandboxing enabled in a normal deployment.

```bash
QA_BROWSER_LIVE_MCP=1 uv run pytest tests/integration/browser_exploration/test_live_mcp.py -v
```

For a genuine autonomous run, configure `OPENAI_API_KEY`, `OPENAI_MODEL`, and independently
enforced browser egress plus a read-only target environment. Then set the documented deployment
attestation and run:

```bash
uv run qa-browser run --url https://example.com --goal 'Explore read-only interactions.'
```

The exact requested RealWorld command and an isolated Docker demo are in [README.md](README.md).
No live LLM or external-application success is claimed here.

## Limits and next integration step

Client URL checks cannot stop redirects or subresource requests before they happen, nor prevent
DNS rebinding. `BROWSER_NETWORK_RESTRICTED` attests to separately enforced controls; it is not a
firewall. Generic clicks are disabled by default; authorized clicks still cannot be classified
as harmless solely by their labels. Use a restricted, read-only application environment.
Pattern redaction is not comprehensive data-loss prevention; raw pixels cannot be redacted by
the text filter. MCP server retention is independent of client retention.

The next step is an independently tested adapter mapping `RequirementAnalysis` into objective
and constraints and `RepositoryContext` into optional snippets, followed by a controlled
credentialed evaluation of exploration quality. That adapter and any workflow invocation are
deliberately deferred. See [future_integration.md](future_integration.md).
