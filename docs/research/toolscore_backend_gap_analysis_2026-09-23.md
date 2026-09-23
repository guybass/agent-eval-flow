# Toolscore backend integration: feasibility and gaps

Source review: 23 September 2026. Agent Eval Flow revision `b344ae0aa9e5c714c669da1d791fc0827be3c503`; Toolscore main independently resolved with `git ls-remote` to `731743ef652d61c5551001e1a4e068cf1da7596d`. Toolscore's source declares package `tool-scorer` version 1.8.1 and Python >=3.10; our package requires Python >=3.11. No integration was implemented, package installed, or runtime compatibility test performed during this review.

**Recommendation:** integrate Toolscore as an optional post-run evaluator, using the same captured runs as the general evaluation. Produce a separate tools report linked in both directions to the general report. The architecture supports this; reliable capture, explicit expectations, and reporting need implementation.

The initial scope is **how an agent uses tools**: names, arguments, sequence, and repeated calls. Toolscore also has an MCP server testing mode, which is a separate integration requiring server configuration, tool schemas, fixtures, and permission to invoke those tools. Agent tool-call scores do not establish tool implementation correctness or task success.

## Existing foundations

- `objects/records.py`: `MetricEvaluator.compute(spec, context, run)`, `MetricSpec`, `Measurement`, evidence references, and evaluation activities already provide the scoring boundary. `Measurement` supports missing, error, and not-applicable states.
- `EvalDataset.references` and `EvaluationContext.references` can carry expected calls keyed by task, outside agent-visible inputs.
- `Run.events`, `Run.executions`, and retained artifacts provide evidence and execution identities. `RuntimeObservation` supports explicitly named boundaries and coverage; it is not a universal tool-call schema.
- Existing suites support diagnostic metrics, summaries, comparisons, and re-evaluation of saved captures. `AssessmentPipeline` also accepts behavioral evaluators and retains a `behavior_result`.
- Both existing report renderers use saved data. Artifact storage supports content hashes. No Toolscore implementation or dependency currently exists in `src`, `tests`, or `pyproject.toml`.

## Gaps to close

| Priority | Gap | Required work |
| --- | --- | --- |
| Required | Complete, consistently interpreted tool evidence | Define a versioned projection with tool name, validated arguments, call identity, execution/parent identity, retry information, lifecycle stage, ordering, and source references. Declare supported adapters and coverage. |
| Required | Expected behavior | Add a versioned per-task contract for expected calls and argument matching. Distinguish no expectation from an explicit expectation of no tool use. Define allowed alternatives before evaluating results. |
| Required | Evaluator integration | Add an optional, lazy-loaded dependency and evaluator; map numeric outputs and errors into saved measurements. Keep Toolscore diagnostics separate from task acceptance unless a suite explicitly selects them. |
| Required | Scoring semantics | Specify strictness, weights, sequencing, and treatment of retries/parallel calls. Validate these against small known traces before choosing thresholds. |
| Required | Durable tools report | Persist score, component metrics, expected/actual calls, source references, coverage, settings, versions, and hashes. Add study summaries and per-run/per-tool details, plus navigation from both general report types. |
| Required | Portable exports | Bundle companion artifacts and use validated relative links. Current manifest saving does not copy referenced evidence; `_safe_link` supports absolute/file/web locations rather than report-relative paths. |
| Required | Compatibility evidence | Add focused offline tests, a saved-run end-to-end example, save/load/render checks, and a dependency matrix covering supported Python/OS versions and installation with/without the extra. |
| Subsequent | MCP server health | Integrate independently if desired; do not run server-generated test calls as an implicit consequence of evaluating an agent trace. |

### Concrete capture issue in this repository

The normalized `tool_call` records are completion-oriented:

- OpenSRE creates them on `tool_execution_end` (`adapters/opensre.py`).
- Claude Code creates them on `tool_result` (`adapters/claude_code.py`).
- Codex creates them for completed command executions; other native item types remain opaque (`adapters/codex.py`).
- OpenKritt projects completed Codex commands or matched Claude tool results (`adapters/openkritt.py`).

Simply filtering `run.events` for `kind == "tool_call"` would omit issued calls without a captured completion and can order concurrent calls by completion rather than request. Preserve requested and completed views separately and correlate them by identity. Retained native evidence may allow recovery for supported dialects; it does not establish complete capture across all runtimes or nested agents.

Unknown or partial capture must not become an empty actual-call list. A confirmed no-tool run is a different observation. Preserve malformed arguments and report an extraction error instead of replacing them with `{}`.

### Toolscore behavior to account for

- The [in-memory core](https://github.com/yotambraun/Toolscore/blob/731743ef652d61c5551001e1a4e068cf1da7596d/toolscore/core.py) provides the simplest integration. Its default composite uses selection (40%), argument F1 (30%), sequence (20%), and non-redundancy (10%). Its conversion into `ToolCall` retains names/arguments, not supplied execution results, timings, IDs, or costs. Keep those in our canonical evidence. The file-based API adds other features, including optional external side-effect validators; it has different behavior and defaults.
- [Selection accuracy](https://github.com/yotambraun/Toolscore/blob/731743ef652d61c5551001e1a4e068cf1da7596d/toolscore/metrics/accuracy.py) measures membership of observed names in the expected name set. It can be perfect while a required tool is missing. Do not use it alone as required-call coverage.
- [Argument scoring](https://github.com/yotambraun/Toolscore/blob/731743ef652d61c5551001e1a4e068cf1da7596d/toolscore/metrics/arguments.py) matches names with a positional restriction and does not maintain a consumed-call set. Repeated names and reordered calls need explicit compatibility cases. Omitted expected `args` means no argument check; `{}` means zero arguments. The default is lenient; strict matching is available. Empty expected/actual lists return argument F1 zero, so legitimate no-tool cases need deliberate metric applicability rules.
- The [HTML report](https://github.com/yotambraun/Toolscore/blob/731743ef652d61c5551001e1a4e068cf1da7596d/toolscore/reports/html_report.py) presents aggregate metrics and counts, not a study-wide per-tool investigation view. Missing latency/cost are internally defaulted to zero and hidden by positive-value conditions. This collapses unknown and measured zero. Use our own renderer over retained Toolscore results and show explicit coverage/unknown states.
- The [JSON report](https://github.com/yotambraun/Toolscore/blob/731743ef652d61c5551001e1a4e068cf1da7596d/toolscore/reports/json_report.py) retains calls and metrics but omits composite score/grade and weights. `EvaluationResult.to_dict()` retains score/grade but omits full calls and weights. Neither alone is a sufficient integration manifest.

## Proposed implementation boundary

1. Map one supported retained-run format into a versioned tool trace with source references and declared coverage.
2. Read a reviewed expected-call contract from private dataset references. Validate it before scoring.
3. Evaluate deterministically with explicit settings. Persist a complete receipt, then project the selected component metrics into our existing evaluator contracts. `MetricOutput` represents one metric and does not contain a general report payload; reference an artifact through measurement evidence, or use the batch activity's artifact support where appropriate. Do not mutate the original captured run to attach grading outputs.
4. Render saved receipts into `tools.html`, with per-run details and links to `report.html`. Retain `tools.json` with identities/settings/provenance. A dedicated tools renderer can reuse our templates and escaping conventions. Render/load must not invoke Toolscore or an agent again.
5. Extend to additional runtime mappers after the initial contract tests pass. Keep dependency/import failures visible as configuration or evaluator errors; never convert them to success.

Suggested module areas: optional dependency in `pyproject.toml`; projection under `objects`/adapters; evaluator under `evaluation`; tools renderer/template under `reporting`; links in both general templates; focused unit and offline end-to-end fixtures.

## Initial acceptance cases

Use known traces covering exact match, wrong tool, missing/extra call, repeated name, legitimate retry, reordered/parallel calls, malformed arguments, requested-but-uncompleted call, explicit no-tool success, absent/partial capture, and unavailable timing/cost. Confirm source identities survive save/load and report bundling, and that both reports refer to the same run. Freeze expected contracts before comparison. Check package behavior without the optional dependency as well as with it.

The first deliverable should be one retained-run example producing both reports with transparent coverage. Supporting every adapter and MCP server health is additional scope. The largest uncertainty is trace completeness and acceptable-behavior contracts, rather than calling Toolscore's API.
