"""SDK-shaped fake-client checks of our harness, using real fixture tools.

These exercise transport, limits and evidence. They do not call Vertex or
establish that a local machine is a GCP worker.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import anyio
import pytest

import agent_eval_flow as a
from agent_eval_flow.adapters.process import NativeProcessCapture, StopEvidence
from agent_eval_flow.storage.artifacts import ArtifactCache
from examples.integrations.vertex import VertexShowcaseBackend, run_loop
from tests.e2e.support import make_study, plain
from tests.e2e.test_toy_pipeline import toy_components
from tests.e2e.test_live_workflow import case_checks, assert_native_tool_pair, native_record


FIXTURE = Path(__file__).parents[1] / "e2e/fixtures/workflow"


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def profile():
    model = {"provider": "vertex", "id": "explicit-fixture-model"}
    return {"backend_ref": {"name": "vertex-showcase", "revision": "fixture"}, "model": model,
        "settings": {"model": model, "sdk_version": "fixture", "model_call_limit": 10,
                     "output_token_limit_per_call": 4096, "supervisor_timeout_s": 30}}


class Response:
    def __init__(self, part): self.raw = {"candidates": [{"content": {"role": "model", "parts": [part]}}], "model_version": "fixture-version"}
    def model_dump(self, *, mode):
        assert mode == "json"
        return deepcopy(self.raw)


class ScriptedSdk:
    """Decision fixture only. Every tool result comes from the actual subprocess."""
    def __init__(self): self.models, self.calls = self, []
    def generate_content(self, **request):
        self.calls.append(deepcopy(request))
        assert request["config"]["automatic_function_calling"] == {"disable": True}
        assert request["config"]["http_options"]["retry_options"] == {"attempts": 1}
        assert 0 < request["config"]["http_options"]["timeout"] <= 60000
        task = json.loads(request["contents"][0]["parts"][0]["text"])
        results = [part["function_response"]["response"] for item in request["contents"] for part in item["parts"] if "function_response" in part]
        tools = request["config"].get("tools", [])
        names = {row["name"] for tool in tools for row in tool["function_declarations"]}
        def call(name, args): return Response({"function_call": {"name": name, "args": args, "id": "native-call-" + str(len(self.calls))}})
        if "workflow_inventory" in names:
            if not results: return call("workflow_inventory", {})
            inventory = results[0]
            if len(results) == 1:
                return call("workflow_run_cases", {"case_ids": [row["id"] for row in inventory["result"]["incident"]["cases"]]})
            reproduction = results[1]
            if len(results) == 2:
                hint = reproduction["result"]["source_hints"][0]
                return call("workflow_read_source", {**hint, "based_on_receipt_id": reproduction["receipt_id"]})
            citation = results[2]
            output = {"task_id": task["task_id"], "nonce": task["nonce"], "summary": "Fixture report built from executed cases",
                "open_questions": [], "case_results": [{"case_id": row["case_id"], "observed": row["observed"],
                    "receipt_id": reproduction["receipt_id"], "explanation": "Fixture explanation with executed source evidence",
                    "source": {"receipt_id": citation["receipt_id"], **{key: citation["result"][key] for key in ("path", "start_line", "end_line")}}}
                    for row in reproduction["result"]["case_results"]]}
        elif "fixture_echo" in names and not results:
            return call("fixture_echo", task)
        else:
            output = {"task_id": task["task_id"], "message": results[0]["message"] if results else task["text"], "items": [task["text"]]}
        return Response({"text": json.dumps(output)})


class FixtureSupervisor:
    """Drive the worker function with a fake SDK; not a live containment claim."""
    hard_wall_time_limit = True
    def __init__(self, cache, client): self.cache, self.client = cache, client
    async def execute(self, launch):
        config = json.loads(launch.stdin)
        rows = []
        output = await anyio.to_thread.run_sync(lambda: run_loop(config, self.client, lambda row: rows.append(deepcopy(row))))
        now = datetime.now(timezone.utc)
        return NativeProcessCapture(stdout=self.cache.write_bytes("trace", b"\n".join(json.dumps(row).encode() for row in rows), "application/x-ndjson"),
            stderr=self.cache.write_bytes("stderr", b"", "text/plain"), exit_code=0, started_at=now, ended_at=now,
            stop=StopEvidence(requested=True, confirmed=True, reason="Test fixture models confirmed containment"),
            output_complete=a.Observation(value=True, status="observed"),
            artifacts={"native.final": self.cache.write_bytes("final", json.dumps(output).encode(), "application/json")})


def backend_and_request(tmp_path, *, mode="plain", workflow=False):
    config = profile()
    cache = ArtifactCache(tmp_path / "cache")
    sdk = ScriptedSdk()
    backend = VertexShowcaseBackend(config=config, workspace=tmp_path / "runs", artifacts=cache,
        supervisor=FixtureSupervisor(cache, sdk), deployment=a.Observation(value={"kind": "unit-test", "project": "fixture", "location": "fixture"}, status="observed"))
    settings = {**config["settings"], "mission": "Return task_id, message, items", "e2e_mode": mode,
        "response_schema": {"type": "object", "properties": {"task_id": {"type": "string"}, "message": {"type": "string"},
            "items": {"type": "array", "items": {"type": "string"}}}, "required": ["task_id", "message", "items"], "additionalProperties": False}}
    unit = {"task_id": "case", "text": "fresh unit receipt"}
    if workflow:
        unit = {"task_id": "dependency", "nonce": "fresh-nonce", "incident_file": "cases.json"}
        settings["workflow_e2e"] = {"fixture_dir": str(FIXTURE.resolve()), "max_model_calls": 10, "max_tool_calls": 16,
            "source_manifest_sha256": sha(FIXTURE / "SOURCE_MANIFEST.json"), "cases_sha256": sha(FIXTURE / "cases.json"),
            "tool_sha256": sha(FIXTURE / "investigation_tool.py"), "mission_file": str((FIXTURE / "MISSION.md").resolve()),
            "response_schema_file": str((FIXTURE / "report.schema.json").resolve())}
    study, _, _ = make_study(a, project_id="vertex-harness-unit", backend_ref=config["backend_ref"],
        units=[unit], settings=settings, input_columns={"units": tuple(unit)}, components=toy_components(a, mode))
    assignment = study.plan().assignments[0]
    request = a.RunRequest(run_id="fixture-run", assignment=assignment, candidate=study.candidates[assignment.candidate_id],
        input=study.dataset.agent_input(assignment.unit), policy=study.execution, environment=None)
    return backend, request, sdk


class Recorder:
    def __init__(self): self.artifacts, self.executions, self.events = {}, {}, {}
    def record_artifact(self, name, value): self.artifacts[name] = value
    def record_execution(self, value): self.executions[value.id] = value
    def record_event(self, value): self.events[value.id] = value


@pytest.mark.parametrize("mode", ["plain", "skill", "tool", "flow"])
def test_vertex_worker_toy_modes_keep_sdk_sources_and_actual_tools(tmp_path, mode):
    backend, request, sdk = backend_and_request(tmp_path, mode=mode)
    async def call(): return await backend._run(request, Recorder())
    run = anyio.run(call)
    assert run.status == "completed" and run.output["message"] == "fresh unit receipt"
    assert run.resources().cost_usd.status == "unknown"
    assert run.environment.value["kind"] == "unit-test"
    skills = [row for row in run.events if row.kind == "skill_loaded"]
    tools = [row for row in run.events if row.kind == "tool_call"]
    assert bool(skills) == (mode in {"skill", "flow"})
    assert bool(tools) == (mode in {"tool", "flow"})
    if tools:
        assert tools[0].fields["result"]["message"] == "fresh unit receipt"
        assert tools[0].source.artifact == run.artifacts["native.trace"]
    if mode == "flow": assert run.events.index(skills[0]) < run.events.index(tools[0])
    assert len(sdk.calls) == (2 if tools else 1)


def test_vertex_workflow_reproduces_downloaded_cases_and_builds_inspectable_bundle(tmp_path):
    backend, request, sdk = backend_and_request(tmp_path, workflow=True)
    async def call(): return await backend._run(request, Recorder())
    run = anyio.run(call)
    receipts = json.loads(Path(run.artifacts["workflow.calls"].uri).read_bytes())
    assert len(sdk.calls) == 4 and len(receipts) == 3
    assert all(ok for _, ok, _ in case_checks(run.output, receipts))
    event_by_id = {event.id: event for event in run.events}
    for event in run.events:
        if event.kind == "tool_call":
            decision = event_by_id[event.fields["decision_event_id"]]
            assert_native_tool_pair("vertex_gcp", native_record(decision.source), native_record(event.source), plain(event.fields["result"]))
    with zipfile.ZipFile(run.artifacts["workflow.bundle"].uri) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["run_id"] == request.run_id
        for entry in manifest["artifacts"]:
            assert hashlib.sha256(archive.read(entry["path"])).hexdigest() == entry["sha256"]
        assert "input/downloaded/LICENSE.txt" in archive.namelist()


def test_vertex_model_limit_stops_before_another_sdk_dispatch(tmp_path):
    backend, request, sdk = backend_and_request(tmp_path, mode="tool")
    config, _, _, _ = backend.prepare(request)
    config["max_model_calls"] = 1
    rows = []
    with pytest.raises(RuntimeError, match="model-call budget"):
        run_loop(config, sdk, rows.append)
    assert len(sdk.calls) == 1 and sum(row["type"] == "harness.tool_result" for row in rows) == 1


def test_vertex_tool_limit_stops_without_executing_unadmitted_tool(tmp_path):
    backend, request, sdk = backend_and_request(tmp_path, mode="tool")
    config, _, _, _ = backend.prepare(request)
    config["max_tool_calls"] = 0
    calls = []
    with pytest.raises(RuntimeError, match="tool-call budget"):
        run_loop(config, sdk, lambda row: None, tool_runner=lambda *args: calls.append(args))
    assert len(sdk.calls) == 1 and calls == []


def test_vertex_factory_cannot_claim_gcp_from_configuration_alone(tmp_path, monkeypatch):
    import examples.integrations.vertex as module
    monkeypatch.setattr(module, "version", lambda name: "fixture")
    class Opener:
        def open(self, *args, **kwargs): raise OSError("No metadata server on this local machine")
    monkeypatch.setattr(module, "build_opener", lambda *args: Opener())
    config = profile()
    config["deployment"] = {"kind": "gcp", "project": "wanted", "location": "us-central1", "worker_image": "sha256:pin"}
    with pytest.raises(a.ConfigurationError, match="Cannot establish GCE host identity"):
        module.make_backend(config, workspace=tmp_path)


def test_vertex_malformed_capture_preserves_raw_bytes_and_terminal_execution(tmp_path, monkeypatch):
    backend, request, _ = backend_and_request(tmp_path)
    execute = backend.supervisor.execute
    async def malformed(launch):
        capture = await execute(launch)
        return replace(capture,
            stdout=backend.artifacts.write_bytes("bad.trace", b'{"type":"harness.tool_result","model_call_id":"missing"}\nnot-json\n', "application/x-ndjson"),
            artifacts={"native.final": backend.artifacts.write_bytes("bad.final", b'{"truncated":', "application/json")})
    monkeypatch.setattr(backend.supervisor, "execute", malformed)
    recorder = Recorder()
    async def call(): return await backend._run(request, recorder)
    run = anyio.run(call)
    assert run.status == "completed" and run.output_state == "unknown"
    assert "could not be projected" in run.error.message and "Final output read failed" in run.error.message
    assert Path(recorder.artifacts["native.trace"].uri).read_bytes().endswith(b"not-json\n")
    assert tuple(recorder.executions.values()) == run.executions


def test_vertex_unconfirmed_deadline_preserves_unknown_inventory_and_end_time(tmp_path, monkeypatch):
    backend, request, _ = backend_and_request(tmp_path)
    execute = backend.supervisor.execute
    async def unconfirmed(launch):
        return replace(await execute(launch), deadline_exceeded=True,
            stop=StopEvidence(requested=True, confirmed=False, reason="Fixture models unconfirmed process tree"))
    monkeypatch.setattr(backend.supervisor, "execute", unconfirmed)
    async def call(): return await backend._run(request, Recorder())
    run = anyio.run(call)
    assert run.status == "infrastructure_error" and run.output_state == "available"
    assert run.execution_inventory_complete.status == "unknown"
    assert run.ended_at is None and run.executions[0].ended_at is None


def test_vertex_bundle_failure_keeps_already_executed_work(tmp_path, monkeypatch):
    backend, request, _ = backend_and_request(tmp_path, workflow=True)
    write = backend.artifacts.write_bytes
    def fail_bundle(name, data, media_type):
        if name == "workflow.bundle": raise OSError("Fixture archive disk full")
        return write(name, data, media_type)
    monkeypatch.setattr(backend.artifacts, "write_bytes", fail_bundle)
    recorder = Recorder()
    async def call(): return await backend._run(request, recorder)
    with pytest.raises(OSError, match="disk full"): anyio.run(call)
    assert len(recorder.executions) == 1 and next(iter(recorder.executions.values())).status == "completed"
    assert len([event for event in recorder.events.values() if event.kind == "tool_call"]) == 3
    assert {"native.trace", "native.final", "native.receipt", "workflow.calls", "workflow.report"} <= recorder.artifacts.keys()


def test_vertex_supervisor_failure_keeps_partial_native_artifacts(tmp_path, monkeypatch):
    backend, request, _ = backend_and_request(tmp_path)
    partial = backend.artifacts.write_bytes("partial", b'{"type":"sdk.request"}\n', "application/x-ndjson")
    async def fail(launch):
        error = RuntimeError("Fixture supervisor failure after model dispatch")
        error.artifacts = {"stdout": partial}
        raise error
    monkeypatch.setattr(backend.supervisor, "execute", fail)
    recorder = Recorder()
    async def call(): return await backend._run(request, recorder)
    with pytest.raises(RuntimeError, match="supervisor failure"): anyio.run(call)
    assert recorder.artifacts["native.trace"] == partial and "harness.source" in recorder.artifacts
