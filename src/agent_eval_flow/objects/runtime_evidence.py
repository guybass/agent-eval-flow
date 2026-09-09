"""Opt-in, versioned observations carried by the existing Event contract.

Collectors own the meaning of phase, boundary and coverage. Native events stay
opaque; this schema neither reconstructs uncaptured work nor modifies a runtime.
"""
from datetime import datetime
import json
from typing import Literal

from pydantic import TypeAdapter
from pydantic.dataclasses import dataclass

from .base import RecordBase
from .identity import plain
from .records import Event, JSONValue, Observation, RECORD_CONFIG, VersionRef


EVENT_KIND = "aef.runtime.observation"


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class RuntimeObservation(RecordBase):
    collector: VersionRef
    subject: str
    phase: str
    boundary: str
    declared: Observation[JSONValue]
    observed: Observation[JSONValue]
    coverage: Literal["complete", "partial", "unknown"]
    component_id: str | None = None
    call_id: str | None = None
    transformations: tuple[str, ...] = ()
    schema_version: Literal["1"] = "1"

    def __post_init__(self):
        super().__post_init__()
        for name in ("subject", "phase", "boundary"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        if not self.collector.revision or not self.collector.revision.strip():
            raise ValueError("A runtime observation requires a versioned collector")
        for name in ("component_id", "call_id"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} must not be empty when supplied")
        for name in ("declared", "observed"):
            value = getattr(self, name)
            if value.status != "unknown" and not value.evidence:
                raise ValueError(f"{name} values require supporting evidence")

    def to_event(self, *, id: str, execution_id: str, at: datetime | None = None) -> Event:
        """Project without I/O; also retain typed references for evidence tooling."""
        sources = (*self.observed.evidence, *self.declared.evidence)
        return Event(id=id, execution_id=execution_id, kind=EVENT_KIND, at=at,
            fields=plain(self), inputs=self.declared.evidence, outputs=self.observed.evidence,
            source=sources[0] if sources else None)


_ADAPTER = TypeAdapter(RuntimeObservation)


def decode_runtime_observation(event: Event) -> RuntimeObservation | None:
    """Decode only opted-in events; reject invalid schema or detached evidence."""
    if event.kind != EVENT_KIND:
        return None
    record = _ADAPTER.validate_json(json.dumps(plain(event.fields), allow_nan=False))
    retained = (*event.inputs, *event.outputs, *((event.source,) if event.source else ()))
    if any(ref not in retained for ref in (*record.declared.evidence, *record.observed.evidence)):
        raise ValueError("Runtime observation evidence is detached from its typed Event references")
    return record
