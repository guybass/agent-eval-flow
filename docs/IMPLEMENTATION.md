# Implementation checkpoint — 9 September 2026

**Version 0.5 update:** the [unified assessment flow](implementation/unified_assessment.md)
is implemented, with parallel configuration checks and behavioral evaluation,
snapshot binding, explicit gates, shared assessments, independent receipt
accounting and a separate v0.1 saved format. The full suite passes **400 tests**
with **21 existing skips**. Both result formats pass installed-wheel load/report
checks, and the original 90 acceptance/fixture hashes are unchanged. The
historical implementation and native-integration checkpoints below remain valid.

## What works now

Agent Eval Flow is now an installable library. The six objects and `eval()` /
`aeval()` pipeline are implemented: configure a study, execute through bound
adapters or import saved runs, apply single/batch/native checks, save the result,
inspect the evidence, compare candidates and change selection priorities.

The initial implementation suite finished with **278 passed and 21 skipped** on Windows with
Python 3.12.14, including all **207 original offline acceptance cases**. The
skips comprise 20 unselected live cases requiring prepared model/cloud/native
runtime profiles and one POSIX-only process containment test. Additional tests
exercise the new adapters with explicitly test-owned native messages and real
local fixture tools; these do not claim live agent compatibility. All 90 files
in the frozen showcase checkpoint remain unchanged.

The initial implementation's wheel and source distribution built successfully.
Distribution verification confirmed that the wheel matched that checkpoint and
that the source archive preserved all 90 frozen acceptance/fixture hashes. A
fresh wheel installation loaded and validated the saved evaluation and rendered
its HTML report; installed dependency checks also passed. That packaging check
predates the local OpenKritt/OpenSRE examples and their capture corrections below.

The runnable [archive example](../examples/archive_review.py) uses a downloaded
SWE-agent repair history. Its output has one real task, 12 source-linked actions,
three observations containing syntax errors, and a submitted patch. It saves,
reloads and regrades the same capture without invoking an agent. OpenSRE and
OpenKritt remain separate studies with separate native bindings.

The subsequent [local OpenKritt + Codex demonstration](implementation/local_openkritt.md)
completed a real scan of 22 Flaskr files using `gpt-5.6-luna`: eight workflow
executions, four post-processing executions, 20 tool results and two candidate
findings in 472.725 seconds. The native streams, iteration inputs, outputs and
resource receipts are retained; results can be saved, reloaded and regraded
without another agent invocation. Earlier failed integration attempts remain
available. This exercises the pinned local Docker binding.

After these local integration changes, the complete suite passed **310 tests**
with the same **21 skips**. A final focused OpenKritt run passed **45 tests**,
and all 90 frozen acceptance/fixture files were verified unchanged. The live
local scan above was executed through the example, separately from pytest's
unselected cloud and CLI profiles.

The [local OpenSRE + Codex investigation](implementation/local_opensre.md) also
completed on the downloaded 2,000-line HDFS sample: six native ReAct iterations,
eight tool calls, seven Codex CLI invocations and 49 typed runtime events in
123.177 seconds. Its cited report, final native turn, goal result and portable
evidence passed all eight integration checks. Unavailable token and dollar
totals remain unknown. The run exposed and fixed native `frozenset` result
serialization, a checkout normalization issue and a container-receipt label.

After the OpenSRE corrections, the full suite passed **326 tests with 21
skips**, and all 90 frozen acceptance/fixture files remained unchanged. These
local live demonstrations do not select or establish GCP/Vertex compatibility.

## Implemented library boundaries

| Module | Implemented responsibility |
| --- | --- |
| `objects` | Strict Pydantic records; immutable nested configuration; keyed datasets; public/private projections; candidate diffs; canonical fingerprints; relational validation |
| `pipeline` | Inert runtime binding; shared preflight; sync/async execution and grading; compatible saved-capture reuse |
| `execution` | Stable assignments; bounded direct dispatch; whole native jobs; incremental recorders; failures, missing work and imports |
| `evaluation` | Dependency graph; custom single/batch checks; retained native grades; standard primitive metrics; score/gate rules; built-in/custom summaries; grading activity accounting |
| `results` | Explanations; descriptive paired-coverage comparisons; lexicographic, weighted and Pareto selection |
| `storage` | Schema-derived typed manifests; Decimal/time/null fidelity; validation on load; atomic writes; verified evidence cache |
| `reporting` | Self-contained Jinja2 HTML with escaped outputs, resource basis, coverage and evidence links |
| `adapters` | Native translation and lifecycle boundaries described below |

This implementation uses Pydantic, AnyIO, Python's `graphlib`, and Jinja2 rather
than creating validation, async scheduling or template frameworks. The optional
CLI extra uses `jsonschema` to validate structured native responses. Native
Harbor and NAT operations remain owned by their upstream runtimes.

## Native adapters: code versus deployment

| Adapter | Code provided | Binding still required for a live run |
| --- | --- | --- |
| Codex | Noninteractive CLI launch, JSONL tool exchanges, raw final output, native usage, source-linked events | Exact installed CLI revision, declared OpenAI model, existing auth, contained launcher |
| Claude Code | Print/stream-json launch, structured result, tool IDs/results, client-reported cost estimate | Exact CLI revision, declared supported provider, existing credentials, contained launcher |
| OpenSRE | Native `AgentSession` driver; typed event observer; contained process route; TurnResult and incident evidence capture | Pinned checkout, session factory installing the observer and scenario tools, provider/deployment connections |
| OpenKritt | Concrete native HTTP routes; staging; workflow import; one scan lifecycle; step/repeat/tool history mapping; evidence ZIP | Prepared service, scan-scoped metadata/harness collector, native post-script, model/severity-ranker options, confirmed stop controller |
| Harbor | Whole-job adapter and native TrialResult mapper; explicit run/trial IDs; verifier activities and multi-step details | Pinned Harbor runtime/schema, native job configuration builder and task/environment fixtures |
| SkillEvaluator | Paired archive import through retained Harbor results, explicit identity index and source hashes | Genuine paired export and the matching native schema validator |
| NAT | Native ATIF batch call and result mapper; one grading activity; no replacement agent loop | Pinned `nvidia-nat-eval`, configured native evaluator and actual ATIF trajectories |

The application-owned [Vertex showcase harness](../examples/integrations/vertex.py)
adds a bounded SDK/tool loop for the toy and downloaded-source scenarios. It
records each SDK request/response and each actual tool result, with automatic
function calling disabled. Its factory is
`examples.integrations.vertex:make_backend`; it runs **on** a prepared GCE worker,
checks the metadata-server project/instance/image label and the installed
`google-genai` pin, and uses ADC. A local client can instead use the prepared
worker transport. This example is separate from the library's evaluation engine.

These are working adapter boundaries with offline tests, not claims that every
upstream version or hosting configuration works. A runtime factory connects
existing deployment resources. The library does not provision a GCP project,
install arbitrary upstream versions, create credentials or silently replace a
missing integration with the scripted backend.

The [CI workflow](../.github/workflows/tests.yml) runs offline tests, checkpoint
verification, the archive example and package builds on Windows/Linux with
Python 3.11–3.13. It has been added but has not been executed on GitHub here.

`PreparedWorkerClient` provides single submission, validated progress, cancellation
and stop acknowledgment, cleanup, artifact download/hash verification and recursive
evidence relocation. The transport is supplied by the prepared deployment. It
does not resubmit an ambiguously acknowledged job.

## Run locally

```bash
python -m pip install -e ".[test,cli]"
python scripts/verify_acceptance_checkpoint.py
python -m pytest
python examples/archive_review.py --output demo-output
```

For a live local showcase, start from
[the current profile template](../examples/profiles.local.example.json).
`examples.integrations.local_cli:make_backend` binds the native CLI and the
application-owned [showcase recipe](../examples/integrations/local_cli.py).
That recipe stages the real source fixture, provides actual terminal tools,
enforces fixture dispatch count and packages source-linked native receipts.
The toy and dependency-investigation missions are not built into the library's
metric engine.

Declare the model in `settings.model` as well as the profile's model metadata;
the former participates in candidate identity. Fill exact runtime/model values
and keep credentials in the runtime's supported authentication mechanism.
Unknown native CLI options fail preflight. Put nonbehavioral application labels
under `settings.metadata`, or declare consumed fields in a scenario binding.
Successful invocation workspaces are removed after their evidence is retained;
failed or incompletely stopped workspaces remain available for investigation.

```bash
python -m pytest tests/e2e --aef-live codex_local --aef-profile-config profiles.local.json
```

The built-in process launcher uses POSIX process groups. On Windows, supply
`launcher_factory="your_module:factory"` returning a verified Job Object
launcher, or use a prepared worker. Without containment, hard wall-time support
is false and dispatch fails before agent work. The Windows host used for this
initial checkpoint therefore did not run the direct native Codex showcase. The
subsequent OpenKritt example runs Codex inside its dedicated Docker deployment.
Claude Code and
`gcloud` were not installed, and no live profile was present.

The new OpenSRE/OpenKritt profile hooks are
`agent_eval_flow.adapters.opensre:make_backend` (`runtime_factory`) and
`agent_eval_flow.adapters.openkritt:make_backend` (`connection_factory`). Their
factories require the prepared bindings described in the table. The original
[live acceptance contracts](../tests/e2e/PROFILES.md) remain unchanged historical
test-first specifications, including the richer observer requirements.
The [native workflow adapter guide](implementation/native_workflow_adapters.md)
documents the concrete session, API, collector and deadline interfaces to supply.

## Practical limits

- Comparisons provide descriptive differences and disclose unmatched coverage.
  Confidence intervals have no supported method in contract 0.4; the library
  reports that limitation when requested.
- CLI aggregate usage does not establish complete native child/retry inventory.
  Unknown inventory or missing resource fields cannot become zero totals.
- Codex tokens do not imply a dollar bill. Claude's `total_cost_usd` is retained
  as an estimate, consistent with its [current native documentation](https://code.claude.com/docs/en/headless).
- Artifact references preserve evidence locations. Manifests do not automatically
  copy every external file into a portable archive; the showcase recipes create
  explicit evidence bundles when that is required.
- This is an initial implementation verified on this Windows/Python 3.12 host,
  including live OpenKritt/Codex and OpenSRE/Codex workflows in local Docker.
  GCP/Vertex and optional-library live acceptance results remain to be established
  against the prepared pinned runtimes.
