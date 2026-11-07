#!/usr/bin/env bash
# Browser Exploration Agent command cookbook.
#
# Run `./examples.sh list` to see the 25 examples.
# Run `./examples.sh 04` to execute one example. No example runs by default.
# Live model/browser calls and ReportPortal publication require explicit opt-ins.
# Start long-running services (fixture and MCP) in separate terminals.

set -euo pipefail

EXAMPLES_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$EXAMPLES_ROOT"

_require_live_opt_in() {
    if [[ "${RUN_LIVE:-}" != "1" ]]; then
        printf '%s\n' "This example can contact a live service or use a paid model."
        printf '%s\n' "Review its comments, then rerun with RUN_LIVE=1."
        exit 2
    fi
}

_require_case_id() {
    case "${CASE_ID:-NET-001}" in
        NET-001|NET-002|NET-003) ;;
        *)
            printf 'Unsupported CASE_ID: %s (use NET-001, NET-002, or NET-003)\n' "${CASE_ID:-}"
            exit 2
            ;;
    esac
}

_show_examples() {
    cat <<'LIST'
Browser Exploration Agent examples (choose one number):

 01  Install the locked project environment
 02  Show the installed CLI commands
 03  Show live-run options and safe defaults
 04  Run the fully offline synthetic demonstration
 05  Export the public V1 JSON schemas to a fresh folder
 06  Read the network-evaluation cases and fixed budgets
 07  Display the independent network request oracle
 08  Check MCP/browser connectivity without calling a model
 09  Make one bounded model connectivity check
 10  Run focused offline network regressions
 11  Run the complete offline unit suite
 12  Test all grader challenge labels and mutations
 13  Inspect the 30-case grader challenge corpus
 14  Start the controlled network fixture server
 15  Check that the local NET-003 fixture page is served
 16  Show the saved-run grader's command options
 17  Grade one existing saved run
 18  Challenge three existing passing saved runs
 19  Audit saved run results without changing source runs
 20  Inspect a saved run's compact result summary
 21  Run one bounded real-model smoke case (synthetic browser)
 22  Run the large-list NET-003 real-model edge case
 23  Run all six bounded real-model component and metric cases
 24  Run one controlled real-browser network evaluation
 25  Publish the selected grader tests to ReportPortal

Examples that need a saved run accept CASE_ID and RUN_DIR. Example 18 also accepts
NET_001_RUN_DIR, NET_002_RUN_DIR, and NET_003_RUN_DIR. See each command's comments.
LIST
}

# 01. Install exactly the versions recorded by uv.lock; this does not start the app.
example_01() {
    uv sync --locked
}

# 02. List the installed `qa-browser` CLI commands without opening MCP or a model connection.
example_02() {
    uv run qa-browser --help
}

# 03. See required URL/goal options, task budgets, domain limits, and disabled-by-default
#    mutation/form permissions before trying a live run.
example_03() {
    uv run qa-browser run --help
}

# 04. Exercise the production graph and artifact writer with scripted model/browser fakes.
#    The output is explicitly synthetic; it does not visit example.com or call an LLM.
example_04() {
    uv run --no-sync --offline python examples/browser_exploration/mock_run.py \
        --output-dir "runs/examples/mock-$(date +%Y%m%dT%H%M%S)"
}

# 05. Export public task/result schemas into a timestamped new directory so existing schema
#    files are not overwritten. This command requires no MCP server or API key.
example_05() {
    local schema_dir
    mkdir -p runs/examples
    schema_dir="$(mktemp -d runs/examples/schemas-XXXXXX)"
    uv run qa-browser schemas --output-dir "$schema_dir"
}

# 06. Read the versioned network dataset: shared objective, case paths, and fixed limits.
#    Expected request details live separately in requests.csv and are not part of the task.
example_06() {
    uv run python -m json.tool evals/network_capture/cases.json
}

# 07. Display the independent expected-request oracle, including methods and response statuses.
#    The oracle is for evaluation; do not copy its expected outcomes into the agent prompt.
example_07() {
    uv run python -c 'import csv; from pathlib import Path; print("\n".join(" | ".join(row) for row in csv.reader(Path("evals/network_capture/requests.csv").open())))'
}

# 08. Initialize the configured MCP session, list browser tools, and check required capabilities.
#    This does not initialize the model. A missing server or network tool is reported as a failure.
example_08() {
    uv run qa-browser check
}

# 09. Send one short structured request to the configured model, without connecting to MCP.
#    This can incur provider usage. Run only after explicitly setting RUN_LIVE=1.
example_09() {
    _require_live_opt_in
    uv run qa-browser check-model --timeout-seconds 20
}

# 10. Run quick, offline network-focused regressions. They use injected MCP/model fakes and
#    enforce the unit suite's network restrictions.
example_10() {
    uv run python -m pytest -c pyproject.toml tests/unit -k network -q
}

# 11. Run the complete offline unit suite. No real browser or real model is enabled by this
#    command; the opt-in suites should remain skipped.
example_11() {
    uv run python -m pytest -c pyproject.toml tests/unit -q
}

# 12. Exercise all 30 grader challenges: seven valid controls and 23 intentionally corrupted
#    artifacts. This checks grader behavior; it is not an agent/model performance benchmark.
example_12() {
    uv run python -m pytest -c pyproject.toml \
        tests/unit/browser_exploration/test_grader_challenges.py -q
}

# 13. Inspect every labeled challenge, including the expected failed check for each corruption.
#    Edge examples include contradictory static flags, foreign origins, broken JSON and bad times.
example_13() {
    uv run python -m json.tool evals/network_capture/grader_cases.json
}

# 14. Serve the deterministic local pages and script chain on loopback. This command stays in
#    the foreground; leave it running and start the configured MCP browser in another terminal.
#    For containerized MCP, loopback may not be shared. See evals/network_capture/README.md.
example_14() {
    uv run python -m evals.network_capture.fixture --bind 127.0.0.1 --port 8766
}

# 15. Check the local NET-003 page responds and exposes its fixture HTML. This only contacts
#    the local fixture; it does not exercise the browser agent or prove that MCP can reach it.
example_15() {
    curl --fail --show-error --silent \
        http://127.0.0.1:8766/cases/NET-003/ | sed -n '1,20p'
}

# 16. Read the offline grader's required case/run arguments and supported case IDs.
example_16() {
    uv run python -m evals.network_capture.grade --help
}

# 17. Grade one saved run without contacting a model, browser, fixture or LangSmith. Set
#    CASE_ID=NET-001 (or NET-002/NET-003) and RUN_DIR to that run's existing artifact directory.
#    Missing/malformed files fail closed; a model-directed `completed` status alone cannot pass.
example_17() {
    _require_case_id
    : "${RUN_DIR:?Set RUN_DIR to an existing saved-run directory}"
    if [[ ! -d "$RUN_DIR" ]]; then
        printf 'Saved run directory does not exist: %s\n' "$RUN_DIR" >&2
        exit 2
    fi
    uv run python -m evals.network_capture.grade \
        --case-id "${CASE_ID:-NET-001}" \
        --run-dir "$RUN_DIR"
}

# 18. Make a separate private copy of each supplied passing run and benchmark all 30 grader
#    challenges. Set NET_001_RUN_DIR, NET_002_RUN_DIR and NET_003_RUN_DIR to passing controls.
#    The output must be new; the runner refuses existing folders and never edits the controls.
example_18() {
    : "${NET_001_RUN_DIR:?Set NET_001_RUN_DIR to a passing NET-001 control}"
    : "${NET_002_RUN_DIR:?Set NET_002_RUN_DIR to a passing NET-002 control}"
    : "${NET_003_RUN_DIR:?Set NET_003_RUN_DIR to a passing NET-003 control}"
    uv run python -m evals.network_capture.challenges \
        --control "NET-001=$NET_001_RUN_DIR" \
        --control "NET-002=$NET_002_RUN_DIR" \
        --control "NET-003=$NET_003_RUN_DIR" \
        --output-dir "runs/evals/grader-challenges/$(date +%Y%m%dT%H%M%S)"
}

# 19. Recompute the saved-run quality audit. It reads existing run artifacts, excludes recognized
#    derived grader copies, and writes a timestamped report rather than replacing prior reports.
example_19() {
    uv run python -m evals.run_quality \
        --runs-dir runs \
        --output "runs/quality/examples-$(date +%Y%m%dT%H%M%S).json"
}

# 20. Print safe, high-level fields from an existing result.json. This helps distinguish a
#    failed/infrastructure-blocked run from a completed run before asking the grader to score it.
example_20() {
    : "${RUN_DIR:?Set RUN_DIR to an existing saved-run directory}"
    if [[ ! -f "$RUN_DIR/result.json" ]]; then
        printf 'No result.json found under: %s\n' "$RUN_DIR" >&2
        exit 2
    fi
    uv run python - "$RUN_DIR/result.json" <<'PY'
import json
import sys
from pathlib import Path

result = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(json.dumps({
    "run_id": result.get("run_id"),
    "status": result.get("status"),
    "target_url": result.get("target_url"),
    "errors": [item.get("code") for item in result.get("errors", [])],
    "metrics": result.get("metrics", {}),
}, indent=2))
PY
}

# 21. Ask the real model to capture the four-request NET-001 task using the synthetic MCP
#    browser. This is a bounded provider smoke test (at most two calls), not a live browser test.
#    It is opt-in because provider usage can cost money; usage may be unknown if the provider fails.
example_21() {
    _require_live_opt_in
    uv run python -m pytest -c pyproject.toml tests/live_llm \
        --live-llm -n 0 -k 001 --llm-max-calls 2 -v
}

# 22. Try the NET-003 large-list edge case with the real model and synthetic MCP data. It checks
#    whether late 500/404 failures survive a 203-request capture and compact summary; still only
#    a two-call provider allowance, and still not a real browser run.
example_22() {
    _require_live_opt_in
    uv run python -m pytest -c pyproject.toml tests/live_llm \
        --live-llm -n 0 -k 003 --llm-max-calls 2 -v
}

# 23. Run the three baseline and three metric-focused checks with the real model and synthetic
#    browser. The harness caps this session at twelve provider attempts and two per case. Provider
#    failure can stop later cases; review the session report before interpreting skipped cases.
example_23() {
    _require_live_opt_in
    uv run python -m pytest -c pyproject.toml tests/live_llm \
        --live-llm -n 0 --llm-max-calls 12 -v
}

# 24. Run one actual browser/MCP capture against the controlled fixture. Start the fixture and a
#    restricted MCP server first; each must be reachable from the other. Set RUN_LIVE=1 and, when
#    needed, FIXTURE_BASE_URL to the browser-reachable fixture address. Loopback is not shared
#    across containers. This makes real provider calls. Use fresh browser context per repetition.
#    The case oracle stays out of the model objective; the offline grader checks the saved run.
example_24() {
    _require_live_opt_in
    _require_case_id
    local fixture_base case_path fixture_host eval_goal
    fixture_base="${FIXTURE_BASE_URL:-http://127.0.0.1:8766}"
    case "${CASE_ID:-NET-001}" in
        NET-001) case_path="/cases/NET-001/" ;;
        NET-002) case_path="/cases/NET-002/" ;;
        NET-003) case_path="/cases/NET-003/" ;;
    esac
    fixture_host="$(FIXTURE_BASE_URL="$fixture_base" uv run python -c \
        'import os; from urllib.parse import urlsplit; print(urlsplit(os.environ["FIXTURE_BASE_URL"]).hostname or "")')"
    if [[ -z "$fixture_host" ]]; then
        printf 'FIXTURE_BASE_URL must be an HTTP(S) URL with a host.\n' >&2
        exit 2
    fi
    eval_goal="$(uv run python -c \
        'from evals.network_capture.dataset import load_dataset; print(load_dataset()["objective"])')"
    BROWSER_MAX_RECENT_OBSERVATIONS=5 \
    BROWSER_MAX_OBSERVATION_CHARS=6000 \
    BROWSER_MAX_CONTEXT_CHARS=18000 \
        uv run qa-browser run \
            --url "${fixture_base%/}${case_path}" \
            --allowed-domain "$fixture_host" \
            --goal "$eval_goal" \
            --max-steps 10 \
            --max-model-iterations 8 \
            --max-duration-seconds 120 \
            --retain-raw-evidence \
            --output-dir "runs/evals/network-capture/${CASE_ID:-NET-001}-$(date +%Y%m%dT%H%M%S)"
}

# 25. Run only grader-focused unit tests, then publish their JUnit results to ReportPortal.
#    This sends test names/statuses to the configured server and creates a launch. Requires the
#    ignored .env.reportportal key and reachable server. Set UPLOAD_REPORTPORTAL=1 explicitly.
#    If verification is uncertain, inspect the receipt before retrying: writes are not replayed.
example_25() {
    if [[ "${UPLOAD_REPORTPORTAL:-}" != "1" ]]; then
        printf '%s\n' "This example uploads a test launch to ReportPortal."
        printf '%s\n' "Review the destination and rerun with UPLOAD_REPORTPORTAL=1."
        exit 2
    fi
    uv run python -m scripts.reportportal -- -k grader -n 0
}

if (( $# > 1 )); then
    printf 'Choose one example number or use "list".\n\n' >&2
    _show_examples >&2
    exit 2
fi

case "${1:-list}" in
    list|-h|--help)
        _show_examples
        ;;
    01) example_01 ;;
    02) example_02 ;;
    03) example_03 ;;
    04) example_04 ;;
    05) example_05 ;;
    06) example_06 ;;
    07) example_07 ;;
    08) example_08 ;;
    09) example_09 ;;
    10) example_10 ;;
    11) example_11 ;;
    12) example_12 ;;
    13) example_13 ;;
    14) example_14 ;;
    15) example_15 ;;
    16) example_16 ;;
    17) example_17 ;;
    18) example_18 ;;
    19) example_19 ;;
    20) example_20 ;;
    21) example_21 ;;
    22) example_22 ;;
    23) example_23 ;;
    24) example_24 ;;
    25) example_25 ;;
    *)
        printf 'Unknown example: %s\n\n' "$1" >&2
        _show_examples >&2
        exit 2
        ;;
esac
