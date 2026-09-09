"""Bind complete dispatch units while retaining invocation-local session ownership."""
from dataclasses import dataclass
from uuid import uuid4

from agent_eval_flow import objects as o
from .capture import capture_error
from .preflight import _limits


@dataclass
class BoundDispatch:
    session: object = None
    delegate: object = None
    bindings: tuple = ()
    blocked_reason: str | None = None


def unit_requests(unit):
    return unit.requests if isinstance(unit, o.NativeJobRequest) else (unit,)


def unknown_bindings(unit, snapshots, reason):
    items = unit_requests(unit)
    return tuple(o.SnapshotBinding(
        candidate_id=cid, candidate_fingerprint=snapshot.candidate_fingerprint,
        snapshot_fingerprint=snapshot.fingerprint, status='unknown', reason=reason,
        run_ids=tuple(item.run_id for item in items if item.candidate.id == cid),
        job_ids=(unit.job_id,) if isinstance(unit, o.NativeJobRequest) else (),
    ) for cid, snapshot in snapshots.items())


async def prepare_bound_dispatch(unit, *, plan, captures, binders, delegate):
    items = unit_requests(unit)
    cids = tuple(dict.fromkeys(item.candidate.id for item in items))
    configured = {cid: plan.configuration[cid] for cid in cids if cid in plan.configuration}
    snapshots = {cid: captures[cid].snapshot for cid in configured if captures[cid].snapshot is not None}
    if any(spec.snapshot_requirement == 'verified' and cid not in snapshots for cid, spec in configured.items()):
        return BoundDispatch(blocked_reason='Required configuration snapshot was not captured')
    if not snapshots:
        return BoundDispatch(delegate=delegate)
    ref = unit.config.backend if isinstance(unit, o.NativeJobRequest) else unit.candidate.backend
    binder = binders.get(ref.name)
    if binder is None:
        if any(spec.snapshot_requirement == 'verified' for spec in configured.values()):
            return BoundDispatch(blocked_reason='Required compatible snapshot binder is unavailable')
        return BoundDispatch(delegate=delegate, bindings=unknown_bindings(
            unit, snapshots, 'No runtime snapshot binding was requested in record-only mode'))
    caps = binder.capabilities()
    native = isinstance(unit, o.NativeJobRequest)
    if (not isinstance(caps, o.SnapshotCapabilities) or ref not in caps.backends
            or not (caps.native if native else caps.direct)
            or (any(spec.snapshot_requirement == 'verified' for spec in configured.values()) and not caps.verified)):
        return BoundDispatch(blocked_reason='Snapshot binder capabilities do not match the selected backend and parity')
    scopes = {entry.scope for snapshot in snapshots.values() for entry in snapshot.entries}
    if not scopes <= set(caps.scopes):
        return BoundDispatch(blocked_reason='Snapshot binder does not support every captured configuration scope')
    request = o.SnapshotBindRequest(
        id='binding/' + uuid4().hex, snapshots=snapshots,
        requirements={cid: configured[cid].snapshot_requirement for cid in snapshots},
        native_job_request=unit if isinstance(unit, o.NativeJobRequest) else None,
        run_request=None if isinstance(unit, o.NativeJobRequest) else unit,
    )
    session = await binder.prepare(request)
    if not callable(getattr(session, '__aenter__', None)) or not callable(getattr(session, '__aexit__', None)):
        raise capture_error('snapshot.session', 'Binder must return an asynchronous session context')
    return BoundDispatch(session=session)


def validate_session(session, unit, *, plan, captures):
    items = unit_requests(unit)
    cids = {item.candidate.id for item in items} & set(plan.configuration)
    snapshots = {cid: captures[cid].snapshot for cid in cids if captures[cid].snapshot is not None}
    bindings = tuple(session.bindings)
    if not all(isinstance(binding, o.SnapshotBinding) for binding in bindings):
        raise capture_error('snapshot.binding', 'Expected SnapshotBinding records')
    if len(bindings) != len(snapshots) or {b.candidate_id for b in bindings} != set(snapshots):
        raise capture_error('snapshot.binding', 'Session must bind each selected candidate exactly once')
    for binding in bindings:
        if not isinstance(binding, o.SnapshotBinding):
            raise capture_error('snapshot.binding', 'Expected SnapshotBinding')
        binding.validate().raise_for_errors()
        if binding.status == 'verified' and not binding.evidence:
            raise capture_error('snapshot.binding', 'Verified runtime parity requires retained proof')
        snapshot = snapshots[binding.candidate_id]
        run_ids = {item.run_id for item in items if item.candidate.id == binding.candidate_id}
        job_ids = {unit.job_id} if isinstance(unit, o.NativeJobRequest) else set()
        if (binding.candidate_fingerprint != snapshot.candidate_fingerprint
                or binding.snapshot_fingerprint != snapshot.fingerprint
                or set(binding.run_ids) != run_ids or set(binding.job_ids) != job_ids):
            raise capture_error('snapshot.binding', 'Session evidence differs from snapshot or dispatch membership')
    native = isinstance(unit, o.NativeJobRequest)
    delegate = session.job_backend if native else session.backend
    other = session.backend if native else session.job_backend
    ref = unit.config.backend if native else unit.candidate.backend
    if delegate is None or other is not None or delegate.ref != ref:
        raise capture_error('snapshot.delegate', 'Staged delegate must retain the original backend revision')
    if not callable(getattr(delegate, 'run_job' if native else 'run', None)):
        raise capture_error('snapshot.delegate', 'Staged delegate does not implement the selected backend protocol')
    caps = delegate.capabilities()
    if native:
        if not isinstance(caps, o.NativeJobCapabilities) or not caps.fixed_repetitions:
            raise capture_error('snapshot.delegate', 'Staged native delegate lacks required job capabilities')
        if any(v.reference_columns for v in unit.config.verifiers) and not caps.private_verifier_channel:
            raise capture_error('snapshot.delegate', 'Staged delegate lacks the private verifier channel')
        for verifier in unit.config.verifiers:
            if verifier.budget is not None:
                _limits(caps.limits, verifier.budget, 'snapshot.delegate.verifier')
        caps = caps.limits
    for item in items:
        _limits(caps, item.policy.budget, 'snapshot.delegate')
    reason = None
    for binding in bindings:
        if plan.configuration[binding.candidate_id].snapshot_requirement != 'verified':
            continue
        inventory = snapshots[binding.candidate_id].inventory_complete
        if binding.status != 'verified' or inventory.status != 'observed' or inventory.value is not True:
            reason = 'Required configuration parity or complete source inventory was not verified'
    return BoundDispatch(delegate=delegate, bindings=bindings, blocked_reason=reason)
