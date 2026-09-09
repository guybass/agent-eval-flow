"""Candidate populations and reducers with explicit unavailable coverage."""
from dataclasses import dataclass
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
import math
from .. import objects as o


@dataclass(frozen=True)
class SummaryInputs:
    assignments: tuple
    runs: tuple
    source_rows: tuple
    planned: int
    observed: int
    missing: int
    errors: int
    not_applicable: int


def make_summary_inputs(spec, assignments, runs, measurements):
    indexed = {m.run_id: m for m in measurements if m.metric == spec.metric and m.key is None}
    rows = tuple(indexed.get(r.id) or o.Measurement(run_id=r.id, metric=spec.metric,
        value=None, status="missing", basis=None, reason="Missing summary source") for r in runs)
    return SummaryInputs(assignments, runs, rows, len(assignments), sum(m.status == "ok" for m in rows),
        sum(m.status == "missing" for m in rows), sum(m.status == "error" for m in rows),
        sum(m.status == "not_applicable" for m in rows))


def counts(inputs):
    return {name: getattr(inputs, name) for name in ("planned", "observed", "missing", "errors", "not_applicable")}


def arithmetic(values, reducer):
    """Stable arithmetic, with decimal precision independent of caller context."""
    if not values:
        return None
    with localcontext() as context:
        context.prec, context.rounding = 34, ROUND_HALF_EVEN
        decimal = isinstance(values[0], Decimal)
        if reducer == "min":
            result = min(values)
        elif reducer == "max":
            result = max(values)
        elif reducer == "p95":
            ordered = sorted(values)
            h = (len(values) - 1) * (Decimal("0.95") if decimal else 0.95)
            low = int(h)
            fraction = h - low
            result = ordered[low] if len(values) == 1 else ordered[low] + fraction * (ordered[min(low + 1, len(values) - 1)] - ordered[low])
        else:
            total = math.fsum(values) if any(type(v) is float for v in values) else sum(values, Decimal(0) if decimal else 0)
            result = total if reducer == "sum" else total / len(values)
        if isinstance(result, Decimal):
            if not result.is_finite():
                raise ValueError("Nonfinite reducer result")
        elif isinstance(result, float) and not math.isfinite(result):
            raise ValueError("Nonfinite reducer result")
        return result


def reduce_builtin(spec, candidate_id, inputs):
    ok = tuple(m for m in inputs.source_rows if m.status == "ok")
    included = tuple(m.run_id for m in ok)
    excluded = {m.run_id: m.reason or m.status for m in inputs.source_rows if m.status != "ok"}
    try:
        available = arithmetic([m.value for m in ok], spec.reducer)
    except (ArithmeticError, ValueError, TypeError) as exc:
        available = None
        reason = "Reducer arithmetic unavailable: " + str(exc)
    else:
        reason = "Complete planned population" if inputs.planned and len(ok) == inputs.planned else "Incomplete or empty planned population"
    known = available is not None and inputs.planned and len(ok) == inputs.planned
    return o.CandidateSummary(candidate_id=candidate_id, summary_id=spec.id,
        value=o.Observation(value=available if known else None,
            status=("estimated" if any(m.basis == "estimated" for m in ok) else "observed") if known else "unknown",
            reason=reason, evidence=tuple(dict.fromkeys(e for m in ok for e in m.evidence))),
        **counts(inputs), included_run_ids=included, exclusions=excluded, reason=reason, available_value=available)


def validate_summary_output(output, *, spec, candidate_id, inputs):
    if not isinstance(output, o.CandidateSummary):
        raise ValueError("Reducer must return CandidateSummary")
    if output.candidate_id != candidate_id or output.summary_id != spec.id:
        raise ValueError("Reducer returned foreign candidate or summary ID")
    if any(getattr(output, key) != value for key, value in counts(inputs).items()):
        raise ValueError("Reducer source counts contradict captured source coverage")
    ids, included, excluded = {r.id for r in inputs.runs}, set(output.included_run_ids), set(output.exclusions)
    if len(included) != len(output.included_run_ids) or included & excluded or included | excluded != ids:
        raise ValueError("Reducer must partition every candidate run exactly once")
    if any(not isinstance(reason, str) or not reason.strip() for reason in output.exclusions.values()):
        raise ValueError("Every exclusion requires a reason")
    if output.value.status == "unknown":
        if output.value.value is not None or not output.value.reason:
            raise ValueError("Unknown reducer value requires a null value and reason")
    elif type(output.value.value) not in {bool, int, float, Decimal, str}:
        raise ValueError("Reducer observation must contain a metric scalar")
    return output


def summarize_candidate(compiled, candidate_id, assignments, runs, measurements, task_scores):
    results = []
    for spec in compiled.suite.summaries:
        inputs = make_summary_inputs(spec, assignments, runs, measurements)
        if isinstance(spec.reducer, str):
            results.append(reduce_builtin(spec, candidate_id, inputs))
            continue
        try:
            output = compiled.reducers[spec.reducer.name].reduce(spec, candidate_id=candidate_id,
                assignments=assignments, runs=runs, measurements=measurements, task_scores=task_scores)
            results.append(validate_summary_output(output, spec=spec, candidate_id=candidate_id, inputs=inputs))
        except Exception as exc:
            reason = f"Reducer {spec.reducer.name} failed: {type(exc).__name__}: {exc}"
            results.append(o.CandidateSummary(candidate_id=candidate_id, summary_id=spec.id,
                value=o.Observation(value=None, status="unknown", reason=reason), **counts(inputs),
                included_run_ids=(), exclusions={r.id: reason for r in runs}, reason=reason, available_value=None))
    return tuple(results)
