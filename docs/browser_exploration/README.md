# Autonomous Browser Exploration Agent V1

`qa-browser` runs an independent LangGraph agent that observes a browser through an existing
Microsoft Playwright MCP server, chooses relevant actions with ChatOpenAI, and writes structured
QA results. It does not import, invoke, or alter the existing Agentic QA workflow.

For a component-by-component explanation of the run lifecycle and MCP connection, see
[how_it_works.md](how_it_works.md).

See the [system architecture design](../architecture.md),
[implementation notes](architecture.md) for the graph and contracts, and
[future_integration.md](future_integration.md) for the deferred integration boundary.

## Install and check the connection

Use the repository's Python **3.12** environment and `uv` lockfile:

```bash
uv sync
export PLAYWRIGHT_MCP_URL=http://127.0.0.1:8931/mcp
uv run qa-browser check
```

`check` opens an initialized Streamable HTTP MCP session, lists discovered tools, and validates
required browser capabilities. It does not require an OpenAI API key or launch a browser locally.
Navigation and accessibility snapshot capabilities are mandatory. Unsupported tool names or
schemas produce diagnostics instead of being guessed from arbitrary server descriptions.

The server must be Microsoft Playwright MCP, with a compatible installed browser. For a native
Linux installation, the server can be started separately using its supported CLI:

```bash
npx --yes @playwright/mcp@0.0.83 --headless --isolated --port 8931 --host 127.0.0.1 \
  --allowed-hosts localhost:8931,127.0.0.1:8931
```

Pin an evaluated MCP release in a repeatable deployment. The Python agent never embeds
Playwright or relies on an IDE MCP connection. One initialized session stays open for an entire
exploration run; a new connection is not created for each action. Use a dedicated MCP server or
isolated browser context rather than another user's authenticated browser.

The lockfile preserves the repository's LangGraph/LangChain versions and adds
`langchain-mcp-adapters` 0.2.2. MCP SDK is constrained to `>=1.26,<2` because adapter 0.2.2
imports APIs removed in MCP 2.x. Update the adapter and SDK together after validating the
persistent-session smoke test.

## Safety prerequisites

Run V1 against controlled read-only applications. Navigation, hover, redirects, page scripts,
and even HTTP GET requests can cause side effects in arbitrary applications. Button names and
MCP read-only annotations cannot establish that an operation is harmless.

Before an exploration, configure browser-side network controls that restrict destinations,
including redirects, subresources, private networks, and metadata endpoints. Also use a read-only
application deployment or credentials/environment with no mutation capability. Setting
`BROWSER_NETWORK_RESTRICTED=true` is an **operator attestation** that these deployment controls
exist; it does not install a firewall. Runs fail closed without that attestation. An allowlist in
the Python client is an additional check, not enforcement of all browser network requests.
Microsoft explicitly states that MCP `--allowed-origins` is not a security boundary and does
not cover redirects.

The client allows exact configured hostnames, checks URL schemes, literal IP addresses, and
recognizable local/private hostnames, and rechecks observed page destinations when available.
It does not resolve DNS or prevent DNS rebinding; those boundaries belong to the browser's
external network controls. Private and local targets require the
separate `BROWSER_ALLOW_PRIVATE_NETWORK=true` opt-in. By default, only the target hostname is
allowed; use repeated `--allowed-domain` flags for additional authorized hosts.

The exposed tool catalog excludes JavaScript execution, code execution, uploads, and credential
management. Generic clicks and form input are blocked by default. A click on a link from the latest
snapshot can be converted to validated URL navigation. Hover, snapshots, screenshots, console,
network inspection, typing, and multi-field form filling are available when compatible server
tools are present. The
`--allow-mutations` flag explicitly authorizes generic clicks on references from the latest
snapshot in a controlled application. It still denies known authentication, destructive, and
publishing controls and never enables arbitrary JavaScript or uploads. `--allow-form-input`
authorizes bounded text entry into observed, non-sensitive fields. `--allow-form-submission`
separately authorizes `browser_type` to submit with Enter; it also implies form-input permission.
It also permits observed Submit or Search controls to be clicked. Credential, token, payment,
authentication, and destructive fields remain denied. That
label-based rejection cannot prove a generic click is safe. Leave the flag disabled for the
demonstration objective; some visible interactions will remain unresolved.

## Run an exploration

Configure an API key in the process environment without committing it:

```bash
export OPENAI_MODEL=gpt-4.1-mini
# Set OPENAI_API_KEY through your shell or secret manager.
# Set this only after independently enforcing the safety prerequisites above.
export BROWSER_NETWORK_RESTRICTED=true

uv run qa-browser run \
  --url https://demo.realworld.show/article/agentic-qa-article-18db9f7677 \
  --goal 'Independently explore the article functionality. Identify available interactions and potential defects. Validate observable behavior where possible and produce reproducible QA findings. Do not authenticate, publish comments, or modify persistent data.' \
  --allowed-domain demo.realworld.show \
  --max-steps 20 \
  --max-model-iterations 12 \
  --max-duration-seconds 120 \
  --output-dir runs
```

The external example requires a suitably restricted browser deployment with access to that
host. The isolated Docker demo below deliberately has no normal external network route and
cannot run this external example. An unreachable application, unavailable MCP server, missing
API key, rejected model, timeout, or policy restriction must produce the corresponding actual
failure or partial result. No live external success is implied by this documentation.

Use `--requirement '...'` and repeated `--context '...'` flags for optional task context. Use
`uv run qa-browser run --help` for the complete CLI. `OPENAI_MODEL` is configurable; the default
`gpt-4.1-mini` is a candidate tool-capable model, not a guarantee of availability to your account.
An authenticated run is required to verify availability and structured-output support.

GPT-5.6 Sol/Luna automatically use the Responses API with `medium` reasoning. To make the
API choice explicit, add `--model-api responses` to `run` or `check-model`. For example:

```bash
uv run qa-browser check-model --model gpt-5.6-sol --model-api responses --reasoning-effort medium
```

GPT-6 Astra, Sol, Luna and GPT-6.1 Sol also select Responses automatically. Astra requires this
API for function calling; `--reasoning-effort medium` is supported. For example:

```bash
uv run qa-browser check-model --model gpt-6-astra --model-api responses --reasoning-effort medium
```

The asynchronous application interface is also independently usable:

```python
from browser_exploration import (
    BrowserExplorationResult,
    BrowserExplorationTask,
    BrowserExplorer,
)
from browser_exploration.explorer import AutonomousBrowserExplorer

explorer: BrowserExplorer = AutonomousBrowserExplorer()


async def explore_article() -> BrowserExplorationResult:
    return await explorer.explore(
        BrowserExplorationTask(
            url="https://demo.realworld.show/article/agentic-qa-article-18db9f7677",
            objective="Explore observable article behavior without authentication or data changes.",
            max_steps=20,
        )
    )
```

This uses the same environment configuration and safety prerequisites as the CLI. Application
callers receive a structured result on browser/model failure or cancellation, and terminal
results are written to the run directory when storage is writable. Inspect `result.status`; a returned result does not
imply successful completion.

## Reproduce the local MCP smoke test

The bundled fixture has static navigation, a hover hint, and a local disclosure control. It has
no login, forms, external assets, or persistence endpoint. It is independent of any company app.

The standalone Docker example uses the official MCP image and a static Python HTTP server:

```bash
docker compose -f examples/browser_exploration/compose.yaml up -d
export PLAYWRIGHT_MCP_URL=http://127.0.0.1:8931/mcp
uv run qa-browser check

QA_BROWSER_LIVE_MCP=1 uv run pytest \
  tests/integration/browser_exploration/test_live_mcp.py -v
```

Wait for `check` to succeed before running the test. The MCP and demo share a network namespace,
so both the host Python client and the browser use `http://127.0.0.1:8765/`. Only loopback ports
8931 and 8765 are published. An `internal: true` Docker network prevents normal external routing;
this is a controlled demo configuration, not a hardened production boundary against access to
the Docker host. Do not put untrusted applications or privileged services on this demo network.
MCP output uses an ephemeral tmpfs. The example does not modify other Docker Compose services.
Image download and Docker runtime availability must be verified on the deployment host.

The live integration test requires no model API key. It navigates to the fixture, reads an
accessibility snapshot, navigates to its details page, and checks another snapshot in the same
MCP session. It validates transport and browser continuity; it does not claim autonomous LLM
quality. `QA_BROWSER_LIVE_URL` may override the fixture URL, but the test accepts only loopback
HTTP hosts and requires the bundled fixture content. Without `QA_BROWSER_LIVE_MCP=1`, it skips
even when running the complete pytest suite.

For the full autonomous agent on this controlled demo:

```bash
BROWSER_NETWORK_RESTRICTED=true BROWSER_ALLOW_PRIVATE_NETWORK=true \
uv run qa-browser run \
  --url http://127.0.0.1:8765/ \
  --goal 'Explore the static article and its read-only links. Verify observed navigation and report unsupported or unresolved interactions. Do not modify data.' \
  --max-steps 12 --max-model-iterations 8 --max-duration-seconds 90

docker compose -f examples/browser_exploration/compose.yaml down
```

Alternatively, serve the fixture on the same host as a native MCP process:

```bash
python -m http.server 8765 --bind 127.0.0.1 --directory examples/browser_exploration
```

Use that native option only in a controlled environment with independently enforced browser
restrictions. The Python server is a fixture host, not a second browser automation engine.

## Configuration

The CLI loads `.env` through `python-dotenv` without replacing existing process environment
values; copy the relevant entries from `.env.example` or export them in your shell. Direct Python
callers of `Settings` use the process environment and may load `.env` themselves. CLI budget flags
override environment defaults.

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | unset | Required only for actual model calls |
| `OPENAI_MODEL` | `gpt-4.1-mini` | Configurable ChatOpenAI model |
| `BROWSER_MODEL_API` | `auto` | Responses for GPT-5.6 Sol/Luna and supported GPT-6 models, Chat Completions otherwise; override with `--model-api` |
| `BROWSER_REASONING_EFFORT` | model dependent | `medium` for GPT-5.6 Sol/Luna, omitted otherwise; override with `--reasoning-effort` |
| `PLAYWRIGHT_MCP_URL` | `http://127.0.0.1:8931/mcp` | Streamable HTTP endpoint; remote endpoints require HTTPS |
| `BROWSER_OUTPUT_DIR` | `runs` | Parent directory for run artifacts |
| `BROWSER_MAX_STEPS` | `30` | Maximum actual browser tool calls, including initial navigation and snapshot |
| `BROWSER_MAX_MODEL_ITERATIONS` | `20` | Maximum model requests |
| `BROWSER_MAX_DURATION_SECONDS` | `120` | Overall execution time budget |
| `BROWSER_TOOL_TIMEOUT_SECONDS` | `20` | Individual browser tool timeout |
| `BROWSER_MAX_RECENT_OBSERVATIONS` | `5` | Recent observations considered for each decision |
| `BROWSER_MAX_OBSERVATION_CHARS` | `6000` | Per-observation text cap |
| `BROWSER_MAX_CONTEXT_CHARS` | `18000` | Message-content character budget |
| `BROWSER_RETAIN_RAW_EVIDENCE` | `false` | Explicit opt-in for separate raw evidence artifacts |
| `BROWSER_NETWORK_RESTRICTED` | `false` | Attestation of external safety controls |
| `BROWSER_ALLOW_PRIVATE_NETWORK` | `false` | Allow authorized private/local destinations |
| `BROWSER_LANGSMITH_ENABLED` | `false` | Explicit tracing opt-in |

For LangSmith, also configure `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, and the standard
LangSmith tracing environment. Tracing may send selected browser content and objectives to the
tracing service; enable it only for approved data. Default runs disable model tracing.

## Results, evidence, and schemas

Each run writes a unique `runs/<run_id>/` directory:

```text
result.json          # BrowserExplorationResult, schema_version "1.0"
trajectory.jsonl     # One serialized BrowserObservation per recorded tool result
metrics.json         # ExecutionMetrics
evidence/            # Separate retained raw text/binary files, when opted in
```

Generate the authoritative Pydantic JSON schemas using:

```bash
uv run qa-browser schemas --output-dir docs/browser_exploration/schemas
```

`result.json` records the objective and target, real terminal status, timestamps, verified
behaviors, findings, unresolved questions, evidence references, observed actions, metrics, and
errors. Status is one of `completed`, `step_limit`, `model_limit`, `timeout`, `cancelled`,
`blocked`, or `failed`; budget exhaustion is never called completed. Findings distinguish
`confirmed`, `potential`, and `inconclusive`. Confirmed records require evidence references to
successful observations and reproduction steps; unknown or failed references are downgraded to
inconclusive. Screenshot-only evidence cannot confirm visual findings because V1's decision model
receives text observations, not pixels. These provenance checks do not independently establish
semantic correctness. Review the cited observation and reproduction steps before accepting a finding.

Metrics include duration, actual browser calls and successes/failures, model calls, provider
input/output/total token usage, findings count, and verified behavior count. Missing token
usage is `null`; no coverage percentage or fabricated token estimate is generated.

Raw evidence retention is off by default. Trajectory entries still contain bounded, redacted
observation summaries and action metadata. Evidence references describe whether a payload was
retained; absent payloads cannot be reconstructed from an identifier. Use
`--retain-raw-evidence` only for approved data when full reproduction evidence is needed.
Screenshots are binary artifacts, never inline base64 in JSON. Redaction removes known secret
patterns and sensitive argument fields, but cannot reliably identify every secret in arbitrary
page text or pixels. Screenshots cannot be text-redacted. Use sanitized test data.

Retention here controls **client-side** artifacts. MCP server snapshots, browser profiles,
downloads, diagnostic logs, and screenshots have independent retention policies. Configure
those separately; disabling client retention does not delete remote files. Never mount a
personal browser profile or unrelated credentials into the demo server.

## Validation and limitations

```bash
uv run pytest
uv run ruff check .
uv run mypy .
```

Unit tests use fake models and MCP sessions and need no internet, OpenAI, browser, or application.
The opt-in live test above is the transport smoke test. A successful fake run demonstrates
control flow and serialization, not real browser actions or application defects.

See [testing.md](testing.md) for the expanded negative-case suite, measured line and branch
coverage, offline isolation, and reproducible coverage commands.

To exercise the complete graph and artifact writer without external services:

```bash
uv run python examples/browser_exploration/mock_run.py --output-dir runs/mock
```

The example injects a fake MCP session and fake model into the real explorer. Its objective,
observations, and stdout identify it as `MOCK ONLY`; its token counts remain `null`. It writes
the same `result.json`, `trajectory.jsonl`, and `metrics.json` contracts used by live runs.

V1 uses deterministic context selection and truncation, not summarization or full-history
compression. It may miss page content beyond the text cap. Its action restrictions intentionally
limit exploration, and confirmed findings still require human review for QA correctness.
Application-specific authorization, authentication, network enforcement, secret detection,
functional coverage measurement, and integration into existing workflow nodes are outside V1.

Reference APIs: [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview),
[LangChain MCP](https://docs.langchain.com/oss/python/langchain/mcp),
[langchain-mcp-adapters](https://github.com/langchain-ai/langchain-mcp-adapters), and
[Microsoft Playwright MCP](https://github.com/microsoft/playwright-mcp).
