# LLD reuse red team: AgentCompass and A2E

Research date: 8 September 2026. This challenges the proposed
[level-2 architecture](../LLD_LEVEL_2.md). It changes no accepted contract and
executes no upstream code.

## Verdict

These are direct precedents for our agent, tool, skill and harness evaluation
design. AgentCompass already composes tasks, harnesses, models and environments;
A2E already separates execution, agent tracing and extensible process/outcome
evaluation. Calling that architecture our invention would be wrong.

The strongest reuse opportunity is their execution and observation machinery.
The strongest reason for our own code is a compact, provider-independent
experiment/result contract: candidate changes, task coverage, evidence joins,
unknown observations and replaceable decision policies. That is a useful product
choice, not proof of a novel evaluation method.

We should require evidence before building another general scheduler or native
CLI parser. Conversely, adopting an entire platform merely to avoid writing a
small planner can introduce more translation and installation work than it saves.

## Paper claims versus available implementation

The [AgentCompass paper](https://arxiv.org/html/2607.13705v1#S3) describes explicit
benchmark/harness/environment protocols, asynchronous execution and separate
verification environments. The [A2E paper](https://arxiv.org/html/2608.07346v1#S4)
defines an Agent Task Protocol and process-level evaluation covering tools,
skills and memory. These are architectural precedents, not merely LLM graders.

Both repositories contain corresponding implementation. Source snapshots inspected:

- AgentCompass: [`c30a5d9472c0ed9afefad7bdabbf096db4c0f92a`](https://github.com/open-compass/AgentCompass/tree/c30a5d9472c0ed9afefad7bdabbf096db4c0f92a).
- A2E: [`d524a03c8bad0ccb735202e4c3468cece396f734`](https://github.com/datamllab/A2E/tree/d524a03c8bad0ccb735202e4c3468cece396f734).

This establishes released source availability. Installation, package-release
compatibility and successful integration with our E2Es remain unverified.

## Concrete overlap with our files

| Our LLD responsibility | Existing source | Build/reuse judgment |
| --- | --- | --- |
| `pipeline/api.py`, `preflight.py`; `execution/runner.py` | AgentCompass `UnifiedEvaluationRuntime.prepare/preflight/execute` and `Orchestrator.execute` | Strong overlap. A whole native job can own scheduling; our facade then maps requests/results. Do not create a second native retry owner. [Runtime](https://github.com/open-compass/AgentCompass/blob/c30a5d9472c0ed9afefad7bdabbf096db4c0f92a/src/agentcompass/runtime/runner.py), [orchestrator](https://github.com/open-compass/AgentCompass/blob/c30a5d9472c0ed9afefad7bdabbf096db4c0f92a/src/agentcompass/runtime/orchestration.py). |
| `execution/planning.py`, `objects/study.py` | A2E `CampaignConfig`, `CampaignPlan`, `CellSpec`, `TrialSpec`, `expand_campaign` | Candidate/task/repetition planning is established. Keep our small planner if translating into its model/benchmark/harness matrix costs more than the expansion itself. [Matrix](https://github.com/datamllab/A2E/blob/d524a03c8bad0ccb735202e4c3468cece396f734/task/packages/ageneval-task-orchestrator/src/ageneval/task/orchestrator/matrix.py). |
| `adapters/process.py`, `worker.py` | A2E `TrialProcessRunner`; AgentCompass `BaseEnvironment`/`EnvironmentSession` | Reuse compatible lifecycle implementations rather than invent a cloud platform. They already address supervision, transfer and execution sessions. [Process supervisor](https://github.com/datamllab/A2E/blob/d524a03c8bad0ccb735202e4c3468cece396f734/task/packages/ageneval-task-orchestrator/src/ageneval/task/orchestrator/process.py), [environment protocols](https://github.com/open-compass/AgentCompass/blob/c30a5d9472c0ed9afefad7bdabbf096db4c0f92a/src/agentcompass/runtime/base.py#L31-L106). |
| `adapters/codex.py`, `claude_code.py` | AgentCompass native harness integrations; `SkillsBenchBenchmark` stages skills and runs verification | Substantial duplicate maintenance risk. Evaluate delegation before rewriting capture. [Codex](https://github.com/open-compass/AgentCompass/blob/c30a5d9472c0ed9afefad7bdabbf096db4c0f92a/src/agentcompass/harnesses/codex.py), [SkillsBench](https://github.com/open-compass/AgentCompass/blob/c30a5d9472c0ed9afefad7bdabbf096db4c0f92a/src/agentcompass/benchmarks/skillsbench/benchmark.py). |
| `execution/capture.py`, `objects/runset.py` | A2E OpenTelemetry/OpenInference instrumentation and `TaskTrace`; AgentCompass ACTF `Trajectory` | Reuse instrumentation and preserve original traces. Own mappings into assignment identity and observed/unknown fields. [Instrumentation](https://github.com/datamllab/A2E/blob/d524a03c8bad0ccb735202e4c3468cece396f734/task/packages/ageneval-task-core/src/ageneval/task/core/instrumentation.py), [ACTF](https://github.com/open-compass/AgentCompass/blob/c30a5d9472c0ed9afefad7bdabbf096db4c0f92a/src/agentcompass/runtime/models/trajectory.py). |
| `evaluation/engine.py`, `aggregation.py` | AgentCompass benchmark evaluation/analyzers/reducers; A2E tool/process evaluators and executor | Existing evaluator callbacks are reusable behind our metric protocol. Our dependency/evidence contract still needs implementation or translation. [Analyzer execution](https://github.com/open-compass/AgentCompass/blob/c30a5d9472c0ed9afefad7bdabbf096db4c0f92a/src/agentcompass/runtime/analysis.py), [A2E tool evaluators](https://github.com/datamllab/A2E/blob/d524a03c8bad0ccb735202e4c3468cece396f734/eval/process_values/tool_eval.py). |
| `storage/`, `results/`, `reporting/` | AgentCompass persisted results; A2E trace IDs, server-backed experiment records and UI | Import/export first. Our offline result interface may justify its own manifest/report; tracing or a dashboard alone is no differentiation. [Result store](https://github.com/open-compass/AgentCompass/blob/c30a5d9472c0ed9afefad7bdabbf096db4c0f92a/src/agentcompass/runtime/results/store.py), [A2E result record](https://github.com/datamllab/A2E/blob/d524a03c8bad0ccb735202e4c3468cece396f734/task/packages/ageneval-task-core/src/ageneval/task/core/result.py). |

## Attacks on the LLD, with defenses

**1. We could spend months rebuilding execution under five innocent filenames.**
Imagine two Codex skill configurations, ten tasks and three repetitions.
A2E already expands comparable experiments into identified trials; AgentCompass
already provisions sessions, runs attempts, checkpoints and finalizes results.
Our `planning/runner/capture/process/worker` split does not make those obligations
small. Reuse a native job for the supported case, then import every attempt.
Keep a minimal direct runner for supplied callbacks. The current per-assignment
`BackendAdapter.run()` should not be assumed to accommodate a complete campaign
without redundant schedulers; prove that mapping before freezing level 3.

**2. Our tidy execute-then-evaluate sequence hides environment verification.**
A patch from an owned coding-agent mission may need tests in a clean checkout. If execution
cleans the workspace and stores only final prose, `EvaluationEngine` cannot
reconstruct the verifier's inputs. AgentCompass explicitly separates artifact
collection from verification and supports reused, fresh or absent environments.
This is a lifecycle/evidence requirement, independent of which test score the
user chooses. Our current artifact-backed callbacks can support it: declare and
preserve the required patch/snapshot, or import the native verification evidence.
We need no universal verifier policy, but we must specify who owns that resource.
[Existing hooks](https://github.com/open-compass/AgentCompass/blob/c30a5d9472c0ed9afefad7bdabbf096db4c0f92a/src/agentcompass/runtime/base.py#L149-L209).

**3. Reuse can accidentally substitute a different harness.**
A2E's `AgentBinding` supplies tool schemas, an executor and a prompt builder to a
generic `AgentRunner`. Recreating OpenSRE with those pieces could replace its
control flow and permission behavior. The measured candidate would change.
Our native OpenSRE/OpenKritt adapters are justified when they preserve those
systems' actual invocation and outputs. A2E can also capture native framework
activity; this is a warning about the integration choice, not an inherent A2E
limitation. [Binding](https://github.com/datamllab/A2E/blob/d524a03c8bad0ccb735202e4c3468cece396f734/task/packages/ageneval-task-core/src/ageneval/task/core/binding.py).

**4. Replacing our records with upstream classes is not free.**
AgentCompass's benchmark metric contract accepts Boolean/scalar observations
and requires one primary `correct` or `score`. Our suite may contain independent
cost, latency, review-work and task measurements without a primary quality score.
Custom telemetry/analyzers can carry additional information upstream, so this is
not a claim of impossibility. It means our typed measurements, evidence joins
and selectable objectives still need a mapping layer.
[Metric constraints](https://github.com/open-compass/AgentCompass/blob/c30a5d9472c0ed9afefad7bdabbf096db4c0f92a/src/agentcompass/runtime/metrics/contract.py#L71-L190).

A2E's `TaskInput` also includes expected actions/outputs in the object handed to
`AgentRunner`; our `AgentInput` intentionally excludes evaluator references.
A wrapper must project inputs explicitly. Renaming the imported type would not
fulfil that contract. [Task fields](https://github.com/datamllab/A2E/blob/d524a03c8bad0ccb735202e4c3468cece396f734/task/packages/ageneval-task-core/src/ageneval/task/core/dataset.py).

## What would convince us

Before selecting an engine, compare a native-job import against our direct
adapter on one owned toy workflow. Require preserved task/attempt IDs, an actual
skill/tool trace, partial failure evidence, unknown usage, saved-run regrading
and offline explanation. Then compare implementation size and dependency burden.

AgentCompass's base dependencies include several cloud/environment SDKs;
A2E's orchestrator package depends on numerous task/harness packages and its
client. These are concrete costs for a small embeddable library, not reasons to
reject them universally. [AgentCompass dependencies](https://github.com/open-compass/AgentCompass/blob/c30a5d9472c0ed9afefad7bdabbf096db4c0f92a/pyproject.toml),
[A2E dependencies](https://github.com/datamllab/A2E/blob/d524a03c8bad0ccb735202e4c3468cece396f734/task/packages/ageneval-task-orchestrator/pyproject.toml).

The justified target is a small domain core with optional execution, tracing and
grader integrations. A new general agent execution platform is not yet justified.
