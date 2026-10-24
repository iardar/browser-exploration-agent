# Extraction and validation record

Recorded extraction: 2026-10-23. Distribution: `browser-exploration-agent` 0.1.0.
This is a dated provenance record, not a description of every subsequent source change or a
fresh validation run. Use the [testing guide](browser_exploration/testing.md) for current commands.

## Source and scope

The browser capability was extracted from the Agentic QA workspace into a separate local Git
repository. Usable source Git history was unavailable during extraction, so this was a source
snapshot rather than a history-preserving split. Original files were left in the source workspace.
[Source SHA-256 hashes](source_manifest.json) identify the original 16 modules; they are extraction
provenance, not checksums of the subsequently annotated working tree.

At extraction, production code differed only by the namespace/logger rename
`agentic_qa.browser_exploration` → `browser_exploration`. A mechanical comparison checked all 16
files against that normalization. Task/result fields, statuses, graph policy, and known limitations
were preserved. Extraction did not resolve semantic or host-orchestration findings. Relevant
review evidence is preserved in the [historical research record](history/research_notes.md);
current correctness boundaries are documented in the [architecture](architecture.md#14-limitations-and-evolution).

Included: production browser modules, browser unit tests, opt-in local MCP test, controlled static
fixture, synthetic example, five JSON schemas, browser guides, Apache-2.0 license, packaging, CI,
boundary investigation, contract, adapter design, and reusable Markdown template.

Excluded: host workflow/analyzer/retrieval/generation/execution modules, sample automation,
credentials, `.env`, historical saved runs, cache directories, browser profiles, and parent Git
metadata. Useful evidence from older browser reports and the parent review is consolidated in
the historical research record, separate from maintained operating guidance.

## Adapter-first integration

Consumers import the public `BrowserExplorer` protocol and V1 task/result models. Host-specific
adapters remain in the consuming application. No actual Agentic QA workflow edge or adapter was
installed. The [adapter design](adapter_design.md) specifies host mapping, a result envelope,
continuation rules, and contract-test cases. The
[V1 contract](contracts/browser_exploration_v1.md) records implemented behavior and consumer
requirements separately. The [template](templates/node_contract_template.md) can be copied for
another node or integration.

## Packaging and dependency decisions

- Renamed the distribution to `browser-exploration-agent` and package to `browser_exploration`.
- Retained `qa-browser`; separate environments avoid CLI-name collisions with the original app.
- Declared directly imported runtime dependencies, including AnyIO and LangChain Core.
- Removed Python Playwright and pytest-playwright; browser execution remains on the MCP server.
- Retained the MCP `<2` compatibility boundary.
- Reduced the original resolved lock to the new direct/development dependency closure: 86 resolved
  dependencies plus the new root project. Existing dependency versions, artifact URLs, and hashes
  were preserved; no dependency upgrade was performed.
- A metadata re-resolution attempt could not reach PyPI because DNS was unavailable. Mechanical
  lock reduction was then validated by `uv lock --check --offline` and a successful independent
  `uv sync --locked --offline`, using a writable copy of the existing package cache.
- Built a source distribution and wheel. Wheel inspection found only the 16 browser modules and
  distribution metadata/license; no parent package or environment file was included.
- Shipped `.env.example` with empty credentials and network attestation disabled.

## Test-only changes

Copied tests/examples use the new import namespace. SDK exception fixtures now use the HTTP types
expected by the locked OpenAI SDK; a test helper's dynamic keyword arguments were typed explicitly.
These do not change runtime exploration behavior.

Four extraction acceptance tests enforce no host imports, public import with the host unavailable,
published-schema parity, and package configuration excluding the host/Playwright client.

`tests.sandbox_polling` is an optional pytest plugin that caps selector polling at 20 ms. It works
around missed async executor wakeups observed in this restricted environment, without changing
runtime code or disabling unit-test network prohibitions. Standard CI does not enable it. A plain
focused graph test timed out before the workaround; that is recorded as an environment limitation.

## Verification record

| Check | Actual result |
| --- | --- |
| Lock check, offline | Passed; 87 packages resolved |
| Independent locked install, offline | Passed into this repository's own `.venv` |
| Runtime comparison with source | Passed; all 16 files match after namespace normalization |
| Public import with host unavailable | Passed; original `agentic_qa` package is absent from the new environment |
| Full copied suite plus extraction checks | 449 passed, 1 live test skipped in 40.79 seconds in the independent environment, with test-only selector polling |
| Ruff lint | Passed |
| Ruff formatting | Passed |
| Full dependency-following mypy | Passed; no issues in 52 source files |
| Source distribution and wheel | Passed |
| Non-editable isolated package install | Passed in a separate temporary environment; public service imported from site-packages with no host package installed |
| Live MCP, real model, Docker deployment | Not run; remain explicit opt-ins |

The first full-suite pass used the already installed source interpreter with the extracted source
on its import path. The second pass used the new independent environment and passed all 449 tests.
No mock or offline pass establishes live model quality, external egress enforcement, or application
correctness.

## Reproduce

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest tests/unit -n auto
uv build
```

For this restricted sandbox's selector limitation only:

```bash
PYTHONPATH=.:src uv run python -m pytest tests/unit -n auto -p tests.sandbox_polling
```

Use `UV_CACHE_DIR` pointed at a writable cache when the default home cache is read-only. No local
cache path is required by the package, lock, tests, or CI configuration.

## Git and publication

An independent `.git` directory and `main` branch were created for extraction. The initial local
commit recorded the extracted code and documentation; subsequent commits can change the working
implementation. No remote hosting or package publication was performed as part of extraction.
Use Git history for the extraction commit and inspect current remote configuration separately.
