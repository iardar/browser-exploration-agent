# Operating the browser exploration agent

This guide covers installation, controlled browser setup, configuration, and run artifacts.
For the run lifecycle, read [How it works](how_it_works.md). The
[architecture](../architecture.md) describes the implementation; the
[V1 contract](../contracts/browser_exploration_v1.md) defines public behavior, and the
[adapter design](../adapter_design.md) explains host integration.

## Install and diagnose dependencies

Run from the repository root with Python 3.12:

```bash
uv sync --locked
uv run qa-browser --help
```

The Python client does not require the Python Playwright package. The operator starts a separate
Microsoft Playwright MCP server with a compatible installed browser. The client does not start
that server, launch a local browser, or reuse an IDE's MCP session.

Use this pinned host setup for normal application runs:

```bash
npx --yes @playwright/mcp@0.0.83 --browser chromium --headless --isolated \
  --port 8931 --host 127.0.0.1 \
  --allowed-hosts localhost:8931,127.0.0.1:8931
```

The `--isolated` flag keeps the browser profile in memory instead of loading or saving persistent
profile state. If an existing host process was started without that flag, stop it and restart it
with the command above before relying on disposable profile state. This is a pinned evaluated
version, not a claim about the latest release. The Python dependencies retain MCP SDK
`>=1.26,<2`; update the SDK and `langchain-mcp-adapters` together and validate session continuity
after an upgrade.

The application and the network evaluation use different MCP deployments:

- Normal application: host Playwright MCP at `http://127.0.0.1:8931/mcp`, started with the
  `npx` command above.
- Network evaluation: pinned Compose stack at `http://127.0.0.1:8932/mcp`, started with the
  command in the evaluation runbook.

The evaluation runner inspects the Docker containers, image digests, launch flags and network.
It therefore rejects the host `npx` endpoint even when that server is otherwise usable by the
normal application. See the
[evaluation runbook](../../evals/network_capture/README.md#run-the-langsmith-agent-capture-evaluation).

The commands check different dependencies:

| Command | What it establishes | External interaction |
| --- | --- | --- |
| `uv run qa-browser check` | MCP initialization and supported tool discovery | MCP only; no navigation or model request |
| `uv run qa-browser check-model --model YOUR_APPROVED_MODEL` | One bounded structured decision request | Model provider only; no browser |
| `uv run qa-browser run ...` | A bounded exploration with an actual terminal result | Model provider, MCP/browser, and target |
| `uv run qa-browser schemas` | Public schema export | None |

Replace `YOUR_APPROVED_MODEL` with a model available to your configured provider/account.
`check-model` defaults to a 30-second deadline; `--timeout-seconds` changes it. Successful MCP
discovery does not establish model access, and a successful model check does not establish browser
availability or exploration quality.

`check` requires navigation and accessibility snapshot capabilities. Optional tools are discovered
only when known names and compatible schemas are available. One initialized MCP session remains
open for the whole exploration run.

## Safety prerequisites

Use an authorized controlled application and a dedicated or isolated browser context. Navigation,
hover, HTTP GET requests, redirects, and page scripts can have side effects. A control label or
server annotation does not prove an operation harmless.

Before a live exploration, independently restrict browser-originated requests, including redirects,
subresources, private destinations, and metadata endpoints. Use application permissions and fixture
state appropriate to the allowed interactions. Set `BROWSER_NETWORK_RESTRICTED=true` only after
those controls exist: it is an operator attestation, not a firewall or a verification of the network.
MCP host/origin flags and Python URL checks do not replace that browser network boundary.

The client checks exact hostnames, URL shape, literal IP ranges, and recognizable private hosts.
It does not resolve DNS or prevent rebinding. Private/local targets also require
`BROWSER_ALLOW_PRIVATE_NETWORK=true`. Additional authorized hosts need repeated `--allowed-domain`
flags; a parent hostname does not implicitly authorize subdomains.

Default policy permits observed link navigation and supported inspections. Generic clicks, input,
and submission have separate flags:

| Flag | Permission added | Remaining restrictions |
| --- | --- | --- |
| `--allow-mutations` | Certain generic clicks on observed references | Known authentication, destructive, payment, and publishing controls remain denied |
| `--allow-form-input` | Bounded typing and filling observed non-sensitive fields | No credentials, tokens, payment data, or unsupported field types |
| `--allow-form-submission` | Supported submission and observed Submit/Search controls; implies input permission | Does not bypass sensitive-control or destination checks |

Arbitrary JavaScript, uploads, filesystem tools, and credential management are excluded. The
label/route checks are heuristic; permission flags do not prove that an operation is safe.

## Controlled fixture and MCP smoke test

The bundled two-page fixture has static navigation, a hover hint, and a local disclosure control.
It has no login, forms, external assets, or persistence endpoint.

The Compose example runs MCP and a static server:

```bash
docker compose -f examples/browser_exploration/compose.yaml up -d
export PLAYWRIGHT_MCP_URL=http://127.0.0.1:8931/mcp
uv run qa-browser check

QA_BROWSER_LIVE_MCP=1 uv run pytest -c pyproject.toml \
  tests/integration/browser_exploration/test_live_mcp.py -v
```

Wait for `check` to succeed. The browser and fixture share a network namespace, so the host Python
client and browser can both use `http://127.0.0.1:8765/`. Only loopback ports 8931 and 8765 are
published. The internal Docker network prevents normal external routing but is not a hardened
boundary against the Docker host. Keep untrusted applications and privileged services off this
demo network. Its image tags need deployment-specific pinning; its sandbox exception is specific
to the controlled demo. MCP output is ephemeral tmpfs.

The live test needs no model key. It navigates and snapshots both fixture pages in one session.
It checks transport continuity, not autonomous model quality. `QA_BROWSER_LIVE_URL` accepts only
loopback HTTP hosts and must still serve the bundled content. Without `QA_BROWSER_LIVE_MCP=1`,
the test skips even when the full suite is selected.

Alternatively, serve the fixture alongside a separately started native MCP process:

```bash
python -m http.server 8765 --bind 127.0.0.1 --directory examples/browser_exploration
```

The native option still needs independently enforced browser restrictions. The static HTTP server
is the application fixture, not a browser automation engine. Image downloads and Docker/browser
availability must be checked on the deployment host; this guide does not claim a new live check.

## Run an exploration

After the fixture and external controls are ready, configure credentials through the environment
or an untracked `.env`. Do not put actual keys in commands or committed examples.

For the host setup, keep the `npx` MCP process running in one terminal and the target or fixture
running in another. The following commands run in a third terminal from the repository root:

```bash
export OPENAI_MODEL=YOUR_APPROVED_MODEL
export PLAYWRIGHT_MCP_URL=http://127.0.0.1:8931/mcp
# Supply OPENAI_API_KEY through your environment or secret manager.
# Attest only after independently enforcing the network controls above.
export BROWSER_NETWORK_RESTRICTED=true

uv run qa-browser check
uv run qa-browser check-model --model YOUR_APPROVED_MODEL

BROWSER_ALLOW_PRIVATE_NETWORK=true uv run qa-browser run \
  --url http://127.0.0.1:8765/ \
  --goal 'Explore the static article and its read-only links. Report observed navigation and unresolved interactions without changing data.' \
  --allowed-domain 127.0.0.1 \
  --max-steps 12 --max-model-iterations 8 --max-duration-seconds 90 \
  --output-dir runs
```

For another authorized target, provide its trusted URL and exact allowed hosts. The isolated demo
has no normal external route and cannot be used as a general public-site exploration setup.
`--requirement` and repeated `--context` flags add optional reference data; `run --help` lists all
options. They do not grant permissions.

Direct Python callers use `BrowserExplorer.explore(task)` as documented in the
[V1 contract](../contracts/browser_exploration_v1.md). Construct explicit task limits and run-local
settings; Settings defaults are not automatically copied into independently constructed tasks.
Direct Settings construction does not load `.env` for you. Inspect returned status, including
`cancelled`, and handle task-validation or artifact-storage exceptions at the caller boundary.

Stop the host MCP process and fixture with `Ctrl-C` when finished. If you used the controlled
Docker demo instead, shut it down with:

```bash
docker compose -f examples/browser_exploration/compose.yaml down
```

## Configuration

The CLI loads `.env` without replacing exported variables. Explicit model/API/reasoning options
override environment settings. `--model` overrides `OPENAI_MODEL` and `BROWSER_MODEL`; the parent
workflow's `LLM_MODEL` does not configure this package. CLI budget flags override Settings defaults
when constructing the actual task.

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | unset | Required for actual model calls, not MCP checks or schema export |
| `OPENAI_MODEL` / `BROWSER_MODEL` | `gpt-4.1-mini` | Configured decision model; account access is not guaranteed |
| `BROWSER_MODEL_API` | `auto` | API mode; explicit `--model-api` overrides it |
| `BROWSER_REASONING_EFFORT` | unset | Optional effort; effective model-specific defaults come from Settings |
| `PLAYWRIGHT_MCP_URL` / `BROWSER_MCP_URL` | `http://127.0.0.1:8931/mcp` | Streamable HTTP endpoint; non-loopback endpoints require HTTPS |
| `BROWSER_OUTPUT_DIR` | `runs` | Parent directory for artifacts |
| `BROWSER_MAX_STEPS` | `30` | Attempted browser calls, including bootstrap and automatic snapshots |
| `BROWSER_MAX_MODEL_ITERATIONS` | `20` | Model requests, including corrections and finish decisions |
| `BROWSER_MAX_DURATION_SECONDS` | `120` | Cooperative async run budget |
| `BROWSER_TOOL_TIMEOUT_SECONDS` | `20` | Individual browser operation timeout |
| `BROWSER_MAX_RECENT_OBSERVATIONS` | `5` | Recent observations considered for context |
| `BROWSER_MAX_OBSERVATION_CHARS` | `6000` | Bounded observation/context entry text |
| `BROWSER_MAX_CONTEXT_CHARS` | `18000` | Selected-message characters; tool schemas have a separate cap |
| `BROWSER_RETAIN_RAW_EVIDENCE` | `false` | Explicit raw payload retention |
| `BROWSER_NETWORK_RESTRICTED` | `false` | Required external-control attestation |
| `BROWSER_ALLOW_PRIVATE_NETWORK` | `false` | Separate private/local target permission |
| `BROWSER_LANGSMITH_ENABLED` | `false` | Explicit tracing opt-in |

For exact model-name routing and effective reasoning defaults, see
[`Settings.effective_model_api` and `effective_reasoning_effort`](../../src/browser_exploration/config.py).
These are implementation rules, not provider capability or availability guarantees. Set only
options supported by the selected model and diagnose them with `check-model`; rejected settings
never trigger a silent model/API/reasoning fallback.

LangSmith also uses `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, and its tracing environment. Selected
application content and objectives may be exported when tracing is enabled; enable it only for
approved data. Default runs disable model tracing.

## Results, evidence, and diagnostics

With writable storage, a run records the following beneath its unique output directory:

```text
<output_dir>/<run_id>/
  run_config.json     # Allowlisted model/task configuration, excluding credentials
  decisions.jsonl     # Decisions, rejections, and model errors when recorded
  trajectory.jsonl    # Bounded observations from attempted browser calls
  result.json         # BrowserExplorationResult, schema_version "1.0"
  metrics.json        # ExecutionMetrics
  evidence/           # Optional separately retained payloads and images
```

Start with `result.json` status/errors/metrics, then compare decisions with the trajectory's
executed actions. Default-policy link clicks can be rewritten as navigation. Provider model names
are retained on decision/error events when available; a current environment file cannot recover
missing historical attribution. If setup succeeds but the first model call fails, use
`check-model`; if `check` fails, inspect the endpoint, server, installed browser, and tool schemas.

`completed` means the model chose to finish, not that the objective passed. Evidence-ID and
reproduction checks establish traceability, not semantic truth. Pixels are unavailable to the
decision model. Missing provider usage remains `null`, and operation counts are not functional
coverage. The [contract](../contracts/browser_exploration_v1.md) defines every status and field.

Raw retention defaults to off; bounded summaries and redacted action metadata remain in the
trajectory. `retained=false` does not make the original payload recoverable. Pattern redaction is
best effort and does not redact pixels. Use approved sanitized data and independently manage MCP
server files, browser profiles, logs, and retention. Storage failure can leave partial files or
raise before a valid result is returned.

### Network evidence and model context

When the agent calls `browser_network_requests` (or its supported Playwright-prefixed alias), a
successful response receives a compact network summary. It reports recognized request counts,
HTTP 4xx/5xx responses, transport failures, and unknown outcomes. Up to five request details are
shown, with failures before unknown outcomes and other responses. For example:

```text
Network requests parsed: 18; HTTP errors: 1; failed: 1; unknown outcome: 0.
Static requests omitted by tool: 21.
Request details omitted: 13.
[500] GET https://example.com/api/articles Server Error
[FAILED] GET https://example.com/api/tags net::ERR_CONNECTION_RESET
[200] GET https://example.com/
[200] GET https://example.com/api/profile
[200] GET https://example.com/api/settings
```

The network summary is capped at 1,000 characters, or the task's smaller observation allowance.
It replaces the full listing
in `trajectory.jsonl` and model context, which remains subject to the overall message budget.
Unrecognized lines and unknown outcomes are disclosed. An unsupported or empty response format
retains a bounded redacted excerpt with unknown counts rather than implying there were no errors.
Counts describe the recognized lines returned by the tool; excluded traffic is not assessed.

Enable `BROWSER_RETAIN_RAW_EVIDENCE=true` or add `--retain-raw-evidence` to `run` to also save the
complete redacted tool response at `evidence/obs-XXXX.json`. The observation's `evidence_ref` and
`result.json` evidence entry link to that file. Keeping the artifact does not add its contents to
model context. With retention disabled, only the bounded summary and metadata remain.

This feature adds no automatic network captures and does not change tool arguments or static
filtering. Request collection in the objective when needed; collection consumes the existing
browser-call budget. It stores the returned request listing, not a HAR, headers, or response
bodies. Tool-level failures retain their normal diagnostic summaries.

### Schema export

Export schemas without external services:

```bash
uv run qa-browser schemas --output-dir docs/browser_exploration/schemas
```

For the network-free mock, unit checks, coverage commands, and opt-in integration scope, use the
[testing guide](testing.md). For editor profiles and breakpoints, use the
[debugging guide](../debugging.md).
