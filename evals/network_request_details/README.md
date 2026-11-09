# Network request-detail evaluation

Use a separate dataset and experiment family for `browser_network_request`. The existing
[`network-capture-v1`](../network_capture/README.md) measures complete **listings** on three
pages; its grader cannot tell whether the agent selected the right request or inspected a header
or body. This corpus keeps the capabilities comparable as separate scores while reusing the same
isolated Compose deployment, fixture origin, browser session owner and artifact format.

`network-request-details-v2` has **22 distinct cases**, each with a decoy request before the
target. The agent receives the target method, route and requested part, with no expected value.
[`cases.json`](cases.json) defines the inputs and budgets. [`oracle.json`](oracle.json) defines
the controlled payloads and expected values; it is never inserted into the model task.
The original 18 inputs are frozen in [`v1/`](v1/), and the
[first experiment review](baseline_review.md) explains its grader correction.

| Cases | Behavior measured |
| --- | --- |
| DET-001–009 | JSON, HTTP 500, plain text, XML, HTML, SVG, problem JSON, Unicode, and a long textual response body |
| DET-010–011 | Exact request-header and response-header selection |
| DET-012–014 | JSON, form and text POST request bodies |
| DET-015–016 | Report a public value while a synthetic secret in JSON or an authorization header is redacted |
| DET-017–018 | Verify an octet-stream or PNG overview and stop without requesting binary response content |
| DET-019 | Distinguish `/target` from a same-method, same-status `/target-preview` |
| DET-020 | Distinguish GET and POST requests to the same URL |
| DET-021 | Distinguish two GETs to the same URL by HTTP 200 versus 503 |
| DET-022 | Distinguish identical listing lines by inspecting candidate request headers |

The fixture generates the target request during parser-blocking page load. Its `Network fixture
ready` heading appears after the request completes, so readiness uses the agent's ordinary
navigation and snapshot. POST routes only check their payload; they do not change server state.
The grader checks the retained listing's target method, URL, status and visible index; the
matching overview; the selected part or an already visible header value and its independent
marker; an evidence-backed result
claim; explicit detail permission; budgets; and secret redaction. Binary cases require an
overview with the declared MIME and no body call **or rejected body proposal**. A `completed`
status alone never passes. The grader does not claim to judge arbitrary prose quality.

See the [evaluation metrics reference](../../docs/evaluation_metrics.md) for every feedback
score, grader check and execution field, with examples.

## Offline verification

No credentials, model or browser are needed for the corpus and grader tests:

```bash
PYTHONPATH=.:src uv run pytest -c pyproject.toml \
  tests/unit/browser_exploration/test_network_detail_eval_dataset.py -n 0 -q
```

To grade a saved run:

```bash
uv run python -m evals.network_request_details.grade \
  --case-id DET-001 --run-dir runs/evals/network-request-details/REPLACE_WITH_RUN_ID
```

The grader reads local artifacts only. Keep `--retain-raw-evidence` enabled for evaluation;
private raw details are redacted and capped by the application before retention. Synthetic
secrets are for redaction checks and must not be copied to LangSmith outputs.

## Publish the separate LangSmith dataset and run experiments

This is opt-in. Install dependencies with `uv sync --locked`, configure `LANGSMITH_API_KEY` in
the environment or an untracked `.env`, and publish the exact local v2 examples:

```bash
uv run python -m evals.network_request_details.langsmith_eval --publish-dataset
```

The command creates `browser-network-request-details-native-v2` once, reads it back and checks
all 22 inputs and compact reference outputs against a source hash. If the name exists with
different content, it fails instead of editing that dataset. The reference outputs contain
public markers, not raw headers, bodies or synthetic secret values.

Start the same pinned, restricted stack used by network capture:

```bash
docker compose -f evals/network_capture/compose.yaml up -d
```

The updated fixture service mounts this corpus read-only and serves its routes on loopback port
8766; MCP remains on loopback port 8932. Ensure those ports are free. Set the environment flags
only after the external network boundary described in the
[capture deployment guide](../network_capture/README.md#run-the-langsmith-agent-capture-evaluation)
is active. `--check-only` validates the remote dataset, dedicated deployment, browser session
isolation, all 22 fixture/tool combinations and four required detail parts without calling the
model:

```bash
BROWSER_NETWORK_RESTRICTED=true BROWSER_ALLOW_PRIVATE_NETWORK=true \
uv run python -m evals.network_request_details.langsmith_eval --check-only
```

For a live agent experiment, configure `OPENAI_API_KEY` and `OPENAI_MODEL`, then run:

```bash
BROWSER_NETWORK_RESTRICTED=true BROWSER_ALLOW_PRIVATE_NETWORK=true \
uv run python -m evals.network_request_details.langsmith_eval \
  --experiment-prefix network-request-details-v2 --num-repetitions 1
```

One repetition makes 22 serial trials. Additional repetitions increase provider use; the CLI
allows 1–10. Each trial receives a new isolated browser session. Full artifacts and isolation
records remain under `runs/evals/network-request-details-langsmith/`, which is ignored by Git.
LangSmith receives only compact grades, check booleans, usage and run IDs. The evaluator records
the dataset source hash, fixture fingerprint, grader version, source snapshot and deployment
versions. Stop the stack when finished:

```bash
docker compose -f evals/network_capture/compose.yaml down
```

The offline replay verifies graph and grader behavior without a real browser. On 2026-11-09,
the v1 `--check-only` verified all 18 original cases. On 2026-11-10, the v2 dataset
(`c6a8ea9f-269a-4666-96bd-686c10c1581d`) was published and `--check-only` verified all
22 cases against the pinned MCP server, all four detail parts, LangSmith dataset readback, and
browser isolation with zero model calls. A real-model v2 experiment remains opt-in.
