# <Node name> integration contract

Template revision: 1. Replace `<placeholders>`, remove unused sections with an explanation, and
classify every promise as implemented, required of the consumer, or proposed. A completed document
must link to concrete schemas, source, and verification evidence. It must not claim approval,
production readiness, or test success from this template alone.

## 1. Contract identity

| Property | Value |
| --- | --- |
| Contract ID | `<capability>/<major-version>` |
| Document revision / date | `<revision>` / `<YYYY-MM-DD>` |
| Repository / distribution / package | `<repository>` / `<distribution>` / `<import path>` |
| Interface / transport | `<async protocol, HTTP, queue, CLI, ...>` |
| Input schema version | `<version or explicitly unversioned>` |
| Output schema version | `<version>` |
| Producer owner | `<team/person; assignment status>` |
| Consumer owner | `<team/person; assignment status>` |
| State | `<draft / evaluated / accepted / deprecated>` |
| Authoritative schemas | `<paths or immutable links>` |
| Reference implementation | `<path and revision>` |

Approval record, if required: `<decision ID, approver, scope, date; otherwise pending/not required>`.

## 2. Purpose, user outcome, and scope

Describe the user request this capability handles and the observable result it returns.
State the first supported application/domain and environment. Separate running an operation from
establishing that the user's intended outcome occurred.

- Supported scenarios: `<bounded list>`.
- Unsupported scenarios: `<explicit routing behavior>`.
- Non-goals: `<capabilities intentionally owned elsewhere>`.
- First complete acceptance scenario: `<input, fixture, expected output, assessment rule>`.

## 3. Architectural boundary

```text
<host workflow and domain models>
        |
<host-owned adapter / policy / outcome mapping>
        |
<versioned node interface>
        |
<node internals and external dependencies>
```

List the public import paths or endpoints. Identify internal state/types consumers must not depend
on. Describe which repository owns adapters, authorization, and orchestration. Specify permitted
dependency direction and how tests enforce it.

## 4. Responsibility matrix

| Concern | Producer/node | Consumer/adapter | Operator/external service |
| --- | --- | --- | --- |
| Requirement interpretation | `<responsibility>` | `<responsibility>` | `<responsibility>` |
| Approved expected behavior | `<responsibility>` | `<responsibility>` | `<responsibility>` |
| Target/data selection | `<responsibility>` | `<responsibility>` | `<responsibility>` |
| Permission and budget policy | `<responsibility>` | `<responsibility>` | `<responsibility>` |
| External execution | `<responsibility>` | `<responsibility>` | `<responsibility>` |
| Evidence and provenance | `<responsibility>` | `<responsibility>` | `<responsibility>` |
| Objective assessment | `<responsibility>` | `<responsibility>` | `<responsibility>` |
| Persistence and retention | `<responsibility>` | `<responsibility>` | `<responsibility>` |
| Downstream continuation | `<responsibility>` | `<responsibility>` | `<responsibility>` |

## 5. Input contract

| Field | Type | Required/default | Bounds/validation | Authority/provenance |
| --- | --- | --- | --- | --- |
| `<field>` | `<type>` | `<required/default>` | `<bounds>` | `<trusted config / reference data>` |

Explain unknown-field behavior, primitive coercion, normalization, cross-field constraints,
mutability, task snapshots, and validation timing. Define which fields can be omitted, truncated,
or rejected. Distinguish identifiers from executable instructions and filesystem/network grants.

Provide a minimal and a representative valid request, plus invalid requests with expected error
types. Link examples to automated schema validation.

## 6. Configuration and credentials

| Setting | Default | Override precedence | Validation | Sensitive? |
| --- | --- | --- | --- | --- |
| `<setting>` | `<default>` | `<CLI/env/config/task>` | `<rule>` | `<yes/no>` |

Describe configuration-loading timing, run-local versus shared state, credential sourcing, and
whether values are persisted. Do not place actual credentials in examples. Explicitly distinguish
an attestation flag from an enforced control.

## 7. Output contract

| Field | Type | Required/default | Meaning | Consumer obligation |
| --- | --- | --- | --- | --- |
| `<field>` | `<type>` | `<required/default>` | `<semantics>` | `<rule>` |

Identify outputs supplied by an executor, outputs proposed by a model, and outputs independently
verified. Define empty-list, null, missing-data, and redacted-value semantics. Include realistic
success, partial, blocked, cancelled, and failure examples.

## 8. Status and continuation semantics

| Node status | Exact meaning | Evidence that may exist | Consumer action | Replay allowed? |
| --- | --- | --- | --- | --- |
| `<completed>` | `<execution meaning>` | `<evidence>` | `<assess/review/continue>` | `<rule>` |
| `<limit/partial>` | `<meaning>` | `<evidence>` | `<rule>` | `<rule>` |
| `<blocked>` | `<meaning>` | `<evidence>` | `<rule>` | `<rule>` |
| `<timeout>` | `<meaning>` | `<unknown side effects>` | `<rule>` | `<rule>` |
| `<cancelled>` | `<meaning>` | `<evidence>` | `<rule>` | `<rule>` |
| `<failed>` | `<meaning>` | `<evidence>` | `<rule>` | `<rule>` |

Keep execution status, finding/assertion status, and host workflow disposition distinct. Define
whether additional statuses require schema negotiation. A normal return is not automatically a
successful product verdict or permission for downstream execution.

## 9. Adapter mapping

| Host source | Node field | Transformation | Bounds | Trust constraint |
| --- | --- | --- | --- | --- |
| `<host requirement>` | `<objective/context>` | `<mapping>` | `<bounds>` | `<no permission grants>` |
| `<host deployment>` | `<target/policy>` | `<mapping>` | `<bounds>` | `<trusted source>` |
| `<retrieved sources>` | `<snippets>` | `<mapping>` | `<bounds>` | `<untrusted data>` |

Document unsupported host types, mixed interaction surfaces, unresolved ambiguities, and mapping
errors. Define a host-owned result envelope with the unchanged node result, parent IDs, policy
snapshot, artifact locator, assessment, and continuation decision. Do not silently add host fields
to a closed node schema.

## 10. Correctness and objective assessment

State the authoritative source of expected behavior. Observed implementation behavior alone must
not define the expected business result.

| Objective criterion | Approved expected source | Evidence needed | Assessment rule |
| --- | --- | --- | --- |
| `<criterion ID>` | `<requirement/decision>` | `<observation type and relevant state>` | `<met/unmet/unknown>` |

Explain how relevance, chronology, environment version, stale evidence, and contradictions are
checked. Distinguish provenance checks from semantic correctness. Define when human assessment is
required and which claims cannot be assessed with the available modalities.

## 11. Evidence and provenance

For each record, define ID, source, action, timestamp, environment, error/success indicator,
content representation, and artifact reference. Define how observations relate to findings and
objective criteria, how unknown/failed evidence is handled, and what remains when raw data is not
retained. A reference must not imply the original payload is recoverable.

Document source revisions, application/fixture versions, model attribution, prompt/configuration
versions, and parent-run linkage. State which fields are implemented versus host additions.

## 12. Capability, authorization, and execution boundaries

| Operation | Default availability | Required permission | Enforcement point | Residual limitation |
| --- | --- | --- | --- | --- |
| `<operation>` | `<allowed/denied>` | `<permission>` | `<client/OS/network/service>` | `<limit>` |

List allowed destinations, filesystem roots, command/tool catalog, credential handling, and
mutation boundaries as relevant. Describe validation of references and parameters. Separate
control-plane trusted configuration from data-plane reference content. Explain controls supplied
outside the node and how their readiness is established.

## 13. Lifecycle, retries, and resource bounds

Describe initialization, external connections, deterministic setup, decision/execution loop,
finalization, and cleanup. Include budgets for requests, steps, elapsed time, individual calls,
context, artifact sizes, and cost where actually implemented.

- Budget accounting: `<what counts, bootstrap, rejected proposals, corrections>`.
- Retry policy: `<eligible failures, count, delay, budget use>`.
- Non-replayable operations: `<operations and rationale>`.
- No-progress rule: `<implemented threshold or explicit absence>`.
- Timeout enforcement: `<async deadline / process kill / external service deadline>`.
- Work outside deadlines: `<initialization, storage, cleanup, synchronous work>`.
- Exactly-once/idempotency: `<guarantee, required keys, or explicit absence>`.

## 14. Exceptions and cancellation

| Failure | Returned result or raised exception? | Safe diagnostics | Consumer handling |
| --- | --- | --- | --- |
| Invalid input | `<behavior>` | `<fields/codes>` | `<rule>` |
| External service failure | `<behavior>` | `<metadata>` | `<rule>` |
| Model parsing failure | `<behavior>` | `<safe codes>` | `<rule>` |
| Storage initialization/finalization | `<behavior>` | `<metadata>` | `<rule>` |
| Cancellation | `<propagate/convert; exact scope>` | `<status>` | `<rule>` |
| Unknown failure | `<behavior>` | `<sanitization>` | `<rule>` |

Do not promise that every failure returns structured output unless the full entry-to-exit path
enforces it. State what may have executed remotely and whether cleanup or final artifacts are
guaranteed. Unknown diagnostics must not be interpreted as success.

## 15. Storage, privacy, and retention

Specify the artifact layout, directory/file permissions, path validation, symlink behavior,
atomicity, partial writes, quotas, and ownership. State whether storage errors escape.

Describe retained summaries, raw payload opt-in, redaction scope, images/binary handling, deletion,
and access control. Identify independent external logs/profiles/provider retention. Record any
tracing/data export destination and its enablement policy.

## 16. Observability and quality measures

| Measure | Definition / denominator | Source | Missing-data behavior | Acceptance threshold |
| --- | --- | --- | --- | --- |
| Objective success | `<reviewed criteria>` | `<assessment>` | `<unknown>` | `<agreed threshold>` |
| Unsupported claims | `<claim-level definition>` | `<audit>` | `<rule>` | `<threshold>` |
| Defect detection | `<known-defect pairs>` | `<benchmark>` | `<rule>` | `<threshold>` |
| Duration | `<clock/start/end>` | `<metrics>` | `<rule>` | `<budget>` |
| Model usage/cost | `<reported units and pricing version>` | `<provider>` | `<null/unknown>` | `<budget>` |

Do not describe code coverage, successful tool calls, or a model's completion message as functional
coverage. Define sample size, repeated trials, environment reset, and confidence limits.

## 17. Compatibility, packaging, and migration

State supported runtime/dependency versions, installation, CLI names, and conflicts. Distinguish
package, document, and schema versions. Define breaking changes, including added enum values and
new fields when older validators reject extras. Supply migration and old/new-reader fixtures.

Identify the source snapshot and extraction/rename changes. Avoid copying host credentials,
generated runs, caches, or unrelated packages into a new distribution. State remote publication
and release status accurately.

## 18. Verification matrix

| Requirement ID | Scenario / fixture | Test or review evidence | Actual result | Remaining limit |
| --- | --- | --- | --- | --- |
| `<ID>` | Independent package import | `<test>` | `<pass/fail/unrun>` | `<limit>` |
| `<ID>` | No host dependency | `<AST/import test>` | `<result>` | `<limit>` |
| `<ID>` | Schema and all statuses | `<test>` | `<result>` | `<limit>` |
| `<ID>` | Limits, errors, cancellation | `<test>` | `<result>` | `<limit>` |
| `<ID>` | Correct/known-defective target | `<benchmark>` | `<result>` | `<limit>` |
| `<ID>` | Real external transport | `<opt-in integration>` | `<result>` | `<limit>` |

Record executed commands, runtime versions, dates, and skipped/blocked checks. Mock tests establish
only their exercised behavior. Do not infer live quality from offline fixtures.

## 19. Open decisions and release gates

| Decision | Owner | Needed before | Default until resolved |
| --- | --- | --- | --- |
| `<decision>` | `<owner>` | `<milestone>` | `<explicit behavior>` |

List concrete acceptance conditions, supported first scenarios, required evidence, and review
boundaries. Mark unresolved thresholds as pending rather than declaring readiness.

## 20. Change history

| Revision | Date | Change | Schema impact | Consumer action |
| --- | --- | --- | --- | --- |
| `<revision>` | `<date>` | `<change>` | `<none/breaking>` | `<migration>` |
