"""Gated dispatch around the existing allocator and behavioral capture buffers."""
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass, replace
from functools import partial

import anyio

from agent_eval_flow import objects as o
from .capture import (CaptureBuffer, JobCaptureBuffer, missing_run, observed, unknown,
                      validate_capture)
from .runner import allocate, _grading_inventory
from .snapshot_binding_assessment import (prepare_bound_dispatch, validate_session, unit_requests)


@dataclass
class _DispatchResult:
    payload: object
    error: object = None
    artifacts: dict = field(default_factory=dict)


def _artifact_receipts(value, prefix='source'):
    """Retain artifact references from typed returns even when their joins fail."""
    found = {}
    def visit(item, path):
        if isinstance(item, o.ArtifactRef):
            found[path] = item
        elif is_dataclass(item) and not isinstance(item, type):
            for member in fields(item):
                visit(getattr(item, member.name), path + '/' + member.name)
        elif isinstance(item, Mapping):
            for key, nested in item.items():
                visit(nested, path + '/' + str(key))
        elif isinstance(item, (list, tuple)):
            for index, nested in enumerate(item):
                visit(nested, path + '/' + str(index))
    visit(value, prefix)
    return found


def not_run(request, reason):
    return replace(missing_run(request.assignment, run_id=request.run_id,
        cost_scope=request.policy.cost_scope, reason=reason), status='not_run', output_state='unavailable',
        execution_inventory_complete=observed(True, 'Dispatch was blocked before any agent invocation'),
        error=o.ErrorRecord(code='DispatchBlocked', message=reason))


def _failed_run(request, recorder, error):
    result = replace(missing_run(request.assignment, run_id=request.run_id,
        cost_scope=request.policy.cost_scope, reason=error.message),
        status='infrastructure_error', error=error, artifacts=recorder.artifacts,
        executions=tuple(recorder.executions.values()), events=tuple(recorder.events.values()))
    if not result.validate().valid:
        # Invalid normalized records never become valid merely because raw evidence exists.
        result = replace(result, executions=(), events=())
    return result


async def _direct(adapter, request, limiter):
    recorder = CaptureBuffer(request)
    returned = None
    try:
        returned = await anyio.to_thread.run_sync(partial(adapter.run, request, recorder=recorder),
                                                 limiter=limiter, abandon_on_cancel=False)
        return _DispatchResult(recorder.finish(returned))
    except o.ConfigurationError:
        raise
    except Exception as exc:
        error = o.ErrorRecord(code=type(exc).__name__, message=str(exc) or type(exc).__name__)
        artifacts = {**_artifact_receipts(returned), **_artifact_receipts(recorder.artifacts, 'receipts')}
        if recorder.state == 'open':
            try:
                return _DispatchResult(recorder.fail(exc), error, artifacts)
            except Exception:
                pass
        return _DispatchResult(_failed_run(request, recorder, error), error, artifacts)
    except BaseException as exc:
        setattr(exc, 'artifacts', {**_artifact_receipts(recorder.artifacts),
                                  **_artifact_receipts(recorder.executions),
                                  **_artifact_receipts(recorder.events)})
        setattr(exc, 'run_receipt', recorder.final)
        raise


async def _native(adapter, request):
    recorder = JobCaptureBuffer(request)
    returned = None
    try:
        returned = await adapter.run_job(request, recorder=recorder)
        return _DispatchResult(recorder.finish(returned))
    except o.ConfigurationError:
        raise
    except Exception as exc:
        error = o.ErrorRecord(code=type(exc).__name__, message=str(exc) or type(exc).__name__)
        receipts = {**_artifact_receipts(returned), **_artifact_receipts(recorder.job, 'job-receipt'),
                    **_artifact_receipts(recorder.runs, 'run-receipts'),
                    **_artifact_receipts(recorder.bundles, 'grade-receipts')}
        if recorder.state == 'open':
            try:
                return _DispatchResult(recorder.fail(exc), error, receipts)
            except Exception:
                pass
        artifacts = recorder.job.artifacts if recorder.job is not None else {}
        retained = []
        links = {link.run_id: link for link in recorder.job.links} if recorder.job else {}
        for item in request.requests:
            run = recorder.runs.get(item.run_id)
            if run is not None and run.validate().valid:
                retained.append(run)
            else:
                retained.append(replace(missing_run(item.assignment, run_id=item.run_id,
                    cost_scope=item.policy.cost_scope, job_id=request.job_id, reason=error.message),
                    error=error, artifacts=run.artifacts if run is not None else {},
                    native_refs=links[item.run_id].native_refs if item.run_id in links else {}))
        job = recorder.job
        if job is None:
            job = o.NativeJobRecord(id=request.job_id, planned_job_id=request.planned_job_id,
                backend=request.config.backend, assignment_ids=tuple(r.assignment.id for r in request.requests),
                status='error', started_at=None, ended_at=None, error=error,
                artifacts=artifacts)
        elif job.status in {'running', 'unknown'}:
            job = replace(job, status='error', ended_at=None, error=error)
        # A valid terminal job receipt is never relabeled failed merely because
        # the subsequent normalized export was invalid.
        output = o.NativeJobOutput(job=job,
            runs=tuple(retained), projections=tuple(recorder.projections),
            native_grades=tuple(recorder.bundles.values()),
            grading_inventory_complete=unknown('Invalid native export; grading inventory is unknown'),
        )
        return _DispatchResult(output, error, receipts)
    except BaseException as exc:
        setattr(exc, 'artifacts', {**_artifact_receipts(recorder.job, 'job-receipt'),
                                  **_artifact_receipts(recorder.runs, 'run-receipts'),
                                  **_artifact_receipts(recorder.bundles, 'grade-receipts')})
        setattr(exc, 'native_job_receipt', recorder.job)
        setattr(exc, 'native_run_receipts', tuple(recorder.runs.values()))
        raise


async def execute_assessment_behavior(prepared, *, plan, captures, binders, resolve_gates):
    """Retain original native-group sequencing and bounded direct concurrency."""
    allocated = allocate(prepared)
    outputs, runs, bindings, branches = [], {}, [], []
    cancellation_receipts = []
    limiter = anyio.CapacityLimiter(prepared.plan.execution.max_concurrency)

    def assembled(extra_runs=None, extra_output=None):
        current = {**runs, **(extra_runs or {})}
        selected_outputs = (*outputs, *((extra_output,) if extra_output is not None else ()))
        ordered = []
        for assignment in prepared.plan.assignments:
            item = allocated.requests[assignment.id]
            ordered.append(current.get(assignment.id) or missing_run(assignment,
                run_id=item.run_id, cost_scope=item.policy.cost_scope,
                reason='This planned assignment has no finalized capture yet'))
        return o.RunSet(id=allocated.run_set_id, plan=prepared.plan, runs=tuple(ordered),
            native_jobs=tuple(output.job for output in selected_outputs),
            native_grades=tuple(bundle for output in selected_outputs for bundle in output.native_grades),
            projections=tuple(projection for output in selected_outputs for projection in output.projections),
            grading_inventory_complete=_grading_inventory(selected_outputs))

    def isolate_invalid(unit, result):
        """Quarantine invalid joins at a unit boundary, retaining healthy units.

        The temporary complete capture supplies original allocated IDs for
        pending assignments; only the final assembled capture is returned.
        """
        native = isinstance(unit, o.NativeJobRequest)
        output = result.payload
        extra = {run.assignment_id: run for run in output.runs} if native else {output.assignment_id: output}
        report = assembled(extra, output if native else None).validate()
        if report.valid:
            return result
        error = o.ErrorRecord(code='CaptureValidationError', message='Invalid dispatch capture: ' + str(report))
        receipts = {**result.artifacts, **_artifact_receipts(output, 'invalid-output')}
        if not native:
            replacement = replace(missing_run(unit.assignment, run_id=unit.run_id,
                cost_scope=unit.policy.cost_scope, reason=error.message), status='infrastructure_error',
                artifacts=output.artifacts, error=error)
            return _DispatchResult(replacement, error, receipts)
        retained_by_id = {run.id: run for run in output.runs}
        linked = {link.run_id: link for link in output.job.links}
        safe_runs = []
        for item in unit.requests:
            old = retained_by_id.get(item.run_id)
            refs = linked[item.run_id].native_refs if item.run_id in linked else (old.native_refs if old else {})
            safe_runs.append(replace(missing_run(item.assignment, run_id=item.run_id,
                cost_scope=item.policy.cost_scope, job_id=unit.job_id, reason=error.message),
                artifacts=old.artifacts if old else {}, native_refs=refs, error=error))
        safe = replace(output, runs=tuple(safe_runs), native_grades=(),
                       grading_inventory_complete=unknown('Invalid native capture leaves grading inventory unknown'))
        # Keep the native terminal receipt intact. Accepted record_job ownership
        # and link checks establish the base; an invalid base remains a real error.
        validate_capture(assembled({run.assignment_id: run for run in safe.runs}, safe))
        for old in output.runs:
            trial_runs = tuple(old if run.id == old.id else run for run in safe.runs)
            trial = replace(safe, runs=trial_runs)
            if assembled({run.assignment_id: run for run in trial.runs}, trial).validate().valid:
                safe = trial
        for bundle in output.native_grades:
            for candidate_bundle in (bundle, replace(bundle, grades=())):
                trial = replace(safe, native_grades=(*safe.native_grades, candidate_bundle))
                if assembled({run.assignment_id: run for run in trial.runs}, trial).validate().valid:
                    safe = trial
                    break
        return _DispatchResult(safe, error, receipts)

    async def dispatch(unit):
        items = unit_requests(unit)
        cids = tuple(dict.fromkeys(item.candidate.id for item in items))
        decisions = await resolve_gates(cids)
        blocked = [decision for decision in decisions if not decision.allowed]
        reason = ('Execution gate blocked complete dispatch unit: ' + ', '.join(
                  decision.gate_id + '/' + decision.candidate_id + '=' + decision.decision
                  for decision in blocked)) if blocked else None
        native = isinstance(unit, o.NativeJobRequest)
        ref = unit.config.backend if native else unit.candidate.backend
        adapter = prepared.job_backends[ref.name] if native else prepared.backends[ref.name]
        output = None
        session = None
        entered = False
        error = None
        bound = None
        receipts = {}
        result = None
        phase = 'snapshot preparation'
        exception_info = (None, None, None)
        try:
            if reason is None:
                bound = await prepare_bound_dispatch(unit, plan=plan, captures=captures,
                                                      binders=binders, delegate=adapter)
                session = bound.session
                if session is not None:
                    active = await session.__aenter__()
                    entered = True
                    bound = validate_session(active, unit, plan=plan, captures=captures)
                bindings.extend(bound.bindings)
                reason = bound.blocked_reason
                if reason is None:
                    phase = 'native dispatch' if native else 'direct dispatch'
                    result = await _native(bound.delegate, unit) if native else await _direct(bound.delegate, unit, limiter)
                    result = isolate_invalid(unit, result)
                    output, error, receipts = result.payload, result.error, result.artifacts
        except Exception as exc:
            import sys
            exception_info = sys.exc_info()
            if isinstance(result, _DispatchResult):
                receipts.update(result.artifacts)
                receipts.update(_artifact_receipts(result.payload, 'invalid-dispatch'))
            error = o.ErrorRecord(code=type(exc).__name__, message=str(exc) or type(exc).__name__)
            reason = phase + ' failed: ' + error.message
        except BaseException as exc:
            import sys
            exception_info = sys.exc_info()
            # Task-group cancellation may replace this child exception with a
            # different parent exception. Save acknowledged receipts locally
            # before cleanup and propagation rather than relying on its attrs.
            cancellation_receipts.append({
                'candidate_ids': cids, 'phase': phase,
                'artifacts': dict(getattr(exc, 'artifacts', {})),
                'native_job_receipt': getattr(exc, 'native_job_receipt', None),
                'native_run_receipts': tuple(getattr(exc, 'native_run_receipts', ())),
                'run_receipt': getattr(exc, 'run_receipt', None),
                'gate_decisions': decisions,
            })
            raise
        finally:
            if session is not None and entered:
                # Cleanup may need to request remote stop. Bound it at the adapter; do
                # not let cancellation prevent acknowledged receipt retention/cleanup.
                try:
                    with anyio.CancelScope(shield=True):
                        await session.__aexit__(*exception_info)
                except Exception as exc:
                    error = o.ErrorRecord(code=type(exc).__name__, message='Snapshot cleanup: ' + str(exc))
        if output is not None:
            # Cleanup awaits may let another unit finalize. Validate against the
            # current sibling inventory immediately before the atomic insertion.
            result = isolate_invalid(unit, _DispatchResult(output, error, receipts))
            output, error, receipts = result.payload, result.error, result.artifacts
        if output is None:
            for item in items:
                if phase == 'snapshot preparation' or (error is not None and error.code == 'ConfigurationError'):
                    runs[item.assignment.id] = not_run(item, reason or 'Dispatch was not started')
                else:
                    runs[item.assignment.id] = replace(missing_run(item.assignment, run_id=item.run_id,
                        cost_scope=item.policy.cost_scope, reason=reason or 'Dispatched work lacks a valid capture'), error=error)
            status = 'error' if phase != 'snapshot preparation' and error is not None else 'not_run'
        elif native:
            outputs.append(output)
            runs.update({run.assignment_id: run for run in output.runs})
            status = 'completed' if output.job.status == 'completed' else 'partial'
            reason = 'Native dispatch completed' if status == 'completed' else 'Native capture contains incomplete work'
        else:
            runs[output.assignment_id] = output
            status = 'completed' if output.status == 'completed' else 'partial'
            reason = 'Behavioral dispatch completed' if status == 'completed' else 'Behavioral capture contains incomplete work'
        branches.append(o.BranchOutcome(kind='behavior', candidate_ids=cids,
            status='error' if error is not None and output is not None else status,
            reason=reason or 'Dispatch completed', error=error, gate_decisions=decisions,
            artifacts={**receipts, **({} if output is None else (output.job.artifacts if native else output.artifacts))}))

    # No native job is subdivided to satisfy a candidate gate. Independent groups
    # still continue once their own gates release, as in the existing executor.
    try:
        for job in allocated.jobs:
            await dispatch(job)
        async with anyio.create_task_group() as tasks:
            for assignment_id in allocated.direct_assignment_ids:
                tasks.start_soon(dispatch, allocated.requests[assignment_id])
    except BaseException as exc:
        # Transient acknowledged progress, not a valid RunSet, completed result
        # or checkpoint. Retain original allocation IDs without inventing runs.
        setattr(exc, 'behavior_progress', {
            'run_set_id': allocated.run_set_id, 'plan': prepared.plan,
            'allocated_run_ids': {aid: request.run_id for aid, request in allocated.requests.items()},
            'allocated_job_ids': {job.planned_job_id: job.job_id for job in allocated.jobs},
            'runs': tuple(runs.values()), 'native_outputs': tuple(outputs),
            'snapshot_bindings': tuple(bindings), 'branches': tuple(branches),
            'cancellation_receipts': tuple(cancellation_receipts),
        })
        raise
    result = assembled()
    return validate_capture(result), tuple(bindings), tuple(branches)
