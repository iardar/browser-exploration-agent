# Adapter-first host integration

## Ownership and direction

The browser package publishes `BrowserExplorer`, `BrowserExplorationTask`, and
`BrowserExplorationResult`. The consuming workflow owns its adapter. Dependencies point from the
host adapter into the public browser interface. The browser core never imports host records or
retrieval/generation/execution services.

```text
Host: requirement analysis + retrieval + trusted deployment configuration
                               |
                    Host-owned browser adapter
                               |
          BrowserExplorer.explore(BrowserExplorationTask)
                               |
                 BrowserExplorationResult (unchanged)
                               |
           Host: objective assessment + continuation/review policy
```

The browser service owns its own LangGraph. The adapter may be one async node in the host graph;
it does not merge graph states, reach into `ExplorationNodes`, or parse CLI console output as an API.

## Host input mapping

| Host input | Public node input | Adapter rule |
| --- | --- | --- |
| Original requirement | `requirement_text` | Preserve original text within a chosen bound |
| Analysis objective/feature/operation | `objective` | Preserve intent and explicit restrictions; do not invent expected behavior |
| Conditions/expected outcomes/ambiguities | Requirement or context text | Label expected versus unknown; retain approved source IDs in host envelope |
| Actors and preconditions | Requirement or context text | Preserve relevant constraints; an actor name does not grant credentials or mutation permission |
| Retrieved item path/snippet | `ContextSnippet(source, text)` | Select bounded source-tagged data; no permission changes |
| Trusted application deployment | `url`, `allowed_domains` | Explicit URL and exact authorized hostnames |
| Trusted execution policy | Task permission flags and budgets | Never derive from model/page/repository instructions |
| Trusted storage/model/MCP policy | `Settings` passed to implementation | Host constructs run-local configuration explicitly |

Do not import `agentic_qa` into this repository to implement these mappings. They describe what a
consumer adapter must do. Record the host package revision and browser package/schema version
separately for reproducibility.

Preserve the original requirement and material ambiguities alongside selected context. Retrieved
snippets are browser hints, not proof of expected behavior or a complete executable-test context.
If a host later generates tests, it separately needs relevant complete symbols, imports, fixture
registration, and repository configuration. That retrieval/generation contract stays outside the
browser package. Do not assume a host retriever implements AST indexing without checking it.

## Host envelope

The consumer should define a separate typed result with fields equivalent to:

| Field | Purpose |
| --- | --- |
| `host_run_id`, `requirement_id` | Parent lineage |
| `routing` | `invoked`, `skipped`, or `blocked` under host policy |
| `node_contract`, `node_package_version` | Exact integration boundary |
| `application_version`, `repository_revision`, `fixture_state` | Reproducible environment |
| `policy_snapshot` | Approved permissions/budgets and policy version, without credentials |
| `node_result` | Full unmodified result, or null if no valid result returned |
| `artifact_root` | Host-authorized locator; do not trust an arbitrary output path |
| `objective_checks` | Host assessments using approved expectations and relevant observations |
| `disposition` | Continue, partial/review, blocked, cancelled, or integration error |
| `integration_error` | Safe validation/storage/transport error outside a returned node result |

This is a design for a host model, not a new implemented browser task/result schema. Adding these
fields to the V1 task would fail unknown-field validation.

## Routing and continuation

Only an explicitly supported UI scenario invokes the browser. API and system requirements should
take an explicit non-browser path. An unknown or mixed surface requires a trusted override or a
host block/review outcome; it must not silently become UI permission.

| Node result / host condition | Host disposition |
| --- | --- |
| Unsupported surface, no invocation | Explicit skipped/blocked host record with reason |
| `completed`, all required objective checks assessed | Eligible for the next host stage under its review policy |
| `completed`, missing/unsupported objective evidence | Partial/review; preserve original `completed` status |
| `step_limit`, `model_limit`, `timeout` | Partial/review; retain evidence and unknown operation outcomes |
| `blocked` | Blocked with prerequisite/policy reason |
| `failed` | Dependency/exploration failure; never a product test assertion failure |
| `cancelled` | Cancelled; stop dependent work |
| Exception without valid node result | Host integration error; do not fabricate a completed node result |

Evidence from a partial run may be useful, but partial execution must remain visible. A completed
exploration does not automatically authorize saving/executing generated Python. Human approval
and downstream execution isolation are host responsibilities.

## Adapter contract-test checklist

Use recorded/public model fixtures and an injected fake `BrowserExplorer`:

1. UI task mapping preserves original requirements and ambiguity.
2. API/system/unknown/mixed routes follow explicit host policy.
3. Retrieved/page/model text cannot change domains, permissions, budgets, or storage.
4. Snippet selection respects declared bounds and keeps source identity.
5. All seven terminal statuses round-trip without flattening or renaming.
6. `completed` with no supported objective evidence requires review.
7. Failed/unknown/stale evidence cannot satisfy an approved objective criterion.
8. Screenshot capture is distinguished from visual-content inspection.
9. Cancellation stops downstream work even when it returns as a normal node result.
10. Validation, artifact initialization, and final-write failures become safe host integration errors.
11. Artifact paths resolve within the trusted run directory and respect retention flags.
12. A correct and a known-defective fixture produce the expected host assessments.

Start with these fixtures before enabling a live edge in the consuming workflow. No live host
integration or automatic migration of Agentic QA is included in this extraction.
