# Testing and verification

Run from the standalone repository root with Python 3.12. The unit suite uses injected model and
MCP fakes; live browser/model services are separate opt-ins. Dated counts and coverage from the
parent workspace are preserved in [historical research](../history/research_notes.md), not used
as current test totals or release thresholds. The [extraction record](../extraction.md) records
the standalone installation and packaging checks performed at extraction.

## Required offline checks

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest tests/unit -n auto
uv build
```

These match the repository instructions and [CI](../../.github/workflows/ci.yml). `uv run mypy`
uses the configured source/test/example scope; it is not necessary to scan unrelated directories.
Use current test output or `--collect-only` for the current number of cases.

If the environment's default cache is read-only, point `UV_CACHE_DIR` at a writable cache. The
package and lock do not require a machine-specific cache path. A moved virtual environment can
have stale console-script paths; invoking a tool with the selected interpreter, such as
`uv run python -m pytest`, bypasses that old launcher.

### Restricted-sandbox selector workaround

Only when the documented environment misses async executor wakeups, enable the existing test-only
plugin:

```bash
PYTHONPATH=.:src uv run python -m pytest tests/unit -n auto -p tests.sandbox_polling
```

The plugin caps selector polling at 20 ms in test processes. It does not alter application runtime
code, remove timeouts, or relax network prohibitions. The explicit import path makes the local
`tests` plugin available before pytest reads its configuration. Normal CI does not enable it.

## What the unit suite checks

| Area | Behaviors covered |
| --- | --- |
| Contracts and settings | Bounds, URL/domain normalization, unknown-field rejection, permission normalization, status distinctions, aliases, and override precedence |
| Tool discovery and policy | Known aliases, supported schemas, exact hosts, observed references, sensitive controls, input/submission limits, and arbitrary-code rejection |
| Context and observations | Full mandatory instructions, newest-first selection, feedback/progress priority, query compaction, truncation, and run separation |
| Model adapter | Structured parsing, injected-decision revalidation, safe diagnostics, configured/provider attribution, and missing/partial usage |
| MCP lifecycle | One initialized session, setup/call/cleanup failures, grouped transport errors, cancellation, and no automatic browser replay |
| Graph and budgets | Three nodes, independent limits, bootstrap accounting, bounded correction, rejected proposals, automatic snapshots, and repeat-inspection control |
| Evidence and reporting | Evidence-ID checks, unsupported claims, screenshot exclusions, finding updates, partial results, retention, redaction, private files, and path protections |
| CLI | Configuration/task construction, help, schema export, dependency checks, safe output, and exit codes |
| Package boundary | Independent import, no host imports, checked-in schema parity, and exclusion of host/Python Playwright dependencies |

Shared browser/model doubles are in
[`fakes.py`](../../tests/unit/browser_exploration/fakes.py). The browser unit
[`conftest.py`](../../tests/unit/browser_exploration/conftest.py) clears operator configuration and
credentials, disables tracing and `.env` loading, and rejects IPv4/IPv6 connects and DNS lookups.
A swallowed network attempt still fails fixture teardown. Local Unix socket pairs remain available
for asyncio wakeups. That fixture is scoped to the browser unit directory.

## Measure source coverage

```bash
uv run pytest tests/unit \
  --cov=browser_exploration --cov-branch \
  --cov-report=term-missing \
  --cov-report=html:runs/unit-coverage/html \
  --cov-report=json:runs/unit-coverage/coverage.json
```

Reports are generated under ignored `runs/`. Add the selector plugin/import path above only if
the sandbox needs it. CI does not currently enforce a coverage-percentage threshold. Any new
threshold should be based on a fresh measurement.
Source coverage does not establish application coverage, model quality, or action safety.

## Offline graph demonstration

```bash
uv run python examples/browser_exploration/mock_run.py --output-dir runs/mock
```

The example executes the real service/graph with synthetic browser responses and two deterministic
model decisions. A normal completed demo has three browser calls: initial navigation, snapshot,
and policy-rewritten Help navigation. It labels its output `MOCK ONLY`, writes actual run artifacts,
and leaves provider token usage unknown. It does not inspect a real site or call a model provider.

## Opt-in MCP/browser smoke test

Start the independently operated MCP server and bundled static fixture as described in the
[operating guide](README.md#controlled-fixture-and-mcp-smoke-test), then run:

```bash
QA_BROWSER_LIVE_MCP=1 uv run pytest tests/integration -v
```

This needs no model key. It checks navigation and snapshots across both fixture pages in one
persistent session. The fixture URL must use loopback HTTP and contain the bundled page content.
Without the opt-in flag, the integration test skips. It is not an arbitrary-target test and does
not establish autonomous model quality.

## Live quality evaluation

Model/application quality needs a separate controlled benchmark: versioned target and fixture
state, approved expected outcomes, known-defect pairs, explicit permissions/budgets, fresh or
validated browser isolation, recorded configured/provider model identity, and repeated trials.
Measure objective fulfillment, unsupported claims, reproducibility, duration, and reported usage
separately. Keep absent token usage unknown. Public-site demonstrations and successful tool calls
must not be treated as a model ranking or proof that a requirement passed.
