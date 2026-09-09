from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from agent_eval_flow.objects import CaptureValidationError
from examples.integrations.opensre_local_session import (
    NativeCallIdentity, NativeProcessCapture, load_incident_module,
)


FIXTURE = Path(__file__).resolve().parents[1] / "e2e/fixtures/opensre"


def test_native_identity_is_required_consumed_and_thread_scoped():
    identity = NativeCallIdentity()
    with pytest.raises(CaptureValidationError):
        identity.consume("logs", {})

    def call(number):
        arguments = {"number": number}
        request = SimpleNamespace(tool_call=SimpleNamespace(id=f"call-{number}", name="logs"),
                                  arguments=arguments)
        identity.before("native-loop", request)
        value = identity.consume("logs", arguments)
        with pytest.raises(CaptureValidationError):
            identity.consume("logs", arguments)
        return value

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(call, range(12))) == [("native-loop", f"call-{i}") for i in range(12)]


def test_incident_module_loads_by_path_and_tools_produce_independent_receipts(tmp_path):
    module = load_incident_module(FIXTURE)
    store = module.IncidentStore(FIXTURE, tmp_path / "incident", invocation_id="test-invocation")
    opened = store.call("fixture_incident_open", {"incident_id": "HDFS-2008-11-09"},
                        loop_id="loop-1", tool_call_id="native-1")
    page = store.call("fixture_logs_search", {"snapshot_id": opened["snapshot_id"], "query": {"level": "WARN"}},
                     loop_id="loop-1", tool_call_id="native-2")
    continuation = store.call("fixture_logs_search", {"snapshot_id": opened["snapshot_id"], "cursor": page["next_cursor"]},
                             loop_id="loop-1", tool_call_id="native-3")
    assert len(page["rows"]) == len(continuation["rows"]) == 4
    assert continuation["previous_receipt_id"] == page["receipt_id"]
    receipts = [json.loads(line) for line in (store.work / "tool-audit.jsonl").read_text().splitlines()]
    assert receipts[-1]["tool_call_id"] == "native-3"
    assert receipts[-1]["result"] == continuation


def test_native_subprocess_capture_preserves_io_identity_and_omits_environment(tmp_path):
    completed = SimpleNamespace(returncode=0, stdout='\x1b[31m{"tool_calls": []}\n', stderr="native stderr\n")
    observed = []

    def run(argv, *args, **kwargs):
        observed.append((argv, kwargs))
        return completed

    proxy = NativeProcessCapture(SimpleNamespace(run=run), tmp_path / "capture")
    argv = ["codex", "exec", "--ephemeral", "-s", "read-only", "-"]
    kwargs = {"input": "public incident prompt", "env": {"SECRET": "never-export-this"}, "cwd": "/aef/run"}
    assert proxy.run(argv, **kwargs) is completed
    assert observed == [(argv, kwargs)]
    directory = proxy.root / "001"
    assert (directory / "stdin.txt").read_text() == kwargs["input"]
    assert (directory / "stdout.txt").read_bytes() == completed.stdout.encode()
    assert (directory / "stderr.txt").read_bytes() == completed.stderr.encode()
    receipt = json.loads((directory / "process.json").read_text())
    assert receipt["returncode"] == 0 and receipt["status"] == "completed"
    assert "env" not in receipt
    assert all(b"never-export-this" not in path.read_bytes() for path in directory.iterdir())


def test_native_timeout_preserves_partial_streams_and_reraises_same_error(tmp_path):
    error = subprocess.TimeoutExpired(["codex", "exec"], 1, output=b"partial output", stderr=b"partial error")

    def run(argv, **kwargs):
        raise error

    proxy = NativeProcessCapture(SimpleNamespace(run=run), tmp_path / "capture")
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        proxy.run(["codex", "exec"], input="prompt")
    assert caught.value is error
    directory = proxy.root / "001"
    assert (directory / "stdout.txt").read_bytes() == error.stdout
    assert (directory / "stderr.txt").read_bytes() == error.stderr
    receipt = json.loads((directory / "process.json").read_text())
    assert receipt["error_type"] == "TimeoutExpired" and receipt["returncode"] is None


def test_unrelated_native_probes_are_not_recorded(tmp_path):
    completed = SimpleNamespace(returncode=0, stdout="version", stderr="")
    proxy = NativeProcessCapture(SimpleNamespace(run=lambda *args, **kwargs: completed), tmp_path / "capture")
    assert proxy.run(["codex", "--version"]) is completed
    assert proxy.count == 0 and not list(proxy.root.iterdir())


def test_native_isolation_settings_apply_before_imports_and_restore_on_setup_failure(monkeypatch, tmp_path):
    from examples.integrations import opensre_local_session as binding

    monkeypatch.setenv("LLM_PROVIDER", "prior-provider")
    monkeypatch.delenv("OPENSRE_MEMORY_AUTOEXTRACT_DISABLED", raising=False)

    def setup(**kwargs):
        assert os.environ["LLM_PROVIDER"] == "codex"
        assert os.environ["CODEX_MODEL"] == "declared-model"
        assert os.environ["OPENSRE_MEMORY_AUTOEXTRACT_DISABLED"] == "1"
        assert os.environ["OPENSRE_MEMORY_DISABLED"] == "1"
        raise RuntimeError("native import/setup failed")

    monkeypatch.setattr(binding, "_make_native_session", setup)
    request = SimpleNamespace(candidate=SimpleNamespace(settings={"model": "declared-model"}))
    with pytest.raises(RuntimeError, match="native import/setup failed"):
        binding.make_session(request=request, workspace=tmp_path, observer=None)
    assert os.environ["LLM_PROVIDER"] == "prior-provider"
    assert "OPENSRE_MEMORY_AUTOEXTRACT_DISABLED" not in os.environ
