"""Evaluate a real published SWE-agent repair history, without replaying commands.

Run from an installed checkout: python examples/archive_review.py --output demo-output
The importer and diagnostic metrics below are ordinary application plugins. They
use the production planner, evaluator, storage, explanation and HTML report.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import agent_eval_flow as a
from agent_eval_flow.objects.values import observed, unknown, zero_resources
from agent_eval_flow.storage.artifacts import ArtifactCache


DEFAULT_SOURCE = Path(__file__).resolve().parents[1] / "tests/e2e/fixtures/sweagent_archive/pydicom__pydicom-1458.traj"


class PublishedRepairImporter:
    ref = a.VersionRef(name="example.published-swe-agent", revision="1")

    def __init__(self, cache: ArtifactCache):
        self.cache = cache

    def read(self, source, *, plan):
        source = Path(source)
        if len(plan.assignments) != 1:
            raise a.ConfigurationError("This example maps one archived task to one assignment")
        data = source.read_bytes()
        native = json.loads(data)
        artifact = self.cache.write_bytes("trajectory", data, "application/json")
        assignment = plan.assignments[0]
        run_id = "historical/" + artifact.sha256[:16]
        execution_id = run_id + "/reported-aggregate"

        def evidence(pointer):
            return a.EvidenceRef(artifact=artifact, locator="json:" + pointer,
                                 description="Published SWE-agent repair history")

        stats = native["info"]["model_stats"]
        resources = a.Resources(
            cost_usd=a.Observation(value=Decimal(str(stats["instance_cost"])), status="estimated",
                reason="Historical agent-reported model cost; no provider invoice retained",
                evidence=(evidence("/info/model_stats/instance_cost"),)),
            cost_scope=("model",),
            input_tokens=observed(stats["tokens_sent"], evidence=(evidence("/info/model_stats/tokens_sent"),)),
            output_tokens=observed(stats["tokens_received"], evidence=(evidence("/info/model_stats/tokens_received"),)),
            human_minutes=unknown("Archive has no human-time receipt"))
        execution = a.Execution(id=execution_id, slot="archive", retry_index=0, parent_id=None,
            status="completed", started_at=None, ended_at=None,
            effective_config=unknown("Full effective configuration was not exported"), resources=resources)
        events = tuple(a.Event(id=f"{execution_id}/step/{index}", execution_id=execution_id,
            kind="native.action", at=None, fields={"step": index, "action": step["action"],
                "observation": step["observation"]}, source=evidence(f"/trajectory/{index}"),
            inputs=(evidence(f"/trajectory/{index}/action"),),
            outputs=(evidence(f"/trajectory/{index}/observation"),))
            for index, step in enumerate(native["trajectory"]))
        run = a.Run(id=run_id, assignment_id=assignment.id, status="completed", cost_scope=("model",),
            output={"patch": native["info"]["submission"], "native_exit_status": native["info"]["exit_status"]},
            output_state="available", output_sources=(execution_id,), artifacts={"native.trajectory": artifact},
            executions=(execution,), started_at=None, ended_at=None,
            environment=unknown("Historical execution host was not captured"),
            execution_inventory_complete=unknown("Export reports aggregate usage without complete child/retry inventory"),
            events=events, native_refs={"instance_id": str(assignment.unit["task_id"]), "source_sha256": artifact.sha256})
        return a.ImportedCapture(runs=(run,), grading_inventory_complete=unknown("Historical grading inventory absent"),
            projections=(a.ProjectionReport(mapper=self.ref,
                source_format=a.VersionRef(name="SWE-agent.traj", revision=artifact.sha256), sources=(artifact,),
                omitted_fields=("history", "trajectory[].thought", "trajectory[].response", "trajectory[].state")),))


class TraceDiagnostics:
    ref = a.VersionRef(name="example.trace-diagnostics", revision="1")

    def compute(self, spec, context, run):
        events = tuple(event for event in run.events if event.kind == "native.action")
        def value(event):
            if spec.id == "actions":
                return 1
            if spec.id == "syntax_error_observations":
                return int("SyntaxError" in event.fields["observation"])
            return len(event.fields["observation"].encode("utf-8"))
        details = tuple(a.Measurement(run_id=run.id, metric=spec.id, key={"step": event.fields["step"]},
            value=value(event), status="ok", basis="observed", reason="Count from the linked native observation",
            evidence=(event.source,)) for event in events)
        return a.MetricOutput(task=a.Measurement(run_id=run.id, metric=spec.id,
            value=sum(row.value for row in details), status="ok", basis="observed",
            reason="Sum across this task's source-linked steps; not independent task trials",
            evidence=tuple(event.source for event in events)), details=details,
            evaluation_resources=zero_resources())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trajectory", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=Path("demo-output"))
    args = parser.parse_args()
    source, output = args.trajectory.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source_bytes = source.read_bytes()
    provenance = source.parent / "PROVENANCE.json"
    if provenance.is_file():
        expected = json.loads(provenance.read_text(encoding="utf-8"))["files"][source.name]
        if hashlib.sha256(source_bytes).hexdigest() != expected["sha256"] or len(source_bytes) != expected["bytes"]:
            raise ValueError("Trajectory differs from its pinned provenance")
    native = json.loads(source_bytes)
    metric = TraceDiagnostics()
    data = a.EvalDataset.from_records(id="published-repair", units=[{
        "task_id": source.stem, "issue": native["history"][2]["content"]}],
        unit_key=("task_id",), input_columns={"units": ("task_id", "issue")})
    candidate = a.Candidate(id="published-swe-agent", backend=a.VersionRef(name="historical-import", revision="1"),
        components={}, settings={"source_sha256": hashlib.sha256(source_bytes).hexdigest()})
    metrics = tuple(a.MetricSpec(id=name, source=a.EvaluatorSource(ref=metric.ref), output_type="int", role="diagnostic")
                    for name in ("actions", "syntax_error_observations", "observation_bytes"))
    suite = a.EvalSuite(id="trace-diagnostics", version="1", metrics=metrics,
        summaries=tuple(a.SummarySpec(id=spec.id + "_per_task", metric=spec.id, reducer="mean") for spec in metrics))
    study = a.Study(id="Published repair investigation", project_id="swe-agent-archive",
        question="Can we inspect the actual tool choices, errors and corrections behind an agent result?",
        dataset=data, candidates={candidate.id: candidate}, suite=suite,
        execution=a.ExecutionPolicy(budget=a.Budget(wall_time_s=60)))
    capture = a.RunSet.import_from(source, plan=study.plan(), importer=PublishedRepairImporter(ArtifactCache(output / "evidence")))
    result = a.EvaluationPipeline(study=study, evaluators={metric.ref.name: metric}).eval(runs=capture)
    study.save(output / "study")
    result.save(output / "result")
    loaded = a.EvaluationResult.load(output / "result")
    report = loaded.report(output / "report.html")
    # Demonstrate a new suite over persisted evidence without a backend binding.
    changed = replace(study, suite=replace(suite, version="2", metrics=metrics[:1], summaries=suite.summaries[:1]))
    regraded = a.EvaluationPipeline(study=changed, evaluators={metric.ref.name: metric}).eval(runs=loaded.runs)
    regraded.save(output / "regraded")
    print(json.dumps({"report": str(report), "runs": len(loaded.runs.runs),
        "native_steps": len(loaded.runs.runs[0].events),
        "diagnostics": {row.summary_id: row.value.value for row in loaded.summary()},
        "saved_result": str(output / "result"), "regraded_same_capture": regraded.runs.id == loaded.runs.id,
        "new_agent_invocations": 0}, indent=2))


if __name__ == "__main__":
    main()
