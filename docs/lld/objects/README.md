# Objects: records, validation and identities — levels 3 and 4

## Next extension: assessment records and identity

**Initial assessment implementation in package 0.5.** See the
[implemented surface and boundaries](../../implementation/unified_assessment.md). The
[shared assessment contract](../ASSESSMENT_CONTRACT.md) defines the proposed
fields and discriminated variants. It is authoritative for this section; the
v0.4 specification retained below continues to govern existing records.
`AssessmentPlan` and `AssessmentResult` are new outer roots. `Study`, `RunSet`,
`Measurement` and `EvaluationResult` keep their current required fields.

### Level 3: ownership and interfaces

| File | New responsibility |
| --- | --- |
| `assessment.py` | AssessmentPlan/Result, ConfigurationSpec/Capture, CandidateSnapshot/SnapshotEntry, SubjectRef variants, CheckSpec, Assessment/Value/Origin, Finding, CheckCoverage, SnapshotBinding, AssessmentActivity/ActivityRef, BranchOutcome, GateSpec, ConfigurationRequirement and AssessmentPolicy. Reuse existing value types rather than duplicate Resources, ArtifactRef, VersionRef or Decision. |
| `protocols.py` or the current boundary-record owner | Configuration collector/evaluator requests and outputs from the shared contract. Import no concrete scanner or runtime client. |
| `validation.py` | Cross-record plan/capture/result validation, reference projection requirements, subject/coverage/activity joins and source-projection equality. |
| `identity.py` | Canonical snapshot, check-input and assessment definition fingerprints; fresh invocation identities remain separate. |

These are proposed private file assignments within `objects`, not a requirement
to split the current `records.py` implementation as part of this extension.

```python
def validate_assessment_plan(plan: AssessmentPlan) -> ValidationReport: ...
def validate_configuration_capture(
    capture: ConfigurationCapture, *, candidate: Candidate,
    spec: ConfigurationSpec,
) -> ValidationReport: ...
def validate_assessment_result(result: AssessmentResult) -> ValidationReport: ...
def snapshot_fingerprint(snapshot: CandidateSnapshot) -> str: ...
def check_input_fingerprint(request: AssessmentRequest) -> str: ...
def validate_behavior_projection(
    assessment: Assessment, *, behavior: EvaluationResult,
) -> ValidationReport: ...
```

### Level 4: plan and subject validation

1. Validate immutable records and unique IDs. Configuration and behavior
   candidate subsets resolve to identical definitions in the outer plan; the
   behavioral project agrees. Derive enabled branches, rejecting an empty plan.
2. Expand the requested candidate/check inventory before dispatch. A check's
   candidates must have configuration plans; dependency checks cover the same
   candidate. Gates target declared behavioral candidates. Reject unsupported
   component/study dispatcher scopes and cycles rather than silently broadening
   selectors or dropping a gate.
3. Validate selected candidate-reference table names, revisions, columns and
   typed candidate join keys. Task-private tables are not implicitly available.
   Record fingerprints of projected values when preparing each request.
4. Validate SubjectRef by its discriminator. A candidate identity resolves to
   the plan; an available snapshot hash resolves to that candidate's capture.
   A run subject resolves to the nested run set, assignment and candidate.
   Component references preserve source-tool/scope/locator distinctions; an
   unresolved provider locator carries a mapping issue instead of inventing
   a captured file. Study subjects require explicit populations and are not
   initial evaluator-dispatch targets.

### Level 4: fingerprints and reconciliation

Snapshot identity hashes candidate definition, sorted entry identities and
content hashes, role/source-tool/scope metadata, normalized logical mounts,
collector revision/parameters, relevant context, omissions and completeness.
Transport/cache absolute paths and capture timestamps do not determine logical
snapshot identity. The raw source locations remain provenance. Duplicate entry
identities with conflicting bytes or roles fail capture validation.

The requested check inventory has stable plan/candidate/check keys. Each actual
invocation gets fresh request/activity IDs. Its input fingerprint includes the
candidate/snapshot identity, selected artifact hashes, complete CheckSpec,
evaluator revision, projected reference values and prerequisite assessment
identities. Unsupported or absent evidence cannot acquire a complete-content
hash by omission. A snapshot hash identifies what was captured; SnapshotBinding
separately establishes what the runtime actually used.

Reconciliation enforces one terminal coverage cell per requested candidate/check
and one configuration assessment for each completed or failed requested check.
Blocked/not-applicable cells retain reasons and may have no invocation. Missing
returns become explicit unknown coverage, never pass. Multiple findings remain
details of their one assessment. Output IDs, declared value types and subjects
must match the allocated request. Neither completion status nor severity
silently supplies an assessment conclusion.

Within one invocation, recorder snapshots retain stable ownership while receipt
inventory grows. A compatible final snapshot replaces partial progress; cumulative
resource observations are not added together. Final returns or explicit final
recorder callbacks close that identity. Only equal terminal duplicates are
accepted afterward, as defined by the shared recorder protocol.

`AssessmentActivity` IDs are unique in the assessment namespace. Equal retained
terminal collection copies reconcile to one canonical outer record; contradictory
terminal copies fail validation. Behavior activity references resolve only to the nested
EvaluationResult. Performed activity refs form a unique subset of existing
records, and an imported capture's historical collection is not performed again.
Projection origin locators resolve to existing behavioral measurements and must
preserve exact typed values, statuses, basis, evidence and activity references.

Schema-invalid provider returns remain branch errors with raw artifacts outside
the valid assessment inventory. This is distinct from accepting invalid records
into AssessmentResult. Final cross-record validation rejects corrupt joins even
when every individual constructor was valid. JSON Pointer issue paths name the
new owning root and related opaque IDs; existing error families are reused.

### Required verification

The [UA acceptance scenarios](../ASSESSMENT_CONTRACT.md#acceptance-scenarios-for-the-extension)
include no fake runs (UA01), reference isolation (UA03), incomplete coverage
(UA04), namespaced single-owned resources (UA07), failed identity reconciliation
(UA08), stale fingerprints (UA10) and exact behavioral projection (UA11).
Also check that same-named components under different tools/scopes do not
collapse, bool/int references hash differently, and malformed SubjectRef
variants cannot bypass required joins. These are future checks, not claimed
passing tests.

## Existing v0.4 behavioral specification

**Final-review proposal, contract 0.4.** The [data contract](../../DATA_CONTRACT.md)
defines public shapes; the [typed proposal](../../contracts/agent_eval_flow.pyi)
is the field inventory. This page finishes implementation ownership and behavior.
It was written after the [test checkpoint](../../../tests/ACCEPTANCE_MANIFEST.json).

## Files and dependencies

| Planned file | Owns | Runtime dependencies within objects |
| --- | --- | --- |
| `values.py` | JSON/key/metric aliases; VersionRef, ArtifactRef, EvidenceRef, Observation, NativeConfig, Resources, ErrorRecord; shared resource/inventory helpers | Pydantic, standard library |
| `dataset.py` | DataTable, EvalDataset, AgentInput, DatasetInfo; typed keyed joins and projections | values, identity, validation |
| `candidate.py` | ComponentSpec, Candidate, ConfigChange; derive/diff | values, identity, validation |
| `suite.py` | EvalSuite, MetricSpec/source variants, rubric/threshold/summary definitions | values, validation, identity |
| `study.py` | Study, Budget, ExecutionPolicy, Contrast, Assignment, RunPlan, NativeJobConfig, VerifierSpec, PlannedJob | values, dataset, candidate, suite |
| `runset.py` | RunSet, Run, Execution, Event, Coverage, NativeJobRecord/Link, ProjectionReport, EvaluationActivity, NativeGrade/Bundle | values, study |
| `result.py` | EvaluationResult, Measurement, TaskScore/contributions/gates, CandidateSummary, Comparison/Selection/Explanation and policy records | values, runset, suite |
| `protocols.py` | Public extension protocols plus RunRequest, NativeJobRequest, VerifierInput, imported/job/batch envelopes and evaluator context/output records | Records above; no concrete adapter imports |
| `validation.py` | ValidationIssue/Report and owner-specific relational validation functions | values; remaining record types through type-only annotations |
| `identity.py` | Defensive snapshots, canonical values and definition fingerprints | Standard library; values via type-only annotations where possible |
| `errors.py` | AgentEvalFlowError and Validation/Configuration/Capture/Storage exception family | Standard library; ValidationReport type-only |

**Import direction matters:** suite fields do not import RunSet; its evaluate
method imports the coordinator locally when called. Study can therefore import
suite, RunSet can import RunPlan from study, and result can import both. Native
grades and EvaluationActivity live in runset, so a captured native grade does
not import EvaluationResult. Protocols do not become a dependency of record
field construction; method annotations use postponed/type-only imports.
Values imports no execution, evaluation, storage, reporting or optional adapter.

The public package exports the existing names regardless of these private file
locations. A simple record does not need a separate service or module.

## Construction and structural validation

Use Pydantic dataclasses for record constructors and TypeAdapter for root
serialization and typed generic boundaries. Keep `frozen=True`, `kw_only=True`,
forbid undeclared record fields, and revalidate existing instances at owning
boundaries. `dataclasses.replace` constructs another validated record. The
public `.validate()` methods additionally return relational issues without
mutation. This is one schema/validation implementation, not parallel handwritten
type checking beside Pydantic.

Before retaining caller containers, recursively copy mappings and arrays into
immutable snapshots. A Mapping view cannot retain a mutable original dict.
JSON arrays become immutable sequences; `from_records` and other convenience
boundaries normalize accepted sequences before strict field validation.
Register Pydantic serializers/validators for these representations; do not use
pickle, executable deserialization or an `Any` escape hatch for typed fields.
An `Observation[T]` constructor alone cannot validate its erased T: validate
it again through the typed field/TypeAdapter that owns it.

| Invariant group | Validation behavior |
| --- | --- |
| Scalars | IDs nonempty; bool distinct from int; keys contain only str/int; JSON floats finite; decimal USD and resource counts finite/nonnegative; repetitions/concurrency/limits positive, retry indices nonnegative integers |
| Observation | observed/estimated require a non-null value of T; unknown requires null and a reason; no inferred zero |
| Time | Normalize aware timestamps to UTC; reject naive timestamps and end-before-start; preserve unknown endpoints as null |
| Table | Nonempty declared key columns, schema keys exist in rows, row keys unique and correctly typed; related keys include root keys; references resolve to root units |
| Study | Mapping key equals Candidate.id; contrasts and job memberships resolve; job IDs unique, candidate groups disjoint; job/backend full VersionRefs agree; repeated verifier channel definitions identical |
| Capture | Exactly one final run per assignment after reconciliation; unique IDs; execution parent graph acyclic; event/output-source IDs resolve; scopes agree; native job/run/link joins resolve |
| Grading | Grade IDs unique; activity references resolve and cover run IDs; task/detail keys unique; source selectors are unambiguous or produce explicit missing/error cells |
| Result | One task cell per requested metric/run; score/summary IDs resolve; performed activities are a distinct subset; activity resource/inventory aggregates agree with stored totals |

ValidationIssue.path uses an unambiguous JSON Pointer into the owning serialized
root, for example `/runs/2/executions/0/parent_id`. Include related opaque IDs in
the message. `ValidationReport.valid` is false if any error exists; warnings do
not make it invalid. `raise_for_errors()` raises the public ValidationError.
Preflight adds ConfigurationError context; bad captured identity graphs become
CaptureValidationError; saved-run definition mismatches become
CaptureCompatibilityError. Direct constructor schema errors may be Pydantic
ValidationError, as permitted by the acceptance tests.

Relational validation is reused on construction boundaries, dispatch results,
import and load. An importer does not receive a weaker schema. A malformed
evaluator/reducer response is contained by its evaluation owner as documented
in [evaluation](../evaluation/README.md); it is never retained as a valid result.

## Dataset and candidate methods

`EvalDataset.from_records(...)` infers homogeneous scalar columns and uses
`json` for general JSON columns; it never casts a boolean key to an integer.
Explicit DataTable construction supplies a schema when inference is ambiguous.
`agent_input(unit)` validates the typed root key, filters each selected public
table to that root, copies only input_columns and returns AgentInput. Root
identity also lives in `AgentInput.unit`; public columns are not expanded.
`select(units)` checks the requested keys, filters units/records/references
together and preserves schema, projection and cluster definitions.

Internal joins use `typed_key(row, columns) -> tuple[tuple[str, str | int], ...]`
with the column order fixed by the declared key. Each component carries its
scalar type in identity encoding; Python bool/int equality must never establish
a join. Build an index once per validated dataset/capture operation and retain
it only as an ephemeral local value. No mutable index is serialized.

`Candidate.derive` copies the original semantic data, replaces/removes entire
named components, applies top-level setting updates and handles native options
with the explicit omission sentinel. Omitted native preserves; explicit None
clears. Return a new Candidate with the supplied ID. `diff(other)` recursively
compares semantic mappings, preserving sequence order. Paths are JSON Pointers;
operations are add/remove/replace. For add, before=null; for remove, after=null;
the operation distinguishes missing fields from real JSON-null values. Labels
and descriptions do not appear as behavioral changes.

## Shared resource algorithms

```python
def combine_inventory(
    observations: Sequence[Observation[bool]],
) -> Observation[bool]: ...

def aggregate_resources(
    items: Sequence[Resources], *,
    inventory_complete: Observation[bool],
    cost_scope: tuple[CostCategory, ...],
) -> Resources: ...
```

`combine_inventory`: an observed false dominates; otherwise any value not
observed true makes completeness unknown; otherwise return observed true.
An empty input is observed true for a scope known to contain no attempts.
Do not promote estimated completeness to observed completeness.

`aggregate_resources`:

1. If inventory is not observed true, return unknown observations for all
   aggregate quantities, with a completeness reason. Keep the input records.
2. Otherwise aggregate each quantity independently. A missing value makes that
   quantity unknown; another quantity can still be known.
3. Sum Decimal costs exactly with a local context sufficient for the input
   coefficient lengths/scales, independent of caller rounding settings; sum
   integer token counts exactly. Human minutes
   remain finite floats. If any used observation is estimated, propagate
   estimated basis; otherwise observed. Empty complete input produces zero.
4. A cost-scope mismatch makes cost unknown; it does not erase known token or
   human-work quantities. No allocation from an inseparable larger scope.
5. Preserve source evidence and give a deterministic explanation of unknown or
   estimated totals. This helper does not invent prices or convert tokens to USD.

`Run.resources()` validates identities and sums exclusive Execution.resources
once per execution ID, including unsuccessful/unselected work and descendants.
Its completeness source is execution_inventory_complete, and its scope is the
run's declared scope. `Run.duration_s()` uses the root run's start/end interval,
never the sum of overlapping execution durations; a missing endpoint or
unconfirmed end yields unknown. `coverage()` applies the exact four-bucket
partition in the data contract; resource_unknown overlaps those buckets.

Grading calls these same helpers over unique EvaluationActivity records.
Deduplication occurs before aggregation, with conflicting same-ID records
rejected. No shared grade expense is copied into each Run.

## Fingerprints and planning identities

```python
def freeze_json(value: JSONValue) -> JSONValue: ...
def canonical_bytes(value: object) -> bytes: ...
def semantic_fingerprint(kind: str, payload: object) -> str: ...
```

Canonicalization is a private format distinct from ordinary agent-output JSON.
Use explicit scalar tags for null, bool, int, float, decimal and text; sequence
tags preserve order and mapping tags sort Unicode keys. Encode UTF-8 JSON with
fixed separators and no nonfinite numbers. Decimal numeric equivalents normalize
without loss of precision: use `as_tuple()`, remove trailing coefficient zeros
while adjusting the exponent, and give decimal zero one canonical form.
Finite floats use `float.hex()`, retaining their float tag and signed zero.
Do not use Python repr/hash, locale or object
identity. UTC times use the wire convention. Standard SHA-256 hashes
`b"agent-eval-flow:0.4:" + kind.encode() + b"\n" + canonical_bytes(payload)`.

| Fingerprint / ID | Exact semantic payload |
| --- | --- |
| Candidate fingerprint | backend, components (kind/ref/params/content), settings, native; exclude id/description |
| Dataset fingerprint | units, unit_key, input_columns, records, references, effective cluster_by; exclude id/description |
| Suite fingerprint | version, metrics including sources/params/dependencies, summaries, acceptance, rubric, evaluation_cost_scope; exclude id |
| Study fingerprint | project_id, dataset fingerprint, candidate-ID-to-fingerprint mapping, suite fingerprint, execution policy, contrasts, environment; exclude id/question |
| Execution-definition fingerprint | project_id, dataset fingerprint, unit_key/effective cluster_by, candidate-ID-to-fingerprint mapping, execution policy and environment; exclude suite, study labels and contrasts |
| Assignment ID | semantic_fingerprint("assignment", execution-definition fingerprint + candidate_id + typed unit key + repetition) |
| PlannedJob ID | semantic_fingerprint("planned-job", execution-definition fingerprint + NativeJobConfig.id) |
| Fresh capture/run/job/result/activity IDs | Random UUID values with readable kind prefix; allocate before dispatch and retain on save/load |
| Fresh post-run grader config fingerprint | VersionRef, MetricSpec definition/params, declared evaluation scope and dataset fingerprint; exclude new activity ID |
| Fresh native verifier config fingerprint | Adapter-versioned fingerprint of VerifierSpec, relevant native configuration, supplied public/private projections and candidate fingerprints; exclude allocated run/job/activity IDs |

The `+` in the payload table means a structured tuple, never ambiguous string
concatenation. Dataset/table serialization includes key/schema/rows; mapping
order is ignored, row and array order preserved. Native dialect revision and
ArtifactRef URI/media type/hash are declared semantic fields. A hash missing
from an artifact does not prove content immutability. Same inputs yield stable
plan identities, not deterministic model outcomes.

NativeJobRequest intentionally lacks the complete dataset and its fingerprint.
A native verifier fingerprint therefore describes the configuration and input
projections actually supplied to that integration, with its algorithm/dialect
identified by the adapter. It cannot claim access to undisclosed reference data.
Retained native fingerprints remain source observations; an import may preserve
unknown as None. The library's post-run engine has the dataset fingerprint and
uses the common post-run definition above.

Saving/loading delegates to [storage](../storage/README.md). `Study.plan` delegates
to planning; execution/evaluation methods delegate to the shared coordinator.
Inspection methods on RunSet/Result are pure joins/read-only arithmetic and
never resolve runtime bindings.

## Acceptance links

- [Configuration](../../../tests/contracts/test_configuration.py): projections,
  defensive copies, derive/diff/null operations, semantic fingerprints and plans.
- [Capture](../../../tests/contracts/test_capture.py): typed observations,
  output availability, identity graphs, inventories, scope and duration.
- [Native jobs](../../../tests/contracts/test_native_jobs.py): ownership and
  private projections at the real extension boundary.
- [Evaluation](../../../tests/contracts/test_evaluation.py) and
  [storage/results](../../../tests/contracts/test_results_storage.py): grading
  relations, typed values and load-time validation.
