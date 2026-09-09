"""Explicit tri-state configuration requirements and behavioral preferences."""
from dataclasses import dataclass
from decimal import Decimal
import operator
from typing import Mapping

from .. import objects as o
from ..objects import assessment as a
from ..objects.identity import freeze_json
from .query import invalid, index_result
from .selection import (PreparedPreference, prepare_preference, validate_selection_policy,
                        rank_lexicographic, rank_weighted, rank_pareto)

_SEVERITY = {"info": 0, "warning": 1, "error": 2}
_OPS = {"==": operator.eq, "!=": operator.ne, ">": operator.gt,
        ">=": operator.ge, "<": operator.lt, "<=": operator.le}


def conjunction(decisions):
    decisions = tuple(decisions)
    return "fail" if "fail" in decisions else "unknown" if "unknown" in decisions else "pass"


def check_coverage_complete(cell):
    """Complete inventory is insufficient when an explicitly requested rule did not run."""
    if (cell is None or cell.status != "completed" or cell.inventory_complete.status != "observed"
            or cell.inventory_complete.value is not True):
        return False
    if cell.requested_rules is not None:
        return (set(cell.rule_statuses) == set(cell.requested_rules)
                and all(state == "completed" for state in cell.rule_statuses.values()))
    return True


def evaluate_requirement_inputs(plan, assessments, check_coverage, candidate_id, requirement):
    """Evaluate retained cells, including an interim gate before result assembly."""
    if candidate_id not in plan.candidates:
        invalid(f"Unknown candidate: {candidate_id}")
    checks = {check.id: check for check in plan.checks}
    if requirement.check_id not in checks:
        invalid(f"Unknown configuration check: {requirement.check_id}", "requirement.check_id")
    coverage = tuple(item for item in check_coverage
        if item.candidate_id == candidate_id and item.check_id == requirement.check_id)
    if len(coverage) > 1:
        invalid("Duplicate candidate/check coverage")
    rows = tuple(item for item in assessments if item.origin.kind == "configuration"
        and item.origin.check_id == requirement.check_id
        and item.subject.kind == "candidate" and item.subject.candidate_id == candidate_id)
    if len(rows) > 1:
        invalid("Duplicate configuration assessment for candidate/check")

    def decision(state, reason):
        return a.RequirementDecision(requirement=requirement, decision=state,
            assessment_ids=tuple(row.id for row in rows), coverage=coverage, reason=reason)

    if not rows or not coverage:
        return decision("unknown", "Requested assessment or check coverage is unavailable")
    row, cell = rows[0], coverage[0]
    complete = check_coverage_complete(cell)
    if requirement.kind == "no_findings":
        uncertain_tier = False
        for finding in row.findings:
            if _SEVERITY[finding.severity] < _SEVERITY[requirement.min_severity]:
                continue
            if requirement.allowed_tiers is not None:
                if finding.evidence_tier is None:
                    uncertain_tier = True
                    continue
                if finding.evidence_tier not in requirement.allowed_tiers:
                    continue
            return decision("fail", f"Retained finding meets the explicit requirement: {finding.id}")
        if uncertain_tier:
            return decision("unknown", "A relevant finding has no provider tier for the requested filter")
        if not complete or cell.omitted_suppressed_findings is not False or row.status != "ok":
            return decision("unknown", "Complete inspected coverage and suppression inventory are not established")
        return decision("pass", "Complete declared scope has no findings matching the explicit predicate")
    if row.status != "ok":
        return decision("unknown", f"Check assessment is {row.status}: {row.reason}")
    if requirement.kind == "conclusion_pass":
        if row.conclusion == "pass" and not complete:
            return decision("unknown", "Pass conclusion lacks complete requested coverage")
        return decision(row.conclusion, "Uses the check's explicit supported conclusion")
    if requirement.kind == "value":
        if not complete:
            return decision("unknown", "Named value lacks complete requested check coverage")
        values = [value for value in row.values if value.id == requirement.value_id]
        if len(values) > 1:
            invalid("Duplicate assessment value")
        if not values or values[0].status != "ok":
            return decision("unknown", "Named check value is unavailable")
        left, right = values[0].value, requirement.expected_value
        numeric = type(left) in (int, float, Decimal) and type(right) in (int, float, Decimal)
        compatible = numeric or (requirement.op in ("==", "!=") and type(left) is type(right))
        if not compatible:
            return decision("unknown", "Typed threshold operands are incompatible")
        return decision("pass" if _OPS[requirement.op](left, right) else "fail",
                        f"{requirement.value_id}: {left!r} {requirement.op} {right!r}")
    invalid(f"Unsupported configuration requirement: {requirement.kind}")


def evaluate_requirement(result, candidate_id, requirement):
    return evaluate_requirement_inputs(result.plan, result.assessments, result.check_coverage,
                                       candidate_id, requirement)


def evaluate_gate(result, candidate_id, gate):
    if candidate_id not in gate.candidate_ids:
        invalid("Gate does not target this candidate")
    requirements = tuple(evaluate_requirement(result, candidate_id, item) for item in gate.requirements)
    state = conjunction(item.decision for item in requirements)
    return a.GateDecision(gate_id=gate.id, candidate_id=candidate_id, decision=state,
        allowed=state == "pass" or (state == "unknown" and gate.on_unknown == "allow"),
        requirements=requirements, reason=f"Explicit gate: {state}; unknown policy: {gate.on_unknown}")


@dataclass(frozen=True)
class AssessmentSelection:
    result_id: str
    policy: a.AssessmentPolicy
    status: str
    selected_ids: tuple[str, ...]
    rows: tuple[o.SelectionRow, ...]
    requirements: Mapping[str, tuple[a.RequirementDecision, ...]]


def decide_assessments(result, policy):
    result.validate().raise_for_errors()
    requirements, prepared = {}, []
    behavior = result.behavior_result
    summaries = {}
    if policy.behavior_policy is not None and behavior is not None:
        validate_selection_policy(behavior, policy.behavior_policy)
        summaries = index_result(behavior).summaries
    for candidate_id in result.plan.candidates:
        reqs = tuple(evaluate_requirement(result, candidate_id, item)
                     for item in policy.configuration_requirements)
        requirements[candidate_id] = reqs
        config_state = conjunction(item.decision for item in reqs)
        reasons = tuple(item.reason for item in reqs if item.decision != "pass")
        if policy.behavior_policy is None:
            prepared.append(PreparedPreference(candidate_id, config_state, reasons, None))
            continue
        if behavior is None or candidate_id not in behavior.runs.plan.candidates:
            base = PreparedPreference(candidate_id, "unknown", ("Behavioral result is unavailable",), None)
        else:
            base = prepare_preference(candidate_id, policy.behavior_policy, summaries)
        state = conjunction((config_state, base.eligibility))
        prepared.append(PreparedPreference(candidate_id, state, reasons + base.reasons,
                                            base.objective_values if state == "pass" else None))
    if policy.behavior_policy is None:
        rows = tuple(o.SelectionRow(candidate_id=row.candidate_id, eligibility=row.eligibility,
            reasons=row.reasons, rank=None, preference_score=None) for row in prepared)
        selected, status = (), "eligibility_only"
    else:
        mode = policy.behavior_policy.mode
        rows = {"lexicographic": rank_lexicographic, "weighted": rank_weighted,
                "pareto": rank_pareto}[mode](tuple(prepared), policy.behavior_policy.objectives)
        selected = tuple(row.candidate_id for row in rows if row.rank == 1)
        status = "none_eligible" if not selected else "frontier" if mode == "pareto" else "selected" if len(selected) == 1 else "tie"
    return AssessmentSelection(result.id, policy, status, selected, rows, freeze_json(requirements))
