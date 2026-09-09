"""Explicit preferences over previously computed candidate summaries."""
from dataclasses import dataclass
from decimal import Context, Decimal, InvalidOperation, localcontext, ROUND_HALF_EVEN
import math
import operator

from .. import objects as o
from .query import index_result, invalid


_OPS = {"==": operator.eq, "!=": operator.ne, ">": operator.gt,
        ">=": operator.ge, "<": operator.lt, "<=": operator.le}


@dataclass(frozen=True)
class PreparedPreference:
    candidate_id: str
    eligibility: str
    reasons: tuple[str, ...]
    objective_values: tuple | None


def _finite(value):
    if type(value) in (bool, int):
        return True
    if isinstance(value, Decimal):
        return value.is_finite()
    return type(value) is float and math.isfinite(value)


def _decimal(value):
    if isinstance(value, Decimal):
        return value
    if type(value) is bool:
        return Decimal(int(value))
    return Decimal(str(value))


def validate_selection_policy(result, policy):
    ids = [row.metric for row in policy.objectives]
    known = {row.id for row in result.suite.summaries}
    if not ids or len(ids) != len(set(ids)):
        invalid("Selection objectives must be nonempty and unique", "policy.objectives")
    if policy.mode not in ("weighted", "lexicographic", "pareto"):
        invalid(f"Unknown selection mode: {policy.mode}", "policy.mode")
    for row in (*policy.objectives, *policy.requirements):
        if row.metric not in known:
            invalid(f"Unknown summary ID: {row.metric}", "policy")
    for row in policy.objectives:
        if not _finite(row.weight) or row.weight <= 0:
            invalid("Objective weights must be finite and positive", "policy.objectives")
        if row.direction not in ("minimize", "maximize"):
            invalid("Unknown objective direction", "policy.objectives")
        if policy.mode == "weighted" and (row.bounds is None or len(row.bounds) != 2
                or not all(_finite(v) for v in row.bounds) or row.bounds[0] >= row.bounds[1]):
            invalid("Weighted objectives require finite fixed bounds with low < high", "policy.objectives")


def prepare_preference(candidate_id, policy, summaries):
    reasons = []
    failed = False
    unknown = False
    for requirement in policy.requirements:
        row = summaries.get((candidate_id, requirement.metric))
        observation = None if row is None else row.value
        if observation is None or observation.status == "unknown":
            unknown = True
            reasons.append(f"Requirement {requirement.metric} is unknown")
        elif observation.status == "estimated" and not policy.allow_estimates:
            unknown = True
            reasons.append(f"Requirement {requirement.metric} is estimated; policy does not allow estimates")
        else:
            try:
                passed = _OPS[requirement.op](observation.value, requirement.value)
            except (TypeError, ValueError, InvalidOperation):
                unknown = True
                reasons.append(f"Requirement {requirement.metric} has incompatible value types")
            else:
                if not passed:
                    failed = True
                    reasons.append(f"Requirement failed: {requirement.metric} {requirement.op} {requirement.value}")
    values = []
    for objective in policy.objectives:
        row = summaries.get((candidate_id, objective.metric))
        observation = None if row is None else row.value
        if observation is None or observation.status == "unknown":
            unknown = True
            reasons.append(f"Objective {objective.metric} is unknown")
        elif observation.status == "estimated" and not policy.allow_estimates:
            unknown = True
            reasons.append(f"Objective {objective.metric} is estimated; policy does not allow estimates")
        elif type(observation.value) is str:
            invalid(f"Objective {objective.metric} is text; numeric ranking requires numeric summaries", "policy.objectives")
        elif not _finite(observation.value):
            unknown = True
            reasons.append(f"Objective {objective.metric} is not a finite numeric value")
        else:
            values.append(observation.value)
    eligibility = "fail" if failed else "unknown" if unknown else "pass"
    return PreparedPreference(candidate_id=candidate_id, eligibility=eligibility,
        reasons=tuple(reasons), objective_values=tuple(values) if eligibility == "pass" else None)


def _row(item, *, rank=None, preference_score=None, extra_reason=None):
    return o.SelectionRow(candidate_id=item.candidate_id, eligibility=item.eligibility,
        reasons=item.reasons + ((extra_reason,) if extra_reason else ()),
        preference_score=preference_score, rank=rank)


def rank_lexicographic(rows, objectives):
    # Decimal comparisons are exact across int/Decimal/finite float and do not
    # depend on arithmetic precision; negation of huge Decimals would round.
    from functools import cmp_to_key
    def compare(a, b):
        for av, bv, objective in zip(a.objective_values, b.objective_values, objectives):
            if av != bv:
                better = av > bv if objective.direction == "maximize" else av < bv
                return -1 if better else 1
        return 0
    ranked = sorted((row for row in rows if row.eligibility == "pass"), key=cmp_to_key(compare))
    ranks = {}
    rank, previous = 0, None
    for item in ranked:
        if previous is None or compare(previous, item) != 0:
            rank += 1
        ranks[item.candidate_id] = rank
        previous = item
    return tuple(_row(item, rank=ranks.get(item.candidate_id)) for item in rows)


def rank_weighted(rows, objectives):
    scores = {}
    failed = set()
    for row in rows:
        if row.eligibility != "pass":
            continue
        try:
            with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
                weights = tuple(_decimal(objective.weight) for objective in objectives)
                weight_sum = sum(weights, Decimal(0))
                total = Decimal(0)
                for value, objective, weight in zip(row.objective_values, objectives, weights):
                    lo, hi = (_decimal(bound) for bound in objective.bounds)
                    z = max(Decimal(0), min(Decimal(1), (_decimal(value) - lo) / (hi - lo)))
                    total += (z if objective.direction == "maximize" else 1 - z) * weight / weight_sum
                score = float(total)
                if not math.isfinite(score):
                    raise ArithmeticError("Nonfinite utility")
                scores[row.candidate_id] = score
        except (ArithmeticError, ValueError):
            failed.add(row.candidate_id)
    dense = {score: index + 1 for index, score in enumerate(sorted(set(scores.values()), reverse=True))}
    output = []
    for row in rows:
        if row.candidate_id in failed:
            output.append(o.SelectionRow(candidate_id=row.candidate_id, eligibility="unknown",
                reasons=row.reasons + ("Weighted arithmetic could not produce a finite preference",),
                preference_score=None, rank=None))
        else:
            score = scores.get(row.candidate_id)
            output.append(_row(row, rank=None if score is None else dense[score], preference_score=score))
    return tuple(output)


def rank_pareto(rows, objectives):
    eligible = [row for row in rows if row.eligibility == "pass"]
    def dominates(a, b):
        improved = False
        for av, bv, objective in zip(a.objective_values, b.objective_values, objectives):
            if av == bv:
                continue
            if (av < bv if objective.direction == "maximize" else av > bv):
                return False
            improved = True
        return improved
    frontier = {row.candidate_id for row in eligible
                if not any(dominates(other, row) for other in eligible if other is not row)}
    return tuple(_row(row, rank=1 if row.candidate_id in frontier else None) for row in rows)


def select(result, policy):
    index = index_result(result)
    validate_selection_policy(result, policy)
    prepared = tuple(prepare_preference(candidate_id, policy, index.summaries)
                     for candidate_id in result.runs.plan.candidates)
    rows = {"lexicographic": rank_lexicographic, "weighted": rank_weighted,
            "pareto": rank_pareto}[policy.mode](prepared, policy.objectives)
    selected = tuple(row.candidate_id for row in rows if row.rank == 1)
    status = "none_eligible" if not selected else "frontier" if policy.mode == "pareto" else "selected" if len(selected) == 1 else "tie"
    return o.Selection(result_id=result.id, policy=policy, status=status, selected_ids=selected, rows=rows)


select_candidates = select
