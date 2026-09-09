"""Public unified assessment flow: freeze, inspect and execute, then join evidence."""
from uuid import uuid4

import anyio

from agent_eval_flow import objects as o
from agent_eval_flow.execution.capture import observed
from agent_eval_flow.objects.values import combine_inventory, unknown
from agent_eval_flow.objects.identity import freeze_json
from agent_eval_flow.execution.configuration_assessment import collect_configuration
from agent_eval_flow.execution.dispatch_assessment import execute_assessment_behavior
from agent_eval_flow.execution.preflight import PreparedExecution
from agent_eval_flow.execution.runner import RunExecutor
from agent_eval_flow.evaluation.configuration import make_assessment_request, invoke_configuration_check
from agent_eval_flow.evaluation.assessment_projection import project_behavioral_assessments
from agent_eval_flow.results.assessment_selection import (evaluate_requirement_inputs, conjunction,
                                                          check_coverage_complete)
from .api import run_sync
from .assessment_bindings import snapshot_assessment
from .assessment_preflight import prepare_assessment


class AssessmentPipeline:
    """Compose candidate configuration checks with an optional behavioral study.

    Construction only snapshots bindings. Passing saved captures or runs bypasses
    their producers; explicitly selected evaluators still run on retained evidence.
    """
    def __init__(self, *, plan, collectors=None, configuration_evaluators=None,
                 references=None, snapshot_binders=None, backends=None, job_backends=None,
                 evaluators=None, batch_evaluators=None, reducers=None):
        self._plan = plan
        self._bindings = snapshot_assessment(collectors=collectors,
            configuration_evaluators=configuration_evaluators, references=references,
            snapshot_binders=snapshot_binders, backends=backends, job_backends=job_backends,
            evaluators=evaluators, batch_evaluators=batch_evaluators, reducers=reducers)

    @property
    def plan(self):
        return self._plan

    def eval(self, *, runs=None, configuration=None):
        return run_sync(lambda: self.aeval(runs=runs, configuration=configuration))

    async def aeval(self, *, runs=None, configuration=None):
        prepared = prepare_assessment(self._plan, self._bindings, runs, configuration)
        return await coordinate_assessment(prepared)


async def coordinate_assessment(prepared):
    plan = prepared.plan
    limiter = anyio.CapacityLimiter(plan.max_concurrency)
    captures = dict(prepared.configuration or {})
    assessments, activities, coverage = {}, {}, {}
    branches, snapshot_bindings, performed = [], [], []
    behavior_result = None
    cancellation_artifacts, behavior_progress = {}, {}
    behavior_inventory = observed(True, 'No new behavioral grading was requested')
    cells = tuple((cid, check) for check in plan.checks for cid in check.candidate_ids)
    completed = {(cid, check.id): anyio.Event() for cid, check in cells}

    def remember_receipts(exc):
        capture = getattr(exc, 'configuration_capture', None)
        if capture is not None:
            captures[capture.candidate_id] = capture
        activity = getattr(exc, 'assessment_activity', None)
        if activity is not None:
            activities[activity.id] = activity
        for name, artifact in getattr(exc, 'artifacts', {}).items():
            # Callback-local aliases may collide; retain each attributed source.
            prefix = activity.id if activity is not None else (capture.id if capture is not None else 'behavior')
            cancellation_artifacts[prefix + '/' + name] = artifact
        behavior_progress.update(getattr(exc, 'behavior_progress', {}))
        for child in getattr(exc, 'exceptions', ()):
            remember_receipts(child)

    def attach_progress(exc):
        remember_receipts(exc)
        owned = {activity.id: activity for capture in captures.values() for activity in capture.activities}
        owned.update(activities)
        # A partial receipt view is not a valid result or a resumable checkpoint.
        setattr(exc, 'assessment_progress', freeze_json({
            'plan': plan, 'configuration': captures, 'assessments': tuple(assessments.values()),
            'activities': tuple(owned.values()), 'check_coverage': tuple(coverage.values()),
            'artifacts': cancellation_artifacts, 'behavior': behavior_progress,
        }))

    async def collect(cid, spec):
        request = o.ConfigurationCollectRequest(id='collection/' + uuid4().hex,
            activity_id='assessment/' + uuid4().hex, candidate=plan.candidates[cid], spec=spec)
        try:
            async with limiter:
                capture = await collect_configuration(request, prepared.collectors[cid])
        except BaseException as exc:
            remember_receipts(exc)
            raise
        captures[cid] = capture
        performed.extend(o.ActivityRef(namespace='assessment', id=activity.id) for activity in capture.activities)

    if prepared.configuration is None:
        try:
            async with anyio.create_task_group() as tasks:
                for cid, spec in plan.configuration.items():
                    tasks.start_soon(collect, cid, spec)
        except BaseException as exc:
            attach_progress(exc)
            raise
    for cid in plan.configuration:
        capture = captures[cid]
        activities.update({activity.id: activity for activity in capture.activities})
        branches.append(o.BranchOutcome(kind='configuration', candidate_ids=(cid,), status=capture.status,
            reason='Configuration capture retained' if prepared.configuration is not None else 'Configuration collection finished',
            artifacts=capture.artifacts, error=capture.error))

    async def check_candidate(cid, check):
        key = (cid, check.id)
        try:
            for dep in check.depends_on:
                await completed[(cid, dep)].wait()
            dependencies = {dep: assessments[(cid, dep)] for dep in check.depends_on
                            if (cid, dep) in assessments}
            reason = None
            if any((cid, dep) not in assessments or assessments[(cid, dep)].status != 'ok'
                   or not check_coverage_complete(coverage[(cid, dep)]) for dep in check.depends_on):
                reason = 'A required same-candidate check did not produce complete evidence'
            capture = captures[cid]
            if capture.snapshot is None:
                reason = 'Configuration snapshot is unavailable'
            elif not set(check.required_roles) <= {entry.role for entry in capture.snapshot.entries}:
                reason = 'Required configuration evidence roles are unavailable'
            if reason is not None:
                coverage[key] = o.CheckCoverage(candidate_id=cid, check_id=check.id,
                                                status='blocked', reason=reason)
                branches.append(o.BranchOutcome(kind='configuration', candidate_ids=(cid,),
                    status='not_run', reason='Check ' + check.id + ' was blocked: ' + reason))
                return
            request = make_assessment_request(plan, prepared.bindings, check, capture, dependencies)
            async with limiter:
                output = await invoke_configuration_check(request, prepared.evaluators[check.id])
            assessments[key], coverage[key] = output.assessment, output.coverage
            activities[output.activity.id] = output.activity
            performed.append(o.ActivityRef(namespace='assessment', id=output.activity.id))
            outcome_status = output.activity.status
            if output.assessment.status == 'error':
                outcome_status = 'error'
            elif not check_coverage_complete(output.coverage) and outcome_status == 'completed':
                outcome_status = 'partial'
            branches.append(o.BranchOutcome(kind='configuration', candidate_ids=(cid,),
                status=outcome_status, reason='Configuration check ' + check.id + ': ' + output.coverage.status,
                artifacts=output.artifacts, error=output.activity.error))
        except Exception as exc:
            coverage[key] = o.CheckCoverage(candidate_id=cid, check_id=check.id, status='blocked',
                reason='Check request could not be constructed: ' + str(exc))
            branches.append(o.BranchOutcome(kind='configuration', candidate_ids=(cid,), status='error',
                reason='Configuration check preparation failed',
                error=o.ErrorRecord(code=type(exc).__name__, message=str(exc) or type(exc).__name__)))
        except BaseException as exc:
            remember_receipts(exc)
            raise
        finally:
            completed[key].set()

    async def resolve_gates(candidate_ids):
        decisions = []
        for gate in plan.gates:
            for cid in gate.candidate_ids:
                if cid not in candidate_ids:
                    continue
                for requirement in gate.requirements:
                    await completed[(cid, requirement.check_id)].wait()
                requirements = tuple(evaluate_requirement_inputs(plan, tuple(assessments.values()),
                    tuple(coverage.values()), cid, req) for req in gate.requirements)
                decision = conjunction(item.decision for item in requirements)
                decisions.append(o.GateDecision(gate_id=gate.id, candidate_id=cid, decision=decision,
                    allowed=decision == 'pass' or (decision == 'unknown' and gate.on_unknown == 'allow'),
                    requirements=requirements, reason='Explicit launch gate; unknown policy: ' + gate.on_unknown))
        return tuple(decisions)

    async def behavior():
        nonlocal behavior_result, behavior_inventory
        from agent_eval_flow.evaluation.engine import aevaluate
        try:
            source = prepared.behavior.source
            fresh = isinstance(source, PreparedExecution)
            if fresh and plan.configuration:
                captured, bindings, outcomes = await execute_assessment_behavior(source, plan=plan,
                    captures=captures, binders=prepared.binders, resolve_gates=resolve_gates)
                snapshot_bindings.extend(bindings)
                branches.extend(outcomes)
            elif fresh:
                captured = await RunExecutor().execute(source)
            else:
                captured = source
                for cid, capture in captures.items():
                    if cid not in plan.behavior.candidates or capture.snapshot is None:
                        continue
                    runs = captured.for_candidate(cid)
                    snapshot_bindings.append(o.SnapshotBinding(candidate_id=cid,
                        candidate_fingerprint=capture.candidate_fingerprint,
                        snapshot_fingerprint=capture.snapshot.fingerprint, status='unknown',
                        reason='Saved runs supply no historical snapshot-binding proof; no runtime was invoked',
                        run_ids=tuple(run.id for run in runs),
                        job_ids=tuple(dict.fromkeys(run.job_id for run in runs if run.job_id is not None))))
            native_ids = tuple(dict.fromkeys(a.id for b in captured.native_grades for a in b.activities)) if fresh else ()
            inventory = captured.grading_inventory_complete if fresh else observed(True, 'No new native grading was performed')
            bindings = prepared.bindings.behavior
            behavior_result = await aevaluate(dataset=prepared.behavior.dataset, runs=captured,
                suite=plan.behavior.suite, evaluators=bindings.evaluators, reducers=bindings.reducers,
                batch_evaluators=bindings.batch_evaluators, compiled=prepared.behavior.evaluation,
                performed_native_activity_ids=native_ids, native_grading_inventory_complete=inventory)
            behavior_inventory = behavior_result.performed_grading_inventory_complete
            performed.extend(o.ActivityRef(namespace='behavior', id=activity_id)
                             for activity_id in behavior_result.performed_activity_ids)
            if not (fresh and plan.configuration):
                branches.append(o.BranchOutcome(kind='behavior', candidate_ids=tuple(plan.behavior.candidates),
                    status='completed', reason='Behavioral evaluation finished over ' + ('fresh' if fresh else 'saved') + ' runs'))
        except Exception as exc:
            behavior_inventory = unknown('Behavioral branch failed; performed grading inventory is not established')
            branches.append(o.BranchOutcome(kind='behavior', candidate_ids=tuple(plan.behavior.candidates),
                status='error', reason='Behavioral branch failed; independent configuration evidence was retained',
                error=o.ErrorRecord(code=type(exc).__name__, message=str(exc) or type(exc).__name__)))
        except BaseException as exc:
            remember_receipts(exc)
            raise

    try:
        async with anyio.create_task_group() as tasks:
            for cid, check in cells:
                tasks.start_soon(check_candidate, cid, check)
            if prepared.behavior is not None:
                tasks.start_soon(behavior)
    except BaseException as exc:
        attach_progress(exc)
        raise
    projections = () if behavior_result is None else project_behavioral_assessments(behavior_result)
    fresh_ids = {ref.id for ref in performed if ref.namespace == 'assessment'}
    performed_inventory = combine_inventory((behavior_inventory, *(
        activity.inventory_complete for activity in activities.values() if activity.id in fresh_ids)))
    result = o.AssessmentResult(id='assessment-result/' + uuid4().hex, plan=plan,
        configuration={cid: captures[cid] for cid in plan.configuration},
        check_coverage=tuple(coverage[(cid, check.id)] for cid, check in cells),
        assessments=tuple(assessments[(cid, check.id)] for cid, check in cells if (cid, check.id) in assessments) + projections,
        activities=tuple(activities.values()), snapshot_bindings=tuple(snapshot_bindings) if behavior_result is not None else (),
        branches=_branch_summary(plan, branches, behavior_result),
        behavior_result=behavior_result, performed_activity_refs=tuple(performed),
        performed_inventory_complete=performed_inventory)
    report = result.validate()
    if not report.valid:
        raise o.CaptureValidationError(report)
    return result


def _branch_summary(plan, outcomes, behavior_result):
    """One terminal branch cell per candidate; individual check coverage stays separate."""
    rows = []
    kinds = [('configuration', tuple(plan.configuration))]
    if plan.behavior is not None:
        kinds.append(('behavior', tuple(plan.behavior.candidates)))
    for kind, cids in kinds:
        for cid in cids:
            parts = [part for part in outcomes if part.kind == kind and cid in part.candidate_ids]
            statuses = {part.status for part in parts}
            if kind == 'behavior' and behavior_result is None:
                status = 'error'
            elif len(statuses) == 1:
                status = next(iter(statuses))
            else:
                status = 'partial'
            reasons = tuple(dict.fromkeys(part.reason for part in parts))
            errors = [part.error for part in parts if part.error is not None]
            decisions = {}
            for part in parts:
                for decision in part.gate_decisions:
                    if decision.candidate_id == cid:
                        decisions[(decision.gate_id, cid)] = decision
            artifacts = {str(i) + '/' + name: artifact for i, part in enumerate(parts)
                         for name, artifact in part.artifacts.items()}
            rows.append(o.BranchOutcome(kind=kind, candidate_ids=(cid,), status=status,
                reason='; '.join(reasons), artifacts=artifacts,
                error=None if not errors else o.ErrorRecord(code=errors[0].code,
                    message='; '.join(dict.fromkeys(error.message for error in errors)),
                    evidence=tuple(e for error in errors for e in error.evidence)),
                gate_decisions=tuple(decisions.values())))
    return tuple(rows)
