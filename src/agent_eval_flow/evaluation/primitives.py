"""Explicit helpers over captured execution facts; no implicit agent verdicts."""
from dataclasses import replace
from .. import objects as o


def from_observation(run_id, metric, observation, *, activity_ids=()):
    return o.Measurement(run_id=run_id, metric=metric, value=observation.value,
        status="missing" if observation.status == "unknown" else "ok",
        basis=None if observation.status == "unknown" else observation.status,
        reason=observation.reason or "Captured " + metric, evidence=observation.evidence,
        activity_ids=activity_ids)


def measure_run(run, metric_id):
    if metric_id == "run.completed":
        if run.status in {"completed", "agent_error", "timed_out", "infrastructure_error", "cancelled"}:
            obs = o.Observation(value=run.status == "completed", status="observed", reason="Captured run status: " + run.status)
        else:
            obs = o.Observation(value=None, status="unknown", reason="Run has no terminal completion result: " + run.status)
    elif metric_id == "run.duration_s":
        obs = run.duration_s()
    else:
        obs = getattr(run.resources(), metric_id.removeprefix("run."))
        if metric_id == "run.cost_usd":
            obs = replace(obs, reason=(obs.reason or "Captured cost") + "; cost scope: " + ", ".join(run.cost_scope))
    return from_observation(run.id, metric_id, obs)
