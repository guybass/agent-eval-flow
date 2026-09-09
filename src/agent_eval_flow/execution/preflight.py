"""Resolve only the required runtime bindings before dispatch."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from types import MappingProxyType

from agent_eval_flow.objects import (
    BackendCapabilities, ConfigurationError, NativeJobCapabilities,
    BackendAdapter, EvalDataset, NativeJobAdapter, RunPlan, Study,
    ValidationIssue, ValidationReport,
)
from .planning import build_plan


def configuration_error(path, message):
    return ConfigurationError(ValidationReport(issues=(
        ValidationIssue(path=path, message=message, severity="error"),
    )))


@dataclass(frozen=True)
class PreparedExecution:
    plan: RunPlan
    dataset: EvalDataset
    backends: Mapping[str, BackendAdapter]
    job_backends: Mapping[str, NativeJobAdapter]


def _resolve(ref, registry, path):
    if not ref.revision:
        raise configuration_error(path, f"Live binding {ref.name!r} requires a resolved revision")
    if ref.name not in registry:
        raise configuration_error(path, f"Missing runtime binding {ref.name!r}")
    adapter = registry[ref.name]
    try:
        actual = adapter.ref
    except Exception as exc:
        raise configuration_error(path, f"Cannot resolve {ref.name!r}: {exc}") from exc
    if actual != ref:
        raise configuration_error(path, f"Binding {ref.name!r} has reference {actual!r}; expected {ref!r}")
    return adapter


def _limits(capabilities, budget, path):
    if not isinstance(capabilities, BackendCapabilities):
        raise configuration_error(path, "Expected BackendCapabilities")
    requirements = {
        "wall_time_limit": budget.wall_time_s is not None,
        "token_limit": budget.max_tokens is not None,
        "cost_limit": budget.max_cost_usd is not None,
        "reset_state": True,
    }
    for capability, required in requirements.items():
        if required and not getattr(capabilities, capability):
            raise configuration_error(path, f"Required capability {capability} is unsupported")


def prepare_execution(
    study: Study,
    backends: Mapping[str, BackendAdapter] | None = None,
    job_backends: Mapping[str, NativeJobAdapter] | None = None,
) -> PreparedExecution:
    plan = build_plan(study)
    direct, jobs, queried = {}, {}, {}
    grouped = {cid for job in plan.native_jobs for cid in job.config.candidate_ids}

    def capabilities(adapter, path):
        marker = id(adapter)
        if marker not in queried:
            try:
                queried[marker] = adapter.capabilities()
            except Exception as exc:
                raise configuration_error(path, f"Cannot establish runtime capabilities: {exc}") from exc
        return queried[marker]

    for cid, candidate in plan.candidates.items():
        if cid in grouped:
            continue
        path = f"candidates.{cid}.backend"
        adapter = _resolve(candidate.backend, backends or {}, path)
        _limits(capabilities(adapter, path), plan.execution.budget, path)
        direct[candidate.backend.name] = adapter
    for job in plan.native_jobs:
        path = f"execution.native_jobs.{job.config.id}"
        adapter = _resolve(job.config.backend, job_backends or {}, path)
        caps = capabilities(adapter, path)
        if not isinstance(caps, NativeJobCapabilities):
            raise configuration_error(path, "Expected NativeJobCapabilities")
        _limits(caps.limits, plan.execution.budget, path)
        if not caps.fixed_repetitions:
            raise configuration_error(path, "Native backend must support fixed_repetitions")
        for verifier in job.config.verifiers:
            if verifier.reference_columns and not caps.private_verifier_channel:
                raise configuration_error(path, "Private references require private_verifier_channel")
            if verifier.budget is not None:
                _limits(caps.limits, verifier.budget, path + ".verifiers." + verifier.id)
        jobs[job.config.backend.name] = adapter
    return PreparedExecution(plan, study.dataset, MappingProxyType(direct), MappingProxyType(jobs))
