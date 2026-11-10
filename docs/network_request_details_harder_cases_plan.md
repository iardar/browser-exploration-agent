# Harder request-detail evaluation cases

Status: **proposed development work**. No new dataset, fixture behavior, grader, or experiment is
implemented by this document. Baseline reviewed on November 10, 2026.

## Why expand now

The [Luna v2 baseline](../evals/network_request_details/baseline_review_v2.md) passed 110/110
trials, with every case passing five times and using the same action sequence each time. This
is a useful regression reference. The next measurement should challenge selection and evidence
handling under different conditions, rather than repeat only the same fixed layouts.

Keep this work in the existing **request-detail evaluation family**, separate from the complete
network-capture dataset. Publish the expansion as a new dataset version. Production-agent
changes are outside this plan; if a new case exposes an agent defect, retain its failing result
and propose a separate fix.

## Dataset and comparison contract

- Preserve `network-request-details-v2`, its 22 tasks, budgets, oracle, grader-v2 semantics, and
  remote dataset `browser-network-request-details-native-v2`. Do not rewrite historical scores.
- Proposed identities: local `network-request-details-v3`, remote
  `browser-network-request-details-native-v3`, fixture v3, and request-detail grader v3.
- Target **38 rows: the 22 unchanged v2 controls plus 16 new cases, DET-023–DET-038**. Report
  control and new-case results separately; a 38-case aggregate is not directly comparable with
  the old 22-case pass rate.
- Keep the current v2 entry point working. Add explicit version selection to loaders, runner,
  probe, and offline grader before exposing v3; a proposed `--dataset-version` flag does not
  exist yet. Store new reviewed data under `evals/network_request_details/v3/` and route new
  fixture behavior without changing existing v2 routes or their semantics.
- Use declared request sequences and fixed variants, not unrecorded runtime randomness.
  Record the variant, event schedule, fixture fingerprint, tool-schema fingerprint, task limits,
  source snapshot, and model/API/reasoning for each experiment.
- Freeze case contracts and a development/reserved split before model tuning. Proposed reserved
  variants are DET-026, DET-030, DET-034, and DET-036. Validate their fixtures and graders before
  publication, but do not use their live-model outcomes to tune prompts. Once used for tuning,
  they become development cases and need a new reserved replacement. These related variants
  provide a limited reserved check, not independent coverage of unseen scenario families.

## Proposed cases

All target body/header values below are hidden in a separate oracle. Tasks provide only the
selection criteria, requested operation, permitted scope, and observation limits. Each decoy
has a distinct value so reading a wrong request cannot accidentally satisfy the answer check.
Request position means position among fixture requests; do not hard-code MCP indices, which may
also include the document or static resources.

| ID | Scenario | Expected behavior and evidence |
| --- | --- | --- |
| DET-023 | Exact target first, followed by two similar-path decoys | Select the exact path even though it is not last; cite its overview and body. |
| DET-024 | Three same-URL requests: GET, target POST, GET | Select the middle POST and report its request-body value. |
| DET-025 | Target HTTP 503 before a successful same-URL retry | Select by requested status, not recency; report the 503 body. |
| DET-026 | Three identical listing identities; chosen header belongs to the first candidate | Verify candidates as needed and read the chosen body's value; do not assume the final candidate is correct. |
| DET-027 | 40 unrelated XHRs plus one exact target | Narrow the listing to make the target's real index visible, verify it, and inspect its body. |
| DET-028 | 40 prefix-matching decoys plus one exact target | Refine an overly broad match and verify the exact URL; a `/target-preview` value must fail. |
| DET-029 | Three same-URL requests distinguished by method and status | Apply both task predicates and inspect only the selected request body. |
| DET-030 | Four same-URL/method/status candidates; chosen header on the third | Inspect overviews within the fixed budget and associate the chosen body with the correct index. |
| DET-031 | Explicit request-header-part task | After verifying the overview, call `part="request-headers"` and cite that part's evidence for the requested public header. |
| DET-032 | Explicit response-header-part task | Call `part="response-headers"`; do not substitute request-header evidence for the same-named header. |
| DET-033 | Explicit request-header part with an authorization canary | Report a public header from that part while the synthetic authorization value remains redacted in every saved artifact. |
| DET-034 | Explicit response-header part with a secret-header canary | Report a public header from that part; do not expose or cite a decoy's value. Verify that the pinned server exposes the safe header before admitting the case. |
| DET-035 | Target becomes available after the first empty filtered listing | Refresh the listing, obtain the newly visible index, verify the overview, and report the body value. |
| DET-036 | First listing has a same-URL decoy; selected request arrives later | Re-list after the declared observation phase, distinguish by the task's header selector, and inspect the selected body. |
| DET-037 | First listing sees an in-flight target; completion occurs later | Preserve uncertainty, then re-list and verify the completed request before reading its body. Conditional on the pinned MCP exposing a reproducible pending state. |
| DET-038 | Target never appears before the declared observation window closes | Make the required bounded observations, report that the target was not observed, and cite listings; inventing an index or body value fails. |

DET-031–034 intentionally test an **explicit operation contract**. Their objectives must request
the part call, and their results must be reported as tool-use compliance as well as answer
correctness. Do not change v2's valid overview-only header answers into failures. If the real
server cannot expose the required safe content, classify the case as incompatible rather than
lowering its grading requirements or manufacturing a live response.

For DET-027/028, use the capability probe to choose and freeze distractor volume/filter behavior
so the narrow target remains discoverable within the actual observation limit. Do not infer MCP
filter syntax from its name. The agent must get a fair route to the answer using advertised tools.

## Step 1 — Preserve v2 and specify v3 contracts

**Implementation:** version selection in `dataset.py`, `langsmith_eval.py`, `grade.py`, and
`probe.py`; separate v3 task/oracle files; explicit remote dataset identity and case membership.
Add only evaluation metadata fields, not public agent task/result fields.

Each new case must declare: selection criteria, expected outcome (`inspected` or
`not_observed_within_window`), required part call if applicable, candidate sequence, readiness and
event rules, evidence required, forbidden actions, and fixed limits. Expected values belong only
to the oracle. Keep fixture event specifications and independently reviewed grading expectations
separate, with tests deliberately making them disagree.

Preserve v2 limits: 8 browser steps, 7 model iterations, 120 seconds, 5 recent observations,
6,000 characters per observation, and 18,000 context characters. Proposed new-case limits are
12 browser steps and 11 model iterations with the other limits unchanged. Verify through
scripted successful paths that the largest candidate set fits, then freeze these limits before
the first model experiment. Per-case limits require loader/runner/grader work; they are not
currently supported by the shared-default-only corpus. Do not increase limits to hide a measured
failure after publication.

**Validation:** unknown version/case rejection, task/oracle consistency, remote drift detection,
no hidden values in model inputs, and unchanged v2 source hash and expected examples. Regrade
all 110 saved baseline trials with grader v2. When the v3 grader supports legacy controls, replay
the same valid controls and corrupted controls to verify it preserves their original rules.

**Done when:** both versions can be selected explicitly, v2 still reproduces its old grades, and
the v3 contracts, limits, and split are reviewable before fixture implementation.

## Step 2 — Build static selection and explicit-part fixtures

**Implementation:** generalize request emission in the detail fixture to support a declared
ordered sequence with targets first/middle/last, larger lists, and independent header fields.
Extend the probe to find targets by actual listing identities rather than assumed indices.
Implement DET-023–034 first. Keep v2's parser-blocking request sequence unchanged.

Use two different values when the same header name exists in request and response sections.
For the canary cases, use only synthetic secrets and retain the existing redaction policy.
The live capability probe must verify that the requested parts and public values actually exist.

**Validation:** unit tests with fake MCP for fixture ordering and correct/wrong candidates;
scripted graph replays for every case; opt-in real-MCP integration checks for ordering, filtering,
part availability, and bounded observations. Reject a correct marker from the wrong index,
method, status, header section, or evidence ID. Require the explicit part call only on its four
declared cases. Verify that normal overview-only v2 header reports still pass.

**Done when:** each case has a passing scripted control and a negative control that fails for
its intended reason; real-MCP probes establish the fixture/tool behavior without a model call.

## Step 3 — Add controlled temporal cases

**Current constraint:** the fixture issues synchronous XHRs before `Network fixture ready`.
`ReadySession` treats that heading as readiness and rejects a bootstrap snapshot without it.
Adding a timer alone would either race the model or fail setup before the intended test begins.

**Implementation:** introduce a v3 readiness policy that distinguishes a loaded case shell from
completion of target traffic. Preserve the original readiness policy for all v2 controls.
Keep session isolation and cleanup checks mandatory.

Start with a deterministic event gate: release the next fixture phase after the adapter has
captured the first real network-listing response. This guarantees an initial empty/decoy state
even with a slow model. Forward real MCP calls and retain their real responses; the adapter must
not add target values, fabricate listings, or perform recovery on the agent's behalf. Record
release events in a private fixture ledger keyed to a unique trial, without leaking markers.
The agent must request the subsequent listing itself. This measures recovery from a controlled
state transition, not arbitrary wall-clock network latency.

Specify two listing observations for the absent-target case and expose a truthful, non-secret
window-closed state in the fixture page. Only claim absence within that declared window. Verify
that a session cannot release another session's events and that cleanup cancels pending work.

DET-037 is a feasibility gate: inspect and record how the pinned MCP represents pending
requests. If it blocks until completion or omits them without a useful state, defer this row
and report a 37-row candidate corpus instead of claiming 38 supported cases. Do not publish a
timing case whose required intermediate state cannot be demonstrated. Timer-based latency
variants and cross-navigation stale-index tests are later work after these gates are stable.

**Validation:** deterministic fake-clock/state tests; real-MCP probes observing both phases;
bounded timeout, never-released gate, duplicate release, cancellation, and concurrent-session
tests. Separate fixture setup failure from the agent's failure to refresh after a valid phase.

**Done when:** observed event order is repeatable without dependence on model speed, deadlines
and cleanup are enforced, and no target becomes visible before its declared release condition.

## Step 4 — Version grading and prove it rejects plausible mistakes

The current detail grader chooses a qualifying listing and expects subsequent detail evidence.
The new grader needs chronological association between each listing, its visible indices, the
matching overview, and the requested part. It must not pair an old overview with a newer listing
solely because the numeric index matches. Allow inspection of legitimate candidate overviews;
reading a known decoy's body should fail the selection contract.

Retain artifact integrity, permissions, budgets, redaction, and existing v2 checks. Add these
proposed v3 measurements with explicit applicability:

| Proposed measurement | Meaning |
| --- | --- |
| `detail_outcome_pass` | Correct cited content, or a justified bounded absence report for an absence case. |
| `explicit_part_used` | Correct explicit header-part call and evidence citation on DET-031–034; null otherwise. |
| `temporal_evidence_consistent` | Listing/overview/part evidence follows the declared event phases on temporal cases; null otherwise. |
| `bounded_absence_report` | Required observations and an evidence-backed limitation with no invented request content on DET-038; null otherwise. |
| `detail_pass` under grader v3 | All applicable case requirements, integrity, permission, and budget checks pass. |

Do not equate absence of a marker with a correct absence report. Start with reviewed valid and
invalid report examples; test negation and invented claims containing otherwise plausible words.
If the existing final-report schema cannot support reliable deterministic classification, keep
the absence metric explicitly manual/unmeasured until its rubric is validated. Never let a null
required measurement silently make an overall case pass.

Build request-detail-specific grader challenges, separate from the existing capture challenges:
correct value/wrong citation, decoy value, wrong header section, stale index, incomplete sequence,
missing explicit part, leaked canary, fabricated absence, missing artifacts, and over-budget
recovery. Include valid shorter paths and valid candidate inspection so strictness does not
reject legitimate behavior. Check the intended failed check, not just an overall failure.

**Done when:** reviewed valid controls pass, each corruption fails its intended requirement,
applicability/null rules are documented, and grader version and hashes accompany every score.

## Step 5 — Publish and measure in stages

1. Finish offline fixture, replay, isolation, grader, and fake-client runner checks. Run the
   normal repository gates when implementing code. Keep all network/model calls opt-in.
2. Run v3 `--check-only` against the pinned restricted deployment. Validate every admitted case,
   all required phases and parts, case counts, dataset read-back, and isolation without a model.
3. Publish a new remote dataset identity only after freezing the accepted case list. A proposed
   38-row set is 22 controls plus 16 challenges; document any deferred row explicitly.
4. Run one repetition with Luna/Responses/medium, preserving all attempts and errors. Review
   failures as agent, fixture, provider, or grader issues before changing anything.
5. After the smoke review, run five repetitions of the frozen candidate: 190 trials if all 38
   cases are admitted. Keep original failures if a correction requires a new dataset/grader
   version. Do not silently replace failed trials.
6. Reconcile local runs, isolation records, remote rows and feedback. Report v2-control and
   new-case results separately, per-case counts out of five, all unstarted/failed attempts, and
   usage/latency including failed trials. Keep provider preflight usage separate and count it once.

Publication receipts and automatic feedback read-back remain work in the
[broader evaluation plan](evaluation_enhancement_plan.md#step-2--account-for-attempts-and-verify-publication).
Until implemented, require an explicit export/local reconciliation like the v2 baseline review.
Do not set a universal release threshold from these development cases or treat a new perfect
score as proof of coverage outside the tested scenarios.
