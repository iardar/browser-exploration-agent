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
uv run pytest -c pyproject.toml tests/unit -n auto
uv build
```

These match the repository instructions. [CI](../../.github/workflows/ci.yml) also collects the
opt-in suites without enabling them; real-model and MCP checks skip. `uv run mypy`
uses the configured source/test/example scope; it is not necessary to scan unrelated directories.
The October 26, 2026 offline verification run completed with 672 passed and 4 skipped tests;
the four skips are opt-in live checks. Use current test output or `--collect-only` for the
current number of cases. This offline result was not uploaded to ReportPortal: the most recent
verified launch remains the separately recorded 633-passed/4-skipped launch below.

The explicit `-c pyproject.toml` matters when a local `pytest.ini` is present: pytest otherwise
ignores the async mode, markers, and other options in `pyproject.toml`. If `rp_*` settings share
the project configuration, keep `pytest-reportportal` installed from the development group so
strict pytest can recognize those options. Disabling the plugin while retaining those settings
causes an unknown-option error. Version 5.6.10 does not define `rp_xdist_enabled`; xdist support
is built into the plugin. ReportPortal reporting stays opt-in; see the uploader below.

If the environment's default cache is read-only, point `UV_CACHE_DIR` at a writable cache. The
package and lock do not require a machine-specific cache path. A moved virtual environment can
have stale console-script paths; invoking a tool with the selected interpreter, such as
`uv run python -m pytest`, bypasses that old launcher. If `VIRTUAL_ENV` refers to the previous
location, deactivate the old shell environment or use `env -u VIRTUAL_ENV uv run python -m pytest`.
`uv sync --locked --reinstall-package pytest` regenerates pytest's console launchers. A copied
environment's activation scripts also contain absolute paths; use a fresh environment after a
move rather than carrying those scripts between checkouts.

### Restricted-sandbox selector workaround

Only when the documented environment misses async executor wakeups, enable the existing test-only
plugin:

```bash
PYTHONPATH=.:src uv run python -m pytest -c pyproject.toml tests/unit -n auto -p tests.sandbox_polling
```

The plugin caps selector polling at 20 ms in test processes. It does not alter application runtime
code, remove timeouts, or relax network prohibitions. The explicit import path makes the local
`tests` plugin available before pytest reads its configuration. Normal CI does not enable it.

## ReportPortal launches

The normal pytest flag is supported:

```bash
uv run python -m pytest -c pyproject.toml \
  tests/ -v -n 4 --reportportal
```

The repository's [pytest adapter](../../tests/support/pytest_reportportal.py) consumes the flag
before the upstream plugin creates a client. Only the controller checks `.env.reportportal`;
workers do not load reporting credentials. The controller checks API access before starting
tests. A connection/authentication failure stops the run with a clear error and private receipt;
no tests run and no launch is claimed. After a successful check, pytest runs all selected tests
and writes JUnit, then the controller uploads once and verifies the launch. It uses the same
resolved settings throughout; pytest CLI flags and `-o` overrides are retained. A missing key
also produces a clear usage error before tests run. Collection-only and
setup-plan commands create no launch and read no reporting credentials. Without the flag,
ordinary pytest has no reporting activity.

[`scripts/reportportal.py`](../../scripts/reportportal.py) runs the offline unit suite in a
subprocess, then uploads its actual JUnit results. It checks authenticated project access before
running tests, creates one ReportPortal launch, and reads it back until the finished status and
passed/failed/skipped totals match the JUnit report. A successful HTTP submission alone is not
reported as a verified launch. Reporting code remains outside the installed browser package.

Set `RP_API_KEY` in the shell or ignored `.env.reportportal`; never place it in `pyproject.toml`.
The local key supplied during setup has been moved to `.env.reportportal` with mode `0600`.
For a fresh checkout, use [the example](../../.env.reportportal.example) to create this local
file and supply your own key. The precedence is environment, pytest CLI flags, `.env.reportportal`,
then the selected pytest configuration (including `-o` overrides). The standalone command uses
project defaults. `RP_ENDPOINT`, `RP_PROJECT`, `RP_LAUNCH`, `RP_LAUNCH_DESCRIPTION`, and
`RP_LAUNCH_ATTRIBUTES` accept environment/file overrides; attributes use semicolon separators.

Choose an endpoint reachable **from the process running this command**. For a server published on
port 8080 of the same Linux host, use `http://localhost:8080`. `host.docker.internal` needs a
working Docker host-gateway mapping inside a container; it is not a universal Linux hostname.
The default is `http://localhost:8080`, project `system_a`, matching the local ReportPortal UI.
Container execution must explicitly override `RP_ENDPOINT` with a reachable service/gateway URL:

```bash
# Checks authentication/project access without creating a launch or running tests.
uv run python -m scripts.reportportal --check

# Runs unit tests in xdist workers and verifies their finished ReportPortal launch.
uv run python -m scripts.reportportal
```

This is separate from application model/MCP access. No paid model calls or browser service are
enabled. Additional pytest arguments follow `--`, for example `-- -k network -n 0`. In a
sandbox needing the selector workaround, use:

```bash
RP_ENDPOINT=http://localhost:8080 PYTHONPATH=.:src \
  uv run python -m scripts.reportportal -- -p tests.sandbox_polling
```

The unit fixture deliberately blocks all test-process TCP/DNS operations. The upstream streaming
hooks can conflict with that guard when sending failure logs during a test. The repository
adapter disables those hooks and publishes after all tests and fixture teardowns have finished;
the standalone command waits until the pytest subprocess exits. The guard needs no network
exceptions. Unit fixtures clear `RP_*` environment variables, and the standalone command also
removes them from its subprocess environment. The upstream plugin remains installed to recognize
the CLI flag and configuration options. Neither entry point loads the application's `.env`.

Each command writes a private receipt at `runs/reportportal/<session>/reportportal.json`.
Successful launches include `verified: true`, their URL/UUID, status, and matching counts. Access
checks do not claim to create a launch. The console prints the effective endpoint, project, and
launch name, plus a prominent verified-launch URL or upload error in pytest's final summary.
To inspect launches, open `http://localhost:8080/ui/#system_a/launches/all`; the dashboard page
is a different view. New test runs also retain private `junit.xml`. Failing or
skipped cases retain their traceback/reason as item descriptions, capped at 8,192 characters;
the configured ReportPortal key is redacted. Captured stdout/stderr and browser artifacts are
not uploaded. Test names, statuses, timings, and descriptions are sent to the configured server.

An API/upload/verification failure exits with code 2 and records the stage and known launch UUID.
A verified failing test run remains nonzero. Nonzero pytest execution/collection errors cannot
become a passing launch. Partial uploads attempt to close the active item and mark the launch
failed; if the connection is lost, inspect the recorded UUID for an unfinished launch. Writes
are not automatically replayed. Verification polls read requests for up to 30 seconds.

Publish an existing pytest JUnit report without rerunning the tests:

```bash
RP_ENDPOINT=http://localhost:8080 uv run python -m scripts.reportportal \
  --junit runs/reportportal/SESSION/junit.xml
```

This creates a new launch. It uses outcomes in the XML; the original pytest process exit code
is unavailable unless supplied with `--pytest-exit-code`. The pytest adapter supplies that code
automatically, so a verified report of a collection/internal failure stays a failed launch.
Avoid publishing the same file again when a prior upload's outcome is uncertain.
Missing/malformed/empty JUnit is rejected before launch creation.

The 2026-10-25 diagnosis found that the old `host.docker.internal:8080` default failed DNS
resolution. That earlier 625-passed/4-skipped run stopped in preflight without a launch UUID;
its passing test counts did not mean the report had been uploaded. The default was corrected
to localhost. An operator-run `--check` confirmed authentication and access to `system_a` from
the normal host terminal. The subsequent verified launch recorded 633 passed, 4 skipped, and
0 failed tests (637 total) in ReportPortal launch 18. Its receipt is
`runs/reportportal/20261025T200229-275c71e3/reportportal.json`. The later offline grader
hardening added tests after that upload, so 633/4 is not the current full-suite count.

The execution sandbox still denies localhost TCP with `EPERM`. Sandbox failures do not establish
whether the host's server is running or its key is valid. Upload/launch verification needs a
process with server access; mocked uploader tests are not evidence of server acceptance.
GitHub-hosted CI remains offline and cannot reach a developer's local ReportPortal.

## What the unit suite checks

| Area | Behaviors covered |
| --- | --- |
| Contracts and settings | Bounds, URL/domain normalization, unknown-field rejection, permission normalization, status distinctions, aliases, and override precedence |
| Tool discovery and policy | Known aliases, supported schemas, exact hosts, observed references, sensitive controls, input/submission limits, and arbitrary-code rejection |
| Context and observations | Full mandatory instructions, newest-first selection, feedback/progress priority, query compaction, network failure selection/counts/unknown formats, truncation, and run separation |
| Model adapter | Structured parsing, injected-decision revalidation, safe diagnostics, configured/provider attribution, and missing/partial usage |
| MCP lifecycle | One initialized session, setup/call/cleanup failures, grouped transport errors, cancellation, and no automatic browser replay |
| Graph and budgets | Three nodes, independent limits, bootstrap accounting, bounded correction, rejected proposals, automatic snapshots, and repeat-inspection control |
| Evidence and reporting | Evidence-ID checks, unsupported claims, screenshot exclusions, finding updates, partial results, retention, redaction, private files, and path protections |
| CLI | Configuration/task construction, help, schema export, dependency checks, safe output, and exit codes |
| Package boundary | Independent import, no host imports, checked-in schema parity, and exclusion of host/Python Playwright dependencies |

[`test_network_observations.py`](../../tests/unit/browser_exploration/test_network_observations.py)
checks failures beyond a long successful prefix, static-filter scope, pending/unsupported
outcomes, redaction, long URLs, omitted details, and both character caps. Real-graph tests with
injected browser/model fakes cover both network aliases, the compact next-decision context,
unchanged retained responses, retention disabled, evidence links, and tool-level errors.

Shared browser/model doubles are in
[`fakes.py`](../../tests/unit/browser_exploration/fakes.py). The browser unit
[`conftest.py`](../../tests/unit/conftest.py) clears operator configuration and
credentials, disables tracing and `.env` loading, and rejects IPv4/IPv6 connects and DNS lookups.
A swallowed network attempt still fails fixture teardown. DNS helpers, TCP connections, and UDP
datagrams are blocked in every unit test. Local Unix socket pairs remain available for asyncio
wakeups. These are test-process guards; external browser network controls remain a separate
deployment prerequisite.

## Fast network regression checks

Run the network-focused offline tests without a model key or MCP server:

```bash
uv run python -m pytest -c pyproject.toml tests/unit -k network -q
uv run python -m pytest -c pyproject.toml tests/unit/test_live_llm_controls.py -q
```

The production graph, adapter, and artifact store are exercised with injected responses. The
same case/task/replay-browser helpers are shared with live-model tests, so switching the model
does not change the expected request oracle. The replay browser rejects missing static-resource
capture and unexpected navigation instead of silently returning a successful-looking response.

The offline suite checks request-order permutations, large unknown response formats, the actual
adapter payload after a network capture, private raw retention, impossible metrics/timestamps,
duplicate IDs, inconsistent browser/model ledgers, and escaping evidence symlinks. Grader results
are checked against the public V1 artifact models and trajectory/decision counts.

The [grader challenge corpus](../../evals/network_capture/README.md#offline-grader-challenge-corpus)
adds 30 labeled saved-artifact variants: seven valid controls and 23 corruptions. The same real
graph/fake transports create each control, and source hashes verify that mutations affect copies.
Run the corpus with:

```bash
uv run python -m pytest -c pyproject.toml \
  tests/unit/browser_exploration/test_grader_challenges.py -q
```

It checks complete
capture scope, summary origins, evidence ownership/kinds/paths, chronology, claim references,
counts, source preservation, CLI reports, and infrastructure classification. These are grader
checks and do not make real-model performance claims.

## Bounded real-LLM component tests

The live suite has three baseline case checks and three metric-focused checks in
[`test_network_capture.py`](../../tests/live_llm/test_network_capture.py) and
[`test_network_capture_metrics.py`](../../tests/live_llm/test_network_capture_metrics.py). They call
the real application model adapter and graph with the synthetic MCP browser. The metrics cover
tool-selection precision/capture recall, evidence grounding and known-failure recall, and
long-context failure recall under the summary budget. They do not launch a real browser or prove
the live MCP transport works.

Default pytest, unit tests, and CI make no paid calls. To opt in explicitly:

```bash
uv run python -m pytest -c pyproject.toml tests/live_llm \
  --live-llm -n 0 --llm-max-calls 12 -v
```

Only this opt-in loads `.env` and requires `OPENAI_API_KEY`. The test model defaults to the app's
`gpt-4.1-mini` baseline; `OPENAI_MODEL` does not silently select a different test model. Choose a
different available model explicitly with `--llm-model MODEL`. Unsupported models or account
limits fail visibly without a model fallback.

| Control | Default |
| --- | --- |
| Total provider-attempt allowance | 6 by default; 12 for all six checks |
| Per-case provider-attempt allowance | 2, including invalid replies and failed calls |
| Output allowance | 384 tokens per request |
| Input preflight | 20,000 characters for message contents, tool catalog, and decision schema |
| Deadline | 20 seconds per request |
| SDK retries | 0 |
| Execution | Serial; parallel live runs are rejected before workers start |

The input preflight is a character check, not an exact tokenizer or price calculation. SDK framing
adds overhead. Provider usage is recorded when supplied; missing counts stay unknown. These
controls bound attempts and output rather than promise an exact monetary cost.

For one small smoke check:

```bash
uv run python -m pytest -c pyproject.toml tests/live_llm --live-llm -n 0 \
  -k NET-001 --llm-max-calls 2 --maxfail=1 -v
```

`--llm-max-calls`, `--llm-max-output-tokens`, and `--llm-timeout` override those explicit limits.
Selecting more cases than fit the request allowance is a configuration error. There are no
automatic reruns to turn a model-behavior failure into a pass. A provider connection/API failure
fails its case and prevents requests for remaining cases; those cases skip with an explanation.

Private reports are saved beneath `runs/test-live-llm/<session>/`: `session.json` records model,
limits, attempts, safe diagnostics, provider-reported usage, and durations. `artifacts/<run_id>/`
contains the normal run configuration, decisions, trajectory, metrics, and evidence. Prompts,
API keys, and the full environment are excluded from the session report. Tracing is disabled.
Never commit generated reports or credentials. Read the first failing run and session report
to distinguish provider infrastructure failures from an incorrect capture/completion decision.

The retry/deadline controls follow the [OpenAI Python SDK documentation](https://developers.openai.com/api/reference/python).
The [baseline model documentation](https://developers.openai.com/api/docs/models/gpt-4.1-mini)
describes function-calling and structured-output support; account availability still needs a
real call. The existing selector workaround above can be added to live component test commands
only when this restricted sandbox needs it.

## Measure source coverage

The coverage runner saves a separate snapshot for every invocation. It measures lines and
branches in `src/browser_exploration`, `evals`, and `scripts`, with separate totals for each
scope. It runs only offline unit tests, clears inherited service credentials and pytest options,
and disables tracing and live MCP checks.

```bash
uv run python -m scripts.coverage_report --label baseline --workers 4
# After adding tests:
uv run python -m scripts.coverage_report --label expanded --workers 4 \
  --compare coverage-reports/baseline
```

Reports are saved under ignored `coverage-reports/<label>/`. An existing label is rejected;
omit `--label` for a new UTC timestamp. By default tests run serially; `--workers` enables xdist.
Add `--sandbox-polling` only when the restricted environment needs the selector workaround.
The command returns pytest's exit code, including when a failed run still produces coverage.

Each snapshot contains:

- `html/index.html`: browsable file coverage and missing branches.
- `coverage.json`, `coverage.xml`, and `.coverage`: machine-readable coverage with test contexts.
- `summary.md`: separate line/branch totals and exact uncovered line/branch locations.
- `pytest.log` and `junit.xml`: test outcomes and failure details.
- `metadata.json`: command, runtime/tool versions, exit code, timestamps, and source/test hashes.
- `comparison.md`, when `--compare` is supplied: file-by-file changes in combined line/branch
  coverage. Both snapshots remain available; added or removed files are marked as scope changes.

Logs are written to disk during execution. Totals and report location are printed when pytest
finishes. Inspect the saved log if collection fails before coverage output can be generated.
CI does not currently enforce a coverage threshold. Compare matching scopes and inspect source
changes when interpreting differences. Execution coverage does not measure assertion quality,
application coverage, model quality, or action safety.

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
QA_BROWSER_LIVE_MCP=1 uv run pytest -c pyproject.toml tests/integration -v
```

This needs no model key. It checks navigation and snapshots across both fixture pages in one
persistent session. The fixture URL must use loopback HTTP and contain the bundled page content.
Without the opt-in flag, the integration test skips. It is not an arbitrary-target test and does
not establish autonomous model quality.

## Live quality evaluation

The [evaluation handbook](../evaluation_handbook.md) explains the current datasets, grader,
artifact examples, experiment design, and proposed LangSmith API integration in its **Next Step**
section. Its PDF is generated locally under `output/pdf/browser_evaluation_handbook.pdf`.

The [run-quality baseline](../quality_baseline.md) records the saved-run audit and the
before/after reference-context metric. Recompute it without a model or MCP server:

```bash
uv run python -m evals.run_quality --runs-dir runs --output runs/quality/audit.json
```

The audit reads every result/trajectory/decision log and referenced evidence payload, reports
damaged/incomplete runs, and compares identical reconstructed snapshot checkpoints. It does
not change the saved runs or turn historical runs into live model performance measurements.

The [network-capture dataset](../../evals/network_capture/README.md) provides two positive cases
and one large-list edge case, with controlled fixture routes, fixed request oracles, identical
agent objectives, and an offline saved-artifact grader. It measures actual capture coverage,
retention/linkage, summary counts and failure preservation, and bounded execution. It remains
outside the installed browser package; fixture/model/MCP execution is opt-in. The existing
static-demo MCP smoke test does not exercise these cases.

Model/application quality needs a separate controlled benchmark: versioned target and fixture
state, approved expected outcomes, known-defect pairs, explicit permissions/budgets, fresh or
validated browser isolation, recorded configured/provider model identity, and repeated trials.
Measure objective fulfillment, unsupported claims, reproducibility, duration, and reported usage
separately. Keep absent token usage unknown. Public-site demonstrations and successful tool calls
must not be treated as a model ranking or proof that a requirement passed.
