"""Concrete bindings for an exclusive, task-owned local OpenKritt Compose stack.

The upstream engine remains responsible for execution. PostgreSQL supplies the
original metadata/results; ``openkritt_observer.py`` preserves process streams
that upstream otherwise discards on successful steps. This module does not
start containers, provision infrastructure or load provider credentials.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import threading

import anyio

from agent_eval_flow import objects as o
from agent_eval_flow.adapters.openkritt import (
    HarnessTrace, LocalRepositoryStager, NativeJSON, OpenKrittHTTPConnection,
    UPSTREAM_REVISION, WorkflowCapture, json_bytes,
)
from agent_eval_flow.adapters.process import StopEvidence
from agent_eval_flow.objects.values import observed
from agent_eval_flow.storage.artifacts import ArtifactCache
from examples.integrations.openkritt_observer import OBSERVER_REVISION

POST_WORKSPACE_ID_OFFSET = 1_000_000_000  # pinned artifact_cleanup.py


class Commands:
    def run(self, argv, *, input=None, timeout=45):
        completed = subprocess.run(argv, input=input, capture_output=True, timeout=timeout, check=False)
        if completed.returncode:
            # argv/environment may contain deployment details: never echo them.
            raise RuntimeError(f"Local deployment command failed with exit code {completed.returncode}")
        return completed.stdout


def _native_id(value):
    if not re.fullmatch(r"[1-9][0-9]*", str(value)):
        raise o.ConfigurationError("OpenKritt native identifiers must be positive decimal integers")
    return str(value)


class LocalDeployment:
    def __init__(self, config, *, commands=None):
        self.commands = commands or Commands()
        self.docker = str(config.get("docker_executable") or shutil.which("docker") or "docker")
        self.project = str(config["compose_project"])
        if not re.fullmatch(r"aef-[a-z0-9][a-z0-9_-]*", self.project):
            raise o.ConfigurationError("The local stop controller requires a task-owned aef- Compose project")
        self.engine = str(config["engine_container"])
        self.db = str(config["db_container"])
        self.user = str(config.get("postgres_user", "open_kritt"))
        self.database = str(config.get("postgres_database", "open_kritt"))
        self.engine_data = Path(config["engine_data_dir"]).resolve()
        self.repo_mount = Path(config["local_repos_dir"]).resolve()
        self.upstream = Path(config["upstream_dir"]).resolve()
        for directory in (self.engine_data, self.repo_mount, self.upstream):
            if not directory.is_dir():
                raise o.ConfigurationError("Prepared local deployment directory is missing: " + str(directory))
        self.engine_info = self.inspect_owned(self.engine, "engine")
        database_info = self.inspect_owned(self.db, "db")
        if not self.engine_info.get("State", {}).get("Running") or not database_info.get("State", {}).get("Running"):
            raise o.ConfigurationError("Prepared local engine and database containers must be running")
        data_mounts = [row for row in self.engine_info.get("Mounts", []) if row.get("Destination") == "/data"]
        if len(data_mounts) != 1 or data_mounts[0].get("Type") != "bind":
            raise o.ConfigurationError("Task-owned engine requires one /data bind mount")
        self.daemon_data_root = PurePosixPath(data_mounts[0]["Source"])
        if not self.daemon_data_root.is_absolute() or self.daemon_data_root == PurePosixPath("/"):
            raise o.ConfigurationError("Engine /data must bind a specific absolute task directory")

    def inspect_owned(self, container, service):
        records = json.loads(self.commands.run([self.docker, "inspect", str(container)]))
        if not isinstance(records, list) or len(records) != 1:
            raise o.ConfigurationError("Cannot identify one task-owned container")
        result = records[0]
        labels = result.get("Config", {}).get("Labels", {}) or {}
        if labels.get("com.docker.compose.project") != self.project or labels.get("com.docker.compose.service") != service:
            raise o.ConfigurationError("Refusing a container outside the declared task-owned Compose service")
        return result

    def query(self, statement):
        # Statement is a fixed read-only query assembled using validated integers.
        # The pinned database image uses SCRAM even on its local socket. Read
        # its existing password inside the container; no credential crosses
        # stdout, host argv, the profile or an evaluation artifact.
        return self.commands.run([self.docker, "exec", "-i", self.db, "sh", "-c",
                                  'export PGPASSWORD="$POSTGRES_PASSWORD"; exec psql "$@"', "sh", "-X", "-q", "-A", "-t",
                                  "-v", "ON_ERROR_STOP=1", "-U", self.user, "-d", self.database],
                                 input=statement.encode("utf-8"))

    def snapshot(self, scan_id):
        identifier = _native_id(scan_id)
        raw = self.query("SELECT json_build_object("
            "'scan', (SELECT row_to_json(s) FROM public.scans s WHERE s.id = " + identifier + "),"
            "'metadata', COALESCE((SELECT json_agg(m ORDER BY m.id) FROM workflows.step_metadata m WHERE m.scan_id = " + identifier + "), '[]'::json),"
            "'results', COALESCE((SELECT json_agg(r ORDER BY r.id) FROM workflows.step_results r WHERE r.scan_id = " + identifier + "), '[]'::json),"
            "'post_process_metadata', COALESCE((SELECT json_agg(p ORDER BY p.id) FROM workflows.post_process_metadata p WHERE p.scan_id = " + identifier + "), '[]'::json));")
        value = json.loads(raw)
        if not value.get("scan") or str(value["scan"]["id"]) != identifier:
            raise o.CaptureValidationError("Native database snapshot did not contain the requested scan")
        return raw, value

    def owned_runners(self):
        identifiers = self.commands.run([self.docker, "ps", "-q", "--filter", "label=open-kritt.scan-runner=1"]).decode().split()
        owned = []
        for identifier in identifiers:
            rows = json.loads(self.commands.run([self.docker, "inspect", identifier]))
            if len(rows) != 1:
                raise o.CaptureValidationError("Native runner identity is ambiguous")
            row = rows[0]
            labels = row.get("Config", {}).get("Labels", {}) or {}
            if labels.get("open-kritt.scan-runner") != "1":
                continue
            for mount in row.get("Mounts", []):
                source = PurePosixPath(mount.get("Source", "/"))
                if (mount.get("Type") == "bind" and mount.get("Destination") == "/home/runner"
                        and source.is_relative_to(self.daemon_data_root / "jobs")):
                    owned.append(identifier)
                    break
        return owned


@dataclass
class DeadlineHandle:
    namespace: str
    timer: threading.Timer | None = None
    result: StopEvidence | None = None
    done: threading.Event = field(default_factory=threading.Event)
    stopping: bool = False


class LocalDeadlineController:
    """Stop the exclusive engine and its positively identified nested runners.

The watchdog is armed before submission, so lost HTTP acknowledgments do not
leave model work running. Stopping applies to the whole dedicated engine;
sharing that engine with another evaluation is deliberately unsupported.
"""
    confirmed_stop_supported = True
    hard_deadline_supported = True

    def __init__(self, deployment):
        self.deployment = deployment
        self.handle = None
        self.lock = threading.Lock()

    def _stop(self, handle):
        with self.lock:
            if handle.stopping:
                return
            handle.stopping = True
        try:
            deployment = self.deployment
            deployment.inspect_owned(deployment.engine, "engine")
            deployment.commands.run([deployment.docker, "stop", "--time", "3", deployment.engine], timeout=15)
            engine = deployment.inspect_owned(deployment.engine, "engine")
            if engine.get("State", {}).get("Running"):
                raise RuntimeError("Engine process still running")
            for runner in deployment.owned_runners():
                deployment.commands.run([deployment.docker, "stop", "--time", "2", runner], timeout=10)
            confirmed = not deployment.owned_runners()
            handle.result = StopEvidence(requested=True, confirmed=confirmed,
                reason="Dedicated engine stopped and matching nested runner inventory is empty" if confirmed
                else "Dedicated engine stopped, but matching nested runners remain")
        except Exception as exc:
            handle.result = StopEvidence(requested=True, confirmed=False,
                reason="Local Docker stop could not be verified: " + type(exc).__name__)
        finally:
            handle.done.set()

    async def arm_deadline(self, namespace, wall_time_s):
        with self.lock:
            if self.handle is not None:
                raise o.ConfigurationError("One local dedicated engine can serve only one active assignment")
            handle = DeadlineHandle(namespace=str(namespace))
            self.handle = handle
        try:
            current = self.deployment.inspect_owned(self.deployment.engine, "engine")
            if not current.get("State", {}).get("Running"):
                raise o.ConfigurationError("Task-owned engine has stopped; restart it before another assignment")
        except BaseException:
            with self.lock:
                self.handle = None
            raise
        timer = threading.Timer(float(wall_time_s), self._stop, args=(handle,))
        timer.daemon = True
        handle.timer = timer
        timer.start()
        return handle

    async def disarm_deadline(self, handle):
        if handle is not self.handle:
            raise o.ConfigurationError("Unknown local deadline handle")
        handle.timer.cancel()
        if handle.stopping:
            await anyio.to_thread.run_sync(partial(handle.done.wait, 35))
        with self.lock:
            self.handle = None

    async def stop_scan(self, scan_id):
        handle = self.handle
        if handle is None:
            return StopEvidence(requested=False, confirmed=False, reason="No armed task-owned namespace")
        _, snapshot = await anyio.to_thread.run_sync(self.deployment.snapshot, scan_id)
        if snapshot["scan"]["repo_full"] != handle.namespace:
            raise o.ConfigurationError("Refusing to stop a scan outside the armed namespace")
        await anyio.to_thread.run_sync(self._stop, handle)
        await anyio.to_thread.run_sync(partial(handle.done.wait, 35))
        return handle.result or StopEvidence(requested=True, confirmed=False, reason="Docker stop is still unconfirmed")


class NativeDatabaseCollector:
    def __init__(self, deployment, cache):
        self.deployment = deployment
        self.cache = cache

    async def collect(self, scan_id):
        return await anyio.to_thread.run_sync(self._collect, scan_id)

    def _collect(self, scan_id):
        raw, snapshot = self.deployment.snapshot(scan_id)
        source = self.cache.write_bytes("openkritt.native_snapshot." + str(scan_id), raw, "application/json")
        evidence = [o.EvidenceRef(artifact=source, description="Unmodified PostgreSQL JSON snapshot")]
        capture_root = self.deployment.engine_data / "aef-capture"
        rows = snapshot["metadata"]
        row_ids = {str(row["id"]) for row in rows}
        reasons = []
        traces = []
        if snapshot["scan"]["status"] not in {"completed", "failed", "stopped"}:
            reasons.append("Native scan has not reached a terminal state")
        mirrored = {str(row["post_process_metadata_id"]) for row in rows if row.get("post_process_metadata_id") is not None}
        if mirrored != {str(row["id"]) for row in snapshot["post_process_metadata"]}:
            reasons.append("Post-processing inventory was not fully mirrored")
        for row in rows:
            if str(row["scan_id"]) != str(scan_id):
                raise o.CaptureValidationError("Cross-scan metadata in native snapshot")
            workspace_id = (POST_WORKSPACE_ID_OFFSET + int(row["post_process_metadata_id"])
                            if row.get("post_process_metadata_id") is not None else int(row["id"]))
            folders = sorted((capture_root / "invocations" / str(workspace_id)).glob("*/invocation.json"))
            records = []
            for manifest_path in folders:
                record_raw = manifest_path.read_bytes()
                record = json.loads(record_raw)
                if record.get("workspace_id") != workspace_id or record.get("schema_version") != OBSERVER_REVISION:
                    raise o.CaptureValidationError("Observer invocation identity differs from its native workspace")
                records.append((record.get("started_at", ""), record, record_raw, manifest_path.parent))
            records.sort(key=lambda item: item[0])
            if len(records) != 1:
                reasons.append(f"Metadata {row['id']} has {len(records)} observed invocations; exact attempt correspondence is incomplete")
            stdout, stderr = [], []
            protocol = None
            for _, record, record_raw, folder in records:
                if record.get("state") != "completed":
                    reasons.append(f"Metadata {row['id']} invocation is {record.get('state')}")
                protocol = record["protocol"] if protocol is None else protocol
                if protocol != record["protocol"]:
                    raise o.CaptureValidationError("One native attempt changed harness protocols")
                manifest_ref = self.cache.write_bytes("openkritt.observer." + record["invocation_id"], record_raw, "application/json")
                evidence.append(o.EvidenceRef(artifact=manifest_ref, description="Native subprocess observer receipt"))
                for name, target in (("stdout", stdout), ("stderr", stderr)):
                    path = folder / (name + ".bin")
                    data = path.read_bytes() if path.exists() else b""
                    if record.get("state") == "completed" and (len(data) != record[name + "_bytes"] or hashlib.sha256(data).hexdigest() != record[name + "_sha256"]):
                        raise o.StorageError("Native observer stream hash mismatch")
                    target.append(data)
            if records:
                # Normally exactly one stream. A resumed native invocation is
                # preserved in order and marked incomplete instead of hidden.
                traces.append(HarnessTrace(str(row["id"]), protocol, b"\n".join(stdout), b"\n".join(stderr)))
        if len(row_ids) != len(rows):
            raise o.CaptureValidationError("Duplicate native metadata identity")
        inventory = (o.Observation(value=None, status="unknown", reason="; ".join(reasons), evidence=tuple(evidence)) if reasons else
                     observed(True, "Terminal native database inventory, mirrored post-processing and one retained invocation per attempt", tuple(evidence)))
        return WorkflowCapture(metadata=NativeJSON(json_bytes(rows)), results=NativeJSON(json_bytes(snapshot["results"])),
            traces=tuple(traces), inventory_complete=inventory,
            observer=o.VersionRef(name="aef-openkritt-local-observer", revision=OBSERVER_REVISION))


def verify_native_source(deployment):
    """Check both the pinned checkout and Python files mounted in the engine."""
    git = str(shutil.which("git") or "git")
    # Git's safe.directory value uses forward slashes on Windows as well.
    command = [git, "-c", "safe.directory=" + deployment.upstream.as_posix(), "-C", str(deployment.upstream)]
    revision = deployment.commands.run(command + ["rev-parse", "HEAD"]).decode().strip()
    if revision != UPSTREAM_REVISION:
        raise o.ConfigurationError("Local OpenKritt checkout is not the verified upstream revision")
    try:
        deployment.commands.run(command + ["diff", "--quiet", "HEAD", "--"])
    except RuntimeError as exc:
        raise o.ConfigurationError("Pinned OpenKritt checkout has modified tracked source") from exc
    tracked = deployment.commands.run(command + ["ls-files", "-z", "--", "engine"]).decode("utf-8").split("\0")
    expected = {}
    for name in filter(None, tracked):
        relative = PurePosixPath(name)
        if relative.parts[0] != "engine" or ".." in relative.parts:
            raise o.ConfigurationError("Unexpected tracked engine path")
        expected[str(relative.relative_to("engine"))] = hashlib.sha256((deployment.upstream / name).read_bytes()).hexdigest()
    if "open_kritt_engine/worker.py" not in expected or "open_kritt_engine/harnesses.py" not in expected:
        raise o.ConfigurationError("Pinned native engine entrypoints were not found")
    check = (
        "import hashlib, importlib.util, json, pathlib, sys; "
        "root=pathlib.Path('/app').resolve(); files=json.load(sys.stdin); "
        "spec=importlib.util.find_spec('open_kritt_engine'); "
        "origin=str(pathlib.Path(spec.origin).resolve()) if spec and spec.origin else None; "
        "observed={name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in files}; "
        "print(json.dumps({'origin':origin,'files':observed}))"
    )
    native = json.loads(deployment.commands.run([deployment.docker, "exec", "-i", deployment.engine,
        "python", "-c", check], input=json_bytes(expected)))
    if native.get("origin") != "/app/open_kritt_engine/__init__.py" or native.get("files") != expected:
        raise o.ConfigurationError("Running engine source differs from the pinned clean checkout")
    return revision, hashlib.sha256(json_bytes(expected)).hexdigest()


def make_connection(config, *, workspace):
    deployment = LocalDeployment(config)
    revision, source_digest = verify_native_source(deployment)
    ready_path = deployment.engine_data / "aef-capture" / "observer-ready.json"
    ready = json.loads(ready_path.read_bytes())
    observer_path = Path(__file__).with_name("openkritt_observer.py")
    if (ready.get("schema_version") != OBSERVER_REVISION or ready.get("upstream_revision") != revision
            or ready.get("observer_sha256") != hashlib.sha256(observer_path.read_bytes()).hexdigest()):
        raise o.ConfigurationError("Prepared engine observer does not match the local reviewed binding")
    cache = ArtifactCache(Path(workspace) / "collector-evidence")
    engine = deployment.engine_info
    receipt = {"platform": "local-docker", "compose_project": deployment.project,
               "engine_container_id": engine["Id"], "engine_image_id": engine["Image"],
               "upstream_revision": revision, "engine_source_manifest_sha256": source_digest, "observer": ready}
    ref = cache.write_bytes("openkritt.local_deployment", json_bytes(receipt), "application/json")
    environment = observed(receipt, "Local Docker inspection, upstream Git pin and running observer receipt",
                           (o.EvidenceRef(artifact=ref),))
    return OpenKrittHTTPConnection(base_url=config["base_url"], stager=LocalRepositoryStager(deployment.repo_mount),
        collector=NativeDatabaseCollector(deployment, cache), stopper=LocalDeadlineController(deployment),
        upstream_ref=o.VersionRef(name="openkritt", revision=revision), deployment=environment,
        timeout_s=float(config.get("http_timeout_s", 30)))
