"""Offline source-format/lifecycle checks; supplied transports are test-owned.

These tests exercise production adapters with representative native records.
They do not stand in for the separately selected live OpenSRE/OpenKritt E2Es.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import anyio
import pytest

from agent_eval_flow import objects as o
from agent_eval_flow.adapters.common import AdapterBinding
from agent_eval_flow.adapters.process import StopEvidence
from agent_eval_flow.adapters.openkritt import (
    OpenKrittBackend, OpenKrittHTTPConnection, LocalRepositoryStager, NativeJSON,
    WorkflowCapture, HarnessTrace, artifact_bytes, json_bytes, tool_events, UPSTREAM_REVISION,
)
from agent_eval_flow.adapters.opensre import (
    OpenSREBackend, RuntimeEventRecorder, SessionCapture, map_runtime_events,
    ProcessOpenSRERuntime,
    UPSTREAM_REVISION as SRE_REVISION,
)
from agent_eval_flow.objects.values import observed, unknown, zero_resources
from agent_eval_flow.storage.artifacts import ArtifactCache


class Recorder:
    def __init__(self):
        self.artifacts, self.executions, self.events = {}, [], []
    def record_artifact(self, name, artifact): self.artifacts[name] = artifact
    def record_execution(self, execution): self.executions.append(execution)
    def record_event(self, event): self.events.append(event)


def binding(tmp_path, revision):
    cache = ArtifactCache(tmp_path / "cache")
    receipt = cache.write_bytes("deployment", json_bytes({"fixture": "local transport"}), "application/json")
    environment = observed({"platform": "local", "resource_id": "test-owned-service", "upstream_revision": revision},
        "Test-owned local transport provenance", (o.EvidenceRef(artifact=receipt),))
    return AdapterBinding(ref=o.VersionRef(name="test-native", revision="1"),
        upstream_ref=o.VersionRef(name="native", revision=revision), workspace_root=tmp_path / "runs",
        artifacts=cache, deployment=environment)


def request_for(binding, settings, *, wall_time=10.0):
    candidate = o.Candidate(id="A", backend=binding.ref, components={}, settings=settings)
    unit = {"task_id": "review"}
    return o.RunRequest(run_id="run-one", assignment=o.Assignment(id="assignment-one", candidate_id="A",
        candidate_fingerprint=candidate.fingerprint(), unit=unit, repetition=0), candidate=candidate,
        input=o.AgentInput(unit=unit, tables={"units": ({"task_id": "review", "repo_kind": "local",
            "repo_full": "owned-repo", "repo_scope": "application", "prompt": "Investigate the supplied task"},)}),
        policy=o.ExecutionPolicy(budget=o.Budget(wall_time_s=wall_time)), environment=None)


def native_metadata(identifier, step, *, repeat=1):
    return {"id": identifier, "scan_id": 7, "step_id": step, "repeat_run": repeat, "prev_id": None,
        "prev_table": None, "kind": "step", "status": "completed", "model": "fixture-model",
        "harness": "codex", "model_provider": "fixture-provider", "prompt_template": "read files",
        "prompt_filled": "read files with retained inputs", "run_started_at": "2026-09-09T10:00:00Z",
        "run_time_ms": 125, "raw_token_usage": {"input_tokens": 13, "output_tokens": 7},
        "output_json": {"results": [{"file": "app.py"}], "stub": False}, "stub": False}


def create_kritt(tmp_path, *, collection_error=False, blocked=False):
    bind = binding(tmp_path, UPSTREAM_REVISION)
    root = tmp_path / "source"
    root.mkdir()
    data = b"def total(values):\n    return sum(values)\n"
    (root / "app.py").write_bytes(data)
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(json_bytes({"files": [{"path": "app.py", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}]}))
    workflow = tmp_path / "workflow.json"
    workflow.write_bytes(json_bytes({"kind": "open-kritt-workflow", "version": 2, "workflow": {"name": "review", "levels": []}}))
    mount = tmp_path / "mount"
    mount.mkdir()
    calls, stopped = [], []
    scan = {"id": 7, "repoKind": "local", "repoFull": "owned-repo", "postScriptId": 2, "status": "pending"}
    stdout = json_bytes({"type": "item.completed", "item": {"id": "command-1", "type": "command_execution",
        "command": "cat app.py", "aggregated_output": data.decode(), "exit_code": 0}}) + b"\n"

    class Collector:
        async def collect(self, scan_id):
            assert scan_id == "7"
            if collection_error:
                raise RuntimeError("Native read-only collector disconnected")
            return WorkflowCapture(metadata=NativeJSON(json_bytes([native_metadata(21, 1), native_metadata(22, 1, repeat=2)])),
                results=NativeJSON(json_bytes([{"scan_id": 7, "step_id": 1, "repeat_run": 1, "json_answer": {"file": "app.py"}}])),
                traces=(HarnessTrace("21", "codex-jsonl", stdout, b""), HarnessTrace("22", "codex-jsonl", stdout, b"")),
                inventory_complete=observed(True), observer=o.VersionRef(name="fixture-native-collector", revision="1"))

    class Stopper:
        confirmed_stop_supported = True
        async def stop_scan(self, scan_id):
            stopped.append(scan_id)
            return StopEvidence(requested=True, confirmed=True, reason="Test-owned service cancellation acknowledged")

    async def transport(method, url, headers, body):
        path = url.split("http://fixture", 1)[1]
        calls.append((method, path, json.loads(body) if body else None))
        if path == "/api/workflows": return 201, json_bytes({"id": 3, "steps": []})
        if path == "/api/post-scripts/2": return 200, json_bytes({"id": 2, "name": "summary"})
        if path == "/api/scans": return 201, json_bytes(scan)
        if path == "/api/scans/7":
            if blocked: await anyio.sleep(10)
            return 200, json_bytes({**scan, "status": "completed"})
        if path == "/api/scans/7/vulnerabilities": return 200, b"[]"
        raise AssertionError((method, path))

    service = OpenKrittHTTPConnection(base_url="http://fixture", stager=LocalRepositoryStager(mount),
        collector=Collector(), stopper=Stopper(), upstream_ref=bind.upstream_ref, deployment=bind.deployment, transport=transport)
    backend = OpenKrittBackend(binding=bind, service=service, poll_interval_s=0.001)
    request = request_for(bind, {"scan_options": {"model": "fixture-model", "harness": "codex",
        "model_provider": "fixture-provider", "severity_ranker": "operator-defined ranker"},
        "openkritt": {"upstream_revision": UPSTREAM_REVISION, "fixture_dir": str(root),
            "source_manifest": str(manifest), "workflow_file": str(workflow), "post_script_id": 2,
            "scan_configuration": {"repeat_runs": 2}}}, wall_time=0.03 if blocked else 10.0)
    return backend, request, calls, stopped, mount


def test_openkritt_native_lifecycle_retains_iterations_tools_and_bundle(tmp_path):
    backend, request, calls, stopped, mount = create_kritt(tmp_path)
    recorder = Recorder()
    run = anyio.run(backend.arun, request, recorder)
    run.validate().raise_for_errors()
    assert run.status == "completed" and run.native_refs == {"scan_id": "7"}
    assert run.output["findings"] == () and run.output["export"]["status"] == "unavailable"
    assert len([e for e in run.executions if "step_metadata_id" in e.native_refs]) == 2
    assert len([e for e in run.events if e.kind == "tool_call"]) == 2
    assert {e.native_refs["repeat_run"] for e in run.executions if "repeat_run" in e.native_refs} == {"1", "2"}
    assert run.resources().input_tokens.value == 26 and run.resources().cost_usd.status == "unknown"
    assert sum(method == "POST" and path == "/api/scans" for method, path, _ in calls) == 1
    assert not stopped and not list(mount.iterdir())
    submitted = next(payload for method, path, payload in calls if path == "/api/scans")
    assert submitted["repo_full"] == "owned-repo" and submitted["configuration"] == {"repeat_runs": 2}
    with zipfile.ZipFile(io.BytesIO(artifact_bytes(run.artifacts["openkritt.bundle"]))) as archive:
        index = json.loads(archive.read("manifest.json"))
        assert set(index["artifacts"]) == set(run.artifacts) - {"openkritt.bundle"}
        for alias, path in index["artifacts"].items():
            assert archive.read(path) == artifact_bytes(run.artifacts[alias])


def test_openkritt_export_failure_does_not_erase_completed_native_scan(tmp_path):
    backend, request, calls, stopped, mount = create_kritt(tmp_path, collection_error=True)
    run = anyio.run(backend.arun, request, Recorder())
    assert run.status == "completed" and run.error.code == "capture_incomplete"
    assert run.execution_inventory_complete.status == "unknown"
    assert "openkritt.scan" in run.artifacts and "openkritt.bundle" in run.artifacts
    assert run.output["findings"] == () and not list(mount.iterdir())


def test_openkritt_deadline_stops_acknowledged_scan_without_resubmitting(tmp_path):
    backend, request, calls, stopped, mount = create_kritt(tmp_path, blocked=True)
    run = anyio.run(backend.arun, request, Recorder())
    assert stopped == ["7"] and run.status == "timed_out"
    assert sum(path == "/api/scans" for _, path, _ in calls) == 1
    assert not list(mount.iterdir())


def test_native_claude_tool_arguments_and_result_keep_different_source_lines(tmp_path):
    bind = binding(tmp_path, UPSTREAM_REVISION)
    data = b'diagnostic line\n' + json_bytes({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "call-1", "name": "Read", "input": {"file_path": "app.py"}}]}}) + b"\n"
    data += json_bytes({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "call-1", "content": "actual file bytes"}]}}) + b"\n"
    trace = HarnessTrace("metadata-1", "claude-stream-json", data, b"")
    ref = bind.artifacts.write_bytes("stdout", data, "application/x-ndjson")
    event, = tool_events(execution_id="step-1", trace=trace, artifact=ref)
    assert event.source.locator == "line:2" and event.outputs[0].locator == "line:3"
    assert event.fields["arguments"]["file_path"] == "app.py" and event.fields["result"]["content"] == "actual file bytes"


@dataclass(frozen=True)
class ProviderStart:
    iteration: int
    message_count: int
    type: str = "provider_request_start"


def test_opensre_typed_trace_and_turn_preserve_native_fields_and_resource_unknowns(tmp_path):
    bind = binding(tmp_path, SRE_REVISION)
    request = request_for(bind, {"required_upstream_revision": SRE_REVISION, "artifact_bundle": "incident.bundle"})

    class NativeRuntime:
        upstream_ref = bind.upstream_ref
        async def run_session(self, *, request, workspace, observer):
            emit = observer.for_loop("native-loop-42")
            emit(ProviderStart(iteration=1, message_count=2))
            emit({"type": "tool_execution_start", "tool_call_id": "native-tool-1", "tool_name": "logs",
                "args": {"cursor": None}, "iteration": 1})
            emit({"type": "tool_execution_end", "tool_call_id": "native-tool-1", "tool_name": "logs",
                "args": {"cursor": None}, "iteration": 1, "result": {"rows": [{"line": 8}, {"line": 12}]}, "is_error": False})
            emit(ProviderStart(iteration=2, message_count=4))
            now = datetime.now(timezone.utc)
            usage = o.Resources(cost_scope=("model",), cost_usd=unknown("Native events omit billing"),
                input_tokens=unknown("Native events omit usage"), output_tokens=unknown("Native events omit usage"), human_minutes=unknown("No human effort receipt"))
            return SessionCapture(invocation_id=observer.invocation_id, turn=json_bytes({"final_intent": "answer",
                "action_result": {"accounting_status": "completed", "cancelled": False, "hit_iteration_cap": False},
                "assistant_response_text": "Observed retained records"}), status="completed", started_at=now, ended_at=now,
                upstream_ref=bind.upstream_ref, effective_config=observed({"model": "fixture-model"}),
                model=observed({"provider": "test-owned", "id": "fixture-model"}), deployment=bind.deployment,
                resources=usage, inventory_complete=observed(True), trace_complete=observed(True), artifacts={})

    backend = OpenSREBackend(binding=bind, runtime=NativeRuntime())
    run = anyio.run(backend.arun, request, Recorder())
    run.validate().raise_for_errors()
    assert run.status == "completed" and len(run.events) == 4
    assert [event.source.locator for event in run.events] == [f"line:{i}" for i in range(1, 5)]
    assert run.events[2].fields["result"]["rows"] == ({"line": 8}, {"line": 12})
    assert run.resources().cost_usd.status == "unknown"
    assert not list(bind.workspace_root.iterdir())
    receipt = json.loads(artifact_bytes(run.artifacts["native.receipt"]))
    assert receipt["process"] is None  # no invented successful GCP process receipt
    assert receipt["capture"]["event_count"] == 4


def test_opensre_mapper_rejects_foreign_invocation_without_fabricating_events(tmp_path):
    bind = binding(tmp_path, SRE_REVISION)
    recorder = RuntimeEventRecorder(cache=bind.artifacts, invocation_id="actual-invocation")
    recorder.for_loop("native-loop")(ProviderStart(iteration=1, message_count=2))
    ref = recorder.commit()
    with pytest.raises(o.CaptureValidationError):
        map_runtime_events(ref, run_id="run-one", execution_id="session-one", invocation_id="wrong-invocation")


@pytest.mark.parametrize("partial", [False, True])
def test_opensre_contained_worker_receipt_and_partial_trace_are_not_reconstructed(tmp_path, partial):
    from pydantic import TypeAdapter
    from agent_eval_flow.adapters.process import CliConnection, NativeProcessCapture
    import sys
    bind = binding(tmp_path, SRE_REVISION)
    request = request_for(bind, {"required_upstream_revision": SRE_REVISION})
    launches = []

    class CapturedSupervisor:
        hard_wall_time_limit = True
        async def execute(self, launch):
            launches.append(launch)
            envelope = json.loads(launch.stdin)
            # Exercise the same typed boundary as the actual contained worker.
            delivered = TypeAdapter(o.RunRequest).validate_json(envelope["request"])
            assert delivered.assignment.id == request.assignment.id
            invocation = envelope["invocation_id"]
            data = json_bytes({"sequence": 0, "invocation_id": invocation, "loop_id": "native-loop-1",
                "event": {"type": "provider_request_start", "iteration": 1, "message_count": 2}}) + b"\n"
            extra = {"native.trace": bind.artifacts.write_bytes("trace", data, "application/x-ndjson")}
            now = datetime.now(timezone.utc)
            if not partial:
                capture = SessionCapture(invocation_id=invocation, turn=json_bytes({"final_intent": "answer",
                    "action_result": {"accounting_status": "completed"}, "assistant_response_text": "native response"}),
                    status="completed", started_at=now, ended_at=now, upstream_ref=bind.upstream_ref,
                    effective_config=observed({"fixture": "native process receipt"}), model=observed({"id": "fixture-model"}),
                    deployment=bind.deployment, resources=zero_resources(), inventory_complete=observed(True),
                    trace_complete=observed(True), artifacts={})
                extra["session.capture"] = bind.artifacts.write_bytes("capture", TypeAdapter(SessionCapture).dump_json(capture), "application/json")
            return NativeProcessCapture(stdout=bind.artifacts.write_bytes("stdout", b"native diagnostic", "text/plain"),
                stderr=bind.artifacts.write_bytes("stderr", b"", "text/plain"), exit_code=-15 if partial else 0,
                started_at=now, ended_at=now, stop=StopEvidence(requested=partial, confirmed=True, reason="Fixture process capture"),
                output_complete=observed(True), artifacts=extra, deadline_exceeded=partial)

    runtime = ProcessOpenSRERuntime(connection=CliConnection(executable=Path(sys.executable).resolve(),
        environment={}, supervisor=CapturedSupervisor()), checkout=tmp_path, session_factory="operator.open_sre:make_session",
        upstream_ref=bind.upstream_ref, deployment=bind.deployment)
    run = anyio.run(OpenSREBackend(binding=bind, runtime=runtime).arun, request, Recorder())
    assert len(launches) == 1 and launches[0].argv[-2:] == ("agent_eval_flow.adapters.opensre", "--worker")
    assert run.status == ("timed_out" if partial else "completed")
    assert len(run.events) == 1 and run.events[0].fields["native"]["message_count"] == 2
    assert run.output_state == ("unknown" if partial else "available")
    assert json.loads(artifact_bytes(run.artifacts["native.receipt"]))["process"]["exit_code"] == (-15 if partial else 0)


def test_openkritt_http_stop_acknowledgment_alone_is_not_a_hard_deadline(tmp_path):
    backend, request, calls, stopped, mount = create_kritt(tmp_path)
    assert backend.service.stopper.confirmed_stop_supported
    assert backend.capabilities().wall_time_limit is False


def test_openkritt_invalid_input_hash_rejects_before_any_http_or_staging(tmp_path):
    backend, request, calls, stopped, mount = create_kritt(tmp_path)
    path = Path(request.candidate.settings["openkritt"]["fixture_dir"]) / "app.py"
    path.write_bytes(b"changed input")
    with pytest.raises(o.ConfigurationError, match="hash/length mismatch"):
        anyio.run(backend.arun, request, Recorder())
    assert not calls and not list(mount.iterdir())


def test_openkritt_production_recorder_accepts_poll_snapshots_and_final_capture(tmp_path):
    from agent_eval_flow.execution.capture import CaptureBuffer
    backend, request, calls, stopped, mount = create_kritt(tmp_path)
    recorder = CaptureBuffer(request)
    run = anyio.run(backend.arun, request, recorder)
    final = recorder.finish(run)
    assert final == run
    assert "openkritt.acknowledgment" in final.artifacts
    assert "openkritt.scan_snapshot.1" in final.artifacts
    assert json.loads(artifact_bytes(final.artifacts["openkritt.acknowledgment"]))["status"] == "pending"
    assert json.loads(artifact_bytes(final.artifacts["openkritt.scan"]))["status"] == "completed"


def test_openkritt_late_bundle_failure_preserves_published_terminal_outcome(tmp_path, monkeypatch):
    from agent_eval_flow.execution.capture import CaptureBuffer
    backend, request, calls, stopped, mount = create_kritt(tmp_path)
    recorder = CaptureBuffer(request)
    original = backend.binding.artifacts.write_bytes
    def failing_bundle(name, data, media_type):
        if name == "openkritt.bundle":
            assert recorder.executions[request.run_id + "/scan"].status == "completed"
            assert recorder.events
            raise OSError("Owned late bundle write failure")
        return original(name, data, media_type)
    monkeypatch.setattr(backend.binding.artifacts, "write_bytes", failing_bundle)
    run = anyio.run(backend.arun, request, recorder)
    assert recorder.finish(run).status == "completed"
    assert run.output["native_status"] == "completed" and run.output["findings"] == ()
    assert run.error.code == "capture_incomplete" and "bundle" in run.error.message
    assert "openkritt.bundle" not in run.artifacts


def test_opensre_late_export_failure_preserves_turn_events_and_completed_execution(tmp_path, monkeypatch):
    from agent_eval_flow.execution.capture import CaptureBuffer
    bind = binding(tmp_path, SRE_REVISION)
    request = request_for(bind, {})
    recorder = CaptureBuffer(request)
    observer = RuntimeEventRecorder(cache=bind.artifacts, invocation_id="native-invocation")
    observer.for_loop("native-loop")(ProviderStart(iteration=1, message_count=2))
    trace = observer.commit()
    recorder.record_artifact("native.trace", trace)
    now = datetime.now(timezone.utc)
    capture = SessionCapture(invocation_id="native-invocation", turn=json_bytes({"final_intent": "answer",
        "action_result": {"accounting_status": "completed"}, "assistant_response_text": "preserve this native answer"}),
        status="completed", started_at=now, ended_at=now, upstream_ref=bind.upstream_ref,
        effective_config=observed({"model": "fixture"}), model=observed({"id": "fixture"}), deployment=bind.deployment,
        resources=zero_resources(), inventory_complete=observed(True), trace_complete=observed(True),
        artifacts={"incident.report": tmp_path / "missing-report.json"})
    original = bind.artifacts.write_bytes
    def failing_bundle(name, data, media_type):
        if name == "native.bundle":
            assert recorder.executions[request.run_id + "/session"].status == "completed"
            assert recorder.events
            raise OSError("Owned late bundle write failure")
        return original(name, data, media_type)
    monkeypatch.setattr(bind.artifacts, "write_bytes", failing_bundle)
    run = OpenSREBackend(binding=bind, runtime=None).map_capture(request, capture, trace, recorder=recorder)
    assert recorder.finish(run).status == "completed"
    assert run.output["assistant_response_text"] == "preserve this native answer"
    assert len(run.events) == 1 and "native.turn" in run.artifacts
    assert run.error.code == "opensre_capture_incomplete" and "incident.report" in run.error.message
    assert "native.bundle" not in run.artifacts
