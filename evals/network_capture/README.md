# Network-capture evaluation dataset

This versioned pilot measures one capability: does the agent actually capture and retain the
requested network traffic, with an accurate, useful summary? It does not grade navigation,
screenshots, console capture, or the prose quality of a defect report.

| Case | Category | Fixed fixture traffic | Required result |
| --- | --- | --- | --- |
| `NET-001` | Positive | Document and three successful scripts: 4 expected requests | Retain every request; accurately report no HTTP errors or transport failures |
| `NET-002` | Positive | Document, two successful scripts, and one HTTP 500 script: 4 expected requests | Retain every request; preserve the 500 endpoint/status in the summary |
| `NET-003` | Edge | Document, 200 successful scripts, then HTTP 500 and 404 scripts: 203 expected requests | Retain the full listing and keep both late failures visible within 1,000 summary characters |

A positive case describes successful **capture**, including capture of a deliberately defective
resource. The fixed oracle is [`requests.csv`](requests.csv). Case inputs and budgets are in
[`cases.json`](cases.json). All three cases use the same objective; expected requests and statuses
are kept out of the model's task and context.

## Run a live trial

From the repository root, install with `uv sync --locked`. Start the fixture explicitly:

```bash
uv run python -m evals.network_capture.fixture --bind 127.0.0.1 --port 8766
```

Start MCP independently with a fresh browser context for each trial. The fixture and browser must
share access to the selected fixture URL. For a containerized browser, host loopback is a different
network namespace; place the server alongside the browser in the controlled network, or use its
approved reachable address. The existing static-demo Compose file does not start this server.

Use the [operating guide](../../docs/browser_exploration/README.md) for the independently enforced
browser network boundary and MCP setup. On a loopback fixture deployment, the app also needs the
explicit `BROWSER_ALLOW_PRIVATE_NETWORK=true` opt-in. Set `BROWSER_NETWORK_RESTRICTED=true` only
after the external controls exist. Neither setting installs isolation. Model requests incur the
normal provider usage; running the fixture or grader alone makes no model calls.

Run `NET-001` through the normal application, using this exact objective and budgets:

```bash
BROWSER_MAX_RECENT_OBSERVATIONS=5 \
BROWSER_MAX_OBSERVATION_CHARS=6000 \
BROWSER_MAX_CONTEXT_CHARS=18000 \
uv run python -m browser_exploration.cli run \
  --url http://127.0.0.1:8766/cases/NET-001/ \
  --goal "Capture the complete network request listing for this page, including static resources. Save the returned logs as evidence and report any observed HTTP errors or request failures. Do not navigate away. Finish once the capture is complete." \
  --max-steps 10 \
  --max-model-iterations 8 \
  --max-duration-seconds 120 \
  --retain-raw-evidence \
  --output-dir runs/evals/network-capture
```

Repeat with `/cases/NET-002/` and `/cases/NET-003/`, keeping the same goal and options. Keep the
default context allowances from `cases.json`: 5 recent observations, 6,000 observation characters,
and 18,000 selected-message characters. Leave mutation/input/submission permissions disabled.
Network inspection must include static resources; a filtered API-only capture cannot satisfy
these fixtures. The required network capability must be available on the MCP server.

The fixture uses parser-inserted same-origin script chains. Each successful script discovers the
next URL; the final errors are therefore requested after the healthy prefix. The document reaches
its final `Network fixture ready` heading after this chain. There are no external resources,
forms, or links. The optional favicon returns HTTP 204 and is excluded from coverage denominators.
Responses use `Cache-Control: no-store`. Fresh browser contexts still matter for trial isolation.

## Run the LangSmith agent-capture evaluation

The evaluation requires its dedicated Docker Compose stack. It cannot use the normal host `npx`
server on port 8931 because the runner verifies the pinned image, exact MCP launch flags, fixture
container and internal Docker network. The evaluation stack listens on port 8932, so both servers
can run at the same time.

Prerequisites:

- Docker with permission to inspect and start containers.
- Python 3.12 dependencies installed with `uv sync --locked`.
- `LANGSMITH_API_KEY` for dataset validation and publication.
- `OPENAI_API_KEY` and an available `OPENAI_MODEL` for the full evaluation. `--check-only` does
  not use the model provider and does not require the OpenAI key.
- Loopback ports 8932 and 8766 available.

Put credentials in the environment, a secret manager, or an untracked `.env`. Do not commit them:

```dotenv
LANGSMITH_API_KEY=
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4.1-mini
```

Start the dedicated evaluation deployment from the repository root:

```bash
uv sync --locked
docker compose -f evals/network_capture/compose.yaml up -d
docker compose -f evals/network_capture/compose.yaml ps
```

This stack pins both container images by digest. MCP uses `--isolated` and
`--block-service-workers`, with no shared context, persistent profile or preloaded storage.
The fixture shares the browser's internal network namespace. A fixed-destination TCP gateway
publishes only loopback ports 8932 (MCP) and 8766 (fixture); the browser has no external network
interface. Ports must be free. The generic demo deployment on port 8931 is separate.
Chrome's writable configuration, cache and temporary files use ephemeral tmpfs mounts.
Do not reuse this dedicated server for interactive browsing during an experiment.

The native LangSmith dataset `browser-network-capture-native-v1` contains the three reviewed task
inputs and expected request lists. The runner below fetches that dataset, verifies its examples
against the local source files, then runs the real agent against the controlled fixture through
the configured MCP server. It saves full artifacts locally, but sends only compact grade and
execution fields to LangSmith. `BROWSER_NETWORK_RESTRICTED=true` is an operator attestation; it
does not install network controls. Configure the provider and LangSmith keys in `.env`.
The evaluator verifies the running Compose deployment before any model call and before each
trial. It rejects other endpoints, launch configurations, container restarts and fixture drift.
It requires local Docker inspection access and defaults to `http://127.0.0.1:8932/mcp`. Leave
`--mcp-url` and `--deployment-container` at their defaults for this checked-in deployment.

Use `--check-only` to verify LangSmith dataset access, the deployment and live isolation probes
without making any model calls. It does launch disposable browser sessions:

```bash
BROWSER_NETWORK_RESTRICTED=true \
BROWSER_ALLOW_PRIVATE_NETWORK=true \
uv run python -m evals.network_capture.langsmith_agent_eval \
  --fixture-base-url http://127.0.0.1:8766 \
  --check-only
```

Continue only when this prints `"status": "ready"`, `"model_calls": 0`, and
`"isolation_verified": true`. The command connects to LangSmith to validate the dataset and uses
disposable local browser sessions to verify isolation.

Then run one serial experiment. Each dataset row runs once by default; use
`--num-repetitions 5` for the small repeated pilot described in the evaluation handbook.

```bash
BROWSER_NETWORK_RESTRICTED=true \
BROWSER_ALLOW_PRIVATE_NETWORK=true \
uv run python -m evals.network_capture.langsmith_agent_eval \
  --fixture-base-url http://127.0.0.1:8766 \
  --experiment-prefix network-capture-agent-baseline \
  --num-repetitions 5
```

Five repetitions run the documented pilot: three cases times five repetitions, for 15 planned
trials. Use `--num-repetitions 1` for a three-trial smoke run. Local artifacts and isolation
records are written below `runs/evals/network-capture-langsmith/`; the command prints the remote
LangSmith experiment name, ID and URL after completion.

The evaluator reports `capture_pass`, `capture_recall`, `failure_summary_recall`,
`no_unexpected_requests`, `evidence_retained`, and `oracle_consistent`. Infrastructure failures
remain attempted rows; recall is null when capture is unavailable or a case has no seeded HTTP
failures. The deterministic local grader supplies these scores. This command does not evaluate
the grader challenge corpus.

See the [evaluation metrics reference](../../docs/evaluation_metrics.md) for definitions,
denominators, examples, per-run checks and the separate grader challenge metrics.

### Trial isolation and verification

The evaluation adapter implements the core's existing `BrowserConnection` interface. Each trial
owns one new MCP session and isolated browser context. The evaluator closes the browser and
terminates the session after success, failure or cancellation, with bounded setup and cleanup.
Failed readiness, deployment verification or cleanup stops further browser/model trials and
makes the evaluation command fail. Subsequent SDK target invocations return failing runner
outcomes; a complete planned-attempt manifest and remote publication read-back remain separate
work.

Preflight seeds cookies, local/session storage, IndexedDB, Cache Storage and distinct network
requests on a fixed probe route. It checks a second context while the first is still alive,
then repeats the clean-context check after cancelling a seeded session. The probe pages report
a fingerprint of the local fixture and oracle files. Service workers are disabled by deployment.
Probe actions occur in disposable sessions and never enter measured trial traffic or budgets.

For each actual case, readiness is verified from the agent's ordinary navigation/snapshot:
the exact case URL, case title and `Network fixture ready` heading must be present. No evaluator
navigation, sleeps or hidden capture calls are inserted into that session. For these fixtures,
the heading follows the parser-blocking request chain; delayed/pending traffic needs its own
future observation-window contract.

Private JSON records live under `<output-dir>/_isolation/<setup-id>/`: `preflight.json` records
probes and their cleanup; `trial-*.json` records case/run identity, readiness, timing, cleanup,
deployment and MCP/Playwright/browser/client versions, configured model parameters, task budgets
and source/dataset/grader provenance. Provider-reported model identity remains in the linked
run's `decisions.jsonl`, including null when unavailable. The directory is covered by `runs/`
in `.gitignore`; records use owner-only permissions. Raw setup responses are not uploaded.

Run the reproducible browser verification without provider or LangSmith credentials:

```bash
QA_BROWSER_LIVE_MCP=1 uv run pytest -c pyproject.toml \
  tests/integration/test_trial_isolation.py -n 0 -q -s
```

This checks the probes, cancellation of an actual agent trial, and all three cases with a
scripted model and the real browser/grader. Records remain in `runs/isolation-verification/`.
It validates infrastructure, not real-model performance. Unit tests cover failure paths with
injected fakes and prohibit network calls. Task cancellation is tested; abrupt process/host
termination cannot guarantee in-process cleanup, so restart the dedicated stack before retrying.

Stop the stack after use:

```bash
docker compose -f evals/network_capture/compose.yaml down
```

## Grade the saved run offline

Use the run directory printed by the application:

```bash
uv run python -m evals.network_capture.grade \
  --case-id NET-001 \
  --run-dir runs/evals/network-capture/REPLACE_WITH_RUN_ID
```

The command prints JSON and exits with 0 for a pass, 1 for a failure or invalid artifacts. It does
not call the model, browser, or fixture server. Grading checks all of the following:

- The case URL, identical objective, fixed budgets, and default permissions match the dataset.
- Both the recorded task and the result carry the same objective; the task validates against V1.
- Static capture is explicitly enabled, without contradictory flags or positive omission notes.
- The agent stays on the requested page.
- Exactly one successful network call appears in the trajectory.
- Its evidence ID resolves to one retained, readable JSON artifact within the run directory.
- Every oracle method/path/status appears in the full returned request listing.
- No unexpected origin/request appears, apart from the optional successful favicon request.
- Summary counts match the full captured listing, and seeded error endpoints/statuses survive.
- The summary stays within 1,000 characters and the configured observation allowance.
- The run finishes within the browser/model/time budgets.
- Public V1 artifact validation succeeds; trajectory counts, model-decision counts, action
  ledgers, and unique evidence/observation IDs agree with the result metadata.
- Evidence owners/kinds, distinct retained paths, referenced claim IDs, result counts, and
  timezone-aware observation/result ordering are consistent.
- Every summary request has the same method, origin, path, and outcome as retained evidence.
  Query values are excluded from this comparison so privacy redaction remains valid.

`capture_recall` is matched unique oracle requests divided by expected unique requests. It must
be 1.0. `failure_summary_recall` must be 1.0 for cases with seeded failures; it is `null` when no
failures are expected. The report also gives raw/summary character counts, missing/unexpected
requests, run error codes, and usage metrics. Summary records are parsed structurally, rather
than grading a prose claim such as "logs captured successfully". Unknown listing formats and
unreadable evidence cannot pass. A `completed` result alone cannot pass.

The grader assesses the saved summary that the current context builder selects, not an independently
recorded provider request. It does not prove every summary reached a particular outgoing model
request. Existing real-graph unit tests separately check next-decision context selection. Manual
review is still needed to assess semantic correctness of model-authored findings.

Grader version `2` includes explicit `failed_checks`, `failure_category`, and safe artifact error
types. Malformed artifacts cannot pass. A failed run blocked before capture by `llm_api_error` or
`mcp_unavailable` is classified as infrastructure, remains visible and failing, and has no invented
zero capture score. Other failures remain evaluation failures. This classification does not
silently remove attempts from a benchmark denominator.

## Offline grader challenge corpus

[`grader_cases.json`](grader_cases.json) is a separate versioned corpus:
`network-grader-challenges-v1`. Its 30 labeled scenarios test **the grader**, using seven valid
controls and 23 deliberately corrupted saved runs. They do not add new live fixture routes or
claim to measure model performance. Most corruptions stress the large `NET-003` capture.

Valid controls include unnumbered/reordered logs, an optional favicon, minimal healthy summaries,
the `includeStatic` argument alias, and repeated requests whose counts remain accurate. Negative
cases cover changed task objectives, missing/false/conflicting capture scope, tool omission notes,
forged summary origins, incorrect evidence kinds/owners/paths, impossible timestamps, retention
contradictions, unknown claim evidence, incorrect result counts, missing/unexpected requests,
incorrect summary counts, unfinished runs, unsupported logs, tool errors, and corrupt JSON.

Run the full corpus without any model key, browser, fixture server, or ReportPortal access:

```bash
uv run python -m pytest -c pyproject.toml \
  tests/unit/browser_exploration/test_grader_challenges.py -q
```

These tests build controls through the real graph and artifact store with scripted model/browser
fakes. Each mutation operates on a separate copy; tests hash the original artifacts to verify
they remain unchanged. Socket/DNS guards stay active. Add the documented selector workaround only
when the restricted sandbox requires it. Passing this suite establishes grader regression coverage.

The offline [challenge runner](challenges.py) can also challenge existing passing saved runs:

```bash
uv run python -m evals.network_capture.challenges \
  --control NET-001=runs/evals/network-capture/RUN_001 \
  --control NET-002=runs/evals/network-capture/RUN_002 \
  --control NET-003=runs/evals/network-capture/RUN_003 \
  --output-dir runs/evals/grader-challenges/NEW_SESSION
```

Every supplied control must already pass the chosen grader. The runner makes no agent/model calls.
It refuses existing output directories and recursive copies, keeps reports private, and checks
that source controls remain unchanged. `benchmark.json` records each label/verdict/failing check,
control fingerprints, corpus and grader source SHA-256, valid acceptance, corruption rejection
recall, and false acceptance rate. All 30 scenarios run when all three controls are supplied;
partial case selections report their actual denominators. Empty metric denominators remain null.
An expected rejection must name the intended failing check, preventing unrelated failures from
masquerading as successful detection. The CLI exits nonzero when expectations are unmet.
Generated challenge trees carry `evaluation_manifest.json`, so the saved-run audit lists them
as excluded derived artifacts instead of counting copied/corrupted runs as agent executions.
Unknown manifest types never suppress an original run. Input controls remain untouched.

The October 26, 2026 local comparison used identical scripted controls and the same reviewed
corpus for both versions:

| Grader metric on this corpus | Previous grader | Version 2 |
| --- | ---: | ---: |
| Valid controls accepted | 7 / 7 | 7 / 7 |
| Corruptions rejected | 9 / 23 | 23 / 23 |
| Corruption rejection recall | 39.1% | 100% |
| False acceptances | 14 / 23 | 0 / 23 |

Generated evidence is under `runs/evals/grader-hardening/`; it includes the preserved previous
grader, source hash, controls, and before/after reports. These are development cases selected to
expose known weaknesses, not a held-out estimate of general grader or agent reliability. Artifact
consistency checks also cannot prove that an untrusted producer actually executed a browser.

## Repeated trials and scope

For a smaller real-model component check without a fixture server or MCP process, see the
[bounded LLM tests](../../docs/browser_exploration/testing.md#bounded-real-llm-component-tests).
They use these same three cases with a synthetic browser and at most six provider attempts.
They measure model capture decisions and application artifact handling; the live trials above
remain necessary to measure real browser/MCP collection.

Start with five independent trials per case. Record case/dataset version, repository revision and
any uncommitted source changes, configured/provider model, MCP/browser version, and relevant
configuration alongside each grade. Per-run model configuration, decisions, trajectory, metrics,
and evidence are already saved by the application. Keep generated artifacts under ignored `runs/`.

Report pass counts per case, capture/failure-summary recall, browser/model calls, input/output
tokens, and duration. Separate provider/MCP/setup failures using run error codes; show them as
infrastructure failures rather than silently dropping them or assigning them to the agent.

These are three development cases, not a broad reliability benchmark. Offline fixture/grader
tests establish their internal behavior; live browser and model performance must be measured
separately. Future cases can cover transport failures, filtered traffic, pending outcomes,
unsupported response formats, and logs generated after user interactions.
