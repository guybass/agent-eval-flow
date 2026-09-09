"""Descriptive comparison only across compatible candidate-check evidence."""
from decimal import Decimal
import math

from ..objects.identity import canonical_bytes, freeze_json
from .assessment_query import explain_candidate_assessments
from .assessment_selection import check_coverage_complete
from .query import invalid


def _complete(cell):
    return check_coverage_complete(cell) and cell.omitted_suppressed_findings is False


def compare_candidate_assessments(result, baseline, challenger, *, check_ids):
    result.validate().raise_for_errors()
    left = explain_candidate_assessments(result, baseline)
    right = explain_candidate_assessments(result, challenger)
    if not check_ids or len(set(check_ids)) != len(check_ids):
        invalid("Comparison requires distinct nonempty check IDs", "check_ids")
    checks = {check.id: check for check in result.plan.checks}
    rows = []
    for check_id in check_ids:
        if check_id not in checks:
            invalid(f"Unknown check: {check_id}", "check_ids")
        sides, coverage, limitations = [], [], []
        for candidate_id, view in ((baseline, left), (challenger, right)):
            sides.append(next((item for item in view["configuration_assessments"]
                               if item.origin.check_id == check_id), None))
            coverage.append(next((item for item in view["coverage"] if item.check_id == check_id), None))
            if sides[-1] is None or not _complete(coverage[-1]):
                limitations.append(f"{candidate_id}: complete check and suppression coverage is unavailable")
        if all(sides) and (sides[0].origin.check_fingerprint != sides[1].origin.check_fingerprint
                           or sides[0].origin.evaluator != sides[1].origin.evaluator):
            limitations.append("Check definitions/evaluator versions differ")
        captures = [left["capture"], right["capture"]]
        if any(capture is None or capture.snapshot is None for capture in captures):
            limitations.append("Configuration snapshot evidence is unavailable")
        else:
            snaps = [capture.snapshot for capture in captures]
            scopes = [{(entry.source_tool, entry.scope, entry.role) for entry in snap.entries} for snap in snaps]
            if (snaps[0].collector != snaps[1].collector or scopes[0] != scopes[1]
                    or snaps[0].params_fingerprint != snaps[1].params_fingerprint):
                limitations.append("Collector versions, parameters or inspected source scopes differ")
            if any(snap.inventory_complete.status != "observed" or snap.inventory_complete.value is not True for snap in snaps):
                limitations.append("Snapshot inventory is incomplete")
            if canonical_bytes(snaps[0].context) != canonical_bytes(snaps[1].context):
                limitations.append("Captured discovery contexts differ")
        comparable = not limitations
        values = []
        if all(sides):
            right_values = {item.id: item for item in sides[1].values}
            for lv in sides[0].values:
                rv = right_values.get(lv.id)
                delta, note = None, "Unavailable or incompatible values"
                if (comparable and rv is not None and lv.status == rv.status == "ok"
                        and lv.unit == rv.unit and type(lv.value) in (int, float, Decimal)
                        and type(rv.value) in (int, float, Decimal)):
                    try:
                        delta = rv.value - lv.value
                        note = "Descriptive challenger minus baseline"
                        if (isinstance(delta, Decimal) and not delta.is_finite()) or (type(delta) is float and not math.isfinite(delta)):
                            delta, note = None, "Numeric delta is not finite"
                    except (TypeError, ArithmeticError):
                        note = "Numeric representations cannot be subtracted without coercion"
                values.append({"id": lv.id, "baseline": lv, "challenger": rv, "delta": delta, "reason": note})
        rows.append({"check_id": check_id, "baseline": sides[0], "challenger": sides[1],
            "coverage": tuple(coverage), "comparable": comparable, "limitations": tuple(limitations),
            "values": tuple(values)})
    return freeze_json({"result_id": result.id, "baseline": baseline, "challenger": challenger,
        "changes": result.plan.candidates[baseline].diff(result.plan.candidates[challenger]),
        "rows": tuple(rows), "snapshot_bindings": result.snapshot_bindings,
        "limitations": ("Configuration findings are candidate observations, not task trials.",
                        "Descriptive comparisons do not establish causation or a confidence interval.")})
