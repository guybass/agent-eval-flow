"""Validated public records for the Agent Eval Flow 0.4 data contract.

Behavior lives in the owning modules. This module defines the single shared
record schema used for construction, runtime boundaries and persistence.
"""
from __future__ import annotations
from dataclasses import field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Generic, Literal, Mapping, Protocol, Sequence, TypeAlias, TypeVar, Union
from typing_extensions import TypeAliasType
from pydantic import ConfigDict
from pydantic.dataclasses import dataclass, rebuild_dataclass
from .base import RecordBase
from .errors import *

JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue = TypeAliasType("JSONValue", Union[str, int, float, bool, None, Mapping[str, "JSONValue"], tuple["JSONValue", ...], list["JSONValue"]])
Record: TypeAlias = Mapping[str, JSONValue]
Key: TypeAlias = Mapping[str, str | int]
MetricValue: TypeAlias = bool | int | float | Decimal | str
CostCategory: TypeAlias = str
T = TypeVar("T")
RECORD_CONFIG = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False, validate_default=True, revalidate_instances="always")
@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class VersionRef(RecordBase):
    name: str
    revision: str | None = None


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class ArtifactRef(RecordBase):
    uri: str
    media_type: str
    sha256: str | None = None


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class EvidenceRef(RecordBase):
    artifact: ArtifactRef
    locator: str | None = None
    description: str = ""


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class Observation(RecordBase, Generic[T]):
    value: T | None
    status: Literal['observed', 'estimated', 'unknown']
    reason: str | None = None
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class ValidationIssue(RecordBase):
    path: str
    message: str
    severity: Literal['error', 'warning']


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class ValidationReport(RecordBase):
    issues: tuple[ValidationIssue, ...]

    @property
    def valid(self):
        return not any(issue.severity == "error" for issue in self.issues)
    def raise_for_errors(self):
        if not self.valid:
            raise ValidationError(self)


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class DataTable(RecordBase):
    rows: tuple[Record, ...]
    key: tuple[str, ...]
    schema: Mapping[str, Literal['str', 'int', 'float', 'bool', 'json']]

    def to_records(self):
        return self.rows


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class AgentInput(RecordBase):
    unit: Key
    tables: Mapping[str, tuple[Record, ...]]


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class EvalDataset(RecordBase):
    id: str
    units: DataTable
    unit_key: tuple[str, ...]
    input_columns: Mapping[str, tuple[str, ...]]
    records: Mapping[str, DataTable] = field(default_factory=dict)
    references: Mapping[str, DataTable] = field(default_factory=dict)
    cluster_by: tuple[str, ...] | None = None
    description: str = ""

    def agent_input(self, unit):
        from .dataset import agent_input
        return agent_input(self, unit)
    def select(self, units):
        from .dataset import select
        return select(self, units)
    @classmethod
    def from_records(cls, **kwargs):
        from .dataset import from_records
        return from_records(**kwargs)


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class ComponentSpec(RecordBase):
    kind: Literal['model', 'skill', 'flow', 'harness', 'tool', 'prompt', 'memory', 'environment', 'other']
    ref: VersionRef
    params: Mapping[str, JSONValue] = field(default_factory=dict)
    content: ArtifactRef | None = None


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class ConfigChange(RecordBase):
    op: Literal['add', 'remove', 'replace']
    path: str
    before: JSONValue
    after: JSONValue


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class Candidate(RecordBase):
    id: str
    backend: VersionRef
    components: Mapping[str, ComponentSpec]
    settings: Mapping[str, JSONValue] = field(default_factory=dict)
    description: str = ""
    native: NativeConfig | None = None

    def derive(self, *, id, components=None, settings=None, native=...):
        from .candidate import derive
        return derive(self, id=id, components=components, settings=settings, native=native)
    def diff(self, other):
        from .candidate import diff
        return diff(self, other)


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class NativeConfig(RecordBase):
    schema_ref: VersionRef
    values: Record = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class VerifierSpec(RecordBase):
    id: str
    implementation: VersionRef
    reference_columns: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    params: Record = field(default_factory=dict)
    native: NativeConfig | None = None
    budget: Budget | None = None


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class NativeJobConfig(RecordBase):
    id: str
    backend: VersionRef
    candidate_ids: tuple[str, ...]
    native: NativeConfig | None = None
    verifiers: tuple[VerifierSpec, ...] = ()


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class Budget(RecordBase):
    wall_time_s: float
    max_tokens: int | None = None
    max_cost_usd: Decimal | None = None


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class ExecutionPolicy(RecordBase):
    budget: Budget
    cost_scope: tuple[CostCategory, ...] = ("model",)
    repetitions: int = 1
    max_concurrency: int = 1
    order_seed: int | None = 0
    infrastructure_retries: int = 0
    native_jobs: tuple[NativeJobConfig, ...] = ()


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class Contrast(RecordBase):
    id: str
    baseline: str
    challenger: str
    metrics: tuple[str, ...]
    expected_changes: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class Assignment(RecordBase):
    id: str
    candidate_id: str
    candidate_fingerprint: str
    unit: Key
    repetition: int


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class PlannedJob(RecordBase):
    id: str
    config: NativeJobConfig
    assignment_ids: tuple[str, ...]


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class DatasetInfo(RecordBase):
    id: str
    fingerprint: str
    unit_key: tuple[str, ...]
    cluster_by: tuple[str, ...]
    units: DataTable


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class RunPlan(RecordBase):
    study_id: str
    study_fingerprint: str
    project_id: str
    dataset: DatasetInfo
    candidates: Mapping[str, Candidate]
    assignments: tuple[Assignment, ...]
    execution: ExecutionPolicy
    contrasts: tuple[Contrast, ...] = ()
    environment: ComponentSpec | None = None
    native_jobs: tuple[PlannedJob, ...] = ()


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class Study(RecordBase):
    id: str
    project_id: str
    question: str
    dataset: EvalDataset
    candidates: Mapping[str, Candidate]
    suite: EvalSuite
    execution: ExecutionPolicy
    contrasts: tuple[Contrast, ...] = ()
    environment: ComponentSpec | None = None

    def plan(self):
        from ..execution.planning import plan
        return plan(self)
    def run(self, *, backends=None, job_backends=None):
        from ..execution.runner import run
        return run(self, backends=backends, job_backends=job_backends)
    def evaluate(self, **bindings):
        from ..pipeline.api import EvaluationPipeline
        return EvaluationPipeline(study=self, **bindings).eval()


RunStatus: TypeAlias = Literal['not_run', 'unobserved', 'running', 'awaiting_input', 'paused', 'completed', 'agent_error', 'timed_out', 'infrastructure_error', 'cancelled']
@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class ErrorRecord(RecordBase):
    code: str
    message: str
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class Resources(RecordBase):
    cost_usd: Observation[Decimal]
    cost_scope: tuple[CostCategory, ...]
    input_tokens: Observation[int]
    output_tokens: Observation[int]
    human_minutes: Observation[float]


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class Execution(RecordBase):
    id: str
    slot: str
    retry_index: int
    parent_id: str | None
    status: RunStatus
    started_at: datetime | None
    ended_at: datetime | None
    effective_config: Observation[Record]
    resources: Resources
    role: Literal['main', 'attempt', 'review', 'selection', 'subagent', 'tool', 'other'] = "main"
    native_refs: Mapping[str, str] = field(default_factory=dict)
    error: ErrorRecord | None = None


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class Event(RecordBase):
    id: str
    execution_id: str
    kind: str
    at: datetime | None
    fields: Record
    inputs: tuple[EvidenceRef, ...] = ()
    outputs: tuple[EvidenceRef, ...] = ()
    source: EvidenceRef | None = None


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class Run(RecordBase):
    id: str
    assignment_id: str
    status: RunStatus
    cost_scope: tuple[CostCategory, ...]
    output: JSONValue
    output_state: Literal['available', 'unavailable', 'unknown']
    artifacts: Mapping[str, ArtifactRef]
    executions: tuple[Execution, ...]
    started_at: datetime | None
    ended_at: datetime | None
    environment: Observation[Record]
    execution_inventory_complete: Observation[bool]
    output_sources: tuple[str, ...] = ()
    native_refs: Mapping[str, str] = field(default_factory=dict)
    events: tuple[Event, ...] = ()
    error: ErrorRecord | None = None
    job_id: str | None = None

    def resources(self):
        from .values import aggregate_resources
        self.validate().raise_for_errors()
        return aggregate_resources([execution.resources for execution in self.executions], inventory_complete=self.execution_inventory_complete, cost_scope=self.cost_scope)
    def duration_s(self):
        from .values import unknown, observed
        if self.started_at is None or self.ended_at is None:
            return unknown("Run start/end or confirmed termination is not captured")
        return observed((self.ended_at - self.started_at).total_seconds())


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class Coverage(RecordBase):
    planned: int
    completed: int
    failed: int
    unavailable: int
    pending: int
    resource_unknown: int


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class RunSet(RecordBase):
    id: str
    plan: RunPlan
    runs: tuple[Run, ...]
    grading_inventory_complete: Observation[bool]
    importer: VersionRef | None = None
    import_source: ArtifactRef | None = None
    native_jobs: tuple[NativeJobRecord, ...] = ()
    native_grades: tuple[NativeGradeBundle, ...] = ()
    projections: tuple[ProjectionReport, ...] = ()
    schema_version: str = "0.4"

    def get(self, run_id):
        return next((run for run in self.runs if run.id == run_id), None) or self._missing(run_id)
    def _missing(self, run_id):
        raise KeyError(run_id)
    def for_candidate(self, candidate_id):
        assignments = {a.id for a in self.plan.assignments if a.candidate_id == candidate_id}
        if candidate_id not in self.plan.candidates:
            raise KeyError(candidate_id)
        return tuple(run for run in self.runs if run.assignment_id in assignments)
    def coverage(self, candidate_id=None):
        from .runset import coverage
        return coverage(self, candidate_id)
    def rows(self, kind):
        from .runset import rows
        return rows(self, kind)
    @classmethod
    def import_from(cls, source, *, plan, importer):
        from ..execution.importing import import_from
        return import_from(source, plan=plan, importer=importer)


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class EvaluatorSource(RecordBase):
    ref: VersionRef
    mode: Literal['single', 'batch'] = "single"
    kind: Literal['evaluator'] = "evaluator"


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class NativeGradeSource(RecordBase):
    channel: str
    native_metric: str
    evaluator: VersionRef | None = None
    activity_id: str | None = None
    kind: Literal['native_grade'] = "native_grade"


MetricSource: TypeAlias = EvaluatorSource | NativeGradeSource
@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class MetricSpec(RecordBase):
    id: str
    source: MetricSource
    output_type: Literal['bool', 'int', 'float', 'decimal', 'text']
    role: Literal['quality', 'resource', 'diagnostic']
    params: Mapping[str, JSONValue] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    unit: str | None = None


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class ScoreTerm(RecordBase):
    metric: str
    points: float


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class Rubric(RecordBase):
    terms: tuple[ScoreTerm, ...]

    @property
    def max_score(self):
        return sum(term.points for term in self.terms)


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class Threshold(RecordBase):
    metric: str
    op: Literal['==', '!=', '>', '>=', '<', '<=']
    value: MetricValue


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class AcceptanceRule(RecordBase):
    all_of: tuple[Threshold, ...]


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class SummarySpec(RecordBase):
    id: str
    metric: str
    reducer: Literal['mean', 'sum', 'min', 'max', 'p95', 'rate'] | VersionRef
    params: Mapping[str, JSONValue] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class EvalSuite(RecordBase):
    id: str
    version: str
    metrics: tuple[MetricSpec, ...]
    summaries: tuple[SummarySpec, ...]
    acceptance: AcceptanceRule | None = None
    rubric: Rubric | None = None
    evaluation_cost_scope: tuple[CostCategory, ...] = ("model",)

    def evaluate(self, *, dataset, runs, evaluators, reducers=None, batch_evaluators=None):
        from ..evaluation.engine import evaluate
        return evaluate(dataset=dataset, runs=runs, suite=self, evaluators=evaluators, reducers=reducers, batch_evaluators=batch_evaluators)


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class Measurement(RecordBase):
    run_id: str
    metric: str
    value: MetricValue | None
    status: Literal['ok', 'missing', 'error', 'not_applicable']
    basis: Literal['observed', 'estimated'] | None
    reason: str
    evidence: tuple[EvidenceRef, ...] = ()
    key: Key | None = None
    activity_ids: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class ScoreContribution(RecordBase):
    metric: str
    earned: float | None
    possible: float
    basis: Literal['observed', 'estimated'] | None
    reason: str
    evidence: tuple[EvidenceRef, ...]


Decision: TypeAlias = Literal['pass', 'fail', 'unknown']
@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class GateResult(RecordBase):
    threshold: Threshold
    decision: Decision
    basis: Literal['observed', 'estimated'] | None
    reason: str


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class TaskScore(RecordBase):
    run_id: str
    score: Observation[float]
    contributions: tuple[ScoreContribution, ...]
    acceptance: Decision
    acceptance_basis: Literal['observed', 'estimated'] | None
    gates: tuple[GateResult, ...]
    reason: str


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class CandidateSummary(RecordBase):
    candidate_id: str
    summary_id: str
    value: Observation[MetricValue]
    planned: int
    observed: int
    missing: int
    errors: int
    not_applicable: int
    included_run_ids: tuple[str, ...]
    exclusions: Mapping[str, str]
    reason: str
    available_value: MetricValue | None = None


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class ComparisonRow(RecordBase):
    metric: str
    baseline: Observation[MetricValue]
    challenger: Observation[MetricValue]
    delta: Observation[float]
    interval: tuple[float, float] | None = None
    interval_note: str | None = None


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class Comparison(RecordBase):
    baseline_id: str
    challenger_id: str
    changes: tuple[ConfigChange, ...]
    rows: tuple[ComparisonRow, ...]
    task_count: int
    cluster_count: int
    limitations: tuple[str, ...]


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class ObjectiveTerm(RecordBase):
    metric: str
    direction: Literal['minimize', 'maximize']
    weight: float = 1.0
    bounds: tuple[float, float] | None = None


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class SelectionPolicy(RecordBase):
    id: str
    objectives: tuple[ObjectiveTerm, ...]
    requirements: tuple[Threshold, ...] = ()
    mode: Literal['lexicographic', 'weighted', 'pareto'] = "lexicographic"
    allow_estimates: bool = False


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class SelectionRow(RecordBase):
    candidate_id: str
    eligibility: Decision
    reasons: tuple[str, ...]
    preference_score: float | None
    rank: int | None


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class Selection(RecordBase):
    result_id: str
    policy: SelectionPolicy
    status: Literal['selected', 'tie', 'frontier', 'none_eligible']
    selected_ids: tuple[str, ...]
    rows: tuple[SelectionRow, ...]


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class Explanation(RecordBase):
    run_id: str
    score: TaskScore
    measurements: tuple[Measurement, ...]
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class EvaluationResult(RecordBase):
    id: str
    created_at: datetime
    runs: RunSet
    suite: EvalSuite
    suite_fingerprint: str
    measurements: tuple[Measurement, ...]
    task_scores: tuple[TaskScore, ...]
    summaries: tuple[CandidateSummary, ...]
    evaluation_resources: Resources
    performed_grading_inventory_complete: Observation[bool]
    activities: tuple[EvaluationActivity, ...] = ()
    performed_activity_ids: tuple[str, ...] = ()
    schema_version: str = "0.4"

    def summary(self):
        from ..results.query import summary
        return summary(self)
    def incremental_evaluation_resources(self):
        from .values import aggregate_resources
        ids = set(self.performed_activity_ids)
        return aggregate_resources([a.resources for a in self.activities if a.id in ids], inventory_complete=self.performed_grading_inventory_complete, cost_scope=self.suite.evaluation_cost_scope)
    def explain(self, run_id):
        from ..results.query import explain
        return explain(self, run_id)
    def compare(self, baseline, challenger, *, metrics, confidence=None):
        from ..results.comparison import compare
        return compare(self, baseline, challenger, metrics=metrics, confidence=confidence)
    def select(self, policy):
        from ..results.selection import select
        return select(self, policy)
    def report(self, path, *, selection=None):
        from ..reporting.html import report
        return report(self, path, selection=selection)


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class BackendCapabilities(RecordBase):
    wall_time_limit: bool
    token_limit: bool
    cost_limit: bool
    reset_state: bool


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class NativeJobCapabilities(RecordBase):
    limits: BackendCapabilities
    private_verifier_channel: bool
    fixed_repetitions: bool


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class RunRequest(RecordBase):
    run_id: str
    assignment: Assignment
    candidate: Candidate
    input: AgentInput
    policy: ExecutionPolicy
    environment: ComponentSpec | None


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class VerifierInput(RecordBase):
    run_id: str
    verifier_id: str
    references: Mapping[str, tuple[Record, ...]]


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class NativeJobRequest(RecordBase):
    job_id: str
    run_set_id: str
    planned_job_id: str
    config: NativeJobConfig
    requests: tuple[RunRequest, ...]
    verifier_inputs: tuple[VerifierInput, ...] = ()


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class NativeRunLink(RecordBase):
    run_id: str
    assignment_id: str
    native_refs: Mapping[str, str]


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class ProjectionReport(RecordBase):
    mapper: VersionRef
    source_format: VersionRef
    sources: tuple[ArtifactRef, ...]
    omitted_fields: tuple[str, ...] = ()
    issues: tuple[ValidationIssue, ...] = ()


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class NativeJobRecord(RecordBase):
    id: str
    planned_job_id: str
    backend: VersionRef
    assignment_ids: tuple[str, ...]
    status: Literal['running', 'completed', 'partial', 'error', 'cancelled', 'unknown']
    started_at: datetime | None
    ended_at: datetime | None
    native_refs: Mapping[str, str] = field(default_factory=dict)
    links: tuple[NativeRunLink, ...] = ()
    artifacts: Mapping[str, ArtifactRef] = field(default_factory=dict)
    effective_config: Observation[Record] | None = None
    error: ErrorRecord | None = None


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class NativeJobOutput(RecordBase):
    job: NativeJobRecord
    runs: tuple[Run, ...]
    grading_inventory_complete: Observation[bool]
    native_grades: tuple[NativeGradeBundle, ...] = ()
    projections: tuple[ProjectionReport, ...] = ()


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class ImportedCapture(RecordBase):
    runs: tuple[Run, ...]
    grading_inventory_complete: Observation[bool]
    native_jobs: tuple[NativeJobRecord, ...] = ()
    native_grades: tuple[NativeGradeBundle, ...] = ()
    projections: tuple[ProjectionReport, ...] = ()


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class EvaluationContext(RecordBase):
    unit: Key
    inputs: AgentInput
    references: Mapping[str, tuple[Record, ...]]
    measurements: Mapping[str, Measurement] = field(default_factory=dict)
    native_job: NativeJobRecord | None = None
    evaluation_cost_scope: tuple[CostCategory, ...] = ("model",)


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class MetricOutput(RecordBase):
    task: Measurement
    details: tuple[Measurement, ...] = ()
    evaluation_resources: Resources


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class EvaluationActivity(RecordBase):
    id: str
    evaluator: VersionRef
    config_fingerprint: str | None
    run_ids: tuple[str, ...]
    status: Literal['completed', 'partial', 'error']
    phase: Literal['native_verifier', 'post_run']
    resources: Resources
    started_at: datetime | None = None
    ended_at: datetime | None = None
    native_refs: Mapping[str, str] = field(default_factory=dict)
    artifacts: Mapping[str, ArtifactRef] = field(default_factory=dict)
    error: ErrorRecord | None = None


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class NativeGrade(RecordBase):
    id: str
    run_id: str
    native_metric: str
    activity_id: str
    value: MetricValue | None
    status: Literal['ok', 'missing', 'error', 'not_applicable']
    basis: Literal['observed', 'estimated'] | None
    reason: str
    evidence: tuple[EvidenceRef, ...] = ()
    key: Key | None = None


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class NativeGradeBundle(RecordBase):
    id: str
    channel: str
    projection: ProjectionReport
    grades: tuple[NativeGrade, ...]
    activities: tuple[EvaluationActivity, ...]


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class EvaluationItem(RecordBase):
    run: Run
    context: EvaluationContext


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class BatchMetricRequest(RecordBase):
    id: str
    config_fingerprint: str
    metric: MetricSpec
    items: tuple[EvaluationItem, ...]


@dataclass(frozen=True, kw_only=True, config=RECORD_CONFIG)
class BatchMetricOutput(RecordBase):
    measurements: tuple[Measurement, ...]
    activity: EvaluationActivity


class NativeJobRecorder(Protocol):

    def record_job(self, job: NativeJobRecord) -> None:
        ...

    def record_run(self, run: Run) -> None:
        ...

    def record_grade_bundle(self, bundle: NativeGradeBundle) -> None:
        ...

class NativeJobAdapter(Protocol):

    @property
    def ref(self) -> VersionRef:
        ...

    def capabilities(self) -> NativeJobCapabilities:
        ...

    async def run_job(self, request: NativeJobRequest, *, recorder: NativeJobRecorder) -> NativeJobOutput:
        ...

class RunRecorder(Protocol):

    def record_execution(self, execution: Execution) -> None:
        ...

    def record_event(self, event: Event) -> None:
        ...

    def record_artifact(self, name: str, artifact: ArtifactRef) -> None:
        ...

class BackendAdapter(Protocol):

    @property
    def ref(self) -> VersionRef:
        ...

    def capabilities(self) -> BackendCapabilities:
        ...

    def run(self, request: RunRequest, *, recorder: RunRecorder) -> Run:
        ...

class RunImporter(Protocol):

    @property
    def ref(self) -> VersionRef:
        ...

    def read(self, source: Path, *, plan: RunPlan) -> ImportedCapture:
        ...

class BatchMetricEvaluator(Protocol):

    @property
    def ref(self) -> VersionRef:
        ...

    async def compute_batch(self, request: BatchMetricRequest) -> BatchMetricOutput:
        ...

class MetricEvaluator(Protocol):

    @property
    def ref(self) -> VersionRef:
        ...

    def compute(self, spec: MetricSpec, context: EvaluationContext, run: Run) -> MetricOutput:
        ...

class SummaryReducer(Protocol):

    @property
    def ref(self) -> VersionRef:
        ...

    def reduce(self, spec: SummarySpec, *, candidate_id: str, assignments: tuple[Assignment, ...], runs: tuple[Run, ...], measurements: tuple[Measurement, ...], task_scores: tuple[TaskScore, ...]) -> CandidateSummary:
        ...

PUBLIC_RECORDS = (VersionRef, ArtifactRef, EvidenceRef, Observation, ValidationIssue, ValidationReport, DataTable, AgentInput, EvalDataset, ComponentSpec, ConfigChange, Candidate, NativeConfig, VerifierSpec, NativeJobConfig, Budget, ExecutionPolicy, Contrast, Assignment, PlannedJob, DatasetInfo, RunPlan, Study, ErrorRecord, Resources, Execution, Event, Run, Coverage, RunSet, EvaluatorSource, NativeGradeSource, MetricSpec, ScoreTerm, Rubric, Threshold, AcceptanceRule, SummarySpec, EvalSuite, Measurement, ScoreContribution, GateResult, TaskScore, CandidateSummary, ComparisonRow, Comparison, ObjectiveTerm, SelectionPolicy, SelectionRow, Selection, Explanation, EvaluationResult, BackendCapabilities, NativeJobCapabilities, RunRequest, VerifierInput, NativeJobRequest, NativeRunLink, ProjectionReport, NativeJobRecord, NativeJobOutput, ImportedCapture, EvaluationContext, MetricOutput, EvaluationActivity, NativeGrade, NativeGradeBundle, EvaluationItem, BatchMetricRequest, BatchMetricOutput,)
for _record in PUBLIC_RECORDS:
    rebuild_dataclass(_record, force=True, _types_namespace=globals())

__all__ = ['VersionRef', 'ArtifactRef', 'EvidenceRef', 'Observation', 'ValidationIssue', 'ValidationReport', 'DataTable', 'AgentInput', 'EvalDataset', 'ComponentSpec', 'ConfigChange', 'Candidate', 'NativeConfig', 'VerifierSpec', 'NativeJobConfig', 'Budget', 'ExecutionPolicy', 'Contrast', 'Assignment', 'PlannedJob', 'DatasetInfo', 'RunPlan', 'Study', 'ErrorRecord', 'Resources', 'Execution', 'Event', 'Run', 'Coverage', 'RunSet', 'EvaluatorSource', 'NativeGradeSource', 'MetricSpec', 'ScoreTerm', 'Rubric', 'Threshold', 'AcceptanceRule', 'SummarySpec', 'EvalSuite', 'Measurement', 'ScoreContribution', 'GateResult', 'TaskScore', 'CandidateSummary', 'ComparisonRow', 'Comparison', 'ObjectiveTerm', 'SelectionPolicy', 'SelectionRow', 'Selection', 'Explanation', 'EvaluationResult', 'BackendCapabilities', 'NativeJobCapabilities', 'RunRequest', 'VerifierInput', 'NativeJobRequest', 'NativeRunLink', 'ProjectionReport', 'NativeJobRecord', 'NativeJobOutput', 'ImportedCapture', 'EvaluationContext', 'MetricOutput', 'EvaluationActivity', 'NativeGrade', 'NativeGradeBundle', 'EvaluationItem', 'BatchMetricRequest', 'BatchMetricOutput', 'NativeJobRecorder', 'NativeJobAdapter', 'RunRecorder', 'BackendAdapter', 'RunImporter', 'BatchMetricEvaluator', 'MetricEvaluator', 'SummaryReducer', 'JSONScalar', 'JSONValue', 'Record', 'Key', 'MetricValue', 'CostCategory', 'RunStatus', 'MetricSource', 'Decision', 'PUBLIC_RECORDS']
