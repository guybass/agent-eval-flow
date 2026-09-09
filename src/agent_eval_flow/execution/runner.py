"""Fresh allocation, sequential native groups and bounded direct dispatch."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from functools import partial
from types import MappingProxyType
from uuid import uuid4

import anyio
from pydantic import ValidationError as SchemaValidationError

from agent_eval_flow.objects import (
    CaptureValidationError, ConfigurationError, NativeJobRequest, RunRequest,
    BackendAdapter, NativeJobAdapter, NativeJobOutput, Run, RunSet, VerifierInput,
)
from agent_eval_flow.objects.identity import typed_key
from .capture import (
    CaptureBuffer, JobCaptureBuffer, capture_error, observed, unknown, validate_capture,
)
from .preflight import PreparedExecution, prepare_execution


@dataclass(frozen=True)
class AllocatedExecution:
    run_set_id: str
    requests: Mapping[str, RunRequest]
    jobs: tuple[NativeJobRequest, ...]
    direct_assignment_ids: tuple[str, ...]


def verifier_inputs(dataset, config, requests):
    projected = []
    for request in requests:
        key = typed_key(request.assignment.unit, dataset.unit_key)
        for verifier in config.verifiers:
            references = {}
            for table_name, selected in verifier.reference_columns.items():
                table = dataset.references[table_name]
                columns = tuple(dict.fromkeys((*table.key, *selected)))
                references[table_name] = tuple(
                    {column: row[column] for column in columns}
                    for row in table.rows if typed_key(row, dataset.unit_key) == key
                )
            projected.append(VerifierInput(run_id=request.run_id,
                                           verifier_id=verifier.id, references=references))
    return tuple(projected)


def allocate(prepared: PreparedExecution) -> AllocatedExecution:
    plan = prepared.plan
    run_set_id = str(uuid4())
    requests = {assignment.id: RunRequest(
        run_id=str(uuid4()), assignment=assignment,
        candidate=plan.candidates[assignment.candidate_id],
        input=prepared.dataset.agent_input(assignment.unit),
        policy=plan.execution, environment=plan.environment,
    ) for assignment in plan.assignments}
    jobs, covered = [], set()
    for planned in plan.native_jobs:
        items = tuple(requests[assignment_id] for assignment_id in planned.assignment_ids)
        jobs.append(NativeJobRequest(
            job_id=str(uuid4()), run_set_id=run_set_id, planned_job_id=planned.id,
            config=planned.config, requests=items,
            verifier_inputs=verifier_inputs(prepared.dataset, planned.config, items),
        ))
        covered.update(planned.assignment_ids)
    return AllocatedExecution(run_set_id, MappingProxyType(requests), tuple(jobs),
                              tuple(key for key in requests if key not in covered))


async def dispatch_direct(adapter: BackendAdapter, request: RunRequest, *, limiter=None) -> Run:
    recorder = CaptureBuffer(request)
    try:
        returned = await anyio.to_thread.run_sync(
            partial(adapter.run, request, recorder=recorder),
            limiter=limiter, abandon_on_cancel=False,
        )
    except (ConfigurationError, CaptureValidationError):
        raise
    except SchemaValidationError as exc:
        raise capture_error("backend.capture", str(exc)) from exc
    except Exception as exc:
        return recorder.fail(exc)
    return recorder.finish(returned)


async def dispatch_job(adapter: NativeJobAdapter, request: NativeJobRequest) -> NativeJobOutput:
    recorder = JobCaptureBuffer(request)
    try:
        returned = await adapter.run_job(request, recorder=recorder)
    except (ConfigurationError, CaptureValidationError):
        raise
    except SchemaValidationError as exc:
        raise capture_error("native_job.capture", str(exc)) from exc
    except Exception as exc:
        return recorder.fail(exc)
    return recorder.finish(returned)


def _grading_inventory(outputs):
    observations = [output.grading_inventory_complete for output in outputs]
    if any(item.status == "observed" and item.value is False for item in observations):
        return observed(False, "At least one native job reported omitted grading inventory")
    if all(item.status == "observed" and item.value is True for item in observations):
        return observed(True, "All native grading inventories complete" if observations
                        else "No native grading was performed")
    return unknown("At least one native job did not establish complete grading inventory")


def _configuration_or_capture(group):
    """Unwrap structural task-group failures without mislabelling them agent errors."""
    for error in group.exceptions:
        if isinstance(error, (ConfigurationError, CaptureValidationError)):
            return error
        if isinstance(error, BaseExceptionGroup):
            nested = _configuration_or_capture(error)
            if nested is not None:
                return nested
    return None


class RunExecutor:
    async def execute(self, prepared: PreparedExecution) -> RunSet:
        allocated = allocate(prepared)
        outputs, by_assignment = [], {}
        for request in allocated.jobs:
            output = await dispatch_job(prepared.job_backends[request.config.backend.name], request)
            outputs.append(output)
            for run in output.runs:
                if run.assignment_id in by_assignment:
                    raise capture_error("runs", "Assignment was finalized more than once")
                by_assignment[run.assignment_id] = run

        limiter = anyio.CapacityLimiter(prepared.plan.execution.max_concurrency)

        async def dispatch(assignment_id):
            request = allocated.requests[assignment_id]
            adapter = prepared.backends[request.candidate.backend.name]
            by_assignment[assignment_id] = await dispatch_direct(adapter, request, limiter=limiter)

        try:
            async with anyio.create_task_group() as tasks:
                for assignment_id in allocated.direct_assignment_ids:
                    tasks.start_soon(dispatch, assignment_id)
        except BaseExceptionGroup as exc:
            structural = _configuration_or_capture(exc)
            if structural is not None:
                raise structural from exc
            raise
        capture = RunSet(
            id=allocated.run_set_id, plan=prepared.plan,
            runs=tuple(by_assignment[item.id] for item in prepared.plan.assignments),
            native_jobs=tuple(output.job for output in outputs),
            native_grades=tuple(bundle for output in outputs for bundle in output.native_grades),
            projections=tuple(projection for output in outputs for projection in output.projections),
            grading_inventory_complete=_grading_inventory(outputs),
        )
        return validate_capture(capture)


async def arun(study, *, backends=None, job_backends=None):
    prepared = prepare_execution(study, backends, job_backends)
    return await RunExecutor().execute(prepared)


def run(study, *, backends=None, job_backends=None):
    from agent_eval_flow.pipeline.api import run_sync
    return run_sync(lambda: arun(study, backends=backends, job_backends=job_backends))
