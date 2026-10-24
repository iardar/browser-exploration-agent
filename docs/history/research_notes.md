# Historical research and validation notes

These records describe the original Agentic QA workspace on October 20-23, 2026, before the
standalone extraction. They preserve useful evidence and design lessons, not current test counts,
provider availability, model rankings, or a roadmap for this repository. Saved browser runs and
the parent application are not included here. No new live evaluation was performed for this
documentation cleanup.

Use the [architecture](../architecture.md), [V1 contract](../contracts/browser_exploration_v1.md),
[adapter design](../adapter_design.md), and [testing guide](../browser_exploration/testing.md) for
maintained guidance. The [extraction record](../extraction.md) describes standalone verification.

## Parent workflow review, October 23

The original plan covered requirement analysis, retrieval, browser exploration, test design,
generation, execution, and result analysis. Its six goals were a reproducible baseline, a browser
adapter, traceable designs, repository-compatible generated tests, isolated execution, and measured
quality. Its phased roadmap and generated-test delivery decisions belong to the parent project;
they are not implementation commitments for the browser package.

| Finding | Evidence recorded at review | Lesson retained |
| --- | --- | --- |
| F-01: observed behavior could define an expectation | The plan allowed expected results to derive from requirement fields or observed evidence. A defective application could therefore become its own oracle. | Use approved requirements/domain decisions for expected behavior; test assessments on correct and known-defective fixtures. |
| F-02: completion did not prove the objective | Run `20261022T215414-cf5704b6f96c` completed with five successful calls but no accepted verified behaviors and an unsupported-evidence question. | Preserve execution status separately from host-owned `met`, `unmet`, and `unknown` objective checks. |
| F-03: lexical evaluation accepted a contradiction | In the parent `search_ui` case, the outcome `search results do not update` passed all four checks for a requirement that results should update. Irrelevant feature, operation, and objective values were not evaluated. | Substring scores do not establish semantic correctness. Include negation, omitted conditions, unsupported additions, and reviewed references. |
| F-04: retrieval omitted executable context | The RealWorld login case retrieved all four expected paths in its top eight, but omitted `conftest.py`, `fixtures/authentication.py`, `pages/base_page.py`, and the `sign_in()` method body. | Relevant file paths are not a complete generation context. A host generator needs full relevant symbols, imports, fixtures, and configuration. |
| F-05: host continuation was undefined | `QAState.exploration_findings` was `list[str]`; the browser returned a richer result. The parent graph used unconditional placeholder-stage edges. | An external adapter must retain the full result and explicitly route unsupported, partial, blocked, cancelled, and review outcomes. |
| F-06: generated-code isolation was unspecified | An allowlisted pytest command and working directory did not confine imports, fixtures, or plugins. The original environment template also pre-enabled the network attestation. | The host owns execution isolation. The standalone template ships the attestation disabled; the flag never installs a firewall. |
| F-07: no concrete pilot or release gates | Application/repository versions, fixture state, reviewed requirements, and quality/cost/latency thresholds were unresolved. | Define versioned evaluation criteria before claiming readiness or model quality. |

The parent retriever used keyword scoring and short first-match windows; the inspected provider
did not implement the proposed AST indexing. Tests in a sibling generated directory would not
automatically inherit fixtures from `sample_automation/conftest.py`.

For the F-03 probe, the supplied conditions were `Components page` and `Search field`; the parent
`evals/requirements/evaluator.py` used substring checks and did not account for negation. For
F-04, the locator-only snippet was from `pages/login_page.py`, omitting its `sign_in()` body.
The host execution review called for explicit filesystem, process, credential, network, and
cleanup boundaries, not only a constrained pytest command.

Four focused evaluator/retrieval tests passed during that review. Parent-wide Ruff reported 14
findings, formatting identified 27 files, collection found 451 tests without establishing a pass,
and a fresh mypy run exceeded 35 seconds. These are parent-workspace observations, not outstanding
failures in the standalone package.

## Historical implementation checks

| Phase | Recorded result and scope |
| --- | --- |
| Initial implementation, October 20, parent commit `fea633c` | 159 passed, 1 live test skipped; scoped lint/format and mypy passed, covering 31 Python files. Parent-wide checks still had 13 lint findings, 27 formatting differences, and 2 evaluator callback type errors. |
| Offline expansion, October 21 | Browser cases increased from 153 to 370, including 217 new boundary/failure cases. The parent default suite had 376 passing tests and 1 live test skipped. |
| Coverage at that October 21 snapshot | Line coverage 94.69% (1053/1112) -> 99.91% (1112/1113); branch coverage 85.62% (274/320) -> 99.69% (319/320); combined coverage 92.67% -> 99.86%. These are not current coverage measurements or a CI threshold. |
| Reference/observation fixes | 384 browser tests passed; 70 focused reference/safety/graph tests passed after type cleanup. Scoped mypy passed on 15 modules; dependency-following mypy exceeded 45 seconds. |
| Decision recovery and compaction | 394 browser tests passed; lint/format and scoped mypy on 16 modules passed. |
| Provider diagnostics | 407 browser tests passed; 25 focused model/diagnostic tests passed after SDK fixture refinement; lint/format and scoped mypy passed. |

The old coverage gap was a defensive minimum-context branch in `ContextBuilder.build`, beneath the
task contract's minimum allowance. Coverage was not inflated by bypassing task validation just to
reach it. Source coverage never established application functional coverage or action safety.

The original real MCP smoke test passed with Microsoft Playwright MCP 0.0.83, navigating and
reading both fixture pages through one session. The recorded Python stack included Python 3.12.14,
LangGraph 1.2.11, LangChain Core 1.6.1, langchain-openai 1.6.0, langchain-mcp-adapters 0.2.2,
MCP SDK 1.30.0, Ruff 0.16.5, mypy 2.3.1, and pytest 9.1.1. MCP 2.x was incompatible with the
adapter imports; that motivated the retained SDK `<2` boundary.

The smoke setup exposed host-header validation, a missing browser, and read-only cache paths.
Explicit `localhost:8931,127.0.0.1:8931` allowed hosts, a compatible installed browser, writable
caches, and a restarted static server resolved those setup failures. The recorded environment used
Chrome Headless Shell 155.0.8059.12, Playwright revision 1247, with a fixture-only `--no-sandbox`
exception. That exception is not a general deployment recommendation. An upstream deprecated
transport-alias warning did not fail that historical smoke test.

The original offline demo completed with three synthetic browser calls and two model decisions,
without invented token usage. Initial live model availability was unverified; an external article
attempt was blocked by absent network attestation. Docker configuration was inspected, but no
container runtime success was established. Original ignored artifact identifiers were
`20261020T200559-e387fa36fc6e` (mock) and `20261020T194022-af2f3882b3f6` (blocked).

## Browser experiments, October 22

These reviews inspected previously saved Google runs. They are diagnostic observations from a
changing public page, not a versioned controlled benchmark. Locale and consent state affect the
page. Old local environment settings cannot establish current settings or recover missing
historical model attribution.

### Batch 1: reference-format rejection, 21:10-21:14 UTC

| Start / run suffix | Status | Proposed target | Model calls | Tokens |
| --- | --- | --- | --- | --- |
| 21:10:23 `8180d019d9ab` | model_limit | CSS-like selector | 5 | 19,167 |
| 21:10:47 `9a5f40ccac8a` | model_limit | CSS-like selector | 5 | 19,330 |
| 21:11:08 `3b393cd798a1` | model_limit | `ref=e43` | 5 | 19,014 |
| 21:12:28 `d992fbfcc3b2` | model_limit | `ref=e43` | 5 | 19,023 |
| 21:12:50 `40132968d898` | completed | `e43` | 3 | 13,474 |
| 21:13:12 `093b77ba72d7` | model_limit | `#e43` | 5 | 19,237 |
| 21:14:09 `99995208b264` | model_limit | `ref=e43` | 5 | 19,351 |
| 21:14:25 `896dcbd2964e` | model_limit | CSS-like selector | 5 | 19,200 |
| 21:14:43 `8807dd7b01e3` | model_limit | `ref=e43` | 5 | 19,014 |

One of nine completed (11.1%); 43 decisions used 166,810 tokens. Forty decisions proposed rejected
typing actions; the eight unsuccessful runs used 153,336 tokens and executed only navigation and
the initial snapshot, with no browser tool failures. The snapshots exposed the Armenian search
combobox `Գտնել` with reference `e43`. Selectors and wrappers were rejected by the old exact-format
rule before any search action.

No configured/provider model identifiers were retained in this batch, and repeated rejected
proposals were deduplicated in unresolved questions. A precise per-iteration sequence or model
ranking cannot be reconstructed.

The successful run `20261022T211250-40132968d898` submitted `Armavir` and captured a screenshot.
Its interaction returned a
results URL/title and snapshot-file link, not inline results content. Pixels were not available
to the model, and the accepted verified-behavior list was empty. The screenshot showed results
to the reviewer; it did not establish model inspection. Run `9a5f40ccac8a` separately claimed
typing worked using only the initial snapshot, illustrating why valid IDs are not semantic proof.

The resulting fixes added bare-reference hints, exact observed-wrapper normalization, current
reference examples, prioritized bounded executor feedback, automatic inline snapshots, and
configuration/decision logs. The repeat-inspection guard alone could not fix a loop in which no
post-bootstrap action ever succeeded.

### Batch 2: invalid model decisions and attribution, 21:31-21:37 UTC

All nine saved configurations selected `gpt-4.1-mini`, contrary to the supplied comparison labels.
The following labels identify the original groups only, not different executed models. Provider
response model names were absent. Group means include failed runs, which can lower averages.

| Supplied label | Run suffixes | Completed | Screenshot | Mean seconds | Mean tokens |
| --- | --- | --- | --- | --- | --- |
| gpt-5.6-luna | `1667aff87416`, `628d725032ac`, `7dcf2033fefc` | 3/3 | 3/3 | 14.47 | 22,260 |
| gpt-4.1-mini | `0caca61a3fbb`, `e14fd7d18e3a`, `bb2369798557` | 1/3 | 2/3 | 11.06 | 15,168 |
| gpt-5.6-sol | `be75705044e4`, `6c0234ece287`, `2f7903b36cee` | 2/3 | 3/3 | 15.01 | 20,748 |

Six completed, eight reached capture, all 43 browser calls succeeded, and no action was rejected.
Total model usage was 174,526 tokens. Run `20261022T213348-0caca61a3fbb` failed on its first
decision after setup. Runs `20261022T213434-bb2369798557` and `20261022T213626-6c0234ece287`
failed on the third decision after search, snapshot, and capture. Raw parsing failures were not
logged, so exact violations cannot be recovered. One successful sequence typed without submission,
then filled/submitted the same query; that was an extra interaction, not an endless loop.

In a representative 6,000-character results summary, 3,336 characters were `/url:` lines; the
generic results heading began at character 5,636 and result details were omitted. Five completed
runs cited screenshot evidence for behavior, causing unsupported-evidence warnings because the
model had not seen the pixels.

Changes added explicit model selection, provider model metadata, safe schema diagnostics, up to
two correction requests without action replay, snapshot query compaction, and relative-URL query
redaction. An offline selection check exposed the first result and Key Facts within the same cap;
it did not establish a new live success rate.

### Attributable baseline, 21:53-21:54 UTC

These runs explicitly selected `gpt-4.1-mini` and recorded provider model
`gpt-4.1-mini-2025-04-14` consistently.

| Run suffix | Status | Seconds | Model calls | Browser calls | Tokens |
| --- | --- | --- | --- | --- | --- |
| `f0a906e6c88e` | completed | 16.22 | 3 | 5 | 18,575 |
| `6106e573a01d` | completed after recovery | 14.96 | 5 | 5 | 33,790 |
| `cf5704b6f96c` | completed | 13.07 | 3 | 5 | 18,550 |

All searched, fetched an inline snapshot including the first result/Key Facts, and captured a
screenshot. All 15 browser calls succeeded. Total usage was 70,915 tokens; mean duration 14.75
seconds and mean usage 23,638 tokens. Three trials do not establish general reliability.

Run `6106e573a01d` submitted the query, captured the page, then hit a root-level Decision error
(`location=[]`, `type=value_error`). Its fourth decision proposed a redundant snapshot, rejected
before execution; its fifth completed at the final allowed iteration. The precise failed
validator was not retained. The two recovery decisions cost another 15,190 tokens without
duplicating a browser action.

All three results rejected capture-completion claims using screenshot IDs. In `cf5704b6f96c`, one
combined search/inspection/capture claim cited three IDs; the screenshot ID caused rejection of
the whole behavior, leaving `verified_behaviors=[]` despite successful execution. Distinct
executor-owned capture reporting, semantic objective checks, and rule-specific safe diagnostics
remain useful design directions rather than evidence that V1 already supplies them.

### Explicit model override, 21:55-21:56 UTC

Runs `20261022T215525-57b03c4fdd51` and `20261022T215545-e5d27ad89eb6` recorded the explicit
`gpt-5.6-luna` configuration, then failed their first API request after browser setup. The provider
cause was not saved, so neither model access nor a particular HTTP/parameter cause was established.
Run `20261022T215639-500eac2650cc` omitted the override and selected/received `gpt-4.1-mini`.

The configuration confusion was between the parent workflow's `LLM_MODEL` and the browser's
`OPENAI_MODEL`; they control different applications. Later local edits were not a portable
configuration guarantee. A restricted-environment diagnostic encountered a provider connection
error, which did not establish the user's account access. The resulting browser-free check-model
command and safe provider diagnostics now make those failures easier to distinguish without
silent model fallback.

## Preserved sources

This record consolidates the original plan review, implementation/testing reports, and browser
experiment analyses. Their complete pre-cleanup text remains in Git at revision `3d20988` under
the former `docs/plan_review_findings.md`, `docs/history/project_plan.md`, and
`docs/browser_exploration/{implementation_report,testing,google_run_analysis,google_model_comparison}.md`
paths. For example:

```bash
git show 3d20988:docs/browser_exploration/google_model_comparison.md
```

Operational instructions now live in the operating/testing guides; implementation and integration
decisions live in the architecture and adapter documents. Historical counts and mutable-site
experiments must not be presented as current acceptance criteria or as model recommendations.
