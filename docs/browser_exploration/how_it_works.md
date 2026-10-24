# How the browser exploration agent works

This is a source-oriented walkthrough of one run. For installation and commands, use the
[operating guide](README.md); for the complete design, use the [architecture](../architecture.md).
The [V1 contract](../contracts/browser_exploration_v1.md) defines accepted fields and exact status
semantics. The model proposes a step; Python code decides whether it may execute.

## Start at the public interface

The caller awaits `BrowserExplorer.explore(task) -> BrowserExplorationResult`.
[`AutonomousBrowserExplorer.explore`](../../src/browser_exploration/explorer.py) is the default
implementation. A host-owned adapter constructs the public task and retains the returned result;
requirement analysis, retrieval, approvals, and downstream test generation/execution stay outside
this package. The [adapter design](../adapter_design.md) describes that mapping.

The task supplies the target, objective, optional reference text, exact allowed domains, permissions,
and limits. Settings supply deployment concerns such as model/MCP selection, retention, and tool
timeout. The CLI copies default limits into the task; direct Python callers do that explicitly.

The service copies and revalidates the task, creates a run ID and artifact store, saves selected
configuration, and initializes fresh state and collaborators. It checks prerequisites before
opening MCP. Validation and artifact setup can raise outside the guarded run.

## The three graph nodes

[`build_graph`](../../src/browser_exploration/graph.py) registers the nodes and one router:

```text
START -> prepare_context -> explore_browser (repeat) -> finalize_report -> END
            |                                           ^
            +----------- terminal prerequisite -----------+
```

| Node | Role |
| --- | --- |
| `prepare_context` | Validate the target, preserve required context, and require external network-control attestation |
| `explore_browser` | Check budgets, select a next step, and execute at most one browser call |
| `finalize_report` | Assemble the validated result from accumulated records and counters |

The router repeats exploration while `status` is `None`; any terminal status routes to reporting.
Model, policy, context, transport, and storage helpers are collaborators, not additional graph nodes.
[`ExplorationState`](../../src/browser_exploration/state.py) is a run-local TypedDict shared by
those nodes. It is not host workflow state or a durable resume checkpoint.

## Preparation and one persistent browser session

Preparation checks URL policy, whether the full objective/restrictions fit the context allowance,
and `network_restricted`. The flag attests to independently configured browser network controls;
it does not create or verify them. A failed prerequisite can block before any MCP/browser/model
call. The same preparation is represented inside the observable graph.

[`MCPConnection.connect`](../../src/browser_exploration/mcp_client.py) then:

1. Configures Streamable HTTP to the selected MCP endpoint.
2. Enters one initialized `ClientSession`.
3. Loads tools bound to that session.
4. Selects known aliases with compatible argument schemas.
5. Yields the supported catalog and call interface until the run exits.

Navigation and accessibility snapshots are mandatory. Click, hover, input, forms, screenshots,
console, and network inspection are optional. Code, upload, filesystem, and credential tools are
excluded. Tools reuse the initialized session, so navigation and later observations share browser
continuity. The Python package does not start the server/browser or use VS Code's MCP connection.

## Choosing and executing a step

The first two calls are deterministic: navigate to the task URL, then read an accessibility
snapshot. Both consume browser-call budget and neither requires a model decision.

Later, `next_action` builds fresh bounded context and asks
[`OpenAIDecisionModel.decide`](../../src/browser_exploration/model.py) for one structured Decision:
an Action or `done=true`, optionally with model-reported claims and questions. The decision is
revalidated even when an injected fake supplied it. An injected model can therefore exercise the
real graph without a provider request.

[`SafetyPolicy.authorize`](../../src/browser_exploration/safety.py) checks the selected tool,
argument keys, destination, permissions, and observed references before execution. The default link
policy can rewrite a click into validated navigation. Generic clicks, form input, and submission
need separate explicit permissions; known sensitive/destructive controls remain denied.

Snapshot references use bare values: `[ref=e43]` means `ref="e43"` or `target="e43"`, according to
the tool schema. Limited wrappers are normalized only if they name an exactly observed reference.
CSS/XPath and label guesses are not selectors the executor resolves. URL/label heuristics do not
prove read-only behavior or contain browser redirects, scripts, DNS changes, and subresources.

`execute_action` counts an accepted attempt, applies a per-tool deadline, extracts text, checks
the observed location and required snapshot content, and classifies the response. Its final block
records a bounded observation and evidence reference even on failure/cancellation when storage
succeeds. Rejected proposals before execution use no browser call; their model request is counted.

## Context and progress

[`ContextBuilder.build`](../../src/browser_exploration/context_builder.py) rebuilds messages rather
than appending an unlimited transcript. Full instructions/objective/restrictions and current URL
come first. Bounded executor feedback and successful-action progress precede newest observations;
findings, questions, requirement text, and source snippets follow as space permits.

The full successful-action ledger survives replacement of recent observations. Selected entries
carry redacted arguments, URL, observation ID, and evidence ID, so completed work can remain visible
without replaying it. Page and repository text are untrusted data and cannot expand authority.
The character allowance is not a token/cost limit; the tool catalog has a separate size cap.

[`summarize_observation`](../../src/browser_exploration/observations.py) redacts recognized patterns,
compacts verbose link queries when useful, and explicitly truncates. It is not a semantic
summarizer and can omit relevant content. Raw image/audio/file payloads never enter model context.

## Recovery within existing limits

- A response containing only a snapshot-file link can queue an inline snapshot before another
  model request. That costs a browser call within the existing limits.
- A denied model proposal is logged and fed back so the model can choose a different permitted
  action or finish with an unresolved limitation.
- Invalid decisions get up to two consecutive correction requests; available usage is retained,
  and a valid reply resets that counter. Provider/configuration errors are terminal.
- Repeated successful inspections are rejected for the same tracked page revision. This local
  counter does not detect every script-driven change and is not a complete no-progress rule.

Browser/model/time limits are independent. Rejected or malformed decisions can consume model
budget without browser progress. A timeout/disconnect can leave an operation's remote outcome
unknown. The implementation does not automatically reconnect and replay it.

## Evidence and finalization

Eligible successful non-screenshot evidence IDs can support model-reported behaviors/findings.
Unknown/failed IDs and missing required reproduction steps prevent certain confirmations. These
are traceability checks, not a proof that the cited observation supports the claim's wording.
Screenshots establish capture when execution succeeds, but their pixels are not inspected by the
model; V1 has no separate executor-owned public capture-completion field.

When the model says done, the run becomes `completed`. That means the model stopped, not that the
objective passed. Other terminal statuses preserve limits, cancellation, blocking, and failures.
The host assesses approved expected behavior against relevant evidence independently.

The graph builds a report, the session exits, and the service finalizes again so cleanup errors
can change the returned outcome. It then saves result and metrics files. Caught cancellation
becomes a `cancelled` result; callers must inspect status. Task-validation, artifact setup, and
final persistence failures can still raise to the host.

## Follow the offline example

The [mock demonstration](../../examples/browser_exploration/mock_run.py) injects a fake session
and two deterministic decisions into the real service/graph:

| Turn | Selected step | Browser calls so far | Model calls so far |
| --- | --- | --- | --- |
| 1 | Bootstrap navigation | 1 | 0 |
| 2 | Bootstrap snapshot | 2 | 0 |
| 3 | Model proposes Help click; policy executes navigation | 3 | 1 |
| 4 | Model returns done with a synthetic evidence-backed claim | 3 | 2 |

It labels all output as mock-only and supplies no invented token counts. The
[testing guide](testing.md) explains running it and the default offline suite. Use the
[debugging guide](../debugging.md) for breakpoints. For artifacts and retention, use the
[operating guide](README.md#results-evidence-and-diagnostics).
