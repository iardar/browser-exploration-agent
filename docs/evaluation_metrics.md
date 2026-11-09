# Evaluation metrics

This page describes metrics emitted by the network-capture and request-detail corpora, plus the
challenge benchmark that checks the network-capture grader. The corpus graders are deterministic
and inspect saved artifacts; they do not use an LLM judge. A `completed` run status alone does not
mean the objective passed.

## Network-capture corpus

The dataset has three controlled pages. Each oracle lists expected HTTP method, path and status.

### LangSmith feedback

| Metric | Meaning | Example |
| --- | --- | --- |
| `capture_pass` | 0 or 1: strict deterministic grade. Null if the LangSmith reference differs from the local oracle. | All applicable checks pass: `1`; one required request is missing: `0`. |
| `oracle_consistent` | 0 or 1: remote reference rows match the reviewed local oracle. This checks comparability, not agent quality. | Same method/path/status rows: `1`; one reference row changed: `0`. |
| `capture_recall` | Matched unique expected method/path/status tuples divided by expected tuples. Null when unscorable or the oracle differs. | Three of four expected tuples found: `3 / 4 = 0.75`. |
| `failure_summary_recall` | Seeded HTTP failures represented in the agent summary divided by expected seeded failures. Null if none exist or evidence is unavailable. | One of two expected failures included: `1 / 2 = 0.5`. |
| `no_unexpected_requests` | 0 or 1: no captured tuple outside the oracle, except the allowed favicon. | An extra `/tracking` request makes this `0`. |
| `evidence_retained` | 0 or 1: successful network observation links to retained evidence. | The observation points to its saved artifact: `1`. |

Capture scores are null when `oracle_consistent` is zero. Zero means measured and failed; null
means not comparable or not measured.

### Per-run grader checks

All checks contribute to the strict pass. Some capture checks are only added after their
prerequisites succeed, as noted below.

| Check | What it verifies | Example failure |
| --- | --- | --- |
| `correct_case` | Task and result use the selected fixture route. | NET-001 task loads NET-002. |
| `correct_objective` | Task/result objective matches the reviewed objective. | Objective omits network capture. |
| `fixed_budgets` | Task limits match the dataset defaults. | Run increases max browser steps. |
| `readonly_permissions` | Mutation and form permissions are disabled. | Form submission is enabled. |
| `stayed_on_target` | Observations and navigation stay on the case page. | Agent navigates to another origin. |
| `finished` | Result status is `completed`. | Result is `partial`. |
| `retention_enabled` | Raw evidence retention is configured. | Retention is disabled. |
| `ordered_timestamps` | Timezone-aware start, finish and observations are ordered within the run. | Observation predates run start. |
| `consistent_browser_counts` | Browser totals, successes, failures and action names match the trajectory. | Metrics say 4 calls; trajectory has 3. |
| `consistent_model_count` | LLM call count matches recorded model decisions/errors. | Metrics report 2 calls; 3 model events exist. |
| `consistent_evidence_ids` | Observation/evidence IDs are unique and linked. | Two observations share one evidence ID. |
| `consistent_evidence_owners` | Each evidence record names its owning observation. | Evidence points to the wrong observation. |
| `correct_evidence_kind` | Evidence is marked as a browser observation. | Evidence is typed as a model response. |
| `unique_evidence_paths` | Retained artifacts use distinct paths inside the run directory. | Two evidence rows point to one file. |
| `known_claim_evidence` | Claims cite IDs present in the evidence ledger. | A claim cites `missing-id`. |
| `consistent_result_counts` | Finding/behavior counts match their result arrays. | Count is 2, but one finding is stored. |
| `within_budgets` | Browser calls, model calls and duration stay within limits. | Browser calls exceed `max_steps`. |
| `one_successful_network_capture` | Exactly one network listing action occurred and succeeded. | Two listing calls or one failed call. |
| `static_resources_requested` | Listing asks for static resources with the supported argument. | Static inclusion is false or absent. |
| `linked_retained_evidence` | Listing observation links to one retained artifact. | The artifact is missing or unretained. |
| `tool_response_succeeded` | Saved MCP response is not marked as an error. | Response contains `isError: true`. |
| `no_static_omissions` | Tool response says no static requests were omitted. | Response reports omitted static rows. |
| `readable_listing` | At least one request parses and all nonempty rows use a recognized format. | A malformed row cannot be parsed. |
| `same_origin_capture` | Parsed rows use the task page's scheme and host. | A row belongs to another host. |
| `all_expected_requests_retained` | Every expected method/path/status tuple appears in evidence. | Expected POST `/save` 201 is absent. |
| `no_unexpected_requests` | Captured tuples are expected, aside from the favicon exception. | Listing includes `/tracking`. |
| `compact_summary` | Summary is nonempty and at most min(1,000, task character limit). | Summary exceeds 1,000 characters. |
| `accurate_summary_counts` | Parsed, HTTP-error, failed and unknown counts match the listing. | Listing has 8 rows; summary says 7. |
| `expected_failures_in_summary` | Each seeded HTTP failure appears with method, origin, path and status. | A seeded 500 is omitted. |
| `summary_adds_no_requests` | Every request in the summary is present in evidence. | Summary invents a 404 row. |

Checks after `one_successful_network_capture` are only added when that prerequisite passes.
Checks requiring the raw listing are only added after `linked_retained_evidence` passes. Early
returns have no recall values; they do not mean recall was zero.

### Numeric grader measurements

| Measurement | Meaning | Example |
| --- | --- | --- |
| `expected_requests` | Number of unique oracle tuples. | Four expected requests: `4`. |
| `matched_requests` | Expected tuples found in captured evidence. | Three of four: `3`. |
| `capture_recall` | `matched_requests / expected_requests`. | `3 / 4 = 0.75`. |
| `missing_requests` | Oracle tuples not found in evidence. | `[('POST', '/save', '201')]`. |
| `unexpected_requests` | Captured tuples absent from oracle, excluding favicon. | `[('GET', '/tracking', '200')]`. |
| `expected_failures` | Oracle requests with status 400 or above. | One 500 and one 404: `2`. |
| `failure_summary_recall` | Seeded failures included in summary divided by expected failures; null if none. | One of two: `0.5`. |
| `raw_text_chars` | Character count of decoded retained listing. | `6120`. |
| `summary_chars` | Character count of model-facing summary. | `843`. |
| `summary_limit` | Effective summary limit. | Task limit 6,000 gives `1000`. |

## Request-detail corpus

The v2 dataset has 22 cases covering target selection, overviews, request/response headers and
bodies, evidence-backed reporting, redaction and binary boundaries.

### LangSmith feedback

| Metric | Meaning | Example |
| --- | --- | --- |
| `detail_pass` | 0 or 1: all applicable checks pass. Null if the reference differs or grade is missing. | Correct target, part and evidence with safety checks passing: `1`. |
| `oracle_consistent` | 0 or 1: remote example exactly matches the local expected example. | Target and marker agree: `1`; edited reference: `0`. |
| `target_listed_with_visible_index` | Expected method, URL and status appear with a usable request index. | GET `/target` is listed as request 4: `1`. |
| `verified_overview` | Correct request overview is inspected; ambiguity cases also need the selector request header. | Agent selects same-URL request with `x-eval-attempt: chosen`: `1`. |
| `correct_part_or_boundary` | Requested value is inspected, or binary content is safely not read. | Body contains `json-001`; PNG case stops after MIME overview. |
| `evidence_backed_report` | Claim includes the expected public marker and cites the observation containing it. | Claim reports `json-001` and cites its body evidence. |
| `no_wrong_request` | Detail calls avoid wrong candidates/decoys. | Agent reads the `/target-preview` decoy: `0`. |
| `secret_redacted` | For canary cases, secret is absent from saved JSON/JSONL artifacts. Null for cases without a secret. | Canary absent from all artifacts: `1`; non-canary case: null. |

The local checks object also contains these configuration/integrity checks, which contribute to
`detail_pass`:

| Check | What it verifies | Example failure |
| --- | --- | --- |
| `case_url` | Case route matches the recorded target URL. | DET-020 uses the DET-019 route. |
| `objective` | Task and result objective match the reviewed objective. | Requested POST body is omitted. |
| `fixed_budgets` | Task limits equal v2 defaults. | Run raises its model-iteration limit. |
| `detail_permission_only` | Detail permission is on; mutation/form permissions remain off. | Task enables mutation. |
| `retention` | Raw evidence retention is on. | Detail evidence is unavailable. |
| `completed` | Agent result status is `completed`. | Agent returns `partial`. |
| `within_budgets` | Browser calls, model calls and duration stay within limits. | Run exceeds browser-call limit. |
| `consistent_actions` | Result action names and call counts match the trajectory. | Result action list differs from observations. |
| `stayed_on_case` | Observations/navigation stay on the case page. | Agent navigates elsewhere. |
| `no_filename` | No detail call or rejected proposal includes a server-side filename. | A call passes `filename`. |
| `consistent_evidence` | Observation and evidence IDs are unique and linked. | Observation refers to a nonexistent evidence row. |
| `detail_tool_used` | At least one request-detail action occurred. | Agent only lists requests. |

For header cases, a value visible in the correct overview satisfies the request; a redundant
header-only call is unnecessary. Binary cases fail if response-body reading is requested or
proposed.

### Execution measurements

LangSmith outputs also include usage fields. These measure effort, not correctness.

| Field | Meaning | Example |
| --- | --- | --- |
| `duration_seconds` | Elapsed run time. | `4.8`. |
| `browser_tool_calls` | Browser actions made. | Navigate, snapshot and listing: `3`. |
| `successful_browser_tool_calls` | Successful browser actions (network-capture output). | Two of three succeed: `2`. |
| `failed_browser_tool_calls` | Failed browser actions (network-capture output). | One of three fails: `1`. |
| `llm_calls` | Model decision/error events counted for the run. | `4`. |
| `input_tokens` | Provider-reported input tokens. | `12400`. |
| `output_tokens` | Provider-reported output tokens. | `520`. |

Token use may be absent or zero if the provider does not report it. Cost needs a separate matching
model and price schedule.

## Network-capture grader challenge corpus

The challenge corpus does not run an agent or open sockets. It changes copies of saved artifacts
to measure whether the network-capture grader passes known-good controls and rejects corruptions.

| Metric | Meaning | Example |
| --- | --- | --- |
| `valid_accepted` / `valid_acceptance_rate` | Positive controls accepted and their fraction. | Seven of seven: `7 / 7 = 1.0`. |
| `corruptions_rejected` / `corruption_rejection_recall` | Negative challenges rejected and their fraction. | Twenty-three of twenty-three: `23 / 23 = 1.0`. |
| `false_acceptance_rate` | Negative challenges incorrectly accepted divided by all negatives. | One of 23 accepted: `1 / 23 ≈ 0.043`. |
| `scenario_count`, `positive_controls`, `negative_challenges` | Total labeled challenge cases and split sizes. | 30 total: 7 positives and 23 negatives. |
| `controls_unchanged` | Saved source-control fingerprints remain unchanged. | All fingerprints match: `true`. |
| `all_expectations_met` | Controls unchanged and every challenge matches its expected verdict/check. | All 30 scenarios match: `true`. |

Each scenario also has `label_matches`, whether actual pass/fail matches its expected label;
`expected_check_detected`, whether the intended failing check appears; and `challenge_correct`,
their conjunction. These challenges measure the current reviewed mutation set and are not an
independent validation set.

## Comparing experiments

Compare quality scores only when `oracle_consistent` is 1 and dataset/grader versions, model
settings and repetition counts match. Keep null distinct from zero. For an aggregate request
recall, use total matched requests divided by total expected requests: an unweighted mean of
case recalls gives a small case the same weight as the 203-request case. Report execution fields
separately from correctness. One repetition is a snapshot, not a reliability estimate.

See the [network-capture guide](../evals/network_capture/README.md), the
[request-detail guide](../evals/network_request_details/README.md), and the
[challenge implementation](../evals/network_capture/challenges.py) for commands and source.
