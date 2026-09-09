"""Shared native CLI capture machinery; agent-specific schemas stay in dialects."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass, replace
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Protocol
from urllib.parse import urlparse
from urllib.request import url2pathname
from uuid import uuid4

import anyio

from .. import objects as o
from ..objects.identity import plain
from ..objects.values import observed, unknown
from .common import AdapterBinding
from .process import CliConnection, NativeProcessCapture, ProcessLaunch


def local_path(ref: o.ArtifactRef) -> Path:
    parsed = urlparse(ref.uri)
    if parsed.scheme == "file" and parsed.netloc in ("", "localhost"):
        return Path(url2pathname(parsed.path))
    if not parsed.scheme or (os.name == "nt" and len(parsed.scheme) == 1):
        return Path(ref.uri)
    raise o.ConfigurationError("CLI assets must be materialized locally before dispatch")


def json_bytes(value) -> bytes:
    return (json.dumps(plain(value), ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")


@dataclass(frozen=True)
class LocatedRecord:
    value: Mapping
    source: o.EvidenceRef


@dataclass(frozen=True)
class CliCapture:
    events: tuple[LocatedRecord, ...]
    terminal: LocatedRecord | None
    final_output: o.JSONValue
    output_state: str
    native_refs: Mapping[str, str]
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class PreparedCli:
    launch: ProcessLaunch
    model: Mapping[str, str]
    runtime_revision: str
    artifacts: Mapping[str, o.ArtifactRef]
    schema: Mapping | bool | None
    skill_loads: tuple[Mapping, ...] = ()
    metadata: Mapping = field(default_factory=dict)


class CliScenario(Protocol):
    """A caller-owned asset/mission recipe; it cannot manufacture native events."""
    def prepare(self, request: o.RunRequest, workspace: Path, artifacts) -> Mapping: ...
    def finish(self, request: o.RunRequest, prepared: PreparedCli, run: o.Run, artifacts) -> o.Run: ...


def parse_jsonl(ref: o.ArtifactRef) -> tuple[tuple[LocatedRecord, ...], tuple[str, ...]]:
    rows, issues = [], []
    # Stream the retained source, never replace it with normalized rows.
    with local_path(ref).open("rb") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
                if not isinstance(value, dict):
                    raise ValueError("native event must be an object")
            except (ValueError, TypeError, UnicodeError) as exc:
                issues.append(f"line:{number}: {exc}")
                continue
            rows.append(LocatedRecord(value, o.EvidenceRef(artifact=ref, locator=f"line:{number}")))
    return tuple(rows), tuple(issues)


def json_output(ref: o.ArtifactRef | None):
    if ref is None:
        return None, "unavailable", ()
    try:
        text = local_path(ref).read_text(encoding="utf-8")
        value = json.loads(text,
                           parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        return value, "available", ()
    except UnicodeError as exc:
        return None, "unknown", (f"Delivered response cannot be decoded as UTF-8: {exc}",)
    except ValueError:
        # Native final text remains an available output even when JSON was not
        # produced. A configured response schema reports that separately.
        return text, "available", ()


def native_token(value, source):
    if type(value) is int and value >= 0:
        return observed(value, evidence=(source,))
    return unknown("The native export did not expose this token total")


def schema_validator(schema):
    if schema is None:
        return None
    try:
        from jsonschema.validators import validator_for
        from referencing import Registry
    except ImportError as exc:
        raise o.ConfigurationError("Install agent-eval-flow[cli] for structured response validation") from exc
    schema = plain(schema)
    try:
        cls = validator_for(schema)
        cls.check_schema(schema)
        # External references require caller materialization; never fetch a schema implicitly.
        return cls(schema, registry=Registry())
    except Exception as exc:
        raise o.ConfigurationError(f"Invalid response_schema: {exc}") from exc


def materialize_owned_evidence(value, workspace, cache, _seen=None):
    """Retain referenced invocation files before deleting only the owned workspace."""
    if _seen is None:
        _seen = {}
    if isinstance(value, o.ArtifactRef):
        key = (value.uri, value.media_type, value.sha256)
        if key in _seen:
            return _seen[key]
        try:
            path = local_path(value).resolve()
        except o.ConfigurationError:
            return value
        if not path.is_relative_to(workspace):
            return value
        if not path.is_file():
            raise o.StorageError(f"Invocation evidence is not a retained regular file: {path}")
        with path.open("rb") as stream:
            retained = cache.write_stream("invocation-evidence", stream, value.media_type)
        if value.sha256 is not None and retained.sha256 != value.sha256:
            raise o.StorageError(f"Invocation evidence changed before retention: {path}")
        _seen[key] = retained
        return retained
    if is_dataclass(value):
        return replace(value, **{item.name: materialize_owned_evidence(getattr(value, item.name), workspace, cache, _seen)
                                  for item in fields(value)})
    if isinstance(value, Mapping):
        return {key: materialize_owned_evidence(item, workspace, cache, _seen) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return tuple(materialize_owned_evidence(item, workspace, cache, _seen) for item in value)
    return value


class NativeCliBackend:
    """One bounded native CLI assignment, with immutable raw evidence.

    Bind an executable, a verified contained launcher, and a versioned dialect.
    Per-candidate settings: mission, response_schema, model {provider,id},
    sandbox (Codex), tools/allowed_tools/max_turns (Claude). Additional scenario
    recipes are runtime bindings; their behavior must be declared in Candidate.
    Unknown settings require a scenario's explicit accepted_settings declaration.
    The reserved metadata mapping is bookkeeping only and does not configure or
    prompt the native agent. Successful owned workspaces are removed after all
    typed evidence references are retained; failures remain for investigation.
    """
    def __init__(self, *, binding: AdapterBinding, connection: CliConnection,
                 dialect, model: Mapping[str, str] | None = None, scenario: CliScenario | None = None):
        self.binding, self.connection, self.dialect = binding, connection, dialect
        self.ref, self.scenario = binding.ref, scenario
        self.default_model = dict(model or {})
        if not binding.ref.revision or not binding.upstream_ref.revision:
            raise o.ConfigurationError("CLI adapter and upstream runtime need explicit revisions")

    def capabilities(self):
        return o.BackendCapabilities(wall_time_limit=self.connection.supervisor.hard_wall_time_limit,
                                     token_limit=False, cost_limit=False, reset_state=True)

    def run(self, request, *, recorder):
        return anyio.from_thread.run(self._run, request, recorder)

    def _runtime_revision(self):
        executable = Path(self.connection.executable)
        if not executable.is_absolute() or not executable.is_file():
            raise o.ConfigurationError("CLI executable must resolve to an existing absolute path")
        try:
            version = subprocess.run([str(executable), "--version"], capture_output=True, text=True,
                                     encoding="utf-8", timeout=10, check=True,
                                     env=dict(self.connection.environment)).stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise o.ConfigurationError(f"Cannot establish native CLI version: {exc}") from exc
        # Match a whole printed token, never a permissive substring such as 1.2 in 1.20.
        expected = self.binding.upstream_ref.revision
        if expected != version and expected not in version.split():
            raise o.ConfigurationError(f"Expected CLI revision {expected!r}, observed {version!r}")
        return version

    def prepare(self, request):
        if request.candidate.backend != self.ref:
            raise o.ConfigurationError("Candidate backend differs from this CLI binding")
        if request.candidate.native is not None:
            self.dialect.validate_native(request.candidate.native)
        settings = request.candidate.settings
        allowed = {"mission", "response_schema", "model", "metadata"}
        allowed.update(getattr(self.dialect, "accepted_settings", ()))
        if self.scenario is not None:
            allowed.update(getattr(self.scenario, "accepted_settings", ()))
        unsupported = set(settings) - allowed
        if unsupported:
            raise o.ConfigurationError(f"Unsupported native CLI settings: {', '.join(sorted(unsupported))}")
        if "metadata" in settings and not isinstance(settings["metadata"], Mapping):
            raise o.ConfigurationError("CLI metadata must be a bookkeeping mapping")
        if "runtime_revision" in settings and settings["runtime_revision"] != self.binding.upstream_ref.revision:
            raise o.ConfigurationError("Declared runtime_revision differs from the prepared runtime binding")
        if "executable" in settings:
            if not isinstance(settings["executable"], str) or Path(settings["executable"]).resolve() != Path(self.connection.executable).resolve():
                raise o.ConfigurationError("Declared executable differs from the prepared CLI connection")
        revision = self._runtime_revision()
        root = Path(self.binding.workspace_root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        work = root / (hashlib.sha256(request.run_id.encode()).hexdigest()[:16] + "-" + uuid4().hex)
        work.mkdir(exist_ok=False)
        artifacts = {}
        configured_model = settings.get("model", {})
        if not isinstance(configured_model, Mapping):
            raise o.ConfigurationError("candidate.settings.model must be a mapping")
        model = dict(configured_model)
        models = [c for c in request.candidate.components.values() if c.kind == "model"]
        if len(models) > 1:
            raise o.ConfigurationError("CLI direct binding accepts one model component")
        if models:
            declared = dict(models[0].params)
            declared.setdefault("id", models[0].ref.name)
            if model and any(model.get(k) != declared.get(k) for k in ("id", "provider") if k in declared):
                raise o.ConfigurationError("Conflicting model component and settings/binding")
            model.update(declared)
        if set(model) - {"id", "provider"}:
            raise o.ConfigurationError("This CLI dialect implements only model id/provider; other model options are unsupported")
        if not all(isinstance(model.get(k), str) and model[k].strip() for k in ("id", "provider")):
            raise o.ConfigurationError("Declare model id/provider in candidate.settings.model or a model component; binding defaults cannot define candidate behavior")
        if self.default_model and any(model.get(k) != value for k, value in self.default_model.items()):
            raise o.ConfigurationError("Declared model differs from the prepared model binding")
        if hasattr(self.dialect, "validate_connection"):
            self.dialect.validate_connection(model, self.connection.environment)
        schema = settings.get("response_schema")
        mission = settings.get("mission", "Perform the supplied task and return your result as JSON.")
        if not isinstance(mission, str):
            raise o.ConfigurationError("mission must be text")
        input_ref = self.binding.artifacts.write_bytes("agent.input", json_bytes(request.input.tables), "application/json")
        artifacts["agent.input"] = input_ref
        (work / "input.json").write_bytes(local_path(input_ref).read_bytes())
        chunks = [mission, "Public task input:\n" + json_bytes(request.input.tables).decode()]
        skills = []
        component_root = work / "components"
        component_root.mkdir()
        for slot, component in request.candidate.components.items():
            if component.content is None:
                continue
            source = local_path(component.content)
            suffix = source.suffix if re.fullmatch(r"\.[a-zA-Z0-9]{1,12}", source.suffix) else ".data"
            destination = component_root / (hashlib.sha256(slot.encode()).hexdigest()[:16] + suffix)
            try:
                data = source.read_bytes()
            except OSError as exc:
                raise o.ConfigurationError(f"Cannot read declared component {slot!r}: {exc}") from exc
            if not component.content.sha256 or hashlib.sha256(data).hexdigest() != component.content.sha256:
                raise o.ConfigurationError(f"Declared component {slot!r} bytes do not match its pinned content hash")
            destination.write_bytes(data)
            ref = self.binding.artifacts.write_bytes(slot, data, component.content.media_type)
            artifacts["component." + slot] = ref
            if component.kind in ("skill", "prompt"):
                try:
                    content = data.decode("utf-8")
                except UnicodeError as exc:
                    raise o.ConfigurationError(f"Declared {component.kind} {slot!r} is not UTF-8 text") from exc
                chunks.append(f"Declared {component.kind} {slot}:\n{content}")
                if component.kind == "skill":
                    match = re.search(r"^name:\s*([^\r\n]+)$", content, re.MULTILINE)
                    skills.append({"slot": slot, "name": match.group(1).strip() if match else component.ref.name,
                                   "sha256": ref.sha256, "artifact": "component." + slot})
            else:
                chunks.append(f"Declared {component.kind} {slot}: {destination.relative_to(work).as_posix()}\n"
                              f"Parameters: {json_bytes(component.params).decode()}")
        metadata = {}
        if self.scenario is not None:
            metadata = dict(self.scenario.prepare(request, work, self.binding.artifacts))
            mission_extra = metadata.get("mission", "")
            if not isinstance(mission_extra, str):
                raise o.ConfigurationError("Scenario mission must be text")
            chunks.append(mission_extra)
            schema = metadata.get("response_schema", schema)
            artifacts.update(metadata.get("artifacts", {}))
        schema_validator(schema)
        if schema is not None:
            (work / "response.schema.json").write_bytes(json_bytes(schema))
        prompt = ("\n\n".join(chunks) + "\n").encode("utf-8")
        artifacts["native.prompt"] = self.binding.artifacts.write_bytes("native.prompt", prompt, "text/plain")
        argv = self.dialect.argv(self.connection.executable, work, model, settings, schema)
        launch = ProcessLaunch(argv=argv, stdin=prompt, workspace=work,
                               environment=self.connection.environment, wall_time_s=request.policy.budget.wall_time_s,
                               capture_files=self.dialect.capture_files)
        return PreparedCli(launch, model, revision, artifacts, schema, tuple(skills), metadata)

    async def _run(self, request, recorder):
        prepared = await anyio.to_thread.run_sync(self.prepare, request)
        try:
            capture = await self.connection.supervisor.execute(prepared.launch)
        except BaseException as exc:
            for name, artifact in {**prepared.artifacts, **getattr(exc, "artifacts", {})}.items():
                recorder.record_artifact(name, artifact)
            raise
        prepared = replace(prepared, artifacts=await anyio.to_thread.run_sync(
            materialize_owned_evidence, prepared.artifacts, prepared.launch.workspace, self.binding.artifacts))
        for name, artifact in {**prepared.artifacts, **capture.artifacts,
                               "native.trace": capture.stdout, "native.stderr": capture.stderr}.items():
            recorder.record_artifact(name, artifact)
        try:
            native = self.dialect.parse(capture)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            native = CliCapture(events=(), terminal=None, final_output=None, output_state="unknown",
                native_refs={}, issues=(f"Native trace projection failed: {type(exc).__name__}: {exc}",))
        run = self.map_capture(request, capture, native, prepared)
        # Publish terminal evidence before optional scenario exports can fail.
        for name, artifact in run.artifacts.items():
            recorder.record_artifact(name, artifact)
        for execution in run.executions:
            recorder.record_execution(execution)
        if self.scenario is not None:
            try:
                run = await anyio.to_thread.run_sync(self.scenario.finish, request, prepared, run, self.binding.artifacts)
            except BaseException:
                # A scenario can enrich a projected event using its native tool
                # receipt. Publish each final event only once; preserve the base
                # projection if that enrichment/export fails.
                for event in run.events:
                    recorder.record_event(event)
                raise
        run = await anyio.to_thread.run_sync(materialize_owned_evidence, run,
                                             prepared.launch.workspace, self.binding.artifacts)
        for event in run.events:
            recorder.record_event(event)
        if capture.stop.confirmed:
            work, root = prepared.launch.workspace, self.binding.workspace_root.resolve()
            if work.is_symlink() or work.resolve() == root or not work.resolve().is_relative_to(root):
                raise o.ConfigurationError("Refusing cleanup outside the owned invocation directory")
            try:
                await anyio.to_thread.run_sync(shutil.rmtree, work)
            except OSError as exc:
                receipt = self.binding.artifacts.write_bytes("native.cleanup", str(exc).encode("utf-8"), "text/plain")
                run = replace(run, artifacts={**run.artifacts, "native.cleanup": receipt})
                recorder.record_artifact("native.cleanup", receipt)
        return run

    def map_capture(self, request, capture, native, prepared):
        artifacts = dict(prepared.artifacts)
        artifacts.update(capture.artifacts)
        artifacts.update({"native.trace": capture.stdout, "native.stderr": capture.stderr})
        execution_id = request.run_id + "/native"
        issues = list(native.issues)
        output_state = native.output_state
        if native.output_state == "available" and prepared.schema is not None:
            try:
                schema_validator(prepared.schema).validate(plain(native.final_output))
            except Exception as exc:
                issues.append(f"Response schema: {exc}")
        terminal = native.terminal
        status = self.dialect.status(native, capture)
        if capture.deadline_exceeded:
            status = "timed_out" if capture.stop.confirmed else "infrastructure_error"
            if not capture.stop.confirmed:
                issues.append("Deadline exceeded, but native process-tree stop is unconfirmed")
        elif not capture.stop.confirmed:
            issues.append("Native process-tree termination is unconfirmed; duration remains unknown")
        elif status == "completed" and (issues or capture.output_complete.value is not True):
            # A terminal agent result remains terminal even if projection is incomplete.
            issues.append("Native terminal outcome retained; normalized capture has diagnostics")
        error = None
        if status not in ("completed",) or issues:
            error = o.ErrorRecord(code="native." + status,
                                  message="; ".join(issues) or f"Native process ended with {status}",
                                  evidence=(o.EvidenceRef(artifact=capture.stdout), o.EvidenceRef(artifact=capture.stderr)))
        receipt = {
            "profile": self.dialect.profile, "native_id": native.native_refs.get("invocation_id"),
            "runtime_revision": prepared.runtime_revision, "adapter": plain(self.ref),
            "model": prepared.model, "model_basis": "explicit CLI request; resolution may be unexposed",
            "deployment": plain(self.binding.deployment.value) if self.binding.deployment.status == "observed" else None,
            "candidate_fingerprint": request.candidate.fingerprint(), "run_id": request.run_id,
            "argv": prepared.launch.argv, "exit_code": capture.exit_code, "stop": {
                "requested": capture.stop.requested, "confirmed": capture.stop.confirmed, "reason": capture.stop.reason},
            "projection_issues": issues,
        }
        receipt_ref = self.binding.artifacts.write_bytes("native.receipt", json_bytes(receipt), "application/json")
        artifacts["native.receipt"] = receipt_ref
        evidence = o.EvidenceRef(artifact=receipt_ref)
        resources = self.dialect.resources(native)
        if resources.cost_scope != request.policy.cost_scope:
            # Preserve independently observed tokens without relabelling a
            # model-only bill as an observed charge over a broader/different scope.
            resources = o.Resources(cost_usd=unknown(
                "Native CLI usage does not establish cost over the requested categories"),
                cost_scope=request.policy.cost_scope, input_tokens=resources.input_tokens,
                output_tokens=resources.output_tokens, human_minutes=resources.human_minutes)
        execution = o.Execution(id=execution_id, slot="native", retry_index=0, parent_id=None,
                                status=status, started_at=capture.started_at, ended_at=capture.ended_at,
                                effective_config=observed({"runtime_revision": prepared.runtime_revision,
                                    "requested_model": prepared.model, "resolved_model": self.dialect.resolved_model(native),
                                    "candidate_fingerprint": request.candidate.fingerprint()}, evidence=(evidence,)),
                                resources=resources, native_refs=native.native_refs, error=error)
        events = []
        for index, skill in enumerate(prepared.skill_loads):
            ref = o.EvidenceRef(artifact=artifacts[skill["artifact"]], description="Exact skill bytes injected in native prompt")
            events.append(o.Event(id=f"{execution_id}/skill/{index}", execution_id=execution_id, kind="skill_loaded",
                                  at=capture.started_at, fields={k: v for k, v in skill.items() if k != "artifact"},
                                  inputs=(ref,), outputs=(o.EvidenceRef(artifact=artifacts["native.prompt"]),), source=ref))
        events.extend(self.dialect.events(native, execution_id))
        return o.Run(id=request.run_id, assignment_id=request.assignment.id, status=status,
                     cost_scope=request.policy.cost_scope, output=native.final_output, output_state=output_state,
                     artifacts=artifacts, executions=(execution,), started_at=capture.started_at,
                     ended_at=capture.ended_at, environment=observed({"scopes": {"workspace": {
                         "namespace": str(prepared.launch.workspace), "observed_state": "empty at preparation",
                         "reset_method": "new-directory"}}, "deployment": receipt["deployment"]}, evidence=(evidence,)),
                     execution_inventory_complete=unknown("Native CLI export has aggregate usage; complete child/retry inventory is not established"),
                     output_sources=(execution_id,) if output_state == "available" else (),
                     native_refs=native.native_refs, events=tuple(events), error=error)
