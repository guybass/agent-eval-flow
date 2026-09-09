"""Evaluate complete captures with identity joins and invocation-level receipts."""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from functools import partial
import inspect
import math
import uuid

import anyio

from .. import objects as o
from ..objects.identity import semantic_fingerprint
from ..objects.values import aggregate_resources, combine_inventory
from .compiler import compile_suite, issue, value_type
from .primitives import measure_run
from .scoring import score_task, score_measurements
from .aggregation import summarize_candidate


def fresh_id(prefix):
    return prefix + "/" + uuid.uuid4().hex


def unknown_resources(scope, reason):
    unknown = o.Observation(value=None, status="unknown", reason=reason)
    return o.Resources(cost_scope=scope, cost_usd=unknown, input_tokens=unknown,
        output_tokens=unknown, human_minutes=unknown)


def error_row(run_id, spec, reason, activity_ids=(), evidence=(), status="error"):
    return o.Measurement(run_id=run_id, metric=spec.id, value=None, status=status,
        basis=None, reason=reason, activity_ids=activity_ids, evidence=evidence)


def typed_key(key):
    if key is None:
        return None
    if not key or any(type(v) not in {str, int} for v in key.values()):
        raise ValueError("Detail keys require nonempty string/integer identities")
    return tuple(sorted((k, type(v).__name__, v) for k, v in key.items()))


def validate_row(row, *, spec, run_id, activities):
    if not isinstance(row, o.Measurement):
        raise ValueError("Expected a Measurement record")
    if row.run_id != run_id or row.metric != spec.id:
        raise ValueError("Measurement belongs to a foreign run or metric")
    if row.status == "ok":
        if row.value is None or row.basis not in {"observed", "estimated"} or value_type(row.value) != spec.output_type:
            raise ValueError("Measurement has wrong strict output type, null value, or invalid basis")
        if isinstance(row.value, float) and not math.isfinite(row.value):
            raise ValueError("Measurement value is nonfinite")
        if isinstance(row.value, Decimal) and not row.value.is_finite():
            raise ValueError("Measurement value is nonfinite")
    elif row.status not in {"missing", "error", "not_applicable"} or row.value is not None or row.basis is not None or not row.reason:
        raise ValueError("Non-ok measurement requires null value/basis and a reason")
    typed_key(row.key)
    if len(set(row.activity_ids)) != len(row.activity_ids):
        raise ValueError("Measurement contains duplicate activity references")
    for activity_id in row.activity_ids:
        if activity_id not in activities or run_id not in activities[activity_id].run_ids:
            raise ValueError("Measurement references an absent or out-of-scope activity")


def valid_resources(resources):
    if not isinstance(resources, o.Resources):
        return False
    required = {"cost_usd": Decimal, "input_tokens": int, "output_tokens": int, "human_minutes": float}
    for field, kind in required.items():
        obs = getattr(resources, field)
        if obs.status == "unknown":
            if obs.value is not None or not obs.reason:
                return False
        elif obs.status not in {"observed", "estimated"} or type(obs.value) is not kind or obs.value < 0:
            return False
        elif isinstance(obs.value, float) and not math.isfinite(obs.value):
            return False
        elif isinstance(obs.value, Decimal) and not obs.value.is_finite():
            return False
    return bool(resources.cost_scope) and len(set(resources.cost_scope)) == len(resources.cost_scope)


def grading_fingerprint(spec, *, dataset_fingerprint, evaluation_cost_scope):
    return semantic_fingerprint("grader-config", {"metric": spec,
        "dataset_fingerprint": dataset_fingerprint, "evaluation_cost_scope": evaluation_cost_scope})


def make_context(dataset, runs, assignment, run, spec, task_measurements, *, evaluation_cost_scope):
    references = {name: tuple(row for row in table.rows
        if all(type(row[k]) is type(v) and row[k] == v for k, v in assignment.unit.items()))
        for name, table in dataset.references.items()}
    return o.EvaluationContext(unit=assignment.unit, inputs=dataset.agent_input(assignment.unit),
        references=references,
        measurements={name: task_measurements[(run.id, name)] for name in spec.depends_on},
        native_job=next((job for job in runs.native_jobs if job.id == run.job_id), None),
        evaluation_cost_scope=evaluation_cost_scope)


def normalize_single_output(output, *, spec, run, activity_id, dependencies, known_activities):
    if not isinstance(output, o.MetricOutput) or not valid_resources(output.evaluation_resources):
        raise ValueError("Expected MetricOutput with valid grading resources")
    if output.task.key is not None:
        raise ValueError("The task measurement must have key=None")
    rows = (output.task, *output.details)
    keys = set()
    normalized = []
    allowed_prior_ids = {a for m in dependencies.values() for a in m.activity_ids}
    for row in rows:
        # Before stamping this call, only known dependency provenance is allowed.
        if any(a not in allowed_prior_ids for a in row.activity_ids):
            raise ValueError("Single callback retained undeclared dependency provenance")
        validate_row(row, spec=spec, run_id=run.id, activities=known_activities)
        key = typed_key(row.key)
        if key in keys:
            raise ValueError("Duplicate task/detail measurement identity")
        keys.add(key)
        normalized.append(replace(row, activity_ids=(*row.activity_ids, activity_id)))
    if any(row.key is None for row in output.details):
        raise ValueError("Detail measurements must have keys")
    return normalized[0], tuple(normalized[1:]), output.evaluation_resources


def activity_matches(activity, request):
    expected = {item.run.id for item in request.items}
    return isinstance(activity, o.EvaluationActivity) and activity.id == request.id and \
        activity.config_fingerprint == request.config_fingerprint and \
        activity.evaluator == request.metric.source.ref and activity.phase == "post_run" and \
        set(activity.run_ids) == expected and len(activity.run_ids) == len(expected) and \
        valid_resources(activity.resources)


def normalize_batch_output(output, *, request, known_activities):
    if not isinstance(output, o.BatchMetricOutput) or not activity_matches(output.activity, request):
        raise ValueError("Batch activity does not match its requested invocation or receipt")
    activities = {**known_activities, request.id: output.activity}
    expected = {item.run.id for item in request.items}
    identities, task_ids, detail_ids = set(), set(), set()
    for row in output.measurements:
        if row.run_id not in expected:
            raise ValueError("Batch output contains a foreign run")
        validate_row(row, spec=request.metric, run_id=row.run_id, activities=activities)
        if request.id not in row.activity_ids:
            raise ValueError("Batch row must reference this invocation")
        identity = (row.run_id, typed_key(row.key))
        if identity in identities:
            raise ValueError("Duplicate batch task/detail measurement identity")
        identities.add(identity)
        (task_ids if row.key is None else detail_ids).add(row.run_id)
    if detail_ids - task_ids:
        raise ValueError("Batch details cannot exist without a task measurement")
    rows = list(output.measurements)
    for item in request.items:
        if item.run.id not in task_ids:
            rows.append(error_row(item.run.id, request.metric, "Batch omitted requested task measurement",
                (request.id,), status="missing"))
    return tuple(rows), output.activity


def select_native_grade(spec, run, bundles):
    source = spec.source
    activities = {a.id: a for b in bundles for a in b.activities}
    matches = []
    for bundle in bundles:
        if bundle.channel != source.channel:
            continue
        for grade in bundle.grades:
            if grade.run_id != run.id or grade.native_metric != source.native_metric:
                continue
            if source.activity_id is not None and grade.activity_id != source.activity_id:
                continue
            if source.evaluator is not None and activities[grade.activity_id].evaluator != source.evaluator:
                continue
            matches.append(grade)
    tasks = [g for g in matches if g.key is None]
    if len(tasks) != 1:
        evidence = tuple(dict.fromkeys(e for g in tasks for e in g.evidence))
        ids = tuple(dict.fromkeys(g.activity_id for g in tasks))
        reason = "No retained native task grade matches selector" if not tasks else "Ambiguous native grades: " + ", ".join(g.id + " (" + g.activity_id + ")" for g in tasks)
        return error_row(run.id, spec, reason, ids, evidence, "missing" if not tasks else "error"), ()
    selected = tasks[0]
    grades = [selected] + [g for g in matches if g.key is not None and g.activity_id == selected.activity_id]
    rows = tuple(o.Measurement(run_id=run.id, metric=spec.id, value=g.value, status=g.status,
        basis=g.basis, reason=g.reason, evidence=g.evidence, key=g.key, activity_ids=(g.activity_id,)) for g in grades)
    try:
        keys = set()
        for row in rows:
            validate_row(row, spec=spec, run_id=run.id, activities=activities)
            key = typed_key(row.key)
            if key in keys:
                raise ValueError("Duplicate retained detail keys")
            keys.add(key)
    except Exception as exc:
        return error_row(run.id, spec, "Invalid retained native grade: " + str(exc),
            (selected.activity_id,), tuple(dict.fromkeys(e for g in grades for e in g.evidence))), ()
    return rows[0], rows[1:]


def validate_capture_dataset(dataset, runs):
    dataset.validate().raise_for_errors()
    runs.validate().raise_for_errors()
    if dataset.fingerprint() != runs.plan.dataset.fingerprint:
        raise o.CaptureCompatibilityError(o.ValidationReport(issues=(issue("dataset", "Dataset fingerprint differs from capture"),)))
    units = {typed_key({k: row[k] for k in dataset.unit_key}) for row in dataset.units.rows}
    if tuple(dataset.unit_key) != tuple(runs.plan.dataset.unit_key) or any(typed_key(a.unit) not in units for a in runs.plan.assignments):
        raise o.CaptureCompatibilityError(o.ValidationReport(issues=(issue("dataset", "Capture unit keys do not match dataset"),)))


async def aevaluate(*, dataset, runs, suite, evaluators, reducers=None, batch_evaluators=None,
                    performed_native_activity_ids=(), native_grading_inventory_complete=None, compiled=None):
    compiled = compiled or compile_suite(suite, evaluators=evaluators, batch_evaluators=batch_evaluators, reducers=reducers)
    validate_capture_dataset(dataset, runs)
    tasks, details, activities = {}, {}, {}
    for bundle in runs.native_grades:
        for activity in bundle.activities:
            if activity.id in activities and activities[activity.id] != activity:
                raise o.CaptureValidationError(o.ValidationReport(issues=(issue("native_grades", "Conflicting retained activity ID"),)))
            activities[activity.id] = activity
    performed = list(performed_native_activity_ids)
    if len(set(performed)) != len(performed) or not set(performed) <= set(activities):
        raise o.CaptureValidationError(o.ValidationReport(issues=(issue("performed_activity_ids", "Performed native IDs must be a distinct retained subset"),)))
    fresh_inventory = native_grading_inventory_complete or o.Observation(value=True, status="observed")
    assignments = {a.id: a for a in runs.plan.assignments}
    runs_by_assignment = {r.assignment_id: r for r in runs.runs}
    ordered_runs = tuple(runs_by_assignment[a.id] for a in runs.plan.assignments)
    for run in ordered_runs:
        for metric_id in compiled.primitive_ids:
            tasks[(run.id, metric_id)] = measure_run(run, metric_id)

    def retain(task, detail_rows):
        tasks[(task.run_id, task.metric)] = task
        details[(task.run_id, task.metric)] = detail_rows

    for metric_id in compiled.metric_order:
        spec = compiled.metrics_by_id[metric_id]
        if isinstance(spec.source, o.NativeGradeSource):
            for run in ordered_runs:
                retain(*select_native_grade(spec, run, runs.native_grades))
            continue
        fingerprint = grading_fingerprint(spec, dataset_fingerprint=dataset.fingerprint(), evaluation_cost_scope=suite.evaluation_cost_scope)
        contexts = {run.id: make_context(dataset, runs, assignments[run.assignment_id], run, spec, tasks,
            evaluation_cost_scope=suite.evaluation_cost_scope) for run in ordered_runs}
        if spec.source.mode == "batch":
            if not ordered_runs:
                continue
            request = o.BatchMetricRequest(id=fresh_id("grading"), config_fingerprint=fingerprint, metric=spec,
                items=tuple(o.EvaluationItem(run=run, context=contexts[run.id]) for run in ordered_runs))
            performed.append(request.id)
            started = datetime.now(timezone.utc)
            output = None
            try:
                output = await compiled.batch_evaluators[spec.source.ref.name].compute_batch(request)
                rows, activity = normalize_batch_output(output, request=request, known_activities=activities)
            except Exception as exc:
                reason = f"Batch evaluator {spec.source.ref.name} failed: {type(exc).__name__}: {exc}"
                salvage = isinstance(output, o.BatchMetricOutput) and activity_matches(output.activity, request)
                resources = output.activity.resources if salvage else unknown_resources(suite.evaluation_cost_scope, reason)
                artifacts = output.activity.artifacts if salvage else {}
                activity = o.EvaluationActivity(id=request.id, evaluator=spec.source.ref, config_fingerprint=fingerprint,
                    run_ids=tuple(r.id for r in ordered_runs), status="error", phase="post_run", resources=resources,
                    started_at=started, ended_at=datetime.now(timezone.utc), artifacts=artifacts,
                    error=o.ErrorRecord(code="evaluator_error", message=reason))
                evidence = tuple(o.EvidenceRef(artifact=a, description="Grading receipt") for a in artifacts.values())
                rows = tuple(error_row(r.id, spec, reason, (request.id,), evidence) for r in ordered_runs)
            activities[activity.id] = activity
            indexed = {run.id: [] for run in ordered_runs}
            for row in rows:
                indexed[row.run_id].append(row)
            for run in ordered_runs:
                task = next(row for row in indexed[run.id] if row.key is None)
                retain(task, tuple(row for row in indexed[run.id] if row.key is not None))
        else:
            callback = compiled.evaluators[spec.source.ref.name]
            for run in ordered_runs:
                activity_id = fresh_id("grading")
                performed.append(activity_id)
                started = datetime.now(timezone.utc)
                output = None
                error = None
                artifacts = {}
                try:
                    # A sync extension runs off-loop; cooperative async callers can cancel.
                    output = await anyio.to_thread.run_sync(partial(callback.compute, spec, contexts[run.id], run))
                    if inspect.isawaitable(output):
                        if inspect.iscoroutine(output):
                            output.close()
                        raise ValueError("Single evaluators must implement synchronous compute")
                    task, detail_rows, resources = normalize_single_output(output, spec=spec, run=run,
                        activity_id=activity_id, dependencies=contexts[run.id].measurements, known_activities=activities)
                except Exception as exc:
                    reason = f"Evaluator {spec.source.ref.name} failed: {type(exc).__name__}: {exc}"
                    resources = output.evaluation_resources if isinstance(output, o.MetricOutput) and valid_resources(output.evaluation_resources) else unknown_resources(suite.evaluation_cost_scope, reason)
                    evidence = output.task.evidence if isinstance(output, o.MetricOutput) and isinstance(output.task, o.Measurement) else ()
                    artifacts = {"receipt." + str(i): e.artifact for i, e in enumerate(evidence)}
                    task, detail_rows = error_row(run.id, spec, reason, (activity_id,), evidence), ()
                    error = o.ErrorRecord(code="evaluator_error", message=reason)
                activity = o.EvaluationActivity(id=activity_id, evaluator=spec.source.ref, config_fingerprint=fingerprint,
                    run_ids=(run.id,), status="error" if error else "completed", phase="post_run", resources=resources,
                    started_at=started, ended_at=datetime.now(timezone.utc), error=error, artifacts=artifacts)
                activities[activity_id] = activity
                retain(task, detail_rows)
    scores = []
    for run in ordered_runs:
        source_rows = {metric: row for (run_id, metric), row in tasks.items() if run_id == run.id}
        score = score_task(suite, run.id, source_rows)
        scores.append(score)
        for row in score_measurements(score, source_rows, requested_ids=compiled.post_score_ids):
            tasks[(run.id, row.metric)] = row
    metric_order = (*compiled.primitive_ids, *compiled.metric_order, *compiled.post_score_ids)
    measurements = tuple(row for run in ordered_runs for metric_id in metric_order
        for row in (tasks[(run.id, metric_id)], *details.get((run.id, metric_id), ())))
    summaries = []
    for candidate_id in runs.plan.candidates:
        own_assignments = tuple(a for a in runs.plan.assignments if a.candidate_id == candidate_id)
        own_ids = {a.id for a in own_assignments}
        own_runs = tuple(r for r in ordered_runs if r.assignment_id in own_ids)
        run_ids = {r.id for r in own_runs}
        summaries.extend(await anyio.to_thread.run_sync(partial(summarize_candidate, compiled, candidate_id,
            own_assignments, own_runs, tuple(m for m in measurements if m.run_id in run_ids),
            tuple(s for s in scores if s.run_id in run_ids))))
    performed_complete = combine_inventory((fresh_inventory, o.Observation(value=True, status="observed")))
    full_inventory = combine_inventory((runs.grading_inventory_complete, performed_complete))
    resources = aggregate_resources(tuple(a.resources for a in activities.values()), inventory_complete=full_inventory,
        cost_scope=suite.evaluation_cost_scope)
    return o.EvaluationResult(id=fresh_id("evaluation"), created_at=datetime.now(timezone.utc), runs=runs,
        suite=suite, suite_fingerprint=suite.fingerprint(), measurements=measurements, task_scores=tuple(scores),
        summaries=tuple(summaries), evaluation_resources=resources, activities=tuple(activities.values()),
        performed_activity_ids=tuple(performed), performed_grading_inventory_complete=performed_complete)


def evaluate(**kwargs):
    from ..pipeline.api import run_sync
    return run_sync(partial(aevaluate, **kwargs))
