# Future integration with Agentic QA

V1 is independent. The original Agentic QA workflow remains in its own repository. This document
describes a possible consumer-owned adapter, not an implemented capability. The standalone
[adapter design](../adapter_design.md) and [V1 contract](../contracts/browser_exploration_v1.md)
define the extraction boundary.

The stable application boundary is:

```python
class BrowserExplorer(Protocol):
    async def explore(self, task: BrowserExplorationTask) -> BrowserExplorationResult: ...
```

An adapter should be outside `browser_exploration/` and own mappings from existing project
contracts. It should preserve both source records and the browser agent's actual result/status.
Domain authorization, URL selection, and budgets must come from trusted application
configuration; an LLM-produced requirement or repository snippet cannot grant those permissions.

## Proposed mappings

| Existing or future input | Browser contract mapping | Important constraint |
| --- | --- | --- |
| `RequirementAnalysis.objective`, `feature`, `operation` | Task objective and requirement text | Preserve original intent; do not turn inferred assumptions into requirements |
| `RequirementAnalysis.actors`, `conditions`, `expected_outcomes` | Requirement text and task constraints | Actor names do not grant browser credentials or mutation permissions |
| `RequirementAnalysis.ambiguities` | Explicit context questions for exploration | Unresolved requirements must remain visible downstream |
| `RequirementAnalysis.interaction_surface` | Adapter routing decision | UI observations cannot validate API/system behavior by themselves |
| `RepositoryContext.items` | `ContextSnippet(source=item.path, text=item.snippet)` | Bound count/size and preserve provenance; repository content is untrusted reference data |
| Existing keyword retrieval or future symbol-aware retrieval | Optional snippets of relevant tests, symbols, and page objects | Relevant code provides hints, not hardcoded mandatory browser actions |
| Future Zephyr knowledge layer | Optional source-tagged test context | Retrieval is a separate future capability; no Zephyr dependency exists in V1 |

The inspected original repository provider uses keyword retrieval and first-match snippets.
AST symbol indexing is not implemented there. A consumer can select relevant results before
passing bounded snippets; this package has no retriever or vector-database dependency.

## Downstream consumers

`BrowserExplorationResult.verified_behaviors` could inform future test design with observed
behavior and evidence. Findings could inform failure analysis, while keeping `confirmed`,
`potential`, and `inconclusive` separate. A partial or failed exploration must not be transformed
into a passed test, a resolved ambiguity, or a completed coverage assessment.

Reproduction steps and evidence references could help validate future generated tests against
the browser's actual observations. Those consumers must check the evidence retention flag,
artifact availability, relevant timestamps, and run status. A reference to discarded evidence
does not provide a reproducible snapshot. Cross-run comparison needs application version,
fixture state, and an independently defined evaluation checklist before measuring coverage.

Future evaluation could score task completion, supported findings, reproducibility, false
positives, cost, duration, and policy compliance using the existing structured JSON. Provider
token usage can be compared where measured; `null` values must remain missing data. A TinyFish
comparison would be a separate evaluation integration using matched tasks and environments.

## Recommended next step

After validating V1 against the controlled demo and an approved read-only application, define
an adapter contract test using recorded `RequirementAnalysis` and `RepositoryContext` fixtures.
Review the intended task mapping, explicit target authorization, evidence retention policy, and
handling of every terminal status before adding any workflow edge. Keep the current workflow
and its baseline evaluations unchanged until that separate integration work is approved.

Authentication, mutation scenarios, Zephyr retrieval, generated Playwright/POM changes, workflow
state changes, and CI/CD integration remain separate future decisions.
