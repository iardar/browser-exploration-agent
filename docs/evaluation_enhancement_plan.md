# Evaluation enhancement plan

Review date: 2026-11-09. Status: proposed work, except where explicitly described as existing.

This plan improves the **evaluation** of the browser exploration agent. Its implementation work
belongs primarily in `evals/`, evaluation fixtures, tests, and documentation. An evaluation may
identify a production-agent defect, but changing agent behavior is a separate decision. Preserve
the public V1 contract: a model-directed `completed` status is not proof that the objective was
fulfilled. Keep live services opt-in and generated artifacts and credentials out of Git.

## What exists today

| Area | Current implementation and what it establishes |
| --- | --- |
| Dataset | [`network-capture-v1`](../evals/network_capture/cases.json) has three cases with one shared objective: a healthy listing, a listing with an HTTP 500, and a 203-request listing with late 500/404 responses. The [CSV oracle](../evals/network_capture/requests.csv) records method, path, integer status, and resource. |
| Grader | [Grader v2](../evals/network_capture/grade.py) validates artifacts and task configuration, requires one successful network capture, checks retained evidence against unique expected requests, and checks the deterministic summary. It validates claim evidence IDs but does not generally judge whether model-authored findings are correct or supported. |
| Grader tests | The [challenge corpus](../evals/network_capture/grader_cases.json) contains 7 valid controls and 23 corruptions. It is a development regression set used to improve the grader, not an independent estimate of its accuracy. |
| Live runner | The [LangSmith runner](../evals/network_capture/langsmith_agent_eval.py) validates remote examples against local inputs, runs serially with repetitions, grades local artifacts, and returns six compact feedback scores. It records source and deployment metadata, but does not maintain a complete planned-attempt receipt or verify published feedback by reading it back. |
| Trial isolation | The [evaluation adapter](../evals/network_capture/isolation.py) and pinned [Compose deployment](../evals/network_capture/compose.yaml) already verify session isolation, fixture readiness, and cleanup. The opt-in integration test exercises these with a scripted model and real browser. This does not establish real-model performance. |
| Real-model component tests | [`tests/live_llm/`](../tests/live_llm/) runs a real model against a synthetic MCP browser when explicitly enabled. It tests model decisions and artifact handling, not real browser traffic. One test checks for a known error claim by text and evidence ID; this is not a general semantic grader. |
| Offline CI | [CI](../.github/workflows/ci.yml) runs lint, types, and tests with live checks disabled. Unit tests use fakes and block network calls. |

For this review, 196 selected offline tests covering the runner, isolation, dataset, grader
challenges, and capture regressions passed. The run used the documented test-only sandbox
selector workaround. No live model, MCP, or LangSmith experiment was run for this review.

The local `runs/evals/` directory inspected for this review contained grader-hardening artifacts,
but no run in the LangSmith runner's default output directory. Remote LangSmith experiments and
other possible output locations were not audited, so a completed real-model baseline cannot be
confirmed from this review. The [older findings](evaluation_enhancements.md) remain useful; this
document turns them into an implementation sequence and adds the capture-scope issue below.

The current LangSmith feedback has these meanings:

| Feedback | Current measurement and limit |
| --- | --- |
| `capture_pass` | Whether all applicable v2 grader checks pass; zero also covers a missing grade when the oracle matches. |
| `oracle_consistent` | Whether the remote reference requests match the local reviewed oracle. |
| `capture_recall` | Matched unique method/path/outcome keys divided by expected unique keys; it does not measure repeated event counts. |
| `failure_summary_recall` | Expected HTTP failures present in the deterministic network summary divided by expected failures; null for cases with none or without a measurement. |
| `no_unexpected_requests` | Whether the parsed listing contains no unexpected request keys, apart from the allowed favicon. Its scope is the returned listing. |
| `evidence_retained` | Whether the selected network observation links to a retained evidence artifact. |

An oracle mismatch invalidates the capture quality scores. Some measurements are also null when
the necessary capture or evidence is unavailable; null must not be treated as zero.

### Finding: filtered capture can pass the complete-capture grader

The agent now supports a network `filter` when the server advertises it. The grader checks the
static-resource flag and compares the *returned* listing with the oracle, but does not check for a
filter. During this review, a copy of the saved `NET-001` control still passed with
`filter: "NET-001"` added to its recorded network action. Its retained listing was left unchanged.
This demonstrates the missing scope check; it does not demonstrate that the original run used a
filter. A real filtered listing could hide an unexpected URL that did not match the filter, making
`no_unexpected_requests` a statement about the returned subset rather than all page traffic.

The synthetic network session in [`tests/support/network_capture.py`](../tests/support/network_capture.py)
returns its oracle listing even when scope arguments are supplied. The existing forwarding test
therefore does not establish actual server filtering or history behavior.

## Order of work

| Step | Priority | Deliverable | Depends on |
| --- | --- | --- | --- |
| 1 | P0 | Make complete-capture scope explicit in a new grader version | None |
| 2 | P0 | Persist planned attempts and verify LangSmith publication | None; use Step 1 grader for the baseline |
| 3 | P0 | Collect and review a reproducible live pilot | Steps 1 and 2 |
| 4 | P1 | Expand the dataset, fixture contracts, and independent oracles | Pilot findings inform selection |
| 5 | P1 | Score recovery and capture outcome separately from efficiency | Step 4 case contracts |
| 6 | P1 | Assess the agent's findings and their evidence | Step 4 reviewed expectations |
| 7 | P1 | Validate graders on separately reviewed examples | New grader/rubric versions from Steps 1, 5, and 6 |
| 8 | P1/P2 | Evaluate network scope options and request details | Step 4; detail cases require a compatible MCP server |
| 9 | P2 | Compare experiments and introduce justified regression gates | Steps 2, 3, and 7 |

Steps 1 and 2 can proceed independently. Dataset design can start while the pilot is being
prepared, but preserve the existing pilot definition and do not tune on the reserved cases.
Update the runbook and evaluation handbook as each step lands.

## Step 1 — Check the scope of complete captures

**Problem.** An unfiltered, static-inclusive listing is necessary for the current complete-page
objective. Grader v2 can accept a filtered action if the returned rows happen to match the oracle.

**Development plan.**

1. Add an explicit scope check in a new version of
   [`grade.py`](../evals/network_capture/grade.py). For the current complete-capture contract,
   fail a network action with a `filter` argument; continue requiring explicit static inclusion,
   retained evidence, and the existing integrity checks. Future partial-capture datasets should
   specify their own allowed scope instead of inheriting this rule.
2. Preserve the identity and reproducibility of v2 grades, including access to the v2 source via
   a tagged snapshot or versioned module. Give the changed rules a new grader version and record
   that version and a source hash in new reports. Do not overwrite old grades or present a
   rescored result as though it used v2.
3. Add a regression using a copied valid artifact whose recorded action includes a filter that
   still matches every expected request. Add a case showing how an out-of-filter unexpected
   request could be hidden. Cover absent filter, a filtered partial listing, and normal unfiltered
   controls; extend the grader challenge corpus with the intended failed check.
4. Update the dataset guide to define the scope rule and clarify that the check establishes the
   requested scope, while the listing and oracle determine what was observed.

**Done when:** unfiltered controls pass, filtered complete-capture controls fail for the named
scope check, the existing challenge suite passes under the appropriate version, and old reports
retain their original grader identity.

## Step 2 — Account for attempts and verify publication

**Problem.** Per-trial isolation records exist, but they are created as targets start. The runner
prints the experiment ID and result count after waiting; it cannot currently reconstruct every
planned case/repetition after an early stop or prove that each feedback value was published.

**Development plan.**

1. Before preflight, create a private, versioned experiment manifest under the ignored output
   directory. Allocate a stable attempt ID for each `(case_id, repetition)` and record planned
   configuration, dataset content hash, source revision and snapshot hash, grader version and
   hash, deployment identity, model settings, and budgets. Record provider-reported identity and
   usage when available; leave unavailable values null.
2. Persist state transitions atomically: `planned`, `started`, and a terminal result such as
   `graded`, `setup_failed`, `runner_failed`, `cancelled`, or `not_started`. Link each attempt to
   its isolation record, local run artifacts, grade, and remote run ID when known. A failed
   preflight must leave all scheduled attempts visible as not started. Define how SDK invocations
   map to stable repetition IDs before adding retries or resumption.
3. Capture the remote experiment ID as soon as the SDK exposes it. After completion, read back
   the dataset/experiment association, each case and repetition identity, and the six feedback
   keys: `capture_pass`, `oracle_consistent`, `capture_recall`, `failure_summary_recall`,
   `no_unexpected_requests`, and `evidence_retained`. Compare values, duplicates, and missing
   rows, with bounded retries for publication delay. Verify how the installed SDK represents null
   feedback; preserve unmeasured values explicitly rather than silently converting them to zero.
4. Freeze or verify the remote dataset version used for execution, not only the preflight read.
   Remove the current fixed 100-example read limit as part of future dataset expansion. On an
   interrupted or ambiguous upload, reconcile the recorded experiment before retrying; never
   silently create duplicate measured trials. Record a separate publication state and safe error
   category.
5. Keep raw browser evidence and local paths in private local records. Publish only reviewed
   compact fields. Keep existing isolation records and failure-stop behavior.

The existing `capture_pass` feedback is zero when the oracle matches but no grade is available;
capture recall is null when unmeasured. Preserve or explicitly version those meanings, and use
the attempt classification to avoid interpreting a runner failure as a measured agent defect.

**Validation:** use fake LangSmith clients and isolated temporary artifacts to test interruption
before/during/after an attempt, preflight failure, missing/duplicate rows, wrong scores, null
scores, dataset drift, delayed publication, and reconciliation. An opt-in controlled smoke run
must verify one real experiment without exposing raw evidence.

**Done when:** every planned attempt has exactly one visible state, local artifacts and remote
rows can be reconciled, and a publication is marked verified only after values are read back.

## Step 3 — Establish a live pilot

**Problem.** Passing scripted browser checks or synthetic-browser model checks does not measure
the combined model, MCP, browser, fixture, and grader path.

**Development and execution plan.**

1. Freeze the existing three cases, new grader version, model/API/reasoning settings, budgets,
   deployment image and tool schema, fixture fingerprint, and source snapshot in the manifest.
   Run the existing `--check-only` isolation preflight against the dedicated stack.
2. Run five independent serial trials per case: 15 planned attempts. Keep setup failures,
   provider failures, cancellations, and unstarted trials visible. Do not rerun failures and
   replace the first measurements without recording both attempts and the reason.
3. Review each local run's readiness, cleanup, trajectory, retained network evidence, grader
   checks, and agent findings. Verify the publication receipt from Step 2. Record failure
   categories and distinguish an agent error from an infrastructure or grading problem.
4. Publish a compact pilot report with per-case counts and provenance. Treat five repetitions
   as a setup and variability check, not a general reliability estimate. Select follow-up cases
   and thresholds from the evidence without rewriting the frozen pilot.

**Done when:** all 15 planned identities are accounted for, every started attempt has a terminal
classification or an explicitly recorded interruption, and the local/remote receipt is reconciled.
If infrastructure prevents completion, retain and report the incomplete pilot instead of
claiming a baseline.

## Step 4 — Broaden cases and make expected behavior independent

**Problem.** The three cases share one objective and are limited to parser-inserted GET scripts.
The fixture and the synthetic listing both consume the same request CSV as the grader, so a
mistake in that shared input can propagate through multiple checks.

**Development plan.**

1. Design a versioned case schema with objective, scope, allowed actions, observation window,
   fixture setup, expected events/outcomes, acceptable limitations, and scoring rules. Extend
   the oracle beyond integer HTTP statuses to represent transport failures, pending/unknown
   outcomes, and repeated events without confusing them with HTTP failures.
2. Add reviewed families for healthy controls, repeated requests, delayed/pending traffic,
   transport failures, partial/filtered listings, unexpected formats, allowed interactions,
   restricted actions, and capture-tool failure. Define readiness and time windows per case;
   the current ready heading only proves readiness for the parser-blocking fixtures.
3. Review fixture behavior against a separately specified case contract. Where meaningful,
   compare it with a fixture-side event ledger or another independently collected observation;
   avoid deriving every expected value from the same CSV used to generate traffic. Include
   controls that detect fixture/oracle drift and distinguish browser events the fixture cannot see.
4. Partition cases into development and reserved evaluation sets before tuning. Prefer splitting
   by fixture variation or scenario family rather than near-identical rows. Freeze content hashes
   and review labels; keep expected answers out of agent task inputs. A working target of 20–30
   reviewed cases is a planning aid, not a statistical guarantee.
5. Preserve `network-capture-v1` and its remote dataset identity. Publish expanded cases under a
   new version only after local fixture, oracle, and remote example read-back checks pass.

**Validation:** offline fixture/schema tests, scripted real-graph controls, deliberate oracle and
fixture mismatches, and an opt-in real-browser check for each new fixture family.

**Done when:** each case has an independently reviewed expected outcome and scope, the split is
frozen, and fixture drift or an unrepresentable outcome cannot silently produce a passing grade.

## Step 5 — Separate recovery, task outcome, and efficiency

**Problem.** Grader v2 requires exactly one successful network call and stops request scoring
otherwise. A valid later capture after a permitted failure cannot pass that pilot contract.

**Development plan.**

1. For the new dataset, specify which captures are eligible and whether final evidence is selected
   or combined. Define time windows, duplicate handling, provenance, and how conflicting or
   partial observations affect the verdict. Never merge evidence in a way that hides missing
   scope or an unknown outcome.
2. Add a versioned grader that can score a successful, budgeted recovery while recording earlier
   failed calls. Retain strict evidence, permission, scope, and budget checks. Keep the original
   single-capture pilot and historical grades comparable under their own rules.
3. Report outcome separately from retries, browser/model calls, duration, tokens, and other
   usage. Keep failed attempts in usage totals; unknown provider usage stays null. Add estimated
   cost only if a pricing source and effective date are recorded.

**Validation:** cover failed-then-successful capture, missing final evidence, duplicate events,
conflicting results, budget exhaustion, and all-failed captures. A successful recovery must pass
its outcome rule and still show its extra calls.

**Done when:** a documented recovery case can pass without weakening evidence or permission
checks, and efficiency changes are visible independently of the pass verdict.

## Step 6 — Grade the agent's conclusions

**Problem.** The existing `failure_summary_recall` measures the application's deterministic
observation summary, not the correctness of the model's final findings. A known evidence ID
alone does not prove that its content supports a claim.

**Development plan.**

1. Add reviewed expected findings and limitations for applicable cases: endpoint, observed
   outcome, evidence required, acceptable uncertainty, and claims that would be unsupported.
   Include healthy cases and cases where evidence is incomplete.
2. Define separate measurements for expected failure recall, factual claim support, false defect
   claims, and uncertainty. Use deterministic checks for structured facts where reliable. Start
   with independent human labels and an adjudication procedure for ambiguous wording; report
   which claims were assessed and why.
3. Consider an LLM judge only after a labeled set and rubric exist. Freeze its prompt, model,
   settings, and version; test agreement and false acceptance/rejection against held-out human
   labels. Preserve an `unmeasured` result when evidence or a reliable judgment is unavailable.
   Keep this score distinct from deterministic capture grading.

**Validation:** include a correct claim citing the right evidence, a correct-looking claim citing
irrelevant evidence, an invented cause, a missing known error, a false healthy-case defect, and an
appropriate uncertainty statement. Review label disagreements before using automated scores.

**Done when:** the evaluation can distinguish captured evidence from accurate agent reporting,
and its rubric and labeled examples are versioned and reproducible.

## Step 7 — Validate the graders independently

**Problem.** The existing 30 challenges were used to harden v2, so passing them is regression
evidence rather than a held-out estimate of grader quality.

**Development plan.**

1. Assemble a separate set of valid runs, legitimate variations, agent failures, and corrupted
   artifacts from controlled trials. Label them against the case contract and evidence before
   reviewers see grader verdicts; record disagreements and adjudication.
2. Freeze a held-out set by scenario family where practical. Evaluate each grader and rubric
   version without tuning on those labels. Track the intended failure reason as well as pass/fail.
3. Report valid acceptance, false rejection, corruption rejection, and false acceptance with
   counts and denominators. Keep invalid/ungradable artifacts distinct from measured errors.
   Protect source artifacts and identify derived copies in the saved-run audit, as the existing
   challenge runner does.

**Validation:** test the evaluation with deliberately over-permissive and over-strict graders;
both must be exposed by the labeled controls. Verify that an unrelated failed check cannot count
as detection of the intended defect.

**Done when:** independently reviewed labels and grader versions can be replayed, and both false
acceptance and false rejection are reported before the grader is used as a release gate.

## Step 8 — Evaluate network options and request details

**Problem.** `filter` and `all` are forwarded when advertised, but the current fixture/replay does
not evaluate their real scope semantics. Tool availability also varies between MCP deployments.
The agent now supports optional per-request details behind explicit permission, but those details
do not yet have a separate evaluation dataset or grader.

**Development plan.**

1. Record a sanitized tool-schema fingerprint in experiment provenance. Test the exact schema
   used by the pinned evaluation deployment; do not assume that the separate host server on port
   8931 has the same options. The host server observed on 2026-11-08 advertised `filter` but not
   `all` for the list tool.
2. Add controlled cases for matching/nonmatching filters, no matches, static inclusion, history
   after navigation when `all` is available, and truthful partial-scope reporting. The oracle must
   distinguish full traffic from the returned subset. Add a real-MCP opt-in test so fake
   forwarding alone cannot stand in for server behavior.
3. Create a separate `browser_network_request` detail dataset and
   versioned grader for selecting an observed request and assessing request/response headers or
   bodies. Cover wrong/stale indices, missing or unavailable parts, large/binary bodies, and
   redaction using synthetic canaries. Score only data the server actually makes available;
   keep raw payloads local under the existing retention policy.

**Done when:** scope cases demonstrate what was requested and what was returned for each tested
server schema. Detail evaluation requires a compatible server and a reviewed data/retention
contract.

## Step 9 — Compare experiments and set regression gates

**Problem.** Individual grades exist, but there is no complete comparison report or justified
release threshold for a broader benchmark.

**Development plan.**

1. Build an aggregate report from the Step 2 manifest and grades. Show planned, started,
   unstarted, completed, infrastructure-failed, invalid, and passing counts by case and overall.
   Define a started attempt as one whose trial setup began; a preflight failure leaves attempts
   unstarted. Use `passes / started` as the operational rate, including started infrastructure
   failures; if none started, report null. Show completion coverage `started / planned` beside it.
2. Report per-case outcomes and macro averages alongside any pooled request recall; 203 requests
   in `NET-003` must not hide whole-case failures. Show capture recall only when measurable,
   deterministic-summary recall separately from agent-finding recall, and unknown/null values
   separately from zero. Present a conditional agent rate only with its explicit denominator and
   the operational rate alongside it.
3. For candidate comparisons, freeze dataset, graders, fixture, deployment, budgets, and scoring;
   record intentional model/prompt/code differences. Pair by case and planned repetition and
   balance order where feasible. Show counts, differences, and uncertainty with assumptions;
   five trials on three cases do not support broad generalization or reliable tail percentiles.
4. Predeclare gates after reviewing the baseline and independent grader validation. Keep safety
   execution violations distinct from proposals rejected by policy, and use controlled external
   observation where needed to check actual execution. Zero observed violations is a target,
   not proof that all possible unsafe actions are prevented.
5. Keep cheap deterministic checks in offline CI. Any real-model, real-MCP, or remote-publication
   gate must be explicit, budgeted, isolated, and opt-in; a provider outage must remain visible.

**Validation:** use fixture manifests containing missing attempts, infrastructure failures,
partial/null metrics, uneven case sizes, and deliberate regressions. Verify denominators and
pairing by hand on small examples.

**Done when:** a comparison can be reproduced from immutable receipts, reports its missing data,
and applies thresholds declared before candidate results are inspected.

## References

- [Current dataset, execution, and grader guide](../evals/network_capture/README.md)
- [Existing evaluation findings](evaluation_enhancements.md)
- [Evaluation handbook](evaluation_handbook.md) — contains historical and proposed integration
  material; reconcile its status statements with the implemented runner as this plan lands.
- [Testing and coverage guide](browser_exploration/testing.md)
- [Browser exploration V1 contract](contracts/browser_exploration_v1.md)
