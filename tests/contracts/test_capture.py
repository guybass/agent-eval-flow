"""Capture boundary contracts: identities and recorded facts, not metric quality.

The real library builds, validates and imports each capture. Test-owned adapters
only execute the existing toy process or supply deliberately malformed receipts.
"""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import importlib
import json

import pytest

from tests.e2e.support import artifact, observed, zero_resources
from tests.e2e.test_data_contract import ProcessReceipt
from tests.e2e.test_toy_pipeline import build


@pytest.fixture
def capture(api, toy_backend):
    study, _, _ = build(api, toy_backend, "plain")
    return study.run(backends={toy_backend.ref.name: toy_backend})


def with_first(capture, run):
    return replace(capture, runs=(run, *capture.runs[1:]))


def unknown(api, reason="The native receipt does not establish this quantity"):
    return api.Observation(value=None, status="unknown", reason=reason)


def validation_errors(api):
    """Typed construction may reject before the public capture validator runs."""
    return api.ValidationError, importlib.import_module("pydantic").ValidationError


@pytest.mark.parametrize("state", ["available", "unavailable", "unknown"])
def test_json_null_preserves_its_output_availability(api, capture, tmp_path, state):
    original = capture.runs[0]
    run = replace(original, output=None, output_state=state,
                  output_sources=original.output_sources if state == "available" else ())
    changed = with_first(capture, run)
    changed.validate().raise_for_errors()
    assert run.status == "completed", "Execution status does not decide output availability"
    changed.save(tmp_path / "nullable-output")
    loaded = api.RunSet.load(tmp_path / "nullable-output").get(run.id)
    assert loaded.output is None and loaded.output_state == state
    assert loaded.output_sources == run.output_sources


@pytest.mark.parametrize("state", ["unavailable", "unknown"])
def test_nonnull_output_cannot_claim_absent_or_unknown_capture(api, capture, state):
    with pytest.raises(validation_errors(api)):
        run = replace(capture.runs[0], output={"actual": "captured"}, output_state=state)
        with_first(capture, run).validate().raise_for_errors()


def test_coverage_partitions_all_statuses_and_unknown_resources_overlap(api, toy_backend):
    study, _, _ = build(api, toy_backend, "plain", repetitions=3)
    capture = study.run(backends={toy_backend.ref.name: toy_backend})
    statuses = ("completed", "agent_error", "timed_out", "infrastructure_error",
                "cancelled", "running", "awaiting_input", "paused", "not_run",
                "unobserved", "completed", "completed")
    rows = []
    for run, status in zip(capture.runs, statuses, strict=True):
        unavailable = status in {"not_run", "unobserved"}
        pending = status in {"running", "awaiting_input", "paused"}
        rows.append(replace(
            run, status=status, output=None, output_state="unknown", output_sources=(),
            executions=() if unavailable else run.executions,
            events=() if unavailable else run.events,
            started_at=None if unavailable else run.started_at,
            ended_at=None if unavailable or pending else run.ended_at,
            execution_inventory_complete=unknown(api),
        ))
    changed = replace(capture, runs=tuple(rows))
    changed.validate().raise_for_errors()
    coverage = changed.coverage()
    assert (coverage.planned, coverage.completed, coverage.failed,
            coverage.pending, coverage.unavailable) == (12, 3, 4, 3, 2)
    assert coverage.planned == sum((coverage.completed, coverage.failed,
                                    coverage.pending, coverage.unavailable))
    assert coverage.resource_unknown == 12
    for candidate_id in study.candidates:
        subset = changed.for_candidate(candidate_id)
        own = changed.coverage(candidate_id)
        assert own.planned == len(subset) == 6
        assert own.resource_unknown == 6
        assert own.completed == sum(run.status == "completed" for run in subset)


@pytest.mark.parametrize("status,value", [("observed", False), ("unknown", None),
                                          ("estimated", True)])
def test_full_resource_totals_require_observed_complete_inventory(api, capture, status, value):
    inventory = api.Observation(value=value, status=status, reason="Fixture inventory evidence")
    run = replace(capture.runs[0], execution_inventory_complete=inventory)
    with_first(capture, run).validate().raise_for_errors()
    totals = run.resources()
    assert totals.cost_scope == run.cost_scope
    for field in ("cost_usd", "input_tokens", "output_tokens", "human_minutes"):
        item = getattr(totals, field)
        assert item.status == "unknown" and item.value is None and item.reason
    assert run.executions[0].resources.cost_usd.value == Decimal("0")


def test_unknown_cost_does_not_erase_independently_known_token_counts(api, capture):
    original = capture.runs[0]
    resources = replace(zero_resources(api), cost_usd=unknown(api),
                        input_tokens=observed(api, 23), output_tokens=observed(api, 7))
    run = replace(original, executions=(replace(original.executions[0], resources=resources),))
    with_first(capture, run).validate().raise_for_errors()
    totals = run.resources()
    assert totals.cost_usd.status == "unknown" and totals.cost_usd.value is None
    assert totals.cost_usd.reason
    assert totals.input_tokens.status == "observed" and totals.input_tokens.value == 23
    assert totals.output_tokens.status == "observed" and totals.output_tokens.value == 7


def test_estimated_resource_component_remains_estimated_in_total(api, capture):
    original = capture.runs[0]
    main = replace(original.executions[0], resources=replace(
        zero_resources(api), cost_usd=observed(api, Decimal("0.40"))))
    child = replace(main, id=main.id + "/estimated-review", parent_id=main.id, role="review",
                    resources=replace(zero_resources(api), cost_usd=api.Observation(
                        value=Decimal("0.10"), status="estimated", reason="Fixture price estimate")))
    run = replace(original, executions=(main, child))
    with_first(capture, run).validate().raise_for_errors()
    assert run.resources().cost_usd.value == Decimal("0.50")
    assert run.resources().cost_usd.status == "estimated"
    assert run.resources().input_tokens.status == "observed"


def test_exclusive_parent_child_and_unsuccessful_work_are_summed_once(api, capture):
    original = capture.runs[0]
    main = replace(original.executions[0], resources=replace(
        zero_resources(api), cost_usd=observed(api, Decimal("0.40"))))
    child = replace(main, id=main.id + "/child", parent_id=main.id, role="subagent",
                    resources=replace(zero_resources(api), cost_usd=observed(api, Decimal("0.20"))))
    failed = replace(main, id=main.id + "/failed-attempt", slot="alternate", retry_index=1,
                     role="attempt", status="agent_error",
                     error=api.ErrorRecord(code="fixture", message="Discarded attempt still consumed resources"),
                     resources=replace(zero_resources(api), cost_usd=observed(api, Decimal("0.10"))))
    run = replace(original, executions=(child, failed, main), output_sources=(child.id,))
    with_first(capture, run).validate().raise_for_errors()
    assert run.resources().cost_usd.value == Decimal("0.70")
    assert run.resources().cost_usd.status == "observed"
    assert len(run.executions) == 3


def test_duration_uses_root_interval_and_keeps_unconfirmed_stop_unknown(api, capture):
    original = capture.runs[0]
    start = original.started_at
    main = replace(original.executions[0], started_at=start, ended_at=start + timedelta(seconds=9))
    child = replace(main, id=main.id + "/overlap", parent_id=main.id,
                    started_at=start + timedelta(seconds=1), ended_at=start + timedelta(seconds=8))
    run = replace(original, started_at=start, ended_at=start + timedelta(seconds=10),
                  executions=(main, child))
    with_first(capture, run).validate().raise_for_errors()
    assert run.duration_s().status == "observed" and run.duration_s().value == 10.0
    unstopped = replace(run, status="running", ended_at=None)
    with_first(capture, unstopped).validate().raise_for_errors()
    assert unstopped.duration_s().status == "unknown"
    assert unstopped.duration_s().value is None and unstopped.duration_s().reason


@pytest.mark.parametrize("invalid", [
    "duplicate_run_id", "duplicate_assignment", "unknown_assignment",
    "duplicate_execution", "dangling_parent", "cyclic_parents",
    "dangling_event", "duplicate_event", "dangling_output_source",
])
def test_capture_rejects_invalid_identity_graphs(api, capture, invalid):
    with pytest.raises(validation_errors(api)):
        run, other = capture.runs[:2]
        execution = run.executions[0]
        if invalid == "duplicate_run_id":
            run = replace(run, id=other.id)
        elif invalid == "duplicate_assignment":
            run = replace(run, assignment_id=other.assignment_id)
        elif invalid == "unknown_assignment":
            run = replace(run, assignment_id="not-in-the-plan")
        elif invalid == "duplicate_execution":
            run = replace(run, executions=(execution, execution))
        elif invalid == "dangling_parent":
            run = replace(run, executions=(replace(execution, parent_id="absent-execution"),))
        elif invalid == "cyclic_parents":
            child_id = execution.id + "/child"
            run = replace(run, executions=(replace(execution, parent_id=child_id),
                replace(execution, id=child_id, parent_id=execution.id)))
        elif invalid == "dangling_event":
            run = replace(run, events=(api.Event(id="event", execution_id="absent-execution",
                          kind="tool_call", at=run.ended_at, fields={}),))
        elif invalid == "duplicate_event":
            event = api.Event(id="event", execution_id=execution.id,
                              kind="tool_call", at=run.ended_at, fields={})
            run = replace(run, events=(event, event))
        else:
            run = replace(run, output_sources=("absent-execution",))
        with_first(capture, run).validate().raise_for_errors()


@pytest.mark.parametrize("target", ["run", "execution"])
def test_agent_resource_scopes_must_match_the_plan(api, capture, target):
    with pytest.raises(validation_errors(api)):
        run = capture.runs[0]
        if target == "run":
            run = replace(run, cost_scope=("model", "compute"))
        else:
            execution = run.executions[0]
            run = replace(run, executions=(replace(execution, resources=replace(
                execution.resources, cost_scope=("model", "compute"))),))
        with_first(capture, run).validate().raise_for_errors()


@pytest.mark.parametrize("field,value", [
    ("cost_usd", Decimal("-0.01")), ("cost_usd", Decimal("NaN")),
    ("cost_usd", Decimal("Infinity")), ("input_tokens", -1),
    ("input_tokens", True), ("input_tokens", 1.25), ("output_tokens", -1),
    ("human_minutes", -0.1), ("human_minutes", float("nan")),
    ("human_minutes", float("inf")),
])
def test_resource_quantities_are_strict_finite_nonnegative_values(api, capture, field, value):
    with pytest.raises(validation_errors(api)):
        run = capture.runs[0]
        execution = run.executions[0]
        resources = replace(execution.resources, **{field: observed(api, value)})
        changed = replace(run, executions=(replace(execution, resources=resources),))
        with_first(capture, changed).validate().raise_for_errors()


@pytest.mark.parametrize("status,value,reason", [
    ("observed", None, "A known value cannot be null"),
    ("estimated", None, "An estimate still requires a value"),
    ("unknown", Decimal("0"), "Unknown does not mean free"),
    ("unknown", None, None),
])
def test_resource_observation_status_value_and_reason_agree(api, capture, status, value, reason):
    with pytest.raises(validation_errors(api)):
        run = capture.runs[0]
        execution = run.executions[0]
        observation = api.Observation(value=value, status=status, reason=reason)
        resources = replace(execution.resources, cost_usd=observation)
        changed = replace(run, executions=(replace(execution, resources=resources),))
        with_first(capture, changed).validate().raise_for_errors()


@pytest.mark.parametrize("retry_index", [-1, True, 0.5])
def test_retry_indices_are_nonnegative_strict_integers(api, capture, retry_index):
    with pytest.raises(validation_errors(api)):
        run = capture.runs[0]
        execution = replace(run.executions[0], retry_index=retry_index)
        with_first(capture, replace(run, executions=(execution,))).validate().raise_for_errors()


@pytest.mark.parametrize("invalid", ["naive_run_start", "naive_execution_end",
                                      "reversed_run_interval", "reversed_execution_interval"])
def test_captured_timestamps_are_aware_and_chronologically_valid(api, capture, invalid):
    with pytest.raises(validation_errors(api)):
        run = capture.runs[0]
        execution = run.executions[0]
        if invalid == "naive_run_start":
            run = replace(run, started_at=run.started_at.replace(tzinfo=None))
        elif invalid == "naive_execution_end":
            run = replace(run, executions=(replace(execution,
                ended_at=execution.ended_at.replace(tzinfo=None)),))
        elif invalid == "reversed_run_interval":
            run = replace(run, ended_at=run.started_at - timedelta(seconds=1))
        else:
            run = replace(run, executions=(replace(execution,
                ended_at=execution.started_at - timedelta(seconds=1)),))
        with_first(capture, run).validate().raise_for_errors()


class NativeReceiptBackend:
    """A whole-job protocol fixture returning actual toy process receipts."""

    def __init__(self, api, process_backend, invalid=None):
        self.api, self.process_backend, self.invalid = api, process_backend, invalid
        self.ref, self.calls = process_backend.ref, []

    def capabilities(self):
        return self.api.NativeJobCapabilities(limits=self.process_backend.capabilities(),
            private_verifier_channel=False, fixed_repetitions=True)

    async def run_job(self, request, *, recorder):
        self.calls.append(request)
        runs = tuple(replace(self.process_backend.run(item, recorder=ProcessReceipt()),
                             job_id=request.job_id, native_refs={"trial": str(index)})
                     for index, item in enumerate(request.requests))
        job = self.api.NativeJobRecord(
            id=request.job_id, planned_job_id=request.planned_job_id, backend=self.ref,
            assignment_ids=tuple(item.assignment.id for item in request.requests),
            status="completed", started_at=runs[0].started_at, ended_at=runs[-1].ended_at,
            links=tuple(self.api.NativeRunLink(run_id=run.id, assignment_id=run.assignment_id,
                                               native_refs=run.native_refs) for run in runs))
        if self.invalid == "unallocated_run_id":
            renamed = replace(runs[0], id="unallocated-native-run")
            runs = (renamed, *runs[1:])
            job = replace(job, links=(replace(job.links[0], run_id=renamed.id), *job.links[1:]))
        elif self.invalid == "wrong_job_id":
            runs = (replace(runs[0], job_id="another-job"), *runs[1:])
        elif self.invalid == "missing_owned_assignment":
            job = replace(job, assignment_ids=job.assignment_ids[1:])
        elif self.invalid == "contradictory_link":
            job = replace(job, links=(replace(job.links[0], assignment_id=runs[1].assignment_id),
                                      *job.links[1:]))
        return self.api.NativeJobOutput(job=job, runs=runs,
                                       grading_inventory_complete=observed(self.api, True))


def native_study(api, toy_backend, invalid=None):
    study, _, _ = build(api, toy_backend, "plain")
    backend = NativeReceiptBackend(api, toy_backend, invalid)
    config = api.NativeJobConfig(id="native-fixture", backend=backend.ref, candidate_ids=("A", "B"))
    return replace(study, execution=replace(study.execution, native_jobs=(config,))), backend


@pytest.mark.parametrize("invalid", ["unallocated_run_id", "wrong_job_id",
                                      "missing_owned_assignment", "contradictory_link"])
def test_native_job_returns_must_match_allocated_work(api, toy_backend, invalid):
    study, backend = native_study(api, toy_backend, invalid)
    with pytest.raises(api.CaptureValidationError):
        study.run(job_backends={backend.ref.name: backend})
    assert len(backend.calls) == 1, "An invalid receipt cannot trigger a hidden native-job retry"
    assert len(toy_backend.calls) == len(study.plan().assignments)


@pytest.mark.parametrize("inventory_known", [True, False])
def test_rich_import_preserves_jobs_native_grades_raw_projection_and_inventory(
        api, toy_backend, tmp_path, inventory_known):
    study, backend = native_study(api, toy_backend)
    capture = study.run(job_backends={backend.ref.name: backend})
    source = tmp_path / "native-export.json"
    raw = {"native_extension": {"unprojected": ["retain", "exactly"]},
           "run_ids": [run.id for run in reversed(capture.runs)]}
    source.write_text(json.dumps(raw), encoding="utf-8")
    original_bytes = source.read_bytes()
    reference = artifact(api, source)
    projection = api.ProjectionReport(
        mapper=api.VersionRef(name="contract.native-import", revision="v1"),
        source_format=api.VersionRef(name="fixture-export", revision="v1"),
        sources=(reference,), omitted_fields=("native_extension",),
        issues=(api.ValidationIssue(path="native_extension", severity="warning",
                                    message="Kept in the original export; outside common projection"),))
    activity = api.EvaluationActivity(
        id="retained-verification", evaluator=api.VersionRef(name="native.verifier", revision="v1"),
        config_fingerprint=None, run_ids=tuple(run.id for run in capture.runs),
        status="completed", phase="native_verifier", resources=zero_resources(api))
    bundle = api.NativeGradeBundle(
        id="retained-grades", channel="fixture.grade", projection=projection, activities=(activity,),
        grades=tuple(api.NativeGrade(id=f"grade-{index}", run_id=run.id, native_metric="present",
            activity_id=activity.id, value=True, status="ok", basis="observed",
            reason="Fixture grade tests retained transport") for index, run in enumerate(capture.runs)))
    inventory = observed(api, True) if inventory_known else unknown(api, "Exporter omitted grading inventory")

    class FixtureImporter:
        ref = projection.mapper

        def read(self, source, *, plan):
            payload = json.loads(source.read_text(encoding="utf-8"))
            assert plan.assignments == study.plan().assignments
            return api.ImportedCapture(
                runs=tuple(capture.get(run_id) for run_id in payload["run_ids"]),
                native_jobs=capture.native_jobs, native_grades=(bundle,),
                projections=(projection,), grading_inventory_complete=inventory)

    imported = api.RunSet.import_from(source, plan=study.plan(), importer=FixtureImporter())
    imported.validate().raise_for_errors()
    imported.save(tmp_path / "rich-import")
    loaded = api.RunSet.load(tmp_path / "rich-import")
    assert loaded.native_jobs == capture.native_jobs
    assert loaded.native_grades == (bundle,) and loaded.projections == (projection,)
    assert loaded.grading_inventory_complete == inventory
    assert loaded.importer == projection.mapper
    assert loaded.import_source is not None and loaded.import_source == imported.import_source
    assert {run.id for run in loaded.runs} == set(raw["run_ids"])
    for run in loaded.runs:
        assert run.job_id == capture.get(run.id).job_id
        assert run.native_refs == capture.get(run.id).native_refs
    assert source.read_bytes() == original_bytes
    assert len(backend.calls) == 1 and len(toy_backend.calls) == len(study.plan().assignments)


@pytest.mark.parametrize("invalid", ["dangling_job", "duplicate_job",
                                      "contradictory_link", "dangling_activity_run"])
def test_import_boundary_cannot_bypass_native_identity_validation(api, toy_backend, tmp_path, invalid):
    study, backend = native_study(api, toy_backend)
    capture = study.run(job_backends={backend.ref.name: backend})
    source = tmp_path / "malformed-native-export.json"
    source.write_text(json.dumps({"invalid_case": invalid}), encoding="utf-8")

    class InvalidImporter:
        ref = api.VersionRef(name="contract.invalid-import", revision="v1")

        def read(self, source, *, plan):
            assert json.loads(source.read_text(encoding="utf-8"))["invalid_case"] == invalid
            runs, jobs, grades = capture.runs, capture.native_jobs, ()
            if invalid == "dangling_job":
                runs = (replace(runs[0], job_id="missing-native-job"), *runs[1:])
            elif invalid == "duplicate_job":
                jobs = (*jobs, jobs[0])
            elif invalid == "contradictory_link":
                job = jobs[0]
                jobs = (replace(job, links=(replace(job.links[0], assignment_id=runs[1].assignment_id),
                                           *job.links[1:])),)
            else:
                projection = api.ProjectionReport(mapper=self.ref,
                    source_format=api.VersionRef(name="fixture.invalid-native-export", revision="v1"),
                    sources=(artifact(api, source),))
                activity = api.EvaluationActivity(
                    id="grading-an-absent-run", evaluator=self.ref, config_fingerprint=None,
                    run_ids=("run-outside-this-capture",), status="error", phase="native_verifier",
                    resources=zero_resources(api),
                    error=api.ErrorRecord(code="fixture", message="Retained failed invocation"))
                grades = (api.NativeGradeBundle(id="invalid-grade-bundle", channel="fixture",
                           projection=projection, grades=(), activities=(activity,)),)
            return api.ImportedCapture(runs=runs, native_jobs=jobs, native_grades=grades,
                                       grading_inventory_complete=observed(api, True))

    schema_error = importlib.import_module("pydantic").ValidationError
    with pytest.raises((api.CaptureValidationError, schema_error)):
        api.RunSet.import_from(source, plan=study.plan(), importer=InvalidImporter())
    assert len(backend.calls) == 1, "Import validation must never attempt an agent rerun"
    assert len(toy_backend.calls) == len(study.plan().assignments)
