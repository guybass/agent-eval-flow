# Agent Eval Flow: what to build, reuse and challenge

**Supplementary review.** This comparison focused too much on general evaluation
libraries for the requested LLD audit. Start with the corrected
[LLD reuse red team](LLD_REUSE_RED_TEAM.md), which returns to the original
agent/skill/tool/harness projects and papers and maps their actual implementations
to our proposed services. The findings below remain background; they are not
the primary justification for our architecture.

**Research date: 8 September 2026.** This reviews the
[level-2 proposal](LLD_LEVEL_2.md), not an implemented product. The
[communication map](MODULE_COMMUNICATION.md) shows the current design.
Recommendations below are proposals for discussion before level 3; they do
not silently replace the public contract.

## The conclusion in under 200 words

Our current design is mostly a custom library around existing agent systems.
It is not yet built on an existing evaluation engine. We have tests and design
files, with no production package, so a percentage of reused code would be made
up.

I would build our experiment records, evidence joins and comparison experience,
while reusing schema validation, graph ordering, native clients, established
graders and compatible runners. A wrapper still needs real engineering when it
must preserve failures, usage, execution identity and artifact links.

Inspect, Pydantic Evals, Promptfoo and MLflow already cover much of the basic
evaluation experience. Our benefit must be that a developer can compare harness
changes and inspect the supporting evidence more easily through our contract.

The main design risks are duplicated orchestration, a per-assignment interface
that does not fit batch runners, and expensive graders without a clear lifecycle.
These are library concerns, independent of how a user defines accuracy or cost.
Keep the six objects and simple `eval()` experience; prove the integration
boundaries before committing to every box in the tree.

## How much is ours, concretely?

There are **38 planned Python files** in the level-2 library tree, including
the root `__init__.py` and excluding omitted package initializer files. None is
implemented under `src/`. The checked-in dependency requirement is currently
only pytest for tests. No evaluation engine has been selected as a dependency.
These are repository observations, not effort estimates.

Separate three different questions:

- **Whose code performs the agent's work?** Existing OpenSRE, OpenKritt, native
  CLIs and provider SDKs. We should not reimplement those agents.
- **Whose code presents our experiment and result contract?** Ours, including
  mapping, validation, coverage and evidence joins.
- **Are the underlying evaluation ideas already available?** Largely yes.
  Existing capability does not imply that its public types are drop-in
  replacements for ours.

| Proposed area | What we should own | What to reuse / wrap |
| --- | --- | --- |
| `objects/` | Domain fields, identity, reference integrity, coverage and deep immutability | Dataclasses/typing and Pydantic `TypeAdapter` for validation/serialization; retain dataclass behavior required by existing tests. |
| `pipeline/` | Our entry point, compatibility checks and one orchestration owner | Delegate compatible native evaluation jobs; avoid nesting whole runners per metric. |
| `execution/` | Assignment identities and reconciliation with captured runs | Native runner scheduling/lifecycle when it owns the batch; a minimal executor for simple supplied backends. |
| `evaluation/` | Our metric dependency contract and output/evidence assembly | Existing individual scorers; `graphlib.TopologicalSorter` for ordering rather than a new DAG library. |
| `results/` | Defined comparison, explanation and selection over our saved records | Standard numeric/data primitives; established statistical methods when that extension is introduced. |
| `adapters/` | Translation, native evidence, version checks, staging and truthful capability claims | Supported upstream APIs/CLIs/SDKs. These are integration wrappers with operational responsibilities. |
| `storage/` | Versioned manifest, identity preservation and artifact reference rules | A mature codec plus standard JSON/hash/archive primitives. No new database in the first build. |
| `reporting/` | Report content and links to explanations | Jinja with explicit HTML escaping; optional exports to existing viewers. |

Pydantic supports dataclasses through `TypeAdapter`; Python already provides
dependency ordering; Jinja provides configurable escaping. Those mechanisms
do not implement our domain invariants or report meaning.
[TypeAdapter](https://pydantic.dev/docs/validation/latest/concepts/type_adapter/),
[graphlib](https://docs.python.org/3/library/graphlib.html),
[Jinja](https://jinja.palletsprojects.com/en/stable/api/#autoescaping).

My recommendation is a small core with optional integrations. Pydantic and
Jinja are proposed reusable foundations, not dependencies installed by this
review. An Inspect or MLflow integration should not be imported merely to load
an old result. The exact pinned package set still needs a compatibility check.

## What established open-source projects actually do

The useful comparison is their public boundaries and operational behavior,
not whether their directory names resemble ours.

| Project | Verified existing design | Difference / implication for us |
| --- | --- | --- |
| Inspect AI | `Task` joins dataset, solver and scorer; typed logs support later scoring. | Substantial runner/logging/scoring reuse potential. Our candidate/assignment/evidence contract would still need a faithful mapping. [Tasks](https://inspect.aisi.org.uk/tasks.html), [rescoring](https://inspect.aisi.org.uk/scoring-workflow.html). |
| Pydantic Evals | Dataset evaluates a callable; typed contexts and reports; sync/async tasks and custom evaluators. | Excellent simple-callable baseline. Our explicit dependency graph and captured-run records are additional integration work. [Dataset API](https://pydantic.dev/docs/ai/api/pydantic_evals/dataset/), [evaluator API](https://pydantic.dev/docs/ai/api/pydantic_evals/evaluators/). |
| Promptfoo | `evaluate` composes providers and assertions; providers can call applications; exports support existing workflows. | Good interoperability target. Its Node runtime and Python bridge are an explicit dependency boundary, not a native drop-in Python engine. [Node API](https://www.promptfoo.dev/docs/usage/node-package/), [Python providers](https://www.promptfoo.dev/docs/providers/python/). |
| MLflow GenAI | Prediction and scorer seams, trace-aware evaluation, evaluation of stored traces. | Useful graders and trace/result integration. Its tracking ownership must be explicit if used underneath our local library. [Stored-trace evaluation](https://mlflow.org/docs/latest/genai/eval-monitor/running-evaluation/traces/), [evaluation implementation](https://mlflow.org/docs/latest/api_reference/_modules/mlflow/genai/evaluation/base.html). |

This is not evidence that none of them can support our workflow. It is evidence
that “agents + configurable metrics + eval() + reports” is already common.
Our proposed coherent experience across harness changes is a product hypothesis
to test, not an established feature gap in every other project.

## Red-team attacks, with concrete examples

### 1. Our backend unit is too narrow for some existing runners

**Evidence:** at Inspect commit `3fea5104189022519d8e432950bc73f37103848e`, its
runner rejects concurrent `eval_async` invocations in one process. Concurrency
belongs inside a native evaluation invocation.
[Pinned guard](https://github.com/UKGovernmentBEIS/inspect_ai/blob/3fea5104189022519d8e432950bc73f37103848e/src/inspect_ai/_eval/eval.py#L744-L753).

**Attack:** our executor dispatches two assignments concurrently. A naive
`InspectBackend.run()` calls Inspect once for each. One invocation fails, or the
adapter serializes everything and loses the native scheduling benefit.

**Change proposed:** import native logs first. Before adding live Inspect
execution, test one native invocation handling a batch. A plan/batch execution
interface may be necessary; it is not yet part of our accepted public contract.
The current per-assignment adapter remains useful for simple subprocess agents.

**Proof:** two tasks and two repetitions preserve native IDs and planned
coverage with exactly the declared work. No duplicate scheduler or retry owner.

### 2. A wrapper can change the number of real executions

**Evidence:** MLflow documents an extra predictor invocation for trace validation,
with a control to disable it. Promptfoo caches responses by default; its current
documentation uses separate repeat namespaces, but later evaluations can reuse
those cached responses unless freshness is requested.
[MLflow prediction validation](https://mlflow.org/docs/latest/genai/eval-monitor/faq/#why-does-mlflow-make-n1-predictions-during-evaluation),
[Promptfoo caching](https://www.promptfoo.dev/docs/configuration/caching/).

**Attack:** we promise two fresh OpenKritt scans. An embedded runner performs a
third validation scan, or a second pipeline call returns cached work under new
run IDs. We misstate what was executed even if the user's scoring formula is
perfect. This is an integration trap, not a claim that caching is inherently bad.

**Change proposed:** one execution owner. Import existing runs or wrap individual
scorers first. A whole-runner integration must explicitly map cache hits,
validation calls, retries and native identities into the declared experiment.

**Proof:** a fixture counts actual invocations across repeated pipeline calls;
freshness assertions inspect receipts, not only generated IDs.

### 3. The evaluator lifecycle is less defined than the agent lifecycle

**Our contract:** `ExecutionPolicy` belongs to agent runs. `MetricEvaluator.compute`
is synchronous and reports evaluation resources, but exposes no evaluation
deadline, cancellation or retry policy.

**Attack:** OpenSRE finishes in 30 seconds. Its remote grading call stalls for
40 minutes. The agent's 120-second budget does not bound grading, and putting
the callback in a thread does not make that work safely cancellable. Python
documents that a running future cannot simply be cancelled.
[Future cancellation](https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.Future.cancel).

**Change proposed:** define evaluation execution controls separately from
metric meaning at level 3. Choose an async/lifecycle seam or explicitly limited
supervision; keep the simple sync facade. Do not promise hard limits for arbitrary
in-process callbacks merely because a timer stops waiting.

**Proof:** a deliberately stalled evaluator returns/raises the specified
evaluation failure, preserves agent evidence, and records whether grading
actually stopped. No assertion about whether the metric itself is scientifically good.

### 4. One upstream judge may return several useful measurements

**Evidence:** an MLflow scorer can return multiple feedback objects. Our current
`MetricOutput` has one task measurement and optional keyed diagnostic rows.
[Scorer outputs](https://mlflow.org/docs/latest/genai/eval-monitor/scorers/custom/#outputs).

**Attack:** an existing judge returns correctness, citation quality and clarity
in one call. Three thin wrappers invoke it three times, increase expense and
produce scores from different judgments. Our dependency mechanism avoids
repeating a declared metric, but it has no explicit typed multi-output bundle.

**Change proposed:** test one captured feedback bundle with projections before
choosing a new interface. Artifacts/text can carry it today, but that should be a
documented mapping with one resource owner, not a hidden global cache. Add a
multi-output operation only if the existing seam proves awkward in practice.

**Proof:** one native judge call produces three named measurements sharing
evidence, with one recorded charge and no cross-run state leakage.

### 5. The GCP wrapper is a real subsystem hiding behind a small name

**Our design:** `PreparedWorkerClient` must stage files, invoke native work,
transfer evidence and confirm stopping. A `run()` method signature does not
make those jobs trivial.

**Attack:** a network disconnect ends OpenKritt polling while its remote scan
continues. A local timeout is then presented as a confirmed job stop. Or an
evidence ZIP is downloaded while event/measurement links still point at the
worker's private path.

**Change proposed:** retain native lifecycle supervision or select an existing
job runtime instead of inventing a general cloud scheduler. Define capabilities
per integration and preserve unknown stop/accounting state. The current design
already recognizes these rules; they remain unproved integration obligations.

**Proof:** interrupt a toy remote job; distinguish requested stop from confirmed
stop; check all evidence references after materialization. This is protocol
testing, independent of whether a timeout deserves a low score.

### 6. Reusing a schema library does not fulfill our data promises

**Evidence:** Pydantic explicitly shows that a dictionary inside a frozen model
can still change. Our proposal promises deep immutability and stable fingerprints.
[Frozen model behavior](https://pydantic.dev/docs/validation/latest/concepts/models/#faux-immutability).

**Attack:** after planning, caller code mutates a nested tool setting through a
retained dictionary. Execution uses changed settings while the old fingerprint
labels the run. Separately, deleting a temporary artifact directory leaves a
saved result with valid scores and broken evidence links.

**Change proposed:** reuse schema machinery but own deep normalization and
cross-record validation. Keep the present records-plus-references storage
limitation explicit; a future portable bundle needs its own relocation proof.
Neither issue is fixed by selecting a different accuracy metric.

## What a well-designed first release should look like

The following are my engineering recommendations from the comparison, not a
claim that every reviewed repository implements every item perfectly:

1. **A small entry point and small extension surface.** Common callable/tool
   tasks should not require hand-building dozens of evidence records. Provide
   helpers with honest unknown observations; retain the richer protocol.
2. **One owner for each lifecycle.** Exactly one runner controls native jobs;
   exactly one evaluation coordinator controls callback reuse. Keep tracking,
   deployment and provider clients explicit.
3. **Use mature mechanisms underneath domain rules.** Schema parsing, graph
   ordering, templates and process/HTTP clients are existing work. Our core
   enforces identity and evidence promises around them.
4. **Optional integrations and versioned compatibility.** Reading saved data
   should not require cloud credentials. Document supported upstream revisions,
   observed/unknown fields and unsupported guarantees. Avoid private upstream APIs.
5. **Tests that support the public claims.** Installation/import checks, typed
   API checks, native-format fixtures and focused lifecycle/serialization tests
   complement the existing E2Es. Publish limitations and a contribution path.

The present 38-file tree is an ownership map, not a requirement to create 38
abstractions before a useful workflow runs. Keep those boundaries; consolidate
small implementations where that makes the first release easier to maintain.

## Recommendation before level 3

Keep the public six-object model and `EvaluationPipeline` as the working proposal.
Prefer **a small domain core plus adapters and imports** over committing now to
a complete new execution platform. Compare one Pydantic Evals integration and
one Inspect log import against the existing toy contracts before deciding how
much of our runner/engine needs its own implementation. Prototype work is a
proposed next task, not something run during this research.

For the intended examples, continue with separate OpenSRE and OpenKritt studies.
Measure how much integration code each needs and whether a user can understand
the A/B difference and its evidence. If a native evaluation tool with a small
adapter offers the same experience more simply, reduce our engine scope and
build on it. The metric definitions remain replaceable in either choice.

Detailed primary-source notes: [Inspect](research/inspect_architecture_review.md),
[Pydantic Evals](research/pydantic_evals_architecture_review.md),
[Promptfoo and MLflow](research/promptfoo_mlflow_architecture_review.md).
Only the cited Inspect code snapshot is pinned to a commit; other current
documentation/source links are dated observations, not verified release
compatibility. No upstream integration or paid agent/model execution was run.
