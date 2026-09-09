"""Inspect captured facts without executing evaluators or fetching evidence."""
from dataclasses import dataclass
from typing import Mapping

from .. import objects as o


def invalid(message: str, path: str = "result") -> None:
    raise o.ValidationError(o.ValidationReport(issues=(o.ValidationIssue(
        path=path, message=message, severity="error"),)))


def unique(rows, key, label):
    result = {}
    for row in rows:
        identity = key(row)
        if identity in result:
            invalid(f"Duplicate {label}: {identity}")
        result[identity] = row
    return result


@dataclass(frozen=True)
class ResultIndex:
    runs: Mapping
    assignments: Mapping
    scores: Mapping
    measurements: Mapping
    summaries: Mapping
    activities: Mapping
    native_jobs: Mapping


def measurement_ids(suite):
    """Requested custom metrics and explicitly referenced built-in helpers."""
    return ({spec.id for spec in suite.metrics}
        | {name for spec in suite.metrics for name in spec.depends_on}
        | {spec.metric for spec in suite.summaries}
        | ({term.metric for term in suite.rubric.terms} if suite.rubric else set())
        | ({gate.metric for gate in suite.acceptance.all_of} if suite.acceptance else set()))


def index_result(result):
    runs = unique(result.runs.runs, lambda r: r.id, "run")
    assignments = unique(result.runs.plan.assignments, lambda r: r.id, "assignment")
    scores = unique(result.task_scores, lambda r: r.run_id, "task score")
    if set(scores) != set(runs):
        invalid("Task scores must cover every run exactly once")
    activities = unique(result.activities, lambda r: r.id, "grading activity")
    measurements = {key: [] for key in runs}
    measurement_keys = set()
    metric_ids = measurement_ids(result.suite)
    for row in result.measurements:
        if row.run_id not in runs or row.metric not in metric_ids:
            invalid(f"Unknown run or metric on measurement: {row.run_id}/{row.metric}")
        key = None if row.key is None else tuple(sorted((k, type(v).__name__, v) for k, v in row.key.items()))
        identity = (row.run_id, row.metric, key)
        if identity in measurement_keys:
            invalid(f"Duplicate measurement: {identity}")
        measurement_keys.add(identity)
        for activity_id in row.activity_ids:
            if activity_id not in activities:
                invalid(f"Dangling activity reference: {activity_id}")
            if row.run_id not in activities[activity_id].run_ids:
                invalid(f"Activity {activity_id} does not cover run {row.run_id}")
        measurements[row.run_id].append(row)
    for activity in activities.values():
        if any(run_id not in runs for run_id in activity.run_ids):
            invalid(f"Activity {activity.id} references an unknown run")
    if len(set(result.performed_activity_ids)) != len(result.performed_activity_ids):
        invalid("Duplicate performed grading activity")
    if any(key not in activities for key in result.performed_activity_ids):
        invalid("Performed grading activity does not exist")
    summaries = unique(result.summaries, lambda r: (r.candidate_id, r.summary_id), "summary")
    summary_ids = {spec.id for spec in result.suite.summaries}
    for row in summaries.values():
        if row.candidate_id not in result.runs.plan.candidates or row.summary_id not in summary_ids:
            invalid(f"Unknown candidate or summary: {row.candidate_id}/{row.summary_id}")
        for run_id in (*row.included_run_ids, *row.exclusions):
            if run_id not in runs or assignments[runs[run_id].assignment_id].candidate_id != row.candidate_id:
                invalid(f"Summary references an unknown or foreign run: {run_id}")
    return ResultIndex(runs=runs, assignments=assignments, scores=scores,
        measurements={k: tuple(v) for k, v in measurements.items()}, summaries=summaries,
        activities=activities,
        native_jobs=unique(result.runs.native_jobs, lambda r: r.id, "native job"))


def collect_run_evidence(index, run_id):
    run = index.runs[run_id]
    refs = []

    def add(values):
        for value in values:
            if value not in refs:
                refs.append(value)

    def artifacts(values):
        add(o.EvidenceRef(artifact=artifact, description=name) for name, artifact in values.items())

    def observation(value):
        if value is not None:
            add(value.evidence)

    def error(value):
        if value is not None:
            add(value.evidence)

    def resources(value):
        for name in ("cost_usd", "input_tokens", "output_tokens", "human_minutes"):
            observation(getattr(value, name))

    artifacts(run.artifacts)
    observation(run.environment)
    observation(run.execution_inventory_complete)
    error(run.error)
    for execution in run.executions:
        observation(execution.effective_config)
        resources(execution.resources)
        error(execution.error)
    for event in run.events:
        if event.source is not None:
            add((event.source,))
        add(event.inputs)
        add(event.outputs)
    for row in index.measurements[run_id]:
        add(row.evidence)
    score = index.scores[run_id]
    observation(score.score)
    for row in score.contributions:
        add(row.evidence)
    activity_ids = {key for row in index.measurements[run_id] for key in row.activity_ids}
    for activity in index.activities.values():
        if activity.id in activity_ids:
            artifacts(activity.artifacts)
            resources(activity.resources)
            error(activity.error)
    if run.job_id is not None and run.job_id in index.native_jobs:
        job = index.native_jobs[run.job_id]
        artifacts(job.artifacts)
        observation(job.effective_config)
        error(job.error)
    return tuple(refs)


def summary(result):
    index_result(result)
    return result.summaries


def explain(result, run_id):
    index = index_result(result)
    if run_id not in index.runs:
        invalid(f"Unknown run ID: {run_id}", "run_id")
    return o.Explanation(run_id=run_id, score=index.scores[run_id],
        measurements=index.measurements[run_id], evidence=collect_run_evidence(index, run_id))


stored_summaries = summary
explain_run = explain
