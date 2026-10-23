# Browser exploration boundary investigation

Investigation date: 2026-10-23. This describes the inspected V1 implementation and the
extraction boundary, not a claim that all planned integration behavior is implemented.

## 1. Extraction decision

Extract the browser capability as the `browser-exploration-agent` distribution with Python
package `browser_exploration`. Preserve the async `BrowserExplorer` protocol and V1 task/result
schemas. Keep parent-specific adapters in their consuming application. The original Agentic QA
source remains available as the extraction reference.

The capability is a service with its own three-node LangGraph, rather than a single imported graph
node. A host can wrap that service in one of its own workflow nodes. It must not import the
service's internal graph state or combine the two state machines.

## 2. Dependency boundary

All 16 implementation modules in `src/browser_exploration/` use local relative imports.
No implementation import reaches `agentic_qa.graph`, `requirements`, `retrieval`, `models`, or
`tools`. The remaining parent namespace appears in the package location and CLI logger name;
tests and examples import that namespace explicitly. Those references can be renamed mechanically.

| Included in the standalone repository | Excluded |
| --- | --- |
| Browser contracts, settings, graph, application service | Main Agentic QA workflow and `QAState` |
| Context selection, observations, model adapter, prompts | Requirement analysis and repository retrieval |
| MCP session, discovery, tool policy | Test design, code generation, pytest execution |
| Artifact storage, redaction, telemetry, CLI | Sample RealWorld automation and unrelated evaluations |
| Browser unit tests and opt-in MCP smoke test | API keys, `.env`, saved runs, caches, browser profiles |
| Static fixture, mock demonstration, browser schemas and guides | Parent Git metadata and local credentials |

Direct runtime dependencies are LangGraph, LangChain Core, LangChain OpenAI, LangChain MCP
Adapters, Pydantic, Pydantic Settings, LangSmith, MCP SDK, AnyIO, python-dotenv, and Typer.
The Python Playwright package is not used by the agent. A compatible Playwright browser runs on
the independently operated MCP server. Runtime imports use `anyio` directly and should declare
it directly even though it was previously available transitively.

## 3. Application and host responsibilities

The node owns bounded exploration, tool authorization, observations, status, and artifacts.
The host owns requirement interpretation, application selection, authorization, approval policy,
objective assessment, and downstream decisions.

The public boundary is:

```python
class BrowserExplorer(Protocol):
    async def explore(self, task: BrowserExplorationTask) -> BrowserExplorationResult: ...
```

An adapter maps host records to the public task and preserves the returned result intact. It
must derive destinations and permissions from trusted deployment configuration. Requirement text,
repository snippets, webpage content, and model decisions cannot grant authority.

`BrowserExplorationTask` and `Settings` are separate contracts. Direct Python callers must set
task budgets themselves; settings defaults are mapped into tasks by the CLI, not automatically
by `AutonomousBrowserExplorer.explore`. `.env` loading is also a CLI responsibility. A host should
construct explicit run-local settings and tasks instead of relying on ambient configuration.

## 4. Browser and network boundary

One initialized Streamable HTTP MCP session lasts for the entire run. Navigation and accessibility
snapshot tools are required. Optional discovered tools include hover, click, typing, form filling,
screenshots, console messages, and network requests. Exact known aliases and compatible argument
schemas determine capabilities; arbitrary server tools are not offered to the decision model.

The catalog excludes arbitrary JavaScript, uploads, downloads/file-management tools, and credential
management. Defaults permit observed link navigation and inspections. Generic clicks, form input,
and form submission need separate explicit task permissions. Even with those permissions, known
authentication, payment, sensitive, destructive, and publishing controls remain denied.

Client policy checks exact hostnames, URL schemes, observed references, recognizable private
destinations, metadata endpoints, and some English route/control labels. It does not implement:

- DNS resolution checks, DNS-rebinding prevention, or browser egress filtering.
- Prevention of redirected requests, subresources, page scripts, or browser-initiated traffic.
- Application-specific path/port authorization or proof that a click/GET is read-only.
- A complete classifier for localized or misleading control labels.

Exact hostname authorization does not independently restrict ports or application routes.
`network_restricted=true` attests to controls installed outside the client. Private network access
has a separate opt-in. An MCP server/browser should be dedicated or isolated; the Python client
does not provision it or establish isolation from another user's browser session.

## 5. Model and context boundary

The model selects one structured action or completion. The executor revalidates decisions and
tool arguments. The model cannot edit budgets or permissions. Page text and context snippets are
untrusted data; prompt instructions are supplemented by executor policy.

Each decision receives newly selected messages: objective/restrictions first, then location,
executor feedback, action progress, bounded observations, findings/questions, and supplementary
context. Character bounds are not token bounds. The tool catalog adds up to 32,000 characters
beyond the task's message-content budget.

Only text reaches the decision model. Screenshots can be retained, but pixels are not inspected.
Snapshot compaction and truncation can omit needed content. There is no semantic summarizer or
guarantee that a relevant portion survives. Browser observations are not an authoritative source
of expected business behavior.

## 6. Lifecycle and resource boundary

Bootstrap navigation and snapshot consume two browser calls. Tasks permitting fewer calls can
legitimately return a limit before reaching a model decision. Browser calls, model iterations,
wall-clock time, and individual tool timeouts are independently bounded.

Malformed model decisions get up to two consecutive correction requests within the existing
budgets. Policy-denied proposals may consume the remaining model budget. There is no additional
no-progress termination rule. Browser actions are not automatically replayed after failure or a
lost session. A timed-out/cancelled operation may have happened remotely; its outcome is unknown.

Run statuses are `completed`, `step_limit`, `model_limit`, `timeout`, `cancelled`, `blocked`, and
`failed`. Finding statuses are separately `confirmed`, `potential`, and `inconclusive`.
`inconclusive` is not a V1 run status. `completed` records model-directed termination, not proof
that the objective or all expected outcomes were satisfied.

The task is copied and revalidated at entry. Settings and injected dependencies are not a proven
concurrent-use boundary; create separate run-local dependencies unless sharing is explicitly
validated. There is no resume/checkpoint persistence API or host workflow cancellation protocol
beyond cancelling the async call. The service converts caught cancellation into a `cancelled`
result, so a host must handle that returned result explicitly.

## 7. Evidence and correctness boundary

The executor records observations, action metadata, errors, and evidence references. Successful
non-screenshot evidence IDs are eligible to support model-reported behavior. Unknown/failed IDs
and missing reproduction steps cause certain findings to be downgraded. This establishes
provenance and structural validity, not semantic entailment.

A model may cite a successful initial snapshot for a later action it did not execute. Conversely,
a screenshot ID can invalidate a combined behavior statement even when search and capture really
succeeded. Capture completion is not represented by a separate executor-owned result field.

The host must assess objective fulfillment independently and keep expected versus observed
behavior separate. Neither a successful browser call nor a `confirmed` model finding is by itself
an independently verified product-test verdict.

## 8. Artifacts, privacy, and exception boundary

The service writes a unique run directory containing `run_config.json`, `decisions.jsonl`,
`trajectory.jsonl`, `result.json`, and `metrics.json`. Raw evidence retention is off by default.
Optional raw text/binary files are separate from JSON results and model context. Bounded summaries
and redacted action metadata remain in the trajectory even when raw retention is disabled.

Private file permissions, safe identifiers, and selected symlink/path protections exist. Pattern
redaction is best effort; screenshots cannot be text-redacted. MCP server profiles, snapshots,
downloads, and logs have a separate retention boundary. Enabling LangSmith can send approved
context to an additional service. Retention and provider policy need host/deployment decisions.

Operational failures inside the guarded run generally produce structured results. However, task
revalidation, artifact-store construction/config writes, and final artifact writes can raise before
or outside that guard. Therefore the API is not an unconditional "never raises" boundary. The
adapter must handle validation and storage exceptions and preserve an integration error without
inventing a successful node result.

## 9. Extraction acceptance checks

1. Install/import the extracted package without the original repository on the import path.
2. Enforce no parent-workflow imports in production modules.
3. Run the copied offline browser tests using their fake model/MCP dependencies.
4. Preserve public task/result schemas and test unknown-field rejection and status distinctions.
5. Build an independently named package and retain the `qa-browser` CLI.
6. Ship an empty credential template with network attestation disabled.
7. Keep live MCP/model probes opt-in and record verification limits honestly.
8. Document the host-owned adapter and publish a reusable contract template.

## 10. Findings carried forward

The extraction does not resolve semantic evidence validation, objective completion, no-progress
termination, external egress enforcement, or unconditional artifact persistence. These remain
explicit limitations. See [plan review findings](../plan_review_findings.md) for the broader
workflow risks and the standalone repository's integration contract for consumer obligations.
