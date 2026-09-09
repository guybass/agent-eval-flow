"""Proposed API contract, not an installed package or implementation.

Six main objects: Study, EvalDataset, Candidate, RunSet, EvalSuite,
EvaluationResult. Contract revision 0.4: see ../DATA_CONTRACT.md for configuration,
wire encoding, native jobs, grading activities and cross-record invariants.
Record decorators describe constructor shapes, not a hand-written validator.
The proposed implementation uses Pydantic dataclasses and immutable snapshots.
Existing domain semantics remain in ../LIBRARY_OBJECT_MODEL.md.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Generic, Literal, Mapping, Protocol, Sequence, TypeAlias, TypeVar

JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | Sequence["JSONValue"] | Mapping[str, "JSONValue"]
Record: TypeAlias = Mapping[str, JSONValue]
Key: TypeAlias = Mapping[str, str | int]
MetricValue: TypeAlias = bool | int | float | Decimal | str
CostCategory: TypeAlias = str  # conventional names: model, tool, compute; extensible
T = TypeVar("T")


# Shared values. Implementations must deeply freeze nested mappings/sequences.
@dataclass(frozen=True, kw_only=True)
class VersionRef:
    name: str
    revision: str | None = ...

@dataclass(frozen=True, kw_only=True)
class ArtifactRef:
    uri: str
    media_type: str
    sha256: str | None = ...

@dataclass(frozen=True, kw_only=True)
class EvidenceRef:
    artifact: ArtifactRef
    locator: str | None = ...  # e.g. JSON Pointer, table key, file line, event ID
    description: str = ...

@dataclass(frozen=True, kw_only=True)
class Observation(Generic[T]):
    value: T | None
    status: Literal["observed", "estimated", "unknown"]
    reason: str | None = ...
    evidence: tuple[EvidenceRef, ...] = ...

@dataclass(frozen=True, kw_only=True)
class ValidationIssue:
    path: str
    message: str
    severity: Literal["error", "warning"]

@dataclass(frozen=True, kw_only=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...]
    @property
    def valid(self) -> bool: ...
    def raise_for_errors(self) -> None: ...

# Public exception names established by LLD level 2; no serialized schema change.
class AgentEvalFlowError(Exception): ...

class ValidationError(AgentEvalFlowError):
    report: ValidationReport
    def __init__(self, report: ValidationReport) -> None: ...

class ConfigurationError(ValidationError): ...
class CaptureCompatibilityError(ValidationError): ...
class CaptureValidationError(ValidationError): ...
class StorageError(AgentEvalFlowError): ...

@dataclass(frozen=True, kw_only=True)
class DataTable:
    rows: tuple[Record, ...]
    key: tuple[str, ...]
    schema: Mapping[str, Literal["str", "int", "float", "bool", "json"]]
    def validate(self) -> ValidationReport: ...
    def to_records(self) -> tuple[Record, ...]: ...


# 2. EvalDataset: ordinary keyed tables, public input projection, private gold.
@dataclass(frozen=True, kw_only=True)
class AgentInput:
    unit: Key
    tables: Mapping[str, tuple[Record, ...]]

@dataclass(frozen=True, kw_only=True)
class EvalDataset:
    id: str
    units: DataTable
    unit_key: tuple[str, ...]
    input_columns: Mapping[str, tuple[str, ...]]
    records: Mapping[str, DataTable] = ...
    references: Mapping[str, DataTable] = ...
    cluster_by: tuple[str, ...] | None = ...
    description: str = ...
    def validate(self) -> ValidationReport: ...
    def agent_input(self, unit: Key) -> AgentInput: ...
    def select(self, units: Sequence[Key]) -> EvalDataset: ...
    def fingerprint(self) -> str: ...
    def save(self, path: Path) -> None: ...
    @classmethod
    def load(cls, path: Path) -> EvalDataset: ...
    @classmethod
    def from_records(
        cls, *, id: str, units: Sequence[Record], unit_key: tuple[str, ...],
        input_columns: Mapping[str, tuple[str, ...]],
        records: Mapping[str, DataTable] | None = ...,
        references: Mapping[str, DataTable] | None = ...,
        cluster_by: tuple[str, ...] | None = ...,
    ) -> EvalDataset: ...


# 3. Candidate: the declared full system, independently of a runtime adapter.
@dataclass(frozen=True, kw_only=True)
class ComponentSpec:
    kind: Literal["model", "skill", "flow", "harness", "tool", "prompt", "memory", "environment", "other"]
    ref: VersionRef
    params: Mapping[str, JSONValue] = ...
    content: ArtifactRef | None = ...

@dataclass(frozen=True, kw_only=True)
class ConfigChange:
    op: Literal["add", "remove", "replace"]
    path: str
    before: JSONValue
    after: JSONValue

@dataclass(frozen=True, kw_only=True)
class Candidate:
    id: str
    backend: VersionRef
    components: Mapping[str, ComponentSpec]
    settings: Mapping[str, JSONValue] = ...
    description: str = ...
    native: NativeConfig | None = ...
    def validate(self) -> ValidationReport: ...
    def derive(
        self, *, id: str,
        components: Mapping[str, ComponentSpec | None] | None = ...,
        settings: Mapping[str, JSONValue] | None = ...,
        native: NativeConfig | None = ...,  # omitted: preserve; explicit None: clear
    ) -> Candidate: ...
    def diff(self, other: Candidate) -> tuple[ConfigChange, ...]: ...
    def fingerprint(self) -> str: ...


# 1. Study and its execution plan. Runtime bindings are not persisted here.
@dataclass(frozen=True, kw_only=True)
class NativeConfig:
    schema_ref: VersionRef  # required revision; identifies the integration dialect
    values: Record = ...  # explicit native options, never silently discarded

@dataclass(frozen=True, kw_only=True)
class VerifierSpec:
    id: str  # stable native-grade channel within the study
    implementation: VersionRef
    reference_columns: Mapping[str, tuple[str, ...]] = ...
    params: Record = ...
    native: NativeConfig | None = ...
    budget: Budget | None = ...

@dataclass(frozen=True, kw_only=True)
class NativeJobConfig:
    id: str
    backend: VersionRef
    candidate_ids: tuple[str, ...]  # may include both arms of a paired study
    native: NativeConfig | None = ...
    verifiers: tuple[VerifierSpec, ...] = ...

@dataclass(frozen=True, kw_only=True)
class Budget:
    wall_time_s: float
    max_tokens: int | None = ...
    max_cost_usd: Decimal | None = ...

@dataclass(frozen=True, kw_only=True)
class ExecutionPolicy:
    budget: Budget
    cost_scope: tuple[CostCategory, ...] = ...  # ("model",); all calls in the harness
    repetitions: int = ...
    max_concurrency: int = ...
    order_seed: int | None = ...
    infrastructure_retries: int = ...
    native_jobs: tuple[NativeJobConfig, ...] = ...

@dataclass(frozen=True, kw_only=True)
class Contrast:
    id: str
    baseline: str
    challenger: str
    metrics: tuple[str, ...]
    expected_changes: tuple[str, ...] = ...

@dataclass(frozen=True, kw_only=True)
class Assignment:
    id: str
    candidate_id: str
    candidate_fingerprint: str
    unit: Key
    repetition: int

@dataclass(frozen=True, kw_only=True)
class PlannedJob:
    id: str  # stable plan identity, not a native invocation ID
    config: NativeJobConfig
    assignment_ids: tuple[str, ...]

@dataclass(frozen=True, kw_only=True)
class DatasetInfo:
    id: str
    fingerprint: str
    unit_key: tuple[str, ...]
    cluster_by: tuple[str, ...]
    units: DataTable  # key and grouping columns; no private references

@dataclass(frozen=True, kw_only=True)
class RunPlan:
    study_id: str
    study_fingerprint: str
    project_id: str
    dataset: DatasetInfo
    candidates: Mapping[str, Candidate]
    assignments: tuple[Assignment, ...]
    execution: ExecutionPolicy
    contrasts: tuple[Contrast, ...] = ...
    environment: ComponentSpec | None = ...
    native_jobs: tuple[PlannedJob, ...] = ...

@dataclass(frozen=True, kw_only=True)
class Study:
    id: str
    project_id: str
    question: str
    dataset: EvalDataset
    candidates: Mapping[str, Candidate]
    suite: EvalSuite
    execution: ExecutionPolicy
    contrasts: tuple[Contrast, ...] = ...
    environment: ComponentSpec | None = ...
    def validate(self) -> ValidationReport: ...
    def fingerprint(self) -> str: ...
    def plan(self) -> RunPlan: ...
    def run(
        self, *, backends: Mapping[str, BackendAdapter] | None = ...,
        job_backends: Mapping[str, NativeJobAdapter] | None = ...,
    ) -> RunSet: ...
    def evaluate(
        self, *, backends: Mapping[str, BackendAdapter] | None = ...,
        evaluators: Mapping[str, MetricEvaluator],
        reducers: Mapping[str, SummaryReducer] | None = ...,
        job_backends: Mapping[str, NativeJobAdapter] | None = ...,
        batch_evaluators: Mapping[str, BatchMetricEvaluator] | None = ...,
    ) -> EvaluationResult: ...
    def save(self, path: Path) -> None: ...
    @classmethod
    def load(cls, path: Path) -> Study: ...


# 4. RunSet: assignments plus facts, including missing/failed work.
RunStatus: TypeAlias = Literal["not_run", "unobserved", "running", "awaiting_input", "paused", "completed", "agent_error", "timed_out", "infrastructure_error", "cancelled"]

@dataclass(frozen=True, kw_only=True)
class ErrorRecord:
    code: str
    message: str
    evidence: tuple[EvidenceRef, ...] = ...

@dataclass(frozen=True, kw_only=True)
class Resources:
    cost_usd: Observation[Decimal]
    cost_scope: tuple[CostCategory, ...]  # shared agent scope in a RunSet; grader scope is separate
    input_tokens: Observation[int]
    output_tokens: Observation[int]
    human_minutes: Observation[float]

@dataclass(frozen=True, kw_only=True)
class Execution:
    id: str
    slot: str  # e.g. "main", "snapshot-V", "snapshot-P"
    retry_index: int
    parent_id: str | None
    status: RunStatus
    started_at: datetime | None
    ended_at: datetime | None
    effective_config: Observation[Record]
    resources: Resources  # exclusive charges; never includes child charges
    role: Literal["main", "attempt", "review", "selection", "subagent", "tool", "other"] = ...
    native_refs: Mapping[str, str] = ...  # native run/thread/checkpoint IDs or URIs
    error: ErrorRecord | None = ...

@dataclass(frozen=True, kw_only=True)
class Event:
    id: str
    execution_id: str
    kind: str
    at: datetime | None
    fields: Record
    inputs: tuple[EvidenceRef, ...] = ...
    outputs: tuple[EvidenceRef, ...] = ...
    source: EvidenceRef | None = ...  # location in the unmodified native trace

@dataclass(frozen=True, kw_only=True)
class Run:
    id: str
    assignment_id: str
    status: RunStatus
    cost_scope: tuple[CostCategory, ...]  # preserved even on an empty placeholder
    output: JSONValue
    output_state: Literal["available", "unavailable", "unknown"]
    artifacts: Mapping[str, ArtifactRef]
    executions: tuple[Execution, ...]
    started_at: datetime | None
    ended_at: datetime | None
    environment: Observation[Record]
    execution_inventory_complete: Observation[bool]
    output_sources: tuple[str, ...] = ...  # IDs of executions producing delivered output
    native_refs: Mapping[str, str] = ...
    events: tuple[Event, ...] = ...
    error: ErrorRecord | None = ...
    job_id: str | None = ...  # capture-local NativeJobRecord.id; None for direct calls
    def resources(self) -> Resources: ...
    def duration_s(self) -> Observation[float]: ...

@dataclass(frozen=True, kw_only=True)
class Coverage:
    planned: int
    completed: int
    failed: int
    unavailable: int
    pending: int
    resource_unknown: int

@dataclass(frozen=True, kw_only=True)
class RunSet:
    id: str
    plan: RunPlan
    runs: tuple[Run, ...]
    grading_inventory_complete: Observation[bool]
    importer: VersionRef | None = ...
    import_source: ArtifactRef | None = ...
    native_jobs: tuple[NativeJobRecord, ...] = ...
    native_grades: tuple[NativeGradeBundle, ...] = ...
    projections: tuple[ProjectionReport, ...] = ...
    schema_version: str = ...
    def validate(self) -> ValidationReport: ...
    def get(self, run_id: str) -> Run: ...
    def for_candidate(self, candidate_id: str) -> tuple[Run, ...]: ...
    def coverage(self, candidate_id: str | None = ...) -> Coverage: ...
    def rows(self, kind: Literal["runs", "executions", "events"]) -> tuple[Record, ...]: ...
    def save(self, path: Path) -> None: ...
    @classmethod
    def load(cls, path: Path) -> RunSet: ...
    @classmethod
    def import_from(cls, source: Path, *, plan: RunPlan, importer: RunImporter) -> RunSet: ...


# 5. EvalSuite: one unambiguous source for each metric.
@dataclass(frozen=True, kw_only=True)
class EvaluatorSource:
    ref: VersionRef
    mode: Literal["single", "batch"] = ...  # single
    kind: Literal["evaluator"] = ...

@dataclass(frozen=True, kw_only=True)
class NativeGradeSource:
    channel: str
    native_metric: str
    evaluator: VersionRef | None = ...
    activity_id: str | None = ...  # disambiguate a selected historical grading pass
    kind: Literal["native_grade"] = ...

MetricSource: TypeAlias = EvaluatorSource | NativeGradeSource

@dataclass(frozen=True, kw_only=True)
class MetricSpec:
    id: str
    source: MetricSource
    output_type: Literal["bool", "int", "float", "decimal", "text"]
    role: Literal["quality", "resource", "diagnostic"]
    params: Mapping[str, JSONValue] = ...
    depends_on: tuple[str, ...] = ...  # reusable measurements, evaluated once per run
    unit: str | None = ...

@dataclass(frozen=True, kw_only=True)
class ScoreTerm:
    metric: str
    points: float

@dataclass(frozen=True, kw_only=True)
class Rubric:
    terms: tuple[ScoreTerm, ...]
    @property
    def max_score(self) -> float: ...

@dataclass(frozen=True, kw_only=True)
class Threshold:
    metric: str
    op: Literal["==", "!=", ">", ">=", "<", "<="]
    value: MetricValue

@dataclass(frozen=True, kw_only=True)
class AcceptanceRule:
    all_of: tuple[Threshold, ...]

@dataclass(frozen=True, kw_only=True)
class SummarySpec:
    id: str
    metric: str
    reducer: Literal["mean", "sum", "min", "max", "p95", "rate"] | VersionRef
    params: Mapping[str, JSONValue] = ...

@dataclass(frozen=True, kw_only=True)
class EvalSuite:
    id: str
    version: str
    metrics: tuple[MetricSpec, ...]
    summaries: tuple[SummarySpec, ...]
    acceptance: AcceptanceRule | None = ...
    rubric: Rubric | None = ...
    evaluation_cost_scope: tuple[CostCategory, ...] = ...  # ("model",)
    def validate(self) -> ValidationReport: ...
    def fingerprint(self) -> str: ...
    def evaluate(
        self, *, dataset: EvalDataset, runs: RunSet,
        evaluators: Mapping[str, MetricEvaluator],
        reducers: Mapping[str, SummaryReducer] | None = ...,
        batch_evaluators: Mapping[str, BatchMetricEvaluator] | None = ...,
    ) -> EvaluationResult: ...


# 6. EvaluationResult and the data it returns.
@dataclass(frozen=True, kw_only=True)
class Measurement:
    run_id: str
    metric: str
    value: MetricValue | None
    status: Literal["ok", "missing", "error", "not_applicable"]
    basis: Literal["observed", "estimated"] | None
    reason: str
    evidence: tuple[EvidenceRef, ...] = ...
    key: Key | None = ...  # None = one task value; otherwise diagnostic row grain
    activity_ids: tuple[str, ...] = ...  # references; resources live on activities

@dataclass(frozen=True, kw_only=True)
class ScoreContribution:
    metric: str
    earned: float | None
    possible: float
    basis: Literal["observed", "estimated"] | None
    reason: str
    evidence: tuple[EvidenceRef, ...]

Decision: TypeAlias = Literal["pass", "fail", "unknown"]

@dataclass(frozen=True, kw_only=True)
class GateResult:
    threshold: Threshold
    decision: Decision
    basis: Literal["observed", "estimated"] | None
    reason: str

@dataclass(frozen=True, kw_only=True)
class TaskScore:
    run_id: str
    score: Observation[float]
    contributions: tuple[ScoreContribution, ...]
    acceptance: Decision
    acceptance_basis: Literal["observed", "estimated"] | None
    gates: tuple[GateResult, ...]
    reason: str

@dataclass(frozen=True, kw_only=True)
class CandidateSummary:
    candidate_id: str
    summary_id: str
    value: Observation[MetricValue]
    planned: int
    observed: int
    missing: int
    errors: int
    not_applicable: int
    included_run_ids: tuple[str, ...]
    exclusions: Mapping[str, str]  # excluded run ID -> reason, including missingness
    reason: str
    available_value: MetricValue | None = ...  # descriptive only; cannot drive selection

@dataclass(frozen=True, kw_only=True)
class ComparisonRow:
    metric: str
    baseline: Observation[MetricValue]
    challenger: Observation[MetricValue]
    delta: Observation[float]
    interval: tuple[float, float] | None = ...
    interval_note: str | None = ...

@dataclass(frozen=True, kw_only=True)
class Comparison:
    baseline_id: str
    challenger_id: str
    changes: tuple[ConfigChange, ...]
    rows: tuple[ComparisonRow, ...]
    task_count: int
    cluster_count: int
    limitations: tuple[str, ...]

@dataclass(frozen=True, kw_only=True)
class ObjectiveTerm:
    metric: str  # summary ID, not a per-run metric
    direction: Literal["minimize", "maximize"]
    weight: float = ...
    bounds: tuple[float, float] | None = ...

@dataclass(frozen=True, kw_only=True)
class SelectionPolicy:
    id: str
    objectives: tuple[ObjectiveTerm, ...]
    requirements: tuple[Threshold, ...] = ...  # thresholds on summary IDs
    mode: Literal["lexicographic", "weighted", "pareto"] = ...
    allow_estimates: bool = ...

@dataclass(frozen=True, kw_only=True)
class SelectionRow:
    candidate_id: str
    eligibility: Decision
    reasons: tuple[str, ...]
    preference_score: float | None
    rank: int | None

@dataclass(frozen=True, kw_only=True)
class Selection:
    result_id: str
    policy: SelectionPolicy
    status: Literal["selected", "tie", "frontier", "none_eligible"]
    selected_ids: tuple[str, ...]
    rows: tuple[SelectionRow, ...]

@dataclass(frozen=True, kw_only=True)
class Explanation:
    run_id: str
    score: TaskScore
    measurements: tuple[Measurement, ...]
    evidence: tuple[EvidenceRef, ...]

@dataclass(frozen=True, kw_only=True)
class EvaluationResult:
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
    activities: tuple[EvaluationActivity, ...] = ...
    performed_activity_ids: tuple[str, ...] = ...
    schema_version: str = ...
    def summary(self) -> tuple[CandidateSummary, ...]: ...
    def incremental_evaluation_resources(self) -> Resources: ...
    def explain(self, run_id: str) -> Explanation: ...
    def compare(
        self, baseline: str, challenger: str, *, metrics: Sequence[str],
        confidence: float | None = ...,
    ) -> Comparison: ...
    def select(self, policy: SelectionPolicy) -> Selection: ...
    def report(self, path: Path, *, selection: Selection | None = ...) -> Path: ...
    def save(self, path: Path) -> None: ...
    @classmethod
    def load(cls, path: Path) -> EvaluationResult: ...


# Runtime extension seams: these are not stored inside Candidate/Study JSON.
@dataclass(frozen=True, kw_only=True)
class BackendCapabilities:
    wall_time_limit: bool  # confirmed stop, not just stopping the caller's wait
    token_limit: bool
    cost_limit: bool  # hard ceiling over declared scope, including review/selection
    reset_state: bool  # ability; each run still needs evidence of declared initial state

@dataclass(frozen=True, kw_only=True)
class NativeJobCapabilities:
    limits: BackendCapabilities
    private_verifier_channel: bool
    fixed_repetitions: bool

@dataclass(frozen=True, kw_only=True)
class RunRequest:
    run_id: str
    assignment: Assignment
    candidate: Candidate
    input: AgentInput
    policy: ExecutionPolicy
    environment: ComponentSpec | None

@dataclass(frozen=True, kw_only=True)
class VerifierInput:
    run_id: str
    verifier_id: str
    references: Mapping[str, tuple[Record, ...]]

@dataclass(frozen=True, kw_only=True)
class NativeJobRequest:
    job_id: str
    run_set_id: str
    planned_job_id: str
    config: NativeJobConfig
    requests: tuple[RunRequest, ...]
    verifier_inputs: tuple[VerifierInput, ...] = ...

@dataclass(frozen=True, kw_only=True)
class NativeRunLink:
    run_id: str
    assignment_id: str
    native_refs: Mapping[str, str]  # scoped by the enclosing native job

@dataclass(frozen=True, kw_only=True)
class ProjectionReport:
    mapper: VersionRef
    source_format: VersionRef
    sources: tuple[ArtifactRef, ...]
    omitted_fields: tuple[str, ...] = ...  # retained in raw source, not projected
    issues: tuple[ValidationIssue, ...] = ...

@dataclass(frozen=True, kw_only=True)
class NativeJobRecord:
    id: str
    planned_job_id: str
    backend: VersionRef
    assignment_ids: tuple[str, ...]
    status: Literal["running", "completed", "partial", "error", "cancelled", "unknown"]
    started_at: datetime | None
    ended_at: datetime | None
    native_refs: Mapping[str, str] = ...
    links: tuple[NativeRunLink, ...] = ...
    artifacts: Mapping[str, ArtifactRef] = ...
    effective_config: Observation[Record] | None = ...
    error: ErrorRecord | None = ...

@dataclass(frozen=True, kw_only=True)
class NativeJobOutput:
    job: NativeJobRecord
    runs: tuple[Run, ...]
    grading_inventory_complete: Observation[bool]
    native_grades: tuple[NativeGradeBundle, ...] = ...
    projections: tuple[ProjectionReport, ...] = ...

class NativeJobRecorder(Protocol):
    def record_job(self, job: NativeJobRecord) -> None: ...
    def record_run(self, run: Run) -> None: ...
    def record_grade_bundle(self, bundle: NativeGradeBundle) -> None: ...

class NativeJobAdapter(Protocol):
    @property
    def ref(self) -> VersionRef: ...
    def capabilities(self) -> NativeJobCapabilities: ...
    async def run_job(
        self, request: NativeJobRequest, *, recorder: NativeJobRecorder,
    ) -> NativeJobOutput: ...

class RunRecorder(Protocol):
    def record_execution(self, execution: Execution) -> None: ...
    def record_event(self, event: Event) -> None: ...
    def record_artifact(self, name: str, artifact: ArtifactRef) -> None: ...

class BackendAdapter(Protocol):
    @property
    def ref(self) -> VersionRef: ...
    def capabilities(self) -> BackendCapabilities: ...
    def run(self, request: RunRequest, *, recorder: RunRecorder) -> Run: ...

class RunImporter(Protocol):
    @property
    def ref(self) -> VersionRef: ...
    def read(self, source: Path, *, plan: RunPlan) -> ImportedCapture: ...

@dataclass(frozen=True, kw_only=True)
class ImportedCapture:
    runs: tuple[Run, ...]
    grading_inventory_complete: Observation[bool]
    native_jobs: tuple[NativeJobRecord, ...] = ...
    native_grades: tuple[NativeGradeBundle, ...] = ...
    projections: tuple[ProjectionReport, ...] = ...

@dataclass(frozen=True, kw_only=True)
class EvaluationContext:
    unit: Key
    inputs: AgentInput
    references: Mapping[str, tuple[Record, ...]]
    measurements: Mapping[str, Measurement] = ...  # this run's declared dependencies only
    native_job: NativeJobRecord | None = ...
    evaluation_cost_scope: tuple[CostCategory, ...] = ...  # engine copies suite scope

@dataclass(frozen=True, kw_only=True)
class MetricOutput:
    task: Measurement
    details: tuple[Measurement, ...] = ...
    evaluation_resources: Resources  # this fresh single callback only; engine creates activity

@dataclass(frozen=True, kw_only=True)
class EvaluationActivity:
    id: str
    evaluator: VersionRef
    config_fingerprint: str | None  # required for fresh grading; imported unknown is None
    run_ids: tuple[str, ...]
    status: Literal["completed", "partial", "error"]
    phase: Literal["native_verifier", "post_run"]
    resources: Resources  # grader-only, counted once per activity ID
    started_at: datetime | None = ...
    ended_at: datetime | None = ...
    native_refs: Mapping[str, str] = ...
    artifacts: Mapping[str, ArtifactRef] = ...
    error: ErrorRecord | None = ...

@dataclass(frozen=True, kw_only=True)
class NativeGrade:
    id: str
    run_id: str
    native_metric: str
    activity_id: str
    value: MetricValue | None
    status: Literal["ok", "missing", "error", "not_applicable"]
    basis: Literal["observed", "estimated"] | None
    reason: str
    evidence: tuple[EvidenceRef, ...] = ...
    key: Key | None = ...

@dataclass(frozen=True, kw_only=True)
class NativeGradeBundle:
    id: str
    channel: str  # stable selector, distinct from this bundle's capture ID
    projection: ProjectionReport
    grades: tuple[NativeGrade, ...]
    activities: tuple[EvaluationActivity, ...]

@dataclass(frozen=True, kw_only=True)
class EvaluationItem:
    run: Run
    context: EvaluationContext

@dataclass(frozen=True, kw_only=True)
class BatchMetricRequest:
    id: str  # fresh grading activity ID, assigned by the engine
    config_fingerprint: str
    metric: MetricSpec
    items: tuple[EvaluationItem, ...]

@dataclass(frozen=True, kw_only=True)
class BatchMetricOutput:
    measurements: tuple[Measurement, ...]
    activity: EvaluationActivity  # one batch invocation; not copied into every row

class BatchMetricEvaluator(Protocol):
    @property
    def ref(self) -> VersionRef: ...
    async def compute_batch(self, request: BatchMetricRequest) -> BatchMetricOutput: ...

class MetricEvaluator(Protocol):
    @property
    def ref(self) -> VersionRef: ...
    def compute(self, spec: MetricSpec, context: EvaluationContext, run: Run) -> MetricOutput: ...

class SummaryReducer(Protocol):
    @property
    def ref(self) -> VersionRef: ...
    def reduce(
        self, spec: SummarySpec, *, candidate_id: str,
        assignments: tuple[Assignment, ...], runs: tuple[Run, ...],
        measurements: tuple[Measurement, ...],
        task_scores: tuple[TaskScore, ...],
    ) -> CandidateSummary: ...


# Configured runtime facade over the six serializable domain objects.
# Construction binds dependencies only; eval is the explicit execution boundary.
class EvaluationPipeline:
    def __init__(
        self, *, study: Study, evaluators: Mapping[str, MetricEvaluator],
        backends: Mapping[str, BackendAdapter] | None = ...,
        reducers: Mapping[str, SummaryReducer] | None = ...,
        job_backends: Mapping[str, NativeJobAdapter] | None = ...,
        batch_evaluators: Mapping[str, BatchMetricEvaluator] | None = ...,
    ) -> None: ...
    @property
    def study(self) -> Study: ...
    def eval(self, *, runs: RunSet | None = ...) -> EvaluationResult: ...
    async def aeval(self, *, runs: RunSet | None = ...) -> EvaluationResult: ...

def evaluate(
    candidate: Candidate, data: EvalDataset, metrics: Sequence[MetricSpec], *,
    backend: BackendAdapter, evaluators: Mapping[str, MetricEvaluator],
    execution: ExecutionPolicy,
    reducers: Mapping[str, SummaryReducer] | None = ...,
    rubric: Rubric | None = ...,
    acceptance: AcceptanceRule | None = ...,
    summaries: Sequence[SummarySpec] = ...,
) -> EvaluationResult: ...
