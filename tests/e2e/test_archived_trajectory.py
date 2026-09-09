"""Downloaded execution history -> public importer -> evaluation -> saved evidence.

This is an archived-data E2E, not a new SWE-agent execution or a production
SWE-agent adapter. Only the source adapter and metrics below are test-owned.
The planner, import reconciliation, grading, storage and reporting must be real.
"""
from dataclasses import replace
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from tests.e2e.support import artifact, plain, zero_resources


ROOT = Path(__file__).parent / "fixtures" / "sweagent_archive"
INSTANCE = "pydicom__pydicom-1458"
SOURCE = ROOT / (INSTANCE + ".traj")


def verified_archive():
    provenance = json.loads((ROOT / "PROVENANCE.json").read_text(encoding="utf-8"))
    for name, record in provenance["files"].items():
        raw = (ROOT / name).read_bytes()
        assert len(raw) == record["bytes"]
        assert hashlib.sha256(raw).hexdigest() == record["sha256"]
    return json.loads(SOURCE.read_text(encoding="utf-8"))


class ArchivedTrajectoryImporter:
    """Explicit mapping of this pinned published fixture, never replays commands."""

    def __init__(self, api):
        self.api = api
        self.ref = api.VersionRef(name="e2e.sweagent-archive", revision="fixture-v1")
        self.calls = 0

    def read(self, source, *, plan):
        self.calls += 1
        a = self.api
        native = verified_archive()
        assert Path(source).resolve() == SOURCE.resolve()
        assert len(plan.assignments) == 1
        assignment = plan.assignments[0]
        assert assignment.unit["task_id"] == INSTANCE
        ref = artifact(a, source)
        run_id = "archive/" + INSTANCE + "/" + ref.sha256[:16]
        execution_id = run_id + "/aggregate"

        def evidence(pointer):
            return a.EvidenceRef(artifact=ref, locator=pointer,
                                 description="Unmodified upstream execution archive")

        def recorded(value, pointer):
            return a.Observation(value=value, status="observed", evidence=(evidence(pointer),))

        def unknown(reason):
            return a.Observation(value=None, status="unknown", reason=reason)

        stats = native["info"]["model_stats"]
        execution = a.Execution(
            id=execution_id, slot="archive", retry_index=0, parent_id=None,
            status="completed", started_at=None, ended_at=None,
            effective_config=unknown("Archive does not establish the full effective model/harness configuration"),
            resources=a.Resources(
                cost_usd=recorded(Decimal(str(stats["instance_cost"])), "/info/model_stats/instance_cost"),
                cost_scope=("model",),
                input_tokens=recorded(stats["tokens_sent"], "/info/model_stats/tokens_sent"),
                output_tokens=recorded(stats["tokens_received"], "/info/model_stats/tokens_received"),
                human_minutes=unknown("No human time observation in archive")),
            native_refs={"instance_id": INSTANCE},
        )
        events = tuple(a.Event(
            id=f"{execution_id}/action/{index}", execution_id=execution_id,
            kind="native.action", at=None,
            fields={"index": index, "action": step["action"],
                    "observation": step["observation"], "native_state": step["state"]},
            inputs=(evidence(f"/trajectory/{index}/action"),),
            outputs=(evidence(f"/trajectory/{index}/observation"),),
            source=evidence(f"/trajectory/{index}"),
        ) for index, step in enumerate(native["trajectory"]))
        run = a.Run(
            id=run_id, assignment_id=assignment.id, status="completed",
            cost_scope=("model",), output={"patch": native["info"]["submission"],
                "native_exit_status": native["info"]["exit_status"]},
            output_state="available", artifacts={"native.trajectory": ref,
                "source.provenance": artifact(a, ROOT / "PROVENANCE.json")},
            executions=(execution,), started_at=None, ended_at=None,
            environment=recorded({"native_name": native["environment"]}, "/environment"),
            execution_inventory_complete=unknown("Only an aggregate bill; archive does not enumerate child executions"),
            output_sources=(execution_id,), events=events,
            native_refs={"instance_id": INSTANCE, "source_sha256": ref.sha256},
        )
        return a.ImportedCapture(
            runs=(run,), grading_inventory_complete=unknown("No benchmark grading inventory exported"),
            projections=(a.ProjectionReport(mapper=self.ref,
                source_format=a.VersionRef(name="SWE-agent.traj", revision=ref.sha256),
                sources=(ref,), omitted_fields=("history", "trajectory[].thought", "trajectory[].response")),),
        )


class ArchiveMeasurements:
    """Count actual retained actions/observations; never grade the medical code."""

    def __init__(self, api):
        self.api = api
        self.ref = api.VersionRef(name="e2e.archive-measurements", revision="fixture-v1")
        self.calls = 0

    def compute(self, spec, context, run):
        self.calls += 1
        actions = [event for event in run.events if event.kind == "native.action"]
        field = spec.params["field"]
        values = [1 if field == "actions" else len(event.fields["observation"].encode("utf-8"))
                  for event in actions]
        details = tuple(self.api.Measurement(
            run_id=run.id, metric=spec.id, key={"step": event.fields["index"]},
            value=value, status="ok", basis="observed", reason="Count from retained native evidence",
            evidence=(event.source,),
        ) for event, value in zip(actions, values, strict=True))
        return self.api.MetricOutput(
            task=self.api.Measurement(run_id=run.id, metric=spec.id, value=sum(values),
                status="ok", basis="observed", reason="Sum of source-linked step measurements",
                evidence=tuple(event.source for event in actions)),
            details=details, evaluation_resources=zero_resources(self.api),
        )


def test_published_multistep_trajectory_survives_import_evaluation_and_storage(api, tmp_path):
    native = verified_archive()
    metric = ArchiveMeasurements(api)
    dataset = api.EvalDataset.from_records(id="pydicom-archive/data",
        units=[{"task_id": INSTANCE, "issue": native["history"][2]["content"]}],
        unit_key=("task_id",), input_columns={"units": ("task_id", "issue")})
    candidate = api.Candidate(id="published-run", backend=api.VersionRef(name="archived-swe-agent"), components={},
        settings={"capture_kind": "historical", "source_sha256": artifact(api, SOURCE).sha256})
    suite = api.EvalSuite(id="archive-structure", version="v1", rubric=None,
        metrics=tuple(api.MetricSpec(id=name, source=api.EvaluatorSource(ref=metric.ref),
            output_type="int", role="diagnostic", params={"field": field})
            for name, field in (("action_count", "actions"), ("observation_bytes", "observations"))),
        summaries=(api.SummarySpec(id="actions_per_task", metric="action_count", reducer="mean"),))
    study = api.Study(id="archive-study", project_id="pydicom-archive",
        question="Can actual iteration history and evidence be used after import and persistence?",
        dataset=dataset, candidates={candidate.id: candidate}, suite=suite,
        execution=api.ExecutionPolicy(budget=api.Budget(wall_time_s=60), repetitions=1))
    importer = ArchivedTrajectoryImporter(api)
    capture = api.RunSet.import_from(SOURCE, plan=study.plan(), importer=importer)
    result = api.EvaluationPipeline(study=study, evaluators={metric.ref.name: metric}).eval(runs=capture)
    result.runs.validate().raise_for_errors()
    assert importer.calls == 1 and metric.calls == 2
    assert len(result.runs.runs) == 1
    run = result.runs.runs[0]
    assert len(run.events) == len(native["trajectory"]) == 12
    assert len(result.runs.rows("events")) == 12
    for index, event in enumerate(run.events):
        assert event.fields["action"] == native["trajectory"][index]["action"]
        assert event.fields["observation"] == native["trajectory"][index]["observation"]
        assert event.source.locator == f"/trajectory/{index}"
        assert event.inputs[0].locator == f"/trajectory/{index}/action"
        assert event.outputs[0].locator == f"/trajectory/{index}/observation"
    # The retained run includes real corrective iterations, not twelve echoes.
    assert any("SyntaxError" in event.fields["observation"] for event in run.events)
    assert sum(event.fields["action"].startswith("edit ") for event in run.events) >= 4
    assert run.output["patch"] == native["info"]["submission"]
    assert run.output["native_exit_status"] == "submitted"  # not "benchmark passed"
    assert run.duration_s().status == "unknown"
    assert run.started_at is None and run.ended_at is None
    assert run.executions[0].resources.cost_usd.value == Decimal("1.26719")
    assert run.executions[0].resources.input_tokens.value == 122612
    assert run.executions[0].resources.output_tokens.value == 1369
    # A known historical aggregate is preserved without asserting the archive
    # establishes a complete inventory of every execution/grade.
    assert run.resources().cost_usd.status == "unknown"
    assert run.resources().cost_usd.value is None and run.resources().cost_usd.reason
    assert result.runs.grading_inventory_complete.status == "unknown"
    assert result.evaluation_resources.cost_usd.status == "unknown"
    assert result.evaluation_resources.cost_usd.value is None
    assert result.incremental_evaluation_resources().cost_usd.value == Decimal("0")
    tasks = {m.metric: m for m in result.measurements if m.key is None}
    assert tasks["action_count"].value == 12
    expected_bytes = sum(len(step["observation"].encode("utf-8")) for step in native["trajectory"])
    assert tasks["observation_bytes"].value == expected_bytes
    assert len([m for m in result.measurements if m.key is not None]) == 24
    assert result.summary()[0].value.value == 12
    assert result.explain(run.id).evidence
    result.save(tmp_path / "archive-result")
    loaded = api.EvaluationResult.load(tmp_path / "archive-result")
    restored = loaded.runs.get(run.id)
    assert plain(restored.output) == plain(run.output)
    assert [(e.id, plain(e.fields), e.source.locator) for e in restored.events] == [
        (e.id, plain(e.fields), e.source.locator) for e in run.events]
    assert Path(restored.artifacts["native.trajectory"].uri).read_bytes() == SOURCE.read_bytes()
    assert restored.executions[0].resources == run.executions[0].resources
    assert isinstance(restored.executions[0].resources.cost_usd.value, Decimal)
    assert restored.resources().cost_usd == run.resources().cost_usd
    assert loaded.evaluation_resources == result.evaluation_resources
    assert loaded.explain(run.id).evidence
    assert loaded.report(tmp_path / "archive-report.html").is_file()
    changed = replace(study, suite=replace(suite, version="v2", metrics=suite.metrics[:1]))
    rescored = api.EvaluationPipeline(study=changed, evaluators={metric.ref.name: metric}).eval(runs=loaded.runs)
    assert rescored.runs.id == loaded.runs.id and rescored.id != loaded.id
    assert importer.calls == 1 and metric.calls == 3
    assert rescored.evaluation_resources.cost_usd.status == "unknown"
    assert rescored.incremental_evaluation_resources().cost_usd.value == Decimal("0")
    assert SOURCE.read_bytes() == Path(restored.artifacts["native.trajectory"].uri).read_bytes()
