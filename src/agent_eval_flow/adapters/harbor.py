"""Delegate a whole Harbor job and map retained native trial/verifier facts.

Task construction and provider cleanup stay with the selected native runtime.
This module supplies the identity, evidence and resource projection, including
multi-step trials and separate verifier activity accounting.
"""
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
from typing import Protocol

from .. import objects as o
from ..storage.artifacts import ArtifactCache
from .common import AdapterBinding


def _unknown(reason):
    return o.Observation(value=None, status="unknown", reason=reason)


def _time(value):
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    # Some Harbor versions export local naive job timestamps. Do not label an
    # undeclared timezone UTC. Exact original timestamps remain in raw evidence.
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _native_trial_validator(payload):
    try:
        from harbor.models.trial.result import TrialResult
    except ImportError as exc:
        raise o.ConfigurationError("The selected Harbor trial schema is unavailable; provide its pinned native validator or isolated runtime") from exc
    return TrialResult.model_validate(payload).model_dump(mode="json")


@dataclass(frozen=True, kw_only=True)
class PinnedHarborTrialValidator:
    """Reuse an installed native schema only at its explicitly selected pin."""
    package_version: str

    def __call__(self, payload):
        try:
            installed = version("harbor")
        except PackageNotFoundError as exc:
            raise o.ConfigurationError(f"Install harbor=={self.package_version} in the selected schema runtime") from exc
        if installed != self.package_version:
            raise o.ConfigurationError(f"Harbor schema version mismatch: expected {self.package_version}, installed {installed}")
        return _native_trial_validator(payload)


def _retained(cache, reference):
    cache.verify(reference).raise_for_errors()
    with Path(reference.uri).open("rb") as stream:
        return cache.write_stream("native.source", stream, reference.media_type)


@dataclass(frozen=True, kw_only=True)
class HarborGradeChannel:
    channel: str
    evaluator: o.VersionRef
    native_metric: str
    reward_name: str
    output_type: str = "float"


@dataclass(frozen=True, kw_only=True)
class HarborTrialCapture:
    run_id: str
    result: o.ArtifactRef
    output: o.ArtifactRef | None = None
    verifier: o.ArtifactRef | None = None
    trajectory: o.ArtifactRef | None = None
    execution_inventory_complete: o.Observation = field(default_factory=lambda: _unknown("Native execution inventory was not declared"))
    verifier_resources: o.Resources | None = None
    verifier_resources_by_step: Mapping[str, o.Resources] = field(default_factory=dict)
    output_step: str | None = None


@dataclass(frozen=True, kw_only=True)
class HarborNativeCapture:
    native_job_id: str
    result: o.ArtifactRef
    trials: tuple[HarborTrialCapture, ...]
    status: str
    grading_inventory_complete: o.Observation
    effective_config: o.Observation
    started_at: datetime | None = None
    ended_at: datetime | None = None


@dataclass(frozen=True, kw_only=True)
class HarborPreparedJob:
    config: Mapping
    run_ids: tuple[str, ...]


class HarborRuntime(Protocol):
    upstream_ref: o.VersionRef
    def capabilities(self) -> o.NativeJobCapabilities: ...
    async def run_job(self, prepared: HarborPreparedJob, *, request: o.NativeJobRequest,
                      artifacts_dir: Path, on_capture: Callable[[HarborNativeCapture], None]) -> HarborNativeCapture: ...


@dataclass(frozen=True, kw_only=True)
class HarborDialect:
    schema_ref: o.VersionRef
    upstream_ref: o.VersionRef
    prepare_job: Callable[[o.NativeJobRequest, Path], HarborPreparedJob]
    grade_channels: tuple[HarborGradeChannel, ...] = ()
    validate_trial: Callable[[Mapping], Mapping] = _native_trial_validator


class HarborTrialMapper:
    def __init__(self, *, mapper_ref, source_format, artifacts, validate_trial=_native_trial_validator,
                 grade_channels=(), deployment=None, verifier_fingerprints=None):
        self.ref, self.source_format, self.artifacts = mapper_ref, source_format, artifacts
        self.validate_trial, self.grade_channels = validate_trial, tuple(grade_channels)
        self.deployment = deployment or _unknown("Native deployment was not captured")
        self.verifier_fingerprints = verifier_fingerprints or {}
        if len({(channel.channel, channel.evaluator.name, channel.evaluator.revision) for channel in self.grade_channels}) > 1:
            raise o.ConfigurationError("One native Harbor verifier maps to one channel/evaluator with any number of reward metrics")
        if len({channel.native_metric for channel in self.grade_channels}) != len(self.grade_channels):
            raise o.ConfigurationError("Native reward metric mappings must be unique")

    def map_trial(self, captured, request, *, job_id=None):
        if captured.run_id != request.run_id:
            raise o.CaptureValidationError("Native trial mapping names a foreign run")
        raw_ref = _retained(self.artifacts, captured.result)
        payload = json.loads(Path(raw_ref.uri).read_bytes())
        native = self.validate_trial(payload)
        if not isinstance(native, Mapping) or not native.get("id") or not native.get("trial_name"):
            raise o.CaptureValidationError("A retained Harbor trial needs native trial identities")
        # Native schema validation is separate from projection; preserve bytes
        # from before model defaults/coercions, including unknown native fields.
        artifacts = {"native.result": raw_ref}
        for name, source in (("native.verifier", captured.verifier), ("native.trajectory", captured.trajectory), ("native.output", captured.output)):
            if source is not None:
                artifacts[name] = _retained(self.artifacts, source)
        source = o.EvidenceRef(artifact=raw_ref, description="Unmodified native Harbor trial result")
        exception = native.get("exception_info")
        exception_type = exception.get("exception_type", "NativeTrialError") if exception else None
        verifier_failure = exception_type is not None and (exception_type.startswith("Verifier") or exception_type.startswith("Reward"))
        if exception and not verifier_failure:
            status = "timed_out" if "Timeout" in exception_type else "infrastructure_error" if any(s in exception_type for s in ("Environment", "Setup", "Docker", "Connection")) else "agent_error"
        else:
            status = "completed" if native.get("finished_at") else "running"
        error = None if not exception or verifier_failure else o.ErrorRecord(code=exception_type,
            message=exception.get("exception_message", exception_type), evidence=(source,))
        steps = native.get("step_results")
        contexts = [(step.get("step_name", str(index)), step.get("agent_result"), step.get("agent_execution"), step.get("exception_info"))
                    for index, step in enumerate(steps)] if steps else [("main", native.get("agent_result"), native.get("agent_execution"), None)]
        executions = []
        for index, (slot, context, timing, step_error) in enumerate(contexts):
            context, timing = context or {}, timing or {}
            def quantity(name, convert):
                value = context.get(name)
                return _unknown(f"Native agent context does not expose {name}") if value is None else o.Observation(
                    value=convert(value), status="observed", evidence=(source,))
            costs = quantity("cost_usd", lambda value: Decimal(str(value))) if request.policy.cost_scope == ("model",) else _unknown("Native model cost does not establish the requested cost scope")
            resources = o.Resources(cost_usd=costs, cost_scope=request.policy.cost_scope,
                input_tokens=quantity("n_input_tokens", int), output_tokens=quantity("n_output_tokens", int),
                human_minutes=_unknown("Harbor trial does not report human intervention time"))
            executions.append(o.Execution(id=f"{request.run_id}/native/{index}", slot=slot, retry_index=0, parent_id=None,
                status="agent_error" if step_error else status, started_at=_time(timing.get("started_at")),
                ended_at=_time(timing.get("finished_at")), resources=resources,
                effective_config=o.Observation(value=native.get("config", {}), status="observed", evidence=(source,)),
                native_refs={"trial_id": str(native["id"]), "trial_name": native["trial_name"]},
                error=error if not steps else None))
        output_state, output = "unknown", None
        if captured.output is not None:
            output_ref = artifacts["native.output"]
            data = Path(output_ref.uri).read_bytes()
            output = json.loads(data) if output_ref.media_type == "application/json" else data.decode("utf-8")
            output_state = "available"
        output_sources = tuple(execution.id for execution in executions
            if captured.output is not None and ((not steps and captured.output_step is None) or execution.slot == captured.output_step))
        run = o.Run(id=request.run_id, assignment_id=request.assignment.id, status=status,
            cost_scope=request.policy.cost_scope, output=output, output_state=output_state,
            artifacts=artifacts, executions=tuple(executions), started_at=_time(native.get("started_at")),
            ended_at=_time(native.get("finished_at")), environment=self.deployment,
            execution_inventory_complete=captured.execution_inventory_complete,
            output_sources=output_sources, native_refs={"trial_id": str(native["id"]), "trial_name": native["trial_name"]},
            error=error, job_id=job_id)
        projection = o.ProjectionReport(mapper=self.ref, source_format=self.source_format,
            sources=tuple(artifacts.values()), omitted_fields=("native-specific fields retained in raw trial/trajectory",))
        bundles = []
        if not self.grade_channels:
            return run, (), projection
        channel = self.grade_channels[0]
        verifier_passes = [("main", None, "", native, captured.verifier_resources)]
        verifier_passes.extend((f"step-{index}", {"step": step.get("step_name", str(index))},
            f"/step_results/{index}", step, captured.verifier_resources_by_step.get(step.get("step_name", str(index))))
            for index, step in enumerate(steps or ()))
        for pass_id, detail_key, pointer, record, resources in verifier_passes:
            verifier_result = record.get("verifier_result") or {}
            rewards = verifier_result.get("rewards") or {}
            failure = record.get("exception_info")
            code = failure.get("exception_type", "") if failure else ""
            failed = bool(failure and (code.startswith("Verifier") or code.startswith("Reward")))
            if not verifier_result and not failed:
                continue
            activity_id = f"{request.run_id}/verifier/{channel.channel}/{pass_id}"
            if resources is None:
                unknown = _unknown("Native verifier output does not expose grader usage")
                resources = o.Resources(cost_scope=("model",), cost_usd=unknown, input_tokens=unknown,
                    output_tokens=unknown, human_minutes=unknown)
            activity = o.EvaluationActivity(id=activity_id, evaluator=channel.evaluator,
                config_fingerprint=self.verifier_fingerprints.get((request.run_id, channel.channel)),
                run_ids=(request.run_id,), status="error" if failed else "completed", phase="native_verifier",
                resources=resources, artifacts={key: value for key, value in artifacts.items() if key in ("native.result", "native.verifier")},
                native_refs={"trial_id": str(native["id"])})
            grades = []
            for metric in self.grade_channels:
                value = rewards.get(metric.reward_name)
                grade_status = "error" if failed else "missing" if value is None else "ok"
                if grade_status == "ok":
                    if metric.output_type == "bool":
                        if type(value) not in (bool, int, float) or value not in (0, 1):
                            raise o.CaptureValidationError("Boolean reward projection requires a native 0/1 reward")
                        value = bool(value)
                    elif metric.output_type == "float":
                        value = float(value)
                    elif metric.output_type == "decimal":
                        value = Decimal(str(value))
                reward_pointer = metric.reward_name.replace("~", "~0").replace("/", "~1")
                grades.append(o.NativeGrade(id=activity_id + "/" + metric.native_metric, run_id=request.run_id,
                    native_metric=metric.native_metric, activity_id=activity_id, value=value if grade_status == "ok" else None,
                    status=grade_status, basis="observed" if grade_status == "ok" else None, key=detail_key,
                    reason=failure.get("exception_message", code) if failed else f"Native reward field {metric.reward_name}",
                    evidence=(o.EvidenceRef(artifact=raw_ref, locator=f"{pointer}/verifier_result/rewards/{reward_pointer}", description="Native verifier reward"),)))
            bundles.append(o.NativeGradeBundle(id=activity_id + "/bundle", channel=channel.channel,
                projection=projection, grades=tuple(grades), activities=(activity,)))
        return run, tuple(bundles), projection


class HarborJobAdapter:
    def __init__(self, *, binding: AdapterBinding, runtime: HarborRuntime, dialect: HarborDialect):
        self.binding, self.runtime, self.dialect, self.ref = binding, runtime, dialect, binding.ref

    def capabilities(self):
        return self.runtime.capabilities()

    async def run_job(self, request, *, recorder):
        if self.binding.upstream_ref != self.dialect.upstream_ref or self.runtime.upstream_ref != self.binding.upstream_ref:
            raise o.ConfigurationError("Harbor runtime, binding and dialect revisions disagree")
        if request.config.backend != self.ref or request.config.native is None or request.config.native.schema_ref != self.dialect.schema_ref:
            raise o.ConfigurationError("Harbor job requires its selected backend and versioned native dialect")
        run_ids = tuple(row.run_id for row in request.requests)
        if len(run_ids) != len(set(run_ids)):
            raise o.ConfigurationError("Harbor job contains duplicate allocated run IDs")
        verifiers = {verifier.id: verifier for verifier in request.config.verifiers}
        for channel in self.dialect.grade_channels:
            if channel.channel not in verifiers or verifiers[channel.channel].implementation != channel.evaluator:
                raise o.ConfigurationError("Harbor reward mapping must match the declared verifier channel and implementation")
        workspace = self.binding.workspace_root / hashlib.sha256(request.job_id.encode()).hexdigest()
        workspace.mkdir(parents=True, exist_ok=False)
        prepared = self.dialect.prepare_job(request, workspace)
        if set(prepared.run_ids) != set(run_ids) or len(prepared.run_ids) != len(run_ids):
            raise o.ConfigurationError("Prepared native job does not preserve the exact allocated assignment set")
        mapping = {row.run_id: row for row in request.requests}
        mapper = HarborTrialMapper(mapper_ref=self.ref, source_format=self.dialect.schema_ref,
            artifacts=self.binding.artifacts, validate_trial=self.dialect.validate_trial,
            grade_channels=self.dialect.grade_channels, deployment=self.binding.deployment,
            verifier_fingerprints={(row.run_id, channel.channel): o.semantic_fingerprint("native-verifier-config", {
                "verifier": verifiers[channel.channel], "candidate": row.candidate.fingerprint(), "input": row.input,
                "references": tuple(value for value in request.verifier_inputs if value.run_id == row.run_id and value.verifier_id == channel.channel)})
                for row in request.requests for channel in self.dialect.grade_channels})
        def project(native, *, final):
            if len({row.run_id for row in native.trials}) != len(native.trials) or any(row.run_id not in mapping for row in native.trials):
                raise o.CaptureValidationError("Harbor capture contains duplicate or foreign mapped trials")
            result_ref = _retained(self.binding.artifacts, native.result)
            job = o.NativeJobRecord(id=request.job_id, planned_job_id=request.planned_job_id, backend=self.ref,
                assignment_ids=tuple(row.assignment.id for row in request.requests), status=native.status,
                started_at=native.started_at, ended_at=native.ended_at, native_refs={"job_id": native.native_job_id},
                artifacts={"native.result": result_ref}, effective_config=native.effective_config)
            runs, bundles, projections, links = [], [], [], []
            for captured in native.trials:
                run, grades, projection = mapper.map_trial(captured, mapping[captured.run_id], job_id=request.job_id)
                runs.append(run)
                bundles.extend(grades)
                projections.append(projection)
                links.append(o.NativeRunLink(run_id=run.id, assignment_id=run.assignment_id, native_refs=run.native_refs))
            from dataclasses import replace
            receipt = self.binding.artifacts.write_bytes("integration.receipt", json.dumps({
                "upstream_ref": {"name": self.runtime.upstream_ref.name, "revision": self.runtime.upstream_ref.revision},
                "native_job_id": native.native_job_id}).encode(), "application/json")
            job = replace(job, links=tuple(links), artifacts={**job.artifacts, "integration.receipt": receipt})
            recorder.record_job(job)
            for run in runs:
                recorder.record_run(run)
            if final:
                for bundle in bundles:
                    recorder.record_grade_bundle(bundle)
            return o.NativeJobOutput(job=job, runs=tuple(runs), native_grades=tuple(bundles) if final else (),
                projections=tuple(projections), grading_inventory_complete=native.grading_inventory_complete)
        native = await self.runtime.run_job(prepared, request=request, artifacts_dir=workspace,
                                           on_capture=lambda value: project(value, final=False))
        return project(native, final=True)
