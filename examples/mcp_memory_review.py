"""Compare three builds of the official MCP memory server in Agent Eval Flow, fully offline.

python examples/mcp_memory_review.py --output demo-output/mcp-memory
Imports the recorded scope matrix (examples/data/mcp-memory/capture.json): the
published package, main, and the fix branch, each seeded with one kind of
unreadable line and given one unrelated write over MCP. Toolscore scores each
recorded tool trace. Makes no MCP, model or network calls.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import agent_eval_flow as a
from agent_eval_flow.evaluation.toolscore import ToolscoreEvaluator, toolscore_metrics
from agent_eval_flow.objects.tool_trace import ToolRequest, ToolTrace
from agent_eval_flow.objects.values import observed, unknown, zero_resources
from agent_eval_flow.storage.artifacts import ArtifactCache

SOURCE = Path(__file__).resolve().parent / "data/mcp-memory/capture.json"
BOUNDARY = "mcp.memory.calls"
RECORDED = datetime(2026, 9, 28, tzinfo=timezone.utc)


def unit_id(cell):
    return f"{cell['scenario']}/{cell['write']}"


def tool_trace(cell, index, evidence, execution_id):
    calls = tuple(ToolRequest(call_id=f"{execution_id}/call/{i}", execution_id=execution_id, tool=call["tool"],
        arguments=call["args"], evidence=(evidence(f"cells/{index}/calls/{i}"),), completion="completed",
        result={"is_error": call["is_error"]}, result_evidence=(evidence(f"cells/{index}/calls/{i}"),))
        for i, call in enumerate(cell["calls"]))
    return ToolTrace(collector=a.VersionRef(name="toolscore.mcp-stdio-client", revision="1"),
        boundary=BOUNDARY, scope="Every MCP tool call the scenario sent to the memory server",
        coverage=observed(True, "The scenario script sends exactly these calls",
                          (evidence(f"cells/{index}/calls"),)), calls=calls)


class MatrixImporter:
    ref = a.VersionRef(name="example.mcp-memory-matrix", revision="1")

    def __init__(self, cache, evidence_uri=None):
        self.cache, self.evidence_uri = cache, evidence_uri

    def read(self, source, *, plan):
        raw = Path(source).read_bytes()
        capture = json.loads(raw)
        artifact = self.cache.write_bytes("mcp-memory-matrix", raw, "application/json")
        if self.evidence_uri is not None:
            artifact = replace(artifact, uri=self.evidence_uri)
        cells = {(c["build"], unit_id(c)): (i, c) for i, c in enumerate(capture["cells"])}
        runs = []
        for assignment in plan.assignments:
            index, cell = cells[(assignment.candidate_id, assignment.unit["unit_id"])]
            run_id = f"recorded/{assignment.candidate_id}/{unit_id(cell)}"
            execution_id = run_id + "/mcp"

            def evidence(pointer):
                return a.EvidenceRef(artifact=artifact, locator="json:/" + pointer,
                                     description="Recorded MCP scenario cell")

            start = RECORDED
            end = start + timedelta(seconds=cell["seconds"])
            execution = a.Execution(id=execution_id, slot="mcp", retry_index=0, parent_id=None,
                status="completed", started_at=start, ended_at=end,
                effective_config=observed({"build": capture["builds"][cell["build"]]}),
                resources=zero_resources())
            trace = tool_trace(cell, index, evidence, execution_id)
            runs.append(a.Run(id=run_id, assignment_id=assignment.id, status="completed", cost_scope=("model",),  # $0: no model is involved
                output={"file_after": cell["file_after"]}, output_state="available",
                output_sources=(execution_id,), artifacts={"matrix.capture": artifact},
                executions=(execution,), started_at=start, ended_at=end,
                environment=observed({"os": "Linux (WSL2)", **capture["upstream"]}),
                execution_inventory_complete=observed(True),
                events=(trace.to_event(id=run_id + "/tool-trace", execution_id=execution_id),),
                native_refs={"cell_index": str(index), "build": cell["build"]}))
        return a.ImportedCapture(runs=tuple(runs),
            grading_inventory_complete=observed(True, "Every metric is computed from the recorded file"),
            projections=(a.ProjectionReport(mapper=self.ref,
                source_format=a.VersionRef(name="mcp-memory-scope-matrix", revision="1"), sources=(artifact,),
                omitted_fields=("Server stderr",)),))


class FileOutcome:
    """Deterministic checks on the memory file after the write."""
    ref = a.VersionRef(name="example.mcp-memory-file-outcome", revision="1")

    def compute(self, spec, context, run):
        index = run.native_refs["cell_index"]
        # One reference row per build for this unit; grade the row of this run's build.
        (cell,) = [r["cell"] for r in context.references["cells"] if r["build"] == run.native_refs["build"]]
        value, status, reason = None, "ok", ""
        if spec.id == "facts_lost":
            value, reason = len(cell["facts_lost"]), "Facts carried by the unreadable line missing from the file"
        elif spec.id == "silent_data_loss":
            value = int(bool(cell["facts_lost"]) and not cell["write_reported_error"])
            reason = "Data disappeared from the file while the write reported success"
        elif spec.id == "write_applied":
            value, reason = int(cell["write_applied"]), "The unrelated write is present in the file"
        elif spec.id == "read_graph_ok":
            value, reason = int(cell["read_graph"] == "ok"), "read_graph returned without an error"
        elif spec.id == "unreadable_line_kept":
            if cell["unreadable_line_kept"] is None:
                status, reason = "not_applicable", "No unreadable line in this scenario"
            else:
                value, reason = int(cell["unreadable_line_kept"]), "The seeded line is still in the file verbatim"
        return a.MetricOutput(task=a.Measurement(run_id=run.id, metric=spec.id, value=value, status=status,
            basis="observed" if status == "ok" else None, reason=reason,
            evidence=(a.EvidenceRef(artifact=run.artifacts["matrix.capture"], locator=f"json:/cells/{index}"),)),
            evaluation_resources=zero_resources())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("demo-output/mcp-memory"))
    parser.add_argument("--evidence-uri", help="Public HTTPS location of the unchanged capture")
    args = parser.parse_args()
    capture = json.loads(SOURCE.read_text(encoding="utf-8"))
    units = sorted({unit_id(c) for c in capture["cells"]})
    first = {unit_id(c): c for c in capture["cells"] if c["build"] == "published-2026.8.31"}
    by_unit_build = {(unit_id(c), c["build"]): c for c in capture["cells"]}
    dataset = a.EvalDataset.from_records(id="mcp-memory-unreadable-lines",
        units=[{"unit_id": u, "scenario": first[u]["scenario"], "write": first[u]["write"]} for u in units],
        unit_key=("unit_id",), input_columns={"units": ("unit_id", "scenario", "write")},
        references={
            "cells": a.DataTable(rows=tuple({"unit_id": u, "build": b, "cell": by_unit_build[(u, b)]}
                                            for u in units for b in capture["builds"]),
                                 key=("unit_id", "build"), schema={"unit_id": "str", "build": "str", "cell": "json"}),
            "tool_expectations": a.DataTable(rows=tuple({"unit_id": u, "contract": {
                "schema_version": "1", "id": "mcp-memory-scenario", "revision": "1",
                "calls": [{"tool": "read_graph"}, {"tool": first[u]["write"]}]}} for u in units),
                key=("unit_id",), schema={"unit_id": "str", "contract": "json"})})
    candidates = {b: a.Candidate(id=b, backend=a.VersionRef(name="recorded-mcp-matrix", revision="1"),
                                 components={"server": a.ComponentSpec(kind="tool", ref=a.VersionRef(name=b))},
                                 settings={"launch": cmd}, description=f"MCP memory server: {b}")
                  for b, cmd in capture["builds"].items()}
    outcome = FileOutcome()
    tools = ToolscoreEvaluator(artifacts=ArtifactCache(args.output / "evidence"))
    specs = tuple(a.MetricSpec(id=name, source=a.EvaluatorSource(ref=outcome.ref), output_type="int", role=role)
                  for name, role in [("silent_data_loss", "quality"), ("facts_lost", "quality"),
                                     ("write_applied", "diagnostic"), ("read_graph_ok", "diagnostic"),
                                     ("unreadable_line_kept", "diagnostic")])
    specs += toolscore_metrics(boundary=BOUNDARY, ordering="ordered")
    study = a.Study(id="MCP memory server · unreadable lines vs one unrelated write", project_id="mcp-memory",
        question="Does one unrelated write delete memory lines the server cannot read? "
                 "Published 2026.8.31 vs main vs fix; recorded runs only.",
        dataset=dataset, candidates=candidates,
        execution=a.ExecutionPolicy(budget=a.Budget(wall_time_s=600)),
        suite=a.EvalSuite(id="mcp-memory-recorded", version="1", metrics=specs,
            summaries=(a.SummarySpec(id="silent_data_loss_total", metric="silent_data_loss", reducer="sum"),
                       a.SummarySpec(id="facts_lost_total", metric="facts_lost", reducer="sum"),
                       a.SummarySpec(id="write_applied_rate", metric="write_applied", reducer="mean"),
                       a.SummarySpec(id="read_graph_ok_rate", metric="read_graph_ok", reducer="mean"))))
    runs = a.RunSet.import_from(SOURCE, plan=study.plan(),
        importer=MatrixImporter(ArtifactCache(args.output / "evidence"), args.evidence_uri))
    result = a.EvaluationPipeline(study=study, evaluators={outcome.ref.name: outcome,
                                                           tools.ref.name: tools}).eval(runs=runs)
    result.save(args.output / "result")
    loaded = a.EvaluationResult.load(args.output / "result")
    report = loaded.report(args.output / "report.html", tools=True)
    print(json.dumps({"runs": len(capture["cells"]), "report": str(report),
                      "tools_report": str(report.with_name("tools.html"))}, indent=2))


if __name__ == "__main__":
    main()
