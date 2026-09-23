"""Versioned, source-linked tool requests; completion is a separate observation."""
from collections.abc import Mapping
import json
from typing import Literal

from pydantic import TypeAdapter
from pydantic.dataclasses import dataclass

from .base import RecordBase
from .identity import plain
from .records import Event, EvidenceRef, JSONValue, Observation, RECORD_CONFIG, VersionRef


EVENT_KIND = "aef.tools.trace"


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class ToolRequest(RecordBase):
    call_id: str
    execution_id: str
    tool: str
    arguments: JSONValue
    evidence: tuple[EvidenceRef, ...]
    completion: Literal["unknown", "completed", "error"] = "unknown"
    result: JSONValue = None
    result_evidence: tuple[EvidenceRef, ...] = ()

    def __post_init__(self):
        super().__post_init__()
        for name in ("call_id", "execution_id", "tool"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be blank")
        if not self.evidence:
            raise ValueError("Tool requests require source evidence")
        if self.completion != "unknown" and not self.result_evidence:
            raise ValueError("Tool completions require result evidence")
        if self.completion == "unknown" and (self.result is not None or self.result_evidence):
            raise ValueError("An unknown completion cannot carry a result")


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class ToolTrace(RecordBase):
    collector: VersionRef
    boundary: str
    coverage: Observation[bool]
    calls: tuple[ToolRequest, ...]
    scope: str
    schema_version: Literal["1"] = "1"

    def __post_init__(self):
        super().__post_init__()
        if not self.collector.revision or not self.boundary.strip() or not self.scope.strip():
            raise ValueError("Tool traces require a collector revision, boundary, and explicit scope")
        if self.coverage.status != "unknown" and not self.coverage.evidence:
            raise ValueError("Tool coverage declarations require source evidence")
        ids = [(call.execution_id, call.call_id) for call in self.calls]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate tool request identity")

    def evidence(self):
        refs = list(self.coverage.evidence)
        for call in self.calls:
            refs.extend((*call.evidence, *call.result_evidence))
        return tuple(dict.fromkeys(refs))

    def to_event(self, *, id: str, execution_id: str):
        refs = self.evidence()
        return Event(id=id, execution_id=execution_id, kind=EVENT_KIND, at=None,
            fields=plain(self), inputs=refs, source=refs[0] if refs else None)


_ADAPTER = TypeAdapter(ToolTrace)


def decode_tool_trace(event):
    if event.kind != EVENT_KIND:
        return None
    trace = _ADAPTER.validate_json(json.dumps(plain(event.fields), allow_nan=False))
    retained = (*event.inputs, *event.outputs, *((event.source,) if event.source else ()))
    if any(ref not in retained for ref in trace.evidence()):
        raise ValueError("Tool trace evidence is detached from the Event")
    return trace


def trace_for_run(run, boundary):
    """Select one explicitly scoped trace; never infer coverage from run status."""
    traces = []
    for event in run.events:
        if event.kind != EVENT_KIND:
            continue
        value = event.fields.get("boundary")
        if isinstance(value, str) and value != boundary:
            continue
        trace = decode_tool_trace(event)
        if trace.boundary == boundary:
            traces.append(trace)
    if len(traces) > 1:
        raise ValueError("Ambiguous tool traces for the selected boundary")
    if not traces:
        return None
    trace = traces[0]
    executions = {execution.id for execution in run.executions}
    if any(call.execution_id not in executions for call in trace.calls):
        raise ValueError("Tool request refers to an absent execution")
    return trace


def scoring_calls(trace):
    calls = []
    for call in trace.calls:
        if not isinstance(call.arguments, Mapping):
            raise ValueError(f"Malformed or unavailable arguments for {call.call_id}; expected an object")
        calls.append({"tool": call.tool, "args": plain(call.arguments)})
    return calls
