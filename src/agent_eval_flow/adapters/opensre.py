"""OpenSRE AgentSession capture with typed runtime events and native identities.

The native harness owns tool selection and all reasoning iterations. A prepared
factory installs the existing ``Agent.on_runtime_event`` callback when building
each native Agent; tuple observer reconstruction is deliberately unsupported.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from functools import partial
import hashlib
import inspect
import importlib
import io
import json
from pathlib import Path
import subprocess
import shutil
import sys
import threading
from typing import Callable, Mapping
from uuid import uuid4
import zipfile

import anyio

from .. import objects as o
from ..objects.values import observed, unknown
from .openkritt import artifact_bytes, json_bytes

UPSTREAM_REVISION = "1a81e1a1b378ecc9aeb211fcaeecb0f5c93548e4"
ENTRYPOINT = "core.agent_harness.AgentSession.chat_until_goal"


def native_json(value):
    """Serialize supported native records without turning unknown objects into text."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return {field.name: native_json(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: native_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [native_json(item) for item in value]
    if isinstance(value, (set, frozenset)):
        # Native SessionGoal.completed is a frozenset. JSON represents its
        # members as a deterministic array, without inventing an ordering fact.
        items = [native_json(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False, allow_nan=False))
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or type(value) in {bool, int, float, str}:
        return value
    raise TypeError("Unsupported native event value: " + type(value).__name__)


class RuntimeEventRecorder:
    """Append every native event as it arrives, using one scope per native loop."""
    def __init__(self, *, cache, invocation_id, stream_path=None):
        self.invocation_id, self.cache = invocation_id, cache
        self.writer = cache.open_writer("native.trace", "application/x-ndjson")
        self._lock, self._sequence, self._loops = threading.Lock(), 0, set()
        self._committed = None
        self._mirror = Path(stream_path).open("wb") if stream_path is not None else None
        self.failures = []

    def for_loop(self, loop_id):
        if not isinstance(loop_id, str) or not loop_id:
            raise ValueError("A native loop scope is required")
        with self._lock:
            if loop_id in self._loops:
                raise ValueError("Runtime observer scope already belongs to another native loop")
            self._loops.add(loop_id)
        def callback(event):
            payload = native_json(event)
            if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
                raise ValueError("Expected the upstream typed RuntimeEvent, not a tuple observer payload")
            with self._lock:
                envelope = {"sequence": self._sequence, "invocation_id": self.invocation_id,
                    "loop_id": loop_id, "event": payload}
                line = json_bytes(envelope) + b"\n"
                self.writer.write(line)
                if self._mirror is not None:
                    self._mirror.write(line)
                    self._mirror.flush()
                self._sequence += 1
        def guarded(event):
            try:
                callback(event)
            except Exception as exc:
                self.failures.append(f"{type(exc).__name__}: {exc}")
                raise
        return guarded

    @property
    def event_count(self):
        return self._sequence

    def commit(self):
        if self._committed is None:
            if self._mirror is not None:
                self._mirror.close()
            self._committed = self.writer.commit()
        return self._committed

    def ingest(self, data):
        """Retain an unchanged stream fetched from the same contained invocation."""
        with self._lock:
            if self._sequence:
                raise ValueError("Cannot mix a fetched native stream with local observer events")
            rows = [json.loads(line) for line in data.splitlines() if line.strip()]
            if any(row.get("invocation_id") != self.invocation_id or row.get("sequence") != index for index, row in enumerate(rows)):
                raise o.CaptureValidationError("Fetched native runtime stream identity/order mismatch")
            self.writer.write(data)
            self._sequence = len(rows)


@dataclass(frozen=True)
class SessionHandle:
    """Native, already configured AgentSession and actual runtime observations.

The factory owns native tool hooks/provider/session setup. ``close`` releases
only this session. ``artifacts`` is evaluated after the session runs, allowing
tools to expose their actual audit/report files without importing test fixtures.
"""
    session: object
    session_id: str
    effective_config: o.Observation
    model: o.Observation
    deployment: o.Observation
    artifacts: Callable[[], Mapping[str, Path]]
    close: Callable[[], None]
    final_turn: Callable[[object], object]
    resources: Callable[[], o.Resources]
    process: Mapping | None = None


@dataclass(frozen=True)
class SessionCapture:
    invocation_id: str
    turn: bytes | None
    status: str
    started_at: datetime
    ended_at: datetime | None
    upstream_ref: o.VersionRef
    effective_config: o.Observation
    model: o.Observation
    deployment: o.Observation
    resources: o.Resources
    inventory_complete: o.Observation
    trace_complete: o.Observation
    artifacts: Mapping[str, Path | o.ArtifactRef]
    stderr: bytes = b""
    process: Mapping | None = None
    error: o.ErrorRecord | None = None


class EmbeddedOpenSRERuntime:
    """Drive an actual prepared AgentSession, without claiming a hard thread stop.

Use this inside a contained worker/process for a hard assignment deadline.
``factory(request=..., workspace=..., observer=...)`` returns SessionHandle and
installs ``observer.for_loop(native_scope)`` at each native Agent construction.
No patched replacement reasoning loop is introduced.
"""
    def __init__(self, *, factory, checkout, ref=None):
        self.factory, self.checkout = factory, Path(checkout).resolve()
        process = subprocess.run(["git", "-c", "safe.directory=" + self.checkout.as_posix(), "-C", str(self.checkout),
            "rev-parse", "HEAD"], capture_output=True, check=False, text=True)
        revision = process.stdout.strip()
        if process.returncode or revision != UPSTREAM_REVISION:
            raise o.ConfigurationError("Embedded OpenSRE checkout must resolve to the supported immutable revision")
        dirty = subprocess.run(["git", "-c", "safe.directory=" + self.checkout.as_posix(), "-C", str(self.checkout),
            "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True, check=False)
        if dirty.returncode or dirty.stdout.strip():
            raise o.ConfigurationError("Observed OpenSRE checkout has modified tracked source")
        self.upstream_ref = ref or o.VersionRef(name="opensre", revision=revision)

    def capabilities(self):
        return o.BackendCapabilities(wall_time_limit=False, token_limit=False, cost_limit=False, reset_state=True)

    async def run_session(self, *, request, workspace, observer):
        return await anyio.to_thread.run_sync(partial(self._run, request=request, workspace=workspace, observer=observer))

    def _run(self, *, request, workspace, observer):
        started = datetime.now(timezone.utc)
        handle = self.factory(request=request, workspace=workspace, observer=observer)
        if not isinstance(handle, SessionHandle) or not callable(getattr(handle.session, "chat_until_goal", None)):
            raise o.ConfigurationError("Prepared factory must return a SessionHandle with the native AgentSession")
        expected_source = (self.checkout / "core" / "agent_harness" / "harness.py").resolve()
        native_class = next((cls for cls in type(handle.session).__mro__ if cls.__module__ == "core.agent_harness.harness"), None)
        if native_class is None or Path(inspect.getfile(native_class)).resolve() != expected_source:
            raise o.ConfigurationError("Prepared session was not loaded from the verified native OpenSRE checkout")
        if not handle.session_id:
            raise o.ConfigurationError("Prepared session must expose its native session ID")
        turn, status, error = None, "infrastructure_error", None
        captured_artifacts = {}
        resources = None
        try:
            task = request.input.tables["units"][0]
            prompt = task[request.candidate.settings.get("prompt_column", "prompt")]
            # The native goal result contains host/version-specific turn history;
            # the explicit accessor preserves the native final TurnResult.
            native_result = handle.session.chat_until_goal(prompt)
            turn = json_bytes(native_json(handle.final_turn(native_result)))
            status = turn_status(json.loads(turn))
        except Exception as exc:
            error = o.ErrorRecord(code="opensre_runtime_error", message=f"{type(exc).__name__}: {exc}")
        finally:
            try:
                resources = handle.resources()
                for name, path in handle.artifacts().items():
                    path = Path(path)
                    media = "application/x-ndjson" if path.suffix == ".jsonl" else "application/json" if path.suffix == ".json" else "text/plain"
                    captured_artifacts[name] = observer.cache.write_bytes(name, path.read_bytes(), media)
            except Exception as exc:
                error = o.ErrorRecord(code="opensre_capture_error", message=f"Capture receipt failed: {type(exc).__name__}: {exc}")
            try:
                handle.close()
            except Exception as exc:
                error = o.ErrorRecord(code="opensre_cleanup_error", message=f"Native session cleanup failed: {type(exc).__name__}: {exc}")
        if resources is None:
            resources = o.Resources(cost_scope=request.policy.cost_scope, cost_usd=unknown("Native usage receipt unavailable"),
                input_tokens=unknown("Native usage receipt unavailable"), output_tokens=unknown("Native usage receipt unavailable"),
                human_minutes=unknown("Native human effort unavailable"))
        return SessionCapture(invocation_id=observer.invocation_id, turn=turn, status=status,
            started_at=started, ended_at=datetime.now(timezone.utc), upstream_ref=self.upstream_ref,
            effective_config=handle.effective_config, model=handle.model, deployment=handle.deployment,
            resources=resources, inventory_complete=observed(True, "One native harness invocation captured as an aggregate"),
            trace_complete=observed(bool(observer.event_count) and not observer.failures,
                "Typed runtime events were observed without recorder errors" if observer.event_count and not observer.failures
                else "Native runtime observer emitted no events or reported a capture error"),
            artifacts=captured_artifacts, process=handle.process, error=error)


class ProcessOpenSRERuntime:
    """Run this module's native-session worker through verified containment.

The prepared Python environment must contain the pinned OpenSRE checkout and
the operator's small SessionHandle factory. The supervisor supplies the hard
deadline; the native AgentSession supplies the entire agent loop.
"""
    def __init__(self, *, connection, checkout, session_factory, upstream_ref, deployment=None):
        if not isinstance(session_factory, str) or ":" not in session_factory:
            raise o.ConfigurationError("session_factory must identify an installed module:callable")
        self.connection, self.checkout, self.session_factory = connection, Path(checkout).resolve(), session_factory
        self.upstream_ref = upstream_ref
        self.deployment = deployment or unknown("Deployment is observed inside the prepared native worker")

    def capabilities(self):
        return o.BackendCapabilities(wall_time_limit=self.connection.supervisor.hard_wall_time_limit,
            token_limit=False, cost_limit=False, reset_state=True)

    async def run_session(self, *, request, workspace, observer):
        from pydantic import TypeAdapter
        from .process import ProcessLaunch
        argv = (str(self.connection.executable), "-m", "agent_eval_flow.adapters.opensre", "--worker")
        payload = {"request": TypeAdapter(o.RunRequest).dump_json(request).decode(),
            "checkout": str(self.checkout), "session_factory": self.session_factory,
            "invocation_id": observer.invocation_id, "upstream_name": self.upstream_ref.name}
        process = await self.connection.supervisor.execute(ProcessLaunch(argv=argv, stdin=json_bytes(payload),
            workspace=workspace, environment=self.connection.environment, wall_time_s=request.policy.budget.wall_time_s,
            capture_files={"session.capture": "session.capture.json", "native.trace": "runtime.jsonl"}))
        trace_ref = process.artifacts.get("native.trace")
        if trace_ref is not None:
            observer.ingest(artifact_bytes(trace_ref))
        source = process.artifacts.get("session.capture")
        if source is not None:
            capture = TypeAdapter(SessionCapture).validate_json(artifact_bytes(source))
            if capture.upstream_ref != self.upstream_ref or capture.invocation_id != observer.invocation_id:
                raise o.CaptureValidationError("Contained worker returned a different native revision/invocation")
            if process.deadline_exceeded:
                capture = replace(capture, status="timed_out" if process.stop.confirmed else "infrastructure_error")
            return replace(capture, started_at=process.started_at, ended_at=process.ended_at,
                process={"argv": list(argv), "exit_code": process.exit_code},
                stderr=artifact_bytes(process.stderr), artifacts={**capture.artifacts, "native.stdout": process.stdout},
                trace_complete=observed(capture.trace_complete.value is True and process.output_complete.value is True,
                    "Native callback and supervising process capture completeness"))
        reason = "Contained native worker did not produce a session receipt"
        unavailable = unknown(reason)
        return SessionCapture(invocation_id=observer.invocation_id, turn=None,
            status="timed_out" if process.deadline_exceeded and process.stop.confirmed else "infrastructure_error",
            started_at=process.started_at, ended_at=process.ended_at, upstream_ref=self.upstream_ref,
            effective_config=unavailable, model=unavailable, deployment=self.deployment,
            resources=o.Resources(cost_scope=request.policy.cost_scope, cost_usd=unavailable,
                input_tokens=unavailable, output_tokens=unavailable, human_minutes=unavailable),
            inventory_complete=unknown(reason), trace_complete=observed(False, reason),
            artifacts={"native.stdout": process.stdout}, stderr=artifact_bytes(process.stderr),
            process={"argv": list(argv), "exit_code": process.exit_code},
            error=o.ErrorRecord(code="opensre_worker_capture", message=reason))


def turn_status(turn):
    if not isinstance(turn, dict) or not isinstance(turn.get("action_result"), dict):
        raise ValueError("Native TurnResult does not contain action_result")
    action = turn["action_result"]
    if action.get("cancelled") is True or turn.get("final_intent") == "cli_agent_cancelled":
        return "cancelled"
    if action.get("hit_iteration_cap") is True:
        return "agent_error"
    if action.get("accounting_status") == "completed":
        return "completed"
    if action.get("accounting_status") == "not_run":
        return "agent_error"
    raise ValueError("Unrecognized native OpenSRE accounting outcome")


def map_runtime_events(trace, *, run_id, execution_id, invocation_id):
    events, keys = [], set()
    for number, line in enumerate(artifact_bytes(trace).splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        sequence = row["sequence"]
        if type(sequence) is not int or sequence != len(events) or sequence in keys:
            raise o.CaptureValidationError("Native runtime events require complete, unique capture ordering")
        if row.get("invocation_id") != invocation_id or not row.get("loop_id"):
            raise o.CaptureValidationError("Native runtime event belongs to a different invocation or lacks loop identity")
        native = row["event"]
        if not isinstance(native, dict) or not isinstance(native.get("type"), str):
            raise o.CaptureValidationError("Malformed typed runtime event")
        keys.add(sequence)
        source = o.EvidenceRef(artifact=trace, locator=f"line:{number}", description="Native OpenSRE runtime event")
        kind = native["type"]
        fields = {"native": native, "native_sequence": sequence, "loop_id": row["loop_id"]}
        if kind == "tool_execution_end":
            kind = "tool_call"
            fields.update({"native_id": native["tool_call_id"], "name": native["tool_name"],
                "arguments": native["args"], "result": native["result"], "is_error": native["is_error"]})
        events.append(o.Event(id=f"{run_id}/native/{sequence}", execution_id=execution_id,
            kind=kind, at=None, fields=fields, source=source, inputs=(source,), outputs=(source,)))
    return tuple(events)


class OpenSREBackend:
    def __init__(self, *, binding, runtime):
        self.binding, self.runtime, self.ref = binding, runtime, binding.ref

    def capabilities(self):
        return self.runtime.capabilities()

    def run(self, request, *, recorder):
        return anyio.from_thread.run(self.arun, request, recorder)

    async def arun(self, request, recorder):
        required = request.candidate.settings.get("required_upstream_revision", UPSTREAM_REVISION)
        if required != UPSTREAM_REVISION or self.runtime.upstream_ref != self.binding.upstream_ref or self.binding.upstream_ref.revision != required:
            raise o.ConfigurationError("Prepared OpenSRE runtime differs from supported declared revision")
        workspace = Path(self.binding.workspace_root).resolve() / uuid4().hex
        workspace.mkdir(parents=True, exist_ok=False)
        invocation_id = uuid4().hex
        observer = RuntimeEventRecorder(cache=self.binding.artifacts, invocation_id=invocation_id)
        try:
            capture = await self.runtime.run_session(request=request, workspace=workspace, observer=observer)
        except BaseException:
            # Preserve bytes emitted before a failure; cancellation is not an outcome.
            recorder.record_artifact("native.trace", observer.commit())
            raise
        trace = observer.commit()
        recorder.record_artifact("native.trace", trace)
        if capture.invocation_id != invocation_id or capture.upstream_ref != self.binding.upstream_ref:
            raise o.CaptureValidationError("Native session capture identity/revision mismatch")
        result = self.map_capture(request, capture, trace, recorder=recorder)
        # Materialized cache references now outlive the transient native workspace.
        root = Path(self.binding.workspace_root).resolve()
        if workspace.resolve().parent != root or workspace.is_symlink():
            raise o.StorageError("Refusing cleanup outside the owned invocation workspace")
        if result.error is not None and result.error.code == "opensre_capture_incomplete":
            return replace(result, error=replace(result.error,
                message=result.error.message + "; original workspace retained at " + str(workspace)))
        try:
            shutil.rmtree(workspace)
        except OSError as exc:
            return replace(result, error=o.ErrorRecord(code="opensre_cleanup_error",
                message="Native outcome preserved; workspace cleanup failed: " + str(exc)))
        return result

    def map_capture(self, request, capture, trace, *, recorder):
        cache, artifacts = self.binding.artifacts, {"native.trace": trace}
        export_issues = []
        def store(name, data, media="application/json"):
            ref = cache.write_bytes(name, data, media)
            artifacts[name] = ref
            recorder.record_artifact(name, ref)
            return ref
        def retain(name, data, media="application/json"):
            try:
                return store(name, data, media)
            except (OSError, o.StorageError) as exc:
                export_issues.append(f"{name}: {type(exc).__name__}: {exc}")
                return None
        output = None
        if capture.turn is not None:
            output = json.loads(capture.turn)
            actual = turn_status(output)
            if capture.status == "completed" and actual != "completed":
                raise o.CaptureValidationError("Session claims completion but native TurnResult does not")
        if output is None and capture.status == "completed":
            raise o.CaptureValidationError("A completed native session must retain its TurnResult")
        execution_id = request.run_id + "/session"
        execution = o.Execution(id=execution_id, slot="native-session", retry_index=0, parent_id=None,
            role="main", status=capture.status, started_at=capture.started_at, ended_at=capture.ended_at,
            effective_config=capture.effective_config, resources=capture.resources,
            native_refs={"invocation_id": capture.invocation_id}, error=capture.error)
        # Publish the evidenced terminal attempt before optional retrieval/ZIP work.
        recorder.record_execution(execution)
        if capture.turn is not None:
            retain("native.turn", capture.turn)
        events = map_runtime_events(trace, run_id=request.run_id, execution_id=execution_id, invocation_id=capture.invocation_id)
        for event in events:
            recorder.record_event(event)
        retain("native.stderr", capture.stderr, "text/plain")
        for name, value in capture.artifacts.items():
            if name in artifacts or name == "native.receipt":
                raise o.CaptureValidationError("Native capture attempted to overwrite a reserved artifact alias")
            try:
                if isinstance(value, o.ArtifactRef):
                    data, media = artifact_bytes(value), value.media_type
                else:
                    path = Path(value)
                    data = path.read_bytes()
                    media = "application/x-ndjson" if path.suffix == ".jsonl" else "application/json" if path.suffix == ".json" else "text/plain"
                retain(name, data, media)
            except (OSError, o.StorageError) as exc:
                export_issues.append(f"{name}: {type(exc).__name__}: {exc}")
        tool_receipt_count = None
        if "incident.tool_audit" in artifacts:
            try:
                tool_receipt_count = len([line for line in artifact_bytes(artifacts["incident.tool_audit"]).splitlines() if line.strip()])
            except (OSError, o.StorageError) as exc:
                export_issues.append("incident.tool_audit: " + str(exc))
        receipt = {"schema_version": "aef-opensre-investigation-receipt/1", "invocation_id": capture.invocation_id,
            "agent": {"revision": capture.upstream_ref.revision, "entrypoint": ENTRYPOINT},
            "deployment": capture.deployment.value, "model": capture.model.value,
            "process": capture.process,
            "capture": {"complete": capture.trace_complete.status == "observed" and capture.trace_complete.value is True,
                "event_count": len(events), "runtime_callback": "core.agent.Agent.on_runtime_event",
                "tool_receipt_count": tool_receipt_count, "artifacts_complete": not export_issues,
                "artifact_errors": list(export_issues)}}
        retain("native.receipt", json_bytes(receipt))
        conventional = {"native.turn": "native/turn.json", "native.trace": "native/runtime.jsonl",
            "native.receipt": "native/receipt.json", "native.stderr": "native/stderr.txt",
            "incident.tool_audit": "incident/tool-audit.jsonl", "incident.report": "incident/investigation.json",
            "incident.source_logs": "inputs/HDFS_2k.log", "incident.context": "inputs/context.json",
            "incident.provenance": "inputs/SOURCES.json", "incident.license": "inputs/LOGHUB_LICENSE"}
        payload = io.BytesIO()
        mapping = {name: conventional.get(name, "extra/" + str(i)) for i, name in enumerate(artifacts)}
        try:
            with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, ref in artifacts.items():
                    archive.writestr(mapping[name], artifact_bytes(ref))
                archive.writestr("manifest.json", json_bytes({"invocation_id": capture.invocation_id,
                    "files": {mapping[name]: ref.sha256 for name, ref in artifacts.items()}}))
            retain(request.candidate.settings.get("artifact_bundle", "native.bundle"), payload.getvalue(), "application/zip")
        except (OSError, o.StorageError, zipfile.BadZipFile) as exc:
            export_issues.append("Evidence bundle: " + str(exc))
        error = o.ErrorRecord(code="opensre_capture_incomplete", message="; ".join(
            ([capture.error.message] if capture.error else []) + export_issues)) if export_issues else capture.error
        return o.Run(id=request.run_id, assignment_id=request.assignment.id, status=capture.status,
            cost_scope=request.policy.cost_scope, output=output, output_state="available" if capture.turn is not None else "unknown",
            artifacts=artifacts, executions=(execution,), started_at=capture.started_at, ended_at=capture.ended_at,
            environment=capture.deployment, execution_inventory_complete=capture.inventory_complete,
            output_sources=(execution_id,) if capture.turn is not None else (),
            native_refs={"invocation_id": capture.invocation_id}, events=events, error=error)


def make_backend(config, *, workspace):
    """Live-profile hook: connect the operator's prepared native session runtime."""
    from .common import AdapterBinding
    from ..storage.artifacts import ArtifactCache
    factory_path = config.get("runtime_factory")
    if not isinstance(factory_path, str) or ":" not in factory_path:
        raise o.ConfigurationError("OpenSRE profile requires runtime_factory='module:callable' for its prepared observed native harness")
    module, name = factory_path.split(":", 1)
    runtime = getattr(importlib.import_module(module), name)(config, workspace=Path(workspace))
    binding = AdapterBinding(ref=o.VersionRef(**config["backend_ref"]), upstream_ref=runtime.upstream_ref,
        workspace_root=Path(workspace) / "sessions", artifacts=ArtifactCache(Path(workspace) / "evidence"),
        deployment=getattr(runtime, "deployment", unknown("Deployment is observed by the native invocation")))
    return OpenSREBackend(binding=binding, runtime=runtime)


def _worker_main():
    """Contained-process entry point; stdout remains untouched native diagnostics."""
    from pydantic import TypeAdapter
    from ..storage.artifacts import ArtifactCache
    payload = json.load(sys.stdin)
    request = TypeAdapter(o.RunRequest).validate_json(payload["request"])
    workspace = Path.cwd().resolve()
    checkout = Path(payload["checkout"]).resolve()
    sys.path.insert(0, str(checkout))
    module, name = payload["session_factory"].split(":", 1)
    factory = getattr(importlib.import_module(module), name)
    runtime = EmbeddedOpenSRERuntime(factory=factory, checkout=checkout,
        ref=o.VersionRef(name=payload["upstream_name"], revision=UPSTREAM_REVISION))
    observer = RuntimeEventRecorder(cache=ArtifactCache(workspace / "native-cache"),
        invocation_id=payload["invocation_id"], stream_path=workspace / "runtime.jsonl")
    try:
        capture = runtime._run(request=request, workspace=workspace, observer=observer)
        (workspace / "session.capture.json").write_bytes(TypeAdapter(SessionCapture).dump_json(capture))
    finally:
        observer.commit()


if __name__ == "__main__":
    if sys.argv[1:] != ["--worker"]:
        raise SystemExit("This module's executable mode is --worker under ProcessOpenSRERuntime")
    _worker_main()
