"""A real agent using the official MCP memory server: main vs main + skip report, offline.

python examples/mcp_memory_agent_review.py --model gpt-5.6-luna --output demo-output/mcp-memory-agent
Imports recorded agent runs (examples/data/mcp-memory/agent_capture.json). Each run
gave a model the server's own tools and the README's example memory prompt, and
one user message with a new fact about an existing entity (or, as a control, a new
person). This evaluator re-grades every run from the recorded final memory file;
Toolscore scores each tool trace. Makes no model, MCP or network calls.
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

SOURCE = Path(__file__).resolve().parent / "data/mcp-memory/agent_capture.json"
BOUNDARY = "mcp.memory.agent-calls"
RECORDED = datetime(2026, 9, 28, tzinfo=timezone.utc)
CLAIMS = ("noted", "saved", "remember", "stored", "updated", "added", "got it", "i'll keep", "will keep")


def final_graph(text):
    graph = {}
    for line in text.splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("type") == "entity" and isinstance(rec.get("name"), str):
            graph.setdefault(rec["name"].lower(), []).extend(
                o for o in rec.get("observations") or [] if isinstance(o, str))
    return graph


def outcomes(run, seed_names):
    """The pre-registered grading (PROTOCOL Addendum B), re-implemented here."""
    kws = [k.lower() for k in run["keywords"]]
    stored = all(k in " ".join(final_graph(run["file_after"]).get(run["target"].lower(), [])).lower() for k in kws)
    sent_to_existing = any(c["tool"] == "create_entities" and any(
        isinstance(e, dict) and str(e.get("name", "")).lower() in seed_names
        and any(k in " ".join(map(str, e.get("observations") or [])).lower() for k in kws)
        for e in c["args"].get("entities", [])) for c in run["calls"])
    return {
        "fact_stored": int(stored),
        "fact_stored_anywhere": int(all(k in run["file_after"].lower() for k in kws)),
        "silent_loss": int(sent_to_existing and not stored),
        "false_confirmation": int(any(w in run["final_text"].lower() for w in CLAIMS) and not stored),
    }


class AgentRunImporter:
    ref = a.VersionRef(name="example.mcp-memory-agent-runs", revision="1")

    def __init__(self, cache, model, evidence_uri=None):
        self.cache, self.model, self.evidence_uri = cache, model, evidence_uri

    def read(self, source, *, plan):
        raw = Path(source).read_bytes()
        capture = json.loads(raw)
        artifact = self.cache.write_bytes("mcp-memory-agent-runs", raw, "application/json")
        if self.evidence_uri is not None:
            artifact = replace(artifact, uri=self.evidence_uri)
        rows = {(r["server"], r["scenario"], r["rep"]): (i, r) for i, r in enumerate(capture["runs"])
                if r["model"] == self.model}
        runs = []
        for assignment in plan.assignments:
            index, row = rows[(assignment.candidate_id, assignment.unit["scenario"], assignment.repetition)]
            run_id = f"recorded/{self.model}/{assignment.candidate_id}/{row['scenario']}/r{row['rep']}"
            execution_id = run_id + "/agent"

            def evidence(pointer):
                return a.EvidenceRef(artifact=artifact, locator="json:/" + pointer,
                                     description="Recorded agent run over MCP")

            calls = tuple(ToolRequest(call_id=f"{execution_id}/call/{i}", execution_id=execution_id,
                tool=c["tool"], arguments=c["args"], evidence=(evidence(f"runs/{index}/calls/{i}"),),
                completion="error" if c["is_error"] else "completed", result={"is_error": c["is_error"]},
                result_evidence=(evidence(f"runs/{index}/calls/{i}"),)) for i, c in enumerate(row["calls"]))
            trace = ToolTrace(collector=a.VersionRef(name="toolscore.mcp-stdio-client", revision="1"),
                boundary=BOUNDARY, scope="Every tool call the agent made to the memory server in this run",
                coverage=observed(True, "The harness records every function call before executing it",
                                  (evidence(f"runs/{index}/calls"),)), calls=calls)
            start = RECORDED
            end = start + timedelta(seconds=row["seconds"])
            resources = a.Resources(
                cost_usd=unknown(f"List-price estimate only: ${row['cost_usd']:.5f}"), cost_scope=("model",),
                input_tokens=observed(row["input_tokens"]), output_tokens=observed(row["output_tokens"]),
                human_minutes=unknown("Not timed"))
            execution = a.Execution(id=execution_id, slot="agent", retry_index=0, parent_id=None,
                status="completed", started_at=start, ended_at=end,
                effective_config=observed({"model": self.model, "server": capture["servers"][row["server"]]}),
                resources=resources)
            runs.append(a.Run(id=run_id, assignment_id=assignment.id, status="completed", cost_scope=("model",),
                output={"reply": row["final_text"]}, output_state="available", output_sources=(execution_id,),
                artifacts={"agent.capture": artifact}, executions=(execution,), started_at=start, ended_at=end,
                environment=observed({"os": "Linux (WSL2)", **capture["upstream"]}),
                execution_inventory_complete=observed(True),
                events=(trace.to_event(id=run_id + "/tool-trace", execution_id=execution_id),),
                native_refs={"row_index": str(index)}))
        return a.ImportedCapture(runs=tuple(runs),
            grading_inventory_complete=observed(True, "Every metric is recomputed from the recorded run"),
            projections=(a.ProjectionReport(mapper=self.ref,
                source_format=a.VersionRef(name="mcp-memory-agent-runs", revision="1"), sources=(artifact,),
                omitted_fields=("Model reasoning items", "Tool results beyond 2,000 characters")),))


class FactOutcome:
    ref = a.VersionRef(name="example.mcp-memory-fact-outcome", revision="1")

    def __init__(self, capture):
        self.seed_names = {e["name"].lower() for e in capture["seed"]["entities"]}
        self.runs = capture["runs"]

    def compute(self, spec, context, run):
        index = int(run.native_refs["row_index"])
        value = outcomes(self.runs[index], self.seed_names)[spec.id]
        reasons = {"fact_stored": "Every fact keyword is in the target entity's observations at the end",
                   "fact_stored_anywhere": "Every fact keyword appears somewhere in the final memory file",
                   "silent_loss": "The fact was sent via create_entities for an existing entity and is not stored",
                   "false_confirmation": "Exploratory: the reply claims it saved the fact, but it is not stored"}
        return a.MetricOutput(task=a.Measurement(run_id=run.id, metric=spec.id, value=value, status="ok",
            basis="observed", reason=reasons[spec.id],
            evidence=(a.EvidenceRef(artifact=run.artifacts["agent.capture"], locator=f"json:/runs/{index}"),)),
            evaluation_resources=zero_resources())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--output", type=Path, default=Path("demo-output/mcp-memory-agent"))
    parser.add_argument("--evidence-uri")
    args = parser.parse_args()
    capture = json.loads(SOURCE.read_text(encoding="utf-8"))
    mine = [r for r in capture["runs"] if r["model"] == args.model]
    reps = max(r["rep"] for r in mine) + 1
    scenarios = capture["scenarios"]
    dataset = a.EvalDataset.from_records(id="mcp-memory-agent-scenarios",
        units=[{"scenario": s["id"], "kind": s["kind"], "message": s["message"]} for s in scenarios],
        unit_key=("scenario",), input_columns={"units": ("scenario", "kind", "message")},
        references={"tool_expectations": a.DataTable(rows=tuple({"scenario": s["id"], "contract": {
            "schema_version": "1", "id": "memory-" + s["kind"], "revision": "1",
            "calls": [{"tool": "add_observations" if s["kind"] == "update" else "create_entities"}]}}
            for s in scenarios), key=("scenario",), schema={"scenario": "str", "contract": "json"})})
    candidates = {name: a.Candidate(id=name, backend=a.VersionRef(name="recorded-agent-runs", revision="1"),
        components={"model": a.ComponentSpec(kind="model", ref=a.VersionRef(name=args.model)),
                    "server": a.ComponentSpec(kind="tool", ref=a.VersionRef(name=name))},
        settings={"server": build}, description=f"{args.model} with the memory server: {name}")
        for name, build in capture["servers"].items()}
    outcome = FactOutcome(capture)
    tools = ToolscoreEvaluator(artifacts=ArtifactCache(args.output / "evidence"))
    specs = tuple(a.MetricSpec(id=name, source=a.EvaluatorSource(ref=outcome.ref), output_type="int", role=role)
                  for name, role in [("fact_stored", "quality"), ("silent_loss", "quality"),
                                     ("fact_stored_anywhere", "diagnostic"), ("false_confirmation", "diagnostic")])
    specs += toolscore_metrics(boundary=BOUNDARY, ordering="unordered")
    study = a.Study(id=f"MCP memory server · agent ({args.model}) · main vs skip report", project_id="mcp-memory",
        question="When an agent records a new fact about an existing entity, is it stored? "
                 "Recorded runs; see examples/data/mcp-memory/README.md.",
        dataset=dataset, candidates=candidates,
        execution=a.ExecutionPolicy(budget=a.Budget(wall_time_s=7200), repetitions=reps),
        contrasts=(a.Contrast(id="skip-report", baseline="main", challenger="c1",
                              metrics=("fact_stored", "silent_loss")),),
        suite=a.EvalSuite(id="mcp-memory-agent", version="1", metrics=specs,
            summaries=tuple(a.SummarySpec(id=f"{m}_rate", metric=m, reducer="mean")
                            for m in ("fact_stored", "silent_loss", "fact_stored_anywhere", "false_confirmation"))))
    runs = a.RunSet.import_from(SOURCE, plan=study.plan(),
        importer=AgentRunImporter(ArtifactCache(args.output / "evidence"), args.model, args.evidence_uri))
    result = a.EvaluationPipeline(study=study, evaluators={outcome.ref.name: outcome,
                                                           tools.ref.name: tools}).eval(runs=runs)
    result.save(args.output / "result")
    loaded = a.EvaluationResult.load(args.output / "result")
    report = loaded.report(args.output / "report.html", tools=True)
    print(json.dumps({"model": args.model, "runs": len(mine), "report": str(report)}, indent=2))


if __name__ == "__main__":
    main()
