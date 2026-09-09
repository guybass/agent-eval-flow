# E2E acceptance tests written before implementation

These tests answer: **can a caller run or import a real workflow and correctly
use its outputs, tool/iteration history, evidence and evaluation objects?** They do not measure whether a skill improves an
agent, a vulnerability was found, a diagnosis was correct, or a metric is
scientifically valid. Known fixture values check callback/data plumbing only.

There is no production package or live adapter implementation yet. Default
execution must therefore be **red**, not silently skipped or satisfied by a
stand-in library. Collection works without importing `agent_eval_flow`.

Start with the [real-workflow showcase](SHOWCASE.md) and its downloaded inputs.
The original tiny cases remain useful smoke coverage. A final answer alone does
not satisfy the native investigation/review scenarios.

## Cases already written

| ID | Test | Mission / input | What must work |
| --- | --- | --- | --- |
| T01 | `test_small_harness_to_consumable_result[plain]` | Echo two tiny task records | Plan 2 candidates × 2 tasks × 2 repetitions; produce 8 distinct runs; preserve public input keys |
| T02 | Same test, `skill` | Load the owned formatting skill | Loaded skill hash and evidence survive into result/explanation; no tool required |
| T03 | Same test, `tool` | Call owned `fixture.echo` | Actual tool arguments/result enter events and link to the delivered output |
| T04 | Same test, `flow` | Load skill, call tool, return receipt | Ordered stage events, output lineage, one root assignment per episode |
| T05 | `test_rescore_saved_runs_without_rerunning_backend` | Grade a saved capture through a pipeline with another suite and no backend binding | New result and suite fingerprint, original capture/plan unchanged, no backend call |
| T06 | `test_process_failures_remain_usable_records` (`fail`, `timeout`) | Owned process exits nonzero or exceeds its timeout | Every assignment remains represented; errors/artifacts/timing serialize; configured callbacks still apply |
| T07 | `test_import_protocol_preserves_missing_assignments` | Import a saved capture stream with one omitted run | Importer invoked through public API, explicit unobserved placeholder, correct coverage and unknown resources |
| T08 | `test_native_small_mission_returns_usable_objects` | Same four small modes through Codex, Claude Code or Vertex | Real native trace/receipt; structured output; skill/tool path evidence; common consumer contract |
| T09 | `test_native_opensre_on_gcp_produces_usable_library_objects` | Investigate 2,000 downloaded HDFS log lines through six tools | Native OpenSRE iterations, paginated observations, exact raw/normalized events, evidence-linked report and retrieved ZIP |
| T10 | `test_openkritt_scan_round_trips_native_outputs_on_gcp` | Review downloaded 22-file Flask tutorial across four steps/three depths and two native passes | Genuine step outputs/lineage, raw harness tool history, findings fidelity, portable evidence package and conditional native findings export |
| T11 | `test_pipeline_preflight_fails_before_any_backend_call` (three configurations) | Omit an evaluator/reducer or bind the wrong evaluator revision | Actionable configuration failure before any agent or evaluation callback |
| T12 | `test_pipeline_reuse_starts_fresh_runs_without_hidden_cache` | Call the same configured pipeline twice | Fresh capture/result/run identities and the expected number of backend calls |
| T13 | `test_one_call_convenience_returns_the_same_public_result_contract` | Use the top-level `evaluate(...)` shortcut for one candidate | Usable, persistable result with the same evaluation and evidence contract |
| T14 | `test_native_job_reconciles_reversed_partial_export` | One owned job executes eight subprocess assignments and exports seven in reverse order | One native job call, ID-based joining, explicit missing run with allocated ID, retained job links, backend-free regrading |
| T15 | `test_batch_grading_joins_by_id_and_charges_one_activity` | One batch grades four subprocess outputs | Batch identity, reversed output joining, derived check, one shared charge, persistence |
| T16 | `test_boolean_task_key_is_rejected_before_identity_coercion` | Supply a boolean as a root task key | Reject the identity before it can become integer 1 |
| T17 | `test_retained_native_grades_share_cost_without_new_grading` | Load four native grade receipts sharing one historical activity | Native values/evidence survive persistence, no callback dispatch, historical expense once and zero incremental grading cost |
| T18 | `test_published_multistep_trajectory_survives_import_evaluation_and_storage` | Downloaded SWE-agent Pydicom execution with 12 actions and a submitted patch | Import genuine failed edits/corrections, preserve observations/source locations and historical usage, measure/save/load/re-evaluate without replay |
| T19 | `test_native_dependency_investigation_preserves_workflow_and_deliverables` | Six concrete cases against downloaded MarkupSafe source | Actual reproduction then source-inspection tool rounds, native dialect evidence, report consistency metric, bundle and normal result consumers |
| Preparation | `test_downloaded_workflow_inputs_execute_real_dependency` | Verify pinned files and execute the case tool locally | Hash/license integrity and genuine dependency execution; reject altered/missing/stale report evidence; this is not a library integration pass |

T01–T07 and T11–T13 use a **test-owned deterministic subprocess harness** and the actual
library once implemented. This is an offline E2E path, not a mock of the six
objects and not a claim of native Codex/Claude/Vertex compatibility. The fixture
tool is an actual function invoked inside that process; the process can be
killed and reaped without leaving a remote job.

T14–T17 are the [revision-0.4 data contract cases](test_data_contract.py).
The job and batch plugins are test-owned boundary implementations, not Harbor
or NAT compatibility claims. T14, T15 and T17 use actual owned subprocesses;
T16 is a focused public-boundary validation case. T18 adds an offline archived-data
E2E; T19 adds three live profiles plus one independent fixture-preparation check.
Current counts are recorded in the [full-suite baseline](../BASELINE.md).

T08 is 12 live cases: four modes × three profiles. T09/T10 are separate studies,
each using two fresh candidate runs per task to exercise comparisons. Candidates
differ in a fixture tag, not in a hypothesis about which agent is better.

## The common consumer contract

The toy pipelines and the new job/batch paths use `assert_usable_result`, which:

1. Joins runs to planned assignments and accesses `get`, `for_candidate`,
   `rows`, and `coverage` through the public API.
2. Reads source-linked explanations and keyed diagnostic measurements; confirms
   that a derived metric receives its dependency and a custom reducer receives
   assignment identities and parameters.
3. Calls `summary`, `compare`, and two selection policies. The offline fixture
   verifies that this starts neither another agent run nor another evaluation.
4. Saves/loads `Study`, `RunSet`, and `EvaluationResult`, preserving identities,
   output contents, measurements, evidence links and Decimal resource values.
5. Uses the loaded result again and produces a readable HTML report file.

`support.py` supplies only public extension protocols and assertions. It must
never grow its own evaluation engine, storage implementation, result classes
or ranking implementation to make tests pass. The import case uses a saved
library-capture stream to exercise the importer seam; it is not an upstream
OpenSRE/OpenKritt importer test. Native mapping is covered by T09/T10.
The retained-grade-only case checks its narrower result directly, without
requiring an unrelated derived metric. All use the same proposed public API.

## Run the tests

Use Python 3.11+ and install `requirements-test.txt` in a local environment:

```text
python -m pip install -r requirements-test.txt
python -m pytest --collect-only -q
python -m pytest tests/e2e/test_toy_pipeline.py
```

Until the production package exists, the last command must fail with
`RED: agent_eval_flow is not implemented/installed yet`. Once it exists, install
the checkout into the same environment; no test should import the `.pyi` proposal
as a runtime substitute.

Live execution requires an explicitly selected profile and an already prepared
runtime. See [PROFILES.md](PROFILES.md). The factory is an integration dependency
still to be implemented, not a test helper that may fabricate native results.

```text
python -m pytest tests/e2e/test_live_toy.py --aef-live codex_local --aef-profile-config profiles.local.json
python -m pytest tests/e2e/cloud --aef-live opensre_gcp --aef-live openkritt_gcp --aef-profile-config profiles.local.json
```

Unselected live cases are explicitly skipped. An explicitly selected profile
with missing configuration, credentials or dependencies fails; it cannot turn
green through a skip or fallback. The checked-in configuration is a template
only. GCP projects, service accounts, enabled model IDs, worker/container images
and upstream versions must be supplied before running; writing tests does not
create them or spend model credits.

## Scope and failure interpretation

A positive live case requires the native transport and its declared execution
path to complete, including the actual tool/stage evidence in showcase cases. Invalid credentials or a broken native server are failed
integration setup, not a negative scientific score. Wrong diagnoses or an empty
findings list are not failures of these tests. Dedicated offline fault cases
verify that error outputs remain usable.

There are no accuracy floors, model rankings, expected vulnerability detections,
statistical-significance assertions or inferred missing bills. Counts, IDs,
fixture arithmetic and hashes establish deterministic API behavior. The native
fixtures document actual upstream fields separately from our proposed adapter
normalization: [OpenSRE](fixtures/opensre/README.md),
[OpenKritt](fixtures/openkritt/README.md).
