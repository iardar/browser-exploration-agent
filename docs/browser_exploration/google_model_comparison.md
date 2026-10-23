# Google exploration comparison: 21:31–21:37 UTC

Historical analysis of original Agentic QA runs. Saved runs and local environment files are not
included in this standalone repository. This report does not claim fresh live model validation.

Reviewed the nine October 22, 2026 runs listed below using saved configuration, decisions,
trajectories, results, and snapshot evidence.

## Model attribution

Every `run_config.json` records `configured_model: "gpt-4.1-mini"` and
`model_adapter: "OpenAIDecisionModel"`. The supplied luna/mini/sol labels conflict with those
records. The current `.env` explains the likely source: `LLM_MODEL=gpt-5.6-sol` configures the
main QA workflow; `OPENAI_MODEL=gpt-4.1-mini` configures browser exploration. Editing `LLM_MODEL`
does not change the model requested by `qa-browser`.

These runs therefore cannot establish relative model performance. Historical provider response
model identifiers were not saved. The following groups preserve the supplied labels only.

| User label | Run ID suffixes | Completed | Reached screenshot | Mean seconds | Mean total tokens |
| --- | --- | --- | --- | --- | --- |
| gpt-5.6-luna | `1667aff87416`, `628d725032ac`, `7dcf2033fefc` | 3/3 | 3/3 | 14.47 | 22,260 |
| gpt-4.1-mini | `0caca61a3fbb`, `e14fd7d18e3a`, `bb2369798557` | 1/3 | 2/3 | 11.06 | 15,168 |
| gpt-5.6-sol | `be75705044e4`, `6c0234ece287`, `2f7903b36cee` | 2/3 | 3/3 | 15.01 | 20,748 |

Averages include failed runs; the early failure reduces its group's average duration and tokens.
Do not interpret the table as a model ranking. Across all nine runs, six completed, eight captured
a screenshot, and all 43 browser calls succeeded. No actions were rejected. Total model usage was
174,526 tokens.

## Remaining failures

| Run | Failure point | Browser outcome |
| --- | --- | --- |
| `20261022T213348-0caca61a3fbb` | First model decision failed validation | Setup only; no search |
| `20261022T213434-bb2369798557` | Third model decision failed validation | Search, snapshot, screenshot succeeded |
| `20261022T213626-6c0234ece287` | Third model decision failed validation | Search, snapshot, screenshot succeeded |

The invalid responses and parsing exceptions were discarded before logging. Their precise schema
violations cannot be recovered from these artifacts. The two late failures occurred while the
model was deciding what to return after capture, with browser evidence already retained.

The first run types without submission, then fills and submits the same query. This adds one
browser/model call; the other searches use `submit=true` in one call. The former is an extra
interaction, rather than an endless loop.

## Evidence quality

The new automatic snapshot is present after submission. However, 3,336 of the original
6,000 summary characters in a representative results snapshot are `/url:` lines, mostly query
parameters. The generic Search results heading appears at character 5,636, while actual result
headings and details are omitted. Completion establishes search and capture more strongly than
it establishes inspection of result content.

Five completed runs also contain an unsupported-evidence warning when the model cites a screenshot
for verified behavior. The current verifier excludes screenshot-only evidence because pixels are
not provided to the model. A future result contract should distinguish capture completion from
visual assertions so successful captures are reported without implying pixel inspection.

## Implemented changes

- Added `--model`, taking precedence over environment settings, and selected-model output at
  startup and completion. The workflow's `LLM_MODEL` remains separate.
- Added provider-reported model metadata to future decision/error events when supplied.
- Retained safe validation diagnostics (field locations and error types) without raw malformed
  output, rejected values, or exception text.
- Added up to two consecutive correction requests, bounded by existing model/time budgets.
  Completed browser actions and evidence are reused; a malformed final response does not replay
  the search or screenshot. Persistent invalid output still produces an explicit failure.
- Compacted over-budget snapshot link queries before truncation and redacted relative URL query
  values. Applying selection to the representative saved snapshot now exposes the first Armavir
  result and Key Facts within the same 6,000-character limit.

## Repeat the comparison

Select the model explicitly for each run; keep the remaining command arguments and budgets equal:

```bash
uv run qa-browser run \
  --model gpt-5.6-luna \
  --url https://www.google.com/ \
  --goal 'Find Search field type Armavir, inspect the results, take a full-page screenshot, then finish.' \
  --allowed-domain www.google.com --allowed-domain google.com \
  --allow-form-submission --retain-raw-evidence --max-model-iterations 5
```

Repeat with `--model gpt-4.1-mini` and `--model gpt-5.6-sol`, using fresh isolated browser sessions
and the same locale/consent state. Verify each saved configured model and, where available, provider
model. Three trials provide an initial observation, not a dependable estimate of model reliability.
No new live model trials were executed during this review.

## Validation

All 394 browser exploration unit tests passed, including bounded correction attempts, recovery
after screenshot capture without replay, safe diagnostic logging, explicit model selection, and
snapshot compaction. Tests used the previously documented sandbox-only selector polling
workaround. Ruff lint/format checks passed. Mypy reported no issues in all 16 package source files
with dependency imports skipped (`--follow-imports=skip`).

## Confirmed gpt-4.1-mini baseline: 21:53–21:54 UTC

The next three runs explicitly selected `--model gpt-4.1-mini`. Their configuration and every
recorded provider model agree: `gpt-4.1-mini-2025-04-14`. These form an attributable baseline
for subsequent model comparisons.

| Run suffix | Status | Seconds | Model calls | Browser calls | Total tokens |
| --- | --- | --- | --- | --- | --- |
| `f0a906e6c88e` | completed | 16.22 | 3 | 5 | 18,575 |
| `6106e573a01d` | completed after recovery | 14.96 | 5 | 5 | 33,790 |
| `cf5704b6f96c` | completed | 13.07 | 3 | 5 | 18,550 |

All three searched, fetched an inline results snapshot, and captured a full-page screenshot.
The selected results summaries now include the first Armavir result and Key Facts within the
6,000-character cap. All 15 executed browser calls succeeded. Total usage was 70,915 tokens;
mean duration was 14.75 seconds, and mean usage was 23,638 tokens. Three successful trials do
not establish reliability across other tasks or page states.

### Recovery in run 6106e573a01d

1. Decision 1 typed and submitted `Armavir`; the executor fetched the results snapshot.
2. Decision 2 captured the full page.
3. Decision 3 failed a root-level schema constraint: `location=[]`, `type=value_error`.
4. Decision 4 proposed a redundant snapshot. The executor rejected it before a browser call.
5. Decision 5 returned completion on the final allowed iteration.

The diagnostics identify a root-level Decision validator failure, but do not retain its exact
constraint message or raw invalid response. The precise violation cannot be established from
these files. Recovery decisions 4 and 5 consumed another 15,190 tokens. The successful repair
preserved the screenshot and executed no duplicate browser operation.

### Remaining reporting and efficiency work

All three results include `Model proposed a verified behavior without valid successful evidence.`
Each final decision cites screenshot evidence for a capture-completion statement. The current
verifier excludes all screenshot evidence to prevent unsupported claims about unseen pixels,
so it also rejects the factual statement that capture succeeded.

In run `cf5704b6f96c`, the model combines search, results inspection, and capture into one behavior
with three evidence IDs. The screenshot ID causes the entire behavior to be rejected, leaving
`verified_behaviors=[]` despite successful execution. This is a reporting limitation; it is not
evidence that the search or capture failed.

Priorities for the next implementation:

- Record capture completion as executor-owned metadata distinct from visual assertions. Keep
  unsupported visual claims rejected while preserving valid search/inspection records.
- Give root-level Decision constraints distinct safe error codes, so correction feedback can
  identify the failed rule without logging raw output or sensitive values.
- Use explicit completion criteria and a concise correction context to reduce extra model calls
  after the required browser actions already succeeded.

This baseline review changed documentation only; it did not execute new live model requests.

## Explicit luna selection and environment fallback: 21:55–21:56 UTC

Runs `20261022T215525-57b03c4fdd51` and `20261022T215545-e5d27ad89eb6` both record
`configured_model: "gpt-5.6-luna"`. They failed on their first API request after successful browser
setup. This confirms the override reached the adapter; it did not silently select mini. Their
saved errors omit the provider cause, so the exact HTTP/access/parameter failure is unknown.

Run `20261022T215639-500eac2650cc` omitted `--model` and requested/received `gpt-4.1-mini`.
At inspection, `.env` contained `LLM_MODEL=gpt-5.6-luna` and `OPENAI_MODEL=gpt-4.1-mini`.
The browser setting has now been updated to `OPENAI_MODEL=gpt-5.6-luna`.

The CLI now provides `qa-browser check-model --model gpt-5.6-luna` to isolate one structured model
request from browser execution. Provider errors preserve safe HTTP/code/parameter diagnostics and
unwrap client transport errors; they never trigger an automatic model fallback. A diagnostic probe
in this restricted execution environment encountered a provider connection error, so it does not
establish whether the user's local API project can access luna. Official OpenAI documentation
lists the model and its function-calling/structured-output support:
[GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna).

Validation for the diagnostic changes: all 407 browser exploration unit tests passed with the
sandbox selector polling workaround; 25 focused model/diagnostic tests passed after the final SDK
exception fixture refinement. Ruff lint/format and the package type check with dependency imports
skipped also passed.
