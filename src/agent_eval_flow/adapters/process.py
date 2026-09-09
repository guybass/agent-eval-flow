"""Bounded process supervision with raw output retained before interpretation.

The built-in launcher supports POSIX process groups. Windows callers supply a
verified containment launcher or a prepared worker; killing one PID is not a
process-tree guarantee.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import partial
import math
import os
from pathlib import Path
import signal
from typing import Protocol

import anyio

from agent_eval_flow import objects as o
from agent_eval_flow.objects.identity import freeze_json
from agent_eval_flow.storage.artifacts import ArtifactCache
from .common import contained_relative


@dataclass(frozen=True, kw_only=True)
class ProcessLaunch:
    argv: tuple[str, ...]
    stdin: bytes
    workspace: Path
    environment: Mapping[str, str]
    wall_time_s: float
    stop_grace_s: float = 5.0
    capture_files: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if not self.argv or not all(isinstance(arg, str) and "\x00" not in arg for arg in self.argv):
            raise o.ConfigurationError("Process argv must be separate non-null strings")
        if not Path(self.argv[0]).is_absolute() or not Path(self.workspace).is_absolute():
            raise o.ConfigurationError("Executable and workspace paths must be absolute")
        if not isinstance(self.stdin, bytes):
            raise o.ConfigurationError("Process stdin must be bytes")
        if isinstance(self.wall_time_s, bool) or not math.isfinite(self.wall_time_s) or self.wall_time_s <= 0:
            raise o.ConfigurationError("Process wall_time_s must be finite and positive")
        if isinstance(self.stop_grace_s, bool) or not math.isfinite(self.stop_grace_s) or self.stop_grace_s < 0:
            raise o.ConfigurationError("Process stop_grace_s must be finite and nonnegative")
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in self.environment.items()):
            raise o.ConfigurationError("Process environment keys and values must be strings")
        root = Path(self.workspace).resolve()
        for path in self.capture_files.values():
            contained_relative(root, path)
        object.__setattr__(self, "argv", tuple(self.argv))
        object.__setattr__(self, "workspace", root)
        object.__setattr__(self, "environment", freeze_json(self.environment))
        object.__setattr__(self, "capture_files", freeze_json(self.capture_files))


@dataclass(frozen=True, kw_only=True)
class StopEvidence:
    requested: bool
    confirmed: bool
    reason: str
    evidence: tuple[o.EvidenceRef, ...] = ()

    def __post_init__(self):
        if type(self.requested) is not bool or type(self.confirmed) is not bool or not self.reason:
            raise o.CaptureValidationError("Stop evidence requires strict booleans and an explanation")
        object.__setattr__(self, "evidence", tuple(self.evidence))


@dataclass(frozen=True, kw_only=True)
class NativeProcessCapture:
    stdout: o.ArtifactRef
    stderr: o.ArtifactRef
    exit_code: int | None
    started_at: datetime
    ended_at: datetime | None
    stop: StopEvidence
    output_complete: o.Observation
    artifacts: Mapping[str, o.ArtifactRef] = field(default_factory=dict)
    deadline_exceeded: bool = False

    def __post_init__(self):
        object.__setattr__(self, "artifacts", freeze_json(self.artifacts))


class ContainedProcess(Protocol):
    stdout: object
    stderr: object
    async def write_stdin(self, data: bytes) -> None: ...
    async def close_stdin(self) -> None: ...
    async def wait(self) -> int: ...
    async def stop_tree(self, *, grace_s: float) -> StopEvidence: ...


class ContainedProcessLauncher(Protocol):
    hard_wall_time_limit: bool
    async def start(self, launch: ProcessLaunch) -> ContainedProcess: ...


class _PosixProcess:
    def __init__(self, process):
        self._process = process
        self.stdout, self.stderr = process.stdout, process.stderr

    async def write_stdin(self, data):
        await self._process.stdin.send(data)

    async def close_stdin(self):
        await self._process.stdin.aclose()

    async def wait(self):
        return await self._process.wait()

    def _group_exists(self):
        try:
            os.killpg(self._process.pid, 0)
            return True
        except ProcessLookupError:
            return False

    async def stop_tree(self, *, grace_s):
        if not self._group_exists():
            await self._process.wait()
            return StopEvidence(requested=False, confirmed=True, reason="Process group no longer exists")
        try:
            os.killpg(self._process.pid, signal.SIGTERM)
        except ProcessLookupError:
            await self._process.wait()
            return StopEvidence(requested=True, confirmed=True, reason="Process group no longer exists")
        deadline = anyio.current_time() + grace_s
        while self._group_exists() and anyio.current_time() < deadline:
            # Reap the leader while waiting; zombies must not be called running work.
            with anyio.move_on_after(min(0.05, max(0, deadline - anyio.current_time()))):
                await self._process.wait()
            await anyio.sleep(0.01)
        if self._group_exists():
            try:
                os.killpg(self._process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            with anyio.move_on_after(max(0.1, grace_s)):
                await self._process.wait()
                while self._group_exists():
                    await anyio.sleep(0.01)
        confirmed = not self._group_exists()
        return StopEvidence(requested=True, confirmed=confirmed,
                            reason="Process group is absent after termination" if confirmed
                            else "Termination requested but process group disappearance is unconfirmed")


class PosixProcessLauncher:
    hard_wall_time_limit = os.name == "posix"

    async def start(self, launch):
        if os.name != "posix":
            raise o.ConfigurationError("Windows requires a supplied verified process-tree launcher or worker")
        process = await anyio.open_process(
            launch.argv, cwd=launch.workspace, env=dict(launch.environment), start_new_session=True,
        )
        return _PosixProcess(process)


@dataclass(frozen=True, kw_only=True)
class CliConnection:
    executable: Path
    environment: Mapping[str, str]
    supervisor: ProcessSupervisor

    def __post_init__(self):
        executable = Path(self.executable)
        if not executable.is_absolute():
            raise o.ConfigurationError("CLI executable must be an absolute path")
        object.__setattr__(self, "executable", executable)
        object.__setattr__(self, "environment", freeze_json(self.environment))


class ProcessSupervisor:
    def __init__(self, *, launcher, artifacts: ArtifactCache, workspace_root=None):
        self.launcher, self.artifacts = launcher, artifacts
        self.workspace_root = Path(workspace_root).resolve() if workspace_root is not None else None

    @property
    def hard_wall_time_limit(self):
        return getattr(self.launcher, "hard_wall_time_limit", False) is True

    async def execute(self, launch: ProcessLaunch) -> NativeProcessCapture:
        if not self.hard_wall_time_limit:
            raise o.ConfigurationError("The selected launcher has no verified hard wall-time containment")
        if not launch.workspace.is_dir():
            raise o.ConfigurationError("Invocation workspace must be an existing directory")
        if self.workspace_root is not None and not launch.workspace.is_relative_to(self.workspace_root):
            raise o.ConfigurationError("Invocation workspace escapes the configured root")
        stdout = self.artifacts.open_writer("stdout", "application/octet-stream")
        stderr = self.artifacts.open_writer("stderr", "application/octet-stream")
        process, exit_code, ended, stream_errors = None, None, None, []
        started = datetime.now(timezone.utc)
        stop = StopEvidence(requested=False, confirmed=False, reason="No process stop was requested")
        streams_done = set()
        committed = {}

        async def drain(stream, writer, label):
            while True:
                try:
                    chunk = await stream.receive(65536)
                except anyio.EndOfStream:
                    streams_done.add(label)
                    return
                except Exception as exc:
                    stream_errors.append(f"{label}: {type(exc).__name__}: {exc}")
                    return
                if not chunk:
                    streams_done.add(label)
                    return
                await anyio.to_thread.run_sync(writer.write, chunk)

        async def feed():
            try:
                if launch.stdin:
                    await process.write_stdin(launch.stdin)
            except (BrokenPipeError, anyio.BrokenResourceError, anyio.ClosedResourceError):
                # Native early exit is captured by its exit status and stderr.
                pass
            finally:
                await process.close_stdin()

        async def cleanup_stop():
            nonlocal stop
            with anyio.move_on_after(max(0.2, launch.stop_grace_s * 2 + 0.2), shield=True) as cleanup:
                try:
                    returned = await process.stop_tree(grace_s=launch.stop_grace_s)
                    if not isinstance(returned, StopEvidence):
                        raise o.CaptureValidationError("Launcher did not return StopEvidence")
                    stop = returned
                except Exception as exc:
                    stop = StopEvidence(requested=True, confirmed=False,
                                        reason=f"Stop failed: {type(exc).__name__}: {exc}")
            if cleanup.cancel_called:
                stop = StopEvidence(requested=True, confirmed=False, reason="Stop confirmation deadline expired")

        try:
            process = await self.launcher.start(launch)
            with anyio.move_on_after(launch.wall_time_s) as deadline:
                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(drain, process.stdout, stdout, "stdout")
                    tasks.start_soon(drain, process.stderr, stderr, "stderr")
                    tasks.start_soon(feed)
                    exit_code = await process.wait()
                    if type(exit_code) is not int:
                        raise o.CaptureValidationError("Native process exit code must be an integer")
            if deadline.cancel_called:
                await cleanup_stop()
                # Obtain remaining pipe bytes after a confirmed or attempted stop.
                with anyio.move_on_after(max(0.1, launch.stop_grace_s), shield=True):
                    async with anyio.create_task_group() as tasks:
                        if "stdout" not in streams_done:
                            tasks.start_soon(drain, process.stdout, stdout, "stdout")
                        if "stderr" not in streams_done:
                            tasks.start_soon(drain, process.stderr, stderr, "stderr")
                        if stop.confirmed:
                            exit_code = await process.wait()
                if stop.confirmed:
                    ended = datetime.now(timezone.utc)
            else:
                # An exited leader and EOF do not prove that quiet descendants
                # have stopped. Finalize the contained group before declaring
                # the end of the complete native attempt.
                await cleanup_stop()
                if stop.confirmed:
                    ended = datetime.now(timezone.utc)
            captured_stdout = await anyio.to_thread.run_sync(stdout.commit)
            committed["stdout"] = captured_stdout
            captured_stderr = await anyio.to_thread.run_sync(stderr.commit)
            committed["stderr"] = captured_stderr
            extra = {}
            for name, relative in launch.capture_files.items():
                path = contained_relative(launch.workspace, relative)
                if path.is_file():
                    def retain(path=path, name=name):
                        with path.open("rb") as stream:
                            return self.artifacts.write_stream(name, stream, "application/octet-stream")
                    extra[name] = await anyio.to_thread.run_sync(retain)
            complete = not stream_errors and len(streams_done) == 2
            return NativeProcessCapture(
                stdout=captured_stdout, stderr=captured_stderr, exit_code=exit_code,
                started_at=started, ended_at=ended, stop=stop, artifacts=extra,
                deadline_exceeded=deadline.cancel_called,
                output_complete=o.Observation(value=complete, status="observed",
                    reason="Both native streams reached EOF" if complete else
                    "; ".join(stream_errors) or "Native streams did not reach EOF before cleanup ended"),
            )
        except BaseException as exc:
            if process is not None:
                await cleanup_stop()
                exc.add_note(f"Process stop: requested={stop.requested}, confirmed={stop.confirmed}: {stop.reason}")
                setattr(exc, "stop_evidence", stop)
            # Preserve successfully spooled partial evidence even on cancellation.
            with anyio.CancelScope(shield=True):
                partial_evidence = dict(committed)
                for label, writer in (("stdout", stdout), ("stderr", stderr)):
                    if label not in committed:
                        try:
                            partial_evidence[label] = await anyio.to_thread.run_sync(writer.commit)
                        except Exception:
                            writer.abort()
                setattr(exc, "artifacts", partial_evidence)
            raise
