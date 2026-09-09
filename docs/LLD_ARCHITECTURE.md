# Agent Eval Flow: LLD at repository levels 0 and 1

**Accepted design extension, 9 September 2026:** the
[unified assessment flow](UNIFIED_ASSESSMENT_FLOW.md) adds configuration
inspection alongside behavioral evaluation. Both branches assess the same frozen
candidate and feed shared assessments and explicit decision policies. This
extension now has a [shared detailed contract](lld/ASSESSMENT_CONTRACT.md) and
[level-3/4 module specifications](lld/README.md) covering both branches. The
extension has an initial implementation in package 0.5 and preserves the revision-0.4 API and wire
contract. The architecture and request flow below describe the behavioral
branch; their original implementation-status statements are historical. See
[current implementation status](IMPLEMENTATION.md).

**Status: proposed architecture; no production implementation.** This was
written after the [E2E tests](../tests/e2e/README.md) and their
[before-implementation baseline](../tests/e2e/BASELINE.md).

The [complete module LLD](lld/README.md) now supplies levels 3 and 4 after the
[full test-first checkpoint](../tests/BASELINE.md). It governs the final private
file/interface refinements; this page retains the original levels 0 and 1 view.

**Contract revision 0.4:** the [data contract](DATA_CONTRACT.md) and
[typed proposal](contracts/agent_eval_flow.pyi) define the current configuration,
input/output records and cross-record rules. This architecture is an adjustable
implementation vision. It incorporates the agreed native-job and batch-grading
boundaries without requiring a new module for every new record.
Capture and evaluation retain separate grading-inventory observations, so
partial exports cannot become apparently free evaluations.

The caller configures tasks, candidates and evaluation in a `Study`, binds the
runtime once, and calls `pipeline.eval()` or awaits `pipeline.aeval()`. The pipeline
coordinates the six domain objects and returns `RunSet` and `EvaluationResult`.
The tests require real output objects, evidence links, custom callbacks,
persistence, comparison, selection and reports. They do not require a specific
definition of agent quality.

Here, **level 0** names the repository's main areas. **Level 1** names the first
implementation split within those areas. `src/agent_eval_flow` is treated as
the library area; `src` is only the Python packaging container. Deeper file and
class layouts are now proposed in [level 2](LLD_LEVEL_2.md).
See also the [module communication map](MODULE_COMMUNICATION.md) and
[LLD reuse audit](LLD_REUSE_RED_TEAM.md). The subsequent
[integration recommendation](INTEGRATION_DECISION.md) uses capability and
adoption evidence to assign concrete dependency responsibilities. The tree
describes ownership; optional integrations supply existing runtime machinery.

## The caller's entry point

```python
# Proposed API: configure definitions and runtime dependencies once.
pipeline = EvaluationPipeline(
    study=study,                 # tasks + candidates + suite + execution policy
    backends=backends,            # direct single-assignment implementations
    job_backends=job_backends,    # optional whole native-job implementations
    evaluators=evaluators,       # ordinary single-run metric implementations
    batch_evaluators=batch_evaluators,  # optional full-sequence grading
    reducers=reducers,           # optional custom aggregation implementations
)

result = pipeline.eval()         # validate -> plan -> execute -> evaluate
result.summary()
```

In an application with an active event loop, use `await pipeline.aeval()`.
Calling synchronous `eval()` there is rejected before work starts; the library
does not hide another event loop in a background thread.

`Study` is the serializable experiment definition. `EvaluationPipeline` is the
configured runtime coordinator: it binds that definition to implementations
and runs the workflow. It is not another persisted domain record, and users
do not manually construct the output objects or connect every stage.

`Study.evaluate(...)` and the one-candidate `evaluate(...)` function remain
shortcuts to this same coordinator. `Study.run()` and `EvalSuite.evaluate()`
remain available for callers who intentionally want execution or grading alone.

## Level 0 — repository areas

```text
agent-eval-flow/                   proposed repository name
├── src/agent_eval_flow/           [planned] installable Python library
├── tests/                        [written] acceptance tests and owned fixtures
├── examples/                     [planned] small caller-facing examples
├── infra/                        [planned] explicit GCP test-environment setup
├── docs/                         [written] API proposal, E2E rationale and LLD
├── pyproject.toml                [planned] package metadata and optional dependencies
├── pytest.ini                    [written] collection and live-profile markers
├── requirements-test.txt         [written] dependencies for the tests themselves
└── README.md                     [written] entry point and project status
```

The local folder is still `skill-eval-flow`; the proposed import is
`agent_eval_flow`. This document does not perform a folder or remote migration.

`src` ships to users. `tests` supplies fixtures and checks the public API.
`examples` demonstrates use of the installed package. `infra` prepares test
workers explicitly; importing or calling the library does not create a cloud
project. Research and design material remain in `docs`.

## Level 1 — the library's first modules

```text
src/agent_eval_flow/
├── __init__.py                   public exports, EvaluationPipeline and evaluate shortcut
├── pipeline/                     configured entry point and whole-workflow coordination
├── objects/                      six main objects and their typed records/protocols
├── execution/                    planning, dispatch and collection of run evidence
├── evaluation/                   metric dependencies, callbacks and aggregation
├── results/                      explanations, comparisons and selection
├── adapters/                     native frameworks, model clients and host transports
├── storage/                      manifests, codecs and artifact references
└── reporting/                    HTML rendering from existing result objects
```

| Module | Responsibilities and main boundary | Driven by tests |
| --- | --- | --- |
| `pipeline` | Own `EvaluationPipeline.eval()/aeval()` and the shared coordinator behind `Study.evaluate` / `evaluate`. Validate the study and direct/job/evaluator/batch/reducer bindings before execution; choose fresh execution or grading of supplied runs; return one `EvaluationResult`. It delegates stage implementations and adds no metric policy. | T01–T04 use the configured pipeline; T05 grades supplied runs without a backend; T11–T13 cover preflight, fresh reuse and the function shortcut; the data-contract cases exercise new boundaries. |
| `objects` | Define `Study`, `EvalDataset`, `Candidate`, `RunSet`, `EvalSuite`, `EvaluationResult` and supporting types. Validate identities/units/references, freeze stored data and fingerprint definitions. Public methods delegate work to the modules below. | Every case constructs or consumes these records; T01 checks public input projection. |
| `execution` | Build assignments and declared native-job groups; validate capabilities; call `BackendAdapter.run` for direct missions or `NativeJobAdapter.run_job` once per whole job. Reconcile runs, native IDs, grade bundles and projection reports, including failed/missing work. Native jobs may execute their declared verifiers before cleanup; the core does not add another native trial scheduler or invoke post-run metrics here. | T01–T04 cardinality and recorder reconciliation; T06 faults; T07 import coverage; native-job data-contract coverage. |
| `evaluation` | Resolve each metric's source: single callback, full-sequence batch or retained native grade. Order dependencies, map identified outputs and grading activities, apply optional rubric/acceptance helpers, and invoke reducers with complete assignments. Preserve original agent costs and account for each grading activity once. | T01–T04 dependency dispatch, custom reducer and fixture arithmetic; T05 rescoring; batch/native-grade accounting cases. |
| `results` | Implement `summary/explain/compare/select` using stored evidence and definitions. Preserve comparison scope, unknown values, exclusions and policy identity. Calling these methods never starts an agent or recomputes a metric. | T01–T04 exact fixture deltas/choices and call counts; T05 retains old results. |
| `adapters` | Implement optional direct, whole-job, importer and batch-grader protocols. Harbor owns its job lifecycle; SkillEvaluator initially supplies retained results; NAT receives batches for grading. Importers return `ImportedCapture`, preserving native jobs/grades and original traces. OpenSRE/OpenKritt keep their own harnesses. | Native-job/import/batch data-contract cases; T08 native CLI/Vertex; T09 OpenSRE; T10 OpenKritt. |
| `storage` | Implement the six objects' relevant save/load methods using versioned manifests and explicit JSON encodings for Decimal/UTC data. Preserve definitions, references and artifact hashes. Resolve/materialize artifacts when an integration promises a local cache. | T01–T07 round trips; T09/T10 native artifact fidelity. |
| `reporting` | Implement `EvaluationResult.report` as a renderer over the loaded result and optional selection. Link explanations to evidence and render supplied output text as data. | All complete-pipeline cases produce an HTML file after loading. |

The exact public fields and signatures remain in the
[typed proposal](contracts/agent_eval_flow.pyi). The E2E factories supply
implementations through those protocols; their placeholder import paths are
not additional public library APIs.

`objects` defines data and protocol contracts without importing Codex, Claude,
Google SDKs, OpenSRE or OpenKritt. Object methods may dispatch to internal
services at call time. Services operate on those records; optional native SDKs
load only when their adapter is selected. `storage`, `results` and `reporting`
must not depend on a live backend to read an old evaluation.

The `pipeline` module composes the `execution` and `evaluation` services.
Those services do not call the pipeline or public convenience methods back,
and `evaluation` never launches an agent. This gives whole-workflow control
one owner while preserving independently usable stages.

## Pipeline responsibilities at level 1

| Boundary | Behavior |
| --- | --- |
| Construction | Store the study and snapshot registry bindings. Do not execute agents, evaluate metrics, provision infrastructure or take ownership of supplied live clients. |
| `eval()` / `aeval()` | Validate the study, metric source graph, implementation revisions and direct/job capabilities before starting an agent. Create a fresh run set, evaluate it, and return a fresh result. `aeval` awaits native async boundaries; `eval` rejects calls from an active event loop. |
| `eval(runs=saved_runs)` | Grade the supplied capture only. Backend bindings are optional and unused; no missing assignment is executed, no job resumes, and the original run set/plan/IDs remain unchanged. |
| Capture compatibility | Match the task data, candidate definitions, assignment coverage and execution conditions. A changed evaluation suite is allowed; equality of the entire study fingerprint would incorrectly reject rescoring. |
| Repeated calls | An argument-free `eval()` starts fresh work each time. Reuse must be explicit through `runs=`; there is no implicit result cache. |
| Results and storage | Return captured runs, native-grade bundles, identified measurements and grading activities. Retained and newly performed grading resources remain distinguishable. Selection, saving and reporting are explicit subsequent operations. |
| Failure handling | Configuration errors stop before backend work. Execution failures remain run records as specified by the execution contract; the pipeline does not invent success rules or add an extra retry loop. |

For example, grading an old capture needs no native client:

```python
grading = EvaluationPipeline(study=revised_study, evaluators=evaluators)
new_result = grading.eval(runs=saved_runs)
```

The constructor parameters and `eval` signature are in the typed proposal.
Internal coordinator classes, exception types and stage interfaces are now
named in [level 2](LLD_LEVEL_2.md); this document establishes their ownership
at level 1.

## Level 1 — supporting areas

```text
tests/
├── e2e/                          current acceptance cases, support code and fixtures
└── unit/                         [later] focused checks required by implementation

examples/
├── toy/                          [planned] small skill/tool/flow walkthroughs
├── opensre/                      [planned] caller example for its own study
└── openkritt/                     [planned] caller example for its own study

infra/
└── gcp/                          [planned] prepared worker images and environment setup

docs/
├── contracts/                    public typed proposal
├── diagrams/                     existing future-pipeline diagrams
├── research/                     source/fixture review evidence
└── *.md                          design and implementation decisions
```

Test-owned `ScriptedBackend`, `WiringMetric` and `WiringReducer` exercise extension
interfaces. They are not production fallbacks. Live factories connect to
prepared environments and must run actual native programs/services. The
[profiles](../tests/e2e/PROFILES.md) define their setup, capture and cleanup
contracts. Future `infra/gcp` owns provisioning; adapters own stopping their
assigned process/scan and returning its evidence. A cancelled caller must not
be treated as proof a remote job stopped.

## One request through the modules

1. The caller configures `EvaluationPipeline` and calls `eval()` or `aeval()`.
   Preflight resolves definitions and bindings, then execution plans candidate ×
   task × repetition and any declared native-job groups. Agent requests carry
   public inputs. Declared native verifiers receive their separately projected
   private references only through a capability-checked verifier channel.
2. A direct adapter runs one assignment; a native-job adapter delegates its
   whole declared job, including native trial expansion and verification.
   Recorders preserve runs, native IDs and retained grade bundles before cleanup.
   Execution reconciles one run per assignment, including absent outcomes.
3. Evaluation selects retained grades, runs ordinary callbacks or sends a whole
   sequence to a batch evaluator. Each measurement joins to its run and metric;
   each grading cost belongs to one activity. Dependencies reuse measurements;
   custom reducers retain complete assignment coverage.
4. The caller receives `EvaluationResult`. `results` supplies explanations and
   decisions; `storage` saves it; `reporting` renders the loaded result.
5. `pipeline.eval(runs=saved_runs)` with another suite reuses the saved `RunSet`
   and creates a new result.
   Another optimization preference reuses the existing measurements and creates
   a selection. Neither operation requires another native run.

## What stays configurable

The core does not choose which spending counts, whether a timeout is acceptable,
what a correct diagnosis means, or how missing observations affect a score.
Those decisions live in named metrics and policies with recorded versions and
parameters. The E2E wiring values 11/29 and their expected arithmetic exercise
this machinery; they have no scientific interpretation.

Runtime choice is independent: local Codex, local Claude Code, and a tiny Vertex
harness exercise the same output contract. The first OpenSRE cloud case uses
its native Vertex provider. OpenKritt runs on GCP with a provider it actually
supports; hosting it there does not add Vertex support. Their studies and
results stay separate.

## Implementation order established by the tests

1. Make T01's plain subprocess path pass through `pipeline.eval()` across objects, execution, evaluation,
   storage and result consumption.
2. Pass skill/tool/flow, custom dependency/reducer, fault and import cases
   T02–T07, followed by pipeline preflight/reuse and shortcut cases T11–T13.
   Pass the four data-contract cases for native jobs, imports, batch outputs
   and structural consistency before claiming those integration seams work.
   Keep the test assertions; implement the production behavior.
3. Implement one chosen live profile and pass its four T08 modes. Additional
   providers reuse the same tests and record contract.
4. Prepare the dedicated GCP environment explicitly, then pass OpenSRE T09 and
   OpenKritt T10 with native evidence. No diagnosis or detection target is added.

The present tests do not prove cross-machine bundle relocation, arbitrary live
human resume, cross-candidate pairwise judges, corrected-reference rescoring,
or statistical validity. OpenKritt's ZIP branch runs only if native findings
exist; a zero-finding run tests its unavailable-export path. These are explicit
coverage boundaries, not reasons to add those features to this first build.

## Next design levels

The [level-2 design](LLD_LEVEL_2.md) now names the files, services/classes,
interfaces and state ownership within each module. Level 3 will define their
method behavior and data exchanges. Level 4 will specify the remaining
execution, persistence and failure details needed to implement against the
E2E contracts. Levels 3 and 4 have not been written yet.
