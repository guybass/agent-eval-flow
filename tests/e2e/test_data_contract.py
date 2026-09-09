"""Future contract E2Es: real API, owned processes, job/batch boundary plugins.

These fixtures test data transport and result consumption, not Harbor or NAT
compatibility. No library planner, reconciler, serializer or grader is replaced.
"""
from dataclasses import replace
from decimal import Decimal
import json

import pytest

from tests.e2e.support import artifact, assert_usable_result, observed, zero_resources
from tests.e2e.test_toy_pipeline import build


class ProcessReceipt:
    """The test-owned job retains its nested process callback receipts."""

    def __init__(self):
        self.executions, self.events, self.artifacts = [], [], {}

    def record_execution(self, execution):
        self.executions.append(execution)

    def record_event(self, event):
        self.events.append(event)

    def record_artifact(self, name, artifact):
        self.artifacts[name] = artifact


class PartialNativeJob:
    def __init__(self, api, process_backend):
        self.api, self.process_backend = api, process_backend
        self.ref, self.calls = process_backend.ref, []
        self.omitted_run_id = None

    def capabilities(self):
        return self.api.NativeJobCapabilities(
            limits=self.process_backend.capabilities(),
            private_verifier_channel=False, fixed_repetitions=True,
        )

    async def run_job(self, request, *, recorder):
        a = self.api
        self.calls.append(request)
        assert request.config.native.values["fixture_export"] == "partial-reversed"
        assert not request.verifier_inputs
        completed = []
        for item in request.requests:
            assert "private_oracle" not in item.input.tables
            receipt = ProcessReceipt()
            run = self.process_backend.run(item, recorder=receipt)
            assert tuple(receipt.executions) == run.executions
            assert receipt.artifacts == run.artifacts
            completed.append(replace(run, job_id=request.job_id,
                                     native_refs={"trial": "trial-" + item.run_id}))
        self.omitted_run_id = completed[-1].id
        exported = tuple(reversed(completed[:-1]))
        record = a.NativeJobRecord(
            id=request.job_id, planned_job_id=request.planned_job_id,
            backend=self.ref,
            assignment_ids=tuple(item.assignment.id for item in request.requests),
            status="partial", started_at=completed[0].started_at,
            ended_at=completed[-1].ended_at,
            native_refs={"job": "owned-native-job-" + request.job_id},
            links=tuple(a.NativeRunLink(run_id=run.id, assignment_id=run.assignment_id,
                                        native_refs=run.native_refs) for run in exported),
        )
        recorder.record_job(record)
        for run in exported:
            recorder.record_run(run)
        return a.NativeJobOutput(job=record, runs=exported,
                                 grading_inventory_complete=observed(a, True))


def test_native_job_reconciles_reversed_partial_export(api, toy_backend, tmp_path):
    study, evaluators, reducers = build(api, toy_backend, "plain", repetitions=2)
    job = PartialNativeJob(api, toy_backend)
    config = api.NativeJobConfig(
        id="paired-toy", backend=job.ref, candidate_ids=("A", "B"),
        native=api.NativeConfig(
            schema_ref=api.VersionRef(name="e2e.native-job", revision="v1"),
            values={"fixture_export": "partial-reversed"},
        ),
    )
    study = replace(study, execution=replace(study.execution, native_jobs=(config,)))
    plan = study.plan()
    capture = study.run(job_backends={job.ref.name: job})

    assert len(job.calls) == 1
    assert len(toy_backend.calls) == len(plan.assignments) == 8
    assert len(plan.native_jobs) == len(capture.native_jobs) == 1
    native = capture.native_jobs[0]
    assert native.planned_job_id == plan.native_jobs[0].id
    assert set(native.assignment_ids) == {row.id for row in plan.assignments}
    assert len(native.links) == 7
    assert len(capture.runs) == 8
    missing = capture.get(job.omitted_run_id)
    assert missing.status == "unobserved" and missing.output_state == "unknown"
    assert missing.resources().cost_usd.value is None
    assert missing.job_id == native.id
    for link in native.links:
        run = capture.get(link.run_id)
        assert run.assignment_id == link.assignment_id and run.job_id == native.id
        assert run.native_refs == link.native_refs
        assignment = next(row for row in plan.assignments if row.id == link.assignment_id)
        assert run.output["task_id"] == assignment.unit["task_id"]
    capture.validate().raise_for_errors()
    capture.save(tmp_path / "job-capture")
    loaded = api.RunSet.load(tmp_path / "job-capture")
    assert loaded.native_jobs == capture.native_jobs
    assert loaded.coverage().unavailable == 1
    result = api.EvaluationPipeline(study=study, evaluators=evaluators,
                                    reducers=reducers).eval(runs=loaded)
    assert result.summary() and result.explain(missing.id).run_id == missing.id
    assert len(job.calls) == 1 and len(toy_backend.calls) == 8


class BatchWiringMetric:
    def __init__(self, api):
        self.api = api
        self.ref = api.VersionRef(name="e2e.batch-wiring", revision="v1")
        self.calls = []

    async def compute_batch(self, request):
        a = self.api
        self.calls.append(request)
        assert request.config_fingerprint and request.items
        rows = tuple(a.Measurement(
            run_id=item.run.id, metric=request.metric.id,
            value=item.run.output["probe_value"], status="ok", basis="observed",
            reason="Owned batch transport probe, not a quality judgment",
            evidence=tuple(a.EvidenceRef(artifact=ref, description="Actual process receipt")
                           for ref in item.run.artifacts.values()),
            activity_ids=(request.id,),
        ) for item in reversed(request.items))
        activity = a.EvaluationActivity(
            id=request.id, evaluator=self.ref,
            config_fingerprint=request.config_fingerprint,
            run_ids=tuple(item.run.id for item in request.items),
            status="completed", phase="post_run",
            resources=replace(zero_resources(a), cost_usd=observed(a, Decimal("0.20"))),
        )
        return a.BatchMetricOutput(measurements=rows, activity=activity)


def test_batch_grading_joins_by_id_and_charges_one_activity(api, toy_backend, tmp_path):
    study, evaluators, reducers = build(api, toy_backend, "plain")
    batch = BatchWiringMetric(api)
    metric = replace(study.suite.metrics[0],
                     source=api.EvaluatorSource(ref=batch.ref, mode="batch"))
    study = replace(study, suite=replace(study.suite,
                                        metrics=(metric, study.suite.metrics[1])))
    result = api.EvaluationPipeline(
        study=study, backends={toy_backend.ref.name: toy_backend},
        evaluators=evaluators, reducers=reducers,
        batch_evaluators={batch.ref.name: batch},
    ).eval()

    assert len(batch.calls) == 1 and len(batch.calls[0].items) == 4
    activity_id = batch.calls[0].id
    assert sum(item.id == activity_id for item in result.activities) == 1
    assert activity_id in result.performed_activity_ids
    assert result.evaluation_resources.cost_usd.value == Decimal("0.20")
    assert result.incremental_evaluation_resources().cost_usd.value == Decimal("0.20")
    for row in result.measurements:
        if row.metric == "wiring":
            assert row.value == result.runs.get(row.run_id).output["probe_value"]
            assert row.activity_ids == (activity_id,)
    loaded = assert_usable_result(api, study, result, root=tmp_path / "batch-result")
    assert loaded.activities == result.activities
    assert loaded.performed_activity_ids == result.performed_activity_ids
    assert loaded.evaluation_resources.cost_usd.value == Decimal("0.20")
    assert len(batch.calls) == 1 and len(toy_backend.calls) == 4


def test_boolean_task_key_is_rejected_before_identity_coercion(api):
    with pytest.raises(api.ValidationError):
        api.EvalDataset.from_records(
            id="invalid-boolean-key", units=[{"task_id": True, "text": "hello"}],
            unit_key=("task_id",), input_columns={"units": ("task_id", "text")},
        )


def test_retained_native_grades_share_cost_without_new_grading(api, toy_backend, tmp_path):
    study, callbacks, _ = build(api, toy_backend, "plain")
    capture = study.run(backends={toy_backend.ref.name: toy_backend})
    activity_id = "fixture-historical-grader"
    grader = api.VersionRef(name="e2e.retained-native-grader", revision="v1")
    records = [{"run_id": run.id, "probe": run.output["probe_value"]}
               for run in reversed(capture.runs)]
    receipt = tmp_path / "retained-native-grades.json"
    receipt.write_text(json.dumps({"activity_id": activity_id, "cost_usd": "0.20",
                                   "grades": records}), encoding="utf-8")
    source = artifact(api, receipt)
    activity = api.EvaluationActivity(
        id=activity_id, evaluator=grader, config_fingerprint=None,
        run_ids=tuple(run.id for run in capture.runs),
        status="completed", phase="native_verifier",
        resources=replace(zero_resources(api), cost_usd=observed(api, Decimal("0.20"))),
        artifacts={"native.grades": source},
    )
    grades = tuple(api.NativeGrade(
        id=f"fixture-grade-{index}", run_id=row["run_id"], native_metric="probe",
        activity_id=activity_id, value=row["probe"], status="ok", basis="observed",
        reason="Retained fixture value tests transport, not metric science",
        evidence=(api.EvidenceRef(artifact=source, locator=f"/grades/{index}/probe",
                                 description="Retained native grade receipt"),),
    ) for index, row in enumerate(records))
    bundle = api.NativeGradeBundle(
        id="fixture-grade-bundle", channel="fixture.native", grades=grades,
        activities=(activity,),
        projection=api.ProjectionReport(
            mapper=api.VersionRef(name="e2e.grade-mapper", revision="v1"),
            source_format=api.VersionRef(name="e2e.grade-receipt", revision="v1"),
            sources=(source,),
        ),
    )
    capture = replace(capture, native_grades=(bundle,))
    capture.validate().raise_for_errors()
    capture.save(tmp_path / "retained-capture")
    loaded_capture = api.RunSet.load(tmp_path / "retained-capture")
    assert loaded_capture.native_grades == (bundle,)
    metric = replace(study.suite.metrics[0], source=api.NativeGradeSource(
        channel=bundle.channel, native_metric="probe", evaluator=grader,
        activity_id=activity_id,
    ))
    study = replace(study, suite=replace(study.suite, metrics=(metric,),
                                        summaries=(study.suite.summaries[0],)))
    result = api.EvaluationPipeline(study=study, evaluators={}).eval(runs=loaded_capture)

    assert len(result.measurements) == len(grades) == 4
    assert tuple(item.id for item in result.activities) == (activity_id,)
    assert result.performed_activity_ids == ()
    assert result.evaluation_resources.cost_usd.value == Decimal("0.20")
    assert result.incremental_evaluation_resources().cost_usd.value == Decimal("0")
    expected = {grade.run_id: grade for grade in grades}
    for row in result.measurements:
        assert row.metric == "wiring" and row.value == expected[row.run_id].value
        assert row.activity_ids == (activity_id,)
        assert row.evidence == expected[row.run_id].evidence
    result.save(tmp_path / "retained-result")
    loaded = api.EvaluationResult.load(tmp_path / "retained-result")
    assert loaded.activities == result.activities
    assert loaded.runs.native_grades == result.runs.native_grades
    assert loaded.measurements == result.measurements
    assert loaded.incremental_evaluation_resources().cost_usd.value == Decimal("0")
    assert loaded.summary()
    for run in loaded.runs.runs:
        assert loaded.explain(run.id).evidence
    incomplete = replace(loaded_capture, grading_inventory_complete=observed(api, False))
    incomplete_result = api.EvaluationPipeline(study=study, evaluators={}).eval(runs=incomplete)
    assert incomplete_result.evaluation_resources.cost_usd.status == "unknown"
    assert incomplete_result.evaluation_resources.cost_usd.value is None
    assert incomplete_result.evaluation_resources.cost_usd.reason
    assert incomplete_result.incremental_evaluation_resources().cost_usd.value == Decimal("0")
    assert incomplete_result.performed_grading_inventory_complete.status == "observed"
    assert incomplete_result.performed_grading_inventory_complete.value is True
    assert {row.run_id: row.value for row in incomplete_result.measurements} == {
        row.run_id: row.value for row in loaded.measurements}
    assert len(toy_backend.calls) == 4
    assert all(not callback.calls for callback in callbacks.values())
