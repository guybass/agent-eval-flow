"""Supervision contract tests plus real POSIX pipe/process-group integration."""
from pathlib import Path
import os
import sys
from urllib.parse import urlparse
from urllib.request import url2pathname

import anyio
import pytest

from agent_eval_flow import ConfigurationError
from agent_eval_flow.adapters.process import (
    NativeProcessCapture, PosixProcessLauncher, ProcessLaunch, ProcessSupervisor, StopEvidence,
)
from agent_eval_flow.storage.artifacts import ArtifactCache


def contents(ref):
    return Path(url2pathname(urlparse(ref.uri).path)).read_bytes()


class ReceiptStream:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    async def receive(self, max_bytes):
        await anyio.sleep(0)
        if not self.chunks:
            raise anyio.EndOfStream
        return self.chunks.pop(0)


class OwnedContainedProcess:
    """Protocol fixture owns all of its simulated work; no platform claims."""
    def __init__(self, *, blocked=False, confirm_stop=True):
        self.stdout = ReceiptStream([b'{"tool":"read"}\n', b'{"result":[1,2,3]}\n'])
        self.stderr = ReceiptStream([b"diagnostic\n"])
        self.blocked, self.confirm_stop = blocked, confirm_stop
        self.finished = anyio.Event()
        self.stdin, self.closed, self.stops = b"", False, 0

    async def write_stdin(self, data):
        self.stdin += data

    async def close_stdin(self):
        self.closed = True

    async def wait(self):
        if self.blocked:
            await self.finished.wait()
        return -9 if self.stops and self.blocked else 0

    async def stop_tree(self, *, grace_s):
        self.stops += 1
        if self.confirm_stop:
            self.finished.set()
        return StopEvidence(requested=self.blocked, confirmed=self.confirm_stop,
                            reason="Controlled protocol fixture stopped" if self.confirm_stop else "Stop not acknowledged")


class OwnedLauncher:
    hard_wall_time_limit = True
    def __init__(self, process):
        self.process, self.calls = process, 0
    async def start(self, launch):
        self.calls += 1
        return self.process


def launch(tmp_path, **options):
    return ProcessLaunch(argv=(str(Path(sys.executable).resolve()),), stdin=b"task\n",
                         workspace=tmp_path, environment={}, wall_time_s=options.pop("wall_time_s", 1),
                         stop_grace_s=0.02, **options)


def test_streams_and_stdin_are_retained_exactly_without_launch_retries(tmp_path):
    async def exercise():
        process = OwnedContainedProcess()
        launcher = OwnedLauncher(process)
        cache = ArtifactCache(tmp_path / "cache")
        supervisor = ProcessSupervisor(launcher=launcher, artifacts=cache, workspace_root=tmp_path)
        result = await supervisor.execute(launch(tmp_path))
        assert isinstance(result, NativeProcessCapture)
        assert launcher.calls == 1 and process.stdin == b"task\n" and process.closed
        assert contents(result.stdout) == b'{"tool":"read"}\n{"result":[1,2,3]}\n'
        assert contents(result.stderr) == b"diagnostic\n"
        assert result.output_complete.value is True and result.ended_at is not None
        assert not result.deadline_exceeded
        cache.verify(result.stdout).raise_for_errors()
    anyio.run(exercise)


@pytest.mark.parametrize("confirmed", [True, False])
def test_deadline_preserves_bytes_and_distinguishes_stop_confirmation(tmp_path, confirmed):
    async def exercise():
        process = OwnedContainedProcess(blocked=True, confirm_stop=confirmed)
        launcher = OwnedLauncher(process)
        supervisor = ProcessSupervisor(launcher=launcher, artifacts=ArtifactCache(tmp_path / "cache"))
        result = await supervisor.execute(launch(tmp_path, wall_time_s=0.02))
        assert launcher.calls == 1 and process.stops == 1
        assert result.deadline_exceeded and result.stop.confirmed is confirmed
        assert (result.ended_at is not None) is confirmed
        assert contents(result.stdout).startswith(b'{"tool":"read"}')
    anyio.run(exercise)


def test_cancellation_requests_stop_and_preserves_partial_artifacts(tmp_path):
    async def exercise():
        process = OwnedContainedProcess(blocked=True)
        supervisor = ProcessSupervisor(launcher=OwnedLauncher(process), artifacts=ArtifactCache(tmp_path / "cache"))
        caught = None
        with anyio.move_on_after(0.02):
            try:
                await supervisor.execute(launch(tmp_path))
            except anyio.get_cancelled_exc_class() as exc:
                caught = exc
                raise
        assert caught is not None and caught.stop_evidence.confirmed
        assert process.stops == 1 and "stdout" in caught.artifacts
    anyio.run(exercise)


def test_unverified_launcher_and_escaping_capture_paths_fail_before_start(tmp_path):
    async def exercise():
        process = OwnedContainedProcess()
        launcher = OwnedLauncher(process)
        launcher.hard_wall_time_limit = False
        supervisor = ProcessSupervisor(launcher=launcher, artifacts=ArtifactCache(tmp_path / "cache"))
        with pytest.raises(ConfigurationError):
            await supervisor.execute(launch(tmp_path))
        assert launcher.calls == 0
        with pytest.raises(ConfigurationError):
            launch(tmp_path, capture_files={"escape": "../outside"})
    anyio.run(exercise)


@pytest.mark.skipif(os.name != "posix", reason="Built-in process-group launcher is POSIX-only; Windows needs verified containment")
def test_real_posix_child_process_drains_both_large_pipes(tmp_path):
    async def exercise():
        script = "import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data*20000); sys.stderr.buffer.write(b'error'*20000)"
        request = ProcessLaunch(argv=(sys.executable, "-c", script), stdin=b"receipt",
                                workspace=tmp_path, environment=dict(os.environ), wall_time_s=5)
        capture = await ProcessSupervisor(launcher=PosixProcessLauncher(),
            artifacts=ArtifactCache(tmp_path / "cache")).execute(request)
        assert capture.exit_code == 0 and not capture.deadline_exceeded
        assert contents(capture.stdout) == b"receipt" * 20000
        assert contents(capture.stderr) == b"error" * 20000
        assert capture.stop.confirmed and capture.output_complete.value
    anyio.run(exercise)
