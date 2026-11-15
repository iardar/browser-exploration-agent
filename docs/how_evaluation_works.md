# How browser evaluation works

An intermediate learning handbook for the browser exploration agent.

**Implementation snapshot: November 14, 2026.** This edition covers the complete evaluation
corpus: network capture v1, request details v2 and its frozen v1 archive, capture-grader
challenges, saved-run audits, context reconstruction, and the associated test layers. Both
agent-run graders currently report grader version 2. Proposed harder cases remain planned work.

You should be comfortable reading Python dictionaries, functions, and JSON. No previous
experience building an evaluation system is assumed. By the end, you should be able to trace a
score back to its evidence, explain a failed grade, distinguish missing measurements from
failures, and design a case that tests a specific behavior.

This book develops the reasoning behind the implementation. Use the existing documents for
their complementary purposes:

| When you need… | Read… |
| --- | --- |
| Setup, credentials, dataset publication, experiment commands, or regrading commands | [Evaluation Handbook](evaluation_handbook.md) |
| Exact feedback keys and the full list of local grader checks | [Evaluation metrics](evaluation_metrics.md) |
| Capture fixture and challenge operation | [Capture corpus guide](../evals/network_capture/README.md) |
| Detail fixture, dataset publication, and compatibility probes | [Request-detail corpus guide](../evals/network_request_details/README.md) |
| Measured experiment results | [v1 review](../evals/network_request_details/baseline_review.md), [v2 baseline review](../evals/network_request_details/baseline_review_v2.md), and [saved-run baseline](quality_baseline.md) |
| Test invocation and live-test prerequisites | [Testing guide](browser_exploration/testing.md) |
| Future evaluation work | [Enhancement plan](evaluation_enhancement_plan.md) and [harder-case plan](network_request_details_harder_cases_plan.md) |

All Python examples have comments explaining every executable line. Unless identified as using
repository helpers, they are independent, standard-library teaching examples. They illustrate
one rule at a time; they do not replace the production graders. Assertions show the expected
answer. Repository examples run from the repository root in the project's Python environment.
None of the examples makes a provider, browser, or LangSmith call.

The EPUB contains the same lessons and code. Its repository references resolve to a source
index with checkout paths, since an ebook reader cannot open files in your repository. Existing
documents are referenced rather than reproduced in the ebook.

**Contents**

1. [Turn an objective into a measurable claim](#1-turn-an-objective-into-a-measurable-claim)
2. [Understand what the corpus covers](#2-understand-what-the-corpus-covers)
3. [Follow one trial through the evaluation system](#3-follow-one-trial-through-the-evaluation-system)
4. [Trace evidence before trusting a score](#4-trace-evidence-before-trusting-a-score)
5. [Learn the capture grader through a worked example](#5-learn-the-capture-grader-through-a-worked-example)
6. [Follow the detail grader from selection to citation](#6-follow-the-detail-grader-from-selection-to-citation)
7. [Interpret privacy and binary-boundary measurements](#7-interpret-privacy-and-binary-boundary-measurements)
8. [Read LangSmith feedback without losing meaning](#8-read-langsmith-feedback-without-losing-meaning)
9. [Evaluate the grader itself](#9-evaluate-the-grader-itself)
10. [Distinguish saved-run diagnostics from oracle grading](#10-distinguish-saved-run-diagnostics-from-oracle-grading)
11. [Understand the reference-context benchmark](#11-understand-the-reference-context-benchmark)
12. [Connect unit tests, integration tests, and experiments](#12-connect-unit-tests-integration-tests-and-experiments)
13. [Compare experiments and account for effort](#13-compare-experiments-and-account-for-effort)
14. [Diagnose a result and design the next case](#14-diagnose-a-result-and-design-the-next-case)

## 1. Turn an objective into a measurable claim

Consider the objective “inspect the POST response body for this request.” A convincing result
needs more than the sentence “done.” The agent must identify the right request, inspect its
response, retain the observation, and connect its reported value to that observation.

Evaluation turns that expectation into a comparison between **what should happen** and
**what the recorded run demonstrates**.

- A **case** specifies an objective, a controlled page, and execution constraints.
- An **oracle** contains the reviewed expected requests or values.
- A **fixture** produces controlled browser traffic for that case.
- A **trial** is one execution of the case under a particular configuration.
- A **grader** computes checks and measurements from the trial's artifacts and the oracle.
- An **experiment** groups trials and their feedback for analysis.
- A **baseline** is a recorded experiment chosen as the comparison point for later changes.

The agent's model is the system being evaluated. The current graders are deterministic Python
programs; there is no separate LLM judge. LangSmith runs the experiment target and collects its
outputs and evaluator feedback. The repository defines the success conditions.

```text
Reviewed case ──► task inputs ──► agent and browser ──► saved artifacts
      │                                                   │
      └──► oracle ──► fixture traffic                      │
                 └──────────────────► Python grader ◄─────┘
                                            │
                                            ▼
                                  LangSmith feedback
```

An agent status, a grade, and a runner status answer different questions. `completed` means
the agent chose to finish; `passed` means every applicable grader check passed; a completed
experiment runner means its evaluation loop finished. A wrapper can return successfully while
carrying a failing grade or `runner_error`.

```python
status = "completed"  # The agent decided to stop making decisions.
checks = {"correct_target": True, "cited_evidence": False}  # Illustrative checks.
passed = all(checks.values())  # A strict grade requires every applicable check.
assert status == "completed" and not passed  # Stopping does not prove fulfillment.
```

The real public statuses are `completed`, `step_limit`, `model_limit`, `timeout`, `cancelled`,
`blocked`, and `failed`. There is no public `partial` status. See the
[result contract](contracts/browser_exploration_v1.md) for their precise meanings.

## 2. Understand what the corpus covers

The capture corpus asks whether the agent can collect a complete request listing and preserve
important failures in its compact observation. Its three cases progress from a small healthy
page, to a small page with an HTTP error, to a 203-request page with two errors near the end.
The scale change tests whether compression loses the most useful information.

The detail corpus asks whether the agent can select and inspect a particular request. Its
22 active cases exercise text and structured response bodies, HTTP errors, Unicode, a long
body, request and response headers, POST bodies, secret redaction, binary boundaries, and
ambiguous candidates. The four ambiguity cases distinguish similar paths, GET versus POST,
different statuses at the same URL, and a request-header selector among otherwise matching
candidates. The frozen v1 archive contains the original 18 cases; the current runner uses v2.

The datasets remain separate because their success conditions differ. Capture expects one
complete listing. Details usually need a listing, an overview, and a selected part. Combining
their scores into one undifferentiated “network accuracy” would hide those distinctions.

Expected capture traffic lives in [requests.csv](../evals/network_capture/requests.csv), while
the detail payloads and markers live in [oracle.json](../evals/network_request_details/oracle.json).
The two `cases.json` files define tasks and budgets. A marker such as `json-001` is a public
fixture value that makes the expected content easy to recognize after observation. It is not
a credential.

Here is an executable inspection of the real detail dataset:

```python
from evals.network_request_details.dataset import expected_examples  # Load remote row shapes.
from evals.network_request_details.dataset import load_oracle  # Read reviewed local answers.
examples = expected_examples()  # Build inputs and reference outputs for all active cases.
case = examples["DET-001"]  # Select the JSON response-body case.
marker = load_oracle()["DET-001"]["marker"]  # Obtain its expected observed value.
assert len(examples) == 22  # Confirm the active v2 case count.
assert marker == case["outputs"]["marker"]  # The reference carries the grading value.
assert marker not in str(case["inputs"])  # The task must not reveal the answer.
```

Remote examples contain inputs and compact reference outputs. The evaluator receives the
references; the agent receives the task inputs. Complete payloads and synthetic secret values
are not published as reference outputs. An objective may reveal a selection criterion, such as
`x-eval-attempt: chosen`, because the agent needs to know which candidate the user wants. The
body marker to discover remains hidden from the task.

The fixture and grader share reviewed oracle data. Separation from model inputs prevents one
kind of answer leakage, but it does not establish independent ground truth: an erroneous
fixture/oracle pair can agree with itself. Reviewing fixtures, testing negative examples, and
eventually using independent validation cases address different parts of that risk.

The controlled pages finish generating their declared traffic before announcing readiness.
Their POST requests are fixture inputs; the detail task still prohibits agent-driven mutation
and form submission. See the [capture fixture](../evals/network_capture/fixture.py) and
[detail fixture](../evals/network_request_details/fixture.py) for the actual responses.

## 3. Follow one trial through the evaluation system

The runners first establish that the measurement environment is usable. They check the
reviewed dataset, the browser deployment, session independence, and fixture/tool compatibility.
The detail probe checks every active case without asking a model to decide what to do.

`--check-only` stops after the model-free checks. It can contact LangSmith and the local browser
deployment, so “model-free” does not mean “offline.” It neither calls the model preflight nor
starts `aevaluate`.

For a live experiment, [validate_model](../evals/model_preflight.py) makes one small structured
decision call using the selected model settings and an empty tool catalog. It requires
`done=True` and no action. HTTP 429, HTTP 401, malformed responses, and unexpected decisions
prevent `aevaluate` from being called. The prompt requests no claims, but the explicit
post-response check tests the finish flag and action, not whether every claim list is empty.

A successful preflight establishes immediate connectivity and basic structured-output support.
It does not reserve provider capacity or prove that later calls will succeed. Its usage is
recorded separately from trial usage.

Each trial opens a fresh isolated browser session. Cookies, local storage, session storage,
IndexedDB, cache storage, and previous network history must not carry over. Isolation probes
cover concurrent sessions and reopening after cancellation. A contaminated browser could make
a later trial pass using a previous trial's traffic, so this is part of measurement validity.
The runners also stop further work after isolation cleanup failures. Deployment details belong
in the [operational handbook](evaluation_handbook.md) and
[isolation implementation](../evals/network_capture/isolation.py).

During exploration, initial navigation and snapshot capture happen before the model chooses
subsequent actions. Each browser call becomes a saved observation. The model receives bounded
context assembled from observations and progress; it does not automatically receive every byte
in the evidence directory. This distinction explains why capture completeness and summary
quality are separate measurements.

After exploration, the target grades the saved directory and returns compact grade and usage
data. The LangSmith evaluator translates that local report into feedback fields. Trials run
serially with `max_concurrency=1`; repetitions are new executions. Five repetitions of 22 cases
mean 110 trials, with a new browser session for each trial.

The runners record source, dataset, fixture, grader, and model metadata to support comparisons.
They do not yet provide a complete durable ledger of every planned attempt or an automatic
read-back proof that all expected remote feedback was published. Those are documented
[enhancements](evaluation_enhancement_plan.md), not guarantees of the current runner.

## 4. Trace evidence before trusting a score

The graders read saved artifacts directly. A LangSmith CSV is useful for comparing trials,
but it cannot substitute for the local evidence when diagnosing a disputed result.

| Artifact | Question it answers |
| --- | --- |
| `run_config.json` | What task, permissions, budgets, and retention settings were used? |
| `result.json` | How did the agent finish, what did it claim, and which evidence IDs exist? |
| `trajectory.jsonl` | Which browser actions ran, in what order, with which arguments and summaries? |
| `decisions.jsonl` | What did the model propose, and which proposals or model responses failed? |
| `evidence/…` | What retained tool content supports a particular observation? |

`metrics.json` is a convenient saved usage view; the graders read metrics from `result.json`.
An evidence artifact is retained tool output subject to the application's redaction and size
limits. “Raw” does not mean an unlimited or unredacted packet capture.

The crucial join is observation → evidence ID → retained path and owner. A file containing the
right string is insufficient if it belongs to a different observation or is not the file cited
by the report.

```python
observation = {"id": "obs-0005", "evidence_ref": "evidence-obs-0005"}  # Body observation.
entry = {"id": "evidence-obs-0005", "observation_id": "obs-0005"}  # Ledger ownership.
entry.update(retained=True, path="evidence/obs-0005.json")  # Declare the retained artifact.
assert observation["evidence_ref"] == entry["id"]  # Follow the observation's evidence ID.
assert entry["observation_id"] == observation["id"]  # Check which observation owns it.
assert entry["retained"] and entry["path"]  # Require a declared, retained file.
```

The production checks go further than this example. Paths must resolve inside the authorized
run directory, including after symlink resolution. Contracts must validate, and relevant IDs,
owners, counts, and action sequences must agree. Missing or malformed artifacts cannot pass;
the public graders return `invalid_artifacts` with safe diagnostics. An unknown case ID is a
configuration error, rather than an ordinary failing trial.

Capture and detail graders do not have identical integrity checks. Capture additionally checks
timestamp ordering, model-event counts, evidence kinds, unique paths, and other ledger
consistency rules. Details checks the selection sequence, required retained observations, and
rejected proposals relevant to its boundary rules. Consult the exact check lists in the
[metric reference](evaluation_metrics.md), rather than assuming one grader covers the other's
entire integrity surface.

## 5. Learn the capture grader through a worked example

The [capture grader](../evals/network_capture/grade.py) first checks the case, objective,
fixed budgets, permissions, completion, and artifact consistency. It then requires exactly
one successful network-listing observation and linked retained evidence. If either prerequisite
fails, it returns before calculating recall. Those missing recall values mean “unmeasured.”

The complete-capture contract requires static resources to be explicitly included. The grader
accepts the supported `static` and `includeStatic` argument spellings, checks for omission
warnings, and rejects unsuccessful or unreadable listings. It parses each retained request into
method, URL, and outcome.

For oracle matching, request identity is `(method, URL path, outcome)`. The outcome is a status
string such as `"500"`. Scheme and host are checked separately against the target origin.
Queries do not distinguish oracle tuples, and repeated identical rows collapse into one tuple.
This is coverage of expected request identities, not a count of every repeated HTTP exchange.

Imagine four expected requests, three observed correctly, and one unexpected request:

```python
expected = {("GET", "/", "200"), ("GET", "/a.js", "200")}  # First two oracle tuples.
expected |= {("GET", "/b.js", "200"), ("GET", "/report.js", "500")}  # Two more.
observed = expected - {("GET", "/b.js", "200")}  # The retained listing misses one.
observed.add(("GET", "/tracking", "200"))  # It also contains traffic outside the oracle.
missing = expected - observed  # These expected identities were not retained.
unexpected = observed - expected  # These retained identities were not expected.
matched = len(expected & observed)  # Only expected identities contribute to recall.
capture_recall = matched / len(expected)  # Divide three matches by four expectations.
assert capture_recall == 0.75  # The extra request cannot compensate for the missing one.
assert len(missing) == len(unexpected) == 1  # Both differences remain diagnostic evidence.
```

This explains `expected_requests`, `matched_requests`, `missing_requests`,
`unexpected_requests`, and `capture_recall`. The production comparison exempts the known
GET `/favicon.ico` response with status 204. It also requires the separate
`no_unexpected_requests` check to pass. Recall can therefore be 1.0 while the strict grade
fails because the listing includes an unexpected request.

### A complete artifact can still produce a poor summary

The model-facing summary is checked independently of the retained listing. Its effective
`summary_limit` is the smaller of 1,000 characters and the task's `max_observation_chars`.
`raw_text_chars` and `summary_chars` show the sizes on either side of that compression.
These are character counts, not token counts.

The grader verifies the summary's total, HTTP-error, transport-failure, and unknown-outcome
counts against parsed records. Counts include repeated records even though oracle recall uses
unique identities. An HTTP error is a numeric status of at least 400; a transport failure is
represented as `FAILED`, and an incomplete outcome as `UNKNOWN`.

It also checks that all seeded oracle failures appear and that the summary invents no request
identities. A short summary need not enumerate every healthy request: accurate counts plus the
important failure rows can satisfy the contract.

```python
failures = {("GET", "/report.js", "500"), ("GET", "/missing.js", "404")}  # Oracle errors.
shown = {("GET", "/report.js", "500")}  # Only one error survives compression.
expected_failures = len(failures)  # Count the two seeded failures.
failure_summary_recall = len(failures & shown) / expected_failures  # Compute one over two.
assert failure_summary_recall == 0.5  # Half the important failures are visible.
healthy_failures = set()  # A healthy case has no failure-summary denominator.
healthy_score = None if not healthy_failures else 1.0  # Keep non-applicability explicit.
assert healthy_score is None  # A healthy case does not earn a fabricated failure score.
```

The six published capture feedback fields now have a practical interpretation:

- `capture_pass` says whether the full applicable conjunction passed.
- `oracle_consistent` checks whether the remote reference request list matches the local oracle.
- `capture_recall` measures expected request coverage in retained evidence.
- `failure_summary_recall` measures seeded failures preserved in the summary.
- `no_unexpected_requests` says whether the retained identities stay within the oracle exemption.
- `evidence_retained` exposes the local `linked_retained_evidence` check.

`evidence_retained=1` establishes that the relevant artifact is linked and retained; other checks
establish its readability and content. Likewise, `known_claim_evidence` checks that claim
citations name existing evidence, but the capture grader does not semantically judge the final
report. It can pass a run whose final wording needs improvement.

There is also a documented scope limitation: the current grader can accept a filtered listing
that happens to contain every expected fixture request. It does not prove that filtering could
not have hidden other traffic. [Step 1 of the enhancement plan](evaluation_enhancement_plan.md)
addresses this. The one-listing rule is also strict: recovery with two listing observations
does not satisfy this corpus, even if the last one is complete.

## 6. Follow the detail grader from selection to citation

The [detail grader](../evals/network_request_details/grade.py) checks a chain of evidence.
Each stage establishes the input needed by the next stage.

| Stage | What is compared |
| --- | --- |
| Listing | Retained listing rows versus the case's expected method, full URL, and status |
| Visible index | Matching request indices versus the indices present in the model-facing summary |
| Overview | A later overview's retained identity and optional request-header selector versus the chosen candidate |
| Requested part | A later part call's index, part name, retained content, and marker versus the case/oracle |
| Report | Final finding or verified behavior versus the expected marker and the supporting observation's evidence ID |

### First, establish a usable candidate

`target_listed_with_visible_index` requires a successful listing with the expected identity
and visible request index. A correct index buried only in a retained artifact is insufficient:
the model needs the index in its summary to act on it.

Ordinary cases need one matching candidate. The request-header ambiguity case intentionally
needs multiple candidates with the same method, URL, and status. Matching indices must be
unique. The grader uses the last qualifying listing in the recorded sequence; it does not
treat an arbitrary number remembered from an earlier run as a valid selection.

For example, DET-020 asks for POST at a URL that also has a GET request. This teaching example
shows why matching only the path is insufficient:

```python
requests = [{"index": 3, "method": "GET", "path": "/target", "status": 200}]  # Decoy.
requests += [{"index": 4, "method": "POST", "path": "/target", "status": 200}]  # Target.
matches = [r for r in requests if (r["method"], r["path"]) == ("POST", "/target")]  # Select.
assert len(matches) == 1  # The method disambiguates the shared path in this example.
assert matches[0]["index"] == 4  # Use the index actually observed for that candidate.
```

Production matching also includes the full expected URL and status. DET-019 catches a similar
path such as a preview endpoint; DET-021 distinguishes different statuses at the same URL;
DET-022 requires the named request header to distinguish otherwise matching candidates.

### Then, verify the overview before inspecting a part

`verified_overview` requires a later successful detail call with exactly the index argument.
Its retained overview must identify the matching request. For DET-022, the selector must appear
in the **request headers** section. Finding the same words in response headers does not count.
Header names are compared without case sensitivity; selector values must match exactly.

`no_wrong_request` checks all recorded detail actions. Ordinary cases cannot inspect a decoy
index. The selector case permits overview calls for eligible candidates so the agent can
discover which one has the requested header. Once it reads a part, that call must use the
selected candidate. Exploring a decoy's body is not equivalent to inspecting its overview.

`correct_part_or_boundary` then checks the case's requested part and its retained marker, or
the binary-stop rule explained in the next chapter. A normal body case requires a successful
part call after the verified overview, with the chosen index and exactly the requested part.
The marker must occur in retained part text with its original case.

Header cases have a useful exception. If the correct header and value are already present in
the verified overview's correct header section, that overview can satisfy the request. The
grader does not demand a redundant header-only call. The citation should then point to the
overview evidence.

### Finally, connect the answer to its source

`evidence_backed_report` searches final `verified_behaviors` and `findings`. A qualifying claim
must contain the expected marker and cite the same observation that supplied the content.
This small example illustrates the essential linkage:

```python
import json  # Match the grader's approach of searching a serialized claim.
marker = "json-001"  # This value should have been observed in the selected body.
evidence_id = "evidence-obs-0005"  # This is the body observation's evidence identifier.
claim = {"description": "Observed json-001", "evidence_ids": [evidence_id]}  # Final claim.
has_marker = marker.casefold() in json.dumps(claim).casefold()  # Search without case sensitivity.
cites_source = evidence_id in claim["evidence_ids"]  # Require the supporting observation.
assert has_marker and cites_source  # Both the value and its provenance are necessary.
claim["evidence_ids"] = ["evidence-obs-0003"]  # Replace the body citation with the listing.
assert evidence_id not in claim["evidence_ids"]  # The same sentence now lacks its source.
```

This is a deterministic string-and-citation rule, not a language-understanding judge. It does
not detect every unsupported extra statement or understand negation. A sentence saying the
marker was “not found” can still contain the marker text. The check searches the serialized
claim, not exclusively its description. Treat passing as evidence for this specific contract,
not a guarantee that the whole explanation is accurate.

## 7. Interpret privacy and binary-boundary measurements

The detail corpus contains two canary cases with known synthetic secrets. `secret_redacted`
searches every saved JSON and JSONL file under the run directory for the literal canary string.
The case passes that check only if the string is absent everywhere searched. Cases without a
secret have no applicable redaction check; their published value is null.

```python
canary = "synthetic-canary"  # A known test secret, never a real credential.
saved_texts = ["Authorization: [redacted]", "public-marker"]  # Illustrative saved content.
secret_redacted = all(canary not in text for text in saved_texts)  # Inspect every artifact.
assert secret_redacted  # The secret is absent, while useful public information remains.
saved_texts.append("debug: synthetic-canary")  # Simulate a leak in another saved record.
assert not all(canary not in text for text in saved_texts)  # One leak fails the check.
```

This verifies absence of the known literal secret in the inspected artifacts. It does not
prove that every possible secret, transformed encoding, console output, or external trace is
safe. The accompanying public marker and evidence checks ensure that redaction did not simply
erase all useful content and produce a meaningless success.

For the two binary cases, successful behavior is to inspect the overview, recognize the
expected MIME type, avoid reading the response body, and report the boundary with its evidence.
`correct_part_or_boundary` requires both no executed response-body call and no rejected
response-body proposal in `decisions.jsonl`. A model that tries the forbidden read and is
blocked by runtime policy fails this behavioral check.

```python
executed_parts = []  # The browser performed no response-body read.
rejected_parts = ["response-body"]  # The model nevertheless proposed one and was blocked.
attempted = "response-body" in executed_parts + rejected_parts  # Inspect both histories.
assert attempted  # Runtime prevention does not erase the model's attempted action.
```

The binary report must cite the overview and mention binary content or the expected MIME type.
An `image/png` boundary is different from an SVG text case, which the corpus expects the agent
to inspect as text. The reviewed oracle defines each case's expected behavior.

The long-text case tests a different boundary: retention and model-context limits. Its marker
is near the start of an approximately 80,000-character body. Passing shows that the marker
survives bounded retention and summarization. It does not demonstrate that the agent can find
an arbitrary value beyond the retained prefix. The [harder-case plan](network_request_details_harder_cases_plan.md)
describes additional challenges without treating them as existing coverage.

## 8. Read LangSmith feedback without losing meaning

The detail adapter publishes eight keys: `detail_pass`, `oracle_consistent`,
`target_listed_with_visible_index`, `verified_overview`, `correct_part_or_boundary`,
`evidence_backed_report`, `no_wrong_request`, and `secret_redacted`. The preceding chapters
explain the behavioral checks; `detail_pass` additionally includes configuration, completion,
budget, retention, action consistency, and evidence checks.

The [capture adapter](../evals/network_capture/langsmith_agent_eval.py) and
[detail adapter](../evals/network_request_details/langsmith_eval.py) publish compact local grades.
They do not rerun the browser or ask a second model for a score. Local booleans become 1.0 or
0.0, while unavailable or inapplicable measurements can become null.

`oracle_consistent` compares the remote reference with the expected local reference. Capture
compares the request list; details compares the complete expected output dictionary. Dataset
validation before execution also checks task inputs and source metadata. A match means the
two references agree, not that the oracle itself has been independently proven correct.

When references disagree, both adapters suppress the other quality scores. A null under those
conditions means “not comparable,” not “the agent failed this behavior.” Here is the actual
detail feedback function operating entirely offline:

```python
from evals.network_request_details.dataset import expected_examples  # Build canonical rows.
from evals.network_request_details.langsmith_eval import score_detail  # Use the real adapter.
example = expected_examples()["DET-001"]  # Choose a case with a known reference shape.
output = {"grade": {"passed": True, "checks": {}}}  # Supply a minimal illustrative grade.
feedback = score_detail(example["inputs"], output, {})  # Deliberately pass a wrong reference.
scores = {row["key"]: row["score"] for row in feedback["results"]}  # Index feedback by key.
assert scores["oracle_consistent"] == 0.0  # The reference mismatch remains visible.
assert scores["detail_pass"] is None  # The apparent local pass is not comparable.
feedback = score_detail(example["inputs"], {}, example["outputs"])  # Now omit the grade.
scores = {row["key"]: row["score"] for row in feedback["results"]}  # Read the second result.
assert scores["detail_pass"] is None  # A missing detail grade is also unmeasured.
```

There is an implementation difference worth preserving in analysis: with a matching oracle,
a missing capture grade maps `capture_pass` to 0.0; a missing detail grade maps `detail_pass`
to null. Do not assume both families have identical missing-data behavior.

Even within one family, null can mean different things: a healthy case has no failure-summary
denominator; a non-canary case has no redaction measurement; a prerequisite failure can leave
later checks unevaluated. Inspect the local `verdict`, `failed_checks`, `runner_error`, and
reference status before assigning a reason.

```python
scores = [1.0, 0.0, None]  # One measured pass, one measured failure, one missing score.
measured = [score for score in scores if score is not None]  # Keep actual zeroes.
pass_rate = sum(measured) / len(measured)  # Average only the two measured trials.
missing = len(scores) - len(measured)  # Report the unmeasured trial separately.
assert pass_rate == 0.5 and missing == 1  # This is not three complete measurements.
```

Do not use `if score` to select measured scores: it removes valid failures with value zero.
Always report the eligible, measured, failed, and missing counts alongside an average.

## 9. Evaluate the grader itself

A grader that accepts every run gives impressive agent scores and measures almost nothing.
A grader that rejects everything catches corruptions but also rejects correct behavior. Both
positive and negative controls are necessary.

The [capture challenge corpus](../evals/network_capture/grader_cases.json) has 30 scenarios:
seven valid controls and 23 deliberate corruptions. It operates on copies of passing saved
runs. It does not execute the agent or open a browser.

Valid variants include changes that should not alter the verdict, such as reordering listing
rows, supported listing formatting, a permitted favicon request, or a repeated request row.
Negative variants deliberately break the objective, retention, evidence ownership, summary
counts, expected requests, timestamps, or other requirements. These controls test the grader's
rules directly instead of hoping a model happens to produce the desired mistake.

Each scenario records `expected_pass`. A negative scenario also names an `expected_check`.
The [benchmark implementation](../evals/network_capture/challenges.py) compares both the verdict
and the diagnostic check:

```python
expected_pass = False  # This challenge deliberately corrupts a retained artifact.
actual_pass = False  # The grader correctly refuses to pass the corrupted run.
expected_check = "linked_retained_evidence"  # This is the defect the challenge introduces.
failed_checks = ["correct_objective"]  # Imagine the grader failed for an unrelated reason.
label_matches = actual_pass == expected_pass  # The pass/fail label happens to be right.
expected_check_detected = expected_check in failed_checks  # Check the intended diagnosis.
challenge_correct = label_matches and expected_check_detected  # A useful derived value.
assert label_matches and not challenge_correct  # Rejection alone is insufficient evidence.
```

`label_matches` and `expected_check_detected` are stored per scenario. `challenge_correct` in
this example is a derived teaching variable, not an emitted report field. Positive controls
automatically satisfy the expected-check condition because no failing check is required.

### Read both sides of the confusion matrix

Suppose six of seven positive controls pass and 22 of 23 corruptions fail. The following
numbers are illustrative; they are not a newly measured benchmark result:

```python
positive_controls, negative_challenges = 7, 23  # Separate the two reviewed label groups.
valid_accepted, corruptions_rejected = 6, 22  # Hypothetical observed grader decisions.
valid_acceptance_rate = valid_accepted / positive_controls  # Six sevenths, about 85.7%.
corruption_rejection_recall = corruptions_rejected / negative_challenges  # About 95.7%.
false_acceptances = negative_challenges - corruptions_rejected  # Count bad runs that passed.
false_acceptance_rate = false_acceptances / negative_challenges  # Measure their fraction.
assert round(valid_acceptance_rate, 3) == 0.857  # One valid case was incorrectly rejected.
assert round(corruption_rejection_recall, 3) == 0.957  # Most corruptions were detected.
assert round(false_acceptance_rate, 3) == 0.043  # One bad case slipped through.
```

The positive-acceptance rate prevents an always-rejecting grader from looking reliable.
The negative-rejection recall measures detection of known corruptions. False acceptance is
the complementary error rate over the negative group, not over all scenarios.

`controls_unchanged` compares source fingerprints before and after the benchmark, so mutation
of the original run cannot silently masquerade as a copied challenge.
`all_expectations_met` requires unchanged originals, at least one scenario, matching labels,
and the intended failed check for every negative scenario.

Always read `scenario_count`, `positive_controls`, and `negative_challenges`. Scenarios whose
base case was not supplied are skipped. A benchmark built from one control does not exercise
all 30 challenges. A missing label group yields null for its rate, rather than a perfect score.

Challenge copies carry an `evaluation_manifest.json` identifying derived artifacts. The saved-run
auditor excludes those directories from agent statistics. Thirty mutated copies of one trial
are thirty grader tests, not thirty independent agent executions.

The challenges helped develop the grader and are not a held-out validation set. Passing them
demonstrates the reviewed checks, not correctness against every imaginable malformed run.
The detail grader has focused corruption tests as well, but no equivalent standalone
30-scenario benchmark. See the [challenge tests](../tests/unit/browser_exploration/test_grader_challenges.py)
and [detail corpus tests](../tests/unit/browser_exploration/test_network_detail_eval_dataset.py).

## 10. Distinguish saved-run diagnostics from oracle grading

[evals/run_quality.py](../evals/run_quality.py) audits a directory of saved runs without a model
or browser. It can inspect ordinary exploratory runs that have no reviewed fixture oracle.
Its measurements describe available evidence and recorded behavior; they cannot establish
that all expected real-world requests were captured.

`run_count` counts usable, non-derived result records. `statuses` and `error_codes` show their
outcomes. `incomplete_runs` lists trajectory directories without a result. `warnings` identifies
unreadable or invalid artifacts. The auditor preserves valid JSONL records around a damaged
line, so event totals may be lower than the true counts when warnings are present.

`excluded_evaluation_directories` and `excluded_evaluation_results` explain which derived
challenge artifacts were removed. If a scan finds ten original results and thirty marked
challenge copies, the intended agent run count is ten, not forty.

### Capture presence and scope

The audit's `network` cohort selects runs whose configured adapter is `OpenAIDecisionModel`
and whose objective contains the word `network` or the historical typo `nework`. This is a
practical heuristic. It is not proof that every selected run used a real browser or that every
network-related task was included.

Within that cohort:

- `explicit_browser_goals` counts the selected runs.
- `provider_blocked` counts runs containing `llm_api_error`.
- `execution_eligible` is the selected count minus that provider-error count.
- `with_readable_capture` counts runs with a successful listing observation and readable retained
  JSON. It does not require every listing line to parse or match an oracle.
- `scope_includes_static` and `scope_excludes_static` count captured runs with an explicit true
  or false scope flag, respectively. A run with multiple captures can count in both groups.

These sets can overlap in other ways. A run can capture traffic and then encounter a provider
error. Therefore, blindly dividing `with_readable_capture` by `execution_eligible` can mix
incompatible numerator and denominator populations. If deriving a rate, form the same eligible
subset for both and state the rule.

```python
runs = [{"provider_error": False, "captured": True}]  # One usable captured run.
runs += [{"provider_error": True, "captured": True}]  # A capture followed by provider failure.
eligible = [run for run in runs if not run["provider_error"]]  # Choose a consistent cohort.
captured_eligible = sum(run["captured"] for run in eligible)  # Count captures within it.
rate = captured_eligible / len(eligible) if eligible else None  # Avoid a zero denominator.
assert rate == 1.0  # Do not divide both captures by the single eligible run.
```

### Recorded failures and action diagnostics

Per-capture diagnostics include `parsed_requests`, `unparsed_lines`, `failed_requests`,
`raw_text_chars`, and `summary_chars`. `failed_requests` counts parsed HTTP statuses of at
least 400 and transport `FAILED` records. `distinct_failures` deduplicates method, full URL,
and outcome within that observation; `visible_distinct_failures` counts those also present in
its summary.

The aggregate `distinct_recorded_failures` and `visible_recorded_failures` sum those per-capture
counts across runs. They do not deduplicate globally. Two observations of the same failing
request can contribute twice. Unlike `failure_summary_recall` in the capture corpus, these
diagnostics have no independent list of expected failures: missing raw failures are invisible
to the audit.

For action diagnostics, `rejected_actions` counts recorded rejections and `rejection_reasons`
groups their messages. `reference_action_proposals` counts model decisions whose action
arguments contain `ref` or `target`. `reference_rejections` recognizes rejections whose reason
contains the current-snapshot reference message. Their ratio is `reference_rejection_rate`.
For example, two such rejections out of eight reference-bearing proposals gives 0.25. This
denominator is not all browser calls, and the numerator depends on the logged reason wording.

`schema_errors` counts `llm_validation_error` model events per run. Counts of findings,
verified behaviors, and unresolved questions describe the saved report, not the truth of its
claims. `completed_with_unresolved_questions` counts finished runs that still report uncertainty;
uncertainty alone is not evidence that the run malfunctioned.

The [saved-run quality baseline](quality_baseline.md) supplies historical measurements and
reproduction commands. Keep that dated record separate from a new scan of a different directory.

## 11. Understand the reference-context benchmark

The saved-run audit also reconstructs what context would look like under two reference
selection policies. It addresses a specific usability problem: an old accessibility reference
can remain visible after a new snapshot makes it stale.

For each successful snapshot with readable raw evidence, `benchmark_reference_context` extracts
the active reference tokens. It rebuilds the same checkpoint twice: once without current-reference
filtering and once with the current references and their owning snapshot ID. Findings and
questions are omitted in both reconstructions. These are controlled comparisons, not archived
outgoing provider requests and not reruns of the model.

The benchmark counts reference **occurrences** in selected recent-observation excerpts:

- `legacy_current_refs` and `filtered_current_refs` count tokens allowed by the current snapshot.
- `legacy_historical_refs` and `filtered_historical_refs` count tokens outside that allowed set.
- `legacy_owner_refs` and `filtered_owner_refs` count allowed tokens in the active snapshot's own
  excerpt, which prevents old excerpts from inflating the useful-retention measurement.

`legacy_current_reference_precision` and `filtered_current_reference_precision` divide current
occurrences by all counted occurrences for their respective policy. `current_reference_retention`
divides filtered owner occurrences by legacy owner occurrences. Both precision and retention are
needed: removing all references would remove stale ones but leave the model unable to act.

```python
legacy_current, legacy_historical = 6, 4  # Ten visible tokens, four stale.
filtered_current, filtered_historical = 5, 0  # Filtering removes every stale token.
legacy_owner, filtered_owner = 5, 5  # All five tokens in the active excerpt survive.
legacy_precision = legacy_current / (legacy_current + legacy_historical)  # Six tenths.
filtered_precision = filtered_current / (filtered_current + filtered_historical)  # All current.
retention = filtered_owner / legacy_owner  # Compare useful tokens from the same owner.
assert legacy_precision == 0.6 and filtered_precision == 1.0  # Visibility is cleaner.
assert retention == 1.0  # Useful references are preserved despite fewer total current tokens.
```

The current count can fall without losing owner references because an older excerpt may repeat
a token also present in the new snapshot. These metrics count occurrences, including repeated
occurrences across reconstructed checkpoints; they do not count unique DOM elements.

`checkpoints` counts compared snapshots, and `affected_checkpoints` counts those with historical
tokens before filtering. `checkpoints_without_raw_evidence` and
`checkpoints_exceeding_mandatory_budget` record two reasons comparison could not be performed.
Snapshots with no extracted references do not contribute a comparison. Zero-denominator
precision or retention is null.

`legacy_message_chars` and `filtered_message_chars` sum the rebuilt message lengths. They measure
text size, not provider token use or cost. A better reconstructed precision score supports a
claim about context selection; it does not by itself prove fewer live model mistakes. The
[reference-context tests](../tests/unit/browser_exploration/test_reference_context_quality.py)
check these counting and ownership rules.

## 12. Connect unit tests, integration tests, and experiments

Different test layers answer different questions. A green unit suite is necessary evidence
about the implementation, but it does not measure whether a real model consistently chooses
the right browser action.

| Layer | What it can establish |
| --- | --- |
| Unit tests with injected model and MCP fakes | Contracts, parsing, permission rules, graph behavior, artifact handling, and grader edge cases without network calls |
| Graph replay with scripted decisions | Whether a known sequence passes through the production graph and produces gradable artifacts |
| Opt-in integration tests with a local MCP server | Compatibility and browser/session behavior against a controlled live deployment |
| Real-model component tests with synthetic MCP | Model decisions under controlled tool responses and strict provider-call budgets |
| Live dataset experiment | Model decisions plus real browser behavior, fixture traffic, persisted evidence, and oracle grading |
| Grader challenges | Whether known valid and corrupted artifacts receive the expected grade and diagnosis |

The [real-model capture tests](../tests/live_llm/test_network_capture.py) and
[metric-focused tests](../tests/live_llm/test_network_capture_metrics.py) use a real model with
a synthetic browser. They require explicit opt-in and serial execution. Their fake tool
responses control the traffic precisely, but cannot prove real MCP filtering or deployment
compatibility.

A test name containing “tool selection precision” does not imply that a numeric metric with
that name is published. The current component tests assert concrete conditions: a network
tool is selected with static traffic included, expected requests are retained, and the grade
passes. Other tests check a known HTTP failure in the final cited report and long-context
summary limits. Those assertions are useful additional evidence, but they are not extra
LangSmith feedback columns.

### Run an actual graph-and-grader example offline

The following standalone example uses repository test helpers. It executes the production
graph with scripted model decisions and a synthetic MCP session, writes temporary artifacts,
and invokes the real detail grader. Navigation and snapshot observations are produced by the
graph before the three scripted browser actions, so the response body is observation five.

```python
import asyncio  # Execute the explorer's asynchronous graph.
from pathlib import Path  # Supply the artifact directory using the expected type.
from tempfile import TemporaryDirectory  # Clean up generated example artifacts afterward.
from evals.network_request_details.grade import grade_run  # Import the real offline grader.
from browser_exploration.contracts import VerifiedBehavior  # Build a validated final claim.
from browser_exploration.model import Action, Decision, DecisionResponse  # Build model replies.
from tests.support.network_request_details import run_detail_replay  # Use the synthetic browser.
from tests.unit.browser_exploration.fakes import FakeModel  # Supply decisions without an LLM.

async def exercise() -> None:  # Keep asynchronous exploration inside one runnable function.
    actions = [  # Arrange the exact three browser decisions needed for DET-001.
        ("browser_network_requests", {"static": True}),  # Discover the target request index.
        ("browser_network_request", {"index": 3}),  # Verify the selected request's overview.
        ("browser_network_request", {"index": 3, "part": "response-body"}),  # Read the body.
    ]  # Navigation and snapshot are supplied separately by the real graph.
    replies = []  # Collect the scripted model responses in execution order.
    for name, arguments in actions:  # Convert each action specification into a model reply.
        action = Action(name=name, arguments=arguments)  # Validate the action shape.
        replies.append(DecisionResponse(Decision(action=action)))  # Queue that decision.
    text = "Observed json-001"  # Report the marker supplied by the fixture response.
    citation = ["evidence-obs-0005"]  # Cite the response-body observation's retained evidence.
    claim = VerifiedBehavior(description=text, evidence_ids=citation)  # Validate the claim.
    finish = Decision(done=True, verified_behaviors=[claim])  # Finish with the cited answer.
    replies.append(DecisionResponse(finish))  # Queue completion after the browser decisions.
    with TemporaryDirectory() as temporary:  # Store this demonstration outside tracked files.
        model = FakeModel(replies)  # Arrange a model that returns only the scripted replies.
        result, directory = await run_detail_replay(Path(temporary), "DET-001", model)  # Act.
        report = grade_run("DET-001", directory)  # Compare saved artifacts with the oracle.
        assert result.status == "completed"  # Assert that the graph reached model completion.
        assert report["passed"] is True, report  # Separately assert objective evidence passes.

asyncio.run(exercise())  # Run the offline example and propagate any failed assertion.
```

This example deliberately scripts the correct answer because it tests graph and grader
plumbing, not the intelligence of a model. The detail replay helper also rejects some incorrect
selections itself, so its success alone cannot establish live selection reliability. Real
experiments supply the missing model/browser evidence.

The [detail corpus tests](../tests/unit/browser_exploration/test_network_detail_eval_dataset.py)
add useful negative examples: wrong indices and parts, a status mismatch, a selector in the
wrong header section, an uncited marker, a secret leak, an escaped evidence path, and a rejected
binary-body proposal. [Preflight tests](../tests/unit/test_model_preflight.py) and the
[capture runner tests](../tests/unit/test_langsmith_agent_eval.py) and
[detail runner tests](../tests/unit/test_network_detail_langsmith_eval.py) verify successful
validation, provider failures, unexpected output, check-only bypass, and prevention of
`aevaluate` after a failed preflight.

Use the [testing guide](browser_exploration/testing.md) for the supported test commands.
Copying a live experiment command into a unit test would change the property being tested and
make the unit suite depend on external capacity and credentials.

## 13. Compare experiments and account for effort

A comparison needs the same measurement definition. Record dataset and oracle versions,
fixture and source snapshots, grader version, model/API/reasoning settings, task budgets,
permissions, and repetition counts. An experiment name is a useful label, but its metadata and
artifacts establish what actually ran.

Changing the grader while holding the saved trials fixed answers “how did our measurement
change?” Re-running the agent after an implementation change answers a different question.
If you regrade old artifacts, retain both grade versions and identify the common grading rule
used for any before/after comparison.

### Choose the denominator to match the question

For request coverage across cases, a weighted recall gives each expected request equal weight.
An unweighted mean gives each case equal weight. They can differ substantially:

```python
matched = [3, 203]  # A small case misses one request; the large case captures all requests.
expected = [4, 203]  # These are their respective oracle sizes.
case_recalls = [m / n for m, n in zip(matched, expected)]  # Compute each case's recall.
macro_recall = sum(case_recalls) / len(case_recalls)  # Give both cases equal weight.
micro_recall = sum(matched) / sum(expected)  # Give all 207 expected requests equal weight.
assert macro_recall == 0.875  # The small case strongly affects the average case score.
assert round(micro_recall, 3) == 0.995  # The large case dominates request-level coverage.
```

Neither aggregation should silently replace the other. Report which question the statistic
answers, and keep per-case results visible. For detail cases, grouping by headers, bodies,
redaction, binary boundaries, and ambiguity can expose a weak capability hidden by an overall
pass rate. Conditional metrics such as `secret_redacted` must use only applicable cases.

Repetitions measure variation on fixed cases. Five successes on one case are stronger evidence
than one success, but do not prove universal reliability. They also do not add five distinct
problem types. Fresh browser sessions remove state carryover; shared fixtures, prompts, and
provider conditions still limit what can be generalized.

The [recorded v2 baseline review](../evals/network_request_details/baseline_review_v2.md)
documents the actual five-repetition Luna experiment. Use that source for measured counts and
usage, rather than treating illustrative numbers in this book as new results. Harder-case
expansion is described separately in the [development plan](network_request_details_harder_cases_plan.md).

### Separate correctness, availability, and cost

`duration_seconds`, `browser_tool_calls`, and `llm_calls` measure effort. Browser usage can also
be split into `successful_browser_tool_calls` and `failed_browser_tool_calls`; the capture
runner includes those splits in its compact execution output. A rejected proposal is recorded
in the decision log and is not the same event as an executed browser call that failed.
One browser action does not imply one provider call: the graph performs initial browser work,
and a model can finish without proposing another action.

`input_tokens` and `output_tokens` use reported provider usage. Absent or zero usage does not
establish that execution was free. Request dollar cost additionally needs the correct model
price schedule and any separate pricing categories, such as cached input. The current token
fields alone do not provide that complete accounting.

```python
trial_input = [12000, 14000]  # Illustrative input usage for two trials.
trial_output = [400, 500]  # Illustrative output usage for those same trials.
preflight_tokens = 200  # Provider validation is separate from trial-level usage.
trial_tokens = sum(trial_input) + sum(trial_output)  # Sum the reported trial usage.
known_total = trial_tokens + preflight_tokens  # Include known setup usage when budgeting.
assert known_total == 27100  # This is a token count, not a dollar price.
```

Trial duration is not total experiment wall time: deployment probes, model preflight, teardown,
and publication add work outside a single exploration. Report missing usage and failed attempts
alongside averages. A fast failed trial should not make successful task execution appear faster.

The capture grader labels certain failed runs with `failure_category="infrastructure"` when a
provider/MCP error prevents the required capture. Otherwise it can report evaluation or artifact
failure. Detail grading does not expose the same category scheme. Use each runner's errors and
local artifacts to keep environment failure distinct from an observed wrong decision.

## 14. Diagnose a result and design the next case

When a trial looks surprising, trace the earliest broken link rather than starting from the
overall average. This order keeps the investigation tied to observable evidence:

1. Check reference consistency and whether the target produced a local grade. A missing grade
   or mismatched oracle cannot establish a behavioral result.
2. Read the run configuration and local verdict. Confirm case, model settings, budgets,
   permissions, retention, and completion conditions.
3. Follow the action sequence in the trajectory. For details, locate the qualifying listing,
   selected overview, and requested part or boundary stop.
4. Resolve each relevant evidence ID to its retained artifact. Confirm that the value appears
   in the expected request and header/body section.
5. Inspect decisions for rejected proposals and provider errors. Successful prevention may
   coexist with a failing behavioral boundary check.
6. Inspect the final claim and its citations. Only after that should you interpret the
   aggregate score or compare usage with another trial.

The [operational handbook's diagnosis guidance](evaluation_handbook.md) supplies commands and
common failure categories. The [metric reference](evaluation_metrics.md) maps individual check
names to their exact conditions.

### Design a case around a falsifiable expectation

A useful new case has a clear reason to exist. “More network traffic” is vague; “the intended
POST is followed by a newer GET at the same URL, and the agent must still select POST” identifies
the mistake being tested.

Start with the desired observation and the plausible wrong action. Define which part of the
fixture creates the distinction, which oracle field records it, and which artifact proves that
the agent noticed it. Keep answer markers out of task inputs. Specify acceptable alternative
behavior, such as using a header already present in the overview, before grading a model's
output. Otherwise a grader can accidentally reward one tool sequence instead of the objective.

Then create a passing scripted replay and a targeted corruption. The positive replay proves
the case can pass under the intended contract. The negative example proves the grader detects
the intended mistake. A live browser probe checks fixture and tool compatibility; a real-model
experiment tests whether the model finds the solution. Each step supplies different evidence.

If a new case changes what old scores mean, version the dataset or grader and preserve the
earlier source. The active v2 corpus and frozen v1 archive illustrate this practice. The current
runner does not provide a general archive-version selector; that proposed capability should
not be assumed from the presence of archived JSON files.

### Check your understanding

**A capture has recall 1.0, but includes an unexpected same-origin request. Can it pass?**
No. `no_unexpected_requests` fails even though every expected identity was found.

**A detail answer reports the correct marker but cites only the listing. Does that prove the
answer came from the body?** No. `evidence_backed_report` requires the evidence that supplied
the marker. Header-overview cases are a deliberate exception because their overview contains
the requested value.

**A PNG response-body proposal was blocked. Was the runtime boundary effective, and did the
model satisfy the binary case?** The runtime prevented the read. The model still fails the
case's no-body-attempt condition because the rejected proposal is recorded.

**A healthy capture has null failure-summary recall. Should it be changed to 1.0?** No. There
were no seeded failures to measure. Preserve null and the applicable-case count.

**All negative grader challenges were rejected, but the wrong check caused one rejection.
Did every challenge meet its expectation?** No. The expected failing check matters in addition
to the pass/fail label, and positive controls must also be accepted.

**Filtered reference precision improves to 1.0. Has live model success improved?** That result
proves the reconstructed context contains only currently allowed reference tokens under the
benchmark's counting rule. Live decision quality requires separate measurements.

Use these distinctions when reading a green dashboard: identify the comparison, locate its
supporting evidence, and state the behavior the measurement actually establishes.
