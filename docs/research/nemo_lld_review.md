# NeMo: audit of the proposed LLD against existing agent infrastructure

Research date: 2026-09-08. This audits our [level-2 design](../LLD_LEVEL_2.md),
not the quality of any particular metric. No upstream code was executed.

## Finding

The initial project plan was right: NeMo already contains substantial agent
evaluation infrastructure. The current LLD moves away from that reuse plan by
giving our library its own assignment dispatcher, capture model, evaluation
engine, comparison layer and native-agent adapters before proving integrations.
Those are not all unnecessary, but their ownership needs a decision.

Keep the six objects and the convenient `pipeline.eval()` interface. Build the
experiment definition, declared component changes, cross-runtime identity,
coverage and usable result interface. Delegate native execution where an
existing system already owns it. Reuse trajectory schemas and compatible
evaluators. Make a small direct runner a supported alternative for simple
callbacks, tools and prepared agent processes.

The difficult work is translating boundaries faithfully. Forcing every existing
runner through a one-assignment callback would make us rebuild orchestration
while claiming to wrap it. Depending on all of NeMo would also be excessive for
a small local skill test. An optional integration, with an explicit ownership
contract, is the credible middle ground.

## Exact source versions

Two code snapshots were read, resolved through the GitHub API:

| Project | Snapshot | Scope of claims below |
| --- | --- | --- |
| NeMo Agent Toolkit (NAT) | [`967a679954ec2557ef4c6b3c4e7446793f6d4a97`](https://github.com/NVIDIA/NeMo-Agent-Toolkit/commit/967a679954ec2557ef4c6b3c4e7446793f6d4a97), `develop`, September 3 | Source snapshot; not a promise about every published NAT wheel. |
| NeMo Evaluator | [`9758d8d5508e0d1bea79cb99ed56d595a1c3acdc`](https://github.com/NVIDIA-NeMo/Evaluator/commit/9758d8d5508e0d1bea79cb99ed56d595a1c3acdc), `main`, September 1 | Its package metadata declares **0.4.0**. Older SDK/Launcher documentation describes a different architecture. |

## What exists, at the level of actual mechanisms

NAT has two relevant layers. `EvaluationRun.run_and_evaluate()` loads workflow
configuration, creates repeated dataset items, executes local or remote
workflows, runs evaluators and returns outputs plus profiling/configuration
artifacts. Its smaller `EvaluationHarness.evaluate()` only grades supplied ATIF
samples. These are different integration choices: whole workflow ownership or
grading-only reuse. [Runtime source][nat-runtime], [small harness][nat-harness]

The evaluator inputs contain actual and expected trajectories as well as input
and output objects. `EvalAtifAdapter` converts trajectories once and shares them
across evaluators. Native output items carry identity, score, reasoning and an
error field. This is agent trajectory evaluation, not merely comparing two
strings. [Input schema][nat-input], [ATIF adapter][nat-atif], [output schema][nat-output]

NeMo Evaluator separates `EvalEnvironment.seed()/verify()` from
`Solver.solve()`. Its solver result includes a trajectory, errors and an
optional already-computed reward. Environments can own batch execution.
`run_evaluation()` implements the normal task loop, concurrency, repetitions,
recovery and sandbox transitions. The current NAT solver consumes a running
agent's HTTP stream and converts model/tool events into trajectories.
[Environment][nel-env], [solver contract][nel-solver], [loop][nel-loop],
[NAT solver][nel-nat]

That last example matters: an agent evaluation platform can already wrap
another agent runtime. Our proposed separation is established practice, not a
new invention. The question is which layers our product needs to own.

## Mapping our LLD to reuse decisions

| Proposed owner | Existing mechanism | Recommended ownership |
| --- | --- | --- |
| `pipeline/api.py`, `prepare_evaluation` | NAT `EvaluationRun`; NeMo evaluation loop | Keep our small facade and study preflight. Delegate the selected execution path. |
| `execution/planning.py` | Native repetitions, dataset selection, sharding | Keep candidate × unit × repetition identity. Translate once; never independently repeat the same assignments twice. |
| `RunExecutor`, `adapters/process.py`, `worker.py` | Native workflow execution; NeMo solvers, environments and sandboxes | Build minimal direct execution; add an optional whole-run integration before expanding worker orchestration. |
| `CaptureBuffer`, `objects/runset.py` | ATIF plus native output and execution artifacts | Keep experiment joins and partial/missing coverage. Preserve native trajectories as artifacts; avoid inventing a competing full trajectory language. |
| `EvaluationEngine`, `MetricEvaluator` | NAT ATIF harness and evaluator contracts | Keep our metric dependency graph and validated output mapping. Wrap compatible native evaluators; support batch grading without rerunning it per task. |
| `results/comparison.py` | NeMo `compare_results()` and statistical utilities | Reuse compatible calculations through an optional adapter, or use a smaller statistics dependency. Keep our pairing and evidence contract. |
| `storage/manifests.py`, `reporting/html.py` | Native run metadata, bundles, reports | Store our study/result envelope and references. Do not rebuild native log viewers or remote run stores in v0. |

NeMo already exposes in-memory paired comparison, averaging repetitions before
testing. It also has bootstrap and clustered interval functions. Therefore
“paired analysis” and “clustering” alone cannot justify a new engine. Compatibility
depends on the chosen unit, pairing, weighting and missingness rules.
[Comparison][nel-compare], [confidence utilities][nel-confidence]

## Attacks on our design

### 1. Two schedulers can run a different experiment from the one declared

Suppose our OpenSRE study specifies two candidates, two incident tasks and three
repetitions: **12 assignments**. `RunExecutor` calls a wrapper once per
assignment. If the wrapper also passes three repetitions to NAT, it performs
**36 executions**. Returning one result per outer call hides 24 executions;
returning all results violates the one-run-per-assignment contract.

This is a concrete adapter ownership bug, not a criticism of a metric. The
remedy is either single-assignment execution with native repetition disabled,
or one native batch submission whose outputs map to the 12 declared
assignments. Our LLD currently specifies only the first protocol. A batch seam
is justified by existing systems, although its exact API still needs design.
[NAT repetition configuration in runtime][nat-runtime]

### 2. A scalar wrapper can erase the evidence needed for agent evaluation

NAT's small harness logs whole-evaluator exceptions and omits unsuccessful
evaluators from its returned mapping. NeMo solver output can bundle verification
with execution. A wrapper that simply copies an average or reward into our
summary loses which evaluator was absent, which trajectory produced the
measurement, and whether checking already happened.

Our mapping should reconcile expected evaluator/item identities, preserve
native error artifacts and distinguish execution evidence from imported
measurements. Native decisions remain available as native metrics; users choose
their interpretation. This justifies custom result normalization, not a second
implementation of each grader. [Harness failure behavior][nat-harness],
[bundled verification result][nel-solver]

### 3. Our offline grading promise exceeds what an output-only capture supports

An OpenKritt-style mission may change a workspace before a native verifier
checks it. Saving the final text and an event list does not save that workspace.
NeMo's lifecycle explicitly captures and transfers workspace state; its recovery
code recognizes when verification requires state that cannot be reattached.

Therefore `eval(runs=saved_runs)` must validate each evaluator's required
evidence. If a check needs an unavailable filesystem snapshot, it must return a
declared missing/error outcome or reject the incompatible request. It cannot
silently recreate the task and still claim grading-only reuse. Add capture
capabilities and evidence requirements before promising universal regrading.
This concerns executable dependencies, not which score is scientifically best.
[Recovery and verification lifecycle][nel-loop]

### 4. Reusing the full package can cost more than implementing a small seam

The NAT evaluation package separates its minimal ATIF dependency from its full
workflow runtime. We should copy that packaging strategy. NeMo Evaluator 0.4.0
requires Python 3.12–3.13 and includes web-serving, dataset, numeric and AWS SDK
dependencies; Harbor and statistics add optional dependencies. Its metadata also
declares conflicting extra combinations. Our proposed contract uses Python
3.11-style typing and local/GCP profiles, so making NeMo mandatory would impose
real compatibility and installation costs. [NAT packaging][nat-package],
[Evaluator packaging][nel-package]

## What would convince us before level 3

Use a saved native NAT or NeMo run to demonstrate these contracts without
running upstream agents: preserve task/repetition joins, tool evidence, partial
coverage and native errors; grade a trajectory through a native evaluator;
change only our selection policy; and reopen the result without installing its
native runner. Then test one direct execution and one delegated batch with
exactly one owner for retries, repetitions and cancellation.

If the adapter becomes mostly schema conversion, reuse wins. If it must disable
or bypass most upstream behavior just to reproduce our small contract, a small
independent implementation is justified. Count responsibilities and integration
tests, not filenames or an invented percentage of reused code.

[nat-runtime]: https://github.com/NVIDIA/NeMo-Agent-Toolkit/blob/967a679954ec2557ef4c6b3c4e7446793f6d4a97/packages/nvidia_nat_eval/src/nat/plugins/eval/runtime/evaluate.py
[nat-harness]: https://github.com/NVIDIA/NeMo-Agent-Toolkit/blob/967a679954ec2557ef4c6b3c4e7446793f6d4a97/packages/nvidia_nat_eval/src/nat/plugins/eval/runtime/eval_harness.py
[nat-input]: https://github.com/NVIDIA/NeMo-Agent-Toolkit/blob/967a679954ec2557ef4c6b3c4e7446793f6d4a97/packages/nvidia_nat_core/src/nat/data_models/evaluator.py
[nat-atif]: https://github.com/NVIDIA/NeMo-Agent-Toolkit/blob/967a679954ec2557ef4c6b3c4e7446793f6d4a97/packages/nvidia_nat_eval/src/nat/plugins/eval/runtime/atif_adapter.py
[nat-output]: https://github.com/NVIDIA/NeMo-Agent-Toolkit/blob/967a679954ec2557ef4c6b3c4e7446793f6d4a97/packages/nvidia_nat_eval/src/nat/plugins/eval/data_models/evaluator_io.py
[nat-package]: https://github.com/NVIDIA/NeMo-Agent-Toolkit/blob/967a679954ec2557ef4c6b3c4e7446793f6d4a97/packages/nvidia_nat_eval/pyproject.toml
[nel-env]: https://github.com/NVIDIA-NeMo/Evaluator/blob/9758d8d5508e0d1bea79cb99ed56d595a1c3acdc/src/nemo_evaluator/environments/base.py
[nel-solver]: https://github.com/NVIDIA-NeMo/Evaluator/blob/9758d8d5508e0d1bea79cb99ed56d595a1c3acdc/src/nemo_evaluator/solvers/base.py
[nel-loop]: https://github.com/NVIDIA-NeMo/Evaluator/blob/9758d8d5508e0d1bea79cb99ed56d595a1c3acdc/src/nemo_evaluator/engine/eval_loop.py
[nel-nat]: https://github.com/NVIDIA-NeMo/Evaluator/blob/9758d8d5508e0d1bea79cb99ed56d595a1c3acdc/src/nemo_evaluator/solvers/nat.py
[nel-compare]: https://github.com/NVIDIA-NeMo/Evaluator/blob/9758d8d5508e0d1bea79cb99ed56d595a1c3acdc/src/nemo_evaluator/engine/comparison.py
[nel-confidence]: https://github.com/NVIDIA-NeMo/Evaluator/blob/9758d8d5508e0d1bea79cb99ed56d595a1c3acdc/src/nemo_evaluator/metrics/confidence.py
[nel-package]: https://github.com/NVIDIA-NeMo/Evaluator/blob/9758d8d5508e0d1bea79cb99ed56d595a1c3acdc/pyproject.toml
