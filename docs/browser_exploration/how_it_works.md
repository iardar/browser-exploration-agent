# How the browser exploration agent works

`qa-browser` is an autonomous browser exploration command in `browser-exploration-agent`. It uses
LangGraph to run a bounded exploration, ChatOpenAI to choose later browser actions, and a separate
Microsoft Playwright MCP server to control the browser. The Python agent is an MCP client: it does
not start Playwright itself, connect to a VS Code MCP session, or launch a local browser.

## Host integration boundary

This repository contains the browser capability only. The original Agentic QA workflow owns
requirement analysis, retrieval, and downstream test stages in its separate repository.
The public `BrowserExplorer` protocol supports a host-owned adapter. No host workflow or adapter
is imported into this package. See [adapter design](../adapter_design.md) for mappings and routing.

## Components

The browser agent's main pieces are:

- `cli.py` parses `check`, `run`, and `schemas` commands and loads `.env` for CLI use.
- `config.py` reads browser-specific settings such as the MCP endpoint, model, budgets, and
  network-boundary attestation.
- `explorer.py` owns one run: it validates and copies the task, creates the result store and graph,
  opens the MCP session, and handles final timeout or cancellation.
- `graph.py` implements the LangGraph state machine and the browser/model decision cycle.
- `mcp_client.py` opens and initializes the Streamable HTTP MCP session and keeps it open for the
  run.
- `browser_tools.py` discovers MCP tools, validates their schemas, and creates the restricted
  capability catalog shown to the agent.
- `model.py` asks ChatOpenAI for a structured `Decision` containing one next action or completion,
  plus any evidence-backed findings and verified behaviors.
- `safety.py` checks destinations and actions immediately before execution.
- `context_builder.py` bounds the model context and marks browser observations as untrusted data.
- `contracts.py`, `reporting.py`, and `telemetry.py` define and write the versioned result,
  observations, evidence references, and measured counters.

## How a run connects to Playwright MCP

The MCP process runs separately from the Python CLI. A native server can run in headed mode from a
logged-in desktop terminal; the Python process only needs to reach its HTTP endpoint. By default,
the endpoint is `http://127.0.0.1:8931/mcp`. Set `PLAYWRIGHT_MCP_URL` to override it. The agent
does not use a browser login or MCP connection configured in VS Code.

When a run starts, `MCPConnection.connect()` in `mcp_client.py`:

1. Builds a `MultiServerMCPClient` entry named `playwright` with the `streamable_http` transport,
   endpoint, and configured timeouts.
2. Enters `client.session("playwright")`, which initializes a single MCP `ClientSession`.
3. Calls `load_mcp_tools(session)` to fetch tools from that initialized session.
4. Passes those tools to `discovered_tools()` to keep only recognized browser capabilities with
   compatible argument schemas.
5. Yields the filtered tools and capability names to the graph. Calls reuse this same initialized
   session for the whole run. Leaving the context closes the session.

`browser_navigate` and `browser_snapshot` (or supported aliases) are required. Click, hover,
typing, multi-field form filling, screenshot, console, and network inspection are optional.
Unknown tools and arbitrary code, filesystem, and upload tools are excluded from the model's
catalog. Form tools require explicit task permissions at the execution boundary.

The `qa-browser check` command uses the same connect, initialize, and discovery path. It reports
the endpoint, discovered tools, capability mapping, package versions, and whether the network
restriction was attested. It does not navigate, initialize an LLM, or require an OpenAI API key.

## The exploration loop

The run starts with a `BrowserExplorationTask`: target URL, objective, optional requirement and
context, exact allowed hostnames, and execution budgets. If no allowed domain is supplied, the
target URL's hostname becomes the only allowed domain.

The graph first validates the URL and context and checks that `BROWSER_NETWORK_RESTRICTED=true`.
That flag is an operator attestation, not a firewall. The browser's deployment still needs its own
network controls. A local or private target also requires `BROWSER_ALLOW_PRIVATE_NETWORK=true`.

After connecting, the first two browser actions are deterministic: navigate to the starting URL,
then take an accessibility snapshot. They use two of the browser-call budget. Each successful
browser response becomes a bounded `BrowserObservation` with an ID, URL, action, redacted
arguments, summary, and evidence reference.

For subsequent steps, the context builder combines the objective and restrictions with the
current URL, a compact executor-owned ledger of successful actions, recent observations, previous
evidence-backed checks/findings, and optional requirement/context. Ledger entries retain the
redacted action arguments, resulting URL, observation ID, and evidence ID, so completion facts do
not disappear when old observations leave the bounded window. It rebuilds the messages on each
decision, limits their size, and keeps raw browser content out of the conversation history. Page
text and supplied context are treated as untrusted data.

`OpenAIDecisionModel` sends this context and the filtered MCP tool schemas to ChatOpenAI. The model
returns a structured decision: either one tool action or `done=true`, along with any new verified
behaviors, findings, or unresolved questions. The agent validates the decision, checks evidence
references, and passes any action through `SafetyPolicy.authorize()` before invoking MCP. The
resulting observation is recorded and the graph repeats.

When an interaction returns only a `[Snapshot](...)` artifact link, the executor requests one
accessibility snapshot before asking the model for another decision. The model receives the
resulting inline content rather than treating an unread file link as inspected results. This
automatic observation uses the existing browser-call and time budgets.

The graph stops when the model reports completion, a browser action or model request fails, a
terminal safety or context rule blocks progress, or a call/time budget is reached. A model action
rejected before execution is recorded as an unresolved limitation and included in the next
decision, allowing the model to choose another permitted action or finish. Status values distinguish
`completed`, `step_limit`, `model_limit`, `timeout`, `cancelled`, `blocked`, and `failed`; reaching a
limit is a partial run, not a completed exploration. The tool-call and model-call limits are
independent. Total wall-clock time is also bounded.

Malformed model decisions receive at most two consecutive correction attempts within the existing
model/time budgets. Corrections execute no browser action and retain completed work. Each failure
is logged with available schema field paths and error types, excluding rejected values and raw
exception text. Repeated invalid decisions or an exhausted correction budget produce
`failed` / `llm_validation_error`; provider API errors remain terminal.

The executor records successful inspections by page revision. Repeating the same accessibility
snapshot or screenshot while the page is unchanged is rejected before another browser call. The
rejection and existing success ledger are included in the next model decision. A successful
screenshot response satisfies a capture request even though screenshot pixels remain outside the
model context.

## Action and network policy

The safety checks serve as a second client-side gate. They constrain URLs to HTTP(S), reject
credential-bearing and ambiguous destinations, enforce exact hostnames, and reject recognized
private destinations unless explicitly enabled. The current snapshot supplies references for
interactions. By default, a click on a recognized link is converted into a validated navigation;
generic clicks are denied unless `--allow-mutations` is set. Known authentication, destructive,
publishing, and other state-changing labels/routes are rejected. The exposed tools never include
arbitrary JavaScript execution. A rejected proposal consumes a model iteration but no browser-call
budget because the action is not executed.

Element targets use bare snapshot references: `[ref=e43]` means `target="e43"` (or `ref="e43"`
when advertised by the tool). The executor accepts unambiguous `ref=e43`, `[ref=e43]`, and `#e43`
wrappers only when the underlying reference exists in the latest snapshot. CSS/XPath selectors
remain invalid. Rejections include current reference examples; the latest executor feedback is
prioritized ahead of page text so a large snapshot cannot hide a correction.

Typing and multi-field filling require `--allow-form-input`; `--allow-form-submission` additionally
permits `browser_type` to press Enter after typing and implies input permission. Each target must be
an element reference from the latest snapshot; observed Submit and Search controls may also be
clicked. Values are bounded, and fields associated with credentials, secrets, tokens, payment
data, authentication, or destructive actions remain denied.

These checks cannot contain redirects, subresource requests, page scripts, DNS rebinding, or other
browser-side network activity. They supplement external browser egress restrictions and a
read-only test environment; they do not replace them. `BROWSER_NETWORK_RESTRICTED=true` only
records the operator's assertion that those controls are already in place.

## Configuration and commands

Browser exploration settings use their own configuration in `browser_exploration/config.py`.
The key values are:

| Setting | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | Authenticates actual model requests; it is not needed by `check`. |
| `OPENAI_MODEL` | Model for browser decisions; defaults to `gpt-4.1-mini`. |
| `BROWSER_REASONING_EFFORT` | Optional model reasoning effort; `--reasoning-effort` overrides it. |
| `BROWSER_MODEL_API` | `auto` (default), `responses`, or `chat_completions`; `--model-api` overrides it. |
| `PLAYWRIGHT_MCP_URL` | MCP Streamable HTTP URL; defaults to `http://127.0.0.1:8931/mcp`. |
| `BROWSER_NETWORK_RESTRICTED` | Required operator attestation for browser-side egress controls. |
| `BROWSER_ALLOW_PRIVATE_NETWORK` | Explicit opt-in for authorized local/private destinations. |
| `BROWSER_MAX_STEPS` | Maximum browser tool calls, including initial navigation and snapshot. |
| `BROWSER_MAX_MODEL_ITERATIONS` | Maximum model decisions. |
| `BROWSER_MAX_DURATION_SECONDS` | Overall run duration. |
| `BROWSER_OUTPUT_DIR` | Parent directory for run artifacts; defaults to `runs`. |

The CLI loads `.env` without replacing variables already exported by the shell. CLI flags override
the run budgets. `--model` overrides both model environment settings; `LLM_MODEL` belongs to the
other workflow and does not configure `qa-browser`. The startup log and final command output show
the selected model. For example:

```bash
uv run qa-browser check
uv run qa-browser check-model --model gpt-5.6-luna
uv run qa-browser check-model --model gpt-5.6-sol --reasoning-effort medium --model-api responses

uv run qa-browser run \
  --model gpt-4.1-mini \
  --url https://example.test/ \
  --goal 'Explore the read-only page and report observable behavior.' \
  --allowed-domain example.test \
  --max-steps 12 \
  --max-model-iterations 8
```

`check` verifies MCP. `run` also needs a model key and model access. A successful `check` therefore
does not prove that an OpenAI request will succeed. `schemas` exports the Pydantic artifact
schemas.

`check-model` makes one small structured model request without starting MCP or a browser. It uses
the same adapter as exploration, prints the selected/provider model and available safe error
diagnostics, and exits nonzero on failure. Use `--timeout-seconds` to adjust its 30-second deadline.
For selection through `.env`, set `OPENAI_MODEL=...`; `LLM_MODEL` configures the other workflow.
Exported variables take precedence over `.env`, and `--model` takes precedence over both.

With `BROWSER_MODEL_API=auto`, GPT-5.6 Sol, Luna, the `gpt-5.6` alias, GPT-6 Astra, Sol, Luna,
GPT-6.1 Sol and their dated snapshots use `/v1/responses`. Other models keep
`/v1/chat/completions`. Use `--model-api responses` to
select Responses explicitly, including for another supported model. This follows the
[reasoning API guidance](https://developers.openai.com/api/docs/guides/reasoning) while retaining
the same non-strict `Decision` function contract and local validation. Responses requests set
`store=false`; each decision receives the bounded context rather than a server-side conversation.

GPT-5.6 Sol/Luna default to `medium` reasoning. Responses serializes this as
`reasoning: {"effort": "medium"}`; Chat Completions uses `reasoning_effort`. Other models omit
the parameter unless you set it. Choose a model-supported value from `none`, `low`, `medium`,
`high`, `xhigh`, or `max`; a non-reasoning model such as `gpt-4.1-mini` should leave the setting
unset. The [function-calling guide](https://developers.openai.com/api/docs/guides/function-calling)
uses reasoning disabled in its GPT-5.6 Chat Completions examples. Changing the effort to `medium`
alone does not address API compatibility for reasoning with tools.

GPT-6 Astra and GPT-6.1 Sol require Responses for function calling. GPT-6 Sol/Luna also use
Responses automatically so reasoning with tools works. For GPT-6 models, an unset effort uses
the provider's default; pass `--reasoning-effort medium` to select it explicitly. Astra does not
support `none` reasoning. See the
[official compatibility guidance](https://developers.openai.com/api/docs/guides/deployment-checklist).

Both commands accept `--reasoning-effort` and `--model-api`, report the effective settings, and
never retry a provider rejection by silently switching models, APIs, or reasoning effort.

## Results and debugging

Each `run` writes `runs/<run_id>/result.json`, `trajectory.jsonl`, and `metrics.json`.
`run_config.json` records the configured model, effective API and reasoning effort, adapter, permissions,
and budgets without reading credentials into the artifact. `decisions.jsonl` records every
validated decision and each rejected proposal, including repeated attempts, as well as safe
diagnostics for invalid model responses.
Decisions also retain the provider-reported model when available. Rejection logs include a redacted reason. These files
support comparison across models; older runs without model metadata cannot be attributed
retroactively from the current `.env`. Raw evidence
is disabled by default; enable `--retain-raw-evidence` only when full page payloads are needed and
approved for storage. Artifacts may contain application content, and redaction is best effort.

Oversized snapshot summaries omit link query strings before truncating page text, retaining more
headings and result details within the configured observation limit. Retained raw evidence is
stored separately with query values redacted for both absolute and relative snapshot URLs.

The result records findings, verified behaviors, unresolved questions, evidence references,
observed browser actions, status, errors, and measured counters. Provider token usage remains null
when the provider does not report it. Confirmed findings require successful evidence and
reproduction steps; unsupported confirmations are downgraded to inconclusive.

For a failure, inspect `status`, `errors`, and `metrics` in `result.json`. If navigation and
snapshot succeeded but the run failed on its first model call, the MCP connection and browser path
worked; inspect model configuration, account access, quota, and network connectivity. Provider
exceptions retain only allowlisted HTTP status, error code, and parameter metadata. Transport
errors wrapped by the model client are inspected through their causes without retaining raw
messages, request bodies, headers, or credentials. Use `check-model` to distinguish model access,
unsupported parameters, authentication, quota, and connection failures. If `check` fails, debug
the endpoint, server process, transport, and discovered tool compatibility first.

## Source map

- CLI and configuration: `src/browser_exploration/cli.py`, `config.py`
- MCP setup and discovery: `mcp_client.py`, `browser_tools.py`
- Run lifetime and graph: `explorer.py`, `graph.py`, `state.py`
- Prompt, context, and model: `prompts.py`, `context_builder.py`, `model.py`
- Policy and results: `safety.py`, `contracts.py`, `reporting.py`, `telemetry.py`
- Host workflow and domain models: owned by the consuming application, outside this repository.
