# Agent Eval Flow: tests before implementation

This is the acceptance suite for the approved revision-0.4 scope. It tests the
library's public behavior, data contracts and integration boundaries. Fixture
values check transport and the documented arithmetic helpers; they do not
establish agent quality or scientific validity of a metric.

The user requested completion of these specifications **before** completing
the module LLD. The frozen collection inventory and source hashes in
`ACCEPTANCE_MANIFEST.json` record that checkpoint. No production substitute is
allowed: every library test must import the real `agent_eval_flow` package.

The later [showcase revision](e2e/SHOWCASE.md) strengthens the E2Es with downloaded
real inputs and a published multi-step agent execution. The original manifest
is historical; current coverage and validation are recorded in [BASELINE.md](BASELINE.md).
Independent fixture-integrity checks can pass before the library exists; they
do not establish an integration pass.

## Coverage map

| Contract area | Acceptance specification | Observable requirements |
| --- | --- | --- |
| Configuration and datasets | [test_configuration.py](contracts/test_configuration.py) | Typed keys, table joins, public/private projections, selection, defensive copies, candidate derive/diff, native dialects, stable plans, native-job ownership and limits |
| Agent capture | [test_capture.py](contracts/test_capture.py) | Valid null versus missing output, every status bucket, unknown/estimated resource facts, exclusive child costs, timestamps, identity graphs, partial native outputs and rich imports |
| Whole native jobs | [test_native_jobs.py](contracts/test_native_jobs.py) | Private verifier projections at dispatch, disjoint native/direct groups, capabilities, failure recovery, recorder reconciliation and fresh invocation IDs |
| Evaluation | [test_evaluation.py](contracts/test_evaluation.py) | Dependency reuse, errors and malformed responses, full batch reconciliation, native grade selection, evidence, activity ownership, shared cost and grading inventory |
| Reducers | [test_reducers.py](contracts/test_reducers.py) | Built-in summaries, custom callback inputs, invalid result containment, complete source populations and explicit exclusions |
| Runtime facade | [test_pipeline_runtime.py](contracts/test_pipeline_runtime.py) | Async entry, rejecting nested sync entry, binding snapshots, saved-run compatibility, capability preflight and actual concurrent subprocess dispatch |
| Result consumers and storage | [test_results_storage.py](contracts/test_results_storage.py) | Rubric contributions, acceptance states, estimate propagation, three selection modes, ties/unknowns, comparison, typed serialization, invalid manifests and escaped HTML |
| Complete small workflows | [toy pipelines](e2e/test_toy_pipeline.py), [job/batch lifecycle](e2e/test_data_contract.py) | One configured eval call, real owned subprocesses, skills/tools/flow evidence, custom checks/reducer, saving/loading, reporting, changed suites and unchanged agent captures |
| Codex, Claude Code, Vertex | [live workflow](e2e/test_live_workflow.py), [small smoke cases](e2e/test_live_toy.py) | Execute real dependency cases, inspect downloaded source, retain actual tool rounds and a report/bundle with resolvable evidence |
| Published agent history | [SWE-agent archive](e2e/test_archived_trajectory.py) | Twelve genuine actions, failed edits/corrections, observations, patch and historical usage survive import/evaluation/storage |
| OpenSRE on GCP | [OpenSRE E2E](e2e/cloud/test_opensre_gcp.py) | Investigate downloaded HDFS logs through native tools/iterations; preserve report, raw runtime/tool events and evidence bundle |
| OpenKritt on GCP | [OpenKritt E2E](e2e/cloud/test_openkritt_gcp.py) | Review a downloaded multi-file app across repeated stages; preserve lineage, harness activity, raw findings and bundles |
| Optional execution/grading libraries | [optional-library cases](integrations/test_optional_libraries.py) | Real Harbor native job, genuine SkillEvaluator retained study and actual NAT batch-grading paths, each explicitly selected with versioned fixtures |

## Running

```text
python -m pytest --collect-only -q
python -m pytest tests --tb=short -q
python -m pytest tests/contracts tests/e2e/test_toy_pipeline.py tests/e2e/test_data_contract.py
```

Use the existing `requirements-test.txt`. The shared fixture imports the future
package only when a test runs. Tests using Pydantic's validation exception do
so only after that import; collection needs no production dependency.

The default run is intentionally RED while the package is absent. Live cases
are skipped only when their profile is unselected. A selected profile cannot
pass through a scripted fallback or skip unavailable credentials/fixtures.
See [native CLI/GCP profiles](e2e/PROFILES.md) and
[optional-library profiles](integrations/PROFILES.md).

`tests/contracts` holds focused public-boundary acceptance tests, not a second
implementation. It may use actual test-owned adapters/evaluators and real toy
subprocesses. Helpers may supply records, fault injection and assertions; they
must not perform the library's scheduling, joins, scoring, storage or selection.

## Deliberate boundaries

The suite covers the approved synchronous/async facade, fixed repetitions,
captured human-work observations, local result storage and explicitly configured
integrations. Live human pause/resume, adaptive trial generation, automatic
cloud provisioning, pairwise judging, statistical interval estimators and
training an optimizer are outside revision 0.4. Comparison must report an
unavailable interval rather than inventing one.

Source collection and field-name checks do not execute the assertions behind
the missing-package fixture. The checkpoint reports this limitation explicitly;
green behavior requires the subsequent real implementation and prepared live
environments. Each native compatibility claim also needs its selected profile
to pass against recorded upstream versions and genuine artifacts.
