"""Thread-safe receipt buffers and faithful finalization of partial work."""
from __future__ import annotations

from dataclasses import replace
from threading import RLock

from agent_eval_flow.objects import (
    ArtifactRef, CaptureValidationError, ErrorRecord, Event, Execution,
    NativeGradeBundle, NativeJobOutput, NativeJobRecord, Observation,
    ProjectionReport, Run, ValidationIssue, ValidationReport, VersionRef,
)


def capture_error(path, message):
    return CaptureValidationError(ValidationReport(issues=(
        ValidationIssue(path=path, message=message, severity="error"),
    )))


def unknown(reason):
    return Observation(value=None, status="unknown", reason=reason)


def observed(value, reason):
    return Observation(value=value, status="observed", reason=reason)


def missing_run(assignment, *, run_id, cost_scope, job_id=None, reason):
    return Run(
        id=run_id, assignment_id=assignment.id, status="unobserved", cost_scope=cost_scope,
        output=None, output_state="unknown", artifacts={}, executions=(), events=(),
        output_sources=(), started_at=None, ended_at=None, environment=unknown(reason),
        execution_inventory_complete=unknown(reason), job_id=job_id,
    )


def _unique(records, path):
    result = {}
    for record in records:
        if record.id in result:
            raise capture_error(path, f"Duplicate record ID {record.id!r} in final snapshot")
        result[record.id] = record
    return result


def _merge_immutable(target, key, value, path):
    old = target.get(key)
    if old is not None and old != value:
        raise capture_error(path, f"Conflicting immutable receipt {key!r}")
    target[key] = value


def _progress(old, new, *, identity_fields, pending, path):
    if old == new:
        return new
    if old.status not in pending:
        raise capture_error(path, f"Terminal receipt {old.id!r} cannot change")
    for name in identity_fields:
        if getattr(old, name) != getattr(new, name):
            raise capture_error(path + "." + name, "Receipt ownership or identity cannot change")
    if old.started_at is not None and new.started_at != old.started_at:
        raise capture_error(path + ".started_at", "Recorded start time cannot change")
    for key, value in old.native_refs.items():
        if new.native_refs.get(key) != value:
            raise capture_error(path + ".native_refs", "Native identifiers cannot be removed or changed")
    return new


class CaptureBuffer:
    def __init__(self, request):
        self.request = request
        self.executions, self.events, self.artifacts = {}, {}, {}
        self.state, self.final = "open", None
        self._returned = None
        self.lock = RLock()

    def _open(self):
        if self.state != "open":
            raise capture_error("recorder", f"Recorder is {self.state}")

    def _record(self, operation):
        with self.lock:
            self._open()
            try:
                operation()
            except CaptureValidationError:
                self.state = "invalid"
                raise

    def record_execution(self, execution):
        def operation():
            if not isinstance(execution, Execution):
                raise capture_error("executions", "Expected Execution receipt")
            if execution.resources.cost_scope != self.request.policy.cost_scope:
                raise capture_error("executions.resources.cost_scope", "Execution cost scope differs from policy")
            old = self.executions.get(execution.id)
            if old is not None:
                _progress(old, execution, identity_fields=("id", "slot", "retry_index", "parent_id", "role"),
                          pending={"running", "awaiting_input", "paused"}, path="executions." + execution.id)
            self.executions[execution.id] = execution
        self._record(operation)

    def record_event(self, event):
        def operation():
            if not isinstance(event, Event):
                raise capture_error("events", "Expected Event receipt")
            _merge_immutable(self.events, event.id, event, "events")
        self._record(operation)

    def record_artifact(self, name, artifact):
        def operation():
            if not isinstance(name, str) or not name or not isinstance(artifact, ArtifactRef):
                raise capture_error("artifacts", "Expected nonempty artifact name and ArtifactRef")
            _merge_immutable(self.artifacts, name, artifact, "artifacts")
        self._record(operation)

    def finish(self, returned):
        with self.lock:
            if self.state == "finalized" and (returned == self.final or returned == self._returned):
                return self.final
            self._open()
            try:
                if not isinstance(returned, Run):
                    raise capture_error("run", "Backend must return a Run")
                if returned.id != self.request.run_id or returned.assignment_id != self.request.assignment.id:
                    raise capture_error("run.id", "Returned run must match allocated run and assignment IDs")
                if returned.job_id is not None:
                    raise capture_error("run.job_id", "A direct run cannot claim native job ownership")
                _unique(returned.executions, "executions")
                _unique(returned.events, "events")
                for execution in returned.executions:
                    self.record_execution(execution)
                for event in returned.events:
                    self.record_event(event)
                for name, artifact in returned.artifacts.items():
                    self.record_artifact(name, artifact)
                if returned.cost_scope != self.request.policy.cost_scope:
                    raise capture_error("run.cost_scope", "Run cost scope differs from policy")
                self.final = replace(returned, executions=tuple(self.executions.values()),
                                     events=tuple(self.events.values()), artifacts=self.artifacts)
                report = self.final.validate()
                if not report.valid:
                    raise CaptureValidationError(report)
                self._returned = returned
                self.state = "finalized"
                return self.final
            except CaptureValidationError:
                self.state = "invalid"
                raise

    def fail(self, error):
        with self.lock:
            self._open()
            reason = f"{type(error).__name__}: {error}"
            run = missing_run(self.request.assignment, run_id=self.request.run_id,
                              cost_scope=self.request.policy.cost_scope, reason=reason)
            # A callback failure does not establish either process stop or complete inventory.
            run = replace(run, status="infrastructure_error", artifacts=self.artifacts,
                          executions=tuple(self.executions.values()), events=tuple(self.events.values()),
                          started_at=min((e.started_at for e in self.executions.values()
                                          if e.started_at is not None), default=None),
                          error=ErrorRecord(code=type(error).__name__, message=reason))
            report = run.validate()
            if not report.valid:
                self.state = "invalid"
                raise CaptureValidationError(report)
            self.final, self.state = run, "finalized"
            return run


class JobCaptureBuffer:
    def __init__(self, request):
        self.request, self.job = request, None
        self.runs, self.bundles, self.projections = {}, {}, []
        self.state, self.final = "open", None
        self._returned = None
        self.lock = RLock()
        self._requests = {item.assignment.id: item for item in request.requests}

    def _open(self):
        if self.state != "open":
            raise capture_error("native_recorder", f"Recorder is {self.state}")

    def _record(self, operation):
        with self.lock:
            self._open()
            try:
                operation()
            except CaptureValidationError:
                self.state = "invalid"
                raise

    def _check_job(self, job):
        if not isinstance(job, NativeJobRecord):
            raise capture_error("native_job", "Expected NativeJobRecord")
        if (job.id != self.request.job_id or job.planned_job_id != self.request.planned_job_id
                or job.backend != self.request.config.backend):
            raise capture_error("native_job.id", "Job receipt differs from allocated identity/backend")
        if (len(job.assignment_ids) != len(self._requests)
                or set(job.assignment_ids) != set(self._requests)):
            raise capture_error("native_job.assignment_ids", "Job must retain every allocated assignment exactly once")
        links = {}
        for link in job.links:
            allocation = self._requests.get(link.assignment_id)
            if allocation is None or link.run_id != allocation.run_id:
                raise capture_error("native_job.links", "Link must match allocated assignment and run IDs")
            if link.run_id in links:
                raise capture_error("native_job.links", "Duplicate native run link")
            links[link.run_id] = link

    def record_job(self, job):
        def operation():
            self._check_job(job)
            if self.job is not None:
                # Membership is validated as a complete set above. Export order
                # can change without changing which assignments this job owns.
                _progress(self.job, job, identity_fields=("id", "planned_job_id", "backend"),
                          pending={"running", "unknown"}, path="native_job." + job.id)
                incoming = {link.run_id: link for link in job.links}
                if any(incoming.get(link.run_id) != link for link in self.job.links):
                    raise capture_error("native_job.links", "Recorded links cannot disappear or change")
            self.job = job
        self._record(operation)

    def record_run(self, run):
        def operation():
            if not isinstance(run, Run):
                raise capture_error("native_job.runs", "Expected Run receipt")
            allocation = self._requests.get(run.assignment_id)
            if (allocation is None or run.id != allocation.run_id
                    or run.job_id != self.request.job_id):
                raise capture_error("native_job.runs", "Run must match allocated run, assignment and job IDs")
            if run.cost_scope != allocation.policy.cost_scope:
                raise capture_error("native_job.runs.cost_scope", "Run cost scope differs from policy")
            _merge_immutable(self.runs, run.id, run, "native_job.runs")
        self._record(operation)

    def record_grade_bundle(self, bundle):
        def operation():
            if not isinstance(bundle, NativeGradeBundle):
                raise capture_error("native_grades", "Expected NativeGradeBundle receipt")
            run_ids = {item.run_id for item in self.request.requests}
            if any(grade.run_id not in run_ids for grade in bundle.grades):
                raise capture_error("native_grades", "Grade refers to a run outside this native job")
            if any(set(activity.run_ids) - run_ids for activity in bundle.activities):
                raise capture_error("native_grades.activities", "Activity refers to runs outside this native job")
            _merge_immutable(self.bundles, bundle.id, bundle, "native_grades")
        self._record(operation)

    def _complete_runs(self, reason):
        result = []
        for request in self.request.requests:
            run = self.runs.get(request.run_id)
            if run is None:
                run = missing_run(request.assignment, run_id=request.run_id,
                                  cost_scope=request.policy.cost_scope,
                                  job_id=self.request.job_id, reason=reason)
            result.append(run)
        links = {link.run_id: link for link in self.job.links}
        for run in result:
            if run.id in links and (links[run.id].assignment_id != run.assignment_id
                                    or links[run.id].native_refs != run.native_refs):
                # A link alone may establish a missing trial's native identity.
                if run.status == "unobserved" and run.id not in self.runs:
                    result[result.index(run)] = replace(run, native_refs=links[run.id].native_refs)
                else:
                    raise capture_error("native_job.links", "Native links contradict captured run facts")
        return tuple(result)

    def finish(self, returned):
        with self.lock:
            if self.state == "finalized" and (returned == self.final or returned == self._returned):
                return self.final
            self._open()
            try:
                if not isinstance(returned, NativeJobOutput):
                    raise capture_error("native_job", "Native adapter must return NativeJobOutput")
                _unique(returned.runs, "native_job.runs")
                _unique(returned.native_grades, "native_grades")
                self.record_job(returned.job)
                for run in returned.runs:
                    self.record_run(run)
                for bundle in returned.native_grades:
                    self.record_grade_bundle(bundle)
                runs = self._complete_runs("Native export omitted this allocated run")
                self.final = NativeJobOutput(
                    job=self.job, runs=runs, native_grades=tuple(self.bundles.values()),
                    projections=tuple(self.projections) + returned.projections,
                    grading_inventory_complete=returned.grading_inventory_complete,
                )
                self._returned = returned
                self.state = "finalized"
                return self.final
            except CaptureValidationError:
                self.state = "invalid"
                raise

    def fail(self, error):
        with self.lock:
            self._open()
            reason = f"{type(error).__name__}: {error}"
            error_record = ErrorRecord(code=type(error).__name__, message=reason)
            if self.job is None:
                self.job = NativeJobRecord(
                    id=self.request.job_id, planned_job_id=self.request.planned_job_id,
                    backend=self.request.config.backend, assignment_ids=tuple(self._requests),
                    status="error", started_at=None, ended_at=None, error=error_record,
                )
            elif self.job.status in {"running", "unknown"}:
                self.job = replace(self.job, status="error", ended_at=None, error=error_record)
            else:
                # Job completion and subsequent export failure are independent observations.
                self.projections.append(ProjectionReport(
                    mapper=VersionRef(name="agent_eval_flow.capture", revision="1"),
                    source_format=VersionRef(name="native-job-output", revision="1"),
                    sources=(), issues=(ValidationIssue(
                        path="native_job.export", message=reason, severity="warning"),),
                ))
            self.final = NativeJobOutput(
                job=self.job, runs=self._complete_runs(reason), native_grades=tuple(self.bundles.values()),
                projections=tuple(self.projections), grading_inventory_complete=unknown(reason),
            )
            self.state = "finalized"
            return self.final


def validate_capture(capture):
    report = capture.validate()
    if not report.valid:
        raise CaptureValidationError(report)
    return capture
