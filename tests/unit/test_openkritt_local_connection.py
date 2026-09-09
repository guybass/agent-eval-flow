"""Local binding checks use native-shaped receipts and real child byte streams."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from zipfile import ZipFile

import anyio
import pytest

from agent_eval_flow import objects as o
from agent_eval_flow.storage.artifacts import ArtifactCache
from agent_eval_flow.adapters.openkritt import build_evidence_bundle, complete_evidence_bundle, artifact_bytes, evidence_refs
from agent_eval_flow.objects.values import observed
from examples.integrations.openkritt_local import (
    LocalDeployment, NativeDatabaseCollector, LocalDeadlineController,
    verify_native_source, UPSTREAM_REVISION,
)
from examples.integrations.openkritt_observer import (
    CapturingSubprocess, OBSERVER_REVISION,
)


def record_invocation(root, workspace_id, *, stdout=b'{"type":"thread.started"}\n', state="completed"):
    folder = root / "aef-capture" / "invocations" / str(workspace_id) / "native-invocation"
    folder.mkdir(parents=True)
    record = {"schema_version": OBSERVER_REVISION, "workspace_id": workspace_id,
              "invocation_id": f"native-{workspace_id}", "started_at": "2026-09-09T10:00:00Z",
              "state": state, "protocol": "codex-jsonl"}
    for name, data in (("stdout", stdout), ("stderr", b"native stderr\n")):
        (folder / (name + ".bin")).write_bytes(data)
        record[name + "_bytes"] = len(data)
        record[name + "_sha256"] = hashlib.sha256(data).hexdigest()
    (folder / "invocation.json").write_text(json.dumps(record))
    return folder


def deployment_fixture(tmp_path, snapshot):
    return SimpleNamespace(engine_data=tmp_path,
        snapshot=lambda scan_id: (json.dumps(snapshot).encode(), snapshot))


def test_collector_preserves_native_attempts_post_processing_and_exact_streams(tmp_path):
    stdout = b'{"type":"item.completed","item":{"id":"tool-1","type":"command_execution","command":"cat app.py","aggregated_output":"source","exit_code":0}}\n'
    rows = [{"id": 13, "scan_id": 7, "status": "completed", "output_json": {"rows": ["auth", "storage"]}},
            {"id": 14, "scan_id": 7, "status": "completed", "post_process_metadata_id": 2}]
    results = [{"id": 6, "scan_id": 7, "json_answer": {"file": "app.py", "line": 24}}]
    snapshot = {"scan": {"id": 7, "status": "completed"}, "metadata": rows,
                "results": results, "post_process_metadata": [{"id": 2, "scan_id": 7}]}
    record_invocation(tmp_path, 13, stdout=stdout)
    record_invocation(tmp_path, 1_000_000_002)
    collector = NativeDatabaseCollector(deployment_fixture(tmp_path, snapshot), ArtifactCache(tmp_path / "evidence"))
    captured = anyio.run(collector.collect, "7")
    assert captured.metadata.value == rows
    assert captured.results.value == results
    assert [trace.metadata_id for trace in captured.traces] == ["13", "14"]
    assert captured.traces[0].stdout == stdout
    assert captured.traces[0].stderr == b"native stderr\n"
    assert captured.inventory_complete.value is True
    assert len(captured.inventory_complete.evidence) == 3


@pytest.mark.parametrize("problem", ["missing", "partial", "mirror"])
def test_collector_marks_incomplete_native_inventory_unknown(tmp_path, problem):
    snapshot = {"scan": {"id": 7, "status": "completed"}, "metadata": [{"id": 13, "scan_id": 7}],
                "results": [], "post_process_metadata": [{"id": 2}] if problem == "mirror" else []}
    if problem != "missing":
        record_invocation(tmp_path, 13, state="running" if problem == "partial" else "completed")
    collector = NativeDatabaseCollector(deployment_fixture(tmp_path, snapshot), ArtifactCache(tmp_path / "evidence"))
    captured = anyio.run(collector.collect, "7")
    assert captured.inventory_complete.status == "unknown"
    assert captured.inventory_complete.evidence
    if problem == "partial":
        assert captured.traces[0].stdout


def test_collector_rejects_corrupted_native_trace(tmp_path):
    snapshot = {"scan": {"id": 7, "status": "completed"}, "metadata": [{"id": 13, "scan_id": 7}],
                "results": [], "post_process_metadata": []}
    folder = record_invocation(tmp_path, 13)
    (folder / "stdout.bin").write_bytes(b"changed")
    collector = NativeDatabaseCollector(deployment_fixture(tmp_path, snapshot), ArtifactCache(tmp_path / "evidence"))
    with pytest.raises(o.StorageError, match="hash mismatch"):
        anyio.run(collector.collect, "7")


class DockerCommands:
    def __init__(self, project="aef-test"):
        self.project = project
        self.calls = []
        self.running = {"engine": True, "ours": True, "theirs": True}

    def run(self, argv, *, input=None, timeout=45):
        self.calls.append(argv)
        if argv[1] == "inspect":
            target = argv[2]
            compose = target in {"engine", "db"}
            labels = ({"com.docker.compose.project": self.project, "com.docker.compose.service": target} if compose
                      else {"open-kritt.scan-runner": "1"})
            source = "/task-data" if target == "engine" else "/task-data/jobs/metadata-13/home" if target == "ours" else "/other-data/jobs/metadata-13/home"
            return json.dumps([{"Id": target, "Image": "sha256:fixture", "Config": {"Labels": labels},
                "State": {"Running": self.running.get(target, True)},
                "Mounts": [{"Type": "bind", "Destination": "/data" if compose else "/home/runner", "Source": source}]}]).encode()
        if argv[1] == "ps":
            return "\n".join(name for name in ("ours", "theirs") if self.running[name]).encode()
        if argv[1] == "stop":
            self.running[argv[-1]] = False
            return argv[-1].encode()
        raise AssertionError(argv)


def local_deployment(tmp_path, commands):
    return LocalDeployment({"compose_project": "aef-test", "engine_container": "engine", "db_container": "db",
        "engine_data_dir": str(tmp_path), "local_repos_dir": str(tmp_path), "upstream_dir": str(tmp_path)}, commands=commands)


def test_stopper_confirms_task_owned_engine_and_leaves_other_stack_running(tmp_path):
    commands = DockerCommands()
    deployment = local_deployment(tmp_path, commands)
    controller = LocalDeadlineController(deployment)

    async def exercise():
        handle = await controller.arm_deadline("fixture", 60)
        controller._stop(handle)
        assert handle.result.confirmed
        await controller.disarm_deadline(handle)
    anyio.run(exercise)
    assert not commands.running["engine"]
    assert not commands.running["ours"]
    assert commands.running["theirs"]
    assert all(call[-1] != "theirs" for call in commands.calls if call[1] == "stop")


def test_deployment_refuses_another_compose_project(tmp_path):
    with pytest.raises(o.ConfigurationError, match="outside"):
        local_deployment(tmp_path, DockerCommands(project="users-normal-stack"))


def test_database_identifier_is_validated_before_query(tmp_path):
    commands = DockerCommands()
    deployment = local_deployment(tmp_path, commands)
    before = len(commands.calls)
    with pytest.raises(o.ConfigurationError):
        deployment.snapshot("7; DELETE FROM scans")
    assert len(commands.calls) == before


@pytest.mark.parametrize("mismatch", [None, "tracked", "mounted"])
def test_native_source_verifies_clean_checkout_and_running_module_bytes(tmp_path, mismatch):
    package = tmp_path / "engine" / "open_kritt_engine"
    package.mkdir(parents=True)
    paths = ["open_kritt_engine/worker.py", "open_kritt_engine/harnesses.py"]
    for path in paths:
        (tmp_path / "engine" / path).write_text("# pinned test source\n")

    class SourceCommands:
        def run(self, argv, *, input=None, timeout=45):
            if "rev-parse" in argv:
                return UPSTREAM_REVISION.encode()
            if "diff" in argv:
                if mismatch == "tracked":
                    raise RuntimeError("changed")
                return b""
            if "ls-files" in argv:
                return ("\0".join("engine/" + path for path in paths) + "\0").encode()
            observed = json.loads(input)
            if mismatch == "mounted":
                observed[paths[0]] = "changed"
            return json.dumps({"origin": "/app/open_kritt_engine/__init__.py", "files": observed}).encode()

    deployment = SimpleNamespace(upstream=tmp_path, commands=SourceCommands(), docker="docker", engine="engine")
    if mismatch:
        with pytest.raises(o.ConfigurationError):
            verify_native_source(deployment)
    else:
        revision, digest = verify_native_source(deployment)
        assert revision == UPSTREAM_REVISION and len(digest) == 64


def test_observer_tees_actual_child_bytes_and_preserves_native_completed_process(tmp_path):
    workspace = tmp_path / "jobs" / "metadata-13"
    workspace.mkdir(parents=True)
    observer = CapturingSubprocess(tmp_path / "capture", lambda args: "codex")
    program = "import sys; data=sys.stdin.read(); sys.stdout.buffer.write(b'line1\\r\\nline2\\n'); sys.stderr.write(data)"
    completed = observer.run([sys.executable, "-c", program], input="observed prompt", cwd=str(workspace),
        env=os.environ.copy(), text=True, capture_output=True, timeout=10, check=False)
    assert completed.returncode == 0
    assert completed.stdout == "line1\nline2\n"
    assert completed.stderr == "observed prompt"
    manifests = list((tmp_path / "capture").rglob("invocation.json"))
    assert len(manifests) == 1
    folder = manifests[0].parent
    assert (folder / "stdout.bin").read_bytes() == b"line1\r\nline2\n"
    record = json.loads(manifests[0].read_bytes())
    assert record["state"] == "completed"
    assert record["workspace_id"] == 13
    assert "args" not in record and "env" not in record


def test_observer_timeout_retains_partial_native_bytes(tmp_path):
    workspace = tmp_path / "jobs" / "metadata-13"
    workspace.mkdir(parents=True)
    observer = CapturingSubprocess(tmp_path / "capture", lambda args: "codex")
    with pytest.raises(subprocess.TimeoutExpired) as exc:
        observer.run([sys.executable, "-c", "import time; print('partial', flush=True); time.sleep(5)"],
            input="prompt", cwd=str(workspace), env=os.environ.copy(), text=True, capture_output=True, timeout=.3, check=False)
    assert b"partial" in exc.value.stdout
    path = next((tmp_path / "capture").rglob("invocation.json"))
    assert json.loads(path.read_bytes())["state"] == "timed_out"
    assert b"partial" in path.with_name("stdout.bin").read_bytes()


def bundle_fixture(tmp_path, *, include_provenance):
    cache = ArtifactCache(tmp_path / "cache")
    metadata = cache.write_bytes("metadata", b'[{"id":13,"scan_id":7}]', "application/json")
    observer = cache.write_bytes("observer", b'{"workspace_id":13,"state":"completed"}', "application/json")
    deployment = cache.write_bytes("deployment", b'{"image":"sha256:real-image-receipt"}', "application/json")
    inventory = observed(True, evidence=(o.EvidenceRef(artifact=observer, locator="/state", description="Native observer"),))
    environment = observed({"platform": "local"}, evidence=(o.EvidenceRef(artifact=deployment),))
    named = {"openkritt.step_metadata": metadata}
    files = {"app.py": b"print('actual source')\n"}
    manifest = json.dumps({"files": [{"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                                     for name, data in files.items()]}).encode()
    bundle = build_evidence_bundle(scan_id="7", run_id="run-1", assignment_id="assignment-1", artifacts=named,
        manifest_raw=manifest, workflow_raw=b'{"kind":"open-kritt-workflow","version":2}', files=files,
        evidence=(inventory, environment) if include_provenance else ())
    named["openkritt.bundle"] = cache.write_bytes("bundle", bundle, "application/zip")
    run = o.Run(id="run-1", assignment_id="assignment-1", status="completed", cost_scope=("model",),
        output={}, output_state="available", artifacts=named, executions=(), started_at=None, ended_at=None,
        environment=environment, execution_inventory_complete=inventory, native_refs={"scan_id": "7"})
    return run, bundle


def assert_indexed_provenance(bundle, run):
    with ZipFile(io.BytesIO(bundle)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        listed = {row["path"]: row for row in manifest["files"]}
        assert set(listed) == set(archive.namelist()) - {"manifest.json"}
        for name, row in listed.items():
            data = archive.read(name)
            assert row["bytes"] == len(data)
            assert row["sha256"] == hashlib.sha256(data).hexdigest()
        actual = {(row["artifact"]["uri"], row["locator"], row["description"]): row for row in manifest["provenance"]}
        for ref in evidence_refs(run):
            row = actual[(ref.artifact.uri, ref.locator, ref.description)]
            assert archive.read(row["path"]) == artifact_bytes(ref.artifact)
        assert archive.read(manifest["artifacts"]["openkritt.step_metadata"]) == artifact_bytes(run.artifacts["openkritt.step_metadata"])


def test_live_bundle_includes_inventory_and_deployment_exact_bytes(tmp_path):
    run, bundle = bundle_fixture(tmp_path, include_provenance=True)
    assert_indexed_provenance(bundle, run)
    with ZipFile(io.BytesIO(bundle)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert len(manifest["provenance"]) == 2
        assert "derivation" not in manifest


def test_offline_bundle_completion_preserves_original_and_repairs_portability(tmp_path):
    run, original = bundle_fixture(tmp_path, include_provenance=False)
    original_ref = run.artifacts["openkritt.bundle"]
    repaired = complete_evidence_bundle(run)
    assert_indexed_provenance(repaired, run)
    assert artifact_bytes(original_ref) == original
    assert run.artifacts["openkritt.bundle"] == original_ref
    with ZipFile(io.BytesIO(repaired)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["derivation"]["operation"] == "offline-provenance-completion"
        assert archive.read(manifest["derivation"]["path"]) == original


def test_bundle_rejects_corrupt_nested_evidence_instead_of_silently_omitting_it(tmp_path):
    from dataclasses import replace
    run, _ = bundle_fixture(tmp_path, include_provenance=False)
    ref = run.environment.evidence[0].artifact
    corrupted = replace(ref, sha256="0" * 64)
    run = replace(run, environment=observed({}, evidence=(o.EvidenceRef(artifact=corrupted),)))
    with pytest.raises(o.StorageError, match="hash mismatch"):
        complete_evidence_bundle(run)
