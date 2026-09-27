"""Import the curated DeerFlow study into Agent Eval Flow with Toolscore, fully offline.

python examples/deerflow_review.py --output demo-output/deerflow
Regenerates linked reports from recorded runs: report.html (outcomes, turns,
evidence path) and tools.html (Toolscore view of each run's tool requests).
Makes no model, search or DeerFlow calls.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import agent_eval_flow as a
from agent_eval_flow.evaluation.toolscore import ToolscoreEvaluator, toolscore_metrics
from agent_eval_flow.objects.tool_trace import ToolRequest, ToolTrace
from agent_eval_flow.objects.values import observed, unknown, zero_resources
from agent_eval_flow.storage.artifacts import ArtifactCache

SOURCE = Path(__file__).resolve().parent / "data/deerflow/capture.json"
BOUNDARY = "deerflow.lead.requests"
DEFAULT_TURNS = 6  # recursion_limit 100 at 827acf51: 14 super-steps per turn + 9 per invocation
CONTRACT = {"schema_version": "1", "id": "deerflow-research", "revision": "1",
            "calls": [{"tool": "web_search"}, {"tool": "web_fetch"}]}


def usage_resources(row):
    usage, complete = row.get("usage") or {}, row.get("usage_complete")
    tokens = (lambda k: observed(usage[k]) if complete else unknown("Run ended before the usage event"))
    return a.Resources(cost_usd=unknown("Provider invoice not attributed per run"), cost_scope=("model",),
                       input_tokens=tokens("input_tokens"), output_tokens=tokens("output_tokens"),
                       human_minutes=unknown("Not timed"))


def tool_trace(row, index, evidence, execution_id):
    results = {r["tool_call_id"]: (i, r) for i, r in enumerate(row["tool_results"])}
    calls = []
    for i, call in enumerate(row["tool_calls"]):
        ref = evidence(f"runs/{index}/tool_calls/{i}")
        done = results.get(call["id"])
        calls.append(ToolRequest(call_id=call["id"], execution_id=execution_id, tool=call["tool"],
            arguments=call.get("args") or {}, evidence=(ref,),
            completion="completed" if done else "unknown",
            result={"chars": done[1]["chars"], "gold_present": done[1]["gold_present"]} if done else None,
            result_evidence=(evidence(f"runs/{index}/tool_results/{done[0]}"),) if done else ()))
    return ToolTrace(collector=a.VersionRef(name="deerflow-study.stream-capture", revision="1"),
        boundary=BOUNDARY, scope="Lead-agent tool requests streamed by DeerFlowClient; no sub-agent tasks ran",
        coverage=observed(row["subagent_events"] == 0 and row["status"] == "ok",
                          "Complete when the run finished and no sub-agent task was delegated",
                          (evidence(f"runs/{index}/subagent_events"),)),
        calls=tuple(calls))


class CuratedDeerFlowImporter:
    ref = a.VersionRef(name="example.deerflow-curated", revision="1")

    def __init__(self, cache, evidence_uri=None):
        self.cache, self.evidence_uri = cache, evidence_uri

    def read(self, source, *, plan):
        raw = Path(source).read_bytes()
        capture = json.loads(raw)
        artifact = self.cache.write_bytes("curated-deerflow", raw, "application/json")
        if self.evidence_uri is not None:
            artifact = replace(artifact, uri=self.evidence_uri)
        records = {r["case_id"]: (i, r) for i, r in enumerate(capture["runs"])}
        runs = []
        for assignment in plan.assignments:
            index, row = records[assignment.unit["case_id"]]
            run_id, execution_id = "recorded/" + row["case_id"], "recorded/" + row["case_id"] + "/lead"

            def evidence(pointer):
                return a.EvidenceRef(artifact=artifact, locator="json:/" + pointer,
                                     description="Curated projection of a retained DeerFlow trace")

            start, end = (datetime.fromisoformat(row[k]) for k in ("started_at", "ended_at"))
            execution = a.Execution(id=execution_id, slot="lead", retry_index=0, parent_id=None,
                status="completed" if row["status"] == "ok" else "agent_error", started_at=start, ended_at=end,
                effective_config=observed({**capture["configuration"], "recursion_limit": row["recursion_limit"]}),
                resources=usage_resources(row))
            trace = tool_trace(row, index, evidence, execution_id)
            runs.append(a.Run(id=run_id, assignment_id=assignment.id,
                status="completed" if row["status"] == "ok" else "agent_error", cost_scope=("model",),
                output={"answer": row["answer"]} if row["status"] == "ok" else None,
                output_state="available" if row["status"] == "ok" else "unavailable",
                output_sources=(execution_id,) if row["status"] == "ok" else (),
                artifacts={"curated.capture": artifact}, executions=(execution,), started_at=start, ended_at=end,
                environment=observed({"os": "Linux (WSL2)", **capture["upstream"]}),
                execution_inventory_complete=observed(row["subagent_events"] == 0),
                events=(trace.to_event(id=run_id + "/tool-trace", execution_id=execution_id),),
                native_refs={"case_id": row["case_id"], "row_index": str(index),
                             "raw_trace_sha256": row["raw_trace_sha256"]}))
        return a.ImportedCapture(runs=tuple(runs),
            grading_inventory_complete=observed(True, "All grading is deterministic and recorded in the capture"),
            projections=(a.ProjectionReport(mapper=self.ref,
                source_format=a.VersionRef(name="curated-deerflow-study", revision="1"), sources=(artifact,),
                omitted_fields=("Page bodies", "Prompts and system text", "Local paths")),))


class RecordedOutcome:
    ref = a.VersionRef(name="example.deerflow-recorded-outcome", revision="1")

    def compute(self, spec, context, run):
        index = run.native_refs["row_index"]
        row = context.references["runs"][0]["row"]
        value, status, reason = None, "ok", ""
        if spec.id == "target_correct":
            if row["status"] != "ok":
                status, reason = "not_applicable", "Run ended without an answer"
            else:
                value, reason = int(row["grade"] == "correct"), "Deterministic normalized gold match"
        elif spec.id == "turns":
            value, reason = row["turns"], "Lead-agent model requests captured"
        elif spec.id == "needed_more_than_default":
            value = int(row["turns"] > DEFAULT_TURNS or "GraphRecursionError" in (row["error"] or ""))
            reason = f"More than {DEFAULT_TURNS} lead turns, or cut off by the recursion limit"
        elif spec.id == "identical_repeats":
            value, reason = row["toolscore"]["identical_repeats"], "Same tool and same arguments requested again"
        elif spec.id == "evidence_lost":
            if row["status"] != "ok":
                status, reason = "not_applicable", "No final answer to compare"
            else:
                value, reason = int(row["evidence"]["loss"]), "Gold retrieved but absent from the last model request"
        return a.MetricOutput(task=a.Measurement(run_id=run.id, metric=spec.id, value=value, status=status,
            basis="observed" if status == "ok" else None, reason=reason,
            evidence=(a.EvidenceRef(artifact=run.artifacts["curated.capture"], locator=f"json:/runs/{index}"),)),
            evaluation_resources=zero_resources())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("demo-output/deerflow"))
    parser.add_argument("--evidence-uri", help="Public HTTPS location of the unchanged capture")
    args = parser.parse_args()
    capture = json.loads(SOURCE.read_text(encoding="utf-8"))
    rows = capture["runs"]
    dataset = a.EvalDataset.from_records(id="deerflow-simpleqa-frames",
        units=[{"case_id": r["case_id"], "query": r["query"]} for r in rows], unit_key=("case_id",),
        input_columns={"units": ("case_id", "query")},
        references={
            "runs": a.DataTable(rows=tuple({"case_id": r["case_id"], "row": r} for r in rows),
                                key=("case_id",), schema={"case_id": "str", "row": "json"}),
            "tool_expectations": a.DataTable(rows=tuple({"case_id": r["case_id"], "contract": CONTRACT}
                                                        for r in rows),
                                             key=("case_id",), schema={"case_id": "str", "contract": "json"})})
    candidate = a.Candidate(id="deerflow-827acf51-gpt-5.6-luna",
        backend=a.VersionRef(name="curated-capture-import", revision="1"),
        components={"model": a.ComponentSpec(kind="model", ref=a.VersionRef(name="gpt-5.6-luna"))},
        settings=capture["configuration"], description="Embedded DeerFlowClient, DDG + Jina, sub-agents enabled")
    outcome = RecordedOutcome()
    tools = ToolscoreEvaluator(artifacts=ArtifactCache(args.output / "evidence"))
    specs = tuple(a.MetricSpec(id=name, source=a.EvaluatorSource(ref=outcome.ref), output_type="int", role=role)
                  for name, role in [("target_correct", "quality"), ("turns", "diagnostic"),
                                     ("needed_more_than_default", "diagnostic"),
                                     ("identical_repeats", "diagnostic"), ("evidence_lost", "diagnostic")])
    specs += toolscore_metrics(boundary=BOUNDARY, ordering="unordered")
    study = a.Study(id="DeerFlow · research runs vs the default recursion budget", project_id="deerflow",
        question="Where do DeerFlow research runs fail, and is it the agent or the budget? "
                 "Recorded runs only; see examples/data/deerflow/README.md.",
        dataset=dataset, candidates={candidate.id: candidate},
        execution=a.ExecutionPolicy(budget=a.Budget(wall_time_s=1800)),
        suite=a.EvalSuite(id="deerflow-recorded", version="1", metrics=specs,
            summaries=(a.SummarySpec(id="correct_rate", metric="target_correct", reducer="mean"),
                       a.SummarySpec(id="needed_more_than_default_rate", metric="needed_more_than_default",
                                     reducer="mean"),
                       a.SummarySpec(id="identical_repeats_total", metric="identical_repeats", reducer="sum"),
                       a.SummarySpec(id="evidence_lost_total", metric="evidence_lost", reducer="sum"))))
    runs = a.RunSet.import_from(SOURCE, plan=study.plan(),
        importer=CuratedDeerFlowImporter(ArtifactCache(args.output / "evidence"), args.evidence_uri))
    result = a.EvaluationPipeline(study=study, evaluators={outcome.ref.name: outcome,
                                                           tools.ref.name: tools}).eval(runs=runs)
    result.save(args.output / "result")
    loaded = a.EvaluationResult.load(args.output / "result")
    report = loaded.report(args.output / "report.html", tools=True)
    needed = sum(1 for r in rows if r["experiment"] in ("simpleqa", "frames")
                 and (r["turns"] > DEFAULT_TURNS or "GraphRecursionError" in (r["error"] or "")))
    print(json.dumps({"runs": len(rows), "needed_more_than_default_in_primary_runs": needed,
                      "report": str(report), "tools_report": str(report.with_name("tools.html"))}, indent=2))


if __name__ == "__main__":
    main()
