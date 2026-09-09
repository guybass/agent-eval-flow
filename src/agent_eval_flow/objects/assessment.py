"""Immutable records and boundaries for the separate assessment 0.1 contract.

These records compose, and never weaken, the existing behavioral 0.4 records.
Runtime sessions and extension clients deliberately remain protocols, not data.
"""
from __future__ import annotations

from dataclasses import field, fields
from datetime import datetime, timezone
from typing import Annotated, Literal, Mapping, Protocol, TypeAlias

from pydantic import Field
from pydantic.dataclasses import dataclass, rebuild_dataclass

from .base import RecordBase
from .identity import semantic_fingerprint
from .records import (
    RECORD_CONFIG, ArtifactRef, BackendAdapter, Candidate, DataTable, Decision,
    ErrorRecord, EvaluationResult, EvidenceRef, Key, MetricValue, NativeJobAdapter,
    NativeJobRequest, Observation, Record, Resources, RunRequest, SelectionPolicy,
    Study, VersionRef,
)

AssessmentStatus: TypeAlias = Literal['ok', 'missing', 'error', 'not_applicable']
ActivityStatus: TypeAlias = Literal['completed', 'partial', 'error', 'cancelled', 'unknown']
OutputType: TypeAlias = Literal['bool', 'int', 'float', 'decimal', 'text']
ParityRequirement: TypeAlias = Literal['verified', 'record_only']


def _unknown_inventory():
    return Observation[bool](value=None, status='unknown', reason='Inventory completeness was not reported')


def _empty_performed_inventory():
    return Observation[bool](value=True, status='observed', reason='No undisclosed new activity inventory')


class AssessmentRecord(RecordBase):
    def __post_init__(self):
        super().__post_init__()
        from .assessment_validation import validate_local
        validate_local(self)

    def validate(self):
        from .assessment_validation import validate
        return validate(self)

    def fingerprint(self):
        return semantic_fingerprint('assessment:0.1:' + type(self).__name__, self)

    def save(self, path):
        from ..storage.assessment_manifests import save_assessment
        return save_assessment(self, path)

    @classmethod
    def load(cls, path):
        from ..storage.assessment_manifests import load_assessment
        return load_assessment(path, expected_type=cls)


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class ConfigurationSpec(AssessmentRecord):
    collector: VersionRef
    params: Record = field(default_factory=dict)
    snapshot_requirement: ParityRequirement = 'verified'


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class SnapshotEntry(AssessmentRecord):
    id: str
    path: str
    source_tool: str
    scope: str
    role: str
    artifact: ArtifactRef


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class CandidateSnapshot(AssessmentRecord):
    candidate_id: str
    candidate_fingerprint: str
    entries: tuple[SnapshotEntry, ...]
    collector: VersionRef
    params_fingerprint: str
    context: Record = field(default_factory=dict)
    inventory_complete: Observation[bool] = field(default_factory=_unknown_inventory)
    omissions: tuple[str, ...] = ()
    fingerprint: str = ''


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class CandidateSubject(AssessmentRecord):
    candidate_id: str
    candidate_fingerprint: str
    snapshot_fingerprint: str | None = None
    kind: Literal['candidate'] = 'candidate'


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class ComponentSubject(AssessmentRecord):
    candidate: CandidateSubject
    locator: str
    source_tool: str
    scope: str
    entry_id: str | None = None
    mapping_issue: str | None = None
    kind: Literal['component'] = 'component'


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class RunSubject(AssessmentRecord):
    candidate_id: str
    candidate_fingerprint: str
    run_set_id: str
    run_id: str
    assignment_id: str
    kind: Literal['run'] = 'run'


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class StudySubject(AssessmentRecord):
    study_id: str
    study_fingerprint: str
    population: Record
    population_fingerprint: str
    kind: Literal['study'] = 'study'


SubjectRef: TypeAlias = Annotated[CandidateSubject | ComponentSubject | RunSubject | StudySubject, Field(discriminator='kind')]


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class ConfigurationOrigin(AssessmentRecord):
    request_id: str
    check_id: str
    check_fingerprint: str
    evaluator: VersionRef
    kind: Literal['configuration'] = 'configuration'


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class BehaviorOrigin(AssessmentRecord):
    run_id: str
    metric: str
    key: Key | None = None
    kind: Literal['behavior'] = 'behavior'


AssessmentOrigin: TypeAlias = Annotated[ConfigurationOrigin | BehaviorOrigin, Field(discriminator='kind')]


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class ActivityRef(AssessmentRecord):
    namespace: Literal['assessment', 'behavior']
    id: str


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class Finding(AssessmentRecord):
    id: str
    subject: SubjectRef
    rule: VersionRef
    message: str
    severity: Literal['error', 'warning', 'info']
    basis: Literal['declared', 'observed', 'estimated', 'inferred']
    evidence: tuple[EvidenceRef, ...] = ()
    suggestion: str | None = None
    evidence_tier: str | None = None
    metadata: Record = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class AssessmentValue(AssessmentRecord):
    id: str
    value: MetricValue | None
    status: AssessmentStatus
    basis: Literal['observed', 'estimated', 'inferred'] | None
    reason: str
    evidence: tuple[EvidenceRef, ...] = ()
    unit: str | None = None


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class Assessment(AssessmentRecord):
    id: str
    subject: SubjectRef
    origin: AssessmentOrigin
    status: AssessmentStatus
    conclusion: Decision
    values: tuple[AssessmentValue, ...] = ()
    findings: tuple[Finding, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    reason: str = ''
    activity_refs: tuple[ActivityRef, ...] = ()


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class CheckSpec(AssessmentRecord):
    id: str
    evaluator: VersionRef
    candidate_ids: tuple[str, ...]
    params: Record = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    required_roles: tuple[str, ...] = ()
    reference_columns: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    output_types: Mapping[str, OutputType] = field(default_factory=dict)
    rule_selection: Record = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class CheckCoverage(AssessmentRecord):
    candidate_id: str
    check_id: str
    status: Literal['completed', 'partial', 'error', 'blocked', 'not_applicable', 'unknown']
    reason: str = ''
    request_id: str | None = None
    assessment_id: str | None = None
    inventory_complete: Observation[bool] = field(default_factory=_unknown_inventory)
    requested_rules: tuple[str, ...] | None = None
    rule_statuses: Mapping[str, str] = field(default_factory=dict)
    omitted_suppressed_findings: bool | None = None


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class AssessmentActivity(AssessmentRecord):
    id: str
    request_id: str
    implementation: VersionRef
    input_fingerprint: str
    subjects: tuple[SubjectRef, ...]
    phase: Literal['collection', 'configuration_evaluation']
    status: ActivityStatus
    resources: Resources
    inventory_complete: Observation[bool]
    started_at: datetime | None = None
    ended_at: datetime | None = None
    artifacts: Mapping[str, ArtifactRef] = field(default_factory=dict)
    error: ErrorRecord | None = None


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class ConfigurationCapture(AssessmentRecord):
    id: str
    request_id: str
    candidate_id: str
    candidate_fingerprint: str
    snapshot: CandidateSnapshot | None
    status: ActivityStatus
    activities: tuple[AssessmentActivity, ...] = ()
    artifacts: Mapping[str, ArtifactRef] = field(default_factory=dict)
    error: ErrorRecord | None = None


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class SnapshotBinding(AssessmentRecord):
    candidate_id: str
    candidate_fingerprint: str
    snapshot_fingerprint: str
    status: Literal['verified', 'mismatch', 'unknown']
    reason: str
    run_ids: tuple[str, ...] = ()
    job_ids: tuple[str, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class ConclusionRequirement(AssessmentRecord):
    check_id: str
    kind: Literal['conclusion_pass'] = 'conclusion_pass'


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class NoFindingsRequirement(AssessmentRecord):
    check_id: str
    min_severity: Literal['error', 'warning', 'info'] = 'warning'
    allowed_tiers: tuple[str, ...] | None = None
    kind: Literal['no_findings'] = 'no_findings'


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class ValueRequirement(AssessmentRecord):
    check_id: str
    value_id: str
    op: Literal['==', '!=', '>', '>=', '<', '<=']
    expected_value: MetricValue
    kind: Literal['value'] = 'value'


ConfigurationRequirement: TypeAlias = Annotated[ConclusionRequirement | NoFindingsRequirement | ValueRequirement, Field(discriminator='kind')]


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class RequirementDecision(AssessmentRecord):
    requirement: ConfigurationRequirement
    decision: Decision
    assessment_ids: tuple[str, ...] = ()
    coverage: tuple[CheckCoverage, ...] = ()
    reason: str = ''


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class GateDecision(AssessmentRecord):
    gate_id: str
    candidate_id: str
    decision: Decision
    allowed: bool
    requirements: tuple[RequirementDecision, ...] = ()
    reason: str = ''


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class GateSpec(AssessmentRecord):
    id: str
    candidate_ids: tuple[str, ...]
    requirements: tuple[ConfigurationRequirement, ...]
    on_unknown: Literal['block', 'allow'] = 'block'


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class AssessmentPolicy(AssessmentRecord):
    id: str
    revision: str
    configuration_requirements: tuple[ConfigurationRequirement, ...] = ()
    behavior_policy: SelectionPolicy | None = None


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class BranchOutcome(AssessmentRecord):
    kind: Literal['configuration', 'behavior']
    candidate_ids: tuple[str, ...]
    status: Literal['completed', 'partial', 'error', 'cancelled', 'not_run', 'unknown']
    reason: str
    artifacts: Mapping[str, ArtifactRef] = field(default_factory=dict)
    error: ErrorRecord | None = None
    gate_decisions: tuple[GateDecision, ...] = ()


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class AssessmentPlan(AssessmentRecord):
    id: str
    project_id: str
    candidates: Mapping[str, Candidate]
    configuration: Mapping[str, ConfigurationSpec] = field(default_factory=dict)
    behavior: Study | None = None
    checks: tuple[CheckSpec, ...] = ()
    gates: tuple[GateSpec, ...] = ()
    reference_sets: Mapping[str, VersionRef] = field(default_factory=dict)
    max_concurrency: int = 1


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class AssessmentResult(AssessmentRecord):
    id: str
    plan: AssessmentPlan
    configuration: Mapping[str, ConfigurationCapture] = field(default_factory=dict)
    snapshot_bindings: tuple[SnapshotBinding, ...] = ()
    check_coverage: tuple[CheckCoverage, ...] = ()
    assessments: tuple[Assessment, ...] = ()
    activities: tuple[AssessmentActivity, ...] = ()
    branches: tuple[BranchOutcome, ...] = ()
    behavior_result: EvaluationResult | None = None
    performed_activity_refs: tuple[ActivityRef, ...] = ()
    performed_inventory_complete: Observation[bool] = field(default_factory=_empty_performed_inventory)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    schema_version: Literal['0.1'] = '0.1'

    def summary(self):
        from ..results.assessment_query import summary_assessments
        return summary_assessments(self)

    def explain(self, subject):
        from ..results.assessment_query import explain_candidate_assessments
        return explain_candidate_assessments(self, subject)

    def compare(self, baseline, challenger, **kwargs):
        from ..results.assessment_comparison import compare_candidate_assessments
        return compare_candidate_assessments(self, baseline, challenger, **kwargs)

    def select(self, policy):
        from ..results.assessment_selection import decide_assessments
        return decide_assessments(self, policy)

    def report(self, path, **kwargs):
        from ..reporting.assessment_html import report_assessment
        return report_assessment(self, path, **kwargs)

    def resources(self, *, cost_scope=('model',), incremental=False):
        from ..results.assessment_query import assessment_resources
        return assessment_resources(self, cost_scope=cost_scope, incremental=incremental)


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class ConfigurationCollectRequest(AssessmentRecord):
    id: str
    activity_id: str
    candidate: Candidate
    spec: ConfigurationSpec
    input_fingerprint: str = ''


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class AssessmentRequest(AssessmentRecord):
    id: str
    check: CheckSpec
    subject: CandidateSubject
    capture: ConfigurationCapture
    activity_id: str
    check_fingerprint: str = ''
    references: Mapping[str, tuple[Record, ...]] = field(default_factory=dict)
    reference_sets: Mapping[str, VersionRef] = field(default_factory=dict)
    reference_keys: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    dependencies: Mapping[str, Assessment] = field(default_factory=dict)
    input_fingerprint: str = ''


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class AssessmentOutput(AssessmentRecord):
    assessment: Assessment
    activity: AssessmentActivity
    coverage: CheckCoverage
    artifacts: Mapping[str, ArtifactRef] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class SnapshotCapabilities(AssessmentRecord):
    backends: tuple[VersionRef, ...]
    scopes: tuple[str, ...]
    direct: bool
    native: bool
    verified: bool


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class SnapshotBindRequest(AssessmentRecord):
    id: str
    snapshots: Mapping[str, CandidateSnapshot]
    requirements: Mapping[str, ParityRequirement]
    run_request: RunRequest | None = None
    native_job_request: NativeJobRequest | None = None


class AssessmentRecorder(Protocol):
    def record_artifact(self, name: str, artifact: ArtifactRef) -> None: ...
    def record_activity(self, activity: AssessmentActivity, *, final: bool = False) -> None: ...
    def record_capture(self, capture: ConfigurationCapture, *, final: bool = False) -> None: ...


class ConfigurationCollector(Protocol):
    @property
    def ref(self) -> VersionRef: ...
    async def collect(self, request: ConfigurationCollectRequest, *, recorder: AssessmentRecorder) -> ConfigurationCapture: ...


class ConfigurationEvaluator(Protocol):
    @property
    def ref(self) -> VersionRef: ...
    async def evaluate(self, request: AssessmentRequest, *, recorder: AssessmentRecorder) -> AssessmentOutput: ...


class SnapshotSession(Protocol):
    @property
    def bindings(self) -> tuple[SnapshotBinding, ...]: ...
    @property
    def backend(self) -> BackendAdapter | None: ...
    @property
    def job_backend(self) -> NativeJobAdapter | None: ...
    async def __aenter__(self) -> SnapshotSession: ...
    async def __aexit__(self, exc_type, exc, traceback) -> bool | None: ...


class SnapshotBinder(Protocol):
    @property
    def ref(self) -> VersionRef: ...
    def capabilities(self) -> SnapshotCapabilities: ...
    async def prepare(self, request: SnapshotBindRequest) -> SnapshotSession: ...


def snapshot_fingerprint(snapshot: CandidateSnapshot) -> str:
    """Hash logical content identity, excluding relocated transport paths."""
    entries = sorted((
        {'id': e.id, 'path': e.path.replace('\\', '/'), 'source_tool': e.source_tool,
         'scope': e.scope, 'role': e.role, 'sha256': e.artifact.sha256,
         'media_type': e.artifact.media_type}
        for e in snapshot.entries
    ), key=lambda entry: (entry['source_tool'], entry['scope'], entry['path'], entry['id']))
    payload = {f.name: getattr(snapshot, f.name) for f in fields(snapshot)
               if f.name not in ('entries', 'fingerprint')}
    payload['entries'] = entries
    return semantic_fingerprint('assessment:0.1:snapshot', payload)


def check_input_fingerprint(request: AssessmentRequest) -> str:
    return semantic_fingerprint('assessment:0.1:check-input', {
        'check': request.check, 'subject': request.subject,
        'snapshot': request.capture.snapshot.fingerprint if request.capture.snapshot else None,
        'capture_status': request.capture.status,
        'references': request.references,
        'reference_sets': request.reference_sets,
        'reference_keys': request.reference_keys,
        'dependencies': request.dependencies,
    })


def collection_input_fingerprint(request: ConfigurationCollectRequest) -> str:
    return semantic_fingerprint('assessment:0.1:collection-input', {
        'candidate': request.candidate.fingerprint(),
        'collector': request.spec.collector, 'params': request.spec.params,
    })


ASSESSMENT_RECORDS = tuple(value for value in tuple(globals().values())
                           if isinstance(value, type) and value is not AssessmentRecord
                           and issubclass(value, AssessmentRecord))
for _record in ASSESSMENT_RECORDS:
    rebuild_dataclass(_record, force=True, _types_namespace=globals())

__all__ = [record.__name__ for record in ASSESSMENT_RECORDS] + [
    'SubjectRef', 'AssessmentOrigin', 'ConfigurationRequirement', 'AssessmentRecorder',
    'ConfigurationCollector', 'ConfigurationEvaluator', 'SnapshotSession', 'SnapshotBinder',
    'snapshot_fingerprint', 'check_input_fingerprint', 'collection_input_fingerprint',
    'ASSESSMENT_RECORDS', 'AssessmentStatus', 'ActivityStatus', 'OutputType', 'ParityRequirement',
]
