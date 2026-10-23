# Project plan review findings

Review date: 2026-10-23. Scope: [the reviewed plan snapshot](history/project_plan.md), implementation,
offline evaluation probes, and saved browser runs. This records findings; it does not mark
the proposed fixes as implemented.

## Findings and recommended changes

| ID | Priority | Finding | Required plan change |
| --- | --- | --- | --- |
| F-01 | High | Observed behavior can become the source of an expected result. | Require an approved requirement or domain decision for each assertion. Use observations to describe actual behavior and available interactions. |
| F-02 | High | Browser `completed` means the model finished, without proving the objective. | Track objective checks independently from execution status and validate supporting evidence before downstream use. |
| F-03 | High | Existing evaluator checks can accept a contradiction. | Add negation, omitted conditions, unsupported additions, and human-reviewed semantic references. |
| F-04 | High | Relevant file snippets omit the symbols and setup needed to generate executable tests. | Define a context bundle containing complete relevant symbols, imports, fixture registration, and repository configuration. |
| F-05 | High | Workflow stage outcomes and continuation rules are undefined. | Define typed stage results and continue/skip/block/review rules before connecting browser exploration. |
| F-06 | High | Generated-code isolation has no specified implementation. | Choose and test filesystem, process, credential, network, and cleanup boundaries before executing generated Python. |
| F-07 | Medium | The first pilot and measurable release gates are unresolved. | Select one application/repository pair and a versioned benchmark before expanding implementation. |

## F-01 — Keep requirements authoritative

G3 permits expected results to reference requirement fields or evidence. A defective application
can supply valid observations of the wrong behavior. If those observations define the assertion,
the generated test can preserve the defect.

Expected outcomes must cite an approved requirement or an explicit reviewed decision. An observation
can establish that an element exists or an action occurred; it cannot establish that the observed
business behavior is correct. Evaluate generated tests against a correct fixture and a fixture with
a known defect. Static validity and a passing execution alone do not establish test usefulness.

## F-02 — Separate execution from objective fulfillment

`browser_exploration/graph.py` sets `status="completed"` when a validated decision says `done=True`.
Evidence acceptance checks successful evidence IDs and reproduction steps, but does not establish
that the evidence semantically supports a claim.

The saved run `20261022T215414-cf5704b6f96c` reports completion and five successful browser calls,
but has `verified_behaviors=[]` and an unsupported-evidence question. It demonstrates successful
actions, not independently verified objective fulfillment. The reporting issue is also documented
in [the model comparison](browser_exploration/google_model_comparison.md).

Define per-objective checks with `met`, `unmet`, and `unknown` outcomes in a host-owned assessment.
Preserve the node's original terminal status separately. Do not add unsupported fields to the V1
node result or silently reinterpret historical `completed` results.

## F-03 — Evaluator false positive reproduced

An offline probe used the existing `search_ui` case:

- Requirement: search results should update when a component name is typed into the Search field.
- Supplied conditions: `Components page`, `Search field`.
- Supplied outcome: `search results do not update`.
- Result: all four evaluator checks passed.

`evals/requirements/evaluator.py` checks substring presence; it does not account for negation.
The probe also supplied irrelevant feature, operation, and objective values, which these checks
did not evaluate. Existing scores therefore establish only their individual lexical checks.

## F-04 — Retrieval needs dependency context

The existing RealWorld login case retrieved all four expected paths in its top eight results.
However, it omitted `conftest.py`, `fixtures/authentication.py`, and `pages/base_page.py`. Its
`pages/login_page.py` snippet contained field locators but omitted `sign_in()`.

The keyword provider returns a short window around the first matching content line. Its evaluation
checks file paths rather than completeness of methods, imports, or fixture dependencies. An adapter
can safely pass those snippets to exploration, but generation needs a stronger context contract.
Also, placing tests in a sibling `generated_tests/` directory does not by itself make fixtures from
`sample_automation/conftest.py` available there.

The integration design mentions existing AST indexing, but the inspected provider implements
keyword scoring only. Do not base extraction or integration on the undocumented AST capability.

## F-05 — Define host orchestration contracts first

`QAState.exploration_findings` is `list[str]`, while the independent explorer returns a rich,
versioned result. The main graph currently follows unconditional edges through the remaining
placeholder stages.

The host adapter should own mappings and orchestration. It must preserve the complete node result,
distinguish run status from finding status, and apply explicit rules for missing context, material
ambiguities, partial exploration, cancellation, and unsupported interaction surfaces. Human review
and persistence policies must be decided before downstream code execution is enabled.

## F-06 — Specify execution and deployment boundaries

An allowlisted pytest command and working directory do not confine the Python executed by tests,
plugins, imports, and fixtures. Define the execution environment before implementing the runner.

Browser network restrictions are also external deployment responsibilities. The browser's
`network_restricted` setting is an attestation, not an egress firewall. The original `.env.example`
sets that attestation to `true` despite comments describing an explicit prerequisite. A standalone
distribution should ship it as `false`.

## F-07 — Make the pilot concrete

Choose an application version, automation-repository revision, fixture state, permitted actions,
and a small set of reviewed requirements. Include success, known product defects, unavailable
dependencies, ambiguity, and unsupported requests. Define measures and thresholds before building
more stages; repeatability, assertion usefulness, false claims, latency, and measured model usage
need separate assessment.

## Proposed delivery order

1. Establish reproducible offline checks and record current limitations.
2. Select the pilot target and write its benchmark and expected outcomes.
3. Define the browser node boundary and host adapter contract.
4. Prove one complete requirement-to-test scenario against correct and defective fixtures.
5. Expand capabilities using the measured results.

## Validation evidence and limits

- Four focused retrieval/evaluator unit tests passed in the review environment.
- Repository-wide Ruff reported 14 findings; formatting reported 27 files needing changes.
- Unit collection found 451 cases; collection is not evidence that those cases pass.
- A fresh non-incremental mypy run exceeded a 35-second bound without completing.
- Saved artifacts were inspected; no new live browser or model evaluation was run.

The review did not alter runtime behavior or resolve the findings above. Extraction into a
standalone repository must preserve that distinction and carry these limitations into its contract.
