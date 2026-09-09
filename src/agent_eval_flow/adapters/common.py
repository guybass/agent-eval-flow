"""Inert connection records shared by optional runtime adapters."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

from agent_eval_flow import objects as o
from agent_eval_flow.objects.identity import freeze_json
from agent_eval_flow.storage.artifacts import ArtifactCache


def contained_relative(root: Path, value: str) -> Path:
    """Resolve an asset destination without allowing drive, traversal or symlink escape."""
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise o.ConfigurationError("Asset paths must use nonempty relative POSIX components")
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in {"", "..", "."} for part in value.split("/")):
        raise o.ConfigurationError(f"Asset path escapes its channel: {value!r}")
    root = Path(root).resolve()
    path = (root / Path(*relative.parts)).resolve()
    if not path.is_relative_to(root):
        raise o.ConfigurationError(f"Asset destination escapes its configured root: {value!r}")
    return path


@dataclass(frozen=True, kw_only=True)
class AdapterBinding:
    ref: o.VersionRef
    upstream_ref: o.VersionRef
    workspace_root: Path
    artifacts: ArtifactCache
    deployment: o.Observation

    def __post_init__(self):
        if not self.ref.revision or not self.upstream_ref.revision:
            raise o.ConfigurationError("Adapter and upstream references require resolved revisions")
        object.__setattr__(self, "workspace_root", Path(self.workspace_root).resolve())


@dataclass(frozen=True, kw_only=True)
class RuntimeCapabilities:
    upstream_ref: o.VersionRef
    deployment: o.Observation
    limits: o.BackendCapabilities | None = None
    native_jobs: o.NativeJobCapabilities | None = None
    batch_grading: bool = False
    formats: tuple[o.VersionRef, ...] = ()

    def __post_init__(self):
        if not self.upstream_ref.revision or any(not ref.revision for ref in self.formats):
            raise o.ConfigurationError("Runtime identity and format revisions must be resolved")
        if self.native_jobs is not None and self.limits != self.native_jobs.limits:
            raise o.ConfigurationError("Runtime limits must agree with native job limits")
        object.__setattr__(self, "formats", tuple(self.formats))


@dataclass(frozen=True, kw_only=True)
class StagedAsset:
    source: o.ArtifactRef
    relative_path: str
    channel: Literal["agent", "verifier", "grader"]

    def __post_init__(self):
        if self.channel not in {"agent", "verifier", "grader"}:
            raise o.ConfigurationError("Unknown asset channel")
        # Syntactic path validation is independent of the eventual worker root.
        contained_relative(Path.cwd(), self.relative_path)
        if not self.source.sha256:
            raise o.ConfigurationError("Staged assets require a source content hash")


@dataclass(frozen=True, kw_only=True)
class MaterializedEvidence:
    replacements: Mapping[str, o.ArtifactRef] = field(default_factory=dict)
    original_locations: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "replacements", freeze_json(self.replacements))
        object.__setattr__(self, "original_locations", freeze_json(self.original_locations))
