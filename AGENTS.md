# Browser exploration repository

This is the standalone browser capability extracted from Agentic QA. Production Python lives in
`src/browser_exploration/`. Keep it independent of host workflow state, requirement analysis,
repository retrieval, test generation, and execution. Host-specific adapters belong to consumers;
the application boundary is `BrowserExplorer.explore(task) -> BrowserExplorationResult`.

Use Python 3.12, four spaces, double quotes, a 100-character line limit, and typed public functions.
Run commands from this repository root:

- `uv sync --locked`
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run mypy`
- `uv run pytest -c pyproject.toml tests/unit -n auto`
- `uv build`

Keep live checks opt-in. Unit tests inject model and MCP fakes and prohibit network calls.
`QA_BROWSER_LIVE_MCP=1 uv run pytest tests/integration -v` needs an independently started local
MCP server and controlled fixture. Credentials and generated artifacts must never be committed.

Real-model component tests live in `tests/live_llm/`, use a synthetic MCP browser, and skip unless
`--live-llm` is passed. Run them serially (`-n 0`). The test harness caps provider calls, output,
input size, and deadlines; no live provider calls belong in `tests/unit/`.

Read `docs/contracts/browser_exploration_v1.md` before changing public task/result fields or
status meaning. Regenerate schemas after contract changes. A model-directed `completed` result
does not establish objective fulfillment. Preserve evidence uncertainty, explicit budgets, default
permission restrictions, and the external network-control prerequisite.

`docs/templates/node_contract_template.md` is a reusable Markdown contract template. It is a
documentation artifact, not an installed Codex skill. Put host-specific mappings and approval
policy in an external adapter rather than importing a host package into the browser core.
