"""Factories for native Codex/Claude showcase profiles on a prepared local host.

The library owns CLI capture. This application recipe owns fixture staging,
tool bridges, receipt aliases and the downloadable scenario bundle.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import zipfile

import agent_eval_flow as a
from agent_eval_flow.adapters.cli import json_bytes, local_path
from agent_eval_flow.adapters.common import AdapterBinding, contained_relative
from agent_eval_flow.adapters.process import CliConnection, PosixProcessLauncher, ProcessSupervisor
from agent_eval_flow.objects.identity import plain
from agent_eval_flow.objects.values import observed
from agent_eval_flow.storage.artifacts import ArtifactCache


def _verify(path, expected):
    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise a.ConfigurationError(f"Fixture content hash differs: {path}")
    return data


def _decoded(value):
    if isinstance(value, str):
        try:
            yield json.loads(value)
        except ValueError:
            for line in value.splitlines():
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    elif isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _decoded(child)
    elif isinstance(value, (tuple, list)):
        for child in value:
            yield from _decoded(child)


class ShowcaseRecipe:
    """Prepare owned tools; normalize only actual native tool-result receipts."""
    # Labels and profile binding fields are deliberately not extra native flags.
    # The actual task/skill/tool bytes, model and sandbox remain declared inputs.
    accepted_settings = frozenset({"workflow_e2e", "e2e_mode", "fixture_dir", "e2e_variant",
                                   "executable", "runtime_revision", "sandbox"})

    def prepare(self, request, workspace, cache):
        settings = request.candidate.settings
        task = plain(request.input.tables["units"][0])
        config = settings.get("workflow_e2e")
        if config is None:
            tool = next((component for component in request.candidate.components.values()
                         if component.kind == "tool" and component.ref.name == "fixture.echo"), None)
            if tool is None:
                return {}
            cache.verify(tool.content).raise_for_errors()
            (workspace / "toy_tool.py").write_bytes(local_path(tool.content).read_bytes())
            # The input comes from the actual native command invocation.
            bridge = '''import json, subprocess, sys
arguments = json.loads(sys.argv[1])
completed = subprocess.run([sys.executable, "toy_tool.py"], input=json.dumps(arguments).encode("utf-8"), capture_output=True, check=True)
print(json.dumps({"schema_version":"aef-toy-tool/1", "name":"fixture.echo", "arguments":arguments, "result":json.loads(completed.stdout)}))
'''
            (workspace / "toy_tool_bridge.py").write_text(bridge, encoding="utf-8")
            return {"mission": "Invoke fixture.echo through the terminal: " + sys.executable +
                " toy_tool_bridge.py '<JSON task object>'. Pass the public task_id and text exactly. "
                "Use the returned result for your final response.", "toy": True}
        if settings.get("sandbox", "read-only") != "workspace-write":
            raise a.ConfigurationError("Workflow CLI showcase requires declared sandbox='workspace-write' for receipts")
        fixture = Path(config["fixture_dir"]).resolve()
        source_bytes = _verify(fixture / "SOURCE_MANIFEST.json", config["source_manifest_sha256"])
        cases_bytes = _verify(fixture / "cases.json", config["cases_sha256"])
        tool_bytes = _verify(fixture / "investigation_tool.py", config["tool_sha256"])
        source_manifest = json.loads(source_bytes)
        staged = workspace / "fixture"
        staged.mkdir()
        for row in source_manifest["files"]:
            original = contained_relative(fixture / "downloaded", row["path"])
            target = contained_relative(staged / "downloaded", row["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_verify(original, row["sha256"]))
        for name, data in (("SOURCE_MANIFEST.json", source_bytes), ("cases.json", cases_bytes), ("investigation_tool.py", tool_bytes)):
            (staged / name).write_bytes(data)
        receipts = workspace / "receipts"
        receipts.mkdir()
        max_calls = config["max_tool_calls"]
        if type(max_calls) is not int or max_calls <= 0:
            raise a.ConfigurationError("max_tool_calls must be positive")
        # Atomic admission bounds actual fixture dispatches, including failures.
        bridge = '''import json, os, subprocess, sys, time
from pathlib import Path
root = Path(__file__).resolve().parent
lock = root / "tool-count.lock"
deadline = time.monotonic() + 10
while True:
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(descriptor)
        break
    except FileExistsError:
        if time.monotonic() >= deadline: raise RuntimeError("Tool admission lock unavailable")
        time.sleep(0.01)
try:
    counter = root / "tool-count.txt"
    count = int(counter.read_text()) if counter.exists() else 0
    if count >= MAX_CALLS: raise RuntimeError("Declared tool-call budget exhausted")
    counter.write_text(str(count + 1))
finally:
    lock.unlink()
action = sys.argv[1]
arguments = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
command = [sys.executable, "-B", str(root / "fixture/investigation_tool.py"), "--fixture", str(root / "fixture"), "--receipts", str(root / "receipts"), "--invocation", INVOCATION, "--nonce", NONCE]
completed = subprocess.run(command, input=json.dumps({"action":action,"arguments":arguments}).encode("utf-8"), capture_output=True)
sys.stdout.buffer.write(completed.stdout)
sys.stderr.buffer.write(completed.stderr)
raise SystemExit(completed.returncode)
'''
        bridge = f"MAX_CALLS = {max_calls!r}\nINVOCATION = {request.run_id!r}\nNONCE = {task['nonce']!r}\n" + bridge
        (workspace / "run_investigation_tool.py").write_text(bridge, encoding="utf-8")
        artifacts = {
            "workflow.inputs": cache.write_bytes("cases", cases_bytes, "application/json"),
            "workflow.source_manifest": cache.write_bytes("manifest", source_bytes, "application/json"),
            "workflow.bridge": cache.write_bytes("bridge", bridge.encode(), "text/x-python"),
        }
        mission = Path(config["mission_file"]).read_text(encoding="utf-8")
        mission += "\nInvoke the actual tools through the terminal:\n" + sys.executable + " run_investigation_tool.py ACTION '<JSON arguments>'\n"
        mission += ("Actions: inventory, run_cases, read_source. First inventory the fixture; then run the cases; "
                    "then read relevant source using the returned source_hints and based_on_receipt_id. "
                    "All receipts contain this invocation's identity. Use their exact values and source lines in the report. "
                    f"There are at most {max_calls} fixture-tool dispatches available.\n")
        return {"mission": mission, "response_schema": json.loads(Path(config["response_schema_file"]).read_text(encoding="utf-8")),
                "artifacts": artifacts, "workflow": True, "fixture": str(staged), "max_tool_calls": max_calls}

    def finish(self, request, prepared, run, cache):
        workflow = bool(prepared.metadata.get("workflow"))
        if not workflow and not prepared.metadata.get("toy"):
            return run
        events, calls = [], []
        decision_by_id = {event.id: event for event in run.events}
        for event in run.events:
            if event.kind != "tool_call" or not event.inputs or not event.outputs:
                events.append(event)
                continue
            decision = decision_by_id.get(event.fields.get("decision_event_id"))
            if decision is None:
                events.append(event)
                continue
            # Require the native command choice, not an assistant quoting a receipt.
            native_choice = json.dumps(plain(decision.fields))
            script = "run_investigation_tool.py" if workflow else "toy_tool_bridge.py"
            if script not in native_choice or event.fields.get("is_error", False):
                events.append(event)
                continue
            native_result = event.fields.get("result")
            if isinstance(native_result, Mapping) and native_result.get("exit_code", 0) != 0:
                events.append(event)
                continue
            schema = "aef-workflow-tool/1" if workflow else "aef-toy-tool/1"
            receipts = [row for row in _decoded(native_result) if isinstance(row, Mapping) and row.get("schema_version") == schema]
            # Duplicate nested references are collapsed by receipt bytes, never by task order.
            unique = {json.dumps(plain(row), sort_keys=True): row for row in receipts}
            if len(unique) != 1:
                events.append(event)
                continue
            receipt = next(iter(unique.values()))
            if workflow and (receipt.get("invocation_id") != request.run_id or
                             receipt.get("nonce") != request.input.tables["units"][0]["nonce"]):
                raise a.CaptureValidationError("Native tool returned a stale or foreign workflow receipt")
            fields = {**event.fields,
                "name": "workflow." + receipt["action"] if workflow else receipt["name"],
                "arguments": receipt["arguments"], "result": receipt if workflow else receipt["result"]}
            events.append(replace(event, fields=fields))
            if workflow:
                calls.append(plain(receipt))
        if not workflow:
            return replace(run, events=tuple(events))
        if len(calls) > prepared.metadata["max_tool_calls"]:
            raise a.CaptureValidationError("Native trace exceeded the declared fixture-tool budget")
        artifacts = dict(run.artifacts)
        artifacts["workflow.calls"] = cache.write_bytes("calls", json_bytes(calls), "application/json")
        if run.output_state == "available":
            artifacts["workflow.report"] = cache.write_bytes("report", json_bytes(run.output), "application/json")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            entries = []
            for index, (name, artifact) in enumerate(artifacts.items()):
                path = f"artifacts/{index:03d}.data"
                cache.verify(artifact).raise_for_errors()
                bundle.writestr(path, local_path(artifact).read_bytes())
                entries.append({"artifact": name, "path": path, "sha256": artifact.sha256})
            fixture = Path(prepared.metadata["fixture"])
            manifest = json.loads((fixture / "SOURCE_MANIFEST.json").read_text(encoding="utf-8"))
            for row in manifest["files"]:
                path = contained_relative(fixture / "downloaded", row["path"])
                bundle.writestr("input/downloaded/" + row["path"], _verify(path, row["sha256"]))
            bundle.writestr("manifest.json", json_bytes({"run_id": run.id,
                "native_id": run.native_refs.get("invocation_id"), "artifacts": entries}))
        artifacts["workflow.bundle"] = cache.write_bytes("workflow.bundle", buffer.getvalue(), "application/zip")
        return replace(run, artifacts=artifacts, events=tuple(events))


def make_backend(config, *, workspace):
    """Native profile factory. Authentication and containment must already exist."""
    profile = config.get("id") or config.get("profile")
    if profile not in ("codex_local", "claude_local"):
        raise a.ConfigurationError("Set profile id to codex_local or claude_local")
    settings = config["settings"]
    model = config["model"]
    if settings.get("model") != model:
        raise a.ConfigurationError("Copy the model declaration into settings.model so it participates in candidate identity")
    executable = shutil.which(settings["executable"])
    if executable is None:
        raise a.ConfigurationError("Native CLI executable is not installed or not on PATH")
    launcher_ref = config.get("launcher_factory")
    if launcher_ref:
        module, name = launcher_ref.split(":", 1)
        launcher = getattr(importlib.import_module(module), name)(config, workspace=workspace)
    elif os.name == "posix":
        launcher = PosixProcessLauncher()
    else:
        raise a.ConfigurationError("This Windows profile needs launcher_factory for a verified Job Object launcher, or a prepared worker; parent-PID termination is insufficient")
    workspace = Path(workspace).resolve()
    cache = ArtifactCache(workspace / "evidence")
    binding = AdapterBinding(ref=a.VersionRef(**config["backend_ref"]),
        upstream_ref=a.VersionRef(name=profile, revision=settings["runtime_revision"]), workspace_root=workspace / "runs",
        artifacts=cache, deployment=observed({"kind": "local", "platform": sys.platform}, reason="Observed local Python host"))
    connection = CliConnection(executable=Path(executable).resolve(), environment=dict(os.environ),
        supervisor=ProcessSupervisor(launcher=launcher, artifacts=cache, workspace_root=workspace / "runs"))
    if profile == "codex_local":
        from agent_eval_flow.adapters.codex import CodexBackend
        cls = CodexBackend
    else:
        from agent_eval_flow.adapters.claude_code import ClaudeCodeBackend
        cls = ClaudeCodeBackend
    return cls(binding=binding, connection=connection, model=model, scenario=ShowcaseRecipe())
