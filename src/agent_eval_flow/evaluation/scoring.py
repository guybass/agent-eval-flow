"""Optional user-declared rubric and conjunction; missing facts stay unknown."""
import math
import operator
from decimal import Decimal

from .. import objects as o
from .primitives import from_observation

OPS = {"==": operator.eq, "!=": operator.ne, ">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}


def evaluate_threshold(threshold, measurement):
    if measurement is None or measurement.status != "ok":
        return o.GateResult(threshold=threshold, decision="unknown", basis=None,
            reason="Missing threshold source" if measurement is None else measurement.reason)
    left, right = measurement.value, threshold.value
    numeric = type(left) in {int, float, Decimal} and type(right) in {int, float, Decimal}
    if not numeric and not (threshold.op in {"==", "!="} and type(left) is type(right)):
        return o.GateResult(threshold=threshold, decision="unknown", basis=None, reason="Incompatible threshold operands")
    decision = "pass" if OPS[threshold.op](left, right) else "fail"
    return o.GateResult(threshold=threshold, decision=decision, basis=measurement.basis,
        reason=f"{threshold.metric}: {left!r} {threshold.op} {right!r} is {decision}")


def combine_gates(gates):
    failed = [g for g in gates if g.decision == "fail"]
    if failed:
        return "fail", "observed" if any(g.basis == "observed" for g in failed) else "estimated"
    if any(g.decision == "unknown" for g in gates):
        return "unknown", None
    return "pass", "estimated" if any(g.basis == "estimated" for g in gates) else "observed"


def score_task(suite, run_id, measurements):
    contributions = []
    if suite.rubric is None:
        score = o.Observation(value=None, status="unknown", reason="No rubric configured")
    else:
        for term in suite.rubric.terms:
            source = measurements.get(term.metric)
            earned, basis = None, None
            reason = "Missing rubric source"
            evidence = ()
            if source is not None:
                evidence, reason = source.evidence, source.reason
                if source.status == "ok":
                    if type(source.value) in {bool, int, float, Decimal} and 0 <= source.value <= 1:
                        earned, basis = float(source.value) * term.points, source.basis
                    else:
                        reason = "Rubric fraction must be within [0, 1]"
            contributions.append(o.ScoreContribution(metric=term.metric, earned=earned, possible=term.points,
                basis=basis, reason=reason, evidence=evidence))
        evidence = tuple(dict.fromkeys(e for c in contributions for e in c.evidence))
        if any(c.earned is None for c in contributions):
            score = o.Observation(value=None, status="unknown", reason="One or more rubric contributions are unavailable", evidence=evidence)
        else:
            try:
                total = math.fsum(c.earned for c in contributions)
            except OverflowError:
                total = math.inf
            score = o.Observation(value=total if math.isfinite(total) else None,
                status=("estimated" if any(c.basis == "estimated" for c in contributions) else "observed") if math.isfinite(total) else "unknown",
                reason="Sum of configured rubric contributions" if math.isfinite(total) else "Rubric total is nonfinite", evidence=evidence)
    sources = {**measurements, "quality.score": from_observation(run_id, "quality.score", score)}
    gates = tuple(evaluate_threshold(t, sources.get(t.metric)) for t in suite.acceptance.all_of) if suite.acceptance is not None else ()
    acceptance, basis = combine_gates(gates) if suite.acceptance is not None else ("unknown", None)
    return o.TaskScore(run_id=run_id, score=score, contributions=tuple(contributions), acceptance=acceptance,
        acceptance_basis=basis, gates=gates, reason="Applied configured rubric and acceptance rule")


def score_measurements(score, source_measurements, *, requested_ids):
    result = []
    for name in requested_ids:
        required = {c.metric for c in score.contributions} if name == "quality.score" else {g.threshold.metric for g in score.gates}
        if "quality.score" in required:
            required.remove("quality.score")
            required.update(c.metric for c in score.contributions)
        sources = tuple(row for metric, row in source_measurements.items() if metric in required)
        inherited = tuple(dict.fromkeys(a for m in sources for a in m.activity_ids))
        obs = score.score if name == "quality.score" else o.Observation(
            value=None if score.acceptance == "unknown" else score.acceptance == "pass",
            status=score.acceptance_basis or "unknown", reason=score.reason,
            evidence=tuple(dict.fromkeys(e for m in sources for e in m.evidence)))
        result.append(from_observation(score.run_id, name, obs, activity_ids=inherited))
    return tuple(result)
