# Local OpenKritt + Codex demonstration

The demonstration runs the native OpenKritt engine against 22 pinned Flaskr
tutorial files. Its four workflow steps map the application, review author
access, review database/rendering flows, and reconcile findings. Each step has
two native repeats. The source-only mission forbids running the application,
tests, or proofs of concept. Findings are retained as native reports, without
claiming that this integration validates their security accuracy.

OpenKritt is pinned to `1ba10d410b4a0a309bacf5bc2d14ec1c27ed8a09` and its image
installs Codex CLI `0.145.0`. The local model is selected from OpenKritt's actual
account-specific catalog. Model, reasoning effort, severity policy, workflow
bytes, observer revision and engine image identity are retained with the run.

## Observed execution — 9 September 2026

The completed native scan is `2`, using `gpt-5.6-luna` with low reasoning effort.
OpenKritt ran locally in Docker; Codex used its hosted model. The scan completed
eight workflow executions and four post-processing executions, retaining 20
actual command results. Elapsed time was 472.725 seconds. Native receipts report
551,363 input tokens and 7,756 output tokens; the dollar charge is unknown.

The final revision-4 assessment passed all eight integration checks (100/100).
Saved-result reload and reweighting passed with zero new agent calls. Its
complete ZIP has 295 indexed files and 66 exact evidence-reference mappings,
including the original archive. These checks establish the observed behavior
of this integration scenario, rather than agent quality across a benchmark.
The final regression suite passed 310 tests with 21 skips; 45 focused OpenKritt
checks passed, and the 90 frozen acceptance/fixture files remained unchanged.

The agent reported two candidate findings: absent CSRF protection on
state-changing routes, and a predictable default secret key if deployment
configuration does not override it. This source-only run did not independently
establish their correctness or exploitability.

The [local native scan](http://127.0.0.1:15173/scans/2) remains available while
the dedicated stack is running. The generated result index is
`demo-output/openkritt-results/index.html`; it links the saved objects, native
prompts and structured outputs, and preserves the earlier failed attempts.

## Local deployment binding

Use a dedicated Docker Compose project, such as `aef-openkritt-local`, with
OpenKritt's native database, backend, executor-view and engine. The connection
verifies the project labels and that the running engine matches its clean,
pinned source. The example's deadline controller stops only that dedicated
engine and its positively identified runner containers.

Mount `examples/integrations` read-only at `/aef` in the engine and launch:

```text
python /aef/openkritt_observer.py
```

The observer retains the actual harness stdout/stderr, including partial output,
outside OpenKritt's temporary job directories. It preserves the native prompts,
commands, model routing and output parser. This is necessary because the pinned
upstream discards successful harness streams after parsing them.

Configure the native local-repository and engine-data mounts, including
`ENGINE_DOCKER_DATA_DIR_HOST` for nested runner containers. Use OpenKritt's own
Codex login/import flow; keep credentials outside evaluation artifacts. For the
prepared Windows demonstration, the UI is on `http://127.0.0.1:15173` and the API
is on `http://127.0.0.1:13002`.

Prepare configuration from the running native API:

```powershell
.venv\Scripts\python.exe -m examples.integrations.openkritt_setup --upstream-dir test-artifacts/openkritt-upstream --engine-data-dir test-artifacts/openkritt-upstream/.data/engine --local-repos-dir test-artifacts/openkritt-upstream/local_repos --output test-artifacts/openkritt-local.json
```

This selects an available Codex model, records the configuration responses,
and creates/reuses a named source-only post-script. It starts no scans.

## Execute and inspect

```powershell
.venv\Scripts\python.exe -m examples.openkritt_local_review --config test-artifacts/openkritt-local.json --output demo-output/my-openkritt-run
```

Choose a fresh output directory for each attempt. The output includes the study,
typed saved result, an overview, a detailed HTML report, a verified evidence ZIP,
and a machine-readable summary. Saving, reloading and regrading reuse the capture
without invoking another agent. Missing billing remains unknown.

The 100-point score checks eight parts of the integration: native completion,
planned workflow attempts, inventory, per-attempt streams, lossless tool receipts,
branch inputs, the evidence bundle, and agreement with native outputs. It is not
an accuracy score. A synthesis step may use earlier outputs without issuing a
new command; all commands actually issued must retain their results and traces.

## Issues exposed by the live run

The original frozen workflow reused output-field names across levels. Native
OpenKritt rejected its import. A second attempt reached Codex but its response
schema rejected free-form object fields generated by the pinned upstream.

The explicit [version 2 workflow](../../examples/workflows/openkritt_flaskr_local_v2.json)
uses unique output names and arrays of text for observations and evidence.
The original fixture, version 1, and failed results remain preserved. The
[workflow provenance](../../examples/workflows/README.md) records these changes.
Regression checks exercise the actual upstream workflow validator and schema
generator. These changes belong to this application's native configuration;
they do not restrict the library's general data model.

The first assessment of the completed scan incorrectly rejected native repeat
inputs: it searched for bare answers, whereas OpenKritt records each answer in
a `repeat_run` / `result` envelope. The corrected evaluator parses the actual
JSON and checks its producing step, input and repeat identities. Regression
cases derived from the real public-source scan reject removed answers and
altered lineage. Reassessment reads retained evidence without running Codex.

The final archive audit also found that the original ZIP omitted inventory and
deployment receipts referenced by the saved result. The bundle builder now
includes nested evidence references and their exact bytes, hashes, locators and
descriptions. Offline completion creates a derived capture with a new
fingerprint, includes the original ZIP unchanged, and preserves every native
execution, event and output. Evaluator revision 4 verifies that complete
provenance; it rejects an internally valid ZIP with missing receipts.

Reproduce the offline completion and assessment into a fresh directory:

```powershell
.venv\Scripts\python.exe -m examples.openkritt_local_regrade --source demo-output/openkritt-local-run3 --output demo-output/openkritt-local-complete --complete-evidence
```

To inspect several attempts together without running agents:

```powershell
.venv\Scripts\python.exe -m examples.openkritt_attempts_report --runs demo-output/openkritt-local demo-output/openkritt-local-run2 demo-output/openkritt-local-run3 --assessment demo-output/openkritt-local-complete --output demo-output/openkritt-results/index.html
```

The index treats them as attempts at one scenario, preserves their failures,
and does not aggregate incomplete resource values into a misleading total.
The separate assessment is identified as offline archive completion and
regrading of scan 2, rather than another agent execution.
