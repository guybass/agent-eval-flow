# Agent Eval Flow: does the LLD rebuild existing agent infrastructure?

**Research date: 8 September 2026. Design review, not an implementation change.**
This is the primary reuse audit of [level 2](LLD_LEVEL_2.md). The earlier
[general-library review](ARCHITECTURE_REUSE_REVIEW.md) is supplementary. The
[communication graph](MODULE_COMMUNICATION.md) still depicts the current LLD;
the changes recommended here have not been applied to its public contract.

Follow-up: the [integration recommendation](INTEGRATION_DECISION.md) investigates
the capability and maturity questions raised here and proposes which libraries
should do which work, with concrete workflow examples and source evidence.

## The conclusion in under 200 words

The six objects and `pipeline.eval()` remain a reasonable product design.
They do not justify implementing every mechanism beneath them ourselves.

Rewriting an established implementation gives up its accumulated fixes,
regression tests and exercised behavior. A small implementation and a passing
toy E2E suite do not recover those benefits. Our earlier recommendations to
build a small planner or dependency executor did not sufficiently account for
that loss.

The default should be to reuse an implementation whose relevant behavior is
well exercised and fits our contract. Own our experiment semantics and public
interface; delegate planning, execution, validation, trace handling and other
machinery wherever practical. Custom code needs a concrete unmet requirement,
an explanation of why configuration or extension is insufficient, and a plan
for the behavior we would become responsible for maintaining.

The original agent projects provide substantial candidate implementations.
However, our source audit established capabilities, not widespread use or
reliability of every relevant module. That maturity assessment remains open.

Neither a wrapper nor our six objects automatically preserves upstream
flexibility. The integration must retain needed configuration, evidence and
extension points. The proposed custom runtime remains unproven.

## Which original work this reviews

The starting points are [PROJECT_PLAN §§4 and 11.2](PROJECT_PLAN.md) and the
[original paper review](AGENT_EVAL_FLOW_REVIEW.md#3-research-that-changes-the-design).
The original plan explicitly called for a NeMo integration experiment before
building the kernel, SkillEvaluator interoperability, and reuse of existing
runners. That decision was never resolved in the newer tree.

| Original source | Evidence used in this audit | Relevance to the LLD |
| --- | --- | --- |
| ACES, NVIDIA SkillEvaluator, Harbor / ATIF | Paper plus pinned implementation: [source audit](research/skill_harbor_lld_review.md). | Paired skill/bundle evaluation, task staging, execution, tool/subagent trajectories and native verification. |
| AgentCompass and A²E | Papers plus pinned implementations: [source audit](research/agentcompass_a2e_lld_review.md). | Agent/harness/environment interfaces, experiment expansion, process supervision, skills and tool/process evaluation. |
| NeMo Agent Toolkit and NeMo Evaluator | Pinned implementations: [source audit](research/nemo_lld_review.md). | Workflow execution, ATIF grading, solver/environment lifecycle, run storage and comparisons. |
| Harness-Bench | [Paper][hb-paper] and actual [`models.py`][hb-models] / [`runner.py`][hb-runner]. Moving `main`, not a pinned integration. | Existing native-harness request/result records and task setup → execution → oracle lifecycle. |
| HarnessLens | [Paper][hl-paper] and [repository layout][hl-repo]; individual rollout implementation bodies were not retrieved. | Separate harness optimization from rollout/verification services. A precedent, not a verified package dependency. |
| Rethinking Harness Evolution and HarnessSafe | [Evolution methods][evolution-paper] and [HarnessSafe adaptation contract][safe-paper]. | Distinguish internal search from experimental repetitions; retain state/boundary evidence through native adapters. |
| SkillRoll | Retained from the [original authoring/simulation assessment](PROJECT_PLAN.md#44-skillroll); no fresh API inspection here. | Potential task/simulator integration, not a reason to rebuild its world model inside the core. |

The earlier reliability, DuMateBench, EarlyEval, memory, deployment-reliability,
realism, reward-hacking and HAL readings remain in the original research index.
They inform scenarios and extension requirements. This audit does not treat a
paper's proposed method as reusable software unless its implementation was
actually inspected. Browser-use, OpenHands, DeepAgents and SWE-agent remain
[native-system compatibility cases](AGENT_COMPATIBILITY_RED_TEAM.md);
OpenSRE and OpenKritt remain separate E2E testbeds.

## Where we would actually be reinventing the wheel

**The burden of proof is on replacing an existing implementation.** Owning the
public API is different from owning its underlying algorithms and runtime.
Different field names or IDs can justify a mapping; they do not, by themselves,
justify rewriting a planner, validator or storage engine. The recommendations
below replace the earlier blanket justification based on small implementation
size or installation convenience.

These are proposed ownership boundaries, not verified dependency choices.
Source details and exact revisions are in the three audits.

| Our planned code | Existing mechanism | Recommended decision and reason |
| --- | --- | --- |
| `pipeline/api.py`, `pipeline/preflight.py` | AgentCompass `UnifiedEvaluationRuntime.prepare/preflight/execute`; SkillEvaluator `EvaluationService.evaluate`. | **Build a thin facade.** It validates our study and selects a runtime; the selected runtime retains its native checks. A second full coordinator is unjustified. |
| `objects/`, `execution/planning.py` | A²E `CampaignConfig`, `CampaignPlan`, `CellSpec`, `TrialSpec`, `expand_campaign`. | **Own the schema and identity requirements; try reuse underneath.** Map native planning into our contract before deciding a custom planner is needed. Different records alone are insufficient justification. Reuse established validation machinery for our own schemas too. |
| `execution/runner.py` | Harbor `Job.create/run` and `TrialQueue`; AgentCompass `Orchestrator`; NeMo `run_evaluation`. | **Prefer delegation after checking maturity and fit.** A lightweight direct path remains a requirement, but does not preselect a new scheduler. Keep new glue narrow and preserve upstream lifecycle behavior. |
| `adapters/codex.py`, `claude_code.py`, `process.py`, `worker.py` | Harbor installed agents; AgentCompass native harnesses and environment sessions; A²E `TrialProcessRunner`. | **Wrap compatible integrations first.** Own a local or prepared-GCP connection where our environment requires it. Do not turn `worker.py` into another provisioning and scheduling platform. |
| `adapters/opensre.py`, `openkritt.py` | Existing runtime extension protocols, not verified ready-made integrations for these two projects. | **Build thin native bridges where needed.** Preserve the real agent invocation. Recreating its tools inside another agent loop would change the candidate. |
| `objects/runset.py`, `execution/capture.py` | ATIF typed trajectories and validators; AgentCompass ACTF; A²E OpenTelemetry/OpenInference capture. | **Reuse trace machinery; build the experiment envelope.** Keep original artifacts, project documented fields and join them to assignments. Do not invent another full tool/subagent trace language. |
| `evaluation/engine.py`, `compiler.py`, `scoring.py` | NAT's ATIF `EvaluationHarness`; SkillEvaluator graders; Harbor verifiers; A²E process evaluators. | **Own measurement semantics; reuse compatible evaluation and graph machinery.** Dependency ordering does not require a new graph algorithm. Add only the missing callback/output mapping. Keep native verification in its required lifecycle. |
| `evaluation/aggregation.py`, `results/comparison.py`, `selection.py` | Native arm summaries; NeMo `compare_results` and confidence utilities. | **Own the common result/policy interface; assess reusable calculations.** A custom formula needs a specific policy requirement or demonstrated mismatch, not the claim that arithmetic is small. Current v0 does not implement confidence intervals. |
| `storage/`, `reporting/` | Native artifact bundles, result stores and viewers. | **Own our document shape and joins; reuse persistence, serialization and rendering mechanisms where they fit.** A custom manifest is not a reason to invent a storage engine. No new trace database, dashboard service or portable-environment archive in v0. |

The common pattern worth adopting from these projects is separation of task
definitions, native execution, observation, verification and stored analysis.
Our proposed difference is an embeddable, consistent experiment/result surface
across selected runtimes. It is not a newly discovered agent-evaluation pipeline.
That difference is valuable only if the mappings are faithful and the common
API actually reduces work for its user.

### What we lose by rewriting, and how to decide

Consider a runner that has accumulated fixes for cancellation during a retry,
partial artifact writes and child-process cleanup. A replacement can pass our
plain/skill/tool/flow E2Es while missing all three conditions. We would have to
rediscover those failures, maintain the fixes and support subsequent changes.
This is an illustrative failure scenario, not a claim that every inspected
runner already handles every case. Our E2Es test our integration contract; they
cannot substitute for an upstream implementation's operating history.

Preserving that advantage means executing the upstream implementation through
its supported interfaces. Recreating its API does not inherit its behavior.
Copying its code also makes us responsible for tracking future fixes. A wrapper
still introduces new risks at the boundary, so we test its mappings and failure
handling while continuing to use the underlying implementation.

Two small examples show that a custom public contract need not mean custom
machinery. `MetricSpec` can describe our dependencies while Python's standard
[`graphlib.TopologicalSorter`](https://docs.python.org/3/library/graphlib.html)
handles ordering and cycle detection. We still validate missing metric IDs and
our callback rules. Our own records can use an existing validation system with
domain-specific checks, such as Pydantic's
[custom validators](https://docs.pydantic.dev/latest/concepts/validators/).
These examples illustrate reuse below the API; they do not select an entire
agent evaluation framework or establish dependency compatibility.

The prior audit did **not** measure the following evidence. Before choosing the
runtime, record it for the actual module and supported version:

| Question | Evidence required |
| --- | --- |
| Is this path actually used? | Identifiable downstream use of the relevant execution/integration path; mark unknown where unavailable. Stars and organization reputation are insufficient. |
| What behavior has been tested and repaired? | Relevant test cases, CI results, regression fixes and release history, especially the failure modes we would otherwise own. Test-file count alone is insufficient. |
| Will we keep receiving those benefits? | Maintenance activity, supported extension points, version compatibility and a feasible upgrade path. |
| Does it stay flexible through our API? | A representative integration preserves needed native configuration, output detail and extension hooks without silently changing behavior. |

Usage is evidence for accumulated learning, not proof that every new module in
a popular project is mature or that its abstractions fit us. Conversely, calling
our new implementation small or flexible is not evidence that it is safer.
Our adapter can become the rigid part if it hides native options, flattens
results or forces every job into one-assignment execution. Preserve documented
native configuration and original results, and provide an explicit integration
path for capabilities outside the common API. If our proposed contract prevents
a faithful mapping, reconsider that contract before replacing the dependency.

For each proposed replacement, the decision record should name the unmet
requirement, show why configuration/composition/extension cannot reasonably
satisfy it, and identify the behavior and maintenance burden we would take over.
Consider contributing a missing extension upstream. Match the evidence to the
scope: a field mapping needs a clear purpose and contract check; a replacement
scheduler requires much stronger lifecycle and failure evidence. Dependency
weight is a real tradeoff, but must be weighed against this lost maturity and
against smaller existing components, not treated as automatic permission to
rewrite.

## Five concrete attacks, including the defense

### 1. Our adapter unit fights the native runner's unit

**Existing LLD:** `BackendAdapter.run(request, recorder)` returns one `Run` for
one assignment. This is a good fit for a small tool or process mission.

**Counterexample:** ten tasks × skill off/on × three planned repetitions = 60
assignments.
SkillEvaluator's `run_harbor_eval` already constructs the paired experiment.
Calling its full study interface once per assignment repeats the experiment;
splitting everything into one-task, one-arm jobs bypasses useful native staging
and orchestration. A hidden cross-call batch cache introduces surprising state.
[Inspected SkillEvaluator implementation][skill-runner]

The LLD already gives retries to the adapter; that is correct. The unresolved
issue is **whole-job ownership**, not a need to add another retry policy.

**Recommendation:** keep the current direct adapter. Initially use the existing
`RunImporter` route for native completed jobs. For integrated one-call native
execution, design a separate job boundary that accepts the declared assignment
set and returns their mapping. Do not disguise a whole study as one assignment.
Its exact API belongs in the next LLD discussion, not a silent contract edit.

### 2. Execute-then-evaluate can discard what a verifier needs

**Counterexample:** an owned coding-agent test mission produces a patch in a
temporary workspace. A native verifier checks that patch before the environment closes.
After cleanup, a final answer and tool log cannot recreate the workspace.
Harbor already has verifier phases; AgentCompass has artifact and verification
environment hooks; NeMo separates environment seeding, solving and verification.
[Harbor lifecycle][harbor-trial], [AgentCompass hooks][compass-base]

**Defense:** our artifact references and custom callbacks are flexible enough
to carry this. The six-object model is not inherently broken.

**Recommendation:** specify who captures the required artifact or native verifier
observation before cleanup. An imported-grade callback can read that observation
and its grader revision from saved evidence. A new live-state check needs the
actual state or a declared missing/error result. `eval(runs=saved)` must never
silently rerun the agent. This defines evidence availability, not the score's
meaning. Native and later grading costs must remain distinguishable when known.

### 3. A generic event record can grow into an unnecessary trace platform

**Counterexample:** a parent agent invokes two subagents, each making tool calls.
ATIF already represents tool observations and subagent trajectory references.
If we flatten them into arbitrary dictionaries, then recreate identity,
validation and conversion rules ourselves, we inherit a large compatibility
burden. [ATIF model and validation][atif-model]

**Defense:** ATIF alone does not represent our full planned experiment, including
an assignment that never produced a trajectory. Our `RunSet` still has a job.

**Recommendation:** native traces remain authoritative evidence; reuse their
validators/converters. Our events are a documented projection with links back
to native records. Unknown or unsupported fields remain available in the source
artifact. Do not force ACTF or OpenInference into ATIF if that loses information.

### 4. Maximum reuse can change the harness we intended to test

**Counterexample:** A²E offers an `AgentBinding` of tool schemas, an executor and
a prompt builder. Recreating OpenSRE through that generic agent runner could
replace OpenSRE's own control flow or permissions. The tools might match while
the harness does not. [Actual binding contract][a2e-binding]

This is an integration-choice risk, not a claim that A²E requires replacement.

**Recommendation:** run the native OpenSRE/OpenKritt entry points and adapt their
outputs or instrument them. Reuse an upstream execution environment if it fits;
keep custom glue where needed to preserve native behavior. This is a concrete
reason to build an adapter even when another project already has agent APIs.

### 5. “Use their objects” can move complexity into every caller

AgentCompass's benchmark metric contract accepts Boolean/scalar observations
and requires one primary `correct` or `score`. Our contract can hold independent
task, cost, latency and review measurements, with a later selection policy.
Extra upstream analyzers can help, but a mapping is still required.
[Metric contract][compass-metrics]

A²E's `TaskInput` includes expected outputs/actions. Our backend input excludes
private evaluator references. Wrapping it therefore requires explicit projection;
renaming its type is insufficient. This does not imply A²E necessarily leaks
answers. [Task schema][a2e-task]

**Recommendation:** own the domain semantics and use existing machinery beneath
them where it fits; a schema mismatch alone does not justify a replacement
implementation. Keep native integrations
optional: their dependency burden is real. For example, the inspected
SkillEvaluator revision requires Python 3.12–3.13 and pins Harbor 0.13.2;
the separately reviewed Harbor snapshot is not a verified compatible replacement.
[Version evidence](research/skill_harbor_lld_review.md#four-concrete-attacks-on-the-lld)

## What the papers change in the architecture

These are design inferences from the original papers, not mandatory metrics.

| Paper | Concrete LLD consequence |
| --- | --- |
| [ACES][aces-paper] | A paired skill intervention should be a study preset/native import. Reuse its live evaluation machinery; do not rebuild it as six freshly written graders. |
| [Harness-Bench][hb-paper] | Preserve native harness behavior and its declared configuration. Its actual `run_task` already combines setup, native execution and oracle collection; mapping it requires accounting for all three phases. |
| [Rethinking Harness Evolution][evolution-paper] | Internal search/refinement is different from independent experimental repetitions. Existing `Execution` lineage versus `Assignment.repetition` is a useful separation; adapters must preserve it. |
| [HarnessLens][hl-paper] | An optimizer proposes/selects candidates outside the evaluation core. Reusable evaluation serves development and separate confirmation runs; putting the optimizer into `pipeline.eval()` would mix responsibilities. |
| [HarnessSafe][safe-paper] | Adapter contracts need explicit state/boundary evidence and supported mappings. A native workflow that cannot express a scenario is different from one that ran and produced an observed result. Preserve that distinction without prescribing a safety score. |

Another limit should be explicit: the proposed contract can declare four
candidates for two models × skill off/on. Its `compare()` defines pairwise
differences; it does not yet provide a first-class factorial interaction
analysis. The model-specific skill effects can be compared externally. Neither
component labels nor a trace prove which internal mechanism caused an effect.

## The proposed decision before level 3

Keep the six objects and public facade as the working proposal, subject to
faithful integration. Retain support for small direct missions without assuming
that we must implement their execution machinery ourselves.
Change the planned ownership beneath them: native-job integration and trace/grade
import should be visible implementation units, with native lifecycle operations
delegated to the selected runtime. Choose **one initial native path**, not four
mandatory engines. SkillEvaluator/Harbor is the first interoperability target
because the original plan already chose a paired skill study as its fixture.
That does not commit OpenSRE or OpenKritt to running inside Harbor.

Source availability is insufficient to select a runtime. First assess the
module-level maturity evidence above. Then use these contract exercises to
check whether our integration preserves its behavior; these exercises are not
a substitute for its operating history or a test of scientific metric quality:

1. A retained native paired study maps all declared tasks, arms and attempts to
   `RunSet`, including absent/failed trials, without inventing observed values.
2. A tool/subagent trace and a native verifier artifact remain reachable through
   `EvaluationResult.explain()`. The mapper preserves identities and provenance.
3. Changing only the selection policy uses the same measurements and makes zero
   new agent or grader calls. A deliberately new evaluator uses only available
   saved evidence and accounts for any new grading work separately.
4. A delegated job has exactly one owner for repetitions, retries and cleanup.
   A small direct tool mission still runs without installing that native engine.

For OpenSRE, the later live proof compares two native configurations within an
incident study. For OpenKritt, a separate study preserves its mission artifacts
and verification evidence. Neither test needs to demonstrate that one agent is
good; both must demonstrate usable, faithful outputs from our library.

**My judgment:** the common experiment/result contract has a clear purpose.
Custom planning, dependency execution, persistence and calculation machinery
remain unproven choices. Prefer existing implementations with demonstrated use
and fit, adding the narrowest necessary domain logic. A replacement must justify
the reliability and maintenance burden it takes on. Source inspection establishes
overlap; module-level adoption, installation and runtime compatibility have not
yet been demonstrated by this review.

[skill-runner]: https://github.com/NVIDIA/SkillEvaluator/blob/73b27dad60d3927e202ea6099ce79bb25053fd2b/src/skillevaluator/tier3/harbor/runner.py
[harbor-trial]: https://github.com/harbor-framework/harbor/blob/71c39eafbd134d43ae3f489b5e6488b2a157de65/src/harbor/trial/trial.py
[atif-model]: https://github.com/harbor-framework/harbor/blob/71c39eafbd134d43ae3f489b5e6488b2a157de65/src/harbor/models/trajectories/trajectory.py
[compass-base]: https://github.com/open-compass/AgentCompass/blob/c30a5d9472c0ed9afefad7bdabbf096db4c0f92a/src/agentcompass/runtime/base.py
[compass-metrics]: https://github.com/open-compass/AgentCompass/blob/c30a5d9472c0ed9afefad7bdabbf096db4c0f92a/src/agentcompass/runtime/metrics/contract.py
[a2e-binding]: https://github.com/datamllab/A2E/blob/d524a03c8bad0ccb735202e4c3468cece396f734/task/packages/ageneval-task-core/src/ageneval/task/core/binding.py
[a2e-task]: https://github.com/datamllab/A2E/blob/d524a03c8bad0ccb735202e4c3468cece396f734/task/packages/ageneval-task-core/src/ageneval/task/core/dataset.py
[hb-paper]: https://arxiv.org/html/2605.27922v1
[hb-models]: https://github.com/Qihoo360/harness-bench/blob/main/src/harnessbench/models.py
[hb-runner]: https://github.com/Qihoo360/harness-bench/blob/main/src/harnessbench/runner.py
[hl-paper]: https://arxiv.org/html/2608.27311v1
[hl-repo]: https://github.com/jhxu5214/HarnessLens
[evolution-paper]: https://arxiv.org/html/2607.12227v1
[safe-paper]: https://arxiv.org/html/2608.06984v1
[aces-paper]: https://arxiv.org/html/2608.20614v1
