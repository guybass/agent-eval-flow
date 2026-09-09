"""Invocation-owned configuration receipts, collection and failure retention."""
from dataclasses import replace
from datetime import datetime, timezone
from threading import RLock

from agent_eval_flow import objects as o
from agent_eval_flow.evaluation.engine import unknown_resources
from agent_eval_flow.objects.identity import canonical_bytes
from .capture import capture_error, unknown
from .snapshot_assessment import candidate_subject, configuration_params_fingerprint


def _equal(left, right):
    return canonical_bytes(left) == canonical_bytes(right)


def _append_mapping(old, new, path):
    for key, value in old.items():
        if key not in new or not _equal(value, new[key]):
            raise capture_error(path, 'Acknowledged source facts cannot be removed or changed')


class AssessmentBuffer:
    """Cumulative progress replaces one owned receipt; only equal terminal copies repeat."""
    def __init__(self, request):
        self.request = request
        self.collection = isinstance(request, o.ConfigurationCollectRequest)
        self.artifacts = {}
        self.activity = None
        self.capture = None
        self.activity_final = self.capture_final = False
        self.closed = False
        self.lock = RLock()
        self.started_at = datetime.now(timezone.utc)

    def _open(self):
        if self.closed:
            raise capture_error('assessment.recorder', 'Recorder is closed')

    @property
    def subject(self):
        return candidate_subject(self.request.candidate) if self.collection else self.request.subject

    @property
    def implementation(self):
        return self.request.spec.collector if self.collection else self.request.check.evaluator

    def record_artifact(self, name, artifact):
        with self.lock:
            self._open()
            if not isinstance(name, str) or not name or not isinstance(artifact, o.ArtifactRef):
                raise capture_error('assessment.artifacts', 'Expected nonempty alias and ArtifactRef')
            if name in self.artifacts and not _equal(self.artifacts[name], artifact):
                raise capture_error('assessment.artifacts', 'Conflicting artifact alias')
            self.artifacts[name] = artifact

    def record_activity(self, activity, *, final=False):
        with self.lock:
            self._open()
            if not isinstance(activity, o.AssessmentActivity):
                raise capture_error('assessment.activity', 'Expected AssessmentActivity')
            activity.validate().raise_for_errors()
            if (activity.id != self.request.activity_id or activity.request_id != self.request.id
                    or activity.implementation != self.implementation
                    or activity.input_fingerprint != self.request.input_fingerprint
                    or activity.phase != ('collection' if self.collection else 'configuration_evaluation')
                    or not _equal(activity.subjects, (self.subject,))):
                raise capture_error('assessment.activity', 'Activity differs from allocated invocation identity')
            old = self.activity
            if old is not None:
                if self.activity_final and not _equal(old, activity):
                    raise capture_error('assessment.activity', 'Conflicting terminal activity')
                if old.started_at is not None and old.started_at != activity.started_at:
                    raise capture_error('assessment.activity', 'Acknowledged start time cannot change')
                if old.resources.cost_scope != activity.resources.cost_scope:
                    raise capture_error('assessment.activity', 'Resource scope cannot change during progress')
                _append_mapping(old.artifacts, activity.artifacts, 'assessment.activity.artifacts')
                for name in ('cost_usd', 'input_tokens', 'output_tokens', 'human_minutes'):
                    previous, current = getattr(old.resources, name), getattr(activity.resources, name)
                    if previous.status == 'observed' and (current.status != 'observed' or current.value < previous.value):
                        raise capture_error('assessment.activity.resources', 'Observed cumulative usage cannot be retracted')
            for name, artifact in activity.artifacts.items():
                self.record_artifact(name, artifact)
            self.activity = activity
            self.activity_final |= final

    def record_capture(self, capture, *, final=False):
        with self.lock:
            self._open()
            if not self.collection:
                raise capture_error('assessment.capture', 'An evaluator cannot replace its input capture')
            validate_collected_capture(capture, self.request)
            old = self.capture
            if old is not None:
                if old.id != capture.id:
                    raise capture_error('assessment.capture', 'Capture identity cannot change')
                if self.capture_final and not _equal(old, capture):
                    raise capture_error('assessment.capture', 'Conflicting terminal capture')
                _append_mapping(old.artifacts, capture.artifacts, 'assessment.capture.artifacts')
                if old.snapshot is not None:
                    if capture.snapshot is None:
                        raise capture_error('assessment.capture', 'Acknowledged snapshot cannot be removed')
                    _append_mapping({e.id: e for e in old.snapshot.entries},
                                    {e.id: e for e in capture.snapshot.entries}, 'assessment.snapshot.entries')
                    _append_mapping(old.snapshot.context, capture.snapshot.context, 'assessment.snapshot.context')
            for activity in capture.activities:
                self.record_activity(activity, final=final)
            for name, artifact in capture.artifacts.items():
                self.record_artifact(name, artifact)
            self.capture = capture
            self.capture_final |= final

    def failure_activity(self, error, *, cancelled=False):
        if self.activity_final:
            return self.activity
        if self.activity is not None:
            # A callback exception establishes neither remote termination nor final usage.
            return replace(self.activity, status='cancelled' if cancelled else 'error', ended_at=None, error=error,
                           inventory_complete=unknown('Callback failed; final usage inventory is unknown'),
                           artifacts={**self.artifacts, **self.activity.artifacts})
        return o.AssessmentActivity(
            id=self.request.activity_id, request_id=self.request.id,
            implementation=self.implementation, input_fingerprint=self.request.input_fingerprint,
            subjects=(self.subject,), phase='collection' if self.collection else 'configuration_evaluation',
            status='cancelled' if cancelled else 'error', resources=unknown_resources(('model',), 'Provider failed before reporting usage'),
            inventory_complete=unknown('Provider did not confirm its resource inventory'),
            started_at=self.started_at, error=error, artifacts=self.artifacts,
        )


def validate_collected_capture(capture, request):
    if not isinstance(capture, o.ConfigurationCapture):
        raise capture_error('configuration.capture', 'Collector must return ConfigurationCapture')
    report = capture.validate()
    if not report.valid:
        raise o.CaptureValidationError(report)
    if (capture.request_id != request.id or capture.candidate_id != request.candidate.id
            or capture.candidate_fingerprint != request.candidate.fingerprint()):
        raise capture_error('configuration.capture', 'Capture differs from allocated candidate/request')
    if capture.snapshot is not None:
        if (capture.snapshot.collector != request.spec.collector
                or capture.snapshot.params_fingerprint != configuration_params_fingerprint(request.spec)):
            raise capture_error('configuration.snapshot', 'Snapshot collector or parameter identity differs')
    if any(activity.id != request.activity_id for activity in capture.activities):
        raise capture_error('configuration.activities', 'Collector returned an unallocated activity')


async def collect_configuration(request, collector):
    recorder = AssessmentBuffer(request)
    try:
        returned = await collector.collect(request, recorder=recorder)
        if isinstance(returned, o.ConfigurationCapture):
            # An invalid outer capture identity must not discard independently
            # valid, invocation-owned usage and source receipts returned with it.
            for name, artifact in returned.artifacts.items():
                recorder.record_artifact(name, artifact)
            for activity in returned.activities:
                recorder.record_activity(activity, final=True)
        recorder.record_capture(returned, final=True)
        if recorder.activity is None:
            raise capture_error('configuration.activity', 'Collector omitted its invocation activity')
        recorder.record_activity(recorder.activity, final=True)
        result = replace(returned, activities=(recorder.activity,), artifacts=recorder.artifacts)
    except Exception as exc:
        error = o.ErrorRecord(code=type(exc).__name__, message=str(exc) or type(exc).__name__)
        activity = recorder.failure_activity(error)
        prior = recorder.capture
        result = o.ConfigurationCapture(
            id=prior.id if prior else request.id + '/capture', request_id=request.id,
            candidate_id=request.candidate.id, candidate_fingerprint=request.candidate.fingerprint(),
            snapshot=prior.snapshot if prior else None, status='error', activities=(activity,),
            artifacts=recorder.artifacts, error=error,
        )
    except BaseException as exc:
        # Propagate cancellation/interrupts while exposing acknowledged receipts
        # to the owner. Cancelling an await proves neither stop nor final usage.
        error = o.ErrorRecord(code=type(exc).__name__, message=str(exc) or type(exc).__name__)
        activity = recorder.failure_activity(error, cancelled=True)
        prior = recorder.capture
        partial = o.ConfigurationCapture(
            id=prior.id if prior else request.id + '/capture', request_id=request.id,
            candidate_id=request.candidate.id, candidate_fingerprint=request.candidate.fingerprint(),
            snapshot=prior.snapshot if prior else None, status='cancelled', activities=(activity,),
            artifacts=recorder.artifacts, error=error)
        setattr(exc, 'configuration_capture', partial)
        setattr(exc, 'artifacts', dict(recorder.artifacts))
        raise
    finally:
        recorder.closed = True
    return result
