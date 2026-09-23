"""Two linked reports from synthetic retained Claude streams; no agent calls.

Install: python -m pip install -e ".[toolscore]"
Run: python examples/toolscore_review.py --output demo-output/toolscore
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import agent_eval_flow as a
from agent_eval_flow.adapters.tool_trace import claude_stream_tool_trace
from agent_eval_flow.evaluation.toolscore import ToolscoreEvaluator, toolscore_metrics
from agent_eval_flow.objects.values import observed, unknown, zero_resources
from agent_eval_flow.storage.artifacts import ArtifactCache


class FixtureOutcome:
    ref = a.VersionRef(name="example.tool-fixture-outcome", revision="1")

    def compute(self, spec, context, run):
        return a.MetricOutput(task=a.Measurement(run_id=run.id, metric=spec.id,
            value=run.output["fixture_success"], status="ok", basis="observed",
            reason="Synthetic fixture outcome; no live agent reliability claim",
            evidence=tuple(a.EvidenceRef(artifact=ref, description="Synthetic retained fixture")
                           for ref in run.artifacts.values())), evaluation_resources=zero_resources())


def build_example(output):
    output = Path(output).resolve()
    cache = ArtifactCache(output / "capture")
    expected = [{"tool": "get_weather", "args": {"city": "Tel Aviv"}},
                {"tool": "format_forecast", "args": {"units": "celsius"}}]
    references = a.DataTable(key=("task_id",), schema={"task_id": "str", "contract": "json"}, rows=(
        {"task_id": "weather", "contract": {"id": "weather-calls", "revision": "1", "calls": expected}},
        {"task_id": "greeting", "contract": {"id": "no-tools", "revision": "1", "calls": []}},
    ))
    dataset = a.EvalDataset.from_records(id="tools-demo", units=[
        {"task_id": "weather", "prompt": "Show the forecast for Tel Aviv in celsius"},
        {"task_id": "greeting", "prompt": "Say hello without using tools"}], unit_key=("task_id",),
        input_columns={"units": ("prompt",)}, references={"tool_expectations": references})
    candidates = {name: a.Candidate(id=name, backend=a.VersionRef(name="retained-fixture", revision="1"),
        components={}) for name in ("baseline", "improved")}
    tools = ToolscoreEvaluator(artifacts=cache)
    outcome = FixtureOutcome()
    metrics = toolscore_metrics(boundary="claude.main.requests")
    suite = a.EvalSuite(id="toolscore-demo", version="1", metrics=(
        a.MetricSpec(id="outcome", source=a.EvaluatorSource(ref=outcome.ref), output_type="bool", role="quality"),
        *metrics), summaries=(a.SummarySpec(id="outcome-rate", metric="outcome", reducer="rate"),),
        acceptance=a.AcceptanceRule(all_of=(a.Threshold(metric="outcome", op="==", value=True),)))
    study = a.Study(id="Tool calling · retained synthetic examples", project_id="tools-demo",
        question="Inspect tool use alongside an independent fixture outcome", dataset=dataset,
        candidates=candidates, suite=suite, execution=a.ExecutionPolicy(budget=a.Budget(wall_time_s=30)))
    plan, runs = study.plan(), []
    now = datetime(2026, 9, 23, tzinfo=timezone.utc)
    for assignment in plan.assignments:
        run_id = assignment.candidate_id + "/" + assignment.unit["task_id"]
        execution_id = run_id + "/main"
        calls = []
        if assignment.unit["task_id"] == "weather":
            city = "Boston" if assignment.candidate_id == "baseline" else "Tel Aviv"
            calls = [{"type": "tool_use", "id": "weather-1", "name": "get_weather", "input": {"city": city}},
                     {"type": "tool_use", "id": "format-1", "name": "format_forecast", "input": {"units": "celsius"}}]
        results = [{"type": "tool_result", "tool_use_id": call["id"], "content": "Fixture result", "is_error": False}
                   for call in calls]
        if assignment.candidate_id == "baseline" and calls:
            results = results[:1]  # The second request was issued but has no completion.
        records = [{"type": "assistant", "message": {"content": calls}},
                   {"type": "user", "message": {"content": results}}]
        raw = ("\n".join(json.dumps(row) for row in records) + "\n").encode()
        artifact = cache.write_bytes(run_id, raw, "application/x-ndjson")
        evidence = a.EvidenceRef(artifact=artifact, description="Complete synthetic stream fixture")
        trace = claude_stream_tool_trace(raw, artifact=artifact, execution_id=execution_id,
            boundary="claude.main.requests", coverage=observed(True,
                "Fixture author declares all requests in this synthetic stream", (evidence,)))
        execution = a.Execution(id=execution_id, slot="fixture", retry_index=0, parent_id=None,
            status="completed", started_at=now, ended_at=now, resources=zero_resources(),
            effective_config=unknown("Synthetic fixture has no live model configuration"))
        runs.append(a.Run(id=run_id, assignment_id=assignment.id, status="completed", cost_scope=("model",),
            output={"fixture_success": not (assignment.candidate_id == "baseline" and calls)},
            output_state="available", artifacts={"stream": artifact}, executions=(execution,),
            started_at=now, ended_at=now, environment=unknown("No agent execution occurred"),
            execution_inventory_complete=observed(True),
            events=(trace.to_event(id=run_id + "/tool-trace", execution_id=execution_id),)))
    capture = a.RunSet(id="synthetic-tool-capture", plan=plan, runs=tuple(runs), grading_inventory_complete=observed(True))
    result = a.EvaluationPipeline(study=study, evaluators={tools.ref.name: tools, outcome.ref.name: outcome}).eval(runs=capture)
    return study, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("demo-output/toolscore"))
    args = parser.parse_args()
    study, result = build_example(args.output)
    study.save(args.output / "study")
    result.save(args.output / "result")
    loaded = a.EvaluationResult.load(args.output / "result")
    report = loaded.report(args.output / "report.html", tools=True)
    print(json.dumps({"general_report": str(report.resolve()),
        "tools_report": str(report.with_name("tools.html").resolve()), "agent_invocations": 0}, indent=2))


if __name__ == "__main__":
    main()
