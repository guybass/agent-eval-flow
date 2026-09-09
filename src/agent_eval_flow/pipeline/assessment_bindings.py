"""Detached registries for one assessment pipeline; construction performs no work."""
from dataclasses import dataclass
from types import MappingProxyType
from collections.abc import Mapping

from .bindings import RuntimeBindings, snapshot


@dataclass(frozen=True)
class AssessmentBindings:
    collectors: Mapping
    configuration_evaluators: Mapping
    references: Mapping
    snapshot_binders: Mapping
    behavior: RuntimeBindings


def snapshot_assessment(*, collectors=None, configuration_evaluators=None,
                        references=None, snapshot_binders=None, backends=None,
                        job_backends=None, evaluators=None, batch_evaluators=None,
                        reducers=None):
    return AssessmentBindings(
        *(MappingProxyType(dict(items or {})) for items in (
            collectors, configuration_evaluators, references, snapshot_binders)),
        behavior=snapshot(backends, job_backends, evaluators, batch_evaluators, reducers),
    )
