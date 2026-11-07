# Evaluation enhancements

Review date: 2026-11-07. Status: findings and proposed work; enhancements below are not
implemented unless explicitly identified as existing capabilities.

The repository has the machinery for a controlled network-capture pilot. The next priority is
a verified, repeatable live baseline, followed by broader cases and evaluation of the agent's
conclusions. Unit coverage establishes which implementation paths execute during tests;
live experiments must establish how reliably the agent fulfills its objectives.

## Current foundation

| Capability | Current evidence and limits |
| --- | --- |
| Offline unit tests | The latest local run passed 782 tests after adding 110 cases. Browser core coverage reached 99.11% of lines and 97.52% of branches. These results use injected transports and models. See the [testing guide](browser_exploration/testing.md#measure-source-coverage) for reproduction. |
| Agent dataset | [Three controlled cases](../evals/network_capture/cases.json): healthy traffic, a seeded HTTP 500, and a large listing with late HTTP 500/404 failures. All use the same objective. |
| Request oracle | [Reviewed request CSV](../evals/network_capture/requests.csv) defines expected methods, paths and statuses. Expected answers are excluded from the agent's task. |
| Deterministic grader | [grade_run](../evals/network_capture/grade.py) checks request coverage, evidence retention and consistency, compact summary accuracy, task configuration and budgets. A `completed` status alone cannot pass. |
| Grader regression corpus | [30 challenge scenarios](../evals/network_capture/grader_cases.json), comprising 7 valid controls and 23 corruptions. Version 2 accepts all controls and rejects all corruptions in the documented development comparison. These cases were used to improve the grader. |
| LangSmith runner | [Native evaluation runner](../evals/network_capture/langsmith_agent_eval.py) validates remote examples against local inputs, runs serially, supports repetitions, and publishes compact scores and execution metrics. Raw browser evidence stays local. |
| Real-model component tests | Opt-in tests in `tests/live_llm/` use a real model with a synthetic browser. Saved local component artifacts exist; they do not exercise real MCP/browser collection. |
| Live baseline | No saved experiments were found in the runner's default local directory, `runs/evals/network-capture-langsmith/`, during this review. Remote LangSmith experiments and alternate output locations were not audited, so this is a local evidence gap. |

The existing grader is deterministic Python code. An LLM judge is optional future work for
semantic assessment; no additional model is required to score known request identities or
artifact consistency.

## Prioritized gaps

| Priority | Missing capability | Completion evidence |
| --- | --- | --- |
| P0 | Independent live trials | Fresh browser context and confirmed fixture readiness for every attempt, with setup failures recorded. |
| P0 | Verified live baseline | All three cases run five times through the real model, MCP and browser; every planned trial is accounted for. |
| P0 | Durable experiment receipt and read-back | Local manifest links attempts, artifacts and remote runs; expected rows and feedback are verified after evaluation. |
| P1 | Representative evaluation cases | Broader traffic, failure, recovery and interaction scenarios, with a reserved evaluation split. |
| P1 | Agent-conclusion assessment | Reviewed expectations and scores for correctly identified failures, evidence-supported claims and explicit uncertainty. |
| P1 | Independent grader validation | Separately reviewed artifacts measure false acceptance and false rejection beyond the development challenge corpus. |
| P1 | Outcome and efficiency separation | Recovery cases can succeed after permitted retries; extra calls and latency remain visible as separate measurements. |
| P2 | Comparable experiments and release criteria | Fixed comparison inputs, declared metrics and thresholds, uncertainty estimates and regression reporting. |

### Independent live trials

The runner constructs a new explorer for each example and sets `max_concurrency=1`. It does
not explicitly create or reset the browser context. A new MCP connection alone does not prove
that cookies, cache, open pages or recorded requests were cleared. The current operating
instructions require the operator to provide a fresh context.

Add orchestration around each trial that:

1. Allocates a fresh browser context or independently restarted browser instance.
2. Confirms fixture accessibility from the browser's network environment and verifies that the
   reviewed fixture version is running.
3. Establishes the case's observation window and readiness condition before assessing capture.
   Delayed-request cases need explicit timing expectations; arbitrary sleeps are insufficient.
4. Records reset/readiness outcomes and closes resources after success, failure or cancellation.
5. Records configured and provider-reported model identity, model parameters, task budgets,
   source revision/hash, dataset/grader versions, and MCP/browser versions.

The existing `--check-only` validates dataset access, fixture URL settings and MCP capabilities.
It does not navigate the fixture or prove browser readiness. External browser network controls
remain a deployment prerequisite; `BROWSER_NETWORK_RESTRICTED=true` only records an attestation.
Keep trial orchestration in evaluation tooling or a deployment adapter, preserving the core
`BrowserExplorer.explore(task)` boundary.

### Experiment accounting and publication verification

The current runner waits for the SDK evaluation result and prints experiment identifiers, URL
and row count. It does not persist a publication receipt or explicitly verify all expected
remote rows and feedback values by reading them back.

Create a local manifest before execution with one identity per planned case/repetition. Update
each attempt with its state, run ID, local artifact references, grade, error category and remote
run ID when available. Persist the experiment ID and dataset fingerprint as soon as known.

After publication, verify case/repetition identities, dataset association, expected row count,
and the six current feedback keys: `capture_pass`, `oracle_consistent`, `capture_recall`,
`failure_summary_recall`, `no_unexpected_requests`, and `evidence_retained`. Verify null scores
as well as numeric values. Count setup failures and unattempted trials explicitly if execution
stops early. A printed experiment URL is insufficient evidence that every score arrived.

Keep local paths and raw evidence in the local manifest. Publish only reviewed compact fields.
If publication is interrupted, reconcile the recorded experiment before starting another one;
avoid silently duplicating trials or losing measurements that were already saved locally.

### Broader cases and a reserved evaluation split

The three current cases exercise a narrow request-capture task. A proposed next development
dataset is approximately 20–30 reviewed cases covering the following distinct behaviors.
That count is a planning starting point, not a statistical reliability guarantee.

| Scenario family | Behavior to evaluate |
| --- | --- |
| Transport failures | Correctly distinguish failed requests from HTTP error responses and identify supported failure details. |
| Delayed and pending requests | Capture within an explicit observation window; preserve unknown outcomes and disclose incomplete collection. |
| Repeated requests | Distinguish unique request coverage from event counts and preserve retries where the task requires them. |
| Filtered or partial traffic | Honor requested scope, detect static-resource omissions and avoid claiming complete capture from a filtered listing. |
| Capture/tool failures | Recover when permitted and budgeted, or report the limitation with accurate evidence. |
| Unexpected formats | Preserve uncertainty when the listing cannot be parsed; avoid fabricated counts or success claims. |
| Interaction-triggered traffic | Perform an explicitly allowed interaction, then collect and associate the resulting traffic with that action. |
| Permission boundaries | Respect blocked destinations and restricted actions while reporting the resulting task limitation. |
| Healthy controls | Report no seeded failure without inventing defects; include cases with optional irrelevant traffic. |

Give each case a reviewed objective, fixture state, allowed actions, expected observations,
acceptable stopping outcomes and scoring rules. Some cases should reward a correct limitation
report rather than successful capture. Extend the oracle representation before adding pending
or transport outcomes: the current request CSV loader expects integer HTTP statuses.

Reserve a separate set of cases for final comparisons. Avoid tuning prompts, agent behavior or
grader rules on those results. Split by scenario family or fixture variation where possible,
so near-identical copies do not create an exaggerated impression of generalization. Keep
navigation, screenshot and console capabilities in their own datasets when expanding scope.

### Assess the agent's conclusions

The current grader checks the deterministic observation summary and validates claim evidence
IDs. It does not determine whether the model-authored findings are semantically supported by
those references, whether all relevant defects were reported, or whether uncertainty was
communicated accurately.

Add reviewed expected findings and a rubric for:

- Correct identification of the affected endpoint and observed failure.
- Claims supported by the cited evidence, without invented causes or effects.
- Coverage of relevant seeded failures and absence of fabricated defects on healthy cases.
- Accurate scope and uncertainty when requests, evidence or outcomes are unavailable.

Use deterministic checks for structured facts. Start semantic assessment with human-reviewed
examples. If an LLM judge is introduced, freeze its rubric/configuration and measure agreement
against independently labeled examples, including false positives and false negatives. Keep
grader version and evaluation-layer identity visible in experiment metadata.

### Validate the grader independently

The 30 existing challenge scenarios are useful regression protection. They were selected to
expose known weaknesses and do not estimate performance on unseen artifacts.

Build an additional labeled set from independently reviewed valid runs, agent failures and
artifact defects. Include acceptable variations in summaries and recoveries so validation can
detect an overly strict grader as well as a permissive one. Reviewers should determine labels
from the fixture contract and evidence before looking at the grader's verdict.

Report valid acceptance, false rejection, corruption rejection and false acceptance with their
actual denominators. For expected rejections, inspect the failing check to distinguish detection
of the intended defect from rejection caused by an unrelated problem. Artifact consistency alone
cannot prove browser execution; trusted trial execution and retained provenance remain necessary.

### Separate task success from efficiency

The current grader requires exactly one recorded network call and requires that call to
succeed. It returns before request scoring when this check fails. This is appropriate to the
current pilot's contract, but it would reject a recovery scenario containing a failed capture
followed by a valid capture.

For a versioned recovery dataset, define how the final usable evidence is selected or combined
and score fulfillment separately from retries, duplicate work and tool-call count. Preserve the
existing strict pilot results under their original grader version. Do not relax permission,
evidence or budget requirements to improve success numbers.

## Proposed scorecard

| Dimension | Measurement | Interpretation |
| --- | --- | --- |
| Outcome | Task successes / started attempts | Include infrastructure-blocked attempts in the primary operational rate. Also publish planned, started and unattempted counts. |
| Capture | Matched expected unique requests / expected unique requests | Existing `capture_recall`; report only when measurable. Event multiplicity needs a separate metric where required. |
| Evidence | Valid linked retained evidence / assessed captures | Keep missing or corrupt evidence visible; do not infer retention from an agent claim. |
| Failure reporting | Correct reported failures / reported failures; correctly reported expected failures / expected failures | Precision and recall for model-authored findings. The existing failure-summary recall assesses the deterministic summary. |
| Claim support | Supported factual claims / assessed factual claims | Evaluate the relationship between claims and cited evidence. Healthy-case false claims remain visible. |
| Safety | Forbidden actions executed; rejected proposal count/rate | Record execution violations separately from proposals prevented by policy. Target zero forbidden executions. |
| Efficiency | Browser/model calls, input/output tokens and duration | Report distributions by case and outcome; keep failed attempts in usage totals. Missing provider usage remains unknown. |
| Reliability | Infrastructure failure rate and success variability by case | Show setup/provider/MCP failures separately while retaining them in attempt accounting. |
| Grader quality | False acceptance and false rejection on reviewed labels | Report held-out counts and uncertainty alongside development-corpus results. |

Use null for unmeasured values and zero only for measured failure or absence. When a denominator
is empty, publish the count and null rate rather than manufacturing a perfect score. Report an
additional agent success rate conditional on working infrastructure only with its denominator
and the primary operational rate beside it. Oracle mismatch invalidates quality comparison and
must be exposed as an evaluation integrity problem.

Duration and token metrics already exist per run. Cost estimates can be added using explicitly
versioned pricing and known usage, with missing usage disclosed. Aggregate reports and candidate
comparisons still need implementation.

## Rollout and decision criteria

1. **Make trials independent and accountable.** Implement reset/readiness checks, attempt
   manifests and publication read-back. Verify that interrupted and failed trials remain visible.
2. **Run the initial live baseline.** Run the three current cases five times each: 15 planned
   trials with a fixed model configuration and budgets. Use the existing repetition option after
   trial isolation is enforced. Review evidence and grades for every trial in this small pilot.
3. **Classify failures.** Distinguish infrastructure, collection, evidence, summary, planning and
   reporting failures. Select new cases based on those observations and the scenario matrix.
4. **Expand and freeze the dataset.** Review new oracles/rubrics, add recovery-specific scoring,
   and reserve final-evaluation cases before tuning candidates.
5. **Compare a candidate with the baseline.** Hold cases, budgets and deployment fixed, record
   intentional model/prompt/code differences, and use matched case/repetition comparisons.
   Balance execution order if provider or environment drift could bias results.
6. **Introduce regression gates.** Declare case-specific success rules and acceptable efficiency
   changes before inspecting candidate results. Retain strict evidence/scope checks and zero
   forbidden executions. Use the baseline to choose justified operating thresholds for future
   comparisons; do not choose thresholds retrospectively to make a candidate pass.

Five repetitions per case are useful for detecting setup problems and obvious variability.
Fifteen trials across three development scenarios provide limited evidence of broader
reliability. Publish per-case counts, failure categories and uncertainty intervals; repeated
trials on one case do not replace diverse cases. Avoid pooling all 203 requests in NET-003 with
the small cases as the sole score, because that can hide whole-case failures. Report per-case
success and macro averages alongside any pooled request metric. Treat tail latency estimates
from a small pilot as exploratory.

The first deliverable should be a reproducible 15-trial baseline with verified artifacts and
remote feedback. That evidence should guide subsequent dataset and rubric changes.

## Related documentation

- [Network-capture dataset and grader](../evals/network_capture/README.md)
- [Evaluation handbook](evaluation_handbook.md)
- [Saved-run quality baseline](quality_baseline.md)
- [Testing and coverage](browser_exploration/testing.md)
- [Browser exploration V1 contract](contracts/browser_exploration_v1.md)
