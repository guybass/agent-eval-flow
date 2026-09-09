"""Whole-job dispatch contracts with real test-owned child processes.

The fixture supplies an adapter, never a replacement planner or reconciler.
Its native verifier is a deterministic transport probe, not a scientific metric.
"""
import asyncio
from dataclasses import replace
from threading import Lock

import pytest

from tests.e2e.support import observed, zero_resources
from tests.e2e.test_data_contract import ProcessReceipt
from tests.e2e.test_toy_pipeline import build


class GroupProbe:
    def __init__(self):
        self.lock = Lock()
        self.native = set()
        self.direct_active = 0
        self.events = []


class DirectProbe:
    def __init__(self, process_backend, probe):
        self.backend, self.probe = process_backend, probe
        self.ref = process_backend.ref
        self.calls = []

    def capabilities(self):
        return self.backend.capabilities()

    def run(self, request, *, recorder):
        with self.probe.lock:
            assert not self.probe.native, "Direct work cannot overlap a native job group"
            self.probe.direct_active += 1
            assert self.probe.direct_active <= request.policy.max_concurrency
            self.probe.events.append(("direct", request.assignment.id))
            self.calls.append(request)
        try:
            return self.backend.run(request, recorder=recorder)
        finally:
            with self.probe.lock:
                self.probe.direct_active -= 1


class OwnedNativeJob:
    def __init__(self, api, process_backend, *, probe=None, private_channel=True,
                 fixed_repetitions=True, fail_after=None, repeat_receipts=False):
        self.api, self.process_backend = api, process_backend
        self.ref = api.VersionRef(name="e2e.whole-job", revision="1")
        self.probe = probe or GroupProbe()
        self.private_channel = private_channel
        self.fixed_repetitions = fixed_repetitions
        self.fail_after = fail_after
        self.repeat_receipts = repeat_receipts
        self.calls = []
        self.completed = []

    def capabilities(self):
        return self.api.NativeJobCapabilities(
            limits=self.process_backend.capabilities(),
            private_verifier_channel=self.private_channel,
            fixed_repetitions=self.fixed_repetitions,
        )

    def record(self, request, runs, status):
        return self.api.NativeJobRecord(
            id=request.job_id, planned_job_id=request.planned_job_id,
            backend=self.ref, assignment_ids=tuple(item.assignment.id for item in request.requests),
            status=status, started_at=runs[0].started_at if runs else None,
            ended_at=runs[-1].ended_at if status == "completed" and runs else None,
            native_refs={"job": "fixture-" + request.job_id},
            links=tuple(self.api.NativeRunLink(
                run_id=run.id, assignment_id=run.assignment_id, native_refs=run.native_refs,
            ) for run in runs),
        )

    def grade(self, request):
        bundles = []
        for verifier in request.config.verifiers:
            projections = tuple(item for item in request.verifier_inputs if item.verifier_id == verifier.id)
            activity_id = request.job_id + "/grade/" + verifier.id
            activity = self.api.EvaluationActivity(
                id=activity_id, evaluator=verifier.implementation,
                config_fingerprint="fixture-verifier-configuration-v1",
                run_ids=tuple(item.run_id for item in projections), status="completed",
                phase="native_verifier", resources=zero_resources(self.api),
            )
            grades = tuple(self.api.NativeGrade(
                id=activity_id + "/" + item.run_id, run_id=item.run_id,
                native_metric="projection_received", activity_id=activity_id,
                value=bool(item.references), status="ok", basis="observed",
                reason="Fixture verifier received its projected input",
            ) for item in projections)
            bundles.append(self.api.NativeGradeBundle(
                id=activity_id + "/bundle", channel=verifier.id, grades=grades,
                activities=(activity,), projection=self.api.ProjectionReport(
                    mapper=self.api.VersionRef(name="e2e.native-projection", revision="1"),
                    source_format=self.api.VersionRef(name="e2e.fixture-grades", revision="1"),
                    sources=(),
                ),
            ))
        return tuple(bundles)

    async def run_job(self, request, *, recorder):
        with self.probe.lock:
            assert not self.probe.native and self.probe.direct_active == 0
            self.probe.native.add(request.job_id)
            self.probe.events.append(("native-start", request.config.id))
        self.calls.append(request)
        runs = []
        try:
            recorder.record_job(self.record(request, runs, "running"))
            await asyncio.sleep(0)  # permit an incorrect concurrent dispatcher to expose overlap
            for item in request.requests:
                if self.fail_after == len(runs):
                    raise RuntimeError("fixture-native-export-disconnected")
                assert "EVALUATOR_ONLY" not in repr(item)
                assert "PRIVATE_UNSELECTED" not in repr(item)
                receipt = ProcessReceipt()
                run = self.process_backend.run(item, recorder=receipt)
                run = replace(run, job_id=request.job_id,
                              native_refs={"trial": "fixture-trial-" + item.run_id})
                assert tuple(receipt.executions) == run.executions
                runs.append(run)
                self.completed.append(run)
                recorder.record_run(run)
                if self.repeat_receipts:
                    recorder.record_run(run)
                recorder.record_job(self.record(request, runs, "running"))
                await asyncio.sleep(0)
            record = self.record(request, runs, "completed")
            bundles = self.grade(request)
            recorder.record_job(record)
            for bundle in bundles:
                recorder.record_grade_bundle(bundle)
                if self.repeat_receipts:
                    recorder.record_grade_bundle(bundle)
            return self.api.NativeJobOutput(
                job=record, runs=tuple(reversed(runs)), native_grades=bundles,
                grading_inventory_complete=observed(self.api, True),
            )
        finally:
            with self.probe.lock:
                self.probe.native.remove(request.job_id)
                self.probe.events.append(("native-end", request.config.id))


def native_study(api, toy_backend, *, private=True, **adapter_options):
    study, _, _ = build(api, toy_backend, "plain")
    adapter = OwnedNativeJob(api, toy_backend, **adapter_options)
    candidates = {key: replace(candidate, backend=adapter.ref) for key, candidate in study.candidates.items()}
    verifiers = ()
    if private:
        oracle = api.DataTable(
            rows=tuple({"task_id": task, "finding_id": finding,
                        "expected": "EVALUATOR_ONLY:" + task + ":" + finding,
                        "unselected": "PRIVATE_UNSELECTED"}
                       for task in ("toy-1", "toy-2") for finding in ("f1", "f2")),
            key=("task_id", "finding_id"),
            schema={"task_id": "str", "finding_id": "str", "expected": "str", "unselected": "str"},
        )
        study = replace(study, dataset=replace(study.dataset, references={"oracle": oracle}))
        verifiers = (api.VerifierSpec(
            id="private-check", implementation=api.VersionRef(name="fixture.verifier", revision="1"),
            reference_columns={"oracle": ("expected",)}, budget=api.Budget(wall_time_s=2.0),
        ),)
    job = api.NativeJobConfig(id="native", backend=adapter.ref, candidate_ids=("A", "B"), verifiers=verifiers)
    study = replace(study, candidates=candidates,
                    execution=replace(study.execution, native_jobs=(job,)))
    return study, adapter


def test_native_verifier_gets_keyed_private_projection_and_agent_gets_only_public_input(api, toy_backend):
    study, adapter = native_study(api, toy_backend)
    capture = study.run(job_backends={adapter.ref.name: adapter})
    assert len(adapter.calls) == 1 and len(toy_backend.calls) == 4
    request = adapter.calls[0]
    allocated = {item.run_id: item for item in request.requests}
    assert len(request.verifier_inputs) == len(allocated) == 4
    assert len({(item.run_id, item.verifier_id) for item in request.verifier_inputs}) == 4
    for item in request.verifier_inputs:
        assert item.verifier_id == "private-check" and set(item.references) == {"oracle"}
        task_id = allocated[item.run_id].assignment.unit["task_id"]
        assert len(item.references["oracle"]) == 2
        assert {row["finding_id"] for row in item.references["oracle"]} == {"f1", "f2"}
        for row in item.references["oracle"]:
            assert set(row) == {"task_id", "finding_id", "expected"}
            assert row["task_id"] == task_id
            assert row["expected"].startswith("EVALUATOR_ONLY:" + task_id + ":")
    for process_request in toy_backend.calls:
        assert set(process_request.input.tables) == {"units"}
        assert set(process_request.input.tables["units"][0]) == {"task_id", "text"}
        assert "EVALUATOR_ONLY" not in repr(process_request)
    assert len(capture.native_grades) == 1 and len(capture.native_grades[0].grades) == 4
    assert all(run.status == "completed" and run.output_state == "available" for run in capture.runs)
    capture.validate().raise_for_errors()


def test_disjoint_native_jobs_and_direct_group_dispatch_each_assignment_once(api, toy_backend):
    study, _, _ = build(api, toy_backend, "plain", repetitions=2)
    probe = GroupProbe()
    job_adapter = OwnedNativeJob(api, toy_backend, probe=probe)
    direct = DirectProbe(toy_backend, probe)
    candidates = {key: replace(candidate, backend=job_adapter.ref) for key, candidate in study.candidates.items()}
    candidates["C"] = study.candidates["A"].derive(id="C")
    jobs = tuple(api.NativeJobConfig(id="group-" + key, backend=job_adapter.ref, candidate_ids=(key,))
                 for key in ("A", "B"))
    study = replace(study, candidates=candidates,
                    execution=replace(study.execution, native_jobs=jobs, max_concurrency=2))
    capture = study.run(backends={direct.ref.name: direct}, job_backends={job_adapter.ref.name: job_adapter})
    plan = study.plan()
    assert len(job_adapter.calls) == 2 and len(direct.calls) == 4 and len(toy_backend.calls) == 12
    requests = tuple(item for job in job_adapter.calls for item in job.requests) + tuple(direct.calls)
    assert len({item.assignment.id for item in requests}) == len(plan.assignments) == 12
    assert {item.assignment.id for item in requests} == {item.id for item in plan.assignments}
    assert len({item.run_id for item in requests}) == 12
    assert all(item.policy.max_concurrency == 2 for item in requests)
    for job in job_adapter.calls:
        assert len(job.requests) == 4
        assert {item.assignment.candidate_id for item in job.requests} == set(job.config.candidate_ids)
    event_kinds = [kind for kind, _ in probe.events]
    direct_positions = [index for index, kind in enumerate(event_kinds) if kind == "direct"]
    assert direct_positions == list(range(min(direct_positions), max(direct_positions) + 1))
    assert len(capture.native_jobs) == 2 and all(run.status == "completed" for run in capture.runs)
    direct_assignments = {item.assignment.id for item in direct.calls}
    assert all((run.job_id is None) == (run.assignment_id in direct_assignments) for run in capture.runs)
    capture.validate().raise_for_errors()


@pytest.mark.parametrize("unsupported", ["private_channel", "fixed_repetitions"])
def test_native_capability_mismatch_fails_preflight_without_dispatch(api, toy_backend, unsupported):
    options = {unsupported: False}
    study, adapter = native_study(api, toy_backend, **options)
    if unsupported == "fixed_repetitions":
        study = replace(study, execution=replace(study.execution, repetitions=2))
    with pytest.raises(api.ConfigurationError):
        study.run(job_backends={adapter.ref.name: adapter})
    assert adapter.calls == [] and toy_backend.calls == []


@pytest.mark.parametrize("completed_before_exception", [0, 1])
def test_native_runtime_exception_preserves_recorded_work_and_allocated_missing_runs(api, toy_backend,
                                                                                   completed_before_exception):
    study, adapter = native_study(api, toy_backend, fail_after=completed_before_exception)
    study = replace(study, execution=replace(study.execution, infrastructure_retries=2))
    capture = study.run(job_backends={adapter.ref.name: adapter})
    assert len(adapter.calls) == 1, "The library must not wrap native trials in a second retry loop"
    request = adapter.calls[0]
    assert len(toy_backend.calls) == completed_before_exception
    assert len(capture.native_jobs) == 1
    job = capture.native_jobs[0]
    assert job.status == "error" and job.error is not None
    assert "fixture-native-export-disconnected" in job.error.message
    assert job.id == request.job_id and job.planned_job_id == request.planned_job_id
    assert set(job.assignment_ids) == {item.assignment.id for item in request.requests}
    assert {run.id for run in capture.runs} == {item.run_id for item in request.requests}
    assert len(capture.runs) == 4
    for run in adapter.completed:
        assert capture.get(run.id) == run
    known_ids = {run.id for run in adapter.completed}
    missing = [run for run in capture.runs if run.id not in known_ids]
    assert len(missing) == 4 - completed_before_exception
    for run in missing:
        assert run.job_id == job.id and run.status == "unobserved"
        assert run.output_state == "unknown" and run.output is None
        assert run.resources().cost_usd.status == "unknown"
    assert capture.grading_inventory_complete.status == "unknown"
    assert capture.grading_inventory_complete.value is None and capture.grading_inventory_complete.reason
    assert capture.coverage().completed == completed_before_exception
    assert capture.coverage().unavailable == len(missing)
    capture.validate().raise_for_errors()


def test_repeated_native_receipts_do_not_duplicate_runs_or_grading_activities(api, toy_backend):
    study, adapter = native_study(api, toy_backend, repeat_receipts=True)
    capture = study.run(job_backends={adapter.ref.name: adapter})
    assert len(capture.runs) == len(toy_backend.calls) == 4
    assert all(len(run.executions) == 1 for run in capture.runs)
    assert len(capture.native_jobs) == 1 and len(capture.native_jobs[0].links) == 4
    assert len(capture.native_grades) == 1
    assert len(capture.native_grades[0].activities) == 1 and len(capture.native_grades[0].grades) == 4
    capture.validate().raise_for_errors()


def test_fresh_native_execution_reuses_assignment_identity_but_allocates_new_run_and_job_ids(api, toy_backend):
    study, adapter = native_study(api, toy_backend, private=False)
    first = study.run(job_backends={adapter.ref.name: adapter})
    second = study.run(job_backends={adapter.ref.name: adapter})
    assert first.id != second.id
    assert first.native_jobs[0].id != second.native_jobs[0].id
    assert first.native_jobs[0].planned_job_id == second.native_jobs[0].planned_job_id
    assert {run.assignment_id for run in first.runs} == {run.assignment_id for run in second.runs}
    assert {run.id for run in first.runs}.isdisjoint(run.id for run in second.runs)
    assert len(adapter.calls) == 2 and len(toy_backend.calls) == 8
