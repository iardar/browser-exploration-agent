# Browser exploration V1 integration contract

| Property | Value |
| --- | --- |
| Contract ID | `browser-exploration/v1` |
| Document revision | 1, 2026-10-23 |
| Distribution | `browser-exploration-agent` |
| Python package | `browser_exploration` |
| Package version at extraction | `0.1.0` |
| Result schema version | `1.0` |
| Python runtime | 3.12 |
| Interface | In-process asynchronous Python protocol |
| State | Describes implemented V1 behavior plus explicit consumer requirements |

## 1. Purpose and interpretation

The node explores an explicitly authorized web target through Playwright MCP, records actions and
observations, and returns structured findings and execution information. A consuming application
may expose it as one node in a larger workflow.

This document separates **implemented behavior**, **consumer requirements**, and **future work**.
Consumer requirements describe what an adapter must enforce; they do not imply the browser package
already implements host orchestration or independent semantic verification.

The public Pydantic models and exported JSON schemas are authoritative for accepted field names
and validation constraints. This document explains their meaning and operational limits. Examples
are synthetic and do not establish live service availability or product correctness.

## 2. Public application interface

```python
from browser_exploration import (
    BrowserExplorationResult,
    BrowserExplorationTask,
    BrowserExplorer,
)
from browser_exploration.explorer import AutonomousBrowserExplorer

# Public structural interface:
# async def explore(task: BrowserExplorationTask) -> BrowserExplorationResult
```

Use `BrowserExplorer` for dependency injection. `AutonomousBrowserExplorer` is the default
implementation. Its settings, model, and MCP connection may be injected for tests or deployment.
Internal `ExplorationState`, graph nodes, tool aliases, prompt text, and artifact-writer internals
are implementation details, not host integration contracts.

There is no HTTP API, remote job queue, public resume endpoint, or distributed workflow protocol.
An HTTP/service adapter would require its own authentication, serialization, cancellation, and
artifact-access contract.

## 3. Responsibility boundary

| Concern | Browser package | Host adapter/application | Deployment operator |
| --- | --- | --- | --- |
| Requirement parsing and expected outcomes | Receives optional text as context | Owns interpretation and approved assertions | Provides approved data |
| Application and starting URL | Validates proposed URL | Selects explicit trusted target | Operates authorized application |
| Repository retrieval | Receives bounded snippets | Retrieves and selects source-tagged context | Controls repository access |
| Permissions and budgets | Enforces configured client policy | Sets policy from trusted configuration | Enforces external controls |
| Browser execution | Calls discovered MCP tools | Invokes the public service | Runs MCP and compatible browser |
| Objective fulfillment | Supplies observations and model claims | Independently assesses criteria | Supplies known fixture state |
| Expected versus actual behavior | Reports observations and claims | Keeps approved oracle separate | Operates correct/defective evaluation fixtures |
| Artifacts | Writes run-local artifacts | Links run to parent records and handles failures | Controls storage lifecycle and server artifacts |
| Test generation and execution | Outside this package | Owns downstream stages and isolation | Provides execution environment |

The host must not import the browser package's internal graph state. The browser core must not
import a host workflow, requirement model, retriever, or test runner.

## 4. Trust and authority

Trusted configuration supplies the target, exact allowed domains, permissions, budgets, MCP
endpoint, output root, retention, and tracing policy. The host should snapshot these values per
run. Natural-language objectives do not grant additional permissions.

Requirement text, repository snippets, page content, tool descriptions, and model proposals are
untrusted inputs. Their presence cannot authorize destinations, credentials, mutation, uploads,
filesystem access, code execution, or larger budgets. Labels and tool annotations cannot prove an
operation harmless.

The operator must enforce browser egress independently. `BROWSER_NETWORK_RESTRICTED=true` is an
attestation of those controls. It does not install or verify a firewall. Use a controlled target
and a dedicated/isolated browser session. The Python package does not provision either.

## 5. Task schema

Unknown fields are rejected. Pydantic applies its normal validation/coercion; V1 is not globally
configured for strict primitive types. A task is revalidated and copied on service entry.

| Field | Type / default | Validation and meaning |
| --- | --- | --- |
| `url` | required string | 1–8,192 chars; absolute HTTP(S); no embedded credentials, control characters, or backslashes |
| `objective` | required string | 1–100,000 chars; stripped, non-whitespace; must fit complete mandatory context |
| `requirement_text` | string or null / null | Up to 1,000,000 chars; optional untrusted reference text |
| `context_snippets` | list / empty | At most 100 `ContextSnippet` records |
| `allowed_domains` | list of strings / empty | At most 100 exact normalized hostnames; no wildcard or scheme/path |
| `max_steps` | integer / 30 | 1–1,000 attempted browser calls, including bootstrap |
| `max_model_iterations` | integer / 20 | 1–1,000 model requests, including correction requests |
| `max_duration_seconds` | integer / 120 | 1–86,400 seconds; bounded exploration lifecycle |
| `max_recent_observations` | integer / 5 | 1–100 observations considered in each decision context |
| `max_observation_chars` | integer / 6,000 | 1–1,000,000 chars per bounded observation/context entry |
| `max_context_chars` | integer / 18,000 | 4,096–2,000,000 chars for selected message contents |
| `allow_mutations` | boolean / false | Allows certain observed generic clicks; does not remove other denials |
| `allow_form_input` | boolean / false | Allows bounded input in observed non-sensitive fields |
| `allow_form_submission` | boolean / false | Allows submission via Enter or observed submission/search controls; implies input permission |
| `allow_network_details` | boolean / false | Allows inspection of selected network request headers and bodies under the restrictions below |

An empty `allowed_domains` list defaults to the target hostname. Consumers should provide an
explicit list from their own authorization policy. Exact hostname matching does not restrict all
ports or application paths; that requires deployment/application policy.

`ContextSnippet` has `source` (1–1,000 chars) and `text` (1–1,000,000 chars). `source` is provenance,
not an instruction or filesystem access grant. Supplementary context can be omitted by context
selection when higher-priority content consumes the budget.

## 6. Settings and environment

`Settings` is independent from task data. It includes the model and API mode, optional reasoning
effort, MCP URL, output directory, retention, network attestations, individual tool timeout,
budget defaults, and tracing opt-in. The CLI maps budget defaults into the task explicitly.
Direct Python callers must do that mapping themselves if desired.

| Setting/environment | Implemented default / boundary |
| --- | --- |
| `OPENAI_MODEL` / `BROWSER_MODEL` | `gpt-4.1-mini`; account availability requires a real probe |
| `BROWSER_MODEL_API` | `auto`; model-specific routing is implementation configuration, not an availability promise |
| `BROWSER_REASONING_EFFORT` | Unset; effective value depends on model configuration |
| `PLAYWRIGHT_MCP_URL` / `BROWSER_MCP_URL` | `http://127.0.0.1:8931/mcp` |
| MCP URL validation | HTTP only for loopback; otherwise HTTPS; no credentials, query, or fragment |
| `BROWSER_OUTPUT_DIR` | `runs`; host selects and controls the output root |
| `BROWSER_TOOL_TIMEOUT_SECONDS` | 20; greater than zero, at most 3,600 |
| `BROWSER_RETAIN_RAW_EVIDENCE` | false |
| `BROWSER_NETWORK_RESTRICTED` | false; required operator attestation |
| `BROWSER_ALLOW_PRIVATE_NETWORK` | false; independent explicit opt-in |
| `BROWSER_LANGSMITH_ENABLED` | false; selected context may be sent to tracing service if enabled |

`OPENAI_API_KEY` is read by the model SDK, not included in a task or result. The CLI loads `.env`
without replacing exported values. Direct settings construction does not load `.env` automatically.
The separate parent workflow's `LLM_MODEL` variable has no meaning in this distribution.

## 7. Supported capabilities and restrictions

| Capability | Availability | Permission / restriction |
| --- | --- | --- |
| Navigation | Required | Validated authorized destination; known unsafe routes denied |
| Accessibility snapshot | Required | URL and usable content must be present |
| Hover | Optional | Observed reference and control checks |
| Click | Optional | Default: observed link converted to validated navigation; generic clicks need permission |
| Type | Optional | Input permission, observed reference, bounded non-sensitive text; separate submit permission |
| Fill form | Optional | Input permission; 1–20 observed fields; bounded values and allowed field types |
| Screenshot | Optional | Supported schema; binary artifacts excluded from model context |
| Console/network inspection | Optional | Supported known aliases and bounded context selection |
| Network request details | Optional | Explicit task permission, an index from the latest listing, verified request identity and supported part |
| JavaScript/code execution | Excluded | No arbitrary execution tool exposed |
| Uploads/credential management | Excluded | Not enabled by mutation flags |

Observed references come from the current accessibility snapshot. The executor accepts a bare
reference or a limited wrapper that exactly names an observed reference; it does not infer CSS,
XPath, or a label-based selector. Page scripts can change state outside these references, so
reference validation is not a general proof of browser-state freshness.

Even explicit permissions do not permit known authentication, payment, destructive, credential,
or publishing controls. Label checks are heuristic and incomplete, including across languages.
Form typing/filling are bounded to 2,000 characters per text value. No mutation permission proves
that a generic click is harmless.

Request detail inspection is independent of mutation and form permissions. It requires an index
shown in the latest network summary and an allowed request URL. The request overview must match
the listing's index, method and URL before its individual parts can be requested. Response body
inspection additionally requires a recognized textual response MIME type. Indices are invalidated
after a new listing or an agent interaction that can change the page. These checks cannot prove
that an asynchronously changing server has kept request indices stable. Detail text may be sent
to the model; recognized secrets are redacted on a best-effort basis and raw retention is separate.
MCP servers may independently write files, including for binary bodies, on their own host.

## 8. Lifecycle and resource accounting

1. Revalidate/copy the task and initialize the artifact store.
2. Validate target, mandatory context, and network attestation.
3. Open one initialized MCP session and discover compatible tools.
4. Navigate and obtain an accessibility snapshot; both consume browser calls.
5. Rebuild bounded context, request one structured decision, authorize one action, and observe it.
6. Repeat until completion, a limit, cancellation, policy block, or failure.
7. Finalize the structured report and persist available artifacts.

The model does not invoke tools directly. The executor validates tool name, arguments, destination,
reference, and policy. Duplicate inspections of the same tracked page revision are rejected.
A response that only links a snapshot file can trigger an automatic snapshot within existing
budgets. That adds a browser call, not a model request.

Invalid structured model decisions can receive up to two consecutive correction requests. They
consume normal model/time budgets and do not replay successful browser actions. API/configuration
errors are terminal. Repeated policy rejections can consume the remaining iteration budget; no
separate no-progress threshold is implemented.

Bounds cover cooperating asynchronous work, not an OS-level process kill guarantee. Artifact
initialization/final writes and synchronous filesystem work are not fully contained by the async
execution timeout. There is no dollar-cost limit or exact token-budget enforcement in V1.

## 9. Result schema and meaning

| Field | Meaning |
| --- | --- |
| `schema_version` | Literal `1.0` |
| `run_id` | Unique generated identifier for the run and artifact directory |
| `objective`, `target_url` | Task identifiers represented in the redacted result |
| `status` | Actual terminal execution status from the set below |
| `started_at`, `finished_at` | Datetimes serialized to ISO-form timestamps |
| `verified_behaviors` | Model-reported behavior descriptions accepted by provenance checks |
| `findings` | Structured model-reported findings with explicit verification status |
| `unresolved_questions` | Uncertainty, rejected claims/proposals, and unresolved questions |
| `evidence` | References to observations and optional retained artifacts |
| `metrics` | Measured counters and provider-reported usage, where available |
| `errors` | Structured safe diagnostic codes/messages |
| `observed_actions` | Names of attempted executed browser tools |

The complete result must be retained by the host, not flattened into a list of finding strings.
The model may report no findings or behaviors. Empty lists do not prove absence of defects.

## 10. Terminal statuses and host routing

| Status | Implemented meaning | Required host treatment |
| --- | --- | --- |
| `completed` | Model chose to finish the decision loop | Assess objective criteria and evidence before advancing |
| `step_limit` | Browser-call budget exhausted | Preserve partial evidence and unmet/unknown criteria |
| `model_limit` | Model-request budget exhausted | Preserve partial evidence; do not silently grant more budget |
| `timeout` | Run/tool deadline reached | Operation outcome may be unknown; avoid automatic replay |
| `cancelled` | Cancellation caught by the service | Stop dependent execution and preserve cancellation |
| `blocked` | Policy/context/unsafe-observation boundary prevented progress | Report the specific unmet prerequisite; require changed trusted input |
| `failed` | Model, browser, transport, or other guarded failure | Preserve safe diagnostics; do not treat as a product assertion failure |

`completed` is not equivalent to passed, verified, or objective fulfilled. `inconclusive` is a
finding status, not a run status. Host outcomes such as `review_required` or `skipped` belong in a
separate host envelope; they must not replace or extend V1 `status` silently.

The host may consume useful evidence from a partial run while keeping the partial status. It must
not automatically execute generated code merely because an exploration returned successfully.

## 11. Nested evidence and finding records

`BrowserObservation` contains `id`, `url`, `action_name`, `action_arguments`, bounded `summary`,
optional `evidence_ref`, `timestamp`, and `error`. Observations live in the trajectory; the result
does not inline the full trajectory.

`EvidenceReference` contains `id`, `observation_id`, `kind`, optional relative `path`, and `retained`.
Resolve paths only beneath the authorized run directory. Do not open a path solely because it came
from a deserialized remote result. A missing path or `retained=false` means the raw payload is not
available from that reference; bounded trajectory summaries may still exist.

`VerifiedBehavior` contains a description and one or more evidence IDs. The executor admits only
successful eligible non-screenshot evidence IDs. This check does not prove the evidence supports
the exact wording or chronology of the description.

`BrowserFinding` contains an ID, title, description, expected/observed behavior, reproduction steps,
evidence IDs, verification status, optional severity, and optional confidence in `[0, 1]`. Status is
`confirmed`, `potential`, or `inconclusive`; severity is `critical`, `high`, `medium`, `low`, or
`informational`. These classifications are model assertions, not independent measured confidence.

Confirmed findings require evidence IDs at schema validation. The executor downgrades findings
with invalid/failed evidence references or missing reproduction steps. Known successful IDs alone
do not establish semantic correctness. Screenshots cannot confirm visual findings because pixels
are not sent to the decision model. Screenshot capture itself lacks a distinct executor-owned
public completion field in V1.

## 12. Artifacts, retention, and provenance

```text
<configured output root>/<run_id>/
  run_config.json
  trajectory.jsonl
  decisions.jsonl       # when decision/error events were recorded
  result.json
  metrics.json
  evidence/             # only when raw retention is enabled and observations exist
```

Artifacts use private permissions. Configuration files contain an explicit field allowlist;
credentials and the full environment are not exported. Trajectory entries retain redacted action
metadata and bounded summaries even with raw retention disabled. Binary images are stored outside
result JSON and excluded from model context.

Redaction covers recognized secret patterns and URL query values; it is not a universal sensitive
data classifier. Pixels cannot be text-redacted. Server-side profiles/logs/files and model-provider
data handling have independent lifecycles. The consumer must decide approved data, retention,
deletion, access control, and tracing before enabling raw evidence or telemetry exports.

The host envelope should add request/requirement IDs, host run ID, application version, repository
revision, fixture state, contract/package version, model attribution, policy/configuration digest,
and any parent approval record. Those fields are not accepted task fields in V1.

## 13. Metrics and objective assessment

`ExecutionMetrics` reports duration, attempted/successful/failed browser calls, LLM calls, finding
count, verified-behavior count, and input/output/total tokens. Missing usage remains `null` rather
than a fabricated estimate. A zero model-call run can also have unknown token totals.

Counters measure operations, not functional application coverage. A successful browser call means
the executor accepted its response, not that the target requirement passed. Provider model metadata
is retained on decision/error events when supplied; configured model alone is not a complete
historical provider attribution record.

The host must maintain separate objective checks, for example:

```json
{
  "check_id": "article-details-navigation",
  "expected_source": "approved-requirement:R-123",
  "outcome": "unknown",
  "evidence_ids": [],
  "reason": "No observation establishes the requested destination content."
}
```

This is a host-owned example, not a V1 node schema extension. A check becomes `met` or `unmet` only
under a defined assessment rule. Run termination and model-reported verification do not supply
that rule automatically.

## 14. Errors, exceptions, and cancellation

`RunError` has `code` and `message`. Common guarded failures include `safety_policy`,
`context_limit`, `mcp_unavailable`, `mcp_disconnected`, `missing_browser_tools`, `navigation_failed`,
`tool_timeout`, `execution_timeout`, `llm_validation_error`, `llm_api_error`, and model configuration
errors. This is illustrative; consumers must tolerate unknown diagnostic codes without assuming
success or replaying an action. Status enums remain closed and versioned.

The default service catches many operational errors and returns terminal results. It can still
raise during task revalidation, artifact setup/config writes, or final persistence. Storage
failures can prevent complete artifacts even after an action occurred. The adapter must catch
validation/storage failures at its own boundary and preserve a host integration error. Do not
manufacture a V1 result claiming browser success when no valid result was returned.

Cancellation is deliberately converted to a `cancelled` result when caught inside the guarded run.
Callers must inspect the returned status; they cannot assume `CancelledError` always propagates.
Cancellation outside that guard may propagate. Remote actions may already have executed when a
timeout or disconnect arrives. V1 does not guarantee exactly-once execution, remote rollback,
automatic reconnection, or safe resumption.

## 15. Adapter-first integration requirements

The host adapter must:

1. Route supported UI tasks explicitly; define skip/block handling for API, system, unknown, or
   mixed requirements without inferring permission from repository text.
2. Preserve original requirement text, approved expected outcomes, and material ambiguities.
3. Map selected repository snippets with provenance and host-enforced count/size limits.
4. Construct task domains, permissions, and budgets from trusted configuration only.
5. Await the public protocol with run-local dependencies and a defined parent cancellation policy.
6. Preserve the full returned result and link its artifact directory without rewriting its status.
7. Distinguish host transport/storage/validation failures from valid node terminal results.
8. Assess each objective against approved expectations and relevant retained/available evidence.
9. Apply host continuation/review rules before test generation or execution.

No adapter for Agentic QA is installed into this package. The [adapter design](../adapter_design.md)
defines a concrete host mapping and outcome envelope without coupling the browser core to it.

## 16. Compatibility and versioning

Package version, document revision, and wire schema version are distinct. The extraction changes
the Python import path from `agentic_qa.browser_exploration` to `browser_exploration`; consumers must
update imports and install the new distribution. The original namespace is not provided as a shim.
The task/result fields and result schema literal are preserved.

`extra="forbid"` means even additive wire fields can break older readers. Do not label new fields,
new enum values, or changed status meanings backward-compatible without reader negotiation and
contract tests. Breaking wire changes require an explicit schema-version decision and migration
fixtures. Do not silently reinterpret old `completed` or `confirmed` records.

MCP SDK remains `<2` for the extracted adapter compatibility boundary. Update MCP and LangChain MCP
Adapters together with a persistent-session smoke test. Dependency/API behavior and model access
must be checked against the deployed lock and account, not inferred from a configured model name.

## 17. Verification and release evidence

Required automated evidence includes independent package import, no parent imports, schema parity,
all terminal-status serialization, unknown-field rejection, tool-policy negatives, context limits,
budget/cancellation behavior, artifact retention/redaction, fake transport/model failures, and
CLI offline help/schema export. A wheel must contain only the standalone production package.

The opt-in local MCP smoke test establishes navigation/snapshot continuity, not autonomous model
quality. A mock graph demonstration establishes plumbing only. Live quality evaluation needs a
versioned target, fixed fixture state, explicit criteria, provider attribution, repeated tasks,
supported/unsupported claims, and known-defect cases. See the extraction record for actual checks
performed; do not infer live verification from the size or coverage of the unit suite.

## 18. Explicit V1 limitations and future decisions

- Objective fulfillment and semantic evidence assessment remain host responsibilities.
- No vision-based screenshot inspection, functional coverage metric, or independent confidence score.
- No built-in retrieval, requirement analysis, test design, code generation, pytest runner, or POM edits.
- No arbitrary production access, credential automation, or unrestricted mutation support.
- No verified cross-run concurrent sharing, public resume API, or exactly-once remote execution.
- No external egress enforcement, comprehensive DLP, or guaranteed persistence on storage failure.
- No automatic remote publication, model fallback, or host approval workflow.

Any extension should update this contract, the schemas, consumer fixtures, and the reusable
[contract template](../templates/node_contract_template.md) as appropriate.
