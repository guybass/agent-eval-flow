"""Read retained SkillEvaluator paired trials without executing its agent stack.

The import index is explicit caller configuration linking native trial IDs to
candidate/task/repetition identities. Public aggregate benchmark rows are never
expanded into invented trials. Raw files and verifier evidence stay unchanged.
"""
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import uuid

from .. import objects as o
from ..storage.artifacts import ArtifactCache
from .common import contained_relative
from .harbor import (HarborTrialCapture, HarborTrialMapper, HarborGradeChannel,
                     _native_trial_validator, _unknown)


@dataclass(frozen=True, kw_only=True)
class PairedTrialSource:
    candidate_id: str
    unit: Mapping
    repetition: int
    native_trial_id: str
    result_path: str
    output_path: str | None = None
    output_media_type: str = "application/json"
    verifier_path: str | None = None
    trajectory_path: str | None = None
    output_step: str | None = None
    execution_inventory_complete: o.Observation = field(default_factory=lambda: _unknown("Retained archive does not establish full execution inventory"))
    verifier_resources: o.Resources | None = None


@dataclass(frozen=True, kw_only=True)
class NativeSource:
    path: Path
    relative_path: str
    format_ref: o.VersionRef
    sha256: str


@dataclass(frozen=True, kw_only=True)
class SkillEvaluatorDialect:
    entries: tuple[PairedTrialSource, ...]
    expected_files: Mapping[str, str]
    grade_channels: tuple[HarborGradeChannel, ...] = ()
    validate_trial: Callable[[Mapping], Mapping] = _native_trial_validator
    grading_inventory_complete: o.Observation = field(default_factory=lambda: _unknown("Retained archive does not establish all grader invocations"))
    deployment: o.Observation = field(default_factory=lambda: _unknown("Retained archive does not expose deployment provenance"))

    def discover(self, source: Path, *, source_format) -> tuple[NativeSource, ...]:
        source = Path(source).resolve()
        root = source if source.is_dir() else source.parent
        if not source.exists():
            raise o.StorageError(f"SkillEvaluator source does not exist: {source}")
        if not self.entries or not self.expected_files:
            raise o.ConfigurationError("Paired import needs explicit native trial identities and source hashes")
        required = {path for entry in self.entries for path in (
            entry.result_path, entry.output_path, entry.verifier_path, entry.trajectory_path) if path is not None}
        if not required <= self.expected_files.keys():
            raise o.ConfigurationError("Every retained trial/output/verifier/trajectory file requires its source hash")
        sources = []
        for relative, expected in self.expected_files.items():
            path = contained_relative(root, relative)
            try:
                with path.open("rb") as stream:
                    actual = hashlib.file_digest(stream, "sha256").hexdigest()
            except OSError as exc:
                raise o.StorageError(f"Cannot read retained source {path}: {exc}") from exc
            if actual != expected:
                raise o.CaptureValidationError(f"Retained source hash mismatch: {relative}")
            sources.append(NativeSource(path=path, relative_path=relative, format_ref=source_format, sha256=actual))
        return tuple(sources)


class SkillEvaluatorImporter:
    def __init__(self, *, ref, source_format, dialect: SkillEvaluatorDialect, artifacts: ArtifactCache):
        if not ref.revision or not source_format.revision:
            raise o.ConfigurationError("Importer and retained source format require resolved revisions")
        self.ref, self.source_format, self.dialect, self.artifacts = ref, source_format, dialect, artifacts

    def read(self, source, *, plan):
        plan.validate().raise_for_errors()
        sources = self.dialect.discover(Path(source), source_format=self.source_format)
        paths = {row.relative_path: row for row in sources}
        assignments = {(row.candidate_id, o.typed_key(row.unit, plan.dataset.unit_key), row.repetition): row for row in plan.assignments}
        selected, native_ids = set(), set()
        mapper = HarborTrialMapper(mapper_ref=self.ref, source_format=self.source_format, artifacts=self.artifacts,
            validate_trial=self.dialect.validate_trial, grade_channels=self.dialect.grade_channels,
            deployment=self.dialect.deployment)
        runs, bundles, projections = [], [], []
        for entry in self.dialect.entries:
            identity = (entry.candidate_id, o.typed_key(entry.unit, plan.dataset.unit_key), entry.repetition)
            if identity not in assignments or identity in selected or entry.native_trial_id in native_ids:
                raise o.CaptureValidationError("Paired trial index has ambiguous, duplicate or foreign assignment/native identity")
            selected.add(identity)
            native_ids.add(entry.native_trial_id)
            assignment = assignments[identity]
            native = json.loads(paths[entry.result_path].path.read_bytes())
            if str(native.get("id")) != entry.native_trial_id:
                raise o.CaptureValidationError("Retained trial ID disagrees with its explicit paired identity index")
            if not native.get("trial_name") or not isinstance(native.get("config"), dict):
                raise o.CaptureValidationError("Expected a retained native TrialResult, not an aggregate benchmark summary")
            def reference(relative, media_type="application/json"):
                if relative is None:
                    return None
                original = paths[relative]
                return o.ArtifactRef(uri=str(original.path), media_type=media_type, sha256=original.sha256)
            run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.source_format.name}:{self.source_format.revision}:{entry.native_trial_id}:{assignment.id}"))
            request = o.RunRequest(run_id=run_id, assignment=assignment,
                candidate=plan.candidates[entry.candidate_id], input=o.AgentInput(unit=assignment.unit, tables={}),
                policy=plan.execution, environment=plan.environment)
            captured = HarborTrialCapture(run_id=run_id, result=reference(entry.result_path),
                output=reference(entry.output_path, entry.output_media_type), verifier=reference(entry.verifier_path),
                trajectory=reference(entry.trajectory_path), output_step=entry.output_step,
                execution_inventory_complete=entry.execution_inventory_complete,
                verifier_resources=entry.verifier_resources)
            run, grades, projection = mapper.map_trial(captured, request)
            runs.append(run)
            bundles.extend(grades)
            projections.append(projection)
        return o.ImportedCapture(runs=tuple(runs), native_grades=tuple(bundles), projections=tuple(projections),
            grading_inventory_complete=self.dialect.grading_inventory_complete)
