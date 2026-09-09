"""Instrumented Vertex showcase harness on a prepared GCE worker.

Factory: ``examples.integrations.vertex:make_backend``. Install this repository,
``agent-eval-flow[cli]`` and the selected ``google-genai`` pin on the worker.
Configure ADC, deployment.kind/project/location/worker_image, model.provider/id,
settings.model (the same model), sdk_version, model_call_limit,
output_token_limit_per_call, supervisor_timeout_s and optional call_timeout_s /
tool_timeout_s. Instance metadata ``aef-worker-image`` must record the declared
worker image. Construction verifies the actual metadata server and package pin;
it never provisions a host or treats a developer laptop as GCP.

This application-owned demonstration uses native SDK function-call responses,
real fixture subprocesses and a contained harness process. No SDK/network or
cloud compatibility is claimed by the fake-client unit tests.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
import hashlib
import importlib
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from urllib.request import Request, build_opener, ProxyHandler
import zipfile

import anyio

import agent_eval_flow as a
from agent_eval_flow.adapters.cli import json_bytes, parse_jsonl, schema_validator
from agent_eval_flow.adapters.common import contained_relative
from agent_eval_flow.adapters.process import ProcessLaunch, ProcessSupervisor, PosixProcessLauncher
from agent_eval_flow.objects.identity import plain
from agent_eval_flow.objects.values import observed, unknown
from agent_eval_flow.storage.artifacts import ArtifactCache


def _positive(value, name, *, integer=False):
    if type(value) not in ((int,) if integer else (int, float)) or not math.isfinite(value) or value <= 0:
        raise a.ConfigurationError(f"{name} must be a positive {'integer' if integer else 'finite number'}")
    return value


def verify_gce_deployment(expected, cache):
    if expected.get("kind") != "gcp" or not all(expected.get(key) for key in ("project", "location", "worker_image")):
        raise a.ConfigurationError("Vertex showcase requires explicit GCP project, location and worker_image")
    opener = build_opener(ProxyHandler({}))
    actual = {}
    for key, path in (("project", "project/project-id"), ("instance_id", "instance/id"),
                      ("zone", "instance/zone"), ("worker_image", "instance/attributes/aef-worker-image")):
        request = Request("http://metadata.google.internal/computeMetadata/v1/" + path, headers={"Metadata-Flavor": "Google"})
        try:
            with opener.open(request, timeout=3) as response:
                if response.headers.get("Metadata-Flavor") != "Google":
                    raise ValueError("Missing authenticated metadata-server response header")
                actual[key] = response.read().decode("utf-8").strip()
        except Exception as exc:
            raise a.ConfigurationError(f"Cannot establish GCE host identity ({key}); run this factory on the prepared GCE worker: {exc}") from exc
    if actual["project"] != expected["project"] or actual["worker_image"] != expected["worker_image"]:
        raise a.ConfigurationError("Observed GCE project/image metadata disagrees with the selected deployment")
    actual.update(kind="gcp", location=expected["location"])
    reference = cache.write_bytes("deployment.receipt", json_bytes(actual), "application/json")
    return observed(actual, reason="Read current GCE metadata server with Metadata-Flavor verification",
                    evidence=(a.EvidenceRef(artifact=reference, description="Observed execution-host metadata"),))


def _functions(workflow, tool):
    if workflow:
        declarations = [
            {"name": "workflow_inventory", "description": "Read the downloaded source inventory and all incident case inputs", "parameters": {"type": "object", "properties": {}}},
            {"name": "workflow_run_cases", "description": "Execute selected cases against the downloaded dependency; returns receipts and source hints", "parameters": {"type": "object", "properties": {"case_ids": {"type": "array", "items": {"type": "string"}}}, "required": ["case_ids"]}},
            {"name": "workflow_read_source", "description": "Read implementation lines after reproduction, referencing the run_cases receipt", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}, "based_on_receipt_id": {"type": "string"}}, "required": ["path", "start_line", "end_line", "based_on_receipt_id"]}},
        ]
    elif tool:
        declarations = [{"name": "fixture_echo", "description": "Execute fixture.echo with this task's ID and text", "parameters": {
            "type": "object", "properties": {"task_id": {"type": "string"}, "text": {"type": "string"}}, "required": ["task_id", "text"]}}]
    else:
        declarations = []
    return declarations


def execute_tool(config, name, arguments, timeout):
    root = Path(config["workspace"])
    if config["workflow"]:
        if name not in {"workflow_inventory", "workflow_run_cases", "workflow_read_source"}:
            raise ValueError(f"Undeclared tool: {name}")
        command = [sys.executable, "-B", str(root / "fixture/investigation_tool.py"), "--fixture", str(root / "fixture"),
            "--receipts", str(root / "receipts"), "--invocation", config["run_id"], "--nonce", config["task"]["nonce"]]
        payload = {"action": name.removeprefix("workflow_"), "arguments": arguments}
    elif config["tool"] and name == "fixture_echo":
        command = [sys.executable, "-B", str(root / "toy_tool.py")]
        payload = arguments
    else:
        raise ValueError(f"Undeclared tool: {name}")
    # The two owned programs do not spawn child processes. The outer contained
    # harness also bounds the complete SDK/tool loop and all descendants.
    completed = subprocess.run(command, input=json.dumps(payload), capture_output=True,
        text=True, encoding="utf-8", timeout=timeout, cwd=root, check=False)
    if completed.returncode:
        raise RuntimeError(f"Tool exited {completed.returncode}: {completed.stderr}")
    return json.loads(completed.stdout)


def run_loop(config, client, emit, *, tool_runner=execute_tool, clock=time.monotonic):
    """Every SDK call and selected tool dispatch crosses this observable loop."""
    started = clock()
    deadline = started + config["wall_time_s"]
    system = config["mission"] + "\nReturn the final report as JSON matching this schema:\n" + json.dumps(config["schema"])
    for skill in config["skills"]:
        data = Path(skill["path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != skill["sha256"]:
            raise ValueError("Staged skill differs from its candidate hash")
        system += "\n\nLoaded skill:\n" + data.decode("utf-8")
        emit({"type": "harness.skill_loaded", "name": "fixture-format", "sha256": skill["sha256"], "artifact": skill["artifact"]})
    functions = _functions(config["workflow"], config["tool"])
    contents = [{"role": "user", "parts": [{"text": json.dumps(config["task"])}]}]
    calls = 0
    for _ in range(config["max_model_calls"]):
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError("Complete harness deadline expired")
        call_id = uuid.uuid4().hex
        options = {"system_instruction": system, "candidate_count": 1,
            "max_output_tokens": config["output_token_limit_per_call"],
            "automatic_function_calling": {"disable": True},
            "http_options": {"timeout": max(1, int(min(remaining, config["call_timeout_s"]) * 1000)),
                             "retry_options": {"attempts": 1}}}
        if functions:
            options["tools"] = [{"function_declarations": functions}]
        else:
            options.update(response_mime_type="application/json", response_json_schema=config["schema"])
        request = {"model": config["model"]["id"], "contents": contents, "config": options}
        emit({"type": "sdk.request", "call_id": call_id, "request": request})
        response = client.models.generate_content(**request)
        raw = response.model_dump(mode="json")
        emit({"type": "sdk.response", "call_id": call_id, "response": raw})
        candidates = raw.get("candidates") or []
        if len(candidates) != 1:
            raise RuntimeError("Expected one native response candidate; inspect retained response")
        content = candidates[0].get("content") or {}
        parts = content.get("parts") or []
        choices = [part["function_call"] for part in parts if part.get("function_call")]
        if not choices:
            text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
            output = json.loads(text)
            validator = schema_validator(config["schema"])
            if validator:
                validator.validate(output)
            emit({"type": "harness.completed", "output": output})
            return output
        contents.append(content)  # Retain native parts, including thought signatures.
        responses = []
        for choice in choices:
            if calls >= config["max_tool_calls"]:
                raise RuntimeError("Declared tool-call budget exhausted")
            if choice["name"] not in {row["name"] for row in functions}:
                raise RuntimeError("Native model requested an undeclared tool")
            remaining = deadline - clock()
            if remaining <= 0:
                raise TimeoutError("Complete harness deadline expired before tool dispatch")
            calls += 1
            try:
                result = tool_runner(config, choice["name"], choice.get("args") or {}, min(remaining, config["tool_timeout_s"]))
            except Exception as exc:
                emit({"type": "harness.tool_result", "model_call_id": call_id, "name": choice["name"],
                    "arguments": choice.get("args") or {}, "result": {"error": str(exc)}, "is_error": True})
                raise
            emit({"type": "harness.tool_result", "model_call_id": call_id, "name": choice["name"],
                "arguments": choice.get("args") or {}, "result": result, "is_error": False})
            function_response = {"name": choice["name"], "response": result}
            if choice.get("id"):
                function_response["id"] = choice["id"]
            responses.append({"function_response": function_response})
        contents.append({"role": "user", "parts": responses})
    raise RuntimeError("Declared model-call budget exhausted without a final report")


def worker_main(config):
    def emit(row):
        print(json.dumps(row, ensure_ascii=False), flush=True)
    try:
        actual = version("google-genai")
        if actual != config["sdk_version"]:
            raise a.ConfigurationError(f"Expected google-genai {config['sdk_version']}; installed {actual}")
        from google import genai
        # Explicit Vertex selection uses ADC; credentials are never serialized.
        with genai.Client(vertexai=True, project=config["deployment"]["project"],
                          location=config["deployment"]["location"],
                          http_options={"timeout": int(config["call_timeout_s"] * 1000), "retry_options": {"attempts": 1}}) as client:
            emit({"type": "harness.started", "invocation_id": config["native_id"], "sdk_version": actual,
                  "model": config["model"], "deployment": config["deployment"]})
            output = run_loop(config, client, emit)
            Path("final.json").write_bytes(json_bytes(output))
    except Exception as exc:
        emit({"type": "harness.error", "error_type": type(exc).__name__, "message": str(exc)})
        raise SystemExit(1) from exc


class VertexShowcaseBackend:
    def __init__(self, *, config, workspace, deployment, supervisor, artifacts):
        self.config, self.workspace, self.deployment = config, Path(workspace), deployment
        self.supervisor, self.artifacts = supervisor, artifacts
        self.ref = a.VersionRef(**config["backend_ref"])

    def capabilities(self):
        return a.BackendCapabilities(wall_time_limit=self.supervisor.hard_wall_time_limit,
                                     token_limit=False, cost_limit=False, reset_state=True)

    def run(self, request, *, recorder):
        return anyio.from_thread.run(self._run, request, recorder)

    def prepare(self, request):
        from examples.integrations.local_cli import ShowcaseRecipe
        settings = request.candidate.settings
        if request.candidate.backend != self.ref:
            raise a.ConfigurationError("Candidate backend differs from this Vertex binding")
        for key in ("sdk_version", "model", "model_call_limit", "output_token_limit_per_call",
                    "supervisor_timeout_s", "call_timeout_s", "tool_timeout_s", "tool_call_limit"):
            if a.canonical_bytes(settings.get(key)) != a.canonical_bytes(self.config["settings"].get(key)):
                raise a.ConfigurationError(f"Candidate {key} disagrees with the configured Vertex runtime")
        work = self.workspace / hashlib.sha256(request.run_id.encode()).hexdigest()
        work.mkdir(parents=True, exist_ok=False)
        recipe_request = replace(request, candidate=replace(request.candidate,
            settings={**settings, "sandbox": "workspace-write"}))
        metadata = ShowcaseRecipe().prepare(recipe_request, work, self.artifacts)
        artifacts = {key: value for key, value in metadata.get("artifacts", {}).items() if key != "workflow.bridge"}
        skills = []
        for slot, component in request.candidate.components.items():
            if component.kind != "skill" or component.content is None:
                continue
            self.artifacts.verify(component.content).raise_for_errors()
            target = work / (hashlib.sha256(slot.encode()).hexdigest() + ".md")
            target.write_bytes(Path(component.content.uri).read_bytes())
            alias = "component." + slot
            artifacts[alias] = self.artifacts.write_bytes(alias, target.read_bytes(), component.content.media_type)
            skills.append({"path": str(target), "sha256": component.content.sha256, "artifact": alias})
        workflow = settings.get("workflow_e2e")
        schema = metadata.get("response_schema", settings.get("response_schema"))
        schema_validator(schema)
        mission = Path(workflow["mission_file"]).read_text(encoding="utf-8") if workflow else settings.get("mission", "Return the task as the declared JSON response")
        if workflow:
            mission += "\nUse workflow_inventory, then workflow_run_cases, then workflow_read_source based on the reproduction receipt. Use actual receipt IDs, observations and source lines in the final report."
        model_limit = self.config["settings"]["model_call_limit"]
        if workflow:
            model_limit = min(model_limit, _positive(workflow.get("max_model_calls", model_limit), "max_model_calls", integer=True))
        run_config = {"workspace": str(work), "run_id": request.run_id, "native_id": uuid.uuid4().hex,
            "deployment": plain(self.deployment.value), "model": plain(self.config["model"]), "sdk_version": settings["sdk_version"],
            "task": plain(request.input.tables["units"][0]), "skills": skills, "mission": mission, "schema": plain(schema),
            "workflow": bool(workflow), "tool": bool(metadata.get("toy")), "max_model_calls": model_limit,
            "max_tool_calls": workflow["max_tool_calls"] if workflow else self.config["settings"].get("tool_call_limit", 8),
            "wall_time_s": min(request.policy.budget.wall_time_s, self.config["settings"]["supervisor_timeout_s"]),
            "call_timeout_s": self.config["settings"].get("call_timeout_s", 60),
            "tool_timeout_s": self.config["settings"].get("tool_timeout_s", 30),
            "output_token_limit_per_call": settings["output_token_limit_per_call"]}
        source = Path(__file__).resolve()
        artifacts["harness.source"] = self.artifacts.write_bytes("harness.source", source.read_bytes(), "text/x-python")
        launch = ProcessLaunch(argv=(str(Path(sys.executable).resolve()), "-u", str(source), "--worker"),
            stdin=json_bytes(run_config), workspace=work, environment=dict(os.environ),
            wall_time_s=run_config["wall_time_s"], capture_files={"native.final": "final.json"})
        return run_config, metadata, artifacts, launch

    async def _run(self, request, recorder):
        config, metadata, artifacts, launch = await anyio.to_thread.run_sync(self.prepare, request)
        for alias, ref in artifacts.items(): recorder.record_artifact(alias, ref)
        try:
            captured = await self.supervisor.execute(launch)
        except BaseException as exc:
            for alias, ref in getattr(exc, "artifacts", {}).items():
                recorder.record_artifact({"stdout": "native.trace", "stderr": "native.stderr"}.get(alias, alias), ref)
            raise
        artifacts.update(captured.artifacts)
        artifacts.update({"native.trace": captured.stdout, "native.stderr": captured.stderr})
        for alias, ref in artifacts.items(): recorder.record_artifact(alias, ref)
        try:
            rows, issues = parse_jsonl(captured.stdout)
            issues = list(issues)
        except (OSError, ValueError, TypeError) as exc:
            rows, issues = (), [f"Native trace read failed: {type(exc).__name__}: {exc}"]
        execution_id = request.run_id + "/harness"
        events, decisions = [], {}
        for position, located in enumerate(rows):
            row, ref = located.value, located.source
            event_id = f"{execution_id}/event/{position}"
            try:
                if row.get("type") == "sdk.response":
                    decisions[row["call_id"]] = (event_id, ref)
                    events.append(a.Event(id=event_id, execution_id=execution_id, kind="model_response", at=None,
                        fields=row, source=ref, outputs=(ref,)))
                elif row.get("type") == "harness.tool_result":
                    decision = decisions.get(row["model_call_id"])
                    if decision is None:
                        raise ValueError("Tool result lacks its recorded SDK decision")
                    name = "workflow." + row["name"].removeprefix("workflow_") if config["workflow"] else "fixture.echo"
                    events.append(a.Event(id=event_id, execution_id=execution_id, kind="tool_call", at=None,
                        fields={"name": name, "arguments": row["arguments"], "result": row["result"],
                            "decision_event_id": decision[0], "is_error": row.get("is_error", False)},
                        source=ref, inputs=(decision[1],), outputs=(ref,)))
                elif row.get("type") == "harness.skill_loaded":
                    events.append(a.Event(id=event_id, execution_id=execution_id, kind="skill_loaded", at=None,
                        fields=row, source=ref, inputs=(a.EvidenceRef(artifact=artifacts[row["artifact"]], description="Actually read skill bytes"),)))
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                issues.append(f"Native event {position} could not be projected: {type(exc).__name__}: {exc}")
        output, output_state = None, "unknown"
        if "native.final" in artifacts:
            try:
                output = json.loads(Path(artifacts["native.final"].uri).read_bytes())
                output_state = "available"
            except (OSError, ValueError, TypeError) as exc:
                issues.append(f"Final output read failed: {type(exc).__name__}: {exc}")
        status = "completed" if captured.exit_code == 0 else "agent_error"
        if captured.deadline_exceeded:
            status = "timed_out" if captured.stop.confirmed else "infrastructure_error"
        if not captured.stop.confirmed:
            issues.append("Native process-tree termination is unconfirmed; inventory and duration remain unknown")
        if captured.output_complete.value is not True:
            issues.append("Native streams were not captured completely")
        if status == "completed" and output_state != "available":
            issues.append("Native process exited successfully without a readable final report")
        failures = [row.value for row in rows if row.value.get("type") == "harness.error"]
        failure_message = str(failures[-1].get("message")) if failures else "Inspect retained harness stderr and trace"
        error = None if status == "completed" and not issues else a.ErrorRecord(code="native." + status,
            message="; ".join(issues) or failure_message,
            evidence=(a.EvidenceRef(artifact=captured.stdout), a.EvidenceRef(artifact=captured.stderr)))
        unknown_usage = unknown("No billing total was reported; native per-response usage remains in the trace")
        resources = a.Resources(cost_scope=request.policy.cost_scope, cost_usd=unknown_usage,
            input_tokens=unknown_usage, output_tokens=unknown_usage, human_minutes=unknown("Human intervention is not observed by this harness"))
        execution = a.Execution(id=execution_id, slot="main", retry_index=0, parent_id=None, status=status,
            started_at=captured.started_at, ended_at=captured.ended_at if captured.stop.confirmed else None, resources=resources,
            effective_config=observed({"sdk_version": config["sdk_version"], "model": config["model"],
                "max_model_calls": config["max_model_calls"], "max_tool_calls": config["max_tool_calls"],
                "automatic_function_calling": False, "sdk_retry_attempts": 1}, evidence=(a.EvidenceRef(artifact=captured.stdout),)),
            native_refs={"invocation_id": config["native_id"]}, error=error)
        # These immutable receipts survive failures in optional bundle creation.
        recorder.record_execution(execution)
        for event in events: recorder.record_event(event)
        receipt = {"profile": "vertex_gcp", "native_id": config["native_id"],
            "runtime_revision": f"vertex-showcase/{artifacts['harness.source'].sha256};google-genai/{config['sdk_version']}",
            "model": config["model"], "deployment": config["deployment"],
            "candidate_fingerprint": request.candidate.fingerprint(), "projection_issues": issues,
            "stop": {"requested": captured.stop.requested, "confirmed": captured.stop.confirmed, "reason": captured.stop.reason}}
        artifacts["native.receipt"] = self.artifacts.write_bytes("native.receipt", json_bytes(receipt), "application/json")
        recorder.record_artifact("native.receipt", artifacts["native.receipt"])
        if config["workflow"]:
            calls = [plain(event.fields["result"]) for event in events if event.kind == "tool_call" and not event.fields.get("is_error")]
            artifacts["workflow.calls"] = self.artifacts.write_bytes("workflow.calls", json_bytes(calls), "application/json")
            recorder.record_artifact("workflow.calls", artifacts["workflow.calls"])
            if output_state == "available":
                artifacts["workflow.report"] = self.artifacts.write_bytes("workflow.report", json_bytes(output), "application/json")
                recorder.record_artifact("workflow.report", artifacts["workflow.report"])
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                entries = []
                for index, (alias, ref) in enumerate(artifacts.items()):
                    member = f"artifacts/{index:03d}.data"
                    self.artifacts.verify(ref).raise_for_errors()
                    archive.writestr(member, Path(ref.uri).read_bytes())
                    entries.append({"artifact": alias, "path": member, "sha256": ref.sha256})
                fixture = Path(metadata["fixture"])
                source_manifest = json.loads((fixture / "SOURCE_MANIFEST.json").read_bytes())
                for row in source_manifest["files"]:
                    archive.writestr("input/downloaded/" + row["path"], contained_relative(fixture / "downloaded", row["path"]).read_bytes())
                archive.writestr("manifest.json", json_bytes({"run_id": request.run_id, "native_id": config["native_id"], "artifacts": entries}))
            artifacts["workflow.bundle"] = self.artifacts.write_bytes("workflow.bundle", buffer.getvalue(), "application/zip")
        for alias, ref in artifacts.items(): recorder.record_artifact(alias, ref)
        return a.Run(id=request.run_id, assignment_id=request.assignment.id, status=status,
            cost_scope=request.policy.cost_scope, output=output, output_state=output_state, artifacts=artifacts,
            executions=(execution,), events=tuple(events), started_at=captured.started_at, ended_at=execution.ended_at,
            environment=self.deployment, execution_inventory_complete=observed(True) if captured.stop.confirmed else unknown("Native process-tree termination is unconfirmed"),
            output_sources=(execution_id,) if output_state == "available" else (),
            native_refs={"invocation_id": config["native_id"]}, error=error)


def make_backend(config, *, workspace):
    settings = config.get("settings", {})
    model = config.get("model", {})
    if model.get("provider") != "vertex" or a.canonical_bytes(settings.get("model")) != a.canonical_bytes(model):
        raise a.ConfigurationError("Declare model.provider='vertex' and copy the model into settings.model for candidate identity")
    if not isinstance(model.get("id"), str) or not model["id"].strip() or "REPLACE" in model["id"]:
        raise a.ConfigurationError("Supply an explicit deployed Vertex model ID")
    for name in ("sdk_version",):
        if not settings.get(name) or "REPLACE" in settings[name]:
            raise a.ConfigurationError(f"Supply an installed, pinned {name}")
    try:
        installed = version("google-genai")
    except PackageNotFoundError as exc:
        raise a.ConfigurationError("Install the selected google-genai version on the prepared worker; no fallback client is provided") from exc
    if installed != settings["sdk_version"]:
        raise a.ConfigurationError(f"google-genai pin mismatch: {installed} != {settings['sdk_version']}")
    for key in ("model_call_limit", "output_token_limit_per_call"):
        _positive(settings.get(key), key, integer=True)
    _positive(settings.get("tool_call_limit", 8), "tool_call_limit", integer=True)
    _positive(settings.get("supervisor_timeout_s"), "supervisor_timeout_s")
    for key in ("call_timeout_s", "tool_timeout_s"):
        _positive(settings.get(key, 60 if key == "call_timeout_s" else 30), key)
    cache = ArtifactCache(Path(workspace) / "evidence")
    deployment = verify_gce_deployment(config.get("deployment", {}), cache)
    if config.get("launcher_factory"):
        module, name = config["launcher_factory"].split(":", 1)
        launcher = getattr(importlib.import_module(module), name)(config, workspace=workspace)
    elif os.name == "posix":
        launcher = PosixProcessLauncher()
    else:
        raise a.ConfigurationError("This worker requires a verified contained launcher; configure launcher_factory")
    root = Path(workspace).resolve() / "runs"
    supervisor = ProcessSupervisor(launcher=launcher, artifacts=cache, workspace_root=root)
    return VertexShowcaseBackend(config=config, workspace=root, deployment=deployment, supervisor=supervisor, artifacts=cache)


if __name__ == "__main__":
    if sys.argv[1:] != ["--worker"]:
        raise SystemExit("Use make_backend on the prepared worker; --worker reads one harness request on stdin")
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    worker_main(json.load(sys.stdin))
