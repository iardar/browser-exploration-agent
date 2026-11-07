# Evaluation Handbook

Browser Exploration Agent | Implementation and experiment guide

Snapshot: October 26, 2026. Python 3.12. Network dataset `network-capture-v1`.
Grader version `2`. Challenge corpus `network-grader-challenges-v1`.

This handbook explains what the current evaluation framework measures, how the code produces
and checks evidence, and how to extend it. The special **Next Step** section compares three
LangSmith integration paths and records the selected path.

**Status of examples:** commands and APIs in Sections 1-13 describe the current repository.
Examples in **Next Step** are proposed integration designs, not an installed evaluation runner.
On October 28, 2026, a native LangSmith dataset named
`browser-network-capture-native-v1` was created with three reviewed cases. Its dataset ID is
`2ed1a341-fd4d-4b95-b123-09bf46d29420`; source content SHA-256 is
`5349f1a0a6a6471999601e8b9f2f5a255007c7091a985ab089cd77d74987cd18`. It contains task inputs
and expected request outputs only; no agent run results, scores, or grader evaluation have been
uploaded. Code is Python unless another language is indicated. Repository paths are relative to
the project root.

# 1. What a successful evaluation means

The agent's network task has four observable obligations: call the network tool with the
required scope, retain the returned listing, link it to the run's evidence, and preserve the
important outcomes in a short summary. A final message saying "captured the logs" does not
establish any of these facts by itself.

The application returns a `BrowserExplorationResult`. Its `completed` status means that the
model chose to finish. The evaluation layer separately decides whether the saved evidence
satisfies the task. A completed run can fail evaluation because it omitted static resources,
lost a failing endpoint, exceeded a budget, or produced inconsistent artifacts.

The current evaluation has a deliberately small scope. It measures network capture on three
controlled pages. It also tests the grader with deliberately corrupted copies of saved runs.
It does not yet establish broad browser-agent reliability, semantic correctness of every
finding, or successful operation across arbitrary websites.

| Term | Meaning in this repository |
| --- | --- |
| Case | A reviewed task and fixture condition, such as NET-003 |
| Oracle | The independent list of expected requests in requests.csv |
| Run | One invocation of the application with its own saved artifacts |
| Grade | The deterministic verdict computed from those artifacts |
| Challenge | A labeled valid or corrupted artifact copy used to test the grader |
| Experiment | A planned set of runs under one configuration; LangSmith publication is proposed |
| Infrastructure failure | A provider/MCP failure that can prevent measurement; it remains visible |

Read Sections 2-7 to understand execution and scoring, Sections 8-10 to reproduce the current
results, and Sections 11-13 to extend and diagnose the framework. The **Next Step** section
maps these existing parts into LangSmith without changing the public browser contract.

# 2. The four evaluation layers

Each layer answers a different question. The results should retain their layer label so that
a successful fake-browser test is never presented as a successful live-browser experiment.

| Layer | Model and browser | What it establishes | Current entry point |
| --- | --- | --- | --- |
| Offline graph regressions | Scripted model and synthetic MCP | Graph, policy, summary, retention and grader behavior | tests/unit/ |
| Grader challenges | Existing artifact copies; no execution | Whether the grader accepts valid data and rejects known corruptions | evals.network_capture.challenges |
| Real-model component trials | Real model and synthetic MCP | Tool selection, evidence grounding, capture recall, and bounded summaries | tests/live_llm/ --live-llm |
| Controlled browser trials | Real model and real MCP/browser | Actual collection against a known traffic oracle | CLI plus fixture plus offline grade |

Unit fixtures block network connections and DNS. The fake MCP session returns reviewed traffic
without starting a browser. This makes ordinary regressions cheap, repeatable and independent
of provider availability. The challenge runner is even narrower: it copies and edits files,
then calls the grader.

Real-model component tests use the same graph and request oracle, but substitute the real
model adapter for a scripted decision list. They are useful before a full browser trial because
they isolate the model's choice to request static resources and finish. They cannot establish
that a browser actually observed the traffic.

Controlled browser trials require an independently started MCP server, a reachable fixture,
fresh browser state and external network controls. Configuration flags record these operating
assumptions; they do not install network isolation. Keep live execution explicit and bounded.

```flow
Reviewed case and oracle | The task contains the objective; expected request outcomes stay separate.
Selected execution layer | Fake graph, real-model replay, or real browser trial.
Saved application artifacts | Result, config, trajectory, decisions, metrics and retained evidence.
Offline grader | Reads files, validates contracts and compares capture with the oracle.
Experiment record | Stores verdicts, denominators, configuration and provenance.
```

# 3. Where the evaluation lives

Evaluation tooling belongs outside the installed browser capability. The application boundary
remains `BrowserExplorer.explore(task) -> BrowserExplorationResult`; the concrete implementation
is `AutonomousBrowserExplorer`. A reporting adapter can consume the saved result without adding
host workflow or experiment state to the core.

| Repository path | Responsibility |
| --- | --- |
| evals/network_capture/cases.json | Version, shared objective, budgets and three case definitions |
| evals/network_capture/requests.csv | Independent method/path/status oracle |
| evals/network_capture/dataset.py | Read cases and expected requests without starting services |
| evals/network_capture/fixture.py | Serve the controlled pages and resource chain |
| evals/network_capture/grade.py | Validate saved artifacts and compute a verdict |
| evals/network_capture/grader_cases.json | Thirty reviewed challenge labels and intended failed checks |
| evals/network_capture/challenges.py | Copy controls, apply mutations and summarize grader behavior |
| evals/run_quality.py | Audit historical runs and reconstruct reference-context metrics |
| tests/support/network_capture.py | Shared synthetic MCP, task factory and graph runner |
| tests/support/live_llm.py | Provider attempt, input, output and deadline limits |
| tests/unit/browser_exploration/test_grader_challenges.py | Exercise labels, source preservation and benchmark output |
| tests/live_llm/test_network_capture.py and test_network_capture_metrics.py | Six opt-in real-model component and metric checks |
| src/browser_exploration/observations.py | Compact network summaries selected for model context |
| src/browser_exploration/reporting.py | Artifact persistence and evidence references |
| scripts/reportportal.py | Separate publication of pytest/JUnit outcomes to ReportPortal |

The project already depends on LangSmith: `pyproject.toml` allows `>=0.11.2,<1`, and the lockfile
currently selects `0.11.2`. `Settings.langsmith_enabled` defaults to false. The explorer wraps
graph execution in a LangSmith tracing context. This optional tracing hook does not create the
reviewed evaluation datasets, associate runs with reference examples, or publish grader scores.

The handbook's API examples were checked against the installed SDK signatures where applicable.
The external experiment import uses a separately documented REST endpoint. The proposed
integration still needs an opt-in service acceptance check against the target workspace.

# 4. The controlled network dataset

`network-capture-v1` uses fixture version `1`. All three cases have the same objective and
budgets. Only the fixture path and expected traffic differ.

| Case | Expected traffic | Seeded HTTP errors | Purpose |
| --- | --- | --- | --- |
| NET-001 | Document plus three scripts: 4 unique requests | None | Complete healthy capture |
| NET-002 | Document plus three scripts: 4 unique requests | One 500 response | Capture a known server error |
| NET-003 | Document plus 200 healthy scripts plus two failing scripts: 203 unique requests | One 500 and one 404 response | Keep late errors visible after a long healthy prefix |

For NET-002, "positive" means that the capture task should succeed. The intentionally failing
resource is part of the expected page behavior and must appear in the evidence.

<!-- code: Current dataset objective -->
```text
Capture the complete network request listing for this page, including
static resources. Save the returned logs as evidence and report any
observed HTTP errors or request failures. Do not navigate away.
Finish once the capture is complete.
```

The line breaks above are for reading. The stored objective is one exact string; use the value
from `load_dataset()` when constructing a task. The grader requires that same objective in both
the saved task and the result.

<!-- code: Current task construction using the dataset -->
```python
from evals.network_capture.dataset import load_case, load_dataset
from browser_exploration.contracts import BrowserExplorationTask

dataset = load_dataset()
case = load_case("NET-003")
task = BrowserExplorationTask(
    url="http://127.0.0.1:8766" + case["path"],
    objective=dataset["objective"],
    **dataset["task_defaults"],
)
```

| Limit | Current value | Interpretation |
| --- | ---: | --- |
| max_steps | 10 | Browser tool-call allowance |
| max_model_iterations | 8 | Application model-call allowance |
| max_duration_seconds | 120 | Whole-run duration allowance |
| max_recent_observations | 5 | Recent observation selection allowance |
| max_observation_chars | 6000 | Per-observation context allowance |
| max_context_chars | 18000 | Selected-message character allowance |
| Network summary cap | 1000 | Additional compact network summary limit |

Mutation, form input and form submission permissions remain false. Raw evidence retention is
required for these evaluations. The real-model test harness imposes tighter provider limits
than the task's maximum allowances, described in Section 10.

## Why the fixture is deterministic

The fixture serves only reviewed routes. Parser-inserted scripts discover the next script
after the previous one loads. In NET-003 the two failing resources occur at the tail of the
chain. The final heading says `Network fixture ready`. This structure makes a long successful
prefix and late failures reproducible without relying on a public website.

The optional favicon returns HTTP 204 and is excluded from the request coverage denominator.
Responses use `Cache-Control: no-store`. A fresh browser context is still required for each
trial because cache headers do not reset all browser/session state.

The browser and fixture must share a reachable network location. Container loopback refers to
the container itself. Choose the fixture address according to the actual deployment, then use
the same address in the task and browser. The static-demo Compose setup does not automatically
start this network evaluation fixture.

# 5. From a model decision to retained evidence

The synthetic replay runs the production graph. Its model double supplies a network action
and then a finish decision. The graph performs its normal bootstrap and records the resulting
observations, metrics and evidence.

<!-- code: Minimal offline control using current repository helpers -->
```python
import asyncio
from pathlib import Path

from evals.network_capture.grade import grade_run
from browser_exploration.model import Action, Decision, DecisionResponse
from tests.support.network_capture import run_network_replay
from tests.unit.browser_exploration.fakes import FakeModel


async def main() -> None:
    model = FakeModel([
        DecisionResponse(Decision(action=Action(
            name="browser_network_requests",
            arguments={"static": True},
        ))),
        DecisionResponse(Decision(done=True)),
    ])
    result, directory = await run_network_replay(
        Path("runs/evals/offline-example"), "NET-003", model,
        configured_model="scripted-offline-control",
    )
    grade = grade_run("NET-003", directory)
    print(result.status, grade["passed"], grade["capture_recall"])


if __name__ == "__main__":
    asyncio.run(main())
```

This example creates local artifacts and makes no provider or browser call. Run it from the
repository root after installing the development environment. In a sandbox with missed asyncio
wakeups, use the existing pytest-based checks and their documented selector workaround.

`NetworkReplaySession.call()` refuses navigation away from the fixture and refuses a network
call unless `static` is exactly true. This is deliberate: the fake should expose incorrect
agent choices rather than silently turn them into a passing capture.

<!-- code: Current replay enforcement, abridged from tests/support/network_capture.py -->
```python
if name == "browser_network_requests":
    if args.get("static") is not True:
        raise AssertionError("This evaluation requires static-resource capture")
    return {
        "content": [{"type": "text", "text": network_listing(self.case_id)}]
    }
```

The real model can choose a different sequence. The grader does not assume the model followed
the scripted example; it reads the recorded actions and requires one network observation in
total, which must succeed, consistent counters, the correct scope and a completed result within
the fixed limits.

## Artifact responsibilities

| Artifact | What the grader or auditor learns |
| --- | --- |
| run_config.json | Original task, configured model and raw-retention setting |
| trajectory.jsonl | Ordered observations, tool arguments, summaries and evidence IDs |
| decisions.jsonl | Model decision/error events used to check model-call counts |
| result.json | Terminal status, evidence ledger, findings, errors and execution metrics |
| metrics.json | Saved execution metrics for inspection and reporting |
| evidence/obs-NNNN.json | Retained MCP response for a specific observation |
| benchmark.json | Challenge labels, verdicts, fingerprints and aggregate grader metrics |

The grader currently obtains execution metrics from `result.json`; it does not use the separate
`metrics.json` file as an independent scoring oracle. A local path in a report identifies a
file on this machine. It does not make that file available to a remote LangSmith evaluator.

# 6. A concrete network evidence example

The saved scripted NET-003 control contains 203 expected requests. Its raw text is 17,506
characters and its summary is 434 characters. The raw response stays in the evidence file;
the compact summary is the form selected for model context.

<!-- code: Actual summary from the saved scripted NET-003 control -->
```text
Network requests parsed: 203; HTTP errors: 2; failed: 0; unknown outcome: 0.
Request details omitted: 198.
[500] GET https://fixture.example/resources/NET-003/report.js
[404] GET https://fixture.example/resources/NET-003/missing.js
[200] GET https://fixture.example/cases/NET-003/
[200] GET https://fixture.example/resources/NET-003/healthy-resource-0001.js
[200] GET https://fixture.example/resources/NET-003/healthy-resource-0002.js
```

The 198 omitted request details are omitted from this summary, not from the retained listing.
Both seeded errors remain visible before the healthy examples. A downstream reader should
interpret the omitted count alongside the full retained artifact and the parsed total.

<!-- code: Evidence reference from the same scripted control -->
```json
{
  "id": "evidence-obs-0003",
  "observation_id": "obs-0003",
  "kind": "browser_observation",
  "path": "evidence/obs-0003.json",
  "retained": true
}
```

The observation points to `evidence-obs-0003`, and that reference points back to `obs-0003`.
The grader checks both directions. It also checks that the retained path stays inside the run
directory and is not reused for another retained observation.

<!-- code: Relevant fields of the associated observation; other fields omitted -->
```json
{
  "id": "obs-0003",
  "action_name": "browser_network_requests",
  "action_arguments": {"static": true},
  "evidence_ref": "evidence-obs-0003",
  "error": false
}
```

These are examples of saved application data. The shortened observation above is not a full
standalone V1 object. The complete observation also has its URL, summary and timestamp.

## What compact context does and does not prove

The network summary preserves errors within a 1,000-character cap. The context builder can
then select it under the broader task limits. Unit regressions check the next-decision context
using the actual graph and model adapter payload.

The saved-artifact grader checks the recorded summary. It does not independently archive and
reconstruct every outgoing provider request. For a future experiment that claims exact prompt
exposure, record a reviewed representation of the selected context with explicit redaction and
size limits, then add an evaluator for that representation.

# 7. How grader version 2 makes its decision

`grade_run(case_id, directory)` is a synchronous function that reads files. It validates the
public task, result and observation models, then runs deterministic checks. It does not contact
the fixture, the model, MCP, ReportPortal or LangSmith.

<!-- code: Calling the current offline grader -->
```python
from pathlib import Path
from evals.network_capture.grade import grade_run

report = grade_run("NET-003", Path("runs/evals/network-capture/RUN_ID"))
print(report["verdict"])
print(report["failed_checks"])
print(report.get("capture_recall"))
```

Replace `RUN_ID` with an actual saved run directory. A malformed or missing artifact produces
an `invalid_artifacts` verdict with `passed=false` and a safe exception type. An unknown case
ID is a caller configuration error and is rejected before artifact grading.

| Check group | Main requirements |
| --- | --- |
| Task identity | Correct fixture path, matching task/result URL, identical exact objective |
| Task policy | Fixed budgets and no mutation/input/submission permission |
| Execution | Stay on target, complete, remain within budgets, one successful network capture |
| Ledger consistency | Browser/model counts, action list, unique IDs and finding counts agree |
| Time | Timezone-aware result times, start before finish, observations ordered and in range |
| Provenance | Evidence owners, kinds, retained paths and claim references are consistent |
| Capture scope | Static resources explicitly enabled with no conflicting flag or omission note |
| Raw content | Successful tool response, recognized listing, correct origin and exact oracle coverage |
| Summary | Correct counts, seeded failures present, no invented requests, within character cap |

## Scope is explicit

The grader recognizes `static`, `includeStatic` and `include_static`. At least one must appear,
and every recognized value present must be exactly true. For example, a true `static` together
with false `includeStatic` fails. A raw note that positive numbers of static requests were
omitted also fails.

<!-- code: Current capture-scope check, abridged from grade.py -->
```python
scope = [
    observation["action_arguments"][key]
    for key in _STATIC_KEYS
    if key in observation["action_arguments"]
]
checks["static_resources_requested"] = (
    bool(scope) and all(value is True for value in scope)
)
```

## Coverage uses the independent oracle

The grader parses raw records into method, URL and outcome. It compares unique method/path/
status identities against the CSV oracle and separately checks origin. The optional successful
favicon is allowed. Legitimate repeated requests do not lower coverage, but summary counts
must reflect the actual number of parsed records, including repeats.

<!-- code: Current oracle comparison, abridged from grade.py -->
```python
captured = {
    (item.method, urlsplit(item.url).path, item.outcome)
    for item in records
}
oracle = {(item.method, item.path, str(item.status)) for item in expected}
missing = oracle - captured
unexpected = captured - oracle - {("GET", "/favicon.ico", "204")}
checks["all_expected_requests_retained"] = not missing
checks["no_unexpected_requests"] = not unexpected
```

## Summary provenance includes origin

A summary entry must match a retained request's method, scheme, host/port, path and outcome.
Changing the host while keeping a familiar path fails. Query values are excluded from this
identity comparison so privacy redaction can remain valid. This is an explicit scope choice:
the current oracle does not distinguish requests solely by query values.

Artifact consistency cannot prove that an untrusted producer actually ran a browser. Controlled
execution, reviewed fixture state and trusted storage are still part of the experiment setup.

# 8. Metrics, verdicts and denominators

The final pass is the conjunction of all applicable grader checks. A high coverage score alone
does not override a failed provenance, budget, scope or summary check.

| Metric | Definition | Unknown or inapplicable behavior |
| --- | --- | --- |
| capture_recall | Matched unique oracle requests / expected unique requests | May be absent if capture cannot be assessed |
| failure_summary_recall | Expected HTTP-error identities retained in summary / expected HTTP-error identities | Null for a case with no seeded failures |
| summary_chars | Length of the saved summary | Missing when grading stops before summary analysis |
| valid_acceptance_rate | Valid challenge variants accepted / valid variants tested | Null if no valid variants selected |
| corruption_rejection_recall | Corrupted challenge variants rejected / corrupted variants tested | Null if no corrupted variants selected |
| false_acceptance_rate | Corrupted variants accepted / corrupted variants tested | Null if no corrupted variants selected |

For the full challenge corpus, rejection recall is a useful count, but a correct challenge
outcome additionally requires the intended failed check. Rejecting a wrong-scope artifact for
an unrelated malformed field must not count as detection of the scope bug.

<!-- code: Selected fields from a passing scripted NET-003 grade -->
```json
{
  "dataset_id": "network-capture-v1",
  "grader_version": "2",
  "case_id": "NET-003",
  "run_status": "completed",
  "passed": true,
  "verdict": "pass",
  "failed_checks": [],
  "failure_category": null,
  "expected_requests": 203,
  "matched_requests": 203,
  "capture_recall": 1.0,
  "expected_failures": 2,
  "failure_summary_recall": 1.0,
  "raw_text_chars": 17506,
  "summary_chars": 434,
  "summary_limit": 1000
}
```

## Infrastructure is visible

The current classifier labels a failed run as `infrastructure` when it has `llm_api_error` or
`mcp_unavailable` and no successful network capture. It remains a failing run. This is a narrow
rule, not a complete taxonomy of every operational problem. A provider error after capture
or another error code can remain an `evaluation` failure under the present implementation.

If a provider never responded, capture recall is not measured. Do not replace an absent value
with zero or one. Report planned, attempted, completed and infrastructure-blocked counts
alongside any conditional success rates. For the end-to-end pass rate, keep every attempted
run in the denominator and explain its outcome.

<!-- code: Current infrastructure classification, abridged from grade.py -->
```python
infrastructure = (
    report["run_status"] == "failed"
    and bool(set(report["run_error_codes"]) & {
        "llm_api_error", "mcp_unavailable"
    })
    and not report["checks"]["one_successful_network_capture"]
)
```

# 9. Testing the grader itself

The challenge corpus has 30 labeled variants: seven valid controls and 23 corruptions. It is
stored separately from the three browser fixture cases. Most negative variants use the large
NET-003 control because it exercises evidence retention and summary compaction together.

Each variant starts as a private copy of a passing saved run. The mutation changes a reviewed
property, such as capture scope or evidence ownership. The runner hashes the source controls
before and after the benchmark. A source change fails the preservation expectation.

<!-- code: Example challenge labels from grader_cases.json -->
```json
[
  {
    "id": "G-006", "base_case": "NET-002",
    "mutation": "scope_alias", "expected_pass": true
  },
  {
    "id": "G-104", "base_case": "NET-003",
    "mutation": "scope_conflict", "expected_pass": false,
    "expected_check": "static_resources_requested"
  },
  {
    "id": "G-106", "base_case": "NET-003",
    "mutation": "summary_foreign_origin", "expected_pass": false,
    "expected_check": "summary_adds_no_requests"
  }
]
```

<!-- code: Current challenge regression, abridged from test_grader_challenges.py -->
```python
source = await control(tmp_path / "control", scenario["base_case"])
before = fingerprints(source)
variant = copy_variant(source, tmp_path / scenario["id"], scenario)
report = grade_run(scenario["base_case"], variant)
assert report["passed"] is scenario["expected_pass"], report
if not scenario["expected_pass"]:
    assert scenario["expected_check"] in report["failed_checks"], report
assert fingerprints(source) == before
```

The runner refuses an existing output directory, overlapping source/output directories and
escaping evidence paths. Reports record corpus and grader hashes, per-case labels and checks,
control fingerprints and aggregate rates. Unknown denominators stay null.

Derived challenge trees carry an `evaluation_manifest.json` marker. The historical run audit
recognizes that exact marker type and excludes copied results from agent execution counts.
An unknown marker type cannot hide an original run.

## Reviewed challenge inventory

| ID | Valid variant | Expected result |
| --- | --- | --- |
| G-001 | Unchanged control | Accept |
| G-002 | Unnumbered request listing | Accept |
| G-003 | Reversed request order | Accept |
| G-004 | Optional successful favicon | Accept |
| G-005 | Healthy counts-only summary | Accept |
| G-006 | includeStatic alias set true | Accept |
| G-007 | Repeated legitimate request with accurate counts | Accept |

| ID | Corruption | Intended failed check |
| --- | --- | --- |
| G-101 | Changed task objective | correct_objective |
| G-102 | Static scope disabled | static_resources_requested |
| G-103 | Static scope absent | static_resources_requested |
| G-104 | Conflicting static flags | static_resources_requested |
| G-105 | Positive static omission note | no_static_omissions |
| G-106 | Foreign origin in summary | summary_adds_no_requests |
| G-107 | Wrong evidence kind | correct_evidence_kind |
| G-108 | Wrong evidence owner | consistent_evidence_owners |
| G-109 | Finish before start | ordered_timestamps |
| G-110 | Reordered observation times | ordered_timestamps |
| G-111 | Reused evidence path | unique_evidence_paths |
| G-112 | Retention disabled in config | retention_enabled |
| G-113 | Claim references missing evidence | known_claim_evidence |
| G-114 | Wrong findings count | consistent_result_counts |
| G-115 | Expected request removed | all_expected_requests_retained |
| G-116 | Wrong summary counts | accurate_summary_counts |
| G-117 | Foreign origin in raw listing | same_origin_capture |
| G-118 | Network evidence not retained | linked_retained_evidence |
| G-119 | Unexpected request added | no_unexpected_requests |
| G-120 | Unfinished result | finished |
| G-121 | Unsupported listing format | readable_listing |
| G-122 | Tool error in raw response | tool_response_succeeded |
| G-123 | Corrupt raw JSON | invalid_artifacts |

# 10. Recorded results and runnable commands

The following results are dated local development evidence. The challenge benchmark used
scripted controls with synthetic browser responses. Both grader versions saw the same corpus
and source control artifacts.

| Grader metric | Previous grader | Version 2 |
| --- | ---: | ---: |
| Valid variants accepted | 7 / 7 | 7 / 7 |
| Corruptions rejected | 9 / 23 | 23 / 23 |
| Corruption rejection recall | 39.1% | 100% |
| False acceptances | 14 / 23 | 0 / 23 |
| False acceptance rate | 60.9% | 0% |

Evidence: `runs/evals/grader-hardening/before-verified/benchmark.json` and
`runs/evals/grader-hardening/after-verified/benchmark.json`. Both reports say
`controls_unchanged=true`. The reviewed corpus is a development regression set; its perfect
current score does not estimate reliability on unseen corruptions or live agent tasks.

| Verification or observation | Recorded result | Interpretation |
| --- | --- | --- |
| October 26 offline suite | 672 passed, 4 skipped | Four live checks remained opt-in |
| Latest verified ReportPortal upload recorded in docs | 633 passed, 4 skipped; launch 18 | Earlier source snapshot; 672-test result was not uploaded |
| Historical run audit | 85 original results; 123 derived results excluded | Saved-run diagnostics, not a fixed controlled benchmark |
| Controlled live network dataset | No successful complete live baseline established in the reviewed evidence | Run repeated real trials before claiming agent success rates |

The prior real-model NET-001 smoke attempt stopped on provider connectivity. It does not
establish model capture quality. The three current scripted controls do pass the grader:

| Scripted control | Capture recall | Failure-summary recall | Raw text / summary characters |
| --- | ---: | ---: | ---: |
| NET-001 | 4 / 4 | Not applicable | 322 / 354 |
| NET-002 | 4 / 4 | 1 / 1 | 307 / 339 |
| NET-003 | 203 / 203 | 2 / 2 | 17506 / 434 |

A tiny listing can have a summary longer than the raw text because the summary adds structured
counts. Character length is a context budget measure, not a compression guarantee.

## Cheap offline verification

<!-- code: Current offline commands -->
```bash
uv sync --locked
uv run python -m pytest -c pyproject.toml tests/unit -k network -q
uv run python -m pytest -c pyproject.toml \
  tests/unit/browser_exploration/test_grader_challenges.py -q
```

The `-k network` selection alone is not the complete challenge corpus. Keep the explicit
challenge test command. If the restricted environment misses asyncio wakeups, add
`PYTHONPATH=.:src` and `-p tests.sandbox_polling`. That plugin only caps selector polling;
network guards remain active.

<!-- code: Current CLI for a complete offline challenge benchmark -->
```bash
uv run python -m evals.network_capture.challenges \
  --control NET-001=runs/evals/network-capture/RUN_001 \
  --control NET-002=runs/evals/network-capture/RUN_002 \
  --control NET-003=runs/evals/network-capture/RUN_003 \
  --output-dir runs/evals/grader-challenges/NEW_SESSION
```

Replace the three run directories with passing controls and use a new output directory.
All three controls are needed for all 30 variants. A partial selection reports its actual
counts. The command exits nonzero if any expected label or intended failed check is missed.

## Real-model component trials

<!-- code: Current opt-in real-model command; browser remains synthetic -->
```bash
uv run python -m pytest -c pyproject.toml tests/live_llm \
  --live-llm -n 0 --llm-max-calls 12 -v
```

The default harness uses `gpt-4.1-mini`, at most six provider attempts per selected session,
two per case, 384 output tokens per request, a 20,000-character input preflight, a 20-second
request deadline and zero SDK retries. Running all six checks requires explicitly raising the
session allowance to 12, as shown above. Select another available model explicitly with
`--llm-model`. Missing provider usage stays unknown. Provider failure stops further requests;
unattempted remaining cases skip with an explanation.

Private session reports are under `runs/test-live-llm/`. These component checks exercise a real
model with synthetic traffic. They are separate from the complete live-browser command below.

## Controlled real-browser trials

Start the fixture and MCP server independently. Configure externally enforced network controls,
fixture reachability and the required private-network opt-in before invoking the app. The
example below sets the task and context limits; it assumes that operating setup is complete.

<!-- code: Start the current fixture -->
```bash
uv run python -m evals.network_capture.fixture \
  --bind 127.0.0.1 --port 8766
```

<!-- code: Current application invocation for NET-001 -->
```bash
EVAL_GOAL="$(uv run python - <<'PY'
from evals.network_capture.dataset import load_dataset
print(load_dataset()["objective"])
PY
)"
BROWSER_MAX_RECENT_OBSERVATIONS=5 \
BROWSER_MAX_OBSERVATION_CHARS=6000 \
BROWSER_MAX_CONTEXT_CHARS=18000 \
uv run python -m browser_exploration.cli run \
  --url http://127.0.0.1:8766/cases/NET-001/ \
  --goal "$EVAL_GOAL" \
  --max-steps 10 --max-model-iterations 8 \
  --max-duration-seconds 120 --retain-raw-evidence \
  --output-dir runs/evals/network-capture
```

For loopback deployments, explicitly configure `BROWSER_ALLOW_PRIVATE_NETWORK=true`.
Set `BROWSER_NETWORK_RESTRICTED=true` only after the external controls exist. Neither flag
creates those controls. Change the fixture URL when the browser runs in another network namespace.

<!-- code: Grade the saved live run without any additional model call -->
```bash
uv run python -m evals.network_capture.grade \
  --case-id NET-001 \
  --run-dir runs/evals/network-capture/REPLACE_WITH_RUN_ID
```

Use a fresh browser context for NET-002 and NET-003 and keep the exact objective and budgets.
Retain every attempt, including failures. The CLI grader prints JSON and exits 0 for a pass
or 1 for a failing/invalid saved run.

# 11. How to add the next dataset case

Start by writing the behavior and the independent expected evidence. A useful case should fail
for a concrete bug and pass for a correct implementation. Avoid defining success as a phrase
that the model can repeat without making a tool call.

For a new request case, update the reviewed case inputs and oracle together. Check whether the
fixture can generate the traffic deterministically and whether the parser and grader can
represent its outcome. Then add a passing graph control and at least one targeted failure
variant. Review expected outcomes separately from producer code.

| Proposed case | What it would measure | Required design work |
| --- | --- | --- |
| Transport failure | A connection failure survives retention and summary | Extend the integer-status CSV oracle and define stable failure identity |
| Capture after interaction | Traffic caused by a permitted action is collected | New task policy, controlled control/action and revised single-capture assumptions |
| Delayed or pending traffic | Capture timing and unknown outcomes remain explicit | Deterministic delay/readiness mechanism and a defined pending-outcome oracle |

These are future cases. The current CSV loader converts `status` to an integer, and the current
grader expects one successful network capture on an unchanged target page. A transport failure
cannot be added correctly by putting the text `FAILED` into the existing integer column.
Likewise, an interaction case requires an intentional change in task permissions and scoring.

## A case review checklist

- State the capability and one observable failure mode.
- Define the fixture version, expected requests and timing/readiness condition.
- Keep expected outcomes out of the agent objective and page labels where possible.
- Define accepted variants such as duplicate legitimate requests or redacted query values.
- Specify budgets, allowed actions and evidence-retention requirements.
- Add a valid control and a targeted negative challenge for the new rule.
- Decide which existing comparisons remain valid after the dataset changes.
- Record a new version or content hash and preserve earlier experiment manifests.

For new grader challenges, add a reviewed row in `grader_cases.json` and an explicit mutation
branch in `copy_variant()`. Negative rows need `expected_check`. The parameterized test then
checks both the verdict and the intended rejection reason. Adjust corpus totals only after
reviewing the expanded labels; do not silently rewrite historical benchmark denominators.

Public task/result changes are a separate contract decision. Read
`docs/contracts/browser_exploration_v1.md` and regenerate schemas when that public contract
changes. An external experiment adapter should usually need no change to that boundary.

# 12. Designing a useful experiment

Begin with five independent trials per live case, as the current dataset guide proposes.
For three cases this is 15 attempts per configuration. This is a diagnostic pilot; five trials
are too few to establish a stable production reliability estimate.

Use a fresh browser context for each attempt. Keep the dataset, fixture version, budgets,
permissions and environment fixed while changing one model, prompt or application policy.
Record the configured and provider-reported model separately. When comparing configurations,
interleave or randomize their order to reduce time-of-day and service-condition effects.

<!-- code: Proposed local experiment manifest; not a current public app contract -->
```json
{
  "schema": "browser-evaluation-experiment/v1",
  "experiment_id": "network-live-baseline-20261026",
  "evaluation_layer": "live_browser",
  "dataset_id": "network-capture-v1",
  "dataset_sha256": "HASH_OF_REVIEWED_INPUTS_AND_ORACLE",
  "grader_version": "2",
  "grader_sha256": "HASH_OF_GRADE_SOURCE",
  "source_revision": "GIT_COMMIT",
  "source_dirty": true,
  "source_snapshot_sha256": "HASH_OF_RELEVANT_WORKTREE_FILES",
  "configured_model": "SELECTED_MODEL",
  "planned_cases": ["NET-001", "NET-002", "NET-003"],
  "planned_repetitions": 5,
  "planned_attempts": 15,
  "attempts": []
}
```

A Git commit alone is insufficient when the worktree has uncommitted changes. Hash the relevant
source and configuration, and retain those versions with the experiment. Do not upload a raw
Git diff or environment dump; they may contain unrelated data or credentials.

## Report both failures and missing measurements

Report end-to-end pass count, infrastructure failures, artifact failures and attempted count.
For measured captures, also report request recall and seeded-failure preservation with explicit
denominators. Show browser/model calls, duration and provider-reported tokens. A missing token
count is unknown usage, not zero cost.

For the large-list case, track summary length and full raw retention together. A shorter summary
is useful only if it still preserves the error identities and accurate counts. Do not optimize
summary size by dropping difficult requests from the raw evidence.

For grader experiments, keep the seven valid controls and 23 corruptions visible separately.
An all-rejecting grader would achieve 100% corruption rejection and 0% valid acceptance. The
two metrics together expose that failure immediately.

## Interpreting historical runs

The October 26 audit contains 85 original saved results and excludes 123 derived challenge
copies. Among 35 real-browser runs with network-log objectives, 28 retained readable network
evidence; six stopped at the provider. The conditional availability was 28/29, or 96.6%.
Eleven captures included static resources and 17 used a static filter.

These public-site runs have no complete independent traffic oracle. Their availability measure
helps locate problems, but it is not the strict capture pass rate of the controlled dataset.
The reference-context reconstruction improved valid-reference precision from 6,178/10,169
(60.75%) to 6,175/6,175 (100%) on the same saved checkpoints. It measures context selection;
new controlled trials are needed to measure the effect on model reference rejections.

# 13. Diagnosing a failing evaluation

Read `failed_checks` before interpreting the final prose. Then follow the relevant evidence
link back to the trajectory and raw artifact. Inspect the run error codes and execution layer
before assigning the cause to the model.

| Symptom | Inspect first | Likely investigation |
| --- | --- | --- |
| invalid_artifacts | artifact_error_type and required files | Missing JSON, invalid schema or escaping path |
| static_resources_requested | Network action arguments | Missing, false or contradictory scope flags |
| all_expected_requests_retained | missing_requests and raw listing | Early capture, filtered traffic or incorrect fixture |
| accurate_summary_counts | Summary first line and all parsed records | Count logic, duplicates or unexpected format |
| expected_failures_in_summary | Tail failures and selected summary entries | Error prioritization or truncation |
| summary_adds_no_requests | Summary origin/path/outcome | Invented endpoint or mismatched evidence |
| consistent_evidence_owners | Observation and evidence ledger | Broken two-way ownership mapping |
| infrastructure | run_error_codes and provider diagnostics | Provider/MCP availability before capture |
| Correct local grade, missing remote experiment | Export receipt and remote IDs | Publication failure; do not rerun the browser to fix an upload |

Useful debugger locations are `summarize_network_observation()` in `observations.py`,
`run_network_replay()` in the shared test support, `_grade()` after parsing records, and
`grade_run()` after it builds `failed_checks`. Use the existing context regression tests to
inspect the actual next-decision payload rather than inferring it from a displayed report.

At a grader breakpoint, compare `oracle`, `captured`, `missing`, `unexpected` and
`summary_records`. At an evidence breakpoint, compare the observation's `evidence_ref` with
the matching reference's ID, owner and path. Keep the full raw response out of the model context
while inspecting it locally in the debugger.

Do not repair a historical run to make it pass. If a fixture or implementation changes, create
a new run. If a grader changes, rescore immutable artifacts and record the new grader identity.
That preserves the ability to explain why a result changed.

## Inspect the context at the model boundary

Set a breakpoint immediately after `self.context.build(...)` in
`ExplorationNodes.next_action()` in `src/browser_exploration/graph.py`. The code below is the
current call that selects observations and carries the latest reference ownership into context.
Inspect `state["messages"]` on the decision after network capture.

<!-- code: Current context-selection call from ExplorationNodes.next_action -->
```python
state["messages"] = self.context.build(
    state["task"],
    state["current_url"],
    state["observations"],
    state["verified_behaviors"],
    state["findings"],
    state["unresolved_questions"],
    state["successful_actions"],
    state["action_feedback"],
    current_references=self.policy.element_labels,
    reference_observation_id=self.reference_observation_id,
)
```

For NET-003, locate the network observation and compare its summary to the selected messages.
Both failing endpoint/status pairs should be visible in the compact summary. The full raw
17,506-character scripted listing should remain an evidence artifact, rather than a pasted
message. Inspect the model adapter's submitted payload as well when investigating exact
provider input; the saved grade alone does not establish that final serialization step.

# Next Step

## Current direction: use the native capture dataset

The selected dataset, `browser-network-capture-native-v1`, was created through the LangSmith
SDK. It has one example for each capture case. Inputs contain the case ID, fixture path, task
objective and budgets. Expected outputs contain the independent method/path/status request
oracle. The oracle is not part of the agent task input.

For future agent runs, use LangSmith's SDK evaluation path against this native dataset. The target
in `evals/network_capture/langsmith_agent_eval.py` executes the agent on the controlled fixture,
saves ordinary local run artifacts, grades each run with `grade_run()`, and sends compact scores
to LangSmith. It verifies the remote examples against the local oracle before starting. The
runner has not yet been used for an experiment, so the dataset still has no actual outputs or
scores. The grader-challenge dataset remains a separate later evaluation.

The earlier design recommended the external experiment importer for publishing already-completed
local evaluations. That remains an option for a separate externally managed dataset. It cannot
append results to this native dataset, so do not use it for the selected capture dataset. [L1]

## Three integration options

| Approach | How it fits | Strength | Main limitation |
| --- | --- | --- | --- |
| A. SDK evaluate/aevaluate | Wrap a runner or saved-artifact reader as the target; adapt deterministic grades into evaluators | Native dataset/example orchestration and experiment comparison | Live targets couple execution to orchestration; saved-file targets measure replay latency unless actual duration is recorded separately |
| B. Completed experiment import | Export saved artifacts and grades to the external experiment REST endpoint | Independent publication, no repeated agent work, explicit export scope | Separate externally managed dataset; cannot append to the selected native dataset |
| C. LangSmith pytest integration | Mark a dedicated opt-in evaluation suite and log inputs, outputs and feedback | Familiar assertions and parameterized cases | Automatic dataset synchronization and network activity conflict with ordinary unit guards |

Because the selected dataset is native, Option A is the path for future capture trials. Option B
remains a separate option for publishing completed local evaluations to an external dataset.
The two dataset types cannot be mixed. [L1, L2, L3]

## Map the existing data before writing an uploader

Create separate dataset families for the three capture cases and the 30 grader challenges.
They have different meanings of success. A challenge artifact may correctly fail `grade_run()`;
the challenge experiment succeeds when that failure matches its reviewed label and intended
check. Calling both values `pass` would make the comparison misleading.

| Repository concept | Proposed LangSmith field or object |
| --- | --- |
| network-capture-v1 plus content hash | External dataset identity for capture experiments |
| network-grader-challenges-v1 plus content hash | Separate external dataset identity for grader experiments |
| NET-001 / G-104 | Stable example identity, encoded as a UUID row_id for external import |
| Task objective, case path and budgets | Example inputs; synthetic and reviewed values only |
| Request oracle or expected challenge label | expected_outputs; kept out of the target's model input |
| Selected saved result/grade fields | actual_outputs |
| Deterministic boolean or numeric grade | evaluation_scores |
| Trial/configuration | One experiment with metadata and planned/attempted counts |
| Original run ID and artifact digest | run_metadata for local provenance |
| Full network logs, screenshots and credentials | Retained locally; outside the initial export allowlist |

LangSmith examples distinguish inputs, optional reference outputs and metadata. Experiments
record results for one system configuration against examples. These map cleanly to this
repository's reviewed inputs, independent oracle and saved outputs. [L4]

For the first capture pilot, publish each repetition as a separate three-row experiment. Stable
case UUIDs then align NET-001 with NET-001 across experiments without inventing a new example
for every attempt. Record `repetition_index` in experiment metadata. The 30 challenge variants
form one 30-row experiment for each grader version.

Do not merge synthetic replay, real-model replay and live browser results under one unlabeled
average. Record `evaluation_layer` on every experiment. For the challenge corpus, use
`evaluation_layer=grader_challenge`, and include `expected_pass`, `actual_pass`,
`label_matches` and `expected_check_detected` in each row.

## A proposed export contract

The export should be a small local file that can be inspected before any API call. It should
identify the dataset content, source snapshot, grader source, local attempt and selected metric
values. Store local artifact locations only in a private receipt; a remote viewer cannot open
this machine's filesystem path.

<!-- code: Proposed capture export envelope; not an application V1 schema -->
```json
{
  "schema": "browser-evaluation-export/v1",
  "evaluation_layer": "live_browser",
  "dataset_id": "network-capture-v1",
  "dataset_content_sha256": "REVIEWED_CONTENT_HASH",
  "experiment_id": "network-baseline-trial-01",
  "grader_version": "2",
  "grader_source_sha256": "GRADER_HASH",
  "rows": [
    {
      "case_id": "NET-002",
      "local_run_id": "LOCAL_RUN_ID",
      "artifact_sha256": "ARTIFACT_HASH",
      "actual": {
        "passed": true,
        "verdict": "pass",
        "failure_category": null,
        "failed_checks": [],
        "capture_recall": 1.0,
        "failure_summary_recall": 1.0
      }
    }
  ]
}
```

A failed pre-capture attempt must still produce an export row. Its `passed` value is false,
its failure category/error code remain visible, and unmeasured recall fields stay absent or
null. If result creation itself failed, the experiment runner needs an attempt record with
an execution error; it must not silently omit that planned attempt from accounting.

For initial export, allow only reviewed task fields, fixed case identifiers, numeric metrics,
booleans, bounded check names and safe error codes. Do not serialize the entire result, raw
evidence, exception text, environment or tracing state. This keeps publication small and avoids
exposing page content that the grading process does not need remotely.

## Option B: construct one import row

The following proposed helper translates a local grade into one external-import row using the
documented request schema. [L11] It uses current dataset functions and grade field names. Its
inputs contain the shared task. The request oracle remains in `expected_outputs`.

<!-- code: Proposed row builder for a completed capture evaluation -->
```python
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from evals.network_capture.dataset import load_case, load_dataset, load_requests


def import_row(
    case_id: str,
    grade: dict[str, Any],
    result: dict[str, Any],
    dataset_content_hash: str,
) -> dict[str, Any]:
    dataset = load_dataset()
    identity = f"{dataset['dataset_id']}:{dataset_content_hash}:{case_id}"
    actual = {
        key: grade[key]
        for key in (
            "passed", "verdict", "failure_category", "failed_checks",
            "capture_recall", "failure_summary_recall", "summary_chars",
        )
        if key in grade
    }
    scores = [{"key": "capture_pass", "score": int(grade["passed"])}]
    for key in ("capture_recall", "failure_summary_recall"):
        value = grade.get(key)
        if value is not None:
            scores.append({"key": key, "score": value})
    return {
        "row_id": str(uuid5(NAMESPACE_URL, identity)),
        "inputs": {
            "case_id": case_id,
            "path": load_case(case_id)["path"],
            "objective": dataset["objective"],
            "limits": dataset["task_defaults"],
        },
        "expected_outputs": {
            "requests": [
                {"method": item.method, "path": item.path,
                 "status": item.status}
                for item in load_requests(case_id)
            ]
        },
        "actual_outputs": actual,
        "evaluation_scores": scores,
        "start_time": result["started_at"],
        "end_time": result["finished_at"],
        "run_name": case_id,
        "run_metadata": {
            "local_run_id": result["run_id"],
            "grader_version": grade["grader_version"],
            "error_codes": grade.get("run_error_codes", []),
        },
    }
```

This is the normal-result path. Before production use, the adapter must also handle missing
or malformed result files from an independent attempt manifest. It must validate row times,
numeric ranges, permitted field sizes and dataset hashes before preparing the payload.
For deliberately corrupted challenge artifacts, use the challenge benchmark's execution times,
not the intentionally invalid timestamps inside the copied result.

<!-- code: Proposed feedback for a grader-challenge row -->
```python
def challenge_scores(scenario: dict) -> list[dict]:
    correct = (
        scenario["label_matches"] and scenario["expected_check_detected"]
    )
    return [
        {"key": "challenge_correct", "score": int(correct)},
        {"key": "label_matches", "score": int(scenario["label_matches"])},
        {"key": "expected_check_detected",
         "score": int(scenario["expected_check_detected"])},
    ]
```

This preserves the difference between a failing artifact and a correctly functioning grader.
Keep `actual_pass` as output data, not as the challenge success score.

## Option B: send the reviewed payload

The documented external importer uses `POST /api/v1/datasets/upload-experiment`. It returns
dataset and experiment objects. Row timestamps must fit inside the experiment interval.
Persistence and aggregate statistics can lag the response. Built-in token/cost totals may
remain null; retain any known local usage as explicit output/metadata instead of claiming
LangSmith inferred it. [L1]

<!-- code: Proposed one-shot transport; payload preparation and verification are separate -->
```python
import os
from typing import Any
import httpx


def submit_external_experiment(payload: dict[str, Any]) -> dict[str, Any]:
    # Explicit full URL avoids guessing a SaaS region or self-hosted prefix.
    url = os.environ["LANGSMITH_IMPORT_URL"]
    headers = {"x-api-key": os.environ["LANGSMITH_API_KEY"]}
    workspace = os.environ.get("LANGSMITH_WORKSPACE_ID")
    if workspace:
        headers["X-Tenant-Id"] = workspace
    with httpx.Client(timeout=30.0, follow_redirects=False) as client:
        response = client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        body = response.json()
    if not body.get("experiment", {}).get("id"):
        raise ValueError("Import response has no experiment identity")
    if not body.get("dataset", {}).get("id"):
        raise ValueError("Import response has no dataset identity")
    return body
```

`LANGSMITH_IMPORT_URL` is a proposed adapter variable, not an SDK-standard setting. For the
documented US service example its value is
`https://api.smith.langchain.com/api/v1/datasets/upload-experiment`; select the actual region
or deployment explicitly. Use a credential permitted to manage evaluation data and read it
back. A tracing-only key does not supply those permissions. Multi-workspace requests need
explicit workspace selection. [L1, L5]

The complete payload adds the required experiment envelope to the constructed rows. [L11]
Record source/configuration hashes and planned/attempted counts in metadata. Include a reviewed
content hash in the external dataset identity so changed cases cannot silently join an
incompatible comparison.

<!-- code: Proposed complete import envelope for one capture repetition -->
```python
from datetime import datetime
from uuid import NAMESPACE_URL, uuid5


def experiment_payload(
    rows: list[dict], content_hash: str, name: str, metadata: dict
) -> dict:
    if not rows:
        raise ValueError("An experiment needs at least one attempted row")
    starts = [datetime.fromisoformat(row["start_time"]) for row in rows]
    ends = [datetime.fromisoformat(row["end_time"]) for row in rows]
    if any(t.tzinfo is None for t in [*starts, *ends]):
        raise ValueError("Experiment timestamps must include a timezone")
    if any(start > end for start, end in zip(starts, ends)):
        raise ValueError("A run cannot finish before it starts")
    identity = f"browser-evaluation:network-capture-v1:{content_hash}"
    return {
        "experiment_name": name,
        "experiment_start_time": min(starts).isoformat(),
        "experiment_end_time": max(ends).isoformat(),
        "dataset_id": str(uuid5(NAMESPACE_URL, identity)),
        "dataset_name": f"browser-network-external-{content_hash[:12]}",
        "experiment_metadata": metadata,
        "results": rows,
    }
```

The transport function performs one write and returns the response. It is not a robust uploader yet.
Do not run it as a retry loop: a timeout after transmission can leave the remote experiment
created even when the client did not receive its ID. Stable row IDs align examples; they are
not a documented guarantee of idempotent experiment creation.

## Receipt and read-back protocol

Implement a receipt with states `prepared`, `submitted`, `verified`, `failed` and `unknown`.
The local payload hash, experiment fingerprint and destination workspace identify the intended
publication. After a successful response, store the returned dataset/experiment IDs immediately.
After an ambiguous transport outcome, reconcile the destination before resubmitting.

Use a bounded polling loop to read the remote experiment, list its root runs and inspect
feedback. `read_project(include_stats=True)` exposes experiment statistics, and the installed
SDK also provides `list_runs()` and `list_feedback()`. A 2xx write alone is insufficient for
the proposed success condition. [L6]

<!-- code: Proposed read-back inspection with the installed SDK -->
```python
from langsmith import Client

client = Client()  # Explicit endpoint/workspace configured by the operator.
experiment_id = receipt["experiment_id"]
project = client.read_project(project_id=experiment_id, include_stats=True)
runs = list(client.list_runs(project_id=experiment_id, is_root=True))
if not runs or len(runs) != receipt["expected_rows"]:
    raise RuntimeError("Remote rows are not ready for verification")
feedback = list(client.list_feedback(run_ids=[run.id for run in runs]))

# Adapter checks must compare identities and values, not only counts.
```

The full verification compares the exact expected example set, every required score and null/
missing behavior, safe failure categories, dataset association and experiment metadata. It
should tolerate eventual consistency only until a configured deadline. A deadline leaves an
unverified receipt and a nonzero exit code, while preserving the original local grades.

For a challenge baseline import, verify 30 rows, seven positive labels, 23 negative labels and
the correct challenge scores. For each capture repetition, verify three rows or explicitly
account for missing/unattempted cases in the experiment manifest. Confirm duplicate protection
by resuming a verified receipt without issuing another creation request.

## Option A: native SDK evaluation

The SDK can run a callable target against a dataset and attach custom evaluator results. For
an async application, `aevaluate()` is the corresponding interface. Set concurrency explicitly
and retain errors with `error_handling="log"`; ignoring errors would distort denominators.
The installed SDK supports repetitions and experiment metadata. [L2, L3]

The lowest-risk prototype of this option reads already saved runs. It exercises dataset and
experiment wiring without another provider call. The target returns only selected grade
fields. A separate local map resolves case IDs to trusted artifact directories; remote inputs
must not be allowed to select arbitrary filesystem paths.

<!-- code: Proposed SDK target for saved artifacts; makes LangSmith calls, no model calls -->
```python
from pathlib import Path
from langsmith import Client
from evals.network_capture.grade import grade_run

client = Client()
local_runs = {
    "NET-001": Path("runs/evals/network-capture/RUN_001"),
    "NET-002": Path("runs/evals/network-capture/RUN_002"),
    "NET-003": Path("runs/evals/network-capture/RUN_003"),
}


def target(inputs: dict) -> dict:
    case_id = inputs["case_id"]
    grade = grade_run(case_id, local_runs[case_id])
    return {
        "passed": grade["passed"],
        "verdict": grade["verdict"],
        "failure_category": grade["failure_category"],
        "failed_checks": grade["failed_checks"],
        "capture_recall": grade.get("capture_recall"),
    }


def capture_pass(outputs: dict) -> dict:
    return {"key": "capture_pass", "score": int(outputs["passed"])}


results = client.evaluate(
    target,
    data=client.list_examples(
        dataset_name="browser-network-capture-native-v1",
        as_of="REVIEWED_VERSION_TAG",
    ),
    evaluators=[capture_pass],
    experiment_prefix="saved-artifact-grade-v2",
    max_concurrency=0,
    num_repetitions=1,
    error_handling="log",
    metadata={"evaluation_layer": "saved_artifact_regrade"},
)
```

Create the native dataset separately with reviewed inputs/reference outputs. Dataset mutations
produce versions; select a fixed version or tag through `list_examples(as_of=...)`. Store the
selected version and local corpus hash with the experiment. The name and tag in the example
are placeholders. [L7, L8]

<!-- code: Proposed initial native dataset creation for Option A only -->
```python
from langsmith import Client
from evals.network_capture.dataset import load_dataset, load_requests

client = Client()
data = load_dataset()
dataset = client.create_dataset(
    dataset_name="browser-network-capture-native-v1",
    description="Reviewed network capture cases; native SDK evaluation.",
)
client.create_examples(
    dataset_id=dataset.id,
    examples=[{
        "inputs": {"case_id": case["id"]},
        "outputs": {
            "expected_request_count": len(load_requests(case["id"])),
        },
        "metadata": {"category": case["category"]},
    } for case in data["cases"]],
)
```

This initial-create snippet is not a repeatable synchronization script. A production sync
needs stable example IDs, content comparison and explicit handling of additions/deletions.
The external importer in Option B must use its own externally managed dataset identity.

For a future live target, wrap `AutonomousBrowserExplorer.explore()` in an async adapter,
persist artifacts, run the same grader locally, then return an allowlisted result. Start with
one trial at a time and a fresh browser context. A saved-artifact target's LangSmith duration
measures file reading/grading; use recorded application duration for browser-performance claims.

## Option C: pytest integration

LangSmith's pytest integration can synchronize marked tests to examples and experiments and
record assertion outcomes. It also supports explicit input, reference-output, actual-output
and feedback logging. This is useful for heterogeneous evaluation cases. [L9]

<!-- code: Proposed dedicated opt-in reporting test; do not add to tests/unit -->
```python
from pathlib import Path
import pytest
from langsmith import testing as t
from evals.network_capture.grade import grade_run


@pytest.mark.langsmith
def test_saved_network_capture(saved_run: tuple[str, Path]) -> None:
    case_id, directory = saved_run
    t.log_inputs({"case_id": case_id})
    t.log_reference_outputs({"capture_pass": True})
    report = grade_run(case_id, directory)
    t.log_outputs({
        "verdict": report["verdict"],
        "failed_checks": report["failed_checks"],
    })
    t.log_feedback(key="capture_pass", score=int(report["passed"]))
    assert report["passed"]
```

The `saved_run` fixture in this sketch must be implemented with a trusted case-to-directory
mapping. Use a dedicated opt-in reporting location, register the marker under strict pytest
configuration, and validate compatibility with the locked SDK. Ordinary unit tests must retain
their socket/DNS guards. Reporting from a marked unit test can conflict with those guards or
with worker initialization; it is not the recommended first integration here.

This approach also synchronizes test definitions into datasets as part of running tests.
That lifecycle requires care when the objective is a frozen comparison dataset. Use explicit
suite names and experiment metadata, and document when changes to parametrization create new
examples. Keep ReportPortal's unit-test dashboard distinct from LangSmith's capability scores.

## External-import rollout for separate datasets

The following sequence applies only if completed local evaluations are later published to a
separate externally managed dataset. It is not the sequence for the native capture dataset.

| Stage | Deliverable | Acceptance evidence |
| --- | --- | --- |
| 1. Export design | Versioned local manifest and strict field allowlist | Fixture and challenge payloads reviewed; no raw evidence or keys |
| 2. Offline adapter | Pure functions mapping grades and labels to import rows | Exact 3-case/30-challenge mappings, correct nulls and stable IDs |
| 3. Transport and receipt | Opt-in submission, stateful receipt and bounded read-back | Mocked failures, ambiguous-write handling and resume behavior |
| 4. Workspace acceptance | One scripted challenge experiment imported explicitly | 30 rows and matching per-row feedback read back; verified receipt |
| 5. Baseline comparison | Previous/current grader experiments under one external dataset | 7/7 controls in both; 9/23 versus 23/23 corruption rejection |
| 6. Live pilot | Five isolated trials per capture case/configuration | All planned attempts accounted for, failures visible, no hidden retries |

Put this adapter under `evals/` or `scripts/`, outside `src/browser_exploration/`. Follow the
ReportPortal pattern of explicit reporting and a durable verification receipt. Keep credentials
in operator configuration, and do not rely on the browser task's settings to authorize remote
reporting. The initial uploader should never execute the agent as a side effect of publication.

Before calling the integration reliable, demonstrate four failure paths: authentication denied
before upload, connection lost after a possibly successful write, partial/eventually consistent
read-back, and resuming an already verified receipt. The expected behavior is a precise receipt
and preserved local measurement, without duplicate execution or a false success message.

For the selected native capture dataset, first collect and preserve agent run results. Then
implement the deterministic evaluator for those saved runs and publish its per-case scores as a
LangSmith experiment. The grader-challenge dataset remains a separate later evaluation.

# References and reproducibility

## Local sources

The implementation descriptions come from `evals/network_capture/`, the shared test helpers,
`src/browser_exploration/observations.py`, `explorer.py`, `reporting.py`, and the testing and
contract documents. Current API signatures were inspected from the locked LangSmith 0.11.2
installation. The native dataset was created and read back successfully; no experiment results
or evaluation scores have been uploaded.

| Evidence | Location |
| --- | --- |
| Reviewed capture cases and budgets | evals/network_capture/cases.json |
| Request oracle | evals/network_capture/requests.csv |
| Reviewed challenge labels | evals/network_capture/grader_cases.json |
| Previous grader benchmark | runs/evals/grader-hardening/before-verified/benchmark.json |
| Current grader benchmark | runs/evals/grader-hardening/after-verified/benchmark.json |
| Scripted control directory map | runs/evals/grader-hardening/controls.json |
| Historical audit with derived-copy exclusion | runs/quality/current-audit.json |
| Verified ReportPortal receipt | runs/reportportal/20261025T200229-275c71e3/reportportal.json |
| Current testing and live commands | docs/browser_exploration/testing.md |
| Public task/result contract | docs/contracts/browser_exploration_v1.md |

Generated artifacts under `runs/` and `output/pdf/` are local, ignored files. A clean checkout
will contain the code and documentation but not these private run records. Preserve the
records with experiment receipts when sharing measured results within an approved destination.

## Official LangSmith sources

Sources checked October 26, 2026. Links are official documentation or official SDK reference.
The integration recommendation and rollout criteria are project-specific design conclusions.

[L1] Upload experiments run outside of LangSmith: https://docs.langchain.com/langsmith/upload-existing-experiments

[L2] Evaluate agents with the SDK: https://docs.langchain.com/langsmith/evaluate-llm-application

[L3] Native evaluation using the REST API: https://docs.langchain.com/langsmith/run-evals-api-only

[L4] Evaluation concepts and example/experiment definitions: https://docs.langchain.com/langsmith/evaluation-concepts

[L5] API keys and workspace configuration: https://docs.langchain.com/langsmith/create-account-api-key

[L6] Fetch experiment performance metrics: https://docs.langchain.com/langsmith/fetch-perf-metrics-experiment

[L7] Create and manage datasets programmatically: https://docs.langchain.com/langsmith/manage-datasets-programmatically

[L8] Dataset versions and fixed-version evaluation: https://docs.langchain.com/langsmith/manage-datasets

[L9] LangSmith pytest integration: https://docs.langchain.com/langsmith/pytest

[L10] Python Client.evaluate reference: https://reference.langchain.com/python/langsmith/client/Client/evaluate

[L11] LangSmith REST schema, Upload Experiment operation: https://api.smith.langchain.com/redoc#tag/datasets/operation/upload_experiment_api_v1_datasets_upload_experiment_post

## Reproduce the recorded local measurements

<!-- code: Read-only grading and audit commands -->
```bash
uv run python -m evals.run_quality \
  --runs-dir runs --output runs/quality/current-audit.json

uv run python -m evals.network_capture.grade \
  --case-id NET-003 \
  --run-dir runs/evals/grader-hardening/controls/20261025T195837-cc4290efc16e
```

The audit command writes a new report and leaves the source run artifacts intact. Its counts
will change when additional original runs are added. Historical comparisons in this handbook
refer to the dated reports above, with the corpus and control fingerprints they recorded.
