"""Descriptive candidate comparisons over stored summaries."""
from decimal import Decimal, localcontext
import math

from .. import objects as o
from .query import index_result, invalid


def _key(key):
    return tuple(sorted((name, type(value).__name__, value) for name, value in key.items()))


def _number(value):
    return (type(value) is int or type(value) is float and math.isfinite(value)
            or isinstance(value, Decimal) and value.is_finite())


def _delta(baseline, challenger):
    if baseline.status == "unknown" or challenger.status == "unknown":
        return o.Observation(value=None, status="unknown", reason="One or both summary values are unknown")
    if not _number(baseline.value) or not _number(challenger.value):
        return o.Observation(value=None, status="unknown", reason="Numeric deltas require numeric, non-boolean summaries")
    try:
        a, b = baseline.value, challenger.value
        if isinstance(a, Decimal) or isinstance(b, Decimal):
            with localcontext() as ctx:
                ctx.prec = max(34, len(str(a)) + len(str(b)))
                value = float(Decimal(str(b)) - Decimal(str(a)))
        else:
            value = float(b - a)
        if not math.isfinite(value):
            raise OverflowError()
    except (ArithmeticError, ValueError):
        return o.Observation(value=None, status="unknown", reason="Numeric delta overflowed its finite float representation")
    estimated = "estimated" in (baseline.status, challenger.status)
    return o.Observation(value=value, status="estimated" if estimated else "observed",
        reason="Difference includes an estimated summary" if estimated else "Challenger minus baseline")


def configuration_limitations(result, baseline, challenger):
    notes = []
    for candidate_id in (baseline, challenger):
        candidate = result.runs.plan.candidates[candidate_id]
        if candidate.backend.revision is None or any(c.ref.revision is None for c in candidate.components.values()):
            notes.append(f"Candidate {candidate_id} has unresolved declared revisions")
        for run in result.runs.for_candidate(candidate_id):
            for execution in run.executions:
                if execution.effective_config.status != "observed":
                    notes.append(f"Candidate {candidate_id} has {execution.effective_config.status} effective configuration")
    for projection in result.runs.projections:
        notes.extend(f"Mapper {projection.mapper.name}: {issue.message}" for issue in projection.issues)
    notes.append("Declared changes and recorded runtime settings describe this comparison; they do not isolate a causal effect")
    return tuple(dict.fromkeys(notes))


def compare(result, baseline, challenger, *, metrics, confidence=None):
    index = index_result(result)
    if baseline not in result.runs.plan.candidates or challenger not in result.runs.plan.candidates:
        invalid("Both candidate IDs must exist in this result", "candidates")
    metrics = tuple(metrics)
    if not metrics or len(set(metrics)) != len(metrics):
        invalid("Comparison requires nonempty, unique summary IDs", "metrics")
    if confidence is not None and (type(confidence) not in (float, int) or not math.isfinite(confidence) or not 0 < confidence < 1):
        invalid("Confidence must be finite and strictly between zero and one", "confidence")
    rows = []
    notes = list(configuration_limitations(result, baseline, challenger))
    for metric in metrics:
        if (baseline, metric) not in index.summaries or (challenger, metric) not in index.summaries:
            invalid(f"Unknown or unavailable summary: {metric}", "metrics")
        a, b = index.summaries[baseline, metric], index.summaries[challenger, metric]
        interval_note = None
        if confidence is not None:
            interval_note = "No supported recorded interval method exists for this result"
            notes.append(interval_note)
        rows.append(o.ComparisonRow(metric=metric, baseline=a.value, challenger=b.value,
            delta=_delta(a.value, b.value), interval=None, interval_note=interval_note))
        def population(summary):
            return {(_key(index.assignments[index.runs[r].assignment_id].unit),
                     index.assignments[index.runs[r].assignment_id].repetition)
                    for r in summary.included_run_ids}
        if population(a) != population(b):
            notes.append(f"{metric}: aggregate delta is not paired over identical included work")
        for value in (a, b):
            if value.exclusions:
                notes.append(f"{value.candidate_id}/{metric}: included {len(value.included_run_ids)} of {value.planned} planned runs; exclusions {dict(value.exclusions)}")
    planned_a = {_key(r.unit) for r in index.assignments.values() if r.candidate_id == baseline}
    planned_b = {_key(r.unit) for r in index.assignments.values() if r.candidate_id == challenger}
    paired = planned_a & planned_b
    info = result.runs.plan.dataset
    if info.cluster_by:
        clusters = {_key({name: row[name] for name in info.cluster_by}) for row in info.units.rows
                    if _key({name: row[name] for name in info.unit_key}) in paired}
        cluster_count = len(clusters)
    else:
        cluster_count = len(paired)
    return o.Comparison(baseline_id=baseline, challenger_id=challenger,
        changes=result.runs.plan.candidates[baseline].diff(result.runs.plan.candidates[challenger]),
        rows=tuple(rows), task_count=len(paired), cluster_count=cluster_count,
        limitations=tuple(dict.fromkeys(notes)))


compare_candidates = compare
