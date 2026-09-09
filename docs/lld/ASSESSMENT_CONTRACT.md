# Unified assessment contract: next-extension LLD

**Initial implementation: package 0.5, assessment format 0.1.** See the
[implementation guide and boundaries](../implementation/unified_assessment.md). This page supplies
the common record, identity and boundary decisions for the
[accepted unified flow](../UNIFIED_ASSESSMENT_FLOW.md). It governs the new
sections of the eight module LLDs. The existing
[v0.4 contract](../DATA_CONTRACT.md) and
[typed API](../contracts/agent_eval_flow.pyi) retain their meaning and wire
format. Names and signatures here specify the extension; exact typed fields live in
`src/agent_eval_flow/objects/assessment.py`.

## Scope and public entry point

The extension adds an outer `AssessmentPipeline` and `AssessmentResult`. It
composes the existing behavioral `EvaluationPipeline` with configuration
capture and evaluation; it does not rename `EvaluationResult` or make its
required `RunSet` nullable. Records use the same immutable Pydantic validation
and explicit reference discipline as v0.4.

```python
class AssessmentPipeline:
    def __init__(
        self, *, plan: AssessmentPlan,
        collectors: Mapping[str, ConfigurationCollector],
        configuration_evaluators: Mapping[str, ConfigurationEvaluator],
        references: Mapping[str, DataTable] | None = None,
        snapshot_binders: Mapping[str, SnapshotBinder] | None = None,
        backends=None, job_backends=None, evaluators=None,
        batch_evaluators=None, reducers=None,
    ): ...

    def eval(
        self, *, runs: RunSet | None = None,
        configuration: Mapping[str, ConfigurationCapture] | None = None,
    ) -> AssessmentResult: ...

    async def aeval(
        self, *, runs: RunSet | None = None,
        configuration: Mapping[str, ConfigurationCapture] | None = None,
    ) -> AssessmentResult: ...
```

Constructor registries are copied without invoking them. Collector/evaluator
maps may be empty when their paths need no implementations. The existing
behavioral registry types remain unchanged. Sync calls inside an active event
loop fail before work, as in v0.4.

| Plan field | Type/default and invariant |
| --- | --- |
| `id`, `project_id` | Nonempty strings. |
| `candidates` | Nonempty `Mapping[str, Candidate]`; keys equal candidate IDs. |
| `configuration` | `Mapping[str, ConfigurationSpec] = {}` keyed by selected candidate IDs. |
| `behavior` | `Study | None = None`; its project agrees and every candidate resolves to an identical definition in `candidates`. A subset is allowed and visible in branch coverage. |
| `checks` | `tuple[CheckSpec, ...] = ()`; unique IDs, valid configured candidate targets and acyclic dependencies. |
| `gates` | `tuple[GateSpec, ...] = ()`; explicit pre-execution requirements on configuration checks. |
| `reference_sets` | `Mapping[str, VersionRef] = {}`; versioned candidate-reference table bindings, distinct from behavioral task references. |
| `max_concurrency` | Positive integer, default 1, bounding concurrent configuration collector/evaluator invocations. Behavioral concurrency remains owned by `Study.execution` and its native runtime. |

At least one branch is configured. A configuration plan can collect evidence
without checks, but that produces no evaluation conclusion. `runs` requires a
behavioral plan. A supplied configuration map must match the configuration
candidate keys exactly; mismatches fail preflight rather than silently launch
missing collectors. Supplied capture suppresses acquisition only in that
branch. Selected checks still run over retained evidence. There is no implicit
assessment cache or execution of missing tasks on reuse.

## Configuration capture and snapshot binding

| Record | Fields and meaning |
| --- | --- |
| `ConfigurationSpec` | `collector: VersionRef` with revision; `params: Record = {}` in the collector's validated dialect; `snapshot_requirement: Literal['verified', 'record_only'] = 'verified'` for binding fresh behavior to this configuration. |
| `SnapshotEntry` | `id`, logical `path`, `source_tool`, `scope`, `role`, and `artifact: ArtifactRef`; resolved external/user-level files have explicit logical mounts. File content hashes, not mutable paths, identify bytes. |
| `CandidateSnapshot` | `candidate_id`, `candidate_fingerprint`, ordered `entries`, collector revision/parameter fingerprint, `context: Record`, `inventory_complete: Observation[bool]`, omissions and computed `fingerprint`. |
| `ConfigurationCapture` | `id`, `candidate_id`, `candidate_fingerprint`, `snapshot: CandidateSnapshot | None`, `status`, collection activity records, raw `artifacts`, and `error: ErrorRecord | None`. `status` is completed/partial/error/cancelled/unknown. Failed discovery may have no snapshot. |
| `SnapshotBinding` | Candidate fingerprint, snapshot fingerprint, applicable run/job IDs, `status: verified/mismatch/unknown`, reason and evidence. Describes actual runtime materialization or inspection; a declaration alone is unknown. |

`context` identifies behavior-relevant discovery context such as source-tool
version, user/project scope, exclusions and resolved dependency mapping. Secret
values and live clients are not configuration identity fields. Omitted or
unreadable files remain explicit. Partial snapshot fingerprints identify that
partial inventory and must not be advertised as a complete configuration hash.

Freezing precedes the parallel work: collect an immutable configuration image,
then inspect it while the runtime executes against a verified materialization
or supplies equivalent capture proof. The collector does not execute target
hooks or apply scanner fixes. Adapter-specific bindings control runtime setup;
the core does not assume a copied directory represents all effective settings.

With `snapshot_requirement='verified'`, a fresh combined branch without a
compatible `SnapshotBinder` fails capability preflight. A materialization failure
after preparation leaves affected behavior unstarted with explicit reasons.
`record_only` permits independent observation but reports unknown/mismatched
parity and prevents claiming the branches describe a verified identical setup.
Imported or saved runs are never rerun to create binding evidence; absent proof
remains unknown, including when the configured requirement is verified.

`SnapshotBinder` is a separate preparation capability, not a new method on
v0.4 `BackendAdapter` or `NativeJobAdapter`. Its versioned binding identifies
the exact target backend revision, and its side-effect-free capability metadata
declares supported configuration scopes. `SnapshotBindRequest` contains the
complete direct/native dispatch unit, its candidate snapshot map and parity
requirement. `SnapshotSession` is an invocation-local async context containing
binding evidence and staged delegates satisfying the original backend/job
protocols. Its clients, mounts and cleanup handles are never serialized. The
session must stay alive through native execution and receipt retention. See
[execution](execution/README.md) and [adapters](adapters/README.md) for ownership.

```python
class SnapshotBinder(Protocol):
    @property
    def ref(self) -> VersionRef: ...
    def capabilities(self) -> SnapshotCapabilities: ...
    async def prepare(self, request: SnapshotBindRequest) -> SnapshotSession: ...
```

`SnapshotCapabilities` declares the supported backend full VersionRefs, source
scopes, direct/native-unit support and whether verified parity is supported.
Registry keys resolve the target backend name, with full revision validation.
Saved runs require no binder. A missing binder in record-only mode leaves parity
unknown, while a fresh verified combined branch requires a compatible binder.

## Subjects and findings

`SubjectRef` is a discriminated union, not a record with every field optional:

| `kind` | Required identity |
| --- | --- |
| `candidate` | Candidate ID and definition fingerprint; snapshot fingerprint when available. |
| `component` | Candidate subject plus a `SnapshotEntry.id` or normalized provider component locator; unresolved locators retain a mapping issue and do not fabricate an entry. |
| `run` | Candidate ID/fingerprint, `run_set_id`, `run_id` and `assignment_id`, resolving to the retained behavioral result. |
| `study` | Study identity/fingerprint and an explicit population definition/fingerprint. Reserved for later evaluator selectors; no implicit all-runs population. |

The first extension dispatches new configuration checks once per candidate.
Individual findings may name component subjects. Existing run metrics appear
through behavioral projections. Arbitrary component or study-wide evaluator
dispatch needs a later selector/output contract and is rejected if requested
through the initial candidate-check interface.

| Record | Required fields and validation |
| --- | --- |
| `Finding` | `id`, `subject`, provider `rule: VersionRef`, `message`, `severity` (error/warning/info), `basis` (declared/observed/estimated/inferred), `evidence: tuple[EvidenceRef, ...]`, optional suggestion and `evidence_tier: str | None`; provider tier/calibration metadata remains attributed metadata. |
| `AssessmentValue` | `id`, typed `value: MetricValue | None`, `status` (ok/missing/error/not_applicable), `basis` (observed/estimated/inferred or null), `reason`, evidence and optional unit. Unknown/error values are null with a reason; no coercion across bool/int/decimal. |
| `Assessment` | `id`, `subject`, `origin`, `status` (ok/missing/error/not_applicable), `conclusion: pass/fail/unknown`, `values`, `findings`, evidence, reason and `activity_refs`. Collections may be empty; a numerical score is not required. |
| `AssessmentOrigin` | Either a configuration `request_id`, complete CheckSpec fingerprint and evaluator VersionRef, or a behavioral measurement locator `(run_id, metric, key)` inside the retained `EvaluationResult`. |

A configuration conclusion is limited to the declared check and its covered
inputs. Complete zero-findings output can satisfy a declared no-findings check;
it is not a general safety claim. Finding severity never determines invocation
success or decision eligibility by itself. Observing a textual credential
reference and inferring an exfiltration risk must be separate claims.

Behavioral projections retain the original measurement's value, status,
observed/estimated basis, evidence and activity IDs. A numeric measurement has
no automatic acceptance threshold: its projected conclusion is unknown unless
an explicit originating gate establishes one. Projections are views with
validated source locators; they do not invoke evaluators or create activities.

## Checks, reference projection and coverage

| Record | Fields and constraints |
| --- | --- |
| `CheckSpec` | `id`, `evaluator: VersionRef`, `candidate_ids`, `params: Record`, `depends_on: tuple[str, ...]`, `required_roles: tuple[str, ...]`, `reference_columns: Mapping[str, tuple[str, ...]]`, `output_types: Mapping[str, output_type]`, optional provider rule-selection metadata. Empty reference selection exposes no reference data. |
| `AssessmentRequest` | Fresh `id`, check definition/fingerprint, candidate subject, compatible `ConfigurationCapture`, explicitly projected references with `reference_sets` revisions and `reference_keys`, same-subject prerequisite assessments, allocated activity identity and computed input fingerprint. |
| `AssessmentOutput` | Returned assessment, invocation activity, check coverage and source artifacts. Returned identities must match the allocated request/subject; output values match declared types. |
| `CheckCoverage` | Requested `(candidate_id, check_id)` inventory, status completed/partial/error/blocked/not_applicable/unknown, reason, optional provider-rule inventory and per-rule/target statuses. |

`ConfigurationCollectRequest` carries the allocated request/activity identity,
candidate definition and ConfigurationSpec. Its collector receives only the
explicit source binding, not task-private references. Both provider protocols
are asynchronous; a synchronous CLI/file implementation uses the existing
worker/thread/process facilities at its adapter boundary.

```python
class ConfigurationCollector(Protocol):
    @property
    def ref(self) -> VersionRef: ...
    async def collect(
        self, request: ConfigurationCollectRequest, *, recorder: AssessmentRecorder,
    ) -> ConfigurationCapture: ...

class ConfigurationEvaluator(Protocol):
    @property
    def ref(self) -> VersionRef: ...
    async def evaluate(
        self, request: AssessmentRequest, *, recorder: AssessmentRecorder,
    ) -> AssessmentOutput: ...

class AssessmentRecorder(Protocol):
    def record_artifact(self, name: str, artifact: ArtifactRef) -> None: ...
    def record_activity(
        self, activity: AssessmentActivity, *, final: bool = False,
    ) -> None: ...
    def record_capture(
        self, capture: ConfigurationCapture, *, final: bool = False,
    ) -> None: ...
```

Recorders are invocation-owned sinks for acknowledged progress. Artifact names
are local aliases, not paths to read. Activities and partial collector captures
must belong to the allocated request and candidate. Within one invocation,
nonfinal snapshots may append receipts, resolve unknown observations and revise
attributed estimates without rewriting established source facts. Their stable
ownership and input identity cannot change. A partial snapshot fingerprint may
change as inventory grows; consumers receive only the finalized capture.

Final returns or `final=True` close the corresponding identity and replace its
compatible partial state. Later terminal duplicates must be equal; contradictory
terminal records or post-terminal rewrites are capture errors. Resource fields
in progress snapshots are cumulative observations, not incremental charges:
retain the final compatible value rather than sum updates. Callbacks do not
create extra invocations or costs.
An evaluator cannot use `record_capture` to overwrite its input collection.
This receipt channel preserves available evidence on failure/cancellation; it
does not add automatic disk checkpoints or promise a successful final result.

The collection input fingerprint includes candidate, collector revision and
collection parameters. The downstream `snapshot_requirement` does not change
source collection identity; callers can change that policy while reusing the
same retained evidence.

Candidate reference tables are supplied through the explicit `references`
binding; the plan contains versioned reference-set identities, not credentials
or clients. The reference binding must provide
candidate-keyed tables with an unambiguous `candidate_id`; otherwise a check
requiring those tables fails configuration validation. Behavioral task answer
tables do not implicitly become candidate references. Projection retains only
requested columns and join identities, and fingerprints the exact projected
values. These fields belong to the proposed API, not v0.4 `EvaluationContext`.
The behavioral path continues using only its dataset's established projections.

Expand candidate/check inventory before invoking extensions. Check
dependencies are explicit and same-candidate in the first version; a check
cannot depend on behavioral output while also gating that output's execution.
Reject cycles before acquisition or agent work. Failed prerequisites produce
blocked/unknown dependent cells with no invocation, not a successful empty list.

Report-only integrations may lack rule coverage. Preserve what they provide
and mark the rest unknown. A scanner exit status and empty findings cannot
establish complete coverage when required rule/input inventory is unavailable.
Suppressions and waivers remain recorded decisions alongside retained provider
evidence. Missing raw suppressed findings are disclosed, never reconstructed.

## Activity ownership and failures

`ActivityRef(namespace='assessment' | 'behavior', id=...)` identifies exactly
one invocation within the containing result. Assessment activity IDs cover new
collection and configuration evaluation only. Behavior references resolve to
the existing result's `EvaluationActivity` records; run execution resources
remain on the `RunSet`.

`AssessmentActivity` contains `id`, versioned implementation, parameter/input
fingerprint, subject references, `phase: collection | configuration_evaluation`,
`status: completed | partial | error | cancelled | unknown`, UTC timestamps,
`Resources`, resource-inventory completeness, artifacts and optional error.
An unstarted operation has coverage/branch records but no invented invocation
cost. Conflicting terminal records under the same namespaced ID are capture
errors; acknowledged nonfinal progress follows the recorder rules above.

`BranchOutcome` records branch kind, affected candidate IDs, status
completed/partial/error/cancelled/not_run/unknown, reason, source artifacts,
optional error and gate decisions. It preserves acquisition or runtime failures
even when no valid child payload exists. Structurally invalid adapter returns
become branch capture errors with source receipts; valid sibling results can
still be retained. A structurally invalid assembled result raises
`CaptureValidationError` instead of returning inconsistent data.

Caller cancellation stops requested work through each adapter's lifecycle
contract; requested stop and confirmed stop remain distinct. A failed branch
does not automatically cancel its independent sibling. A dependency or explicit
caller cancellation may stop work. Unknown remote state is not not_run.

## Gates and post-assessment decisions

`ConfigurationRequirement` is a discriminated predicate:

- `conclusion_pass(check_id)` requires that check's supported pass conclusion.
- `no_findings(check_id, min_severity, allowed_tiers=None)` requires no retained
  findings meeting the chosen severity and optional explicit provider-tier
  filter. `None` intentionally considers all tiers; an unavailable tier under a
  requested filter is unknown, not excluded. Unknown coverage or omitted suppressed findings produces
  unknown unless a separately recorded, explicit waiver policy covers them.
  A policy may explicitly select advisory findings; severity alone does not
  automatically promote them into a blocking rule.
- `value(check_id, value_id, op, expected_value)` compares a named typed value
using the same strict operators as v0.4 thresholds. Numeric int/float/Decimal
operands compare without converting retained values; bool is excluded. Declared
check output types still require exact matches.

Each predicate returns pass/fail/unknown, source assessment IDs, coverage and a
reason. AND is noncompensatory: any fail means fail; otherwise any unknown means
unknown; otherwise pass. Unknown evidence cannot become a factual pass through
an operational decision to proceed.

`GateSpec` contains `id`, targeted candidate IDs, requirements and
`on_unknown: block | allow = block`. Gate check dependencies are resolved before
the affected behavioral launch. A failed gate blocks; an unknown gate follows
the recorded policy and keeps its unknown outcome. Existing saved runs bypass
launch gates because no launch occurs; current gate assessments must not be
reported as authorization that existed at the historical execution time.

Native jobs remain atomic declared groups. A blocking candidate gate blocks
that entire not-yet-started job with reasons for all affected assignments; do
not split a paired study or relabel its candidates. Other independent jobs and
direct candidates can proceed. Proven unstarted behavioral assignments retain
`Run.status='not_run'`; configuration-only work creates none.

`AssessmentPolicy` contains `id`, `revision`,
`configuration_requirements: tuple[ConfigurationRequirement, ...]` and
`behavior_policy: SelectionPolicy | None`. Apply requirements per candidate,
combine eligibility with the behavioral policy when present, then use existing
pure ranking helpers on eligible, comparable behavioral summaries. Missing
behavior or scope/check incompatibility yields unknown, not a zero score.
Without a behavioral policy, return eligibility/explanations without inventing
a ranking. All candidates remain visible. Waivers, if enabled, are separate
versioned policy records with exact targets/reasons, not deleted source findings.

## Outer result, reuse and persistence

`AssessmentResult` contains its ID and plan, configuration captures, snapshot
bindings, requested check inventory/coverage, assessments, assessment activities,
branch outcomes, optional `behavior_result: EvaluationResult`, and distinct
`performed_activity_refs`, with `performed_inventory_complete` retaining unknown
new activity inventories even when no activity IDs were returned. An absent behavioral result is explained by the
plan and branch outcomes; it is not an empty fake `RunSet`.

The registry of assessment activities is the canonical owner at the outer
result; configuration captures retain their source collection records, and
assembly must reconcile byte-equivalent terminal same-ID copies. Behavioral activity
refs resolve only in the nested behavioral result. Validate every projection
against its source before saving/loading. Totals are sums of distinct source
activities, not the sum of every assessment's linked resources. Historical
and newly performed charges remain separate; loading performs no activity.

The explicit pure `wrap_behavior_result(existing_result, *, plan)` bridge
requires a compatible behavior-only plan containing the full Study. A saved
RunSet's DatasetInfo cannot reconstruct that definition. Wrapping retains the
old result and projections, with an empty outer performed-activity set.

New evaluator definitions require explicit reassessment of retained compatible
evidence. The initial facade always runs selected checks; it has no hidden
cache. Any later assessment-reuse API must match subject/snapshot, evidence,
evaluator/collector revisions, parameters and projected-reference fingerprints.
Saved reports alone cannot promise re-execution of checks requiring missing
source files. Changing only `AssessmentPolicy` performs no collection, grading
or runtime calls.

The proposed outer format is `agent-eval-flow-assessment`, version `0.1`, with
distinct root kinds for plan and result. This is a separate versioned format, preserving
v0.4 files. A nested behavioral payload retains its complete v0.4
envelope. `storage/assessment_codec.py` implements the new roots through a separate decoder. See
[storage LLD](storage/README.md) for atomic save/load and relocation limits.

## Acceptance scenarios for the extension

These are the acceptance requirements. The implementation guide links the
new record, orchestration, consumer, receipt and adapter regression suites.

| ID | Scenario and required result |
| --- | --- |
| UA01 | Configuration-only evaluation: no dataset/backend calls or fabricated runs; complete findings and provenance can save/load. |
| UA02 | Combined evaluation: inspection and runtime may overlap after snapshot preparation; their outputs bind to the same verified snapshot or explicitly disclose unknown/mismatch. |
| UA03 | Reference isolation: an unrelated configuration reviewer and the agent receive no private task answers; only named candidate references reach a requesting check. |
| UA04 | Empty malformed/partial scan: unknown/error coverage remains visible and cannot pass a no-findings requirement. |
| UA05 | Gate blocks a native job: all proven unstarted assignments remain not_run; the native group is not split; unrelated groups continue. |
| UA06 | Reuse saved runs/configuration: no backend/collector calls; explicit selected graders can run; branch compatibility is validated. |
| UA07 | One finding and activity linked to 100 runs: one candidate observation and one charge; behavioral activity IDs cannot collide with assessment IDs. Cumulative partial recorder updates finalize once, and only equal terminal duplicates are accepted. |
| UA08 | Branch failure/cancellation: sibling evidence survives and remote stop uncertainty is retained; invalid source identities never enter valid records. |
| UA09 | Save/load and policy change: original reports, source locators, unknowns and historical costs survive without extension calls or implicit downloads. |
| UA10 | Changed rule/reference/snapshot: stale assessments cannot be reused as if equivalent; noncomparable candidate checks are reported explicitly. |
| UA11 | Behavioral projection: values, evidence and activity refs equal the retained v0.4 source; projecting triggers no judge and adds no charge. |
| UA12 | Compatibility: all existing v0.4 roots remain valid under their existing APIs; new roots require the explicitly new format/decoder. |
