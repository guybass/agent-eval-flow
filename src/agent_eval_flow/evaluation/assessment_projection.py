"""Source-preserving views of existing behavioral measurements; no grading."""
from datetime import datetime, timezone
from uuid import uuid4

from .. import objects as o
from ..objects import assessment as a
from ..objects.identity import semantic_fingerprint
from ..results.query import index_result, invalid
from .scoring import combine_gates


def project_behavioral_assessments(result):
    index = index_result(result)
    projected = []
    for row in result.measurements:
        run = index.runs[row.run_id]
        assignment = index.assignments[run.assignment_id]
        subject = a.RunSubject(candidate_id=assignment.candidate_id,
            candidate_fingerprint=assignment.candidate_fingerprint,
            run_set_id=result.runs.id, run_id=run.id, assignment_id=assignment.id)
        conclusion = "unknown"
        if row.key is None:
            score = index.scores[run.id]
            gates = tuple(gate for gate in score.gates if gate.threshold.metric == row.metric)
            if row.metric == "task.accepted":
                conclusion = score.acceptance
            elif gates:
                conclusion = combine_gates(gates)[0]
        origin = a.BehaviorOrigin(run_id=row.run_id, metric=row.metric, key=row.key)
        projected.append(a.Assessment(
            id="behavior-" + semantic_fingerprint("assessment-projection", {"result": result.id, "origin": origin}),
            subject=subject, origin=origin, status=row.status, conclusion=conclusion,
            values=(a.AssessmentValue(id=row.metric, value=row.value, status=row.status,
                basis=row.basis, reason=row.reason, evidence=row.evidence),),
            evidence=row.evidence, reason=row.reason,
            activity_refs=tuple(a.ActivityRef(namespace="behavior", id=value) for value in row.activity_ids)))
    return tuple(projected)


def wrap_behavior_result(result, *, plan):
    """Compose an explicit compatible behavior-only plan without invoking work."""
    from ..pipeline.preflight import check_capture
    from ..storage.manifests import validate_result
    plan.validate().raise_for_errors()
    validate_result(result)
    if plan.behavior is None or plan.configuration or plan.checks:
        invalid("Wrapping requires a behavior-only AssessmentPlan", "plan")
    check_capture(plan.behavior, result.runs).raise_for_errors()
    if plan.behavior.suite.fingerprint() != result.suite_fingerprint:
        invalid("Wrapping plan must describe the retained evaluation suite", "plan.behavior.suite")
    wrapped = a.AssessmentResult(id=str(uuid4()), plan=plan,
        assessments=project_behavioral_assessments(result), behavior_result=result,
        branches=(a.BranchOutcome(kind="behavior", candidate_ids=tuple(plan.behavior.candidates),
            status="completed", reason="Explicitly wrapped retained behavioral evidence; no work performed"),),
        performed_activity_refs=(), created_at=datetime.now(timezone.utc))
    wrapped.validate().raise_for_errors()
    return wrapped
