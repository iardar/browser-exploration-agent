# Review of the first request-detail experiment

Source: local, ignored LangSmith export
`ls_results/network-request-details-baseline-49359744.csv` and its linked runs under
`runs/evals/network-request-details-langsmith/`.
Experiment: `network-request-details-baseline-49359744`, one repetition of the 18-case v1
dataset, model `gpt-5.6-luna`, grader version 1.

| Measure | Observed result |
| --- | ---: |
| LangSmith target executions marked successful | 18/18 |
| Grader v1 passes in the export | 15/18 |
| Target listed with a visible index | 18/18 |
| Target overview verified | 18/18 |
| Wrong-request check passed | 18/18 |
| Grader v2 local regrade of the same saved artifacts | 18/18 |
| Browser calls; LLM calls | 85; 67 |
| Provider input; output tokens in `outputs.execution` | 251,407; 6,596 |
| Sum of exported row latencies | 168.77 seconds |

DET-010, DET-011 and DET-016 failed only `correct_part_or_boundary` and
`evidence_backed_report` in grader v1. The agent stopped after a verified overview because that
overview already contained the requested request or response header and its public marker. Its
report cited the overview evidence. Grader v1 nevertheless required a separate header-part call,
which the task did not need. Grader v2 accepts an exact header value in the correct overview
section with a claim citing that observation. It still accepts an explicit matching part call.
Historical LangSmith feedback has **not** been overwritten; the 18/18 figure is a local regrade
under the changed rubric.

The CSV's root `tokens` column is zero and `total_cost` is blank for all rows, while the nested
`outputs.execution` contains nonzero provider token usage. The export cannot support a cost
estimate. `secret_redacted` is null for the 16 cases without a synthetic secret; this means not
applicable, not failed redaction. All 18 cases had one repetition, so the run measures this one
configuration and does not establish repeatability.

The v1 cases had one obvious target URL and a static script decoy. Four v2 cases now add similar
paths, same-URL requests with different methods or statuses, and same-URL requests with identical
listing lines that require inspecting candidate overviews. These edge cases test selection more
directly. Subsequent v2 measurements are documented in the [v2 baseline review](baseline_review_v2.md):
all four passed once in the Sol smoke and five times each in the Luna baseline. The historical
v1 verdicts and exported scores remain unchanged.
