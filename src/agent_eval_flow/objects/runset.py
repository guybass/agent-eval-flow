"""Relational capture validation and read-only capture views.

Execution status, output availability and inventory completeness remain separate
facts. Validation rejects contradictory identity graphs, never a low agent score.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from decimal import Decimal
import math

from .identity import freeze_json, typed_key
from .records import Coverage, ValidationIssue, ValidationReport


def _issue(issues, path, message):
    issues.append(ValidationIssue(path=path, message=message, severity="error"))


def _index(items, issues, path):
    result = {}
    for index, item in enumerate(items):
        if item.id in result:
            _issue(issues, f"{path}/{index}/id", f"Duplicate ID {item.id!r}")
        result[item.id] = item
    return result


def _observation(value, issues, path, expected=None):
    if value.status == "unknown":
        if value.value is not None or not value.reason:
            _issue(issues, path, "Unknown observation requires null value and a reason")
    elif value.value is None:
        _issue(issues, path, "Known observation requires a non-null value")
    elif expected is bool and type(value.value) is not bool:
        _issue(issues, path, "Inventory observation must be a strict boolean")
    if value.status == "estimated" and not value.reason:
        _issue(issues, path, "Estimated observation requires a reason")


def _times(value, issues, path):
    for field in ("started_at", "ended_at"):
        timestamp = getattr(value, field, None)
        if timestamp is not None and (not isinstance(timestamp, datetime)
                or timestamp.tzinfo is None or timestamp.utcoffset() is None):
            _issue(issues, path + "/" + field, "Timestamp must be timezone-aware")
    start, end = getattr(value, "started_at", None), getattr(value, "ended_at", None)
    if start is not None and end is not None:
        try:
            if end < start:
                _issue(issues, path + "/ended_at", "End precedes start")
        except TypeError:
            _issue(issues, path, "Timestamps are not comparable")


def _resources(value, issues, path):
    if not value.cost_scope or len(set(value.cost_scope)) != len(value.cost_scope):
        _issue(issues, path + "/cost_scope", "Cost categories must be nonempty and distinct")
    for field, expected in (("cost_usd", Decimal), ("input_tokens", int),
                            ("output_tokens", int), ("human_minutes", float)):
        observation = getattr(value, field)
        _observation(observation, issues, path + "/" + field)
        quantity = observation.value
        if quantity is None:
            continue
        types = (int, float) if expected is float else (expected,)
        if type(quantity) not in types:
            _issue(issues, path + "/" + field, "Resource quantity has the wrong strict numeric type")
            continue
        finite = (quantity.is_finite() if isinstance(quantity, Decimal)
                  else True if type(quantity) is int else math.isfinite(quantity))
        if not finite or quantity < 0:
            _issue(issues, path + "/" + field, "Resource quantity must be finite and nonnegative")


def validate_run(run):
    issues = []
    _times(run, issues, "")
    _observation(run.environment, issues, "/environment")
    _observation(run.execution_inventory_complete, issues, "/execution_inventory_complete", bool)
    if run.output_state != "available":
        if run.output is not None:
            _issue(issues, "/output", "Non-null output must have available capture state")
        if run.output_sources:
            _issue(issues, "/output_sources", "Absent/unknown output has no delivered-output source")
    executions = _index(run.executions, issues, "/executions")
    events = _index(run.events, issues, "/events")
    for index, execution in enumerate(run.executions):
        path = f"/executions/{index}"
        _times(execution, issues, path)
        _observation(execution.effective_config, issues, path + "/effective_config")
        _resources(execution.resources, issues, path + "/resources")
        if execution.resources.cost_scope != run.cost_scope:
            _issue(issues, path + "/resources/cost_scope", "Execution resource scope differs from run")
        if type(execution.retry_index) is not int or execution.retry_index < 0:
            _issue(issues, path + "/retry_index", "Retry index must be a nonnegative strict integer")
        if execution.parent_id is not None and execution.parent_id not in executions:
            _issue(issues, path + "/parent_id", f"Unknown parent {execution.parent_id!r}")
        visited, current = set(), execution.id
        while current is not None and current in executions:
            if current in visited:
                _issue(issues, path + "/parent_id", f"Cyclic execution parent graph involving {current!r}")
                break
            visited.add(current)
            current = executions[current].parent_id
    for index, event in enumerate(run.events):
        path = f"/events/{index}"
        if event.execution_id not in executions:
            _issue(issues, path + "/execution_id", f"Unknown execution {event.execution_id!r}")
        if event.at is not None and (event.at.tzinfo is None or event.at.utcoffset() is None):
            _issue(issues, path + "/at", "Event timestamp must be timezone-aware")
    if len(run.output_sources) != len(set(run.output_sources)):
        _issue(issues, "/output_sources", "Output sources must be distinct")
    for source in run.output_sources:
        if source not in executions:
            _issue(issues, "/output_sources", f"Unknown producing execution {source!r}")
    if run.status in {"not_run", "unobserved"}:
        if run.executions or events or run.output_sources:
            _issue(issues, "/status", "Unstarted/unobserved work cannot include captured execution records")
    return ValidationReport(issues=tuple(issues))


def _validate_native_grading(capture, run_ids, issues):
    _index(capture.native_grades, issues, "/native_grades")
    activities, grade_ids, task_keys, detail_keys = {}, set(), set(), set()
    for bundle_index, bundle in enumerate(capture.native_grades):
        path = f"/native_grades/{bundle_index}"
        _index(bundle.activities, issues, path + "/activities")
        for index, activity in enumerate(bundle.activities):
            activity_path = path + f"/activities/{index}"
            if activity.id in activities and activities[activity.id] != activity:
                _issue(issues, activity_path + "/id", f"Conflicting grading activity {activity.id!r}")
            activities[activity.id] = activity
            _times(activity, issues, activity_path)
            _resources(activity.resources, issues, activity_path + "/resources")
            if len(activity.run_ids) != len(set(activity.run_ids)):
                _issue(issues, activity_path + "/run_ids", "Activity run IDs must be distinct")
            for run_id in activity.run_ids:
                if run_id not in run_ids:
                    _issue(issues, activity_path + "/run_ids", f"Unknown activity run {run_id!r}")
    for bundle_index, bundle in enumerate(capture.native_grades):
        path = f"/native_grades/{bundle_index}"
        for index, grade in enumerate(bundle.grades):
            grade_path = path + f"/grades/{index}"
            if grade.id in grade_ids:
                _issue(issues, grade_path + "/id", f"Duplicate native grade ID {grade.id!r}")
            grade_ids.add(grade.id)
            if grade.run_id not in run_ids:
                _issue(issues, grade_path + "/run_id", "Grade run is outside this capture")
            activity = activities.get(grade.activity_id)
            if activity is None:
                _issue(issues, grade_path + "/activity_id", f"Unknown grading activity {grade.activity_id!r}")
            elif grade.run_id not in activity.run_ids:
                _issue(issues, grade_path + "/run_id", "Grade is outside its activity's run scope")
            if not grade.reason:
                _issue(issues, grade_path + "/reason", "Grade requires an explanation")
            if grade.status == "ok":
                if grade.value is None or grade.basis not in {"observed", "estimated"}:
                    _issue(issues, grade_path, "Ok grade requires non-null value and basis")
                elif isinstance(grade.value, Decimal) and not grade.value.is_finite():
                    _issue(issues, grade_path + "/value", "Grade value must be finite")
                elif isinstance(grade.value, float) and not math.isfinite(grade.value):
                    _issue(issues, grade_path + "/value", "Grade value must be finite")
            elif grade.value is not None or grade.basis is not None:
                _issue(issues, grade_path, "Non-ok grades require null value and basis")
            root = (bundle.channel, grade.run_id, grade.native_metric, grade.activity_id)
            if grade.key is None:
                if root in task_keys:
                    _issue(issues, grade_path, "Duplicate task grade for channel/run/metric/activity")
                task_keys.add(root)
            else:
                try:
                    columns = tuple(sorted(grade.key))
                    key = (root, tuple(zip(columns, typed_key(grade.key, columns))))
                    if key in detail_keys:
                        _issue(issues, grade_path + "/key", "Duplicate native detail key")
                    detail_keys.add(key)
                except (ValueError, TypeError, KeyError) as exc:
                    _issue(issues, grade_path + "/key", str(exc))
    for root, _ in detail_keys:
        if root not in task_keys:
            _issue(issues, "/native_grades", "Native detail grade requires its matching task grade")


def validate_runset(capture):
    issues = []
    plan_report = capture.plan.validate()
    issues.extend(ValidationIssue(path="/plan" + issue.path, message=issue.message, severity=issue.severity)
                  for issue in plan_report.issues)
    _observation(capture.grading_inventory_complete, issues, "/grading_inventory_complete", bool)
    assignments = _index(capture.plan.assignments, issues, "/plan/assignments")
    runs = _index(capture.runs, issues, "/runs")
    assigned = Counter(run.assignment_id for run in capture.runs)
    if set(assigned) != set(assignments):
        _issue(issues, "/runs", "Capture must contain every planned assignment and no foreign assignments")
    if any(count != 1 for count in assigned.values()):
        _issue(issues, "/runs", "Exactly one final run is required per assignment")
    execution_ids, event_ids = set(), set()
    for index, run in enumerate(capture.runs):
        path = f"/runs/{index}"
        for issue in validate_run(run).issues:
            issues.append(ValidationIssue(path=path + issue.path, message=issue.message, severity=issue.severity))
        if run.cost_scope != capture.plan.execution.cost_scope:
            _issue(issues, path + "/cost_scope", "Run cost scope differs from plan")
        for field, records, seen in (("executions", run.executions, execution_ids),
                                     ("events", run.events, event_ids)):
            for record in records:
                if record.id in seen:
                    _issue(issues, path + "/" + field, f"Captured ID {record.id!r} is not unique")
                seen.add(record.id)
    jobs = _index(capture.native_jobs, issues, "/native_jobs")
    planned_jobs = {job.id: job for job in capture.plan.native_jobs}
    planned_seen, job_assignment_owners = set(), {}
    for index, job in enumerate(capture.native_jobs):
        path = f"/native_jobs/{index}"
        _times(job, issues, path)
        planned = planned_jobs.get(job.planned_job_id)
        if planned is None:
            _issue(issues, path + "/planned_job_id", "Unknown planned native job")
        else:
            if job.planned_job_id in planned_seen:
                _issue(issues, path + "/planned_job_id", "Multiple invocations claim one planned native job")
            planned_seen.add(job.planned_job_id)
            if job.backend != planned.config.backend:
                _issue(issues, path + "/backend", "Native job backend differs from planned configuration")
            if (set(job.assignment_ids) != set(planned.assignment_ids)
                    or len(job.assignment_ids) != len(planned.assignment_ids)):
                _issue(issues, path + "/assignment_ids", "Native job must retain the complete planned assignment list")
        for assignment_id in job.assignment_ids:
            if assignment_id not in assignments:
                _issue(issues, path + "/assignment_ids", f"Unknown assignment {assignment_id!r}")
            if assignment_id in job_assignment_owners:
                _issue(issues, path + "/assignment_ids", "Assignment belongs to multiple captured native jobs")
            job_assignment_owners[assignment_id] = job.id
        linked_runs, linked_assignments = set(), set()
        for link in job.links:
            if link.run_id in linked_runs or link.assignment_id in linked_assignments:
                _issue(issues, path + "/links", "Native links must be unique by run and assignment")
            linked_runs.add(link.run_id)
            linked_assignments.add(link.assignment_id)
            run = runs.get(link.run_id)
            if run is None:
                _issue(issues, path + "/links", f"Link refers to unknown run {link.run_id!r}")
            elif (run.assignment_id != link.assignment_id or run.job_id != job.id
                  or run.native_refs != link.native_refs):
                _issue(issues, path + "/links", "Native link contradicts its captured run")
            if link.assignment_id not in job.assignment_ids:
                _issue(issues, path + "/links", "Native link is outside job assignment scope")
    for index, run in enumerate(capture.runs):
        if run.job_id is not None:
            if run.job_id not in jobs:
                _issue(issues, f"/runs/{index}/job_id", "Run refers to unknown native job")
            elif run.assignment_id not in jobs[run.job_id].assignment_ids:
                _issue(issues, f"/runs/{index}/job_id", "Run is outside its native job's assignments")
        owner = job_assignment_owners.get(run.assignment_id)
        if owner is not None and run.job_id != owner:
            _issue(issues, f"/runs/{index}/job_id", "Run omits or contradicts retained native job ownership")
    _validate_native_grading(capture, set(runs), issues)
    return ValidationReport(issues=tuple(issues))


def coverage(capture, candidate_id=None):
    capture.validate().raise_for_errors()
    selected = capture.runs if candidate_id is None else capture.for_candidate(candidate_id)
    counts = Counter(run.status for run in selected)
    unknown = 0
    for run in selected:
        resources = run.resources()
        if any(getattr(resources, field).status == "unknown"
               for field in ("cost_usd", "input_tokens", "output_tokens", "human_minutes")):
            unknown += 1
    return Coverage(
        planned=len(selected), completed=counts["completed"],
        failed=sum(counts[status] for status in ("agent_error", "timed_out", "infrastructure_error", "cancelled")),
        unavailable=counts["not_run"] + counts["unobserved"],
        pending=sum(counts[status] for status in ("running", "awaiting_input", "paused")),
        resource_unknown=unknown,
    )


def _json(value):
    if is_dataclass(value):
        return {field.name: _json(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return tuple(_json(item) for item in value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def rows(capture, kind):
    """Detached JSON-compatible rows with explicit task/candidate/run join keys.

Decimal resource amounts stay lossless strings and retain their observation
state. The public records, rather than these convenience rows, are the typed
round-trip representation.
"""
    if kind not in {"runs", "executions", "events"}:
        raise ValueError("kind must be 'runs', 'executions' or 'events'")
    assignments = {assignment.id: assignment for assignment in capture.plan.assignments}
    result = []
    for run in capture.runs:
        assignment = assignments[run.assignment_id]
        common = {"run_id": run.id, "assignment_id": assignment.id,
                  "candidate_id": assignment.candidate_id, "unit": _json(assignment.unit),
                  "repetition": assignment.repetition}
        records = (run,) if kind == "runs" else getattr(run, kind)
        result.extend(freeze_json({**_json(record), **common}) for record in records)
    return tuple(result)
