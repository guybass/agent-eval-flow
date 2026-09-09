"""Run the native OpenSRE worker inside one dedicated local Docker container."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from functools import partial
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import time
from uuid import uuid4

import anyio
from pydantic import TypeAdapter

from agent_eval_flow import objects as o
from agent_eval_flow.adapters.opensre import SessionCapture, UPSTREAM_REVISION
from agent_eval_flow.adapters.openkritt import json_bytes
from agent_eval_flow.adapters.worker import _refs, _relocate
from agent_eval_flow.objects.values import observed, unknown

OWNER_LABEL = "org.agent-eval-flow.opensre-invocation"
CONTAINER_WORKSPACE = PurePosixPath("/aef/run")


def materialize_capture(capture, *, workspace, cache):
    """Verify and relocate typed artifact references before the worker is released."""
    replacements = {}
    for ref in _refs(capture):
        remote = PurePosixPath(ref.uri)
        if not remote.is_relative_to(CONTAINER_WORKSPACE) or ".." in remote.parts:
            raise o.StorageError("Worker artifact escaped its mounted invocation workspace")
        local = (workspace / remote.relative_to(CONTAINER_WORKSPACE)).resolve()
        if not local.is_relative_to(workspace.resolve()):
            raise o.StorageError("Worker artifact escaped through a local link")
        data = local.read_bytes()
        if ref.sha256 is None or hashlib.sha256(data).hexdigest() != ref.sha256:
            raise o.StorageError("Worker artifact is missing its verified source hash")
        existing = replacements.get(ref.uri)
        if existing is not None and (existing.sha256 != ref.sha256 or existing.media_type != ref.media_type):
            raise o.StorageError("Worker artifact URI has inconsistent identities")
        replacements[ref.uri] = cache.write_bytes("opensre.worker-artifact", data, ref.media_type)
    return _relocate(capture, replacements)


class DockerOpenSRERuntime:
    def __init__(self, config):
        self.config = config
        self.docker = shutil.which(config.get("docker", "docker"))
        if not self.docker:
            raise o.ConfigurationError("Docker CLI was not found")
        self.project = Path(__file__).resolve().parents[2]
        self.checkout = Path(config["checkout"]).resolve()
        self.auth = Path(config["codex_home"]).resolve()
        if not (self.auth / "auth.json").is_file():
            raise o.ConfigurationError("The dedicated Codex authentication file is unavailable")
        self.image = config.get("image", "aef-opensre-local:1a81e1a")
        self.session_factory = config.get("session_factory", "examples.integrations.opensre_local_session:make_session")
        actual = subprocess.run(["git", "-c", "safe.directory=" + self.checkout.as_posix(), "-C", str(self.checkout),
                                 "rev-parse", "HEAD"], capture_output=True, text=True, timeout=20, check=True)
        if actual.stdout.strip() != UPSTREAM_REVISION:
            raise o.ConfigurationError("OpenSRE checkout differs from the supported revision")
        self.image_id = self._command("image", "inspect", "--format", "{{.Id}}", self.image).stdout.decode().strip()
        self.binding_sources = {name: (Path(__file__).parent / name).read_bytes() for name in (
            "opensre_local.py", "opensre_local_worker.py", "opensre_local.Dockerfile")}
        self.upstream_ref = o.VersionRef(name="opensre", revision=UPSTREAM_REVISION)
        self.deployment = observed({"kind": "local_docker", "image": self.image, "image_id": self.image_id},
                                   "Docker daemon reported the selected local image")

    def _command(self, *args, timeout=30, check=True):
        return subprocess.run([self.docker, *args], capture_output=True, timeout=timeout, check=check)

    def capabilities(self):
        return o.BackendCapabilities(wall_time_limit=True, token_limit=False, cost_limit=False, reset_state=True)

    def _state(self, name, invocation):
        result = self._command("inspect", "--format", "{{json .}}", name, check=False)
        if result.returncode:
            error = result.stderr.decode(errors="replace")
            if "No such object:" in error or "No such container:" in error:
                return None
            raise o.CaptureValidationError("Docker state is unavailable: " + error[:400])
        item = json.loads(result.stdout)
        if item.get("Config", {}).get("Labels", {}).get(OWNER_LABEL) != invocation or item.get("Image") != self.image_id:
            raise o.CaptureValidationError("Docker container ownership or image identity differs")
        return item

    def _stop(self, name, invocation):
        state = self._state(name, invocation)
        if state is None:
            return True
        if state["State"]["Running"]:
            self._command("kill", name, timeout=15, check=False)
        state = self._state(name, invocation)
        return state is None or state["State"]["Running"] is False

    async def run_session(self, *, request, workspace, observer):
        return await anyio.to_thread.run_sync(partial(self._run, request=request, workspace=workspace, observer=observer))

    def _run(self, *, request, workspace, observer):
        started, clock_start = datetime.now(timezone.utc), time.monotonic()
        invocation = observer.invocation_id
        name = "aef-opensre-" + uuid4().hex
        mounts = ((self.project / "src", "/workspace/src", True),
                  (self.project / "examples", "/workspace/examples", True),
                  (self.project / "tests/e2e/fixtures/opensre", "/workspace/tests/e2e/fixtures/opensre", True),
                  (self.auth, "/aef/auth", True),
                  (workspace, str(CONTAINER_WORKSPACE), False))
        command = ["create", "--name", name, "--label", f"{OWNER_LABEL}={invocation}", "--init", "--interactive",
                   "--workdir", str(CONTAINER_WORKSPACE)]
        for source, destination, readonly in mounts:
            command.extend(["--mount", f"type=bind,source={source.resolve()},target={destination}" + (",readonly" if readonly else "")])
        command += [self.image_id]
        payload = {"request": TypeAdapter(o.RunRequest).dump_json(request).decode(), "checkout": "/opt/opensre",
                   "session_factory": self.session_factory, "invocation_id": invocation, "upstream_name": self.upstream_ref.name}
        stdout_path, stderr_path = workspace / "docker.stdout", workspace / "docker.stderr"
        timed_out, stopped, error, exit_code, process = False, False, None, None, None
        state = None
        try:
            self._command(*command)
            self._state(name, invocation)
            remaining = request.policy.budget.wall_time_s - (time.monotonic() - clock_start)
            if remaining <= 0:
                raise subprocess.TimeoutExpired("worker deadline", request.policy.budget.wall_time_s)
            with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
                process = subprocess.Popen([self.docker, "start", "--attach", "--interactive", name],
                                           stdin=subprocess.PIPE, stdout=out, stderr=err)
                process.communicate(json_bytes(payload), timeout=remaining)
                exit_code = process.returncode
            state = self._state(name, invocation)
        except subprocess.TimeoutExpired:
            timed_out = True
            error = "The dedicated native worker exceeded its wall-time budget"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                stopped = self._stop(name, invocation)
                state = self._state(name, invocation)
            except Exception as exc:
                error = (error + "; " if error else "") + f"Stop verification failed: {type(exc).__name__}: {exc}"
            if process is not None and process.poll() is None:
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()  # CLI transport only; container stop is verified separately.
                    process.communicate(timeout=5)
            if state is not None:
                exit_code = state["State"].get("ExitCode", exit_code)
            if stopped:
                # Remove only this verified, stopped task container. Retain its mounted evidence.
                try:
                    self._command("rm", name, check=False)
                except (OSError, subprocess.TimeoutExpired):
                    pass  # A verified stopped container may remain for manual inspection.
        ended = datetime.now(timezone.utc)
        stdout = observer.cache.write_bytes("native.docker.stdout", stdout_path.read_bytes() if stdout_path.exists() else b"", "text/plain")
        stderr = stderr_path.read_bytes() if stderr_path.exists() else b""
        trace_path = workspace / "runtime.jsonl"
        if trace_path.exists():
            observer.ingest(trace_path.read_bytes())
        transport = {"schema_version": "aef-opensre-local-docker/1", "invocation_id": invocation,
                     "container": name, "image_id": self.image_id, "upstream_revision": UPSTREAM_REVISION,
                     "container_exit_code": exit_code, "stopped": stopped, "deadline_exceeded": timed_out,
                     "started_at": started.isoformat(), "ended_at": ended.isoformat(), "error": error}
        receipt = observer.cache.write_bytes("native.docker.receipt", json_bytes(transport), "application/json")
        source = o.EvidenceRef(artifact=receipt, description="Verified local Docker worker lifecycle")
        deployment = observed(self.deployment.value, self.deployment.reason, (source,))
        artifacts = {"native.docker.stdout": stdout, "native.docker.receipt": receipt}
        for binding_name, data in self.binding_sources.items():
            artifacts["native.binding." + binding_name] = observer.cache.write_bytes("native.binding", data, "text/plain")
        capture_path = workspace / "session.capture.json"
        if capture_path.exists():
            capture = TypeAdapter(SessionCapture).validate_json(capture_path.read_bytes())
            if capture.invocation_id != invocation or capture.upstream_ref != self.upstream_ref:
                raise o.CaptureValidationError("Local worker returned another invocation or source revision")
            capture = materialize_capture(capture, workspace=workspace, cache=observer.cache)
            status = "timed_out" if timed_out and stopped else "infrastructure_error" if error or not stopped or exit_code != 0 else capture.status
            return replace(capture, status=status, started_at=started, ended_at=ended, deployment=deployment,
                process={"argv": ["python", "-m", "examples.integrations.opensre_local_worker"], "exit_code": exit_code,
                         "container": name, "image_id": self.image_id, "stop_confirmed": stopped},
                stderr=stderr, artifacts={**capture.artifacts, **artifacts},
                trace_complete=observed(capture.trace_complete.value is True and not error and stopped,
                                        "Native typed observer and completed Docker transport"),
                error=o.ErrorRecord(code="opensre_local_transport", message=error or "Worker did not stop cleanly")
                    if error or not stopped else capture.error)
        reason = error or "Native worker did not produce a final session receipt"
        # A timeout or process failure can leave useful tool/CLI evidence even
        # without the final worker envelope. Copy it before adapter cleanup.
        for path in sorted(workspace.rglob("*")):
            if not path.is_file() or not path.resolve().is_relative_to(workspace.resolve()):
                continue
            relative = path.relative_to(workspace)
            if relative.parts[0] not in {"incident", "codex", "native-cache"} and path.name not in {
                    "native-construction.json", "native-output.json", "native-goal.json"}:
                continue
            media = "application/json" if path.suffix == ".json" else "application/x-ndjson" if path.suffix == ".jsonl" else "text/plain"
            artifacts["partial." + ".".join(relative.parts)] = observer.cache.write_bytes(
                "opensre.partial", path.read_bytes(), media)
        unavailable = unknown(reason)
        return SessionCapture(invocation_id=invocation, turn=None,
            status="timed_out" if timed_out and stopped else "infrastructure_error", started_at=started, ended_at=ended,
            upstream_ref=self.upstream_ref, effective_config=unavailable, model=unavailable, deployment=deployment,
            resources=o.Resources(cost_scope=request.policy.cost_scope, cost_usd=unavailable,
                                  input_tokens=unavailable, output_tokens=unavailable, human_minutes=unavailable),
            inventory_complete=unknown("The worker failed before its complete session receipt"),
            trace_complete=observed(False, reason), artifacts=artifacts, stderr=stderr,
            process={"container": name, "image_id": self.image_id, "exit_code": exit_code, "stop_confirmed": stopped},
            error=o.ErrorRecord(code="opensre_worker_capture", message=reason))


def make_runtime(config, *, workspace):
    return DockerOpenSRERuntime(config)
