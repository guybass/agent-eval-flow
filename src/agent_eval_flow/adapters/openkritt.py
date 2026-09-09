"""Pinned OpenKritt scan lifecycle, native workflow and tool evidence capture.

HTTP routes are verified against 1ba10d410b4a0a309bacf5bc2d14ec1c27ed8a09.
Native PostgreSQL/engine capture and confirmed process stopping are explicit
deployment bindings: the public scan API does not provide those capabilities.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass, replace
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from functools import partial
from collections.abc import Mapping
import hashlib
import importlib
import io
import json
from pathlib import Path, PurePosixPath
import shutil
import time
from typing import Protocol
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen, url2pathname
import zipfile

import anyio

from .. import objects as o
from ..objects.identity import plain
from ..objects.values import unknown, observed, zero_resources

UPSTREAM_REVISION = "1ba10d410b4a0a309bacf5bc2d14ec1c27ed8a09"
TERMINAL = {"completed", "stopped", "failed", "paused"}
STATUS = {"completed": "completed", "stopped": "cancelled", "failed": "agent_error",
          "paused": "paused", "pending": "running", "queued": "running", "running": "running",
          "prewarming_cache": "running", "post_processing": "running", "rate_limited": "paused"}


def json_bytes(value):
    return json.dumps(plain(value), ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def artifact_bytes(ref):
    parsed = urlparse(ref.uri)
    path = Path(url2pathname(parsed.path)) if parsed.scheme == "file" and parsed.netloc in {"", "localhost"} else Path(ref.uri)
    if not path.is_absolute():
        raise o.StorageError("Native evidence must be materialized locally before mapping")
    data = path.read_bytes()
    if ref.sha256 is not None and hashlib.sha256(data).hexdigest() != ref.sha256:
        raise o.StorageError("Native evidence hash mismatch: " + ref.uri)
    return data


def evidence_refs(*records):
    """Walk typed capture records without treating arbitrary strings as files."""
    seen, emitted = set(), set()

    def walk(value):
        if isinstance(value, o.EvidenceRef):
            key = (value.artifact.uri, value.artifact.sha256, value.artifact.media_type,
                   value.locator, value.description)
            if key not in emitted:
                emitted.add(key)
                yield value
            return
        if isinstance(value, Mapping):
            children = value.values()
        elif isinstance(value, (tuple, list)):
            children = value
        elif is_dataclass(value) and not isinstance(value, type):
            children = (getattr(value, item.name) for item in fields(value))
        else:
            return
        if id(value) in seen:
            return
        seen.add(id(value))
        for child in children:
            yield from walk(child)

    for record in records:
        yield from walk(record)


def build_evidence_bundle(*, scan_id, run_id, assignment_id, artifacts,
                          manifest_raw, workflow_raw, files, evidence=(), original_bundle=None):
    """Build one indexed archive containing named artifacts and nested evidence.

``provenance`` links each EvidenceRef to its portable archive path while
preserving the original URI, locator and description. Existing named-artifact
and source-file contracts remain unchanged. No URI is fetched over the network.
"""
    named = {key: ref for key, ref in artifacts.items() if key != "openkritt.bundle"}
    mapping = {key: "artifacts/" + str(index) for index, key in enumerate(named)}
    contents = {mapping[key]: artifact_bytes(ref) for key, ref in named.items()}
    by_hash = {hashlib.sha256(data).hexdigest(): path for path, data in contents.items()}
    for name in files:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
            raise o.StorageError("Native bundle source path must stay relative to the source root")
    contents.update({"inputs/source_manifest.json": manifest_raw, "configuration/workflow.json": workflow_raw,
                     **{"inputs/review_target/" + path: data for path, data in files.items()}})
    provenance, read = [], {}
    for source in evidence_refs(evidence):
        ref = source.artifact
        key = (ref.uri, ref.sha256, ref.media_type)
        if key not in read:
            read[key] = artifact_bytes(ref)
        data = read[key]
        digest = hashlib.sha256(data).hexdigest()
        path = by_hash.get(digest, "provenance/" + digest)
        contents[path] = data
        by_hash[digest] = path
        provenance.append({"path": path, "artifact": plain(ref), "locator": source.locator,
                           "description": source.description})
    derivation = None
    if original_bundle is not None:
        original_ref, original_bytes = original_bundle
        if original_ref.sha256 is not None and hashlib.sha256(original_bytes).hexdigest() != original_ref.sha256:
            raise o.StorageError("Original native bundle hash mismatch")
        path = "provenance/original-bundle.zip"
        contents[path] = original_bytes
        derivation = {"operation": "offline-provenance-completion", "path": path, "artifact": plain(original_ref)}
    index = {"schema_version": "aef-openkritt-evidence-bundle/1", "scan_id": scan_id,
             "run_id": run_id, "assignment_id": assignment_id, "artifacts": mapping,
             "provenance": provenance,
             "files": [{"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                       for path, data in contents.items()]}
    if derivation is not None:
        index["derivation"] = derivation
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, data in contents.items():
            archive.writestr(path, data)
        archive.writestr("manifest.json", json_bytes(index))
    return bundle.getvalue()


def complete_evidence_bundle(run):
    """Repackage a saved capture offline, retaining the original archive intact.

Returns new bytes only. The caller must write a new ArtifactRef and a derived
RunSet; existing saved records and the old archive are never changed here.
"""
    original_ref = run.artifacts["openkritt.bundle"]
    original = artifact_bytes(original_ref)
    with zipfile.ZipFile(io.BytesIO(original)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise o.StorageError("Original native bundle has duplicate paths")
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
                raise o.StorageError("Original native bundle has an unsafe path")
        index = json.loads(archive.read("manifest.json"))
        if index.get("schema_version") != "aef-openkritt-evidence-bundle/1":
            raise o.StorageError("Unsupported original native bundle schema")
        listed = {row["path"]: row for row in index["files"]}
        if len(listed) != len(index["files"]) or set(listed) != set(names) - {"manifest.json"}:
            raise o.StorageError("Original native bundle inventory differs from its index")
        if (index.get("run_id") != run.id or index.get("assignment_id") != run.assignment_id
                or index.get("scan_id") != run.native_refs.get("scan_id")):
            raise o.CaptureValidationError("Original bundle belongs to another native capture")
        for name, row in listed.items():
            data = archive.read(name)
            if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise o.StorageError("Original native bundle member hash mismatch")
        expected_names = set(run.artifacts) - {"openkritt.bundle"}
        if set(index.get("artifacts", {})) != expected_names or any(
                archive.read(index["artifacts"][name]) != artifact_bytes(run.artifacts[name]) for name in expected_names):
            raise o.StorageError("Original named artifacts differ from the saved capture")
        manifest_raw = archive.read("inputs/source_manifest.json")
        workflow_raw = archive.read("configuration/workflow.json")
        source_files = json.loads(manifest_raw)["files"]
        files = {row["path"]: archive.read("inputs/review_target/" + row["path"]) for row in source_files}
        if len(files) != len(source_files) or any(len(files[row["path"]]) != row["bytes"]
                or hashlib.sha256(files[row["path"]]).hexdigest() != row["sha256"] for row in source_files):
            raise o.StorageError("Original source files differ from their manifest")
    return build_evidence_bundle(scan_id=run.native_refs.get("scan_id"), run_id=run.id,
        assignment_id=run.assignment_id, artifacts=run.artifacts, manifest_raw=manifest_raw,
        workflow_raw=workflow_raw, files=files, evidence=(run,), original_bundle=(original_ref, original))


@dataclass(frozen=True)
class NativeJSON:
    """Unmodified response/export bytes and their parsed view."""
    raw: bytes

    @property
    def value(self):
        return json.loads(self.raw)


@dataclass(frozen=True)
class HarnessTrace:
    metadata_id: str
    protocol: str
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True)
class WorkflowCapture:
    metadata: NativeJSON
    results: NativeJSON
    traces: tuple[HarnessTrace, ...]
    inventory_complete: o.Observation
    observer: o.VersionRef


class OpenKrittCollector(Protocol):
    async def collect(self, scan_id: str) -> WorkflowCapture: ...


class LocalRepositoryStager:
    """Stage manifest files into an immediate child of an existing repo mount."""

    def __init__(self, mount):
        self.mount = Path(mount).resolve()
        if not self.mount.is_dir():
            raise o.ConfigurationError("OpenKritt local repository mount does not exist")
        self._owned = set()

    async def stage(self, namespace, files):
        if not namespace or Path(namespace).name != namespace or any(c in namespace for c in "/\\:") or namespace in {".", ".."}:
            raise o.ConfigurationError("Repository namespace must be one immediate directory name")
        root = (self.mount / namespace).resolve()
        if root.parent != self.mount or root.exists():
            raise o.ConfigurationError("Repository namespace must be fresh and owned by this invocation")
        root.mkdir()
        self._owned.add(root)
        staged = []
        try:
            for relative, data in files.items():
                target = (root / relative).resolve()
                if not target.is_relative_to(root):
                    raise o.ConfigurationError("Repository asset escaped its namespace")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                staged.append({"path": relative, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
        except BaseException:
            await self.release(namespace)
            raise
        return tuple(staged)

    async def release(self, namespace):
        root = (self.mount / namespace).resolve()
        if root not in self._owned or root.parent != self.mount or root.is_symlink():
            raise o.ConfigurationError("Refusing to release an unowned repository namespace")
        shutil.rmtree(root)
        self._owned.remove(root)


class OpenKrittHTTPError(RuntimeError):
    def __init__(self, status, body):
        self.status, self.body = status, body
        super().__init__(f"OpenKritt HTTP {status}: {body[:2048].decode(errors='replace')}")


class OpenKrittHTTPConnection:
    """Existing HTTP API plus supplied scan-scoped collector and stop controller.

``stopper.stop_scan(id)`` must confirm the relevant worker processes stopped;
successful HTTP PATCH delivery alone is insufficient. No credentials or URL
headers are serialized into the study or execution receipts.
"""
    def __init__(self, *, base_url, stager, collector, stopper, upstream_ref,
                 deployment, headers=None, transport=None, timeout_s=30.0):
        if urlparse(base_url).scheme not in {"http", "https"}:
            raise o.ConfigurationError("OpenKritt base URL must use HTTP(S)")
        self.base_url, self.headers = base_url.rstrip("/"), dict(headers or {})
        self.stager, self.collector, self.stopper = stager, collector, stopper
        self.upstream_ref, self.deployment = upstream_ref, deployment
        self.transport, self.timeout_s = transport, timeout_s

    def capabilities(self):
        hard_deadline = bool(getattr(self.stopper, "confirmed_stop_supported", False)) and bool(getattr(self.stopper, "hard_deadline_supported", False))
        return o.BackendCapabilities(wall_time_limit=hard_deadline,
            token_limit=False, cost_limit=False, reset_state=True)

    async def arm_deadline(self, namespace, wall_time_s):
        """The prepared controller covers even a POST with a lost acknowledgment."""
        return await self.stopper.arm_deadline(namespace, wall_time_s)

    async def disarm_deadline(self, handle):
        await self.stopper.disarm_deadline(handle)

    async def _request(self, method, path, payload=None):
        data = None if payload is None else json_bytes(payload)
        headers = {**self.headers, "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self.transport is not None:
            status, content = await self.transport(method, self.base_url + path, headers, data)
        else:
            def send():
                request = Request(self.base_url + path, data=data, headers=headers, method=method)
                try:
                    with urlopen(request, timeout=self.timeout_s) as response:
                        return response.status, response.read()
                except HTTPError as exc:
                    return exc.code, exc.read()
            status, content = await anyio.to_thread.run_sync(send, abandon_on_cancel=True)
        if status < 200 or status >= 300:
            raise OpenKrittHTTPError(status, content)
        return content

    async def stage_fixture(self, files, *, namespace):
        return await self.stager.stage(namespace, files)

    async def import_workflow(self, portable):
        # POST /api/workflows accepts the workflow body, not the portable wrapper.
        document = portable.value
        if document.get("kind") != "open-kritt-workflow" or document.get("version") != 2:
            raise o.ConfigurationError("Expected a version-2 OpenKritt portable workflow")
        return NativeJSON(await self._request("POST", "/api/workflows", document["workflow"]))

    async def create_scan(self, payload):
        return NativeJSON(await self._request("POST", "/api/scans", payload))

    async def get_scan(self, scan_id):
        return NativeJSON(await self._request("GET", "/api/scans/" + quote(scan_id, safe="")))

    async def get_findings(self, scan_id):
        return NativeJSON(await self._request("GET", "/api/scans/" + quote(scan_id, safe="") + "/vulnerabilities"))

    async def get_post_script(self, identifier):
        return NativeJSON(await self._request("GET", "/api/post-scripts/" + quote(str(identifier), safe="")))

    async def export_scan(self, scan_id):
        return await self._request("GET", "/api/scans/" + quote(scan_id, safe="") + "/export")

    async def collect(self, scan_id):
        return await self.collector.collect(scan_id)

    async def stop_scan(self, scan_id):
        return await self.stopper.stop_scan(scan_id)

    async def release_namespace(self, namespace):
        await self.stager.release(namespace)


def parse_time(value):
    if value is None:
        return None
    timestamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("Native timestamps require an explicit timezone")
    return timestamp.astimezone(timezone.utc)


def tool_events(*, execution_id, trace, artifact):
    """Decode actual tool results; retain all other native records in the artifact."""
    events, invocations, used = [], {}, set()
    for number, line in enumerate(trace.stdout.decode("utf-8", errors="replace").splitlines(), 1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        calls = []
        if trace.protocol == "codex-jsonl":
            item = record.get("item", {})
            if record.get("type") == "item.completed" and isinstance(item, dict) and item.get("type") == "command_execution":
                calls.append((str(item["id"]), "command_execution", {"command": item["command"]},
                    {"output": item["aggregated_output"], "exit_code": item["exit_code"]}, number))
        elif trace.protocol == "claude-stream-json":
            message = record.get("message", {})
            for block in message.get("content", []) if isinstance(message, dict) and isinstance(message.get("content", []), list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    if block["id"] in invocations:
                        raise ValueError("Duplicate native tool invocation ID")
                    invocations[block["id"]] = (number, block)
                elif block.get("type") == "tool_result":
                    source_line, invocation = invocations[block["tool_use_id"]]
                    calls.append((str(invocation["id"]), invocation["name"], invocation["input"],
                        {"content": block["content"], "is_error": block.get("is_error", False)}, source_line))
        else:
            raise o.ConfigurationError("Unsupported native harness protocol: " + trace.protocol)
        for native_id, name, arguments, result, source_line in calls:
            if native_id in used:
                raise ValueError("Duplicate native tool completion ID")
            used.add(native_id)
            source = o.EvidenceRef(artifact=artifact, locator=f"line:{source_line}", description="Native tool invocation")
            output = o.EvidenceRef(artifact=artifact, locator=f"line:{number}", description="Native tool result")
            events.append(o.Event(id=f"{execution_id}/tool/{len(events)}", execution_id=execution_id,
                kind="tool_call", at=None, fields={"native_id": native_id, "name": name,
                    "arguments": arguments, "result": result}, source=source, inputs=(source,), outputs=(output,)))
    return tuple(events)


def metadata_resources(row, scope, evidence):
    raw = row.get("raw_token_usage")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = None
    raw = raw if isinstance(raw, dict) else {}
    def tokens(*names):
        for name in names:
            value = raw.get(name)
            if type(value) is int and value >= 0:
                return observed(value, "Native token receipt: " + name, (evidence,))
        return unknown("Native metadata does not expose this token quantity")
    return o.Resources(cost_scope=scope, cost_usd=unknown("Native token usage is not a dollar charge"),
        input_tokens=tokens("input_tokens", "prompt_tokens"), output_tokens=tokens("output_tokens", "completion_tokens"),
        human_minutes=unknown("Native metadata does not record human work"))


class OpenKrittBackend:
    def __init__(self, *, binding, service, poll_interval_s=1.0):
        if poll_interval_s <= 0:
            raise o.ConfigurationError("Poll interval must be positive")
        self.binding, self.service, self.poll_interval_s = binding, service, poll_interval_s
        self.ref = binding.ref

    def capabilities(self):
        return self.service.capabilities()

    def run(self, request, *, recorder):
        return anyio.from_thread.run(self.arun, request, recorder)

    def prepare(self, request):
        options = request.candidate.settings.get("openkritt_e2e", request.candidate.settings.get("openkritt", {}))
        if not isinstance(options, Mapping):
            raise o.ConfigurationError("OpenKritt options must be an object")
        required_files = ("source_manifest", "workflow_file", "fixture_dir")
        if any(not isinstance(options.get(key), str) or not options[key] for key in required_files):
            raise o.ConfigurationError("OpenKritt requires source_manifest, workflow_file and fixture_dir paths")
        if self.binding.upstream_ref != self.service.upstream_ref or self.binding.upstream_ref.revision != options.get("upstream_revision", UPSTREAM_REVISION):
            raise o.ConfigurationError("Prepared OpenKritt revision differs from declared upstream pin")
        if self.binding.upstream_ref.revision != UPSTREAM_REVISION:
            raise o.ConfigurationError("This OpenKritt dialect has not been verified for the requested revision")
        manifest_path, workflow_path = Path(options["source_manifest"]), Path(options["workflow_file"])
        try:
            manifest_raw, workflow_raw = manifest_path.read_bytes(), workflow_path.read_bytes()
            manifest, portable = json.loads(manifest_raw), json.loads(workflow_raw)
        except (OSError, ValueError) as exc:
            raise o.ConfigurationError("Cannot read declared OpenKritt input/configuration: " + str(exc)) from exc
        if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
            raise o.ConfigurationError("Source manifest must contain a files array")
        if not isinstance(portable, dict) or portable.get("kind") != "open-kritt-workflow" or portable.get("version") != 2:
            raise o.ConfigurationError("Expected a version-2 OpenKritt portable workflow")
        root, files = Path(options["fixture_dir"]).resolve(), {}
        for entry in manifest["files"]:
            if not isinstance(entry, dict) or not {"path", "sha256", "bytes"} <= entry.keys():
                raise o.ConfigurationError("Manifest files require path, sha256 and bytes")
            relative = PurePosixPath(entry["path"])
            path = (root / str(relative)).resolve()
            if relative.is_absolute() or ".." in relative.parts or "\\" in str(relative) or ":" in str(relative) or not path.is_relative_to(root):
                raise o.ConfigurationError("Invalid source manifest path")
            data = path.read_bytes()
            if str(relative) in files:
                raise o.ConfigurationError("Duplicate source manifest path: " + str(relative))
            if hashlib.sha256(data).hexdigest() != entry["sha256"] or len(data) != entry["bytes"]:
                raise o.ConfigurationError("Repository source hash/length mismatch: " + str(relative))
            files[str(relative)] = data
        task = request.input.tables["units"][0]
        namespace = str(options.get("repo_full", task["repo_full"]))
        if not namespace or Path(namespace).name != namespace or any(c in namespace for c in "/\\:") or namespace in {".", ".."}:
            raise o.ConfigurationError("Repository namespace must be one immediate directory name")
        native_options = request.candidate.settings.get("scan_options", {})
        if not isinstance(native_options, Mapping):
            raise o.ConfigurationError("scan_options must be an object")
        scan_options = dict(native_options)
        if not scan_options.get("severity_ranker"):
            raise o.ConfigurationError("Declare OpenKritt scan_options.severity_ranker; native create-scan requires it")
        if not scan_options.get("model") or not scan_options.get("harness") or not scan_options.get("model_provider"):
            raise o.ConfigurationError("Declare native model, harness and model_provider in scan_options")
        if not options.get("post_script_id"):
            raise o.ConfigurationError("A configured native post_script_id is required")
        protected = {"workflowId", "workflow_id", "postScriptId", "post_script_id", "repo_kind", "repo_full", "repo_scope", "dependencies", "configuration"}
        if protected & scan_options.keys():
            raise o.ConfigurationError("Put repository/workflow/configuration fields in OpenKritt options, not conflicting scan_options")
        return options, task, namespace, files, manifest_raw, workflow_raw, scan_options

    async def arun(self, request, recorder):
        options, task, namespace, files, manifest_raw, workflow_raw, scan_options = self.prepare(request)
        started, deadline = datetime.now(timezone.utc), time.monotonic() + request.policy.budget.wall_time_s
        cache, artifacts, snapshots = self.binding.artifacts, {}, []
        scan_id, scan, staged, workflow, post_script = None, None, (), None, None
        failure, stop = None, None
        deadline_handle = None
        submission_attempted = False
        capture = None
        findings = None
        export_state = {"status": "unknown", "reason": "Native export not yet retrieved"}

        def store(name, data, media_type="application/json"):
            ref = cache.write_bytes(name, data, media_type)
            artifacts[name] = ref
            recorder.record_artifact(name, ref)
            return ref

        try:
            if self.capabilities().wall_time_limit:
                deadline_handle = await self.service.arm_deadline(namespace, request.policy.budget.wall_time_s)
            with anyio.fail_after(max(0.001, deadline - time.monotonic())):
                staged = await self.service.stage_fixture(files, namespace=namespace)
                expected = {p: hashlib.sha256(data).hexdigest() for p, data in files.items()}
                if {row["path"]: row["sha256"] for row in staged} != expected:
                    raise o.ConfigurationError("Observed staged repository hashes differ from declared input")
                workflow = await self.service.import_workflow(NativeJSON(workflow_raw))
                store("openkritt.workflow", workflow.raw)
                post_script = await self.service.get_post_script(options["post_script_id"])
                store("openkritt.post_script", post_script.raw)
                payload = {**scan_options, "workflowId": workflow.value["id"], "postScriptId": options["post_script_id"],
                    "repo_kind": task.get("repo_kind", "local"), "repo_full": namespace,
                    "repo_scope": task.get("repo_scope", "full repository"), "dependencies": options.get("dependencies", []),
                    "configuration": options.get("scan_configuration", {})}
                submission_attempted = True
                scan = await self.service.create_scan(payload)
                scan_id = str(scan.value["id"])
                store("openkritt.acknowledgment", scan.raw)  # immutable acknowledgment survives later failure
                snapshots.append(scan.value)
                while scan.value.get("status") not in TERMINAL:
                    if scan.value.get("status") not in STATUS:
                        raise ValueError("Unknown native scan status: " + str(scan.value.get("status")))
                    await anyio.sleep(self.poll_interval_s)
                    scan = await self.service.get_scan(scan_id)
                    if str(scan.value["id"]) != scan_id:
                        raise ValueError("Scan polling returned a different native ID")
                    store("openkritt.scan_snapshot." + str(len(snapshots)), scan.raw)
                    snapshots.append(scan.value)
        except o.ConfigurationError:
            if staged:
                with anyio.CancelScope(shield=True):
                    await self.service.release_namespace(namespace)
            if deadline_handle is not None:
                with anyio.CancelScope(shield=True):
                    await self.service.disarm_deadline(deadline_handle)
            raise
        except TimeoutError:
            failure = "Assignment deadline expired"
            if scan_id:
                with anyio.CancelScope(shield=True):
                    with anyio.move_on_after(30):
                        stop = await self.service.stop_scan(scan_id)
        except Exception as exc:
            failure = f"Native lifecycle failed: {type(exc).__name__}: {exc}"
            if scan_id and scan.value.get("status") not in TERMINAL:
                with anyio.CancelScope(shield=True):
                    with anyio.move_on_after(30):
                        stop = await self.service.stop_scan(scan_id)
        except BaseException:
            with anyio.CancelScope(shield=True):
                confirmed = False
                if scan_id:
                    with anyio.move_on_after(30):
                        cancellation_stop = await self.service.stop_scan(scan_id)
                        confirmed = cancellation_stop.confirmed
                        store("openkritt.stop", json_bytes({"requested": cancellation_stop.requested,
                            "confirmed": confirmed, "reason": cancellation_stop.reason}))
                if staged and (confirmed or not submission_attempted or (scan and scan.value.get("status") in {"completed", "failed"})):
                    await self.service.release_namespace(namespace)
                    if deadline_handle is not None:
                        await self.service.disarm_deadline(deadline_handle)
            raise

        native_status = scan.value.get("status") if scan else None
        status = STATUS.get(native_status, "infrastructure_error")
        if failure and native_status not in TERMINAL:
            status = "timed_out" if stop is not None and stop.confirmed and failure.startswith("Assignment deadline") else "infrastructure_error"
        control_id = request.run_id + "/scan"
        control_resources = zero_resources(request.policy.cost_scope)
        if any(category != "model" for category in request.policy.cost_scope):
            control_resources = replace(control_resources, cost_usd=unknown("Native API control-plane non-model cost was not exported"))
        executions = [o.Execution(id=control_id, slot="scan-control", retry_index=0, parent_id=None,
            role="main", status=status, started_at=started,
            ended_at=datetime.now(timezone.utc) if native_status in {"completed", "failed"} or (stop and stop.confirmed) else None,
            effective_config=observed({"native_scan_id": scan_id, "scan_options": scan_options}, "Native API control capture"),
            resources=control_resources, native_refs={"scan_id": scan_id} if scan_id else {},
            error=o.ErrorRecord(code="native_lifecycle", message=failure) if failure else None)]
        recorder.record_execution(executions[0])

        def retain(name, data, media_type="application/json"):
            nonlocal failure
            try:
                return store(name, data, media_type)
            except (OSError, o.StorageError) as exc:
                failure = (failure + "; " if failure else "") + f"{name} export failed: {exc}"
                return None

        try:
            if scan_id:
                store("openkritt.scan", scan.raw)
                with anyio.fail_after(60):
                    findings = await self.service.get_findings(scan_id)
                    if not isinstance(findings.value, list):
                        raise ValueError("Native findings must be an array")
                    store("openkritt.findings", findings.raw)
                    capture = await self.service.collect(scan_id)
                    store("openkritt.step_metadata", capture.metadata.raw)
                    store("openkritt.step_results", capture.results.raw)
                    if findings.value:
                        try:
                            data = await self.service.export_scan(scan_id)
                            # Validate the ZIP envelope while retaining the untouched bytes.
                            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                                if archive.testzip() is not None:
                                    raise ValueError("Native finding archive failed CRC validation")
                            store("openkritt.export", data, "application/zip")
                            export_state = {"status": "available"}
                        except OpenKrittHTTPError as exc:
                            store("openkritt.export_error", exc.body)
                            export_state = {"status": "unavailable", "reason": str(exc)}
                    else:
                        export_state = {"status": "unavailable", "reason": "Pinned native export requires at least one finding"}
        except Exception as exc:
            failure = (failure + "; " if failure else "") + f"Evidence export failed: {type(exc).__name__}: {exc}"
            if "openkritt.step_metadata" not in artifacts:
                capture = None
        finally:
            safe_to_release = not submission_attempted or (scan and scan.value.get("status") in {"completed", "failed"}) or (stop and stop.confirmed)
            if staged and safe_to_release:
                with anyio.CancelScope(shield=True):
                    try:
                        await self.service.release_namespace(namespace)
                    except Exception as exc:
                        failure = (failure + "; " if failure else "") + "Namespace cleanup failed: " + str(exc)
            elif staged:
                failure = (failure + "; " if failure else "") + "Staged namespace retained because native termination is unconfirmed"
            if deadline_handle is not None and safe_to_release:
                with anyio.CancelScope(shield=True):
                    await self.service.disarm_deadline(deadline_handle)

        if snapshots:
            retain("openkritt.status_history", json_bytes(snapshots))
        if stop is not None:
            retain("openkritt.stop", json_bytes({"requested": stop.requested, "confirmed": stop.confirmed, "reason": stop.reason}))
        events, traces = [], []
        if capture is not None:
            seen = set()
            for index, row in enumerate(capture.metadata.value):
                if str(row["scan_id"]) != scan_id or str(row["id"]) in seen:
                    raise o.CaptureValidationError("Metadata IDs must be unique and belong to the requested scan")
                seen.add(str(row["id"]))
                execution_id = request.run_id + "/step/" + str(row["id"])
                source = o.EvidenceRef(artifact=artifacts["openkritt.step_metadata"], locator=f"/{index}", description="Native workflow attempt")
                at = parse_time(row.get("run_started_at"))
                finish = at + timedelta(milliseconds=float(row["run_time_ms"])) if at and row.get("run_time_ms") is not None else None
                state = STATUS.get(row.get("status"), "infrastructure_error")
                refs = {"scan_id": scan_id, "step_metadata_id": str(row["id"])}
                refs.update({key: str(row[key]) for key in ("step_id", "repeat_run", "prev_id", "prev_table") if row.get(key) is not None})
                executions.append(o.Execution(id=execution_id, slot="native-step/" + str(row.get("step_id", row["id"])),
                    retry_index=int(row.get("retry_index", 0)), parent_id=control_id, role="attempt", status=state,
                    started_at=at, ended_at=finish, effective_config=observed(row, "Native persisted attempt", (source,)),
                    resources=metadata_resources(row, request.policy.cost_scope, source), native_refs=refs))
                recorder.record_execution(executions[-1])
                events.append(o.Event(id=execution_id + "/result", execution_id=execution_id, kind="workflow_step", at=finish,
                    fields={"step_metadata_id": str(row["id"]), "repeat_run": row.get("repeat_run"),
                        "output": row.get("output_json"), "native": row}, source=source, outputs=(source,)))
            for trace in capture.traces:
                if trace.metadata_id not in seen:
                    raise o.CaptureValidationError("Harness trace has no captured native metadata identity")
                stdout_key, stderr_key = "openkritt.stdout." + trace.metadata_id, "openkritt.stderr." + trace.metadata_id
                stdout = retain(stdout_key, trace.stdout, "application/x-ndjson")
                stderr = retain(stderr_key, trace.stderr, "text/plain")
                if stdout is None or stderr is None:
                    continue
                traces.append({"metadata_id": trace.metadata_id, "protocol": trace.protocol,
                    "stdout_artifact": stdout_key, "stderr_artifact": stderr_key})
                try:
                    events.extend(tool_events(execution_id=request.run_id + "/step/" + trace.metadata_id, trace=trace, artifact=stdout))
                except (ValueError, KeyError, TypeError, o.ConfigurationError) as exc:
                    failure = (failure + "; " if failure else "") + "Native tool projection failed: " + str(exc)
        for event in events:
            recorder.record_event(event)
        inventory = capture.inventory_complete if capture is not None else unknown("Complete native workflow inventory was not captured")
        capture_index = {"schema_version": "aef-openkritt-capture/2", "scan_id": scan_id,
            "source_manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(), "workflow_sha256": hashlib.sha256(workflow_raw).hexdigest(),
            "staged_files": list(staged), "metadata_inventory_complete": inventory.status == "observed" and inventory.value is True,
            "observer": {"name": capture.observer.name, "revision": capture.observer.revision} if capture else None,
            "traces": traces, "capture_error": failure}
        retain("openkritt.capture", json_bytes(capture_index))
        try:
            bundle = build_evidence_bundle(scan_id=scan_id, run_id=request.run_id,
                assignment_id=request.assignment.id, artifacts=artifacts, manifest_raw=manifest_raw,
                workflow_raw=workflow_raw, files=files,
                evidence=(inventory, self.service.deployment, executions, events))
            retain("openkritt.bundle", bundle, "application/zip")
        except (OSError, o.StorageError, zipfile.BadZipFile) as exc:
            failure = (failure + "; " if failure else "") + "Evidence bundle failed: " + str(exc)
        error = o.ErrorRecord(code="capture_incomplete", message=failure) if failure else None
        return o.Run(id=request.run_id, assignment_id=request.assignment.id, status=status,
            cost_scope=request.policy.cost_scope, output={"scan_id": scan_id, "native_status": native_status,
                "findings": findings.value if findings is not None else None, "export": export_state} if scan else None,
            output_state="available" if scan else "unknown", artifacts=artifacts, executions=tuple(executions),
            started_at=started, ended_at=executions[0].ended_at, environment=self.service.deployment,
            execution_inventory_complete=inventory, output_sources=(control_id,) if scan else (),
            native_refs={"scan_id": scan_id} if scan_id else {}, events=tuple(events), error=error)


def make_backend(config, *, workspace):
    """Live-profile hook for the prepared API, collector, staging and stop bindings."""
    from .common import AdapterBinding
    from ..storage.artifacts import ArtifactCache
    path = config.get("connection_factory")
    if not isinstance(path, str) or ":" not in path:
        raise o.ConfigurationError("OpenKritt profile requires connection_factory='module:callable' supplying its prepared API/collector/stop controller")
    module, name = path.split(":", 1)
    service = getattr(importlib.import_module(module), name)(config, workspace=Path(workspace))
    binding = AdapterBinding(ref=o.VersionRef(**config["backend_ref"]), upstream_ref=service.upstream_ref,
        workspace_root=Path(workspace), artifacts=ArtifactCache(Path(workspace) / "evidence"), deployment=service.deployment)
    return OpenKrittBackend(binding=binding, service=service, poll_interval_s=config.get("poll_interval_s", 1.0))
