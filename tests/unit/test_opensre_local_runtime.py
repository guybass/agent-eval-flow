"""Local transport failures must not become false evidence or stop acknowledgments."""
from dataclasses import replace
import hashlib
import subprocess
from pathlib import Path

import pytest

from agent_eval_flow import objects as o
from agent_eval_flow.objects.values import observed
from agent_eval_flow.storage.artifacts import ArtifactCache
from examples.integrations.opensre_local import DockerOpenSRERuntime, materialize_capture


def test_worker_nested_provenance_is_relocated_and_hash_verified(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    data = b'actual observer receipt'
    (work / "receipt.json").write_bytes(data)
    ref = o.ArtifactRef(uri="/aef/run/receipt.json", media_type="application/json", sha256=hashlib.sha256(data).hexdigest())
    observation = observed(True, "Native record", (o.EvidenceRef(artifact=ref, locator="/status"),))
    result = materialize_capture(observation, workspace=work, cache=ArtifactCache(tmp_path / "cache"))
    assert result.value is True and result.evidence[0].locator == "/status"
    assert Path(result.evidence[0].artifact.uri).read_bytes() == data
    (work / "receipt.json").write_bytes(b"corrupted")
    with pytest.raises(o.StorageError, match="source hash"):
        materialize_capture(observation, workspace=work, cache=ArtifactCache(tmp_path / "cache"))


def test_worker_cannot_export_private_files_outside_its_workspace(tmp_path):
    ref = o.ArtifactRef(uri="/root/.codex/auth.json", media_type="application/json", sha256="a" * 64)
    with pytest.raises(o.StorageError, match="escaped"):
        materialize_capture(ref, workspace=tmp_path, cache=ArtifactCache(tmp_path / "cache"))


def test_unavailable_daemon_is_not_a_successful_stop():
    runtime = object.__new__(DockerOpenSRERuntime)
    runtime._command = lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, b"", b"cannot connect to Docker daemon")
    with pytest.raises(o.CaptureValidationError, match="unavailable"):
        runtime._stop("aef-opensre-test", "owned-invocation")


def test_other_container_cannot_be_stopped():
    runtime = object.__new__(DockerOpenSRERuntime)
    runtime.image_id = "sha256:ours"
    calls = []
    def command(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, b'{"Config":{"Labels":{}},"Image":"sha256:ours","State":{"Running":true}}', b"")
    runtime._command = command
    with pytest.raises(o.CaptureValidationError, match="ownership"):
        runtime._stop("aef-opensre-test", "owned-invocation")
    assert [args[0] for args in calls] == ["inspect"]
