"""Run the pinned native engine while retaining its actual harness byte streams.

Mount this file in the engine and launch ``python /aef/openkritt_observer.py``.
It changes no native prompts, subprocess arguments, model routing or parsing.
Only the native harness module's subprocess object is proxied; other engine
modules keep the original subprocess module. Secrets in argv/env are not saved.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import json
import locale
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import uuid

OBSERVER_REVISION = "aef-openkritt-local-observer/1"
UPSTREAM_REVISION = "1ba10d410b4a0a309bacf5bc2d14ec1c27ed8a09"


def _write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _workspace_id(cwd, env):
    for value in ((env or {}).get("HOME", ""), str(cwd or "")):
        match = re.search(r"(?:^|/)jobs/metadata-(\d+)(?:/|$)", value.replace("\\", "/"))
        if match:
            return int(match.group(1))
    return None


class CapturingSubprocess:
    """A module-local proxy for native text/capture_output harness invocations."""

    def __init__(self, root, command_harness):
        self.root = Path(root)
        self.command_harness = command_harness

    def __getattr__(self, name):
        return getattr(subprocess, name)

    def run(self, args, **kwargs):
        workspace_id = _workspace_id(kwargs.get("cwd"), kwargs.get("env"))
        selected = (workspace_id is not None and kwargs.get("capture_output") is True
                    and kwargs.get("text") is True and isinstance(kwargs.get("input"), str))
        if not selected:
            return subprocess.run(args, **kwargs)
        harness = self.command_harness(args)
        if harness not in {"codex", "claude-code"}:
            return subprocess.run(args, **kwargs)
        invocation_id = uuid.uuid4().hex
        folder = self.root / "invocations" / str(workspace_id) / invocation_id
        folder.mkdir(parents=True, exist_ok=False)
        record = {"schema_version": OBSERVER_REVISION, "invocation_id": invocation_id,
                  "workspace_id": workspace_id, "harness": harness,
                  "protocol": "codex-jsonl" if harness == "codex" else "claude-stream-json",
                  "started_at": datetime.now(timezone.utc).isoformat(), "state": "running"}
        _write_json(folder / "invocation.json", record)
        options = dict(kwargs)
        prompt = options.pop("input")
        timeout = options.pop("timeout", None)
        check = options.pop("check", False)
        options.pop("capture_output")
        options.pop("text")
        encoding = options.pop("encoding", None) or locale.getpreferredencoding(False)
        errors = options.pop("errors", None) or "strict"
        streams = {"stdout": bytearray(), "stderr": bytearray()}
        reader_errors = []

        def read_stream(name, stream):
            try:
                with (folder / (name + ".bin")).open("wb") as output:
                    while data := stream.read1(65536):
                        streams[name].extend(data)
                        output.write(data)
                        output.flush()
            except BaseException as exc:
                reader_errors.append(type(exc).__name__)
            finally:
                stream.close()

        def native_text(data):
            # Match subprocess text mode's newline handling exactly.
            return io.TextIOWrapper(io.BytesIO(data), encoding=encoding, errors=errors).read()

        process = None
        readers = []
        try:
            process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, **options)
            for name in streams:
                reader = threading.Thread(target=read_stream, args=(name, getattr(process, name)), daemon=True)
                reader.start()
                readers.append(reader)
            try:
                process.stdin.write(prompt.encode(encoding, errors))
                process.stdin.close()
            except BrokenPipeError:
                process.stdin.close()
            process.wait(timeout=timeout)
            for reader in readers:
                reader.join()
            if reader_errors:
                raise OSError("Native output capture stream failed")
            result = subprocess.CompletedProcess(args, process.returncode,
                native_text(bytes(streams["stdout"])), native_text(bytes(streams["stderr"])))
            record.update(state="completed", returncode=process.returncode)
            if check:
                result.check_returncode()
            return result
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            for reader in readers:
                reader.join()
            record.update(state="timed_out", returncode=process.returncode)
            raise subprocess.TimeoutExpired(args, timeout, output=bytes(streams["stdout"]), stderr=bytes(streams["stderr"]))
        except BaseException as exc:
            record.update(state="failed", error_type=type(exc).__name__)
            raise
        finally:
            record["ended_at"] = datetime.now(timezone.utc).isoformat()
            for name, data in streams.items():
                record[name + "_bytes"] = len(data)
                record[name + "_sha256"] = hashlib.sha256(data).hexdigest()
            _write_json(folder / "invocation.json", record)


def main():
    # The upstream image copies the package to /app without installing it.
    # An absolute launcher in /aef does not inherit /app on sys.path.
    sys.path.insert(0, "/app")
    from open_kritt_engine import harnesses
    from open_kritt_engine.worker import main as native_main
    root = Path(os.environ.get("ENGINE_DATA_DIR", "/data")) / "aef-capture"
    root.mkdir(parents=True, exist_ok=True)
    _write_json(root / "observer-ready.json", {
        "schema_version": OBSERVER_REVISION, "upstream_revision": UPSTREAM_REVISION,
        "observer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    })
    harnesses.subprocess = CapturingSubprocess(root, harnesses._command_harness)
    native_main()


if __name__ == "__main__":
    main()
