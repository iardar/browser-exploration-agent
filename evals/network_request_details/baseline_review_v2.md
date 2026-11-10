# Request-detail v2 smoke and repeated baseline

Review date: November 10, 2026. These are measured results, not proposed acceptance thresholds.

## Evidence and configuration

The reviewed, Git-ignored exports are:

- `ls_results/network-request-details-v2-smoke-d87c7d0e.csv`
- `ls_results/network-request-details-v2-baseline-luna-medium-18afb255.csv`

Each row was joined by `outputs.run_id` to its artifacts under
`runs/evals/network-request-details-langsmith/`. Inputs and reference outputs were compared with
the reviewed corpus. Grader v2 was rerun locally and its checks, verdicts, and failed-check lists
were compared with exported grades. Execution fields were compared with `result.json` metrics.
The repeated baseline had no missing or duplicate case/repetition pairs, missing local runs, or
grade/metric discrepancies. This was a manual export reconciliation; the runner does not yet
perform automatic publication read-back.

| Setting | Sol smoke | Luna repeated baseline |
| --- | --- | --- |
| Experiment | `network-request-details-v2-smoke-d87c7d0e` | `network-request-details-v2-baseline-luna-medium-18afb255` |
| Model | `gpt-5.6-sol` | `gpt-5.6-luna` |
| API / reasoning | Responses / medium | Responses / medium |
| Cases × repetitions | 22 × 1 | 22 × 5 |
| Dataset / fixture / grader | v2 / v2 / v2 | v2 / v2 / v2 |
| Recorded revision | `0525fab-dirty` | `995ef0f` (clean) |
| Model preflight | Ready; 411 tokens | Ready; 411 tokens |

The repeated baseline's first agent run started at `2026-11-10T19:14:10.670818Z`; its last
finished at `2026-11-10T19:31:23.465362Z`. This interval excludes experiment setup before the
first trial. Its full source revision is `995ef0f14118b085eb8bc59e24bdcc01be7ea6cb`.

Both experiments record the same fingerprints:

| Artifact | SHA-256 |
| --- | --- |
| Source snapshot | `ef036fde8f762c075376b33674b52dcc964f24e6e71c0953873492334e580a26` |
| Dataset source | `28859db7589b4d0859bb1b6f3ebd4e983496c66fe2ea64298253ccd0022c85c9` |
| Fixture | `51ce98fcddf85a6a4e7def11e9c785b890ba28247caa2e1241627a0a10fecd7f` |

The recorded runtime versions also match: MCP `0.0.83`, Playwright
`1.64.0-alpha-1790635538000`, and Chrome for Testing `155.0.8059.12`. Matching inputs make the
experiments useful references, but one Sol repetition versus five Luna repetitions is not a
balanced model comparison.

## Correctness and repeatability

| Measurement | Sol smoke | Luna repeated baseline |
| --- | ---: | ---: |
| `detail_pass` | 22/22 | 110/110 |
| `oracle_consistent` | 22/22 | 110/110 |
| Target listed, overview verified, correct part/boundary, evidence-backed report, no wrong request | Each 22/22 | Each 110/110 |
| `secret_redacted` | 2/2 applicable | 10/10 applicable |
| Agent status `completed` | 22/22 | 110/110 |
| Distinct browser sessions | 22 | 110 |
| Trial readiness and cleanup | All passed | All passed |
| Browser-call errors / result errors | 0 / 0 | 0 / 0 |
| Listings using `filter` | 22/22 | 110/110 |

`secret_redacted` is unscored on the other 100 Luna trials because those cases contain no
redaction canary. Empty scores do not mean failed redaction. `completed` alone would not prove
success; the evidence-based grader independently passed all these runs.

Every case from **DET-001 through DET-022 passed 5/5** in the repeated baseline. Each of the five
repetition groups passed 22/22. The four ambiguity cases passed 20/20 collectively:

- DET-019 selected exact `/target` rather than `/target-preview`.
- DET-020 selected POST rather than GET at the same URL and inspected the request body.
- DET-021 selected the HTTP 503 request rather than the HTTP 200 request at the same URL.
- DET-022 inspected both candidate overviews, identified `x-eval-attempt: chosen`, and read
  only the selected response body.

DET-017 and DET-018 passed all ten binary-boundary trials by inspecting metadata and reporting
the limitation without requesting the binary body. An unresolved body-content question in this
situation is appropriate uncertainty.

The repeated baseline's isolation records are in
`runs/evals/network-request-details-langsmith/_isolation/b747c7b6faf640318f70873464cac449/`.
All 110 records have readiness `ready`, cleanup `passed`, status `finished`, distinct session
hashes, and `browser_closed: true`.

## Effort and latency

| Measurement | Sol smoke, 22 trials | Luna baseline, 110 trials |
| --- | ---: | ---: |
| Browser calls | 109 | 530 |
| Model calls during trials | 87 | 420 |
| Input tokens | 329,423 | 1,582,740 |
| Output tokens | 8,475 | 41,050 |
| Mean trial duration | 8.61 s | 9.33 s |
| Median trial duration | 8.52 s | 9.15 s |
| Maximum trial duration | 13.56 s | 12.75 s |

Luna's 95th-percentile trial duration is 11.23 seconds, using the nearest-rank method over 110
`outputs.execution.duration_seconds` values. Summed agent duration is 1,025.92 seconds. Setup,
publication, and model-preflight time are excluded from those per-trial durations.

Luna used 1,623,790 trial tokens, plus 411 tokens for the single preflight: **1,624,201 total**.
The preflight metadata is repeated on every exported row; count its usage once per experiment.
The CSV top-level `tokens` values are zero and `total_cost` is blank. Use the nested execution
usage for these totals; this export does not establish monetary cost.

## Interpretation and remaining coverage

Each case used the same tool names and arguments across its five Luna repetitions. Final report
wording varied, but no outcome or action-path inconsistency was observed. This supports using v2
as a regression baseline for this configuration and controlled fixture. Five attempts per fixed
case do not establish universal reliability or performance on unfamiliar websites.

Luna answered DET-010, DET-011, and DET-016 from headers in the verified overview, avoiding the
extra header-part call used in the Sol smoke. Those passes are correct under the v2 task and
grader. **No explicit `request-headers` or `response-headers` part call was measured in this Luna
baseline.** Capability preflight exercises those parts, but that is separate from agent use.

Other gaps include varied request positions, large candidate sets, traffic arriving after the
initial listing, and a target that never appears. The current fixture issues parser-blocking
requests before its ready heading. Using `filter` in these trials does not validate every filter
or `all` history semantic. Keep the current corpus and historical feedback unchanged; expand
coverage through the [harder-case development plan](../../docs/network_request_details_harder_cases_plan.md).

## Reproduce the configuration

With the documented restricted Compose stack running and credentials configured:

```bash
OPENAI_MODEL=gpt-5.6-luna \
BROWSER_MODEL_API=responses \
BROWSER_REASONING_EFFORT=medium \
BROWSER_NETWORK_RESTRICTED=true \
BROWSER_ALLOW_PRIVATE_NETWORK=true \
uv run python -m evals.network_request_details.langsmith_eval \
  --experiment-prefix network-request-details-v2-baseline-luna-medium \
  --num-repetitions 5
```

A rerun creates a new experiment. Record its actual source/deployment hashes rather than
assuming they match this baseline. Retain failed or interrupted attempts in the review instead
of replacing them with successful reruns.
