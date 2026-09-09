"""NAT ATIF grading: one native invocation and one retained grading activity.

The local runtime uses NAT's published ``evaluate_atif_fn`` protocol. Native
packages are imported only when that runtime is selected, never by core imports.
"""
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from importlib.metadata import PackageNotFoundError, version
import json
import math
from pathlib import Path
from typing import Protocol

from .. import objects as o
from ..storage.artifacts import ArtifactCache


def _unknown_resources(scope, reason):
    unknown = o.Observation(value=None, status="unknown", reason=reason)
    return o.Resources(cost_scope=scope, cost_usd=unknown, input_tokens=unknown,
                       output_tokens=unknown, human_minutes=unknown)


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


@dataclass(frozen=True, kw_only=True)
class NATInputItem:
    run_id: str
    native_item_id: str
    trajectory: o.ArtifactRef
    references: Mapping
    dependencies: Mapping
    output: object = None


@dataclass(frozen=True, kw_only=True)
class NATNativeResult:
    result: o.ArtifactRef
    resources: o.Resources
    started_at: datetime
    ended_at: datetime
    native_refs: Mapping[str, str]


class NATGradingRuntime(Protocol):
    upstream_ref: o.VersionRef

    async def grade(self, items: tuple[NATInputItem, ...], *, configuration: Mapping,
                    artifacts: ArtifactCache, cost_scope: tuple[str, ...]) -> NATNativeResult: ...


@dataclass(frozen=True, kw_only=True)
class NATDialect:
    upstream_ref: o.VersionRef
    trajectory_alias: str = "native.trajectory"
    configuration_validator: Callable[[Mapping], Mapping] | None = None

    def configuration(self, values):
        return self.configuration_validator(values) if self.configuration_validator else values


class NativeATIFRuntime:
    """Connect an already configured native AtifEvaluator without an agent runner.

    ``configuration`` is the exact versioned grader configuration bound to the
    evaluator instance. Changing MetricSpec.params requires a correspondingly
    configured instance; a request cannot silently change the bound grader.
    """
    def __init__(self, *, evaluator, package_version: str, configuration: Mapping,
                 expected_output: Callable[[NATInputItem], object] | None = None):
        self.evaluator = evaluator
        self.upstream_ref = o.VersionRef(name="nvidia-nat-eval", revision=package_version)
        self.configuration = o.freeze_json(configuration)
        self.expected_output = expected_output

    async def grade(self, items, *, configuration, artifacts, cost_scope):
        try:
            installed = version("nvidia-nat-eval")
        except PackageNotFoundError as exc:
            raise o.ConfigurationError("Install the selected nvidia-nat-eval pin in this runtime or provide a prepared NAT runtime") from exc
        if installed != self.upstream_ref.revision:
            raise o.ConfigurationError(f"NAT version mismatch: expected {self.upstream_ref.revision}, installed {installed}")
        if o.canonical_bytes(configuration) != o.canonical_bytes(self.configuration):
            raise o.ConfigurationError("Metric params differ from the configured native evaluator")
        from nat.atif import ATIFTrajectory
        from nat.plugins.eval.evaluator.atif_evaluator import AtifEvalSample
        samples = []
        for item in items:
            trajectory = ATIFTrajectory.model_validate_json(Path(item.trajectory.uri).read_bytes())
            expected = self.expected_output(item) if self.expected_output else _plain(item.references)
            samples.append(AtifEvalSample(item_id=item.native_item_id, trajectory=trajectory,
                expected_output_obj=expected, output_obj=_plain(item.output),
                metadata={"agent_eval_flow_run_id": item.run_id,
                          "dependency_metrics": {key: {"value": str(row.value) if isinstance(row.value, Decimal) else row.value,
                              "status": row.status, "reason": row.reason} for key, row in item.dependencies.items()}}))
        started = datetime.now(timezone.utc)
        result = await self.evaluator.evaluate_atif_fn(samples)
        ended = datetime.now(timezone.utc)
        raw = artifacts.write_bytes("native.result", result.model_dump_json().encode("utf-8"), "application/json")
        return NATNativeResult(result=raw, started_at=started, ended_at=ended,
            resources=_unknown_resources(cost_scope, "Native EvalOutput does not expose grader usage"), native_refs={})


class NATBatchEvaluator:
    def __init__(self, *, ref, runtime, dialect: NATDialect, artifacts: ArtifactCache):
        if not ref.revision or not dialect.upstream_ref.revision:
            raise o.ConfigurationError("NAT adapter and upstream references require resolved revisions")
        self.ref, self.runtime, self.dialect, self.artifacts = ref, runtime, dialect, artifacts

    async def compute_batch(self, request):
        if getattr(self.runtime, "upstream_ref", None) != self.dialect.upstream_ref:
            raise o.ConfigurationError("NAT runtime identity does not match the selected dialect")
        if not request.items or len({item.run.id for item in request.items}) != len(request.items):
            raise o.ConfigurationError("NAT requires a nonempty batch of distinct run IDs")
        if request.metric.source != o.EvaluatorSource(ref=self.ref, mode="batch"):
            raise o.ConfigurationError("NAT metric source does not identify this batch evaluator")
        if not request.id or not request.config_fingerprint:
            raise o.ConfigurationError("NAT requires allocated activity and configuration identities")
        configuration = self.dialect.configuration(request.metric.params)
        items = []
        for item in request.items:
            trajectory = item.run.artifacts.get(self.dialect.trajectory_alias)
            if trajectory is None:
                raise o.ConfigurationError(f"Run {item.run.id} lacks {self.dialect.trajectory_alias}")
            self.artifacts.verify(trajectory).raise_for_errors()
            with Path(trajectory.uri).open("rb") as stream:
                local = self.artifacts.write_stream("native.trajectory", stream, trajectory.media_type)
            items.append(NATInputItem(run_id=item.run.id, native_item_id=item.run.id, trajectory=local,
                references=item.context.references, dependencies=item.context.measurements, output=item.run.output))
        scopes = {item.context.evaluation_cost_scope for item in request.items}
        if len(scopes) != 1:
            raise o.ConfigurationError("A NAT invocation must have one grading cost scope")
        scope = next(iter(scopes))
        native = await self.runtime.grade(tuple(items), configuration=configuration,
                                         artifacts=self.artifacts, cost_scope=scope)
        self.artifacts.verify(native.result).raise_for_errors()
        with Path(native.result.uri).open("rb") as stream:
            raw_ref = self.artifacts.write_stream("native.result", stream, native.result.media_type)
        payload = json.loads(Path(raw_ref.uri).read_bytes())
        native_rows = payload.get("eval_output_items")
        if not isinstance(native_rows, list):
            raise o.CaptureValidationError("NAT result has no native eval_output_items list")
        expected = {item.native_item_id: item.run_id for item in items}
        rows, seen = [], set()
        for position, row in enumerate(native_rows):
            native_id = row.get("id")
            if not isinstance(native_id, str) or native_id not in expected or native_id in seen:
                raise o.CaptureValidationError(f"NAT returned a duplicate or foreign item ID: {native_id}")
            seen.add(native_id)
            reason = row.get("reasoning")
            error = row.get("error") or (reason.get("error") if isinstance(reason, dict) else None)
            evidence = (o.EvidenceRef(artifact=raw_ref, locator=f"/eval_output_items/{position}", description="Native NAT item result"),)
            value = row.get("score")
            kind = {bool: "bool", int: "int", float: "float", Decimal: "decimal", str: "text"}.get(type(value))
            if not error and (kind != request.metric.output_type or isinstance(value, float) and not math.isfinite(value)):
                raise o.CaptureValidationError(f"NAT score for {native_id} does not match {request.metric.output_type}")
            rows.append(o.Measurement(run_id=expected[native_id], metric=request.metric.id,
                value=None if error else value, status="error" if error else "ok", basis=None if error else "observed",
                reason=str(error) if error else reason if isinstance(reason, str) else json.dumps(reason, ensure_ascii=False),
                evidence=evidence, activity_ids=(request.id,)))
        receipt = {"upstream_ref": {"name": self.runtime.upstream_ref.name, "revision": self.runtime.upstream_ref.revision},
            "trajectory_hashes": sorted(item.trajectory.sha256 for item in items),
            "native_invocation_count": 1, "agent_invocation_count": 0}
        receipt_ref = self.artifacts.write_bytes("integration.receipt", json.dumps(receipt).encode(), "application/json")
        status = "partial" if len(rows) != len(items) or any(row.status != "ok" for row in rows) else "completed"
        if rows and all(row.status == "error" for row in rows):
            status = "error"
        activity = o.EvaluationActivity(id=request.id, evaluator=self.ref, config_fingerprint=request.config_fingerprint,
            run_ids=tuple(item.run.id for item in request.items), status=status, phase="post_run",
            resources=native.resources, started_at=native.started_at, ended_at=native.ended_at,
            native_refs=native.native_refs, artifacts={"native.result": raw_ref, "integration.receipt": receipt_ref})
        return o.BatchMetricOutput(measurements=tuple(rows), activity=activity)
