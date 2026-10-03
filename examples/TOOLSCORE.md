# Tool calling evaluation with Toolscore

Install `agent-eval-flow[toolscore]` (or `pip install -e ".[toolscore]"` in a
checkout). This extra pins `tool-scorer==1.10.0`. Importing Agent Eval Flow and
rendering saved reports do not require Toolscore. A requested evaluation with a
missing or unsupported dependency produces an explicit error measurement.

Run `python examples/toolscore_review.py --output demo-output/toolscore` for a
complete offline example with two candidates and two synthetic tasks. The
example is an integration demonstration, not a live agent benchmark.

## Configure the evaluation

```python
from agent_eval_flow.evaluation.toolscore import ToolscoreEvaluator, toolscore_metrics
from agent_eval_flow.storage.artifacts import ArtifactCache

evaluator = ToolscoreEvaluator(artifacts=ArtifactCache(output / "evidence"))
metrics = toolscore_metrics(boundary="claude.main.requests")
# Include metrics in your EvalSuite and register the evaluator:
# evaluators={evaluator.ref.name: evaluator}
# Evaluate the same RunSet used for your task-outcome checks.
result.report(output / "report.html", tools=True)
```

These are ordinary diagnostic `MetricSpec` records. They do not change task
acceptance unless the caller adds explicit gates. Candidate summaries and
comparisons can select individual tool metrics using the existing APIs.
Both `EvaluationResult` and behavioral `AssessmentResult` support `tools=True`.

Add one row per task to the private `EvalDataset.references["tool_expectations"]`
table. Include the dataset unit keys and a JSON `contract` column:

```json
{
  "task_id": "weather",
  "contract": {
    "schema_version": "1",
    "id": "weather-calls",
    "revision": "1",
    "calls": [{"tool": "get_weather", "args": {"city": "Tel Aviv"}}]
  }
}
```

The table key must include the task's unit keys, as with other reference tables.
Expected calls are not exposed to the agent through input columns. Missing
contract rows produce missing scores; `"calls": []` explicitly expects no calls.
Omitting `args` or using null disables argument checking for that expected call;
`"args": {}` expects zero arguments. Names are case-sensitive. Contracts use
plain JSON; executable Toolscore matcher objects are not supported in this
version. Predeclare alternate valid traces using `"alternatives": [[...], [...]]`.
All alternatives and their scores are retained. The highest native composite
wins and ties use declaration order. A matching empty alternative scores 1.0.
Freeze the contract before comparing candidates.

## Capture requests and coverage

`agent_eval_flow.objects.tool_trace.ToolTrace` is a versioned, evidence-backed
payload carried by an ordinary saved `Event`. It records a named boundary,
explicit scope, collector version, coverage, and ordered `ToolRequest` records.
Each request retains its native call ID, owning execution, original arguments,
source evidence, completion status and optional result evidence. Parent and
retry identities come from the run's retained executions.

For retained Claude Code stream JSON, use
`agent_eval_flow.adapters.tool_trace.claude_stream_tool_trace`. Pass the original
bytes, a matching hashed `ArtifactRef`, execution ID, boundary, and an
evidence-backed coverage observation. The helper preserves request order when
results arrive out of order, and retains requests with no completion. Its
default coverage is unknown. It rejects malformed stream JSON, duplicate IDs,
and orphan results. Invalid or absent arguments remain in the trace and cause
an evaluator error instead of becoming an empty argument dictionary.

Attach `trace.to_event(id=..., execution_id=...)` during capture/projection,
before evaluation. Exactly one trace may describe a selected boundary in a run.
Multiple boundaries can use separate metric prefixes. Call execution IDs must
resolve to the saved run. Only observed, explicitly complete request coverage
can be scored. Run completion alone does not establish that coverage, and
completeness at a named boundary does not prove all subagent work was captured.

This first version supplies a Claude stream projector and a generic trace
contract for other collectors. Existing completion-only `tool_call` events are
not automatically assumed to be complete request traces. Additional runtime
projectors and live capture validation remain separate work. MCP server testing
is not invoked by this integration.

## Metric meanings

The eight diagnostics are native composite score, invocation accuracy,
selection accuracy, argument F1, sequence accuracy, redundant rate,
required-call recall, and identical-call rate (`identical_rate`). Recall uses tool-name multiplicities so a
missing repeated invocation is not hidden by perfect selection accuracy; it
does not validate arguments or side effects.

`redundant_rate` counts calls beyond the contract's per-tool expectations.
`identical_rate` measures the fraction of observed calls that repeat an earlier
call with the same tool and arguments, ignoring dictionary key order. Distinct
searches can therefore be redundant by count without being identical. Declared
retries still count as identical repeats; this is a diagnostic, not proof of a
loop. It adds no composite weight. The native `identical_count` is also retained
in each alternative's raw efficiency metrics.

Strict argument matching is the default. Set `strict=False` explicitly to use
Toolscore's lenient comparisons. Default weights are selection 0.4, arguments
0.3, sequence 0.2 and non-redundancy 0.1. Custom weights must specify all four
keys (`selection_accuracy`, `argument_f1`, `sequence_accuracy`, `redundant_rate`)
and are normalized. `ordering="unordered"` sorts calls deterministically by
name and JSON arguments and gives sequence zero weight; its sequence metric is
not applicable. This is canonical-list scoring, not dependency-graph evaluation.
Since 1.9.0, Toolscore pairs expected and actual calls of each tool one-to-one to
maximize argument matches, independently of order. Pairing is exact for up to
12 actual calls per tool and greedy above that limit. A missing earlier call
does not hide a correct later call, and an actual call cannot satisfy two
expected calls. Inspect the retained calls and keep independent outcome checks.

Retries remain separate calls. Legitimate retries can be declared in expected
or alternative traces; they are not silently removed. Completion status and
returned results are displayed, but the in-memory Toolscore API scores requests.
The adapter never runs tools, side-effect validators, MCP tests, or an LLM judge.

For an explicit empty expectation and a complete empty trace, invocation
accuracy, composite score and argument F1 are 1.0. Required-call recall is not
applicable, and redundant and identical rates are zero. Correct no-tool runs
now contribute to candidate composite means.
Unknown per-tool timing/cost remain unknown. Offline grading's model cost is
zero, which is distinct from the cost of the captured agent run.

### Upgrading from 1.9.0

The adapter revision is now `2+tool-scorer.1.10.0`. Scores do not change: the
adapter passes all four weights explicitly, and 1.10.0's new
`required_call_recall` weight defaults to 0. The retained raw metrics gain
`error_count`, `error_rate`, `retry_after_error_count`, `required_call_recall`
(Toolscore's version counts only required calls that did not fail) and
`security_metrics` (credentials found in tool arguments, as redacted previews).
Saved 1.9.0 receipts still render without regrading.
See the [Toolscore 1.10.0 release notes](https://github.com/yotambraun/Toolscore/releases/tag/v1.10.0).

### Upgrading from 1.8.1

The adapter revision became `2+tool-scorer.1.9.0`. One-to-one argument pairing
can raise scores for existing traces, and correct no-tool runs now have an
applicable perfect score. Re-evaluate retained runs for both candidates with
the new evaluator before comparing scores or recalibrating gates. Saved 1.8.1
receipts still render their original metrics and statuses without regrading.
See the [Toolscore 1.9.0 release notes](https://github.com/yotambraun/Toolscore/releases/tag/v1.9.0).

## Saved reports and evidence

Each receipt retains the contract and its fingerprint, raw and selected metrics,
effective settings, Toolscore/adapter versions, run identity/fingerprint,
source references, request coverage and lifecycle observations. Measurement
evidence references its hashed JSON artifact. Save and load evaluations using
the existing APIs; reporting reads saved receipts without regrading.

`result.report(..., tools=True)` writes the general report, a companion tools
HTML/JSON report, and verified local evidence under `evidence/`. Links in the
HTML reports are relative to the bundle. For `report.html`, companion names are
`tools.html` and `tools.json`; other report names use `<stem>-tools.*`.
The tools view compares candidates, lists expected/observed requests and per-tool
counts, and shows missing/error/not-applicable coverage. Receipt read failures
are visible. Unavailable, unhashed, changed, or remote evidence is listed as
unavailable; it is never downloaded implicitly.

Move or share the whole report folder to preserve links. Original saved
manifest references remain unchanged: this exports portable reports and a JSON
evidence index, not a relocated executable `RunSet`. As with general reports,
the bundle contains retained arguments/results and should contain only the
evidence you intend to share.

CI retains the existing jobs without Toolscore and adds optional integration
checks on Windows/Linux and Python 3.11–3.13. Running those jobs on CI is
separate from local validation.
