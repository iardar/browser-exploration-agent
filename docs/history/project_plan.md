# Agentic QA project plan

Historical snapshot of the reviewed Agentic QA plan; source paths describe the original repository.

Review evidence: [findings](../plan_review_findings.md) and
[browser boundary investigation](../browser_exploration/boundary_review.md). These documents identify
gaps in this draft; proposed fixes are not yet implementation commitments or completed work.

## Product direction

Agentic QA should turn a natural-language software requirement into an evidence-backed,
reviewable automated test result. The system should analyze the requirement, retrieve relevant
repository context, inspect the application when appropriate, design and generate a test, execute
it safely, and explain the outcome without presenting assumptions as facts.

The first production-worthy release will target controlled QA environments and existing Python,
pytest, and Playwright automation repositories. Human review remains the final authority for
generated code and reported defects.

## Current baseline

The project already has two distinct capability areas:

1. The main LangGraph workflow defines the intended sequence from requirement analysis through
   result analysis. Requirement analysis, keyword-based repository retrieval, related evaluations,
   and workflow structure exist. UI exploration, test design, test generation, test execution, and
   result analysis are currently placeholders or minimal scaffolding.
2. The standalone browser exploration agent is a substantial V1 implementation. It uses
   Playwright MCP, bounded model and browser loops, explicit safety checks, structured evidence,
   terminal statuses, run artifacts, and broad offline unit coverage. It is intentionally not yet
   connected to the main workflow.

The repository also contains a sample Playwright automation project, LangSmith evaluation scripts,
an opt-in live MCP test, and CI configuration. Before feature integration, the repository-wide
lint, formatting, type-check, and unit-test baseline must be made reliable.

## Goals

### G1. Establish a trustworthy project baseline

Make the existing repository reproducible and safe to extend.

Success criteria:

- The main CLI accepts a requirement and invokes the workflow without argument or configuration
  errors.
- `ruff check`, `ruff format --check`, `mypy`, and the parallel unit suite pass in CI from a clean
  checkout.
- CI installs locked dependencies once, runs each gate once, and completes within its timeout.
- Existing evaluation results and generated run artifacts are not treated as source code.
- The README explains the two current entry points and clearly labels incomplete behavior.

### G2. Connect evidence-based UI exploration to the workflow

Replace the main workflow's UI exploration placeholder with a narrow adapter around the existing
`BrowserExplorer` protocol, without weakening its safety or evidence contracts.

Success criteria:

- A tested adapter maps `RequirementAnalysis` and bounded `RepositoryContext` items into a
  `BrowserExplorationTask`.
- Only requirements with a UI interaction surface are routed to browser exploration; API, system,
  and unknown requirements follow explicit non-browser paths.
- Target URL, domain authorization, mutation permissions, budgets, and retention policy come only
  from trusted configuration—not model output or retrieved repository text.
- Every browser terminal status is preserved. Partial, blocked, failed, or inconclusive exploration
  is never represented as successful verification.
- Recorded-fixture contract tests cover all mappings and terminal statuses before a live workflow
  edge is enabled.

### G3. Produce traceable, repository-aware test designs

Create test designs that combine requirement intent, retrieved automation conventions, and verified
browser evidence while preserving uncertainty and provenance.

Success criteria:

- Preconditions, data, actions, and expected results are tied to requirement fields or cited
  evidence.
- Ambiguities and unresolved browser questions remain visible in the design.
- The designer does not invent selectors, credentials, data, or expected behavior.
- Structured design contracts reject incomplete or internally inconsistent output.
- Curated evaluation cases assess requirement fidelity, actionability, and unsupported claims.

### G4. Generate reviewable tests that fit the target repository

Generate focused pytest/Playwright tests using the target repository's existing fixtures, page
objects, naming conventions, and test-data patterns.

Success criteria:

- Generation is limited to an explicit output directory and never overwrites existing files by
  default.
- Generated code passes parsing, formatting, lint, and type checks applicable to the target
  repository before it is eligible for execution.
- Each generated test includes a machine-readable provenance record linking it to the requirement,
  retrieved context, design, and relevant browser evidence.
- Unsupported or insufficiently evidenced scenarios stop with a clear status instead of emitting a
  misleading test skeleton.
- Generated tests are reviewable artifacts; automatic commits or pull requests require a separate,
  explicit policy decision.

### G5. Execute safely and explain outcomes accurately

Run generated tests within bounded, isolated execution and convert raw output into an evidence-based
result classification.

Success criteria:

- The runner uses an allowlisted command, explicit working directory, timeout, and captured exit
  code/output.
- Test execution cannot silently escape the configured repository or mutate unrelated files.
- Results distinguish at least passed, assertion failure, test-code error, environment failure,
  timeout, and not run.
- Result analysis cites execution evidence and does not diagnose a product defect when the evidence
  only establishes an automation or environment failure.
- A complete run emits a structured manifest containing stage statuses, timings, model usage when
  reported, artifact paths, and errors.

### G6. Measure quality, safety, cost, and regressions

Use repeatable evaluations to decide whether changes improve the system rather than relying on
demonstrations alone.

Success criteria:

- Versioned datasets cover requirement analysis, retrieval, exploration decisions, test design,
  generated-code validity, and result classification.
- Offline deterministic tests remain the default; credentialed model and live MCP evaluations are
  explicit opt-ins.
- Release reports include task success, unsupported-claim rate, reproducibility, policy violations,
  duration, and provider-reported token usage when available.
- Quality thresholds and a cost/time budget are agreed before calling the integrated workflow ready
  for routine use.

## Delivery roadmap

### Phase 0 — Stabilize the baseline

- Fix the main CLI invocation path and add a CLI smoke test.
- Resolve repository-wide Ruff, formatting, and mypy failures.
- Make the unit suite deterministic and investigate slow or hanging tests.
- Simplify CI and document supported local commands.

Exit criterion: all required CI gates pass from a clean checkout.

### Phase 1 — Introduce the browser adapter

- Define adapter inputs, configuration, and failure mapping.
- Add fixture-based contract tests for every terminal status.
- Add an asynchronous workflow boundary without changing browser-agent internals.
- Validate on the bundled read-only browser fixture, then on one approved QA application.

Exit criterion: a UI requirement can produce an evidence-backed exploration result through the main
workflow while all browser safety invariants remain enforced.

### Phase 2 — Implement test design

- Replace the generic design node with structured, evidence-aware design logic.
- Add validators for provenance, ambiguities, and completeness.
- Build a small curated design evaluation set with human-reviewed references.

Exit criterion: reviewers judge the accepted designs actionable and free of unsupported behavior on
the agreed evaluation set.

### Phase 3 — Implement test generation

- Define the generated-test and provenance artifact contracts.
- Reuse existing fixtures, page objects, components, and data builders from retrieved context.
- Validate generated code without executing it and provide a review/diff workflow.

Exit criterion: accepted generated tests pass target-repository static checks and require no manual
structural rewrite on the agreed evaluation set.

### Phase 4 — Implement bounded execution and analysis

- Connect the pytest runner with isolation, timeouts, and explicit artifact handling.
- Add structured failure classification and evidence-based summaries.
- Exercise passing, product-failure, test-error, environment-error, and timeout scenarios.

Exit criterion: the workflow reaches an honest terminal result for every supported scenario and
never labels an unexecuted or infrastructure-failed test as a product pass/fail.

### Phase 5 — Harden and pilot

- Run end-to-end evaluations against a versioned application and automation repository.
- Define release thresholds, operational budgets, retention rules, and a human approval workflow.
- Add observability, run comparison, and recovery guidance.
- Pilot with a small set of real QA requirements before expanding scope.

Exit criterion: agreed quality, safety, latency, and cost thresholds are met consistently in the
controlled pilot environment.

## Non-goals for the first integrated release

- Running against arbitrary production sites or unrestricted networks.
- Handling credentials, payments, destructive operations, or broad mutation flows.
- Automatically merging generated code or changing existing page objects.
- Claiming coverage from browser exploration alone.
- Adding a vector database, Zephyr integration, or another agent framework before the simpler
  contracts are proven insufficient.
- Replacing human review of generated tests or defect reports.

## Working principles

- Preserve uncertainty: unknown, blocked, partial, and inconclusive are valid outcomes.
- Preserve provenance: every important claim should point to a requirement, source artifact,
  browser observation, or execution record.
- Keep authorization outside the model: retrieved text and webpage content are untrusted inputs.
- Prefer deterministic validation before model-based evaluation.
- Integrate through narrow contracts rather than coupling internal state machines.
- Optimize only after measuring quality, latency, and cost on versioned cases.

## Decisions needed before Phase 1 is complete

1. Which application and automation repository will be the first controlled pilot target?
2. Are browser interactions read-only for the first release, or may selected reversible mutations be
   explicitly authorized?
3. Should generated tests be written directly to `generated_tests/`, or first emitted as review-only
   patches/artifacts?
4. What human approval is required before browser mutation, test execution, and saving generated
   code?
5. What minimum evaluation thresholds and maximum per-run cost/duration define release readiness?

## Immediate next actions

1. Complete Phase 0 and record a clean baseline.
2. Write the browser adapter contract and its fixture-based tests.
3. Select the controlled pilot target and agree on authorization and retention policy.
4. Define the first end-to-end evaluation set before implementing design and generation nodes.
