"""Deterministic checks of recorded runtime observations, usable during regrade.

These checks describe selected *recorded* observations. Complete value coverage
does not prove complete runtime-call coverage. Expected phase/call selectors and
optional counts belong to the caller; causal explanations are never inferred.
"""
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .. import objects as o
from ..objects.identity import plain, semantic_fingerprint
from ..objects.runtime_evidence import EVENT_KIND, decode_runtime_observation
from ..objects.values import zero_resources


class _Request(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    subject: str = Field(min_length=1)
    phase: str = Field(min_length=1)
    boundary: str = Field(min_length=1)
    component_id: str | None = None
    call_id: str | None = None
    operator: Literal["equals", "contains", "includes", "at_most", "at_least"] = "equals"
    expected: o.JSONValue = None
    selection: Literal["all", "first", "last"] = "all"
    expected_count: int | None = Field(default=None, gt=0)


def _same(left, right):
    return semantic_fingerprint("runtime-value", left) == semantic_fingerprint("runtime-value", right)


def _compare(actual, expected, operator):
    if operator == "equals":
        return _same(actual, expected)
    if operator == "contains":
        if not isinstance(actual, str) or not isinstance(expected, str):
            raise ValueError("contains requires text values")
        return expected in actual
    if operator == "includes":
        if not isinstance(actual, (list, tuple)) or not isinstance(expected, (list, tuple)):
            raise ValueError("includes requires arrays")
        # Multiplicity matters: two required tools/items cannot match one item.
        remaining = list(actual)
        for item in expected:
            position = next((i for i, value in enumerate(remaining) if _same(item, value)), None)
            if position is None:
                return False
            remaining.pop(position)
        return True
    if type(actual) not in (int, float) or type(expected) not in (int, float):
        raise ValueError(f"{operator} requires numbers, excluding booleans")
    return actual <= expected if operator == "at_most" else actual >= expected


def _preview(value):
    text = json.dumps(plain(value), ensure_ascii=False, allow_nan=False)
    return text if len(text) <= 160 else text[:157] + "..."


def _evidence(*groups):
    result = []
    for group in groups:
        for item in group:
            if item not in result:
                result.append(item)
    return tuple(result)


class RuntimeEvidenceEvaluator:
    """Compare versioned observation records through ordinary MetricSpec params."""
    ref = o.VersionRef(name="agent-eval-flow.runtime-evidence", revision="1")

    def compute(self, spec, context, run):
        def row(status, reason, *, value=None, event=None, evidence=()):
            return o.Measurement(run_id=run.id, metric=spec.id, status=status, value=value,
                basis="observed" if status == "ok" else None, reason=reason, evidence=evidence,
                key={"event": event.id} if event is not None else None)

        def output(task, details=()):
            return o.MetricOutput(task=task, details=tuple(details),
                evaluation_resources=zero_resources(context.evaluation_cost_scope))

        try:
            request = _Request.model_validate(dict(spec.params))
            if spec.output_type != "bool":
                raise ValueError("Runtime evidence checks require output_type='bool'")
            for name in ("subject", "phase", "boundary", "component_id", "call_id"):
                value = getattr(request, name)
                if value is not None and not value.strip():
                    raise ValueError(f"{name} must not be blank")
        except ValueError as exc:
            return output(row("error", "Invalid runtime evidence check: " + str(exc)))

        scope = f"{request.subject} at {request.phase} / {request.boundary}"
        selected = []
        for event in run.events:
            if event.kind != EVENT_KIND:
                continue
            # Skip proven out-of-scope records, but do not hide a malformed
            # selector whose scope cannot be established.
            if any(isinstance(event.fields.get(name), str) and event.fields[name] != getattr(request, name)
                   for name in ("subject", "phase", "boundary", "component_id", "call_id")
                   if getattr(request, name) is not None):
                continue
            try:
                observation = decode_runtime_observation(event)
                if any(getattr(observation, name) != getattr(request, name)
                       for name in ("subject", "phase", "boundary", "component_id", "call_id")
                       if getattr(request, name) is not None):
                    continue
                selected.append((event, observation, None))
            except ValueError as exc:
                selected.append((event, None, str(exc)))

        count = sum(observation is not None for _, observation, _ in selected)
        # A count requirement concerns the entire matching population, even if
        # only its first/last observation will be compared. Unparseable records
        # cannot establish membership or disappear behind that selection.
        population_errors = [item for item in selected if item[2] is not None] if request.expected_count is not None else []
        if request.selection == "first":
            selected = selected[:1]
        elif request.selection == "last":
            selected = selected[-1:]
        selected_ids = {event.id for event, _, _ in selected}
        selected.extend(item for item in population_errors if item[0].id not in selected_ids)
        details = []
        for event, observation, error in selected:
            if error is not None:
                details.append(row("error", f"{scope}: malformed runtime observation: {error}", event=event,
                    evidence=_evidence(event.inputs, event.outputs)))
                continue
            refs = _evidence(observation.declared.evidence, observation.observed.evidence)
            if observation.coverage != "complete" or observation.observed.status != "observed":
                details.append(row("missing", f"{scope}: observed value coverage={observation.coverage}, "
                    f"basis={observation.observed.status}; {observation.observed.reason or 'No complete observed value'}",
                    event=event, evidence=refs))
                continue
            if "expected" in spec.params:
                expected = request.expected
            elif observation.declared.status == "observed":
                expected = observation.declared.value
            else:
                details.append(row("missing", f"{scope}: declared value is {observation.declared.status}; "
                    f"{observation.declared.reason or 'No observed declaration'}", event=event, evidence=refs))
                continue
            try:
                value = _compare(observation.observed.value, expected, request.operator)
                details.append(row("ok", f"{scope}: expected {request.operator} {_preview(expected)}; "
                    f"observed {_preview(observation.observed.value)}", value=value, event=event, evidence=refs))
            except ValueError as exc:
                details.append(row("error", f"{scope}: {exc}", event=event, evidence=refs))

        refs = _evidence(*(detail.evidence for detail in details))
        if any(detail.status == "error" for detail in details):
            task = row("error", f"{scope}: selected evidence or comparison is invalid; see details", evidence=refs)
        elif request.expected_count is not None and count > request.expected_count:
            task = row("error", f"{scope}: expected {request.expected_count} matching recorded observations, found {count}", evidence=refs)
        elif any(detail.status == "ok" and detail.value is False for detail in details):
            task = row("ok", f"{scope}: a complete recorded observation violates the requirement; see details",
                value=False, evidence=refs)
        elif request.expected_count is not None and count < request.expected_count:
            task = row("missing",
                f"{scope}: expected {request.expected_count} matching recorded observations, found {count}", evidence=refs)
        elif not details or any(detail.status != "ok" for detail in details):
            task = row("missing", f"{scope}: no complete supported result for the selected recorded observations", evidence=refs)
        else:
            task = row("ok", f"{scope}: all {len(details)} selected recorded observations match; "
                "runtime-wide coverage is not asserted", value=True, evidence=refs)
        return output(task, details)
