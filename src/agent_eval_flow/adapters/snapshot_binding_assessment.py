"""Explicit callback/context integration for runtime-specific snapshot staging.

The caller supplies the real preparation and cleanup. This adapter validates
declared support and delegates; it never assumes that copying files establishes
runtime configuration parity.
"""
from __future__ import annotations

import inspect
import sys

from .. import objects as o


class _BoundDirect:
    def __init__(self, delegate, request, owner):
        self._delegate, self._request, self._owner = delegate, request, owner
    @property
    def ref(self):
        return self._delegate.ref
    def capabilities(self):
        return self._delegate.capabilities()
    def run(self, request, *, recorder):
        if not self._owner._active:
            raise o.ConfigurationError("Snapshot delegate is outside its active session")
        if request != self._request:
            raise o.ConfigurationError("A snapshot session cannot dispatch a different assignment")
        return self._delegate.run(request, recorder=recorder)


class _BoundJob:
    def __init__(self, delegate, request, owner):
        self._delegate, self._request, self._owner = delegate, request, owner
    @property
    def ref(self):
        return self._delegate.ref
    def capabilities(self):
        return self._delegate.capabilities()
    async def run_job(self, request, *, recorder):
        if not self._owner._active:
            raise o.ConfigurationError("Snapshot delegate is outside its active session")
        if request != self._request:
            raise o.ConfigurationError("A snapshot session cannot split or replace its native job")
        return await self._delegate.run_job(request, recorder=recorder)


class _CheckedSession:
    def __init__(self, context, request):
        self._context, self._request = context, request
        self.bindings = ()
        self.backend = self.job_backend = None
        self._entered = False
        self._active = False

    async def __aenter__(self):
        if self._entered:
            raise o.ConfigurationError("Snapshot sessions are single-use")
        self._entered = True
        session = await self._context.__aenter__()
        try:
            request = self._request
            bindings = tuple(session.bindings)
            if not all(isinstance(binding, o.SnapshotBinding) for binding in bindings):
                raise o.CaptureValidationError("Session returned an invalid snapshot binding")
            if len(bindings) != len(request.snapshots) or {b.candidate_id for b in bindings} != set(request.snapshots):
                raise o.CaptureValidationError("Snapshot bindings must cover the requested candidates exactly")
            requests = (request.run_request,) if request.run_request else request.native_job_request.requests
            for binding in bindings:
                if not isinstance(binding, o.SnapshotBinding):
                    raise o.CaptureValidationError("Session returned an invalid snapshot binding")
                snapshot = request.snapshots[binding.candidate_id]
                expected_runs = {r.run_id for r in requests if r.candidate.id == binding.candidate_id}
                expected_jobs = {request.native_job_request.job_id} if request.native_job_request else set()
                if (binding.candidate_fingerprint != snapshot.candidate_fingerprint
                        or binding.snapshot_fingerprint != snapshot.fingerprint
                        or set(binding.run_ids) != expected_runs or set(binding.job_ids) != expected_jobs):
                    raise o.CaptureValidationError("Snapshot binding changed subject, snapshot or dispatch membership")
                if request.requirements[binding.candidate_id] == "verified" and binding.status != "verified":
                    raise o.ConfigurationError("Required snapshot parity was not verified")
                if binding.status == "verified" and not binding.evidence:
                    raise o.CaptureValidationError("Verified parity requires retained evidence")
            if request.run_request:
                delegate = session.backend
                if session.job_backend is not None or delegate is None or delegate.ref != request.run_request.candidate.backend:
                    raise o.CaptureValidationError("Session must supply the selected direct backend revision")
                if not callable(getattr(delegate, "run", None)) or not callable(getattr(delegate, "capabilities", None)):
                    raise o.CaptureValidationError("Session delegate does not implement the direct backend protocol")
                self.backend = _BoundDirect(delegate, request.run_request, self)
            else:
                delegate = session.job_backend
                if session.backend is not None or delegate is None or delegate.ref != request.native_job_request.config.backend:
                    raise o.CaptureValidationError("Session must supply the selected native backend revision")
                if not callable(getattr(delegate, "run_job", None)) or not callable(getattr(delegate, "capabilities", None)):
                    raise o.CaptureValidationError("Session delegate does not implement the native backend protocol")
                self.job_backend = _BoundJob(delegate, request.native_job_request, self)
            self.bindings = bindings
            self._active = True
            return self
        except BaseException:
            await self._context.__aexit__(*sys.exc_info())
            raise

    async def __aexit__(self, exc_type, exc, traceback):
        self._active = False
        await self._context.__aexit__(exc_type, exc, traceback)
        return False


class CallbackSnapshotBinder:
    """Adapt an async preparation callback returning an async session context.

    The callback owns actual configuration verification and retained proof. Its
    returned context must yield ``bindings``, ``backend`` and ``job_backend``.
    It performs no agent work until a staged delegate is explicitly called.
    """

    def __init__(self, *, ref, capabilities, prepare):
        if not isinstance(ref, o.VersionRef) or not ref.revision:
            raise o.ConfigurationError("Snapshot binder requires a resolved version reference")
        if not isinstance(capabilities, o.SnapshotCapabilities) or not callable(prepare):
            raise o.ConfigurationError("Snapshot binder requires typed capabilities and a preparation callback")
        if not capabilities.backends or any(not item.revision for item in capabilities.backends):
            raise o.ConfigurationError("Snapshot binder must advertise complete backend revisions")
        self.ref, self._capabilities, self._prepare = ref, capabilities, prepare

    def capabilities(self):
        return self._capabilities

    async def prepare(self, request):
        if not isinstance(request, o.SnapshotBindRequest):
            raise o.ConfigurationError("Snapshot binder requires a SnapshotBindRequest")
        caps = self._capabilities
        if request.run_request:
            backend = request.run_request.candidate.backend
            supported = caps.direct
        else:
            backend = request.native_job_request.config.backend
            supported = caps.native
        scopes = {entry.scope for snapshot in request.snapshots.values() for entry in snapshot.entries}
        if (not supported or backend not in caps.backends or not scopes.issubset(set(caps.scopes))
                or ("verified" in request.requirements.values() and not caps.verified)):
            raise o.ConfigurationError("Snapshot binder cannot prepare this backend, scope or parity requirement")
        context = self._prepare(request)
        if inspect.isawaitable(context):
            context = await context
        if not callable(getattr(context, "__aenter__", None)) or not callable(getattr(context, "__aexit__", None)):
            raise o.CaptureValidationError("Preparation must return an asynchronous session context")
        return _CheckedSession(context, request)
