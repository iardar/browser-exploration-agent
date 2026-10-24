# Debugging in VS Code

Open the repository root in VS Code and run `uv sync --locked` to create `.venv`. Install the
recommended Microsoft Python and Python Debugger extensions. The configurations use the workspace
interpreter directly, with a Windows path override, and set the working directory to the repository
root. They do not depend on the location of an older `qa-browser` console script.

Open **Run and Debug**, select a profile, set breakpoints, and press **F5**. These are standard
`debugpy` launch configurations; see the [VS Code Python debugging documentation](https://code.visualstudio.com/docs/python/debugging).

## Profiles

| Profile | Purpose | External dependencies | Output |
| --- | --- | --- | --- |
| `Browser: Offline demo` | Step through the real graph using synthetic model/browser responses | None | `runs/debug/mock/<run_id>/` |
| `Browser: Explore target (live)` | Debug the actual CLI and exploration loop | Model credentials, MCP/browser, authorized target | `runs/debug/browser/<run_id>/` |
| `Tests: Unit tests` | Debug the unit suite in a single process | Installed development dependencies | Integrated terminal and pytest temporary artifacts |

## Offline demo

This profile launches `examples/browser_exploration/mock_run.py`. It uses `.env.example`, disables
tracing, and injects fake browser/model implementations. It does not make live requests or require
credentials. The launch profile supplies a 600-second run budget and a 120-second individual tool
timeout for stepping. The script's ordinary command-line default remains 10 seconds.

Useful breakpoint locations:

- `MockModel.decide` in the example, to inspect deterministic decisions.
- `ExplorationNodes.next_action` in `src/browser_exploration/graph.py`, to follow bootstrap and model selection.
- `SafetyPolicy.authorize` in `src/browser_exploration/safety.py`, to inspect permitted action arguments.
- `ArtifactStore.record` in `src/browser_exploration/reporting.py`, to inspect observation persistence.

## Live exploration

Create an untracked `.env` using `.env.example` as a starting point. Configure `OPENAI_API_KEY`,
`OPENAI_MODEL`, and `PLAYWRIGHT_MCP_URL`. Start the MCP/browser deployment independently. The
[operating guide](browser_exploration/README.md) describes the external network restrictions and
target prerequisites. The launch profile does not start a server or change permission flags.

The profile prompts for the starting URL and objective. The CLI defaults the domain allowlist to
the chosen target's hostname. Add explicit `--allowed-domain` arguments only for additional
authorized hosts needed by your target.

The profile reads credentials and policy from `.env`/the process environment. It supplies budgets
of 20 browser calls, 12 model requests, 600 seconds overall, and 120 seconds per tool call. Generic
mutation, form input, and form submission remain disabled unless explicitly added as CLI flags.
Model requests may incur provider charges.

Useful breakpoint locations:

- `run` in `src/browser_exploration/cli.py`, to inspect the constructed settings and task.
- `AutonomousBrowserExplorer.explore` in `src/browser_exploration/explorer.py`, to inspect run/session lifetime.
- `OpenAIDecisionModel.decide` in `src/browser_exploration/model.py`, to inspect selected messages and parsed decisions.
- `ExplorationNodes.execute_action` in `src/browser_exploration/graph.py`, to inspect execution and observations.

Wall-clock limits continue to count while the debugger is paused. Long pauses can still produce
timeout results. Increase only the debug profile's explicit budgets when necessary; the profile
does not change application defaults.

## Unit tests

The profile launches `python -m pytest tests/unit -vv -s --maxfail=1 -n 0`. Disabling worker processes
and output capture makes source breakpoints and terminal output easier to follow. It reads the
non-secret `.env.example`, disables tracing, and targets only unit tests; existing fake dependencies
and unit-test network prohibitions remain active.

To focus on one test, replace the `tests/unit` argument with its pytest node ID, for example:

```text
tests/unit/browser_exploration/test_graph.py::test_real_graph_completes_autonomous_action_and_persists_one_session
```

Some tests intentionally exercise short deadlines; pausing inside those operations can affect their
results. If you encounter the documented selector wakeup issue in this restricted sandbox, add
`"-p", "tests.sandbox_polling"` to the unit profile's `args`. The workaround is opt-in and applies
only to that test process.

All three profiles use `justMyCode: true`. Temporarily set it to `false` when debugging a dependency
such as LangGraph or the MCP adapter.
