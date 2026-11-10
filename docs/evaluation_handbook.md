# Evaluation Handbook

Snapshot: November 10, 2026. Python 3.12.

This handbook explains the evaluation system at an intermediate level. It assumes you understand
unit tests and API calls, but it does not assume prior LangSmith or browser-agent experience.

The repository currently evaluates two related capabilities:

1. **Network capture:** can the agent retain a complete request listing and summarize it
   accurately?
2. **Request details:** can the agent select the right request, inspect the requested header or
   body, and cite the supporting evidence?

Both evaluations use controlled fixtures and deterministic Python graders. LangSmith stores
dataset rows, experiment outputs, and compact feedback. Raw browser evidence remains local.

## 1. Current evaluation status

| Evaluation | Local identity | LangSmith dataset | Cases | Grader | Current evidence |
| --- | --- | --- | ---: | --- | --- |
| Network capture | `network-capture-v1` | `browser-network-capture-native-v1` (`2ed1a341-fd4d-4b95-b123-09bf46d29420`) | 3 | v2 | One three-case smoke experiment passed 3/3; one repetition does not establish reliability |
| Request details | `network-request-details-v2` | `browser-network-request-details-native-v2` (`c6a8ea9f-269a-4666-96bd-686c10c1581d`) | 22 | v2 | Dataset published; model-free preflight passed all 22 cases; no v2 real-model experiment recorded |
| Capture grader challenges | `network-grader-challenges-v1` | Local benchmark only | 30 | capture grader v2 | 7/7 valid controls accepted and 23/23 corruptions rejected |

The request-detail v1 dataset is frozen under
[`evals/network_request_details/v1/`](../evals/network_request_details/v1/). Its first experiment
used `browser-network-request-details-native-v1`
(`dea20d43-852d-43e4-936d-37aaea8f5464`), 18 cases, and grader v1. The historical LangSmith
feedback passed 15/18. Grader v2 regrades the same saved artifacts as 18/18 because three header
cases already had sufficient cited overview evidence. Historical scores remain unchanged.

The available local exports are:

- `ls_results/network-capture-smoke-da8ea165.csv`
- `ls_results/network-request-details-baseline-49359744.csv`

These files are local experiment evidence. They are ignored by Git and should not be treated as
part of the versioned corpus.

## 2. Names and versions

Several identifiers appear in an experiment. They describe different things.

| Name | Example | Meaning |
| --- | --- | --- |
| Local dataset ID | `network-request-details-v2` | Version of the reviewed files in this repository |
| LangSmith dataset name | `browser-network-request-details-native-v2` | Remote collection of examples and reference outputs |
| LangSmith dataset UUID | `c6a8ea9f-...` | Stable remote dataset identity |
| Case ID | `DET-022` | One task/reference pair inside a dataset |
| Fixture version | `2` | Version of the controlled HTTP behavior |
| Grader version | `2` | Version of the rules that interpret saved artifacts |
| Experiment prefix | `network-request-details-v2` | User-selected prefix for a new LangSmith experiment |
| Experiment name | `network-request-details-baseline-49359744` | Concrete experiment created by LangSmith |
| Run ID | `20261109T202318-ec458baa1040` | One local agent execution and artifact directory |

A dataset version and a grader version can change independently. If the grading rules change,
record the new grader version even when the dataset rows do not change. When examples or expected
outputs change, publish a new dataset identity instead of silently editing a comparison corpus.

## 3. What each evaluation layer proves

The system separates cheap checks from live model experiments.

| Layer | Model | Browser | LangSmith | What it proves |
| --- | --- | --- | --- | --- |
| Unit and replay tests | Fake | Synthetic MCP | No | Corpus loading, graph behavior, fixture rules, and grader logic |
| Grader challenge benchmark | No | No | No | Known valid artifacts pass and known corruptions fail |
| `--check-only` | No | Real, isolated | Dataset read | Dataset identity, deployment, browser isolation, capabilities, and fixtures are ready |
| Model preflight | One small call | No additional browser work | No experiment yet | Provider accepts the configured model/API/reasoning and structured output request |
| Real-model component tests | Real | Synthetic MCP | Optional trace only | Model adapter and graph behavior without a real browser deployment |
| Live experiment | Real | Real, isolated | Yes | Agent behavior for each reviewed case under one recorded configuration |
| Offline regrade | No | No | No | A saved run under a selected grader version |

Passing `--check-only` does not say anything about model behavior. Passing the model preflight
only establishes that one provider request worked at that moment. It does not guarantee that
quota, rate limits, or connectivity will remain available for every trial.

The focused offline checks require no credentials or running services:

```bash
PYTHONPATH=.:src uv run pytest -c pyproject.toml \
  tests/unit/browser_exploration/test_network_eval_dataset.py \
  tests/unit/browser_exploration/test_network_detail_eval_dataset.py \
  tests/unit/test_langsmith_agent_eval.py \
  tests/unit/test_network_detail_langsmith_eval.py \
  -n 0 -q
```

Real-model component tests are opt-in and serial. They use the synthetic MCP browser, so they do
not replace a live experiment through the dedicated Playwright deployment:

```bash
uv run pytest -c pyproject.toml tests/live_llm --live-llm -n 0 -q
```

## 4. Data flow

```text
reviewed cases + separate oracle
              |
              v
controlled HTTP fixture ----> isolated Playwright MCP session
                                      |
                                      v
                               browser agent run
                                      |
                                      v
                  result + trajectory + decisions + evidence
                                      |
                                      v
                          deterministic local grader
                                      |
                                      v
                  compact outputs and feedback in LangSmith
```

The model sees the task and browser observations. It does not receive the hidden oracle. The
grader reads the oracle after execution and compares it with retained evidence. This separation
is what makes a score more useful than asking the model whether it succeeded.

## 5. Repository map

| Path | Responsibility |
| --- | --- |
| `evals/network_capture/cases.json` | Three capture cases, shared objective, fixture version, and budgets |
| `evals/network_capture/requests.csv` | Capture oracle: expected method, path, and status |
| `evals/network_capture/fixture.py` | Controlled capture and request-detail HTTP routes |
| `evals/network_capture/grade.py` | Capture grader v2 |
| `evals/network_capture/langsmith_agent_eval.py` | Capture LangSmith runner |
| `evals/network_capture/isolation.py` | One browser session/context per measured trial |
| `evals/network_capture/grader_cases.json` | Seven valid controls and 23 corrupted-artifact challenges |
| `evals/network_capture/challenges.py` | Offline grader benchmark runner |
| `evals/network_request_details/cases.json` | Twenty-two request-detail tasks and budgets |
| `evals/network_request_details/oracle.json` | Hidden fixture payloads, selectors, markers, and secrets |
| `evals/network_request_details/grade.py` | Request-detail grader v2 |
| `evals/network_request_details/langsmith_eval.py` | Dataset publication and live experiment runner |
| `evals/network_request_details/probe.py` | Model-free validation of request-detail tools and fixture cases |
| `evals/model_preflight.py` | Provider and structured-output check before experiment creation |
| `docs/evaluation_metrics.md` | Complete metric and per-check reference with examples |

## 6. Network-capture corpus

The capture corpus asks the agent to collect the complete network request listing, including
static resources, retain the listing as evidence, and report HTTP or transport failures.

| Case | Fixture traffic | Main risk tested |
| --- | --- | --- |
| `NET-001` | Document plus three successful scripts: 4 expected requests | A healthy listing is retained completely |
| `NET-002` | Document, successful scripts, and one HTTP 500: 4 expected requests | The failure is present in evidence and summary |
| `NET-003` | Document, 200 successful scripts, then HTTP 500 and 404: 203 expected requests | Late failures survive a 1,000-character summary limit |

The favicon response is allowed but excluded from the expected-request denominator. The grader
identifies requests with `(method, path, status)`. Query values are not part of that identity.

### Capture verdict

`capture_pass` is strict. It requires all applicable checks to pass, including:

- the reviewed URL, objective, budgets, and read-only permissions;
- one successful request-listing action with static resources enabled;
- retained evidence linked to the correct observation;
- every oracle request present and no unexpected request;
- accurate summary counts and all seeded failures preserved;
- consistent timestamps, action counts, evidence IDs, and result counts.

Useful partial metrics include `capture_recall`, `failure_summary_recall`,
`no_unexpected_requests`, and `evidence_retained`. A run can have recall `1.0` and still fail the
strict verdict because its summary, permissions, budgets, or artifact relationships are wrong.

## 7. Request-detail corpus

The request-detail corpus starts with a network listing, then asks the agent to select one request
and inspect a specific part.

| Cases | Capability |
| --- | --- |
| `DET-001`–`DET-009` | JSON, error, text, XML, HTML, SVG, Unicode, and long response bodies |
| `DET-010`–`DET-011` | Request and response headers |
| `DET-012`–`DET-014` | JSON, form, and text POST request bodies |
| `DET-015`–`DET-016` | Public values remain usable while synthetic secrets are redacted |
| `DET-017`–`DET-018` | Binary MIME is recognized without reading binary response content |
| `DET-019` | Exact path versus a similar `/target-preview` path |
| `DET-020` | Same URL with GET and POST candidates |
| `DET-021` | Same URL and method with different statuses |
| `DET-022` | Identical listing lines distinguished by a request header |

### Detail verdict

`detail_pass` requires the task/configuration checks and the capability checks to pass. The main
capability feedback is:

- `target_listed_with_visible_index`
- `verified_overview`
- `correct_part_or_boundary`
- `evidence_backed_report`
- `no_wrong_request`
- `secret_redacted` for the two synthetic-secret cases

For a header case, the correct overview can already contain the requested value. Grader v2
accepts that overview when the report cites it; a redundant part call is unnecessary. For a
binary case, the safe outcome is to verify MIME information and avoid requesting or proposing a
binary response-body read.

## 8. Agent status, grader verdict, and runner status

These fields answer different questions.

### Agent run status

| Status | Meaning |
| --- | --- |
| `completed` | The model chose to finish; objective fulfillment still needs grading |
| `step_limit` | Browser-action budget was exhausted |
| `model_limit` | Model-call budget was exhausted |
| `timeout` | Duration limit was reached |
| `cancelled` | Caller cancelled execution |
| `blocked` | Policy or available capabilities prevented further progress |
| `failed` | Operational failure, such as provider or MCP failure |

### Grader result

| Field/value | Meaning |
| --- | --- |
| `passed: true`, `verdict: pass` | Every applicable deterministic check passed |
| `passed: false`, `verdict: fail` | Artifacts were valid, but one or more checks failed |
| `verdict: invalid_artifacts` | Required files or schema-valid data could not be read |
| `failed_checks` | Exact deterministic rules that failed |
| `failure_category: infrastructure` | Capture run failed from provider/MCP before a successful listing |
| `failure_category: evaluation` | Capture run was gradable but did not meet the evaluation rules |
| `failure_category: artifacts` | Capture artifacts were invalid or missing |

The request-detail grader reports `pass`, `fail`, or `invalid_artifacts`; it does not currently
add the capture grader's `failure_category` field.

### Evaluation runner output

`--check-only` prints `status: ready` when setup succeeds and `model_calls: 0`. A completed live
runner prints `status: completed` plus the experiment name, ID, URL, and row count. If a target
raises unexpectedly, its LangSmith output uses `run_status: runner_error`; that is a runner
record, not a public `BrowserExplorationResult` status.

LangSmith CSV `status: success` means the target function returned a row successfully. It does
not mean the agent fulfilled the objective. Use `capture_pass` or `detail_pass` for that decision.

## 9. Saved artifacts

Each measured agent run keeps its detailed evidence locally:

| Artifact | Contents |
| --- | --- |
| `run_config.json` | Task, model, API mode, reasoning effort, and retention setting |
| `result.json` | Public terminal result, claims, evidence ledger, errors, and metrics |
| `trajectory.jsonl` | Browser observations in execution order |
| `decisions.jsonl` | Model decisions, model errors, usage, and provider identity when available |
| `metrics.json` | Standalone copy of execution metrics |
| `evidence/obs-XXXX.json` | Retained, redacted raw browser output when retention is enabled |

The dedicated runners also write isolation records under
`<output-dir>/_isolation/<setup-id>/`. `preflight.json` describes deployment and separation
checks. Each `trial-*.json` describes one case's readiness, cleanup, versions, task, and source
provenance.

LangSmith receives compact grades, selected checks, run IDs, and execution measurements. It does
not receive the retained raw headers, bodies, or synthetic secrets.

## 10. Run the model-free preflight

Configure `LANGSMITH_API_KEY`, install dependencies, and start the pinned evaluation stack.
`--check-only` needs LangSmith dataset access but does not need `OPENAI_API_KEY`:

```bash
uv sync --locked
docker compose -f evals/network_capture/compose.yaml up -d
docker compose -f evals/network_capture/compose.yaml ps
```

The stack exposes the dedicated MCP endpoint on `127.0.0.1:8932` and the fixture on
`127.0.0.1:8766`. It is separate from an interactive MCP server on port 8931.

Set `BROWSER_NETWORK_RESTRICTED=true` only after the external network boundary is active. The
setting is an operator attestation; it does not install a firewall. Local fixtures also require
`BROWSER_ALLOW_PRIVATE_NETWORK=true`.

### Capture preflight

```bash
BROWSER_NETWORK_RESTRICTED=true \
BROWSER_ALLOW_PRIVATE_NETWORK=true \
uv run python -m evals.network_capture.langsmith_agent_eval \
  --fixture-base-url http://127.0.0.1:8766 \
  --check-only
```

### Request-detail preflight

```bash
BROWSER_NETWORK_RESTRICTED=true \
BROWSER_ALLOW_PRIVATE_NETWORK=true \
uv run python -m evals.network_request_details.langsmith_eval --check-only
```

Both commands read their LangSmith dataset, inspect the dedicated deployment, and create
disposable browser sessions. Request-detail preflight also probes all 22 cases and all four parts:
request headers, request body, response headers, and response body. Neither command calls the
model.

## 11. Publish the request-detail dataset

The active v2 request-detail runner can create its remote dataset once:

```bash
uv run python -m evals.network_request_details.langsmith_eval --publish-dataset
```

Publication verifies all rows and a source-content hash after creation. If a dataset with the same
name has different content, the command fails instead of editing it. The active v2 dataset is
already published, so normal evaluation runs only validate it.

The capture dataset is also already published. Its runner validates the configured dataset UUID;
it does not publish or mutate that dataset.

## 12. Run live experiments

Configure `LANGSMITH_API_KEY`, `OPENAI_API_KEY`, and a model available to the API project. Model,
API mode, and reasoning effort come from the normal browser settings, for example:

```dotenv
OPENAI_MODEL=gpt-5.6-luna
BROWSER_MODEL_API=responses
BROWSER_REASONING_EFFORT=medium
```

Before `aevaluate` creates an experiment, both runners make one minimal structured-output model
request through [`evals/model_preflight.py`](../evals/model_preflight.py). Authentication,
permission, model-access, quota, rate-limit, connection, and structured-output failures stop the
command before experiment creation. This probe is one additional provider call.

### Network capture

```bash
BROWSER_NETWORK_RESTRICTED=true \
BROWSER_ALLOW_PRIVATE_NETWORK=true \
uv run python -m evals.network_capture.langsmith_agent_eval \
  --fixture-base-url http://127.0.0.1:8766 \
  --experiment-prefix network-capture-agent-baseline \
  --num-repetitions 5
```

Five repetitions produce 15 planned trials: 3 cases × 5. Use one repetition for a three-row smoke
experiment. Trials are serial and each receives a new isolated browser session.

### Request details

```bash
BROWSER_NETWORK_RESTRICTED=true \
BROWSER_ALLOW_PRIVATE_NETWORK=true \
uv run python -m evals.network_request_details.langsmith_eval \
  --experiment-prefix network-request-details-v2 \
  --num-repetitions 1
```

One repetition produces 22 planned trials. The runner permits 1–10 repetitions. The capture
runner permits 1–20.

Stop the dedicated stack when finished:

```bash
docker compose -f evals/network_capture/compose.yaml down
```

## 13. Grade a saved run

Offline grading needs no browser, model, or LangSmith connection.

### Capture run

```bash
uv run python -m evals.network_capture.grade \
  --case-id NET-003 \
  --run-dir runs/evals/network-capture-langsmith/REPLACE_WITH_RUN_ID
```

### Request-detail run

```bash
uv run python -m evals.network_request_details.grade \
  --case-id DET-022 \
  --run-dir runs/evals/network-request-details-langsmith/REPLACE_WITH_RUN_ID
```

The commands print a JSON report and return a nonzero exit code when the run does not pass.
Regrading can change a verdict when grader logic changes, so always record the grader version and
do not overwrite historical experiment feedback.

## 14. Test the capture grader

The challenge benchmark copies passing control runs and applies 30 reviewed scenarios. It never
changes the source controls.

```bash
uv run python -m evals.network_capture.challenges \
  --control NET-001=runs/evals/network-capture/RUN_001 \
  --control NET-002=runs/evals/network-capture/RUN_002 \
  --control NET-003=runs/evals/network-capture/RUN_003 \
  --output-dir runs/evals/grader-hardening/NEW_OUTPUT_DIR
```

The current verified v2 result is:

- valid acceptance: 7/7;
- corruption rejection: 23/23;
- false acceptance rate on this corpus: 0;
- all reviewed expectations met.

This is evidence about the deterministic grader on known mutations. It is not agent performance
and it is not an independent estimate for every possible artifact corruption.

## 15. Read metrics correctly

The full metric reference is [`docs/evaluation_metrics.md`](evaluation_metrics.md). Four rules
prevent the most common interpretation errors:

1. **Keep null distinct from zero.** Zero means a metric was measured and failed. Null means it
   was not applicable, not comparable, or not available.
2. **Check `oracle_consistent` first.** Other LangSmith quality scores are not comparable when the
   remote reference differs from the local reviewed oracle.
3. **Separate correctness from effort.** Duration, browser calls, model calls, and tokens describe
   cost and efficiency; they do not prove task success.
4. **Use weighted aggregate recall.** Sum matched requests across cases and divide by total
   expected requests. Averaging case-level recall would give the four-request and 203-request
   cases equal weight.

Provider token totals can be null. Some CSV exports also show zero in the top-level `tokens`
column even when `outputs.execution` contains nonzero token counts. Cost is not currently a
reliable evaluation metric because the export does not record a complete matching price basis.

## 16. Recorded experiment interpretation

### Network-capture smoke experiment

`network-capture-smoke-da8ea165` ran all three capture cases once with `gpt-5.6-luna`, Responses
API, and medium reasoning effort.

- `capture_pass`: 3/3;
- `capture_recall`: 1.0 for all three;
- `no_unexpected_requests`: 1 for all three;
- `evidence_retained`: 1 for all three;
- `failure_summary_recall`: 1.0 for NET-002 and NET-003, null for healthy NET-001;
- execution: 9 browser calls, 6 model calls, 20,023 input tokens, and 965 output tokens.

This is a successful smoke result. With one repetition, it does not estimate variance or
reliability.

### Request-detail v1 baseline

`network-request-details-baseline-49359744` ran 18 v1 cases once with `gpt-5.6-luna`, Responses
API, and medium reasoning effort.

- all 18 target executions completed;
- target listing, overview, and wrong-request checks passed 18/18;
- grader v1 passed 15/18;
- grader v2 locally regrades the same artifacts as 18/18;
- execution: 85 browser calls, 67 model calls, 251,407 input tokens, and 6,596 output tokens.

The v1 result does not measure the four ambiguous v2 cases. A v2 real-model experiment is still
needed.

## 17. Diagnose failures

Start with the layer that failed rather than rerunning the full experiment immediately.

| Symptom | Likely layer | What to inspect |
| --- | --- | --- |
| Dataset name/hash mismatch | Dataset | Local case/oracle files and remote dataset identity |
| `--check-only` cannot connect | Deployment | Compose status, ports 8932/8766, endpoint, Docker access |
| Isolation verification fails | Browser lifecycle | `_isolation/<setup-id>/preflight.json` and container flags/restarts |
| Required network/detail tool missing | MCP capability | Pinned MCP image and discovered compatible schema |
| Model preflight HTTP 401 | Provider authentication | API key and endpoint |
| Model preflight HTTP 403/404 | Provider access | Project/model permission and selected model/API |
| Model preflight HTTP 429 | Provider capacity | Project quota and rate limits |
| `run_status: runner_error` | Evaluation adapter | Trial isolation record and sanitized error type |
| Agent `status: failed` with `llm_api_error` | Provider during trial | `result.json` and `decisions.jsonl` diagnostics |
| `invalid_artifacts` | Artifact integrity | Missing/malformed JSON, evidence paths, schemas |
| `completed` with pass score 0 | Evaluation behavior | `failed_checks`, retained evidence, and summary |
| Metric is null | Applicability/comparability | Oracle consistency, prerequisite checks, seeded failures |

For a failed agent run, inspect in this order:

1. `result.json`: terminal status, safe errors, metrics, and evidence ledger.
2. `run_config.json`: actual model/API/reasoning, task permissions, and budgets.
3. `decisions.jsonl`: model calls, sanitized provider diagnostics, and rejected actions.
4. `trajectory.jsonl`: browser actions, arguments, URLs, and observation summaries.
5. `evidence/`: retained raw tool output linked from the relevant observation.
6. Offline grader report: exact failed checks.

Do not include raw credentials or authorization values in bug reports. Retained evidence is
redacted, but it should still be handled as private evaluation data.

## 18. Add or change cases

### Network capture

1. Add a case to `evals/network_capture/cases.json`.
2. Add its expected requests to `evals/network_capture/requests.csv`.
3. Implement deterministic fixture behavior in `evals/network_capture/fixture.py`.
4. Add replay/fixture/grader coverage.
5. Decide whether the change requires a new dataset or fixture version.
6. Publish a new remote dataset identity when comparison rows change.

### Request details

1. Add task metadata to `evals/network_request_details/cases.json`.
2. Add the hidden payload, marker, selector, and status to `oracle.json`.
3. Extend the fixture and model-free probe when the behavior is new.
4. Add replay and grader edge cases, including incorrect target selection.
5. Bump the local dataset/fixture version when semantics change.
6. Publish a new LangSmith dataset name rather than mutating a frozen version.

Keep expected values out of model objectives. Markers should be unique so the grader can tell a
correct selection from a decoy. Include at least one plausible wrong choice when the capability
involves selection.

## 19. Current limits and next evidence

The current system is useful but deliberately narrow:

- capture has only three controlled cases;
- request-detail v2 has no real-model baseline yet;
- the recorded experiments use one repetition;
- grader challenges were used during grader hardening and are not an independent holdout;
- no semantic LLM judge evaluates general prose quality;
- `filter` and `all` network-listing semantics are not covered by the current corpora;
- the operator must enforce the external browser network boundary;
- provider reachability can change after the model preflight;
- experiment receipts and feedback read-back do not yet prove every planned row was published.

The next strong evidence would be a five-repetition capture baseline and a repeated v2
request-detail experiment, with every planned attempt accounted for and confidence intervals or
at least per-case pass counts reported.

## Related documentation

- [Network-capture evaluation guide](../evals/network_capture/README.md)
- [Request-detail evaluation guide](../evals/network_request_details/README.md)
- [Evaluation metrics reference](evaluation_metrics.md)
- [Evaluation enhancement plan](evaluation_enhancement_plan.md)
- [Browser exploration testing guide](browser_exploration/testing.md)
- [Browser exploration contract](contracts/browser_exploration_v1.md)
