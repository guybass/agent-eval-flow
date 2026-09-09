# Execution: plans, native jobs and preserved captures

**Behavioral contract 0.4; initial assessment implementation in package 0.5.**
See the [implemented surface and boundaries](../../implementation/unified_assessment.md). The extension below specifies the execution responsibilities
of the accepted [unified flow](../../UNIFIED_ASSESSMENT_FLOW.md); the remaining
sections specify the existing behavioral boundary. This page completes levels
3 and 4 for `agent_eval_flow/execution/`. Existing public fields come from the
[data contract](../../DATA_CONTRACT.md); [objects](../objects/README.md) owns
validation/identity, and [pipeline](../pipeline/README.md) owns request coordination.

## Proposed extension: snapshots, collection and gated dispatch

This module obtains evidence and dispatches declared native work. Configuration
checks remain in evaluation; a collector is not an agent backend. Public
`CandidateSnapshot`, `ConfigurationCapture`, `AssessmentActivity` and related
record shapes belong to the [assessment contract](../ASSESSMENT_CONTRACT.md).
The proposed files stay inside the existing execution module:

| Proposed file | Level 3 ownership |
| --- | --- |
| `execution/snapshot_assessment.py` | Snapshot helpers used during collection: resolve immutable configuration sources, verify retained content, and expose source-aware snapshots to both branches. Uses storage's content-addressed artifact services. |
| `execution/configuration_assessment.py` | Invoke `ConfigurationCollector.collect` for selected candidate/source definitions, retain collection activity/coverage and partial receipts, validate the returned capture and snapshot ownership. |
| `execution/snapshot_binding_assessment.py` | Prepare one complete dispatch unit through its selected `SnapshotBinder`, validate session bindings/delegates, and own async session cleanup after capture. |
| `execution/dispatch_assessment.py` | Wrap the existing planning/allocation/dispatch helpers with explicit gate dependencies; create truthful never-dispatched placeholders without changing the public `RunExecutor.execute` signature. |

Private prepared state contains resolved sources and collector bindings, not
live work results. Per-invocation mutable collection buffers own acknowledged
artifact receipts and activity progress, following the existing recorder
identity rules. Binding objects, native clients and temporary staging roots
remain unsaved implementation state. No collector result is inserted into
`Run.executions`, `RunPlan.assignments` or native job membership.

### Level 3: a separate snapshot preparation protocol

Existing `BackendAdapter`, `NativeJobAdapter` and capability records do not
provide snapshot preparation. The extension therefore uses the separate
versioned `SnapshotBinder` protocol in the
[assessment contract](../ASSESSMENT_CONTRACT.md):

```text
SnapshotBinder.prepare(request: SnapshotBindRequest) -> Awaitable[SnapshotSession]

# Invocation-local orchestration; no new method on an existing backend.
prepare_bound_dispatch(complete_dispatch_unit, candidate_snapshots,
                       requested_parity, selected_binder)
```

Preflight resolves the binder against the target backend's complete version
reference and checks its declared capabilities. `SnapshotBindRequest` contains
the complete allocated direct/native dispatch unit, the applicable
candidate-to-`CandidateSnapshot` map and requested parity. `prepare()` only
materializes or verifies configuration; it starts no task, native job or model.

The returned `SnapshotSession` is an invocation-owned asynchronous session. It
provides `SnapshotBinding` records and staged direct/job backend delegates that
obey the unchanged original protocols. Validate candidate/snapshot joins and
complete assignment/job membership before dispatch. A delegate cannot change
the selected backend revision, execution policy, assignment identities, native
group membership or private reference channels. Verified configuration staging
does not establish later agent task success.

Execution retains the session while its existing recorder captures the complete
unit, including partial native returns and grading artifacts. Retain required
artifacts before session cleanup removes temporary staging. Close the session
on completion, ordinary failure and cancellation; retain cleanup/stop evidence
without replacing captured work. The session, its delegates, clients and local
paths are private live state. Only serializable snapshot bindings, source
artifacts and outcomes enter the outer result.

Strict fresh combined execution requires a compatible binder. Saved runs never
open a session. With `record_only` and no selected binder, dispatch through the
original backend and retain unknown parity. A selected binder still must match
its target revision and validate its output; permissive parity is not permission
to accept contradictory identities.

### Level 4: collect/freeze, then inspect or execute the same subject

1. After whole-plan preflight, invoke the selected collector over the candidate's
   `ConfigurationSpec`. It enumerates explicitly selected configuration roots,
   files and resolved context using snapshot helpers. Preserve logical paths, source tool,
   configuration scope and root relationships. Reading `SKILL.md` bytes without
   identifying which agent setup owns them is insufficient context for a
   candidate-scoped assessment.
2. Freeze retained bytes with a content manifest and verify hashes while
   copying. Detect source changes across enumeration/copy; retry only under
   a declared collection policy or record an unstable/partial snapshot. Do not
   assert a complete immutable configuration over a moving source tree. Never
   follow escaping references into arbitrary machine files implicitly.
3. Resolve the candidate definition and snapshot identity through the common
   identity service. Record omitted, unreadable or unresolved sources and
   relevant context as coverage facts. Private expected outcomes are outside
   this manifest and outside agent materialization.
4. Finalize the `ConfigurationCapture`, then pass the same immutable snapshot
   reference to configuration evaluators and behavioral preparation. Collection
   may parse files and build component/reference records, but may not alter
   frozen bytes, fix source files or invoke task agents. Its missing/unsupported
   inputs remain explicit capture coverage. Checks and native execution may
   overlap after this shared freezing boundary.
5. For fresh native execution, use the selected binder session to materialize
   the snapshot into the runtime's actual configuration channels, or obtain
   verifiable evidence that the runtime uses the retained bytes/context.
   Preserve native effective
   settings separately. If a native runtime reads additional ambient settings,
   they must be captured/isolated by the declared adapter or remain unknown;
   identical candidate labels cannot establish parity.
6. Retain parity evidence and staging receipts with the behavioral capture
   association defined by the assessment contract. With
   `snapshot_requirement="verified"`, missing compatible binder capability fails
   preflight; failed materialization prevents affected fresh dispatch. With
   `snapshot_requirement="record_only"`, retain unknown/mismatched parity as a
   qualification in the shared assessment; no silent claim of a controlled
   comparison is allowed.

Source freezing is an execution-time evidence operation. Constructing an
`AssessmentPlan`, compiling its checks or enumerating its intended tasks starts
neither collection nor native execution. Snapshot materialization only writes
invocation-owned staging paths and never changes the user's configuration.

Saved configuration captures are validated and used directly: no collector is
called to make their old evidence look current. Saved runs similarly bypass
all native dispatch. Reused behavioral evidence with only a declared candidate
fingerprint may identify the intended candidate without proving historical
physical configuration parity. Record that distinction; do not replace its
snapshot identity with a hash of the directory today.

### Level 4: gates operate on complete dispatch units

The pipeline supplies resolved gate decisions, not scanner-specific verdicts.
Execution accepts a decision per affected dispatch unit and does not recompute
policy. It allocates the complete behavioral plan and stable assignment joins
using existing planning helpers before filtering launch eligibility.

- For direct work, the dispatch unit is one assignment. It starts only when
  all applicable explicit gates release it. Unrelated candidates need not wait
  for that candidate's configuration evaluator.
- For a native job, the unit is the complete `PlannedJob`. Wait for gates on
  every candidate in its membership. If any applicable gate blocks, withhold
  the whole job. Preserve `not_run` for all its assignments, citing the gate
  and native-job atomicity for otherwise unaffected members. Never silently
  subdivide the job, drop a candidate or rerun its allowed subset separately.
- Gate readiness may delay a dispatch unit without changing the study's
  scheduler ownership, concurrency limit, repetitions or retry policy. Native
  job internals remain with the native runtime; the configuration branch is
  an independent pipeline dependency and does not become another trial loop.

`gate_blocked_run(...)` is a new private helper, distinct from current
`missing_run(...)`. Core knowledge that launch did not happen establishes
`not_run`, empty execution inventory, no native execution identities and the
saved gate reason. A known-never-launched native job likewise receives no
fabricated native receipt. Keep the complete original planned membership and
allocated library identities. Scope-aware resource accounting records no agent
execution expense for known undispatched work, while its separately performed
inspection activity retains its real cost.

After an acknowledged native launch, lack of an export is `unobserved` unless
native evidence establishes `not_run`. An unknown gate decision with
`on_unknown="allow"` is a release instruction, not evidence that the check
passed. A gate evaluated over saved runs cannot turn observed historical work
into a blocked placeholder.

### Collection failure, cancellation and receipt lifetime

Collection records identify actual invocations with assessment-namespace
activities. An invocation may retain zero, one or many artifacts. Reusing a
saved capture refers to the historical collection activity and creates no new
collection charge. Parsing that capture with a newly requested evaluator is
a separate activity owned by evaluation.

Ordinary read/parse/collector errors preserve acknowledged sources, failed
regions and unknown completeness; a partial component list does not establish
that omitted components do not exist. The pipeline may continue its independent
behavioral branch. Contradictory artifact hashes, foreign snapshot identities
and incompatible terminal receipts are structural capture errors, never empty
successful inspections.

Each source collector owns cleanup of its staging resources. Parent
cancellation waits for managed local work and requests stop from an external
collector where supported; stop request and confirmed stop remain different
facts. Preserve receipts before cleanup. This introduces no automatic
checkpointing and makes no promise to terminate an uncooperative worker.
Snapshot artifacts still referenced by a returned or retained partial capture
must outlive the temporary staging directory.

### Future acceptance scenarios

These additions require new versioned acceptance cases; existing v0.4 tests
below remain unchanged:

1. Change a live skill file after freezing. The scanner reads the retained
   bytes, and a capable backend consumes the same materialization; the new live
   bytes cannot enter either branch unnoticed.
2. Use a backend that cannot isolate a relevant user-scope configuration.
   Strict parity rejects before launch; permissive parity retains unknown
   context instead of asserting matching snapshots.
3. Block candidate B in one native job containing A and B. Observe zero
   `run_job` calls, complete planned assignment membership and `not_run`
   reasons for both candidates; no replacement job is created for A.
4. Fail a native export after launch. Missing tasks remain `unobserved`, while
   a separate pre-launch gated task is `not_run`; the two states cannot be
   collapsed into one placeholder constructor.
5. Return a partial configuration capture after a parser error. Known files
   and its activity remain accessible, coverage is incomplete, and a healthy
   ungated behavioral branch still finishes.
6. Reuse saved configuration and runs whose physical parity is unknown. Make
   no backend/collector calls and preserve unknown parity; inspecting today's
   files cannot retroactively establish yesterday's configuration.
7. Supply private reference tables to selected check projections. Assert they
   never occur in the snapshot manifest, collector inputs or agent staging.
8. A binder prepares a native job containing two candidates. Its request and
   delegate retain the complete membership, and exactly one original-protocol
   `run_job` call occurs. An attempted reduced membership is rejected before
   dispatch; session cleanup still runs.
9. Raise during capture after staged agent launch. Retain acknowledged output
   and snapshot bindings before cleanup, preserve unknown remote-stop state,
   and serialize no session/client/delegate object.

## Existing v0.4 behavioral specification

## Level 3: file boundaries and private records

```text
execution/
├── __init__.py     packaging only
├── planning.py    deterministic assignments and native group membership
├── preflight.py   adapter references, capabilities and prepared execution
├── runner.py      fresh IDs, bounded direct calls and whole native calls
├── capture.py     mutable recorders, reconciliation and failure placeholders
└── importing.py   import envelope through the same capture validation
```

| Private record | Exact fields |
| --- | --- |
| `PreparedExecution` | `plan: RunPlan`; `dataset: EvalDataset`; `backends: Mapping[str, BackendAdapter]`; `job_backends: Mapping[str, NativeJobAdapter]` |
| `AllocatedExecution` | `run_set_id: str`; `requests: Mapping[str, RunRequest]` keyed by assignment ID; `jobs: tuple[NativeJobRequest, ...]`; `direct_assignment_ids: tuple[str, ...]` |

These immutable records live for one invocation. They are not saved or exposed
as seventh/eighth domain objects. The runner owns mutable local maps of finalized
runs and returned job outputs; the recorders below are the only mutable capture
owners. No execution module imports evaluation metrics, compilers or judges.

## `planning.py`: complete assignment identity before runtime

```python
def build_plan(study: Study) -> RunPlan: ...
def assignment_identity(candidate_id: str, unit: Key, repetition: int,
                        execution_identity: str) -> str: ...
```

`build_plan` validates the study, constructs `DatasetInfo` with only root key
and grouping columns, and enumerates candidate × root unit × repetition.
Candidate IDs are sorted; root rows retain declared table order; repetition
indices start at zero. The assignment ID uses the versioned semantic identity
function in `objects/identity.py`, never `repr(unit)` or concatenated key text.
The typed unit key distinguishes integers from strings and rejects booleans.
The execution fingerprint covers project ID, dataset fingerprint, unit key and
effective cluster columns, candidate-ID-to-fingerprint mapping, execution policy
and shared environment. It excludes study labels, suite and contrasts.
Use `semantic_fingerprint("assignment", (execution_identity, candidate_id,
typed_unit_key, repetition))`; the candidate fingerprint already participates
through execution identity and is not hashed through a second recipe.

Construct all assignments before ordering them. When `order_seed` is an integer,
apply one local seeded ordering operation to the completed assignment list;
`None` preserves enumeration order. Never consume process-global random state.
Changing scheduling order must not create duplicate assignments. Native groups
subselect this ordered list, so every group has precisely its candidates' work.

For each declared `NativeJobConfig`, create one `PlannedJob` whose copied config
equals the policy entry and whose assignment IDs include all and only that
group's candidates. Group IDs are `semantic_fingerprint("planned-job",
(execution_identity, config.id))`, not future native job IDs.
Jobs appear in policy order. Non-covered candidates remain direct assignments.
The plan retains `study.fingerprint()` as provenance, while compatibility checks
use execution meaning rather than this full fingerprint. Planning calls no
runtime adapter, reads no credentials and allocates no invocation IDs.

## `preflight.py`: resolve the selected execution path

```python
def prepare_execution(
    study: Study,
    backends: Mapping[str, BackendAdapter],
    job_backends: Mapping[str, NativeJobAdapter],
) -> PreparedExecution: ...
```

1. Build the plan and validate candidate IDs, policy limits, disjoint job
   membership and verifier definitions. A job-owned candidate's complete backend
   reference equals its job's; unlisted candidates use the direct registry.
2. Resolve each required registry name and compare its implementation `ref` with
   the declared name/revision. Live adapter revisions must be resolved;
   imported unknown revisions remain valid captured observations. Missing,
   unresolved or mismatched live bindings are
   `ConfigurationError`. Keep only required bindings in the prepared record.
3. Query each distinct selected adapter's `capabilities()` once for this
   preparation. Required wall-time, token and cost limits must be enforceable;
   state reset must be supported. Native jobs must guarantee declared fixed
   repetitions. Private reference input additionally requires a private verifier
   channel. Capability values do not establish effective settings or reset facts.
4. Return `PreparedExecution`; no native process, tool or trial starts here.
   A reference/capability query that raises cannot establish readiness: report
   it as `ConfigurationError` with its original cause before dispatch.

Native semantic validation has an explicit boundary. Core preflight validates
the `NativeConfig` envelope, resolved dialect reference, canonical ownership and
capabilities. It cannot interpret arbitrary third-party dialect keys through
the existing protocols. Each built-in adapter has private preparation that
validates its dialect and canonical conflicts before that adapter launches
work. Custom adapters have the same duty. They raise `ConfigurationError`
without starting their native agent. No undocumented validator registry or new
mandatory adapter method is introduced to claim global dialect preflight.

## `runner.py`: allocation, projection and dispatch order

```python
def allocate(prepared: PreparedExecution) -> AllocatedExecution: ...
def verifier_inputs(dataset: EvalDataset, config: NativeJobConfig,
                    requests: tuple[RunRequest, ...]) -> tuple[VerifierInput, ...]: ...

class RunExecutor:
    async def execute(self, prepared: PreparedExecution) -> RunSet: ...

async def dispatch_direct(adapter: BackendAdapter, request: RunRequest) -> Run: ...
async def dispatch_job(adapter: NativeJobAdapter,
                       request: NativeJobRequest) -> NativeJobOutput: ...
```

Allocate a fresh run-set ID, one run ID per assignment and one job ID per planned
native job before any dispatch. Use new opaque UUID identifiers; assignment and
planned-job IDs stay stable. Every `RunRequest` contains its allocated ID,
matching candidate/assignment, `dataset.agent_input(unit)`, policy and shared
environment. It contains no full dataset or private reference values.

`verifier_inputs` emits one item per request/verifier pair. Resolve only private
table names, filter rows by the typed root key, retain each selected table's key
columns plus explicitly selected columns, and preserve matching row order.
Unselected columns/tables never appear. Empty selected tables remain empty
tuples; they are not fabricated answers. A verifier with an empty projection
receives `{}`. Its budget is independent of the assignment budget. The trusted
adapter routes these records only to its verifier environment.

```text
allocated = allocate(prepared)
for native_request in allocated.jobs:             # declared policy order
    outputs.append(await dispatch_job(selected_adapter, native_request))
await bounded_direct_group(allocated.direct_assignment_ids)
capture = reconcile all native outputs and direct finalized runs
validate complete run/job/grade/activity joins
return immutable RunSet with runs in plan assignment order
```

The direct group is last and completes before `execute` returns. It may be
empty. Native groups are awaited sequentially; core code never starts the
trials inside one native job. `max_concurrency` bounds assignments inside the
active group; the native runner is responsible for its internal limit.

The direct group uses an AnyIO task group and a per-invocation capacity limiter
with `max_concurrency` tokens. Pass that limiter into `anyio.to_thread.run_sync`
around the complete `BackendAdapter.run` call, binding its recorder argument and
using `abandon_on_cancel=False`. Do not also acquire the same limiter outside
the call. This private limiter leaves the default thread pool capacity available
to async helpers. Do not block the event loop with synchronous
backend work. A built-in synchronous adapter that needs an async process/worker
helper uses `anyio.from_thread.run` from that managed thread to call back into
the existing loop. It does not create another loop or background thread.
The library always invokes this protocol through its dispatcher; manually
calling adapter methods outside that managed context is not an execution API.
Capacity is an upper bound, not a promise to saturate the limit.
Results enter an assignment-keyed map, so completion order never establishes
identity. Do not mutate a shared global limiter or an SDK client's configuration.

`infrastructure_retries` and all budgets are passed through to the selected
adapter. A direct callback owns its entire assignment, including child work and
retries; a native callback owns its job's trial scheduling. Core dispatch calls
each adapter once for its declared unit of work. It must not wrap either call
in a second retry loop or a caller-only timeout that pretends to stop native work.

## `capture.py`: record receipts, then finalize once

The two small buffers implement existing recorder protocols; they are not
general event buses or persistent workflow engines.

| Mutable owner | Private fields |
| --- | --- |
| `CaptureBuffer` | `request: RunRequest`; `executions: dict[str, Execution]`; `events: dict[str, Event]`; `artifacts: dict[str, ArtifactRef]`; `state: Literal["open", "finalized", "invalid"]`; `final: Run \| None`; `lock: RLock` |
| `JobCaptureBuffer` | `request: NativeJobRequest`; `job: NativeJobRecord \| None`; `runs: dict[str, Run]`; `bundles: dict[str, NativeGradeBundle]`; `projections: list[ProjectionReport]`; `state: Literal["open", "finalized", "invalid"]`; `final: NativeJobOutput \| None`; `lock: RLock` |

```text
CaptureBuffer(request: RunRequest)
CaptureBuffer.record_execution(execution: Execution) -> None
CaptureBuffer.record_event(event: Event) -> None
CaptureBuffer.record_artifact(name: str, artifact: ArtifactRef) -> None
CaptureBuffer.finish(returned: Run) -> Run
CaptureBuffer.fail(error: Exception) -> Run

JobCaptureBuffer(request: NativeJobRequest)
JobCaptureBuffer.record_job(job: NativeJobRecord) -> None
JobCaptureBuffer.record_run(run: Run) -> None
JobCaptureBuffer.record_grade_bundle(bundle: NativeGradeBundle) -> None
JobCaptureBuffer.finish(returned: NativeJobOutput) -> NativeJobOutput
JobCaptureBuffer.fail(error: Exception) -> NativeJobOutput

def missing_run(assignment: Assignment, *, run_id: str, cost_scope: tuple[str, ...],
                job_id: str | None, reason: str) -> Run: ...
```

Every recorder call validates scalar shape and ownership before changing its
locked state. Identical receipts are idempotent. Execution and job progress may
advance an existing nonterminal record; identity and declared ownership cannot
change. Terminal records, events and grade bundles cannot be replaced by
different facts. Cross-record references may arrive out of order, so complete
parent/event/output-source checks occur at finalization. A conflicting receipt
sets state invalid and raises `CaptureValidationError` with its identity/path.

`finish` validates returned IDs against the allocated request, merges identical
receipts with returned records by ID and validates the complete graph. Receipts
omitted from the final export remain captured; arrays are never concatenated.
Every direct finalized run has the allocated run/assignment ID. Every native
run has its allocated ID and owning job ID; links agree with both. The job's
assignment list remains complete even when export is partial. Missing exports
become placeholders through `missing_run`. Native identifiers are never invented.

`missing_run` returns `status="unobserved"`, `output_state="unknown"`, null
output, no executions/events/output sources, unknown environment and execution
inventory observations with reasons, null timestamps and the declared cost
scope. Its resource helper therefore remains unknown. It does not infer
`not_run` from a job error. Explicit native evidence can establish `not_run`.
Finalization sets `final` and state finalized; identical repeated finalization
returns that snapshot, while a different terminal result is invalid.

### Failure and lifetime rules

`dispatch_direct` catches an ordinary adapter `Exception` and calls `fail`:
preserve receipts, create an `infrastructure_error` run with original exception
type/message, unknown uncaptured output/environment/inventory and no invented
completion time. It does not label an already captured successful child
execution failed. Returned agent errors/timeouts keep their declared statuses.

`dispatch_job` handles an ordinary exception similarly: preserve recorded runs,
links and grade bundles; produce an error job when no terminal job receipt
exists; fill uncovered allocated work as unobserved; set grading inventory
unknown with the export failure reason. Preserve original exception evidence
when supplied; retain its class/message in the error record in every case.
If a completed terminal job receipt already exists, keep it immutable and record
the subsequent export exception through a `ProjectionReport` issue and available
raw exception artifact. The native job may have completed while export failed;
unknown inventory and placeholders still express that incomplete capture.

Configuration rejection and `CaptureValidationError` propagate; they are not
normalized as agent infrastructure errors. A structural failure in concurrent
direct work prevents returning a valid RunSet, cancels undispatched siblings and
awaits already running non-abandoned thread callbacks. Preserve the concrete
validation exception when unwrapping AnyIO task-group failures; do not catch
the entire task group and manufacture successful records. `BaseException`
cancellation/interrupts propagate. Async native adapters own cancellation cleanup;
waiting cancellation cannot establish that remote work stopped. No automatic
checkpoint/resume or forced thread termination is promised.

Final RunSet grading completeness is observed true only when every native
output establishes observed true inventory; no native jobs means observed true
empty inventory. A known omission yields observed false; otherwise an unknown
or estimated inventory yields unknown. Keep actual grading activities even
when completeness is false/unknown. Resource totals are computed by the shared
record helpers, never by summing a job-level invoice onto per-run charges.

## `importing.py`: identical joins without execution

```python
def import_runs(source: Path, plan: RunPlan, importer: RunImporter) -> RunSet: ...
def reconcile_import(capture: ImportedCapture, *, plan: RunPlan,
                     run_set_id: str, importer: VersionRef,
                     import_source: ArtifactRef) -> RunSet: ...
```

Validate the plan and importer reference, then call `importer.read(source,
plan=plan)` once. Require an `ImportedCapture` envelope, including its inventory
observation. Preserve supplied run/job/grade/activity IDs, raw source references
and projection reports. Reject unknown assignments, duplicate final runs,
dangling jobs/links/activities and contradictory ownership with the same common
validators used by fresh capture. Missing assignments get new placeholder IDs
once; subsequent save/load preserves them. Assign missing job ownership only
from unambiguous declared job coverage and retained job records; never invent a
native trial identifier. No backend registry is available here.

The importer reports known partial exports in its envelope. An exception before
returning the envelope propagates with import/source context; the core has no
partial importer recorder and must not invent captured facts. An unreadable
source is a `StorageError`; malformed captured data is `CaptureValidationError`.
The core creates `import_source` from the resolved source path: an ordinary
file uses `application/octet-stream` and a hash of the observed bytes; a directory
uses media type `inode/directory` and `sha256=None`. Directory manifests and raw
native file references stay in the importer's projection sources; the core does
not guess their layout or claim a directory content hash. Compare a file's hash
before and after import; mismatch raises `CaptureValidationError` for changed
source. Directory snapshot consistency belongs to its importer. Reading an
artifact does not execute its contents.

## Acceptance links

- [Configuration](../../../tests/contracts/test_configuration.py): stable plan,
  typed keys, native group ownership, verifier channels and invalid limits.
- [Native jobs](../../../tests/contracts/test_native_jobs.py): private projections,
  sequential native/direct groups, capabilities, exception receipts, duplicate
  snapshots and fresh run/job identities.
- [Capture](../../../tests/contracts/test_capture.py): allocated identity graphs,
  scopes, missing outputs, inventory, resources and timestamp rules.
- [Pipeline runtime](../../../tests/contracts/test_pipeline_runtime.py): bounded
  direct concurrency and preflight before dispatch.
- [Toy import](../../../tests/e2e/test_toy_pipeline.py):
  `test_import_protocol_preserves_missing_assignments` and process failures;
  [native/batch E2Es](../../../tests/e2e/test_data_contract.py) reconcile reversed
  partial exports and preserve result usability.
