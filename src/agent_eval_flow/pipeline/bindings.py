"""Immutable registry membership with caller-owned implementation identity."""
from dataclasses import dataclass
from collections.abc import Mapping
from types import MappingProxyType
from agent_eval_flow.objects import (
    BackendAdapter, NativeJobAdapter, MetricEvaluator, BatchMetricEvaluator, SummaryReducer,
)


@dataclass(frozen=True)
class RuntimeBindings:
    backends: Mapping[str, BackendAdapter]
    job_backends: Mapping[str, NativeJobAdapter]
    evaluators: Mapping[str, MetricEvaluator]
    batch_evaluators: Mapping[str, BatchMetricEvaluator]
    reducers: Mapping[str, SummaryReducer]


def snapshot(backends=None, job_backends=None, evaluators=None, batch_evaluators=None, reducers=None) -> RuntimeBindings:
    return RuntimeBindings(*(MappingProxyType(dict(registry or {})) for registry in (
        backends, job_backends, evaluators, batch_evaluators, reducers,
    )))
