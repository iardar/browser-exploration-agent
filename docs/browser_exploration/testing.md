# Browser exploration unit tests

Historical report from the original Agentic QA repository. Counts and coverage below describe
that earlier snapshot, not extraction verification. See [the extraction record](../extraction.md)
for the standalone repository's current commands, results, and environment limits.

The browser agent has **370 offline unit tests**, including **217 additional cases** for
boundaries, negative inputs, and failures. These tests run the real LangGraph, context builder,
safety policy, validation, and artifact writer with injected model and MCP dependencies. No
OpenAI key, internet access, MCP server, browser, or web application is required.

## Run and measure

From the repository root, using its Python 3.12 environment:

```bash
uv sync
uv run pytest tests/unit/browser_exploration
uv run pytest

uv run pytest tests/unit/browser_exploration \
  --cov=browser_exploration --cov-branch \
  --cov-report=term-missing \
  --cov-report=html:runs/unit-coverage/html \
  --cov-report=json:runs/unit-coverage/coverage.json \
  --cov-fail-under=99
```

The last command enforces 99% combined line and branch coverage for this package only. It
does not change repository-wide pytest settings or add a CI workflow. Reports are generated
under the ignored `runs/` directory. Open `runs/unit-coverage/html/index.html` for annotated
source and branch details. In an environment with a read-only home cache, prefix commands
with `UV_CACHE_DIR=/tmp/qa-uv-cache`.

## Measured coverage

Measured on 2026-10-21 with pytest 9.1.1 and pytest-cov 7.1.0, without additional coverage
exclusions:

| Measure | Before expansion | After expansion |
| --- | ---: | ---: |
| Browser-agent unit cases | 153 | 370 |
| Line coverage | 94.69% (1053/1112) | 99.91% (1112/1113) |
| Branch coverage | 85.62% (274/320) | 99.69% (319/320) |
| Combined coverage | 92.67% | 99.86% |

The remaining uncovered line and branch are the defensive minimum-context guard in
`ContextBuilder.build`. The validated task contract already rejects budgets below 4096
characters, above that guard's threshold. The suite does not bypass validation just to count
that otherwise unreachable path. Coverage is source-code execution coverage, not functional
coverage of an application, model quality, or proof that an arbitrary browser action is safe.

## Significant cases

| Area | Behaviors exercised |
| --- | --- |
| Contracts and configuration | Numeric boundaries; invalid URLs, ports, domains and IDNA; IPv6; unknown fields; independent defaults; invalid statuses, evidence and confidence; environment aliases and precedence |
| Safety and tool discovery | Exact domain restrictions; unsafe destinations; missing or stale element references; redirects; unsupported tool schemas; arbitrary code and mutation rejection; button labels containing the word `link` |
| Context | Complete objective and restrictions; newest observations first; Unicode and very small limits; saturation; separation between runs; overflow after navigation; preservation of source observations |
| Model adapter | Malformed structured output; constructor and structured-output setup failures; sanitized diagnostics; bounded decisions; provider-reported, missing, partial and invalid token usage |
| MCP lifecycle | One initialized session; supported capabilities; tool errors; disconnects; HTTP failures and nested exception groups; initialization and execution cancellation; cleanup failure; no automatic action retries |
| Graph and budgets | Exact browser/model limits; completion on the last allowed decision; wall-clock and tool timeouts; cancellation during connection, browser or model calls; lazy initialization; unexpected dependency failures; immutable run budgets |
| Findings and evidence | Known successful evidence required; invented references downgraded; screenshot-only claims rejected; duplicate checks removed; findings updated after another observation; interrupted runs remain incomplete |
| Artifacts and telemetry | JSON round trips; complete trajectories; retention off/on; independent binary screenshots; malformed and oversized images; private permissions; symlink/path rejection; redacted credentials; unknown token metrics remain null |
| CLI | Environment defaults and explicit overrides; invalid inputs rejected before execution; structured redacted logs; help and schema export without services; actual report paths and nonzero failure exit codes |

The added cases are in `tests/unit/browser_exploration/test_*_edge_cases.py`. Shared fake
models and sessions remain in `fakes.py`. `conftest.py` clears agent configuration and model
credentials, disables tracing and `.env` loading, and rejects IPv4/IPv6 connections and DNS
lookups. A swallowed network error still fails fixture teardown. Local Unix socket pairs
remain available for asyncio thread wakeups. These fixtures apply only to this agent's unit
test directory, leaving existing workflow tests and the opt-in live test unchanged.

Two small agent fixes accompany the regressions:

- Read-only link recognition now uses the accessibility element's role. A button labelled
  “Preview link” cannot acquire navigation authorization from its label.
- Failures while configuring ChatOpenAI structured output use the same sanitized
  `ModelError` as constructor failures, suppressing credential-bearing exception context.

## Validation scope

The full default suite passes **376 tests**, with the single live MCP test skipped unless
explicitly enabled. The six existing workflow tests and existing workflow implementation
are unchanged.

```bash
uv run ruff check src/browser_exploration \
  tests/unit/browser_exploration tests/integration/browser_exploration \
  examples/browser_exploration/mock_run.py
uv run ruff format --check src/browser_exploration \
  tests/unit/browser_exploration tests/integration/browser_exploration \
  examples/browser_exploration/mock_run.py
uv run mypy src/browser_exploration \
  tests/unit/browser_exploration tests/integration/browser_exploration \
  examples/browser_exploration/mock_run.py
```

Scoped Ruff lint, formatting, and mypy pass. Repository-wide checks continue to report
13 pre-existing Ruff findings and two pre-existing mypy callback-type errors in the LangSmith
evaluation scripts. Those files and checks were not changed.

`uv lock --check --offline` validates the installed dependency lock. The latest
`uv run qa-browser check` returned `mcp_unavailable`; no live browser or OpenAI success is
claimed for this test expansion. The separate live smoke test and controlled fixture setup
are documented in [README.md](README.md#reproduce-the-local-mcp-smoke-test).
