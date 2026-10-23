# Google exploration run analysis

Historical analysis of original Agentic QA runs. The underlying saved runs are not included in
this standalone repository. This report does not claim fresh live model validation.

Reviewed nine saved runs from October 22, 2026, 21:10–21:14 UTC: each `result.json`,
`trajectory.jsonl`, retained observations, and the successful run's screenshot.

## Observed results

| Run (UTC start and ID suffix) | Status | Proposed target | Model calls | Total tokens |
| --- | --- | --- | --- | --- |
| 21:10:23 `8180d019d9ab` | model_limit | CSS-like selector | 5 | 19,167 |
| 21:10:47 `9a5f40ccac8a` | model_limit | CSS-like selector | 5 | 19,330 |
| 21:11:08 `3b393cd798a1` | model_limit | `ref=e43` | 5 | 19,014 |
| 21:12:28 `d992fbfcc3b2` | model_limit | `ref=e43` | 5 | 19,023 |
| 21:12:50 `40132968d898` | completed | `e43` | 3 | 13,474 |
| 21:13:12 `093b77ba72d7` | model_limit | `#e43` | 5 | 19,237 |
| 21:14:09 `99995208b264` | model_limit | `ref=e43` | 5 | 19,351 |
| 21:14:25 `896dcbd2964e` | model_limit | CSS-like selector | 5 | 19,200 |
| 21:14:43 `8807dd7b01e3` | model_limit | `ref=e43` | 5 | 19,014 |

One of nine runs reported completion (11.1%). The batch consumed 166,810 tokens and 43 model
decisions. Forty decisions resulted in rejected typing attempts; their runs used 153,336 tokens.
All eight failed runs executed only navigation and the initial snapshot, with zero browser tool
failures. These failures occurred at local action validation, before search execution.

The artifacts do not contain model names or provider response model identifiers. They cannot
establish which model produced each run, per-model success rates, or a model ranking. Reading the
current `.env` would not recover historical settings. Individual rejected proposals are also
deduplicated in `unresolved_questions`, so their exact iteration sequence cannot be reconstructed.

## Failure mechanism

Every initial snapshot exposed `combobox "Գտնել" [active] [ref=e43]`. The executor required
`target="e43"`, but failed proposals used a selector, `ref=e43`, or `#e43`. The model-facing tool
catalog preserved server targeting descriptions, and the prompt did not explain the required
literal format. The generic rejection did not show the corrected argument or current references.
Rejections were placed after recent observations, where the context budget could omit them.

The previous duplicate-inspection guard does not address this failure: no model action succeeded
after setup, so there were no repeated snapshots or screenshots to reject.

## Completion quality

Run `20261022T211250-40132968d898` submitted `Armavir` with `target="e43"`, then captured a
full-page screenshot and finished after three decisions. Its screenshot visibly contains Google
results for Armavir. However, the typing response contains only the results URL/title and a
snapshot-file link. No results snapshot was fetched, and screenshot pixels were omitted from the
model context. The run demonstrates search and capture; it does not establish that the model
inspected result content. Its final verified-behavior list is empty, and one proposed behavior
was rejected for invalid evidence.

Another failed run (`9a5f40ccac8a`) claimed text entry worked using only the initial snapshot as
evidence. Evidence-ID validity establishes provenance; it does not establish semantic support for
a claim. A visible search field alone cannot demonstrate a successful search.

## Implemented enhancements

1. Replace ambiguous targeting descriptions with the bare-reference contract, including nested
   form fields, and show `[ref=e43]` → `"e43"` in the prompt and tool schema.
2. Normalize only exact observed references wrapped as `ref=e43`, `[ref=e43]`, or `#e43`.
   Unknown references and arbitrary selectors remain rejected; sensitive-control checks still run.
3. Include current reference examples in rejection reasons and prioritize the latest executor
   feedback ahead of page content. Bound progress/feedback size so observations retain context space.
4. Automatically request an accessibility snapshot when an interaction returns only a snapshot-file
   link, before another model decision. This uses browser calls without spending a model decision.
5. Save the configured model and task limits in `run_config.json`; save each validated decision and
   each rejection in `decisions.jsonl`. Include redacted rejection reasons in CLI logs.

These changes address observed failure paths. Offline replay verifies execution and context
behavior; it does not measure new live model success rates.

Validation: the browser exploration unit suite passed all 384 tests; the focused reference,
safety, and graph checks passed all 70 tests after final type-only cleanup. Ruff lint and formatting
checks passed. Async tests used a selector polling interval of 20 ms in the test process to work
around missed executor wakeups in this sandbox; application files do not contain that workaround.
Mypy passed for all 15 package source files with `--follow-imports=skip`; the check following
dependency imports exceeded a 45-second execution limit in this environment.

## Further improvements to evaluate

- **Completion criteria:** represent requested steps and their evidence explicitly, so `done=true`
  can report unmet inspection requirements instead of automatically implying objective fulfillment.
  Functional claims should cite evidence from the relevant executed interaction and resulting page.
- **No-progress termination:** stop after several equivalent rejections without a successful action,
  preserving an honest partial status and the exact correction required. Varying an element label
  should not reset this count.
- **Observation selection:** prioritize result headings, snippets, and actionable references over
  generic page boilerplate. Repeatedly fetching an unchanged large snapshot cannot recover content
  that deterministic prefix truncation always removes.
- **Repeatable comparisons:** use fixed model identifiers, identical task/budgets, an isolated fresh
  browser session, and repeated trials. Report task completion quality, rejection count, tokens,
  and duration from saved artifacts. Browser locale and consent state can change the observations.
