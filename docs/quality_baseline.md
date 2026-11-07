# Saved-run quality baseline

Audit snapshot: October 26, 2026 (local date; scan timestamp `2026-10-25T20:09:39Z`). The
generated [`runs/quality/current-audit.json`](../runs/quality/current-audit.json) contains one
row for each of 85 original saved results, plus incomplete-run and artifact warnings. It
excludes 123 derived grader-challenge results under `runs/evals/grader-hardening/`; those are
mutated copies, not additional agent executions. Generated reports stay under ignored `runs/`;
the audit does not repair or overwrite source run artifacts.

## What the runs establish

| Recorded termination | Runs |
| --- | ---: |
| `completed` | 52 |
| `failed` | 21 |
| `model_limit` | 8 |
| `step_limit` | 1 |
| `blocked` | 2 |
| `timeout` | 1 |

Eighteen failures report `llm_api_error`; the remaining failures report one MCP availability
error and two browser-tool execution errors. These infrastructure failures need separate
treatment from model planning quality. Thirty-one completed runs still contain unresolved
questions. `completed` therefore remains a stopping decision, not an objective-success score.

The readable decision logs contain 127 rejected actions. They include 44 repeated snapshot
requests, 38 reference rejections, and four repeated network inspections. Reference rejections
account for 38 of 114 reference-bearing proposals, or 33.3%. One damaged decision log contains
16 unreadable lines, so event counts are limited to readable records.

For real-browser runs whose objective explicitly mentions network logs, 28 of 35 have successful,
readable retained network evidence. Six stopped at the provider; among the remaining 29, capture
availability is 28/29 (96.6%). This measures evidence availability, not complete objective
fulfillment. Eleven captures include successful static resources; 17 use a static-resource filter.
The captured listings contain five distinct failed-request records, all visible in their saved
summaries. These public-site runs do not supply a full independent traffic oracle and are not a
controlled model comparison. One additional run uses a synthetic browser and is excluded from
the real-browser network cohort.

Two older directories have an empty trajectory and no final result. One screenshot metadata
JSON is unreadable; its PNG exists separately. The audit reports these alongside the damaged
decision log rather than treating missing records as successes or deleting historical evidence.

## Selected metric and measured improvement

The primary regression metric is **current action-reference precision in selected observation
excerpts**: reference-token occurrences still valid in the latest raw snapshot divided by all
reference-token occurrences displayed in those excerpts. Unknown/empty denominators stay unknown.

The audit reconstructs 122 snapshot checkpoints from saved task limits, recent observations,
successful-action ledgers, and retained raw snapshots. It compares identical inputs with the
previous context policy and the current filtering policy. Thirteen checkpoints without retained
raw snapshots are excluded. It does not claim to recreate archived provider requests exactly:
original prompts were not saved, URLs are redacted, and lower-priority findings/questions are
omitted from the reconstruction.

| Metric | Previous policy | Current policy |
| --- | ---: | ---: |
| Reference tokens valid for the current page | 6,178 / 10,169 | 6,175 / 6,175 |
| Current-reference precision | 60.75% | 100% |
| Invalid/historical reference occurrences | 3,991 | 0 |
| Visible references from the newest snapshot retained | 6,175 | 6,175 |

Fifty-five checkpoints previously exposed references invalid for their current snapshot. The
current policy preserves every reference previously visible from the newest snapshot. Three
older occurrences that reused still-valid IDs are also removed because their labels belong to
historical observations. Page facts, evidence IDs, and original saved payloads remain available.

This is a measured context-quality improvement, not a measured increase in live model success.
The next outcome metric is the reference-rejection rate on new controlled trials; compare it
against the recorded 33.3% diagnostic baseline while keeping tasks, budgets, and model fixed.
Historical public-site runs alone cannot establish causality or a model ranking.

## Offline grader hardening

On October 26, 2026, the separate
[grader challenge corpus](../evals/network_capture/README.md#offline-grader-challenge-corpus)
measured the network grader with 7 valid controls and 23 deliberate artifact corruptions. The
previous grader rejected only 9 corruptions and accepted 14. Version 2 rejects all 23 while still
accepting all 7 valid controls. The same control artifacts and corpus were used for both versions;
reports record source/corpus hashes and control fingerprints under
`runs/evals/grader-hardening/`.

The fixes verify both task/result objectives, explicit complete-network scope, origin-aware
summary provenance, evidence ownership/kinds/distinct paths, claim references, result counts,
and ordered timestamps. Failed checks and infrastructure-blocked captures are reported explicitly.
This is a grader regression result on reviewed development cases, not an improvement measured
in live agent completion. The three live fixture cases and their request oracles remain unchanged.

## Implemented changes and guards

- `SafetyPolicy` increments a reference revision whenever it replaces its element map.
- The executor records the observation that owns those references and passes both to context.
- Current exact identifiers remain available; historical page reference markers are removed.
  Network/console text is preserved, and no fuzzy identifier correction is performed.
- The catalog omits snapshot when the existing unchanged-page repeat guard would reject it.
  A subsequent interaction makes it available again. Automatic snapshot recovery still works.
- Unit regressions cover same-URL refreshes, reused IDs, missing reference origins, diagnostics,
  immutable evidence, catalog availability, executor rejection, and damaged audit inputs.
- The existing three network-capture cases retain their separate request/evidence/summary
  oracles. Their offline graph checks must continue passing; real model/MCP trials remain opt-in.

Snapshot truncation can still hide an important control, and navigation cycles can still exhaust
budgets. This change does not add objective-completion claims, grant permissions, or increase run
budgets. Those remaining limitations require their own measured scenarios.

## Reproduce

```bash
uv run python -m evals.run_quality --runs-dir runs --output runs/quality/current-audit.json
uv run python -m pytest -c pyproject.toml tests/unit/browser_exploration/test_reference_context_quality.py -q
uv run python -m pytest -c pyproject.toml tests/unit/browser_exploration/test_network_eval_dataset.py -q
```

Use `-c pyproject.toml` when a local reporting `pytest.ini` would override the project settings.
See the [testing guide](browser_exploration/testing.md) for the restricted-sandbox selector
workaround and the bounded real-model trial command. Provider/MCP availability failures must
remain visible in trial reports; they do not establish behavior success or failure.
