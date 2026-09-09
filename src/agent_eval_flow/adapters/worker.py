"""One acknowledged invocation on a caller-supplied prepared runtime.

This client neither provisions a worker nor resubmits ambiguous dispatches.
Evidence is fetched and recursively relocated without changing native bytes.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Literal, Protocol
from urllib.parse import urlparse
from urllib.request import url2pathname

import anyio

from agent_eval_flow import objects as o
from agent_eval_flow.objects.identity import freeze_json
from agent_eval_flow.storage.artifacts import ArtifactCache
from .common import MaterializedEvidence, RuntimeCapabilities, StagedAsset
from .process import StopEvidence

WorkerCapture = o.Run | o.NativeJobOutput | o.BatchMetricOutput


@dataclass(frozen=True, kw_only=True)
class WorkerSubmission:
    request_id: str
    kind: Literal["direct", "native_job", "batch"]
    request: o.RunRequest | o.NativeJobRequest | o.BatchMetricRequest
    assets: tuple[StagedAsset, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "assets", tuple(self.assets))
        expected = {"direct": o.RunRequest, "native_job": o.NativeJobRequest, "batch": o.BatchMetricRequest}
        if self.kind not in expected or not isinstance(self.request, expected[self.kind]):
            raise o.ConfigurationError("Worker request kind does not match its request record")
        allocated = (self.request.run_id if self.kind == "direct" else
                     self.request.job_id if self.kind == "native_job" else self.request.id)
        if self.request_id != allocated:
            raise o.ConfigurationError("Worker submission must reuse the allocated run/job/activity ID")
        destinations = set()
        for asset in self.assets:
            allowed = {"agent"} if self.kind == "direct" else {"grader"} if self.kind == "batch" else {"agent", "verifier"}
            if asset.channel not in allowed:
                raise o.ConfigurationError("Worker asset channel is incompatible with the request kind")
            key = (asset.channel, asset.relative_path)
            if key in destinations:
                raise o.ConfigurationError("Worker assets have duplicate channel destinations")
            destinations.add(key)


@dataclass(frozen=True, kw_only=True)
class WorkerHandle:
    id: str
    request_id: str
    native_refs: Mapping[str, str]

    def __post_init__(self):
        if not self.id or not self.request_id:
            raise o.CaptureValidationError("Worker acknowledgment requires handle and request IDs")
        object.__setattr__(self, "native_refs", freeze_json(self.native_refs))


@dataclass(frozen=True, kw_only=True)
class WorkerUpdate:
    cursor: str | None
    state: Literal["running", "completed", "failed", "cancelled", "unknown"]
    capture: WorkerCapture | None
    error: o.ErrorRecord | None = None

    def __post_init__(self):
        if self.state not in {"running", "completed", "failed", "cancelled", "unknown"}:
            raise o.CaptureValidationError("Unknown worker update state")


class RuntimeWorker(Protocol):
    async def describe(self) -> RuntimeCapabilities: ...
    async def submit(self, submission: WorkerSubmission) -> WorkerHandle: ...
    async def poll(self, handle: WorkerHandle, *, cursor: str | None) -> WorkerUpdate: ...
    async def stop(self, handle: WorkerHandle, *, reason: str) -> StopEvidence: ...
    async def fetch(self, source: o.ArtifactRef, *, destination: Path) -> o.ArtifactRef: ...
    async def release(self, handle: WorkerHandle) -> None: ...


def _refs(value):
    if isinstance(value, o.ArtifactRef):
        yield value
    elif is_dataclass(value):
        for field in fields(value):
            yield from _refs(getattr(value, field.name))
    elif isinstance(value, Mapping):
        for child in value.values():
            yield from _refs(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _refs(child)


def _relocate(value, replacements):
    if isinstance(value, o.ArtifactRef):
        return replacements[value.uri]
    if is_dataclass(value):
        return replace(value, **{field.name: _relocate(getattr(value, field.name), replacements)
                                 for field in fields(value)})
    if isinstance(value, Mapping):
        return {key: _relocate(child, replacements) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return tuple(_relocate(child, replacements) for child in value)
    return value


def _attach(capture, name, artifact):
    if isinstance(capture, o.Run):
        return replace(capture, artifacts={**capture.artifacts, name: artifact})
    if isinstance(capture, o.NativeJobOutput):
        return replace(capture, job=replace(capture.job, artifacts={**capture.job.artifacts, name: artifact}))
    return replace(capture, activity=replace(capture.activity,
                                           artifacts={**capture.activity.artifacts, name: artifact}))


def _limits(actual, required):
    if actual is None:
        raise o.ConfigurationError("Prepared worker lacks the requested execution capabilities")
    for name in ("wall_time_limit", "token_limit", "cost_limit", "reset_state"):
        if getattr(required, name) and not getattr(actual, name):
            raise o.ConfigurationError(f"Prepared worker does not support {name}")


class PreparedWorkerClient:
    def __init__(self, *, worker: RuntimeWorker, expected: RuntimeCapabilities,
                 artifacts: ArtifactCache, poll_interval_s: float = 1.0,
                 cleanup_timeout_s: float = 5.0):
        if not math.isfinite(poll_interval_s) or poll_interval_s < 0:
            raise o.ConfigurationError("Worker poll interval must be finite and nonnegative")
        if not math.isfinite(cleanup_timeout_s) or cleanup_timeout_s <= 0:
            raise o.ConfigurationError("Worker cleanup timeout must be finite and positive")
        self.worker, self.expected, self.artifacts = worker, expected, artifacts
        self.poll_interval_s, self.cleanup_timeout_s = poll_interval_s, cleanup_timeout_s

    def _describe(self, actual, submission):
        if not isinstance(actual, RuntimeCapabilities):
            raise o.ConfigurationError("Worker describe must return RuntimeCapabilities")
        if actual.upstream_ref != self.expected.upstream_ref:
            raise o.ConfigurationError("Prepared worker upstream revision differs from the binding")
        if not set(self.expected.formats).issubset(actual.formats):
            raise o.ConfigurationError("Prepared worker does not provide the configured capture formats")
        if self.expected.deployment.status == "observed":
            if (actual.deployment.status != "observed"
                    or actual.deployment.value != self.expected.deployment.value):
                raise o.ConfigurationError("Prepared worker deployment identity differs from the binding")
        if self.expected.limits is not None:
            _limits(actual.limits, self.expected.limits)
        for asset in submission.assets:
            parsed = urlparse(asset.source.uri)
            if parsed.scheme == "file" or not parsed.scheme or (os.name == "nt" and len(parsed.scheme) == 1):
                report = self.artifacts.verify(asset.source)
                if not report.valid:
                    raise o.ConfigurationError(report)
        if submission.kind == "batch":
            if not actual.batch_grading:
                raise o.ConfigurationError("Prepared worker does not support batch grading")
            return
        if submission.kind == "native_job":
            caps = actual.native_jobs
            if caps is None or not caps.fixed_repetitions:
                raise o.ConfigurationError("Prepared worker lacks fixed native-job repetitions")
            private_data = any(item.references for item in submission.request.verifier_inputs)
            private_data = private_data or any(asset.channel == "verifier" for asset in submission.assets)
            if private_data and not caps.private_verifier_channel:
                raise o.ConfigurationError("Prepared worker lacks private verifier input isolation")
            requests = submission.request.requests
            for verifier in submission.request.config.verifiers:
                if verifier.budget is not None:
                    _limits(actual.limits, o.BackendCapabilities(wall_time_limit=True,
                        token_limit=verifier.budget.max_tokens is not None,
                        cost_limit=verifier.budget.max_cost_usd is not None, reset_state=True))
        else:
            requests = (submission.request,)
        for request in requests:
            budget = request.policy.budget
            _limits(actual.limits, o.BackendCapabilities(
                wall_time_limit=True, token_limit=budget.max_tokens is not None,
                cost_limit=budget.max_cost_usd is not None, reset_state=True,
            ))

    def _check_capture(self, submission, capture):
        request = submission.request
        if submission.kind == "direct":
            if not isinstance(capture, o.Run) or capture.id != request.run_id or capture.assignment_id != request.assignment.id:
                raise o.CaptureValidationError("Worker direct capture differs from allocated identities")
            if capture.job_id is not None:
                raise o.CaptureValidationError("Worker direct run cannot claim a native job")
            capture.validate().raise_for_errors()
        elif submission.kind == "native_job":
            if not isinstance(capture, o.NativeJobOutput):
                raise o.CaptureValidationError("Worker native job must return NativeJobOutput")
            job = capture.job
            expected = {item.run_id: item for item in request.requests}
            if (job.id != request.job_id or job.planned_job_id != request.planned_job_id
                    or job.backend != request.config.backend
                    or set(job.assignment_ids) != {item.assignment.id for item in request.requests}
                    or len(job.assignment_ids) != len(request.requests)):
                raise o.CaptureValidationError("Worker native job capture differs from allocated work")
            if len({run.id for run in capture.runs}) != len(capture.runs):
                raise o.CaptureValidationError("Worker exported duplicate native runs")
            for run in capture.runs:
                item = expected.get(run.id)
                if item is None or run.assignment_id != item.assignment.id or run.job_id != job.id:
                    raise o.CaptureValidationError("Worker exported foreign native run identities")
                run.validate().raise_for_errors()
            linked = set()
            for link in job.links:
                item = expected.get(link.run_id)
                if item is None or item.assignment.id != link.assignment_id or link.run_id in linked:
                    raise o.CaptureValidationError("Worker native links contradict allocated identities")
                linked.add(link.run_id)
        else:
            if not isinstance(capture, o.BatchMetricOutput):
                raise o.CaptureValidationError("Worker batch must return BatchMetricOutput")
            activity = capture.activity
            run_ids = {item.run.id for item in request.items}
            if (activity.id != request.id or activity.config_fingerprint != request.config_fingerprint
                    or set(activity.run_ids) != run_ids or len(activity.run_ids) != len(run_ids)):
                raise o.CaptureValidationError("Worker batch activity differs from allocated scope")
            if any(row.run_id not in run_ids or row.metric != request.metric.id for row in capture.measurements):
                raise o.CaptureValidationError("Worker batch returned foreign measurements")

    async def _materialize(self, capture, handle, cached):
        sources = {}
        for source in _refs(capture):
            if source.uri in sources and sources[source.uri] != source:
                raise o.CaptureValidationError("Conflicting artifact declarations for one native URI")
            sources[source.uri] = source
        replacements = {}
        for uri, source in sources.items():
            if uri in cached:
                previous, local = cached[uri]
                if previous != source:
                    raise o.CaptureValidationError("Worker changed an already captured artifact declaration")
                replacements[uri] = local
                continue
            fd, name = tempfile.mkstemp(prefix=".worker-", dir=self.artifacts.root)
            os.close(fd)
            destination = Path(name).resolve()
            try:
                fetched = await self.worker.fetch(source, destination=destination)
                if not isinstance(fetched, o.ArtifactRef):
                    raise o.StorageError("Worker fetch did not return an ArtifactRef")
                parsed = urlparse(fetched.uri)
                returned_path = Path(url2pathname(parsed.path)) if parsed.scheme == "file" else Path(fetched.uri)
                if returned_path.resolve() != destination:
                    raise o.StorageError("Worker fetch returned a different destination")
                if destination.is_symlink() or not destination.is_file():
                    raise o.StorageError("Worker fetch did not produce a regular staging file")
                def retain():
                    with destination.open("rb") as stream:
                        return self.artifacts.write_stream("worker-artifact", stream, source.media_type)
                local = await anyio.to_thread.run_sync(retain)
                if (source.sha256 is not None and local.sha256 != source.sha256
                        or fetched.sha256 is not None and local.sha256 != fetched.sha256):
                    raise o.StorageError(f"Worker artifact hash mismatch: {source.uri}")
                cached[uri] = source, local
                replacements[uri] = local
            finally:
                # The name was allocated by this client directly under the cache.
                # unlink removes a substituted symlink itself, never its target.
                destination.unlink(missing_ok=True)
        relocated = _relocate(capture, replacements)
        provenance = {
            "request_id": handle.request_id, "handle_id": handle.id,
            "native_refs": dict(handle.native_refs),
            "sources": {uri: {"local_uri": ref.uri, "sha256": ref.sha256,
                              "source_sha256": sources[uri].sha256}
                        for uri, ref in replacements.items()},
        }
        receipt = self.artifacts.write_bytes("worker-provenance",
            json.dumps(provenance, sort_keys=True).encode("utf-8"), "application/json")
        return _attach(relocated, "aef.worker.provenance", receipt), MaterializedEvidence(
            replacements=replacements, original_locations={ref.uri: uri for uri, ref in replacements.items()})

    async def invoke(self, submission: WorkerSubmission, *, record_partial: Callable[[WorkerCapture], None]):
        actual = await self.worker.describe()
        self._describe(actual, submission)
        handle, final, last, cursor = None, None, None, None
        cached, bundles = {}, {}
        original_exception, cleanup_error = None, None
        try:
            acknowledged = await self.worker.submit(submission)
            if not isinstance(acknowledged, WorkerHandle) or acknowledged.request_id != submission.request_id:
                raise o.CaptureValidationError("Worker acknowledgment differs from the submitted request")
            handle = acknowledged
            while True:
                update = await self.worker.poll(handle, cursor=cursor)
                if not isinstance(update, WorkerUpdate):
                    raise o.CaptureValidationError("Worker poll must return WorkerUpdate")
                cursor = update.cursor
                captured = update.capture
                if captured is not None:
                    self._check_capture(submission, captured)
                    if isinstance(captured, o.NativeJobOutput):
                        for bundle in captured.native_grades:
                            if bundle.id in bundles and bundles[bundle.id] != bundle:
                                raise o.CaptureValidationError("Worker changed a finalized native grading bundle")
                            bundles[bundle.id] = bundle
                    try:
                        captured, _ = await self._materialize(captured, handle, cached)
                    except Exception as exc:
                        # Retain known native outcomes and remote locations when
                        # download fails; this is not a fabricated local capture.
                        record_partial(update.capture)
                        setattr(exc, "capture", update.capture)
                        raise
                terminal = update.state in {"completed", "failed", "cancelled"}
                if terminal:
                    if captured is None:
                        detail = update.error.message if update.error is not None else "No terminal capture was exported"
                        raise RuntimeError(f"Worker {handle.id}: {detail}")
                    final = captured
                    if update.error is not None:
                        receipt = self.artifacts.write_bytes("worker-error",
                            json.dumps({"code": update.error.code, "message": update.error.message}).encode(),
                            "application/json")
                        final = _attach(final, "aef.worker.error", receipt)
                    break
                if captured is not None and captured != last:
                    record_partial(captured)
                    last = captured
                await anyio.sleep(self.poll_interval_s)
        except BaseException as exc:
            original_exception = exc
            exc.add_note(f"Worker request_id={submission.request_id}; handle_id={getattr(handle, 'id', None)}; no resubmission attempted")
            if handle is not None and isinstance(exc, anyio.get_cancelled_exc_class()):
                stop = StopEvidence(requested=True, confirmed=False, reason="Worker stop confirmation unavailable")
                with anyio.move_on_after(self.cleanup_timeout_s, shield=True):
                    try:
                        returned = await self.worker.stop(handle, reason="Client invocation cancelled")
                        if not isinstance(returned, StopEvidence):
                            raise o.CaptureValidationError("Worker did not return StopEvidence")
                        stop = returned
                    except Exception as stop_error:
                        stop = StopEvidence(requested=True, confirmed=False, reason=str(stop_error))
                setattr(exc, "stop_evidence", stop)
                exc.add_note(f"Worker stop confirmed={stop.confirmed}: {stop.reason}")
            raise
        finally:
            if handle is not None:
                with anyio.move_on_after(self.cleanup_timeout_s, shield=True) as cleanup:
                    try:
                        await self.worker.release(handle)
                    except Exception as exc:
                        cleanup_error = f"Worker release failed: {type(exc).__name__}: {exc}"
                if cleanup.cancel_called:
                    cleanup_error = "Worker release did not finish within its cleanup deadline"
                if cleanup_error and original_exception is not None:
                    original_exception.add_note(cleanup_error)
        if cleanup_error:
            receipt = self.artifacts.write_bytes("worker-cleanup", cleanup_error.encode(), "text/plain")
            final = _attach(final, "aef.worker.cleanup", receipt)
        record_partial(final)
        return final
