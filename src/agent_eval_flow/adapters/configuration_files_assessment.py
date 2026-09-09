"""Explicit, content-addressed configuration collection; never executes target files.

``ConfigurationSpec.params['files']`` is a nonempty sequence of source mappings.
Each has ``root`` (a constructor binding) and a relative POSIX ``path``; optional
``logical_path``, ``source_tool``, ``scope`` and ``role`` describe its meaning.
Machine roots are connection data; the saved manifest retains source provenance.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
from pathlib import Path

import anyio

from .. import objects as o
from ..objects.identity import plain, semantic_fingerprint
from ..objects.values import observed, unknown
from ..storage.artifacts import ArtifactCache
from .common import contained_relative


def _unknown_resources():
    return o.Resources(cost_usd=unknown("Collection/inspection expense was not measured"),
                       cost_scope=("configuration",), input_tokens=unknown("Not measured"),
                       output_tokens=unknown("Not measured"), human_minutes=unknown("Not measured"))


def _retain_json(cache, name, value):
    return cache.write_bytes(name, json.dumps(plain(value), sort_keys=True,
                             ensure_ascii=False).encode("utf-8"), "application/json")


class FileConfigurationCollector:
    """Collect only caller-selected files under explicitly bound roots.

    Symlinks (including an intermediate symlink) are rejected rather than
    followed. A missing/unreadable/unstable source is a retained omission, while
    malformed selectors or contradictory logical paths are configuration errors.
    """

    def __init__(self, *, roots: Mapping[str, Path], artifacts: ArtifactCache,
                 ref=None, max_file_bytes: int = 16 * 1024 * 1024):
        self.ref = ref or o.VersionRef(name="configuration-files", revision="1")
        if not self.ref.revision or not roots:
            raise o.ConfigurationError("A collector revision and explicit source roots are required")
        if type(max_file_bytes) is not int or max_file_bytes <= 0:
            raise o.ConfigurationError("max_file_bytes must be a positive integer")
        self.roots = {key: Path(value).resolve() for key, value in roots.items()}
        self.artifacts, self.max_file_bytes = artifacts, max_file_bytes

    def _selectors(self, spec):
        if spec.collector != self.ref:
            raise o.ConfigurationError("Selected collector revision does not match the binding")
        if set(spec.params) - {"files", "context"}:
            raise o.ConfigurationError("File collector supports only files and context parameters")
        if not isinstance(spec.params.get("context", {}), Mapping):
            raise o.ConfigurationError("Configuration context must be a mapping")
        selectors = spec.params.get("files")
        if not isinstance(selectors, (tuple, list)) or not selectors:
            raise o.ConfigurationError("File collector requires a nonempty explicit files manifest")
        result, logical_seen = [], set()
        for source in selectors:
            if not isinstance(source, Mapping) or set(source) - {
                    "root", "path", "logical_path", "source_tool", "scope", "role"}:
                raise o.ConfigurationError("Invalid configuration source selector")
            root_name, relative = source.get("root"), source.get("path")
            if root_name not in self.roots:
                raise o.ConfigurationError(f"Unbound configuration root: {root_name!r}")
            # Validate lexical containment without following a possibly escaping symlink.
            contained_relative(Path.cwd(), relative)
            logical = source.get("logical_path", relative)
            contained_relative(Path.cwd(), logical)
            if logical in logical_seen:
                raise o.ConfigurationError(f"Duplicate logical configuration path: {logical}")
            logical_seen.add(logical)
            metadata = {key: source.get(key, default) for key, default in (
                ("source_tool", "unknown"), ("scope", "project"), ("role", "configuration"))}
            if not all(isinstance(value, str) and value for value in metadata.values()):
                raise o.ConfigurationError("Source tool, scope and role must be nonempty strings")
            result.append((root_name, relative, logical, metadata))
        return result

    def _read(self, root_name, relative):
        root = self.roots[root_name]
        path = root
        for part in relative.split("/"):
            path = path / part
            if path.is_symlink():
                raise OSError("Symbolic-link configuration sources require an explicit resolved source")
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise OSError("Configuration source escaped its declared root")
        before = resolved.stat()
        if not resolved.is_file() or before.st_size > self.max_file_bytes:
            raise OSError("Source is not a regular file within the configured size limit")
        data = resolved.read_bytes()
        middle = resolved.stat()
        repeated = resolved.read_bytes()
        after = resolved.stat()
        signature = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        if (path.resolve() != resolved or signature(before) != signature(middle) or signature(middle) != signature(after)
                or data != repeated or len(data) > self.max_file_bytes):
            raise OSError("Source changed while its configuration snapshot was collected")
        return data, str(resolved)

    async def collect(self, request, *, recorder):
        selectors = self._selectors(request.spec)
        started = datetime.now(timezone.utc)
        candidate = request.candidate
        fingerprint = candidate.fingerprint()
        entries, omissions, artifacts, manifest = [], [], {}, []
        for root_name, relative, logical, metadata in selectors:
            try:
                data, original = await anyio.to_thread.run_sync(self._read, root_name, relative)
                artifact = self.artifacts.write_bytes(logical, data,
                    mimetypes.guess_type(logical)[0] or "application/octet-stream")
                entry_id = semantic_fingerprint("snapshot-entry", {"path": logical, **metadata})
                entries.append(o.SnapshotEntry(id=entry_id, path=logical, artifact=artifact, **metadata))
                artifacts[entry_id] = artifact
                recorder.record_artifact(entry_id, artifact)
                manifest.append({"id": entry_id, "path": logical, "source": original,
                                 "root": root_name, "sha256": hashlib.sha256(data).hexdigest(), **metadata})
            except OSError as exc:
                omissions.append(f"{root_name}:{relative}: {type(exc).__name__}: {exc}")
        context = request.spec.params.get("context", {})
        if not isinstance(context, Mapping):
            raise o.ConfigurationError("Configuration context must be a mapping")
        snapshot = o.CandidateSnapshot(candidate_id=candidate.id, candidate_fingerprint=fingerprint,
            entries=tuple(entries), collector=self.ref,
            params_fingerprint=semantic_fingerprint("configuration-params", request.spec.params),
            context={**context, "selection": "explicit-files", "files": request.spec.params["files"],
                     "max_file_bytes": self.max_file_bytes},
            inventory_complete=observed(not omissions, "Coverage of explicitly requested file manifest"),
            omissions=tuple(omissions))
        artifacts["configuration.manifest"] = _retain_json(self.artifacts, "configuration.manifest",
            {"sources": manifest, "omissions": omissions, "snapshot_fingerprint": snapshot.fingerprint,
             "collector": self.ref, "params": request.spec.params})
        recorder.record_artifact("configuration.manifest", artifacts["configuration.manifest"])
        # Collection starts before the snapshot exists; its invocation subject
        # stays stable while the capture records the finalized snapshot identity.
        subject = o.CandidateSubject(candidate_id=candidate.id, candidate_fingerprint=fingerprint)
        status = "partial" if omissions else "completed"
        activity = o.AssessmentActivity(id=request.activity_id, request_id=request.id,
            implementation=self.ref, input_fingerprint=request.input_fingerprint, subjects=(subject,),
            phase="collection", status=status, resources=_unknown_resources(),
            inventory_complete=unknown("Collection resource inventory was not measured"),
            started_at=started, ended_at=datetime.now(timezone.utc), artifacts=artifacts)
        recorder.record_activity(activity, final=True)
        capture = o.ConfigurationCapture(id=request.id, request_id=request.id, candidate_id=candidate.id,
            candidate_fingerprint=fingerprint, snapshot=snapshot, status=status,
            activities=(activity,), artifacts=artifacts)
        recorder.record_capture(capture, final=True)
        return capture
