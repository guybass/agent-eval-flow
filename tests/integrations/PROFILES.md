# Optional-library integration acceptance profiles

These three tests exercise real **Harbor execution**, **SkillEvaluator import**
and **NAT trajectory grading** through the proposed public API. They supplement
the test-owned protocol fixtures. The factories and genuine source fixtures are
not implemented or supplied yet; no optional runtime was installed or invoked.
An unselected profile skips. A selected profile with an absent dependency,
factory, source archive or worker fails. A test-owned replacement cannot turn it
green. No upstream benchmark score is required.

```text
python -m pytest tests/integrations --aef-live harbor_native --aef-profile-config profiles.local.json
python -m pytest tests/integrations --aef-live skillevaluator_import --aef-profile-config profiles.local.json
python -m pytest tests/integrations --aef-live nat_batch --aef-profile-config profiles.local.json
```

The profile JSON uses those names as top-level keys. Every profile contains:

| Field | Meaning |
| --- | --- |
| `factory: "module:callable"` | Deployment-owned construction function; see returned object below |
| `integration_ref: {name, revision}` | Exact adapter/importer/evaluator reference returned by the factory |
| `upstream_ref: {name, revision}` | Actual upstream runtime/source revision, distinct from adapter revision |
| Additional runtime settings | Pinned worker image, deployment and connection configuration; credentials remain outside saved profile data |

Factories have `factory(config: dict, *, workspace: Path) -> object`. Construction
connects to a prepared runtime or importer; it does not start the task, upgrade
packages or provision cloud infrastructure. The integration uses the selected
upstream's actual supported entry point. These tests deliberately do not invent
upstream class constructors, CLI flags or configuration keys. A changed upstream
version needs a compatible factory and explicit pin.

`native.result`, `native.verifier`, `native.trajectory` and `integration.receipt`
below are **our test artifact aliases**, not upstream filenames. Native aliases
point to unmodified upstream bytes, locally materialized with matching SHA-256.
The receipt is separate adapter metadata and cannot replace native evidence.
Temporary native processes, containers and per-run remote resources are cleaned
up by the integration in `finally`; the evidence cache remains through assertions.

## Harbor: a tiny complete native job

Factory returns a real `NativeJobAdapter`. Additional fields are `native_agent`
(the selected native deterministic fixture agent), `verifier_ref: {name,
revision}`, `native_schema_ref: {name, revision}`, `native_options: object`,
`native_metric: str`, and `wall_time_s: float`. The native options are validated
by the selected adapter dialect; the test never interprets them as Harbor CLI
options. The adapter's capabilities must support the declared wall-time limit
and native verifier channel.

The test creates two public tasks, each containing a distinct fresh receipt in
`text`. The factory's adapter materializes a tiny native task whose deterministic
agent writes that receipt and whose native verifier checks the resulting file.
Use a supported deterministic/oracle path from the pinned Harbor checkout or a
test-owned native agent that actually executes inside Harbor's lifecycle. Do not
substitute `ScriptedBackend` or synthesize Harbor results. This specifically
tests task materialization, native scheduling, execution, verification and
capture; it is not evidence of a model's problem-solving ability.

Return one native job with two linked trials. Normalize the delivered file as
`Run.output={"receipt": <the actual file contents>}`. Retain raw job and trial
results as `native.result`, each trial's verifier output as `native.verifier`,
and grades in channel `receipt-check`. The selected native metric returns a
boolean; the test checks its transport and provenance, without asserting its
scientific meaning. The job's `integration.receipt` contains:

```json
{
  "upstream_ref": {"name": "harbor", "revision": "REPLACE_WITH_ACTUAL_PIN"},
  "native_job_id": "ACTUAL_NATIVE_JOB_ID"
}
```

Fresh grading activities belong to the fresh invocation. Evaluating the same
capture afterward needs no job adapter and incurs no new grading activities.

## SkillEvaluator: a genuine retained paired experiment

Factory returns a real `RunImporter`. The profile also supplies a `fixture`
object with these fields:

| Field | Required contents |
| --- | --- |
| `root` | Fixture directory relative to the profile JSON, or an absolute directory |
| `files` | Mapping of relative file paths to SHA-256 hashes; include every raw source file and the saved Study manifest/assets |
| `study` | Relative path to a saved revision-0.4 Study defining the original tasks, two candidates, repetition count and native-grade-only suite |
| `source` | Relative file/directory consumed by the importer, containing genuine retained SkillEvaluator results, native trial results, verifier evidence and available trajectories |
| `provenance` | Relative JSON capture receipt containing `upstream_ref: {name, revision}`, recorded from the actual source runtime; include this receipt in `files` |
| `assignments` | Explicit expected candidate/task/repetition identities with captured status, output state and native IDs, as below |

```json
{
  "candidate_id": "with-skill",
  "unit": {"task_id": "receipt-task"},
  "repetition": 0,
  "status": "completed",
  "output_state": "available",
  "native_refs": {"trial_id": "THE_RETAINED_NATIVE_TRIAL_ID"}
}
```

List **every** planned assignment, including both native baseline/skill arms and
at least one genuinely failed trial. These are expected mappings from the source,
not desired agent performance. The saved candidate definitions retain the actual
skill/harness/model revisions. If the importer emits `NativeJobRecord` objects,
the Study must contain their matching native-job configurations.
Its suite uses no custom runtime reducers: summaries are empty or use built-ins.

Every run, including a failure, retains `native.result`. `RunSet.projections`
identify the importer and raw source files; native grade bundles preserve
verifier provenance and grader activities. The importer does not rerun agents or
verifiers. The test evaluates retained grades with no evaluator or backend
registry and verifies persistence and unchanged source-file hashes.

**Fixture prerequisite:** the previously inspected public `benchmarks.json`
contains dimension summaries, not the raw paired trial archive this test needs.
A real archive must therefore be captured from a pinned SkillEvaluator run and
reviewed with the identity/hash manifest before this profile can execute. Never
manufacture one from summary rows. The separately locked SkillEvaluator worker
must respect its own Harbor dependency pin; importing does not require loading
that worker runtime into the library process.

## NAT: grade retained trajectories as one native batch

Factory returns a real `BatchMetricEvaluator` that invokes the supported NAT
ATIF-only evaluation path. Additional fields are `output_type` (one public metric
type) and `grader_params` (serialized native grader configuration through the
adapter dialect). Use a native grader supported by the chosen NAT pin, with its
required references and provider access. The test does not prescribe a universal
NAT evaluator configuration or a score threshold.

The `fixture` uses the same `root`, `files`, `study`, `provenance` and `assignments`
fields as above, plus `runs`: a saved revision-0.4 RunSet with at least two genuine captured
trajectories. Each run retains its original trajectory as `native.trajectory`.
The manifests pin input bytes, original agent identities and source conversion
versions. The adapter uses the real upstream trajectory validator/converter and
preserves the source; it cannot replace inputs with synthetic traces that merely
have the same task IDs. The saved Study's original suite is replaced by the
single requested batch metric; its dataset/candidates/execution stay fixed.
For NAT, the provenance receipt identifies the pinned NAT-compatible fixture
validation/conversion runtime; it does not relabel the original agent as NAT.
The source agent's separate provenance remains in the saved capture.

The activity retains the actual grader result as `native.result`. Each returned
measurement has native evidence and the one allocated activity ID. At least one
successful native value must be transported; native errors remain unavailable
values with reasons, never apparently successful zero scores. Its separate
`integration.receipt` contains `upstream_ref`, the sorted `trajectory_hashes`,
`native_invocation_count: 1`, and `agent_invocation_count: 0`. These counts come
from the actual integration dispatch log, not the requested settings. A receipt
alone is insufficient: the test also requires raw native output and source-linked
measurements. Retained native grading activities in the input, when present,
remain historical; the new NAT invocation is counted once.

The NAT profile is blocked until there is a genuine compatible trajectory
capture, its saved Study, a pinned grader configuration and the real batch
adapter. Those are deployment/implementation prerequisites, not reasons to skip
an explicitly selected profile or claim integration success today.

The integration choices and version constraints come from the existing
[Harbor/SkillEvaluator evidence](../../docs/research/harbor_skillevaluator_integration_evidence.md)
and [NAT evidence](../../docs/research/nemo_integration_evidence.md).
