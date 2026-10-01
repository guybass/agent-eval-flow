"""Import a curated GPT Researcher pilot into Agent Eval Flow, fully offline.

python examples/gpt_researcher_review.py --output demo-output/gpt-researcher
This regenerates a report of recorded runs, not new agent runs or new judgments.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import agent_eval_flow as a
from agent_eval_flow.objects.values import observed, unknown, zero_resources
from agent_eval_flow.storage.artifacts import ArtifactCache

SOURCE = Path(__file__).resolve().parent / "data/gpt-researcher/capture.json"


def resources(usage):
    return a.Resources(cost_usd=unknown("No dollar charge reported by Codex CLI"),
        cost_scope=("model",), input_tokens=observed(usage["input_tokens"]),
        output_tokens=observed(usage["output_tokens"]), human_minutes=unknown("Not timed"))


class CuratedPilotImporter:
    ref = a.VersionRef(name="example.gpt-researcher-curated", revision="1")

    def __init__(self, cache, evidence_uri=None):
        self.cache = cache
        self.evidence_uri = evidence_uri

    def read(self, source, *, plan):
        raw = Path(source).read_bytes()
        capture = json.loads(raw)
        artifact = self.cache.write_bytes("curated-pilot", raw, "application/json")
        if self.evidence_uri is not None:
            artifact = replace(artifact, uri=self.evidence_uri)
        records = {r["case_id"]: (i, r) for i, r in enumerate(capture["runs"])}
        runs = []
        for assignment in plan.assignments:
            index, row = records[assignment.unit["case_id"]]
            run_id = "recorded/" + row["case_id"]
            execution_id = run_id + "/aggregate"

            def evidence(suffix):
                return a.EvidenceRef(artifact=artifact, locator=f"json:/runs/{index}/{suffix}",
                    description="Curated summary of retained native evidence; full captures omitted")

            start, end = (datetime.fromisoformat(row[k]) for k in ("started_at", "ended_at"))
            execution = a.Execution(id=execution_id, slot="recorded", retry_index=0,
                parent_id=None, status="completed", started_at=start, ended_at=end,
                effective_config=observed(capture["configuration"]), resources=resources(row["usage"]))
            events = tuple(a.Event(id=f"{execution_id}/call/{i+1}", execution_id=execution_id,
                kind="llm.call", at=None, fields=event, source=evidence(f"trace/{i}"))
                for i, event in enumerate(row["trace"]))
            runs.append(a.Run(id=run_id, assignment_id=assignment.id, status="completed",
                cost_scope=("model",), output={"answer_summary":row["answer"],
                    "grade":row["grade"], "metrics":row["metrics"], "judge_usage":row["judge_usage"]},
                output_state="available", output_sources=(execution_id,),
                artifacts={"curated.capture":artifact}, executions=(execution,),
                started_at=start, ended_at=end, events=events,
                environment=observed({"os":"Windows", **capture["upstream"]}),
                execution_inventory_complete=unknown("Public artifact is a curated projection"),
                native_refs={"case_id":row["case_id"], "row_index":str(index),
                    "public_capture_sha256":artifact.sha256}))
        return a.ImportedCapture(runs=tuple(runs),
            grading_inventory_complete=unknown("Public artifact summarizes original grading receipts"),
            projections=(a.ProjectionReport(mapper=self.ref,
                source_format=a.VersionRef(name="curated-gpt-researcher-pilot", revision="1"),
                sources=(artifact,), omitted_fields=("Full prompts and reports", "Scraped page bodies",
                    "CLI stdout and session identifiers", "Local paths", "Detailed grading receipts")),))


class RecordedMetrics:
    ref = a.VersionRef(name="example.gpt-researcher-recorded-metrics", revision="1")

    def compute(self, spec, context, run):
        judgment = spec.id == "target_correct"
        value = run.output["metrics"][spec.id]
        row_index = run.native_refs["row_index"]
        evidence = a.EvidenceRef(artifact=run.artifacts["curated.capture"],
            locator=f"json:/runs/{row_index}/" + ("grade" if judgment else f"metrics/{spec.id}"))
        return a.MetricOutput(task=a.Measurement(run_id=run.id, metric=spec.id, value=value,
            status="ok", basis="estimated" if judgment else "observed", evidence=(evidence,),
            reason="Recorded same-model SimpleQA judgment of target only; not whole-report factuality"
                   if judgment else "Recorded native count or timing"),
            evaluation_resources=resources(run.output["judge_usage"]) if judgment else zero_resources())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("demo-output/gpt-researcher"))
    parser.add_argument("--evidence-uri", help="Public HTTPS location of the unchanged capture")
    args = parser.parse_args()
    capture = json.loads(SOURCE.read_text(encoding="utf-8"))
    dataset = a.EvalDataset.from_records(id="gptr-selected-simpleqa",
        units=[{"case_id":r["case_id"],"query":r["query"]} for r in capture["runs"]],
        unit_key=("case_id",), input_columns={"units":("case_id","query")},
        references={"gold":a.DataTable(rows=tuple({"case_id":r["case_id"],
            "answer":r["grade"]["expected_answer"]} for r in capture["runs"]),
            key=("case_id",), schema={"case_id":"str","answer":"str"})})
    candidate = a.Candidate(id="gptr-codex-luna-recorded",
        backend=a.VersionRef(name="curated-capture-import", revision="1"),
        components={"model":a.ComponentSpec(kind="model", ref=a.VersionRef(name="gpt-5.6-luna"))},
        settings=capture["configuration"], description=capture["selection"])
    evaluator = RecordedMetrics()
    specs = tuple(a.MetricSpec(id=name, source=a.EvaluatorSource(ref=evaluator.ref),
        output_type=kind, role=role) for name, kind, role in [
            ("target_correct","int","quality"), ("model_calls","int","diagnostic"),
            ("search_calls","int","diagnostic"), ("captured_pages","int","diagnostic"),
            ("elapsed_seconds","float","resource")])
    study = a.Study(id="GPT Researcher · recorded pilot", project_id="gpt-researcher",
        question="Where did useful evidence disappear? Three selected runs, two correct targets. "
                 "Curated baseline evidence only: no measured answer improvement from the patch.",
        dataset=dataset, candidates={candidate.id:candidate},
        execution=a.ExecutionPolicy(budget=a.Budget(wall_time_s=1800)),
        suite=a.EvalSuite(id="recorded-simpleqa-pilot", version="1", metrics=specs,
            summaries=tuple(a.SummarySpec(id=s.id+"_mean",metric=s.id,reducer="mean") for s in specs),
            acceptance=a.AcceptanceRule(all_of=(a.Threshold(metric="target_correct",op="==",value=1),))))
    runs = a.RunSet.import_from(SOURCE, plan=study.plan(),
        importer=CuratedPilotImporter(ArtifactCache(args.output/"evidence"), args.evidence_uri))
    result = a.EvaluationPipeline(study=study,evaluators={evaluator.ref.name:evaluator}).eval(runs=runs)
    result.save(args.output/"result")
    loaded = a.EvaluationResult.load(args.output/"result")
    report = loaded.report(args.output/"report.html")
    assert len(loaded.runs.runs) == 3
    assert sum(r.output["metrics"]["target_correct"] for r in loaded.runs.runs) == 2
    print(f"Imported 3 recorded runs (2 correct targets); no live calls. Report: {report}")


if __name__ == "__main__":
    main()
