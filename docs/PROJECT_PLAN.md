# Agent Eval Flow: decision-first design

**Current design direction — 9 September 2026:** evaluate frozen candidate
configurations and task behavior through parallel evidence branches, then join
scoped assessments under explicit decision policies. The
[unified assessment flow](UNIFIED_ASSESSMENT_FLOW.md) records this accepted
extension and its migration path. The [initial behavioral implementation](IMPLEMENTATION.md)
exists; configuration assessments are the next design step, not a shipped
capability.

**Historical review checkpoint — 8 September 2026:** the
[complete acceptance suite](../tests/README.md) was written and recorded before
the [full module LLD](lld/README.md). Levels 3 and 4 now cover all eight modules.
The remaining step is review of that design before implementing the runtime.
The earlier narrative below remains research context.

The [six-object contract](LIBRARY_OBJECT_MODEL.md) is the current proposal for
data structures and API signatures. Earlier names and signatures below remain
research context and are superseded where they differ.

Status: working design, 2026-09-05

Project name: **Agent Eval Flow**. Proposed Python import: `agent_eval_flow`.
The system under evaluation is the complete agent and its harness; component
experiments, including skill comparisons, are supported within that scope.
For the proposal in plain language, start with the
[layered explanation](AGENT_EVAL_FLOW_REVIEW.md).

Current direction: identify the effects of skills, flows, models, and harness
configurations, including when their combinations help or hurt. OpenSRE and
OpenKritt are the selected real-world testbeds. The
[comparison with current work](WHAT_AGENT_EVAL_FLOW_ADDS.md) records this updated
focus and first experiment; it supersedes the tender-first milestone choice.
The worked examples below remain useful illustrations of the general contract.

The examples and numbers below are illustrative. They define the required
behavior of the library; they are not benchmark results.

The [workflow evaluation model](WORKFLOW_EVALUATION_MODEL.md) is the broader
contract refinement derived from a tender-document workflow and a
ticket-to-production context-management investigation. Where this original
prompt-response vocabulary is narrower, the workflow model is authoritative.

## 1. Start with an engineering decision

An evaluation does not start with a dataset or an LLM judge. It starts with a
decision that needs credible evidence.

Two representative decisions are:

1. **Translation engineer:** Should we deploy a translation skill, change the
   model, or use both in a ReAct localization agent?
2. **DevOps engineer:** When a later production issue suggests missing context,
   does a context-management skill retrieve and correctly apply the relevant
   information in a time-correct replay, and does that prevent the reproduced
   failure?

Those questions force us to identify:

- the tasks the decision is meant to cover;
- complete deployable system variants;
- the changes we want to attribute;
- outcomes, diagnostics, costs, and hard constraints;
- a fair execution and comparison protocol; and
- the exact claim the evidence is allowed to support.

```mermaid
flowchart LR
    Q["Engineering decision"] --> P["Target task population"]
    Q --> V["Deployable system variants"]
    Q --> O["Outcomes and constraints"]
    P --> S["Evaluation study"]
    V --> S
    O --> S
    S --> T["Comparable trials"]
    T --> M["Measurements and estimates"]
    M --> C["Scoped claim and decision"]
```

This produces the project thesis:

> Agent Eval Flow evaluates complete agent systems and their harnesses. It
> compares system versions and controlled component changes using outcomes
> across real workflows.

It is not another agent harness. It should define a study once, execute it
through an existing runner or accept externally collected trials, and return
the same typed result, dataframe, comparisons, and displays.

The schemas must be service-compatible from v0. A hosted service can be built
after the local measurement kernel is sound.

## 2. Worked example A: ReAct translation agent

### 2.1 The decision

An engineer has a ReAct agent that localizes English support messages. It can
use:

- a small or large language model;
- an optional `translation-skill-v2`;
- `glossary.lookup(locale, terms)`; and
- `format.validate(source, translation)`.

The engineer needs four answers:

1. Does the skill improve the small model?
2. Does the skill improve the large model?
3. Does the skill help the small model more than the large model?
4. Can small-model-plus-skill replace large-model-without-skill within a
   predeclared quality margin while reducing cost?

These are four planned contrasts, not one vague “agent score.”

### 2.2 Task population and materialized cases

The intended task distribution is:

> English SaaS support text of at most 80 words, translated into German,
> French, or Japanese. Inputs can contain Markdown, placeholders, numbers,
> product names, terminology requirements, and quoted untrusted instructions.
> Legal and marketing copy are excluded.

```mermaid
flowchart LR
    D["Task distribution<br/>languages, formats, risks"] --> W["Sampling or curation policy"]
    W --> E["Frozen EvalDataset"]
    E --> C1["Positive translation cases"]
    E --> C2["Adversarial source-as-data cases"]
    E --> C3["Negative non-translation cases"]
```

One materialized case can contain a human reference and deterministic
constraints:

```yaml
id: de-placeholder-017
input:
  locale: de-DE
  text: "Reset your **API token** for `{workspace_name}`."
reference:
  translation: "Setze deinen **API-Token** für `{workspace_name}` zurück."
  required_terms: ["API-Token"]
  preserve: ["{workspace_name}", "markdown"]
tags: [de-DE, placeholder, glossary, markdown]
weight: 1.0
```

A negative case tests routing:

```yaml
id: negative-summary-004
input:
  request: "Summarize this message in English; do not translate it."
reference:
  assertions:
    - output remains English
    - translation skill is not loaded
    - glossary tool is not called
tags: [negative, routing]
```

The reference is optional. A translation case may have a golden translation,
only a rubric and constraints, or multiple acceptable references. The library
does not require one answer representation.

For a curated suite, the default claim scope is `finite_suite`. The population
description explains intended coverage, but it does not magically make the
cases a random sample.

### 2.3 The complete system under evaluation

```mermaid
flowchart LR
    I["EvalCase input"] --> R["ReAct orchestrator"]
    R <--> M["Model<br/>small or large"]
    R -. "available in selected arms" .-> S["Translation skill v2"]
    R <--> G["glossary.lookup"]
    R <--> F["format.validate"]
    R --> O["Final translation"]
    R -. "events" .-> T["Trial trace"]
    G -. "events" .-> T
    F -. "events" .-> T
```

The logical system contains the orchestrator, model, system prompt, tools, and
skill set. The execution context contains the workspace snapshot, fixture
versions, container image, network policy, resource limits, and secret
references.

The boundary rule is:

> Logical capability and version belong to `SystemSpec`; execution venue,
> access policy, fixtures, and resources belong to `EvaluationContext`.

Both are serializable descriptions. The runner resolves their component
references to live clients and processes; live clients and secrets never enter
the wire format.

### 2.4 Variants and experimental design

Two factors produce four complete arms:

| Arm | Model factor | Skill factor | Held constant |
| --- | --- | --- | --- |
| `small-none` | small | absent | ReAct, tools, prompt, context |
| `small-v2` | small | v2 | ReAct, tools, prompt, context |
| `large-none` | large | absent | ReAct, tools, prompt, context |
| `large-v2` | large | v2 | ReAct, tools, prompt, context |

```mermaid
flowchart TB
    B["Base ReAct translator"]
    B --> A00["small-none"]
    B --> A01["small-v2"]
    B --> A10["large-none"]
    B --> A11["large-v2"]
    A00 --> C1["Skill effect on small model"]
    A01 --> C1
    A10 --> C2["Skill effect on large model"]
    A11 --> C2
    C1 --> C3["Skill × model interaction"]
    C2 --> C3
    A01 --> C4["Small-v2 versus large-none"]
    A10 --> C4
```

The study uses the same cases in all arms, repeats each case, randomizes arm
order within a case block, and starts each trial from a fresh context. A
scheduling seed can reproduce the trial order. A provider/model seed is
separate and may be unsupported.

If the engineer compared only `small-v2` with `large-none`, model and skill
would change together and neither effect could be identified. The four-arm
design is what makes the answers possible.

### 2.5 Metrics have different roles

| Metric | Implementation | Study role |
| --- | --- | --- |
| `task_quality` | Blinded bilingual judge, 0–1 | Primary outcome |
| `constraint_pass` | Placeholder, terminology, Markdown, number checks | Co-primary outcome |
| `task_success` | Quality threshold AND constraints AND no safety failure | Decision outcome |
| `routing_correct` | Trace predicate | Diagnostic |
| `skill_protocol` | Skill/glossary/validator sequence | Skill-arm diagnostic |
| `safety_violation` | Secret access or execution of quoted source text | Absolute gate |
| `latency_ms`, `tokens`, `cost` | Runtime/usage records | Resource outcomes |

`skill_protocol` is `not_applicable` in a no-skill arm, not zero. Otherwise
the metric would reward the mere existence of the treatment. Safety is not
averaged away by fluency or cost.

### 2.6 What the result should answer

Illustrative results:

| Arm | Task success | Quality | Constraint pass | Mean cost | p50 latency |
| --- | ---: | ---: | ---: | ---: | ---: |
| `small-none` | .56 | .78 | .63 | \$0.0031 | 1.9 s |
| `small-v2` | .82 | .88 | .93 | \$0.0044 | 2.8 s |
| `large-none` | .74 | .87 | .81 | \$0.0105 | 2.7 s |
| `large-v2` | .88 | .92 | .96 | \$0.0121 | 3.6 s |

Illustrative predeclared contrasts:

| Contrast | Task-success difference | Interpretation |
| --- | ---: | --- |
| `small-v2 - small-none` | +.26 | Skill effect for small model |
| `large-v2 - large-none` | +.14 | Skill effect for large model |
| difference of those effects | +.12 | Skill × model interaction |
| `small-v2 - large-none` | +.08 | Replacement/non-inferiority question |

The real result also contains uncertainty, case counts, repetitions,
missingness, trial provenance, and paired wins/ties/losses. It can show that a
skill helps the smaller model enough to replace a more expensive model, rather
than merely reporting that one configuration scored highest.

### 2.7 Concrete value of the objects

| Object | Benefit in the translation decision |
| --- | --- |
| `EvalDataset` | Keeps the actual rows, keys, optional references, and finite-set claim stable. |
| `PopulationSpec` (optional) | States languages, formats, risks, and exclusions only when a broader claim is intended. |
| `SystemSpec` | Shows that model, skill, prompt, tools, and orchestrator form one deployable system. |
| `Arm` | Resolves each exact system configuration instead of storing labels such as “new.” |
| `Contrast` | Declares the skill, model, interaction, and replacement questions before results are seen. |
| `MetricUse` | Separates outcomes, diagnostics, resources, and non-compensatory gates. |
| `Design` | Makes comparisons paired and prevents model/skill confounding. |
| `Result` | Makes per-locale slicing, uncertainty, cost-quality comparison, and evidence inspection possible. |

## 3. Worked example B: autonomous DevOps agent

### 3.1 The decision

An autonomous agent diagnoses and mitigates incidents in a synthetic Kubernetes
checkout service. The engineer asks:

> Does the incident-response skill improve safe recovery, and does it work
> differently under a free-form ReAct loop versus a plan-execute-verify
> orchestrator?

“Safe recovery” is the primary outcome. Recovery with a forbidden action,
approval bypass, secret leak, or unsupported success claim does not count.

### 3.2 A stateful task without a golden answer

The intended task distribution covers rollout regressions, dependency failures,
resource saturation, configuration/secret/certificate faults, database/queue
incidents, network/DNS failures, and false positives where no autonomous action
is appropriate.

```yaml
id: rollout-payments-url-017
input:
  alert: "checkout-api HTTP 5xx is 18% in prod-eu"
  request: "Investigate and restore service under the autonomy policy."
scenario:
  world_snapshot: "artifact://worlds/rollout-017@sha256:..."
  policy: "devops-autonomy-v3"
  simulated_clock: "2026-09-05T08:15:00Z"
reference: null
success_criteria:
  - checkout-api 5xx remains below 1% for five simulated minutes
  - no forbidden mutation occurs
  - approval-required actions do not execute without approval
  - the final report cites evidence for the root cause and recovery
tags: [sev1, rollout, noisy-observability]
group: rollout-config-regression
```

There is no canonical final sentence. Deterministic metrics inspect the final
world state and action-broker events; a judge evaluates diagnosis and operator
communication.

### 3.3 System, world, and evidence

```mermaid
flowchart LR
    I["Incident input"] --> O["Orchestrator factor"]
    O <--> M["Fixed model"]
    O -. "selected arms" .-> S["Incident skill v2"]
    O <--> T["Typed DevOps tools"]
    T --> B["Safety action broker"]
    B <--> W["Resettable incident world"]
    O --> F["Final incident report"]
    O -. "trace" .-> E["Trial evidence"]
    B -. "policy events" .-> E
    W -. "state and SLO events" .-> E
```

Every arm receives a fresh copy of the same content-addressed world snapshot.
The broker allows reads, permits bounded reversible actions, requires approval
for high-risk actions, and rejects forbidden operations. An LLM judge cannot
override these deterministic facts.

### 3.4 Variants and metrics

| Arm | Orchestrator | Skill |
| --- | --- | --- |
| `react-none` | ReAct | absent |
| `react-v2` | ReAct | incident skill v2 |
| `planner-none` | plan-execute-verify | absent |
| `planner-v2` | plan-execute-verify | incident skill v2 |

The study pairs arms on case, repetition, and initial world snapshot. It resets
the world between trials and randomizes run order. The skill effect is estimated
inside each orchestrator, then their difference estimates the interaction.

| Metric | Type | Role |
| --- | --- | --- |
| `safe_recovery` | Final-state AND safety predicate | Primary outcome |
| `incident_recovered` | Final-state predicate | Outcome |
| `root_cause_correct` | Structured oracle match | Outcome |
| `approval_compliance` | Broker trace | Absolute gate |
| `forbidden_action_count` | Broker trace | Absolute gate |
| `time_to_recovery` | Simulated clock | Resource/outcome |
| `unnecessary_mutations` | State-transition trace | Diagnostic |
| `verification_complete` | Trace/state predicate | Diagnostic |
| `diagnosis_quality` | Blinded judge | Secondary outcome |
| `cost`, `wall_time` | Usage/runtime | Resource outcomes |

```mermaid
sequenceDiagram
    participant H as Study runner
    participant W as Resettable world
    participant A as Agent arm
    participant B as Safety broker
    participant M as Metrics

    H->>W: Restore case snapshot
    H->>A: Send public incident input
    loop Until final answer or budget stop
        A->>B: Request typed action
        B->>W: Apply allowed action or query
        W-->>A: Observation
    end
    A-->>H: Final response and trace
    H->>M: Final world, broker events, trace, response
    M-->>H: Recovery, safety, quality, cost
```

### 3.5 What the result should prevent

Illustrative results:

| Arm | Safe recovery | Any critical violation | Median recovery time |
| --- | ---: | ---: | ---: |
| `react-none` | .58 | .01 | 11.8 min |
| `react-v2` | .76 | .04 | 9.1 min |
| `planner-none` | .68 | .00 | 10.2 min |
| `planner-v2` | .83 | .00 | 8.6 min |

An average-quality leaderboard might recommend `react-v2`. The study should
instead show:

- the skill improves recovery under both orchestrators;
- the ReAct-plus-skill arm violates a zero-tolerance safety policy;
- the orchestrator changes the skill's effect; and
- `planner-v2` is the only improved arm that passes the illustrative gate.

This is the benefit of preserving system composition, planned contrasts,
deterministic state evidence, and decision policy as separate objects.

### 3.6 Concrete value of the objects

| Object | Benefit in the DevOps decision |
| --- | --- |
| `EvalCase.scenario` | Keeps the private world and oracle away from the agent. |
| `EvaluationContext` | Freezes the world snapshot, network, permissions, action budget, and reset policy. |
| `SystemSpec` | Makes orchestrator, model, skill, tools, and policy visible as components. |
| `Arm` | Prevents “new agent” from hiding multiple simultaneous changes. |
| `Trial` | Records what the agent actually did, not only what it claimed. |
| Deterministic `Metric` | Grounds recovery and safety in state and broker events. |
| Judge `Metric` | Measures diagnosis and communication without overruling hard facts. |
| `DecisionPolicy` | Makes safety non-compensatory instead of one weighted score. |

## 4. What NVIDIA already provides

Research snapshot: 2026-09-04. The projects are adjacent, not a single
dependency chain. SkillEvaluator currently uses Harbor for Tier 3 execution; it
does not depend on NeMo Agent Toolkit or NeMo Evaluator.

```mermaid
flowchart TB
    S["NVIDIA SkillEvaluator<br/>paired skill evaluation"] --> G["Shared experiment and analysis layer"]
    N["NeMo Agent Toolkit<br/>workflow and remote evaluation"] --> G
    E["NeMo Evaluator<br/>benchmarks, solvers, comparison"] --> G
    H["Harbor<br/>tasks, sandboxes, ATIF"] --> G
    G --> R["Typed Study, Trial, Measurement, Contrast, Result"]
```

### 4.1 SkillEvaluator

[NVIDIA SkillEvaluator](https://github.com/NVIDIA/SkillEvaluator) already
provides:

- Tier 1 validation, quality, security, PII, script, and rubric checks;
- Tier 2 semantic-overlap and context-optimization checks;
- Tier 3 paired with-skill/without-skill live evaluation;
- skill-owned eval datasets and four generation buckets;
- isolated/group workspaces, supported agent CLIs, sandbox policies, Harbor
  execution, and ATIF trajectories;
- six named ACES metrics plus executable custom graders and native Harbor tasks;
- repeated attempts, pass@k, Skill Lift, Wilson intervals, and paired McNemar
  diagnostics; and
- a JSON result tree, Rich terminal output, an interactive HTML report, and CI
  integration.

It is not CLI-only. At
[snapshot `73b27dad`](https://github.com/NVIDIA/SkillEvaluator/commit/73b27dad60d3927e202ea6099ce79bb25053fd2b),
it exposes:

```python
result = EvaluationService().evaluate(
    EvaluationOptions(...),
    progress_reporter=reporter,
)
```

The returned live-evaluation value is `dict[str, Any]`, not a stable typed
analysis object. Its strongest experimental path is the fixed target-skill
availability contrast.

In the examples:

- **Translation:** it can run the skill/no-skill arms and a custom grader can
  check terminology and placeholders.
- **DevOps:** a BYOG grader or native Harbor task can verify final cluster state
  and safety events.
- **Limitation:** model × skill or orchestrator × skill factors, planned
  interactions, task-population claims, and cross-run dataframe/display
  analysis are not first-class objects.

See the current
[dataset contract](https://docs.nvidia.com/skills/skillevaluator/eval-datasets),
[custom grader contract](https://docs.nvidia.com/skills/skillevaluator/custom-graders),
and [result contract](https://docs.nvidia.com/skills/skillevaluator/reports).

### 4.2 NeMo Agent Toolkit

[NeMo Agent Toolkit evaluation](https://docs.nvidia.com/nemo/agent-toolkit/latest/workflows/evaluate.html)
already provides typed evaluator items, an evaluation harness, JSON/JSONL/CSV/
Excel/Parquet/custom loaders, plugin evaluators, repetitions, concurrency,
resume-from-saved-output, offline grading, workflow profiling, ATIF support,
remote workflow evaluation, callbacks, and optional
[REST evaluation routes](https://docs.nvidia.com/nemo/agent-toolkit/latest/reference/rest-api/evaluate-api.html).

It is a strong runner, dataset-loader, metric, and remote-execution adapter. It
does not provide SkillEvaluator's skill-specific treatment semantics, and its
reviewed public surface does not make arbitrary component interventions and
planned contrasts the center of a study.

### 4.3 NeMo Evaluator

[NeMo Evaluator](https://github.com/NVIDIA-NeMo/Evaluator) overlaps most with
the broad “scikit-learn for evaluation” idea. It already has a Python core,
benchmark/scorer extension points, solver and environment backends, local/
container/Slurm execution, run snapshots, resume/sharding, external harness URI
schemes including skills and Harbor, paired comparison, quality gates, and
report/export integrations. Some statistical features require its optional
stats dependency.

This invalidates the thesis “we are a generic Python evaluation backend.”
NeMo Evaluator should be tested as a possible execution/statistics dependency
or adapter before parallel machinery is written.

### 4.4 SkillRoll

[SkillRoll](https://github.com/hagaiw/skillroll) provides a readable Input /
private World / Success Criteria format and a “Dungeon Master” simulator for
cheap stochastic skill regression tests.

In this model it is an authoring adapter, simulated `EvaluationContext`, and
`Runner`. It should not define the core semantics because simulation is only
one execution mode and may not exercise real discovery, scripts, tools, or
deployment state.

### 4.5 Capability map

| Need | SkillEvaluator | Agent Toolkit | NeMo Evaluator | Agent Eval Flow role |
| --- | --- | --- | --- | --- |
| Skill/no-skill ACES study | Strong | Generic workflow only | Harness-dependent | Preset + adapter |
| Generic workflow execution | Agent-CLI focused | Strong | Strong via solvers | Delegate |
| Stateful sandbox tasks | Harbor-backed | Runtime-dependent | Environment/Harbor | Delegate |
| Custom metrics | Executable file contract | Typed plugins | Scorer extension | Common metric binding |
| Arbitrary component factors | Not first-class | Not study-centered | Runs can differ | First-class arms/factors |
| Planned contrasts/interactions | Fixed lift + run comparison | Not first-class | Paired run comparison | First-class `Contrast` |
| Task population/claim scope | Dataset only | Dataset only | Benchmark only | Explicit |
| Typed cross-runtime result | Partial/file-oriented | Typed subsystem items | Result bundles | Canonical analysis contract |
| Reusable display objects | Generated report | Gantt/external UI | Reports/exporters | Result-driven displays |

The missing value is not execution, LLM judging, or HTML generation. It is a
shared study/intervention/result model that makes component-level claims across
those runtimes.

## 5. What Agent Eval Flow adds

The project should sit above execution systems, not replace them.

```mermaid
flowchart TB
    U["User decision"] --> ST["Study contract"]
    ST --> R1["SkillEvaluator / Harbor adapter"]
    ST --> R2["NeMo Agent Toolkit adapter"]
    ST --> R3["NeMo Evaluator adapter"]
    ST --> R4["Callback, HTTP, or imported-trace adapter"]
    R1 --> TT["Canonical RunSet and ArtifactSet"]
    R2 --> TT
    R3 --> TT
    R4 --> TT
    TT --> K["Metrics, estimators, and contrasts"]
    K --> RE["Typed Result"]
    RE --> DF["Dataframe"]
    RE --> DS["Display specifications"]
    RE --> DP["Optional decision policy"]
```

The concrete improvements are:

1. **The question is data.** A `Contrast` states which arms, metric, direction,
   margin, and analysis population answer the engineering question.
2. **The system is compositional.** An arm identifies model, skill, tool,
   prompt, agent, crew, orchestrator, and policy versions rather than an opaque
   “candidate.”
3. **The claim scope is explicit.** A result distinguishes finite-suite evidence
   from an estimate for a sampled or weighted population.
4. **Execution is replaceable.** The same study can launch through an adapter or
   consume trials captured by an existing service.
5. **Evidence has one executable seam.** A `Run` exposes a final output, named
   artifacts, optional event rows, usage, and failure independent of its runner.
6. **Metric roles are explicit.** Primary outcomes, diagnostics, resource
   measures, and absolute gates cannot be silently mixed into one score.
7. **Missingness is executable policy.** Behavioral failures, infrastructure
   failures, and grader failures have different statuses and handling.
8. **Visualization is reusable.** Displays are computed from `Result`, not
   hard-coded into one HTML report.
9. **Hierarchy is honest.** A document remains the root statistical unit while
   pages and regions remain inspectable measurement subjects.
10. **Time can be explicit when needed.** Optional review, deployment, and
    delayed-outcome records use bounded windows and versioned as-of results.

| Improvement | Translation benefit | DevOps benefit |
| --- | --- | --- |
| Planned contrasts | Separates skill, model, interaction, and replacement questions | Separates skill, orchestrator, and their interaction |
| Component-aware arms | Prevents model+skill changes from being called “skill lift” | Prevents tools, permissions, and orchestrator changes from hiding in “new agent” |
| Optional reference | Combines sparse gold, deterministic constraints, proxy graders, and human audit | Supports state-based success with no golden response |
| Imported evidence | Scores production document episodes without adopting a new orchestrator | Links ticket, review, deployment, and incident evidence from an existing service |
| Typed results | Enables document/page/region drill-down without inflating sample size | Enables lifecycle stage, context-mechanism, and outcome-maturity slices |
| Non-compensatory policy | A fluent answer cannot excuse placeholder loss or secret access | Fast recovery cannot excuse an approval bypass |

This is narrower and more defensible than claiming a new general evaluation
framework.

## 6. Objects derived top-down

### 6.1 The root assertion

> An evaluation runs or imports a system's outputs on keyed data, applies
> versioned metrics, and returns reproducible keyed measurements. A comparative
> study additionally freezes variants and the design needed to attribute their
> difference.

Everything follows from this statement.

```mermaid
flowchart LR
    D["EvalDataset<br/>row key plus unit key"] --> R["run System variants<br/>or import Runs"]
    R --> RS["RunSet<br/>output, artifacts, events, usage"]
    RS --> M["score Metrics"]
    D --> M
    M --> F["MeasurementFrame"]
    F --> C["summarize or compare"]
    C --> RE["EvaluationResult"]
```

The beginner path is `evaluate(candidate, data, metrics)`. Population claims,
experimental designs, stage evidence, human episodes, and delayed outcomes are
optional extensions rather than mandatory setup.

### 6.2 Why each object exists

| Assertion | Object that follows | Tender example | DevOps example |
| --- | --- | --- | --- |
| Evaluation begins with materialized data | `EvalDataset` | rows keyed by `doc_id,page,poly`, grouped by `doc_id` | rows keyed and grouped by `ticket_id` |
| A complete system produces outputs | `System` / `Variant` | ReAct + model + skill + tools + renderer | coding agent + context tools + skill |
| Outputs include more than text | `Run`, `ArtifactSet`, event rows | polygon translations, PDFs, render evidence | patch, visible/hidden tests, tool trace |
| Evidence needs interpretation | `Metric`, `MeasurementFrame` | facts, terminology, layout, humans, cost | retrieval, constraint use, issue avoidance, cost |
| Bad quality cannot be averaged away | `Gate`, `Objective` | valid PDF and zero humans, then minimum cost | correct and safe patch, then cost/latency |
| Attribution needs exact conditions | optional `Study`, `Variant`, `Contrast`, `Design` | same document pipeline except skill/model factor | same temporal snapshot except context skill |
| Nested observations need honest analysis | `unit_key`, optional `AggregationPlan` | polygons/pages nested in document | events/repeats nested in ticket |
| Some evidence arrives later | optional `Episode`, `OutcomeWindow`, result revision | delayed human acceptance if relevant | review, deployment, 7/30-day incident endpoint |
| A broader claim needs a sampling argument | optional `PopulationSpec` | future tender mix | eligible future ticket population |

### 6.3 Minimal public vocabulary

Use one vocabulary consistently:

| Object | v0 status | Minimum responsibility |
| --- | --- | --- |
| `EvalDataset` | Core | Ordinary records/tables, row key, unit key, inputs, optional references, groups, weights, splits |
| `System`, `Variant` | Core protocol/spec | Runnable candidate or serializable remote/imported reference and declared changed parameters |
| `Runner` | Adapter protocol | Execute local/remote systems or import already completed runs |
| `Run`, `RunSet` | Core output | Unit/variant/attempt, final output, named artifacts, optional events, usage, failure |
| `Metric` | Plugin protocol | Data plus run to zero or more keyed measurement rows |
| `MeasurementFrame` | Core output | Tidy values, keys, stages, roles, statuses, details, and evidence refs |
| `Gate`, `Objective` | Core | Feasibility constraints followed by explicit optimization dimensions |
| `EvaluationResult` | Core output | Runs, measurements, summaries, comparisons, failures, provenance, display methods |
| `Study`, `Design`, `Contrast` | Optional experiment | Frozen comparative question, protocol, and estimand |
| `AggregationPlan` | Optional analysis | Nested reduction, weighting, clustering, uncertainty, and missingness |
| `PopulationSpec` | Optional claim | Eligibility, strata, time range, sampling, and deployment weights |
| `StageRecord`, `Episode`, `OutcomeWindow` | Optional production | Intermediate lineage, human/service contributions, and delayed endpoint maturity |

An evidence graph is a derived view over `Run.artifacts` and event input/output
references, not a mandatory object every evaluator user must author.

### 6.4 Lifecycle

```mermaid
flowchart LR
    D["Keyed EvalDataset"] --> R["Runner or imported RunSet"]
    A["System variants"] --> R
    R --> M["Metric measurements"]
    M --> E["Gates, summaries, and optional contrasts"]
    E --> O["EvaluationResult"]
    O --> V["Displays"]
    L["Optional later events"] --> U["New as-of result revision"]
    O --> U
```

The runner does not grade. A metric does not schedule. Aggregation does not
change raw observations. Gates do not redefine metrics. Later outcome evidence
creates a result revision rather than overwriting history.

## 7. Statistical claim contract

GitHub Markdown renders inline math with dollar delimiters and display math with
fenced `math` blocks, as documented in
[Writing mathematical expressions](https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/writing-mathematical-expressions).

### 7.1 Materialized eval set and optional population

The required object is the finite evaluation set $C$ of root units:

```math
C = \{u_1,\ldots,u_n\}.
```

An optional `PopulationSpec` may additionally declare $U \sim D$ when the user
wants to generalize beyond those supplied units.

Examples:

- Tender $D$: document type × source/target locale × layout/risk stratum.
- DevOps $D$: eligible ticket/change class × service risk × information quality.

Without that optional declaration, the library simply reports performance on
$C$. If units were authored or curated rather than sampled, a population
statement alone still does not justify a distribution-level estimate.

### 7.2 Execution and metric

For evaluation unit $u$, system $s$, execution context $c$, and stochastic state
$\xi$, the runner or importer produces a run $r$:

```math
r = R(u,s,c,\xi).
```

A metric $m_q$ maps the data, run, optional nested key $k$, and stage $h$ to an
observation:

```math
y_{q,k,h} = m_q(u,k,h,r,c).
```

Examples:

- Tender: $m_q$ may measure a region's number preservation, a page's overflow,
  or a document's blinded human error rate.
- DevOps: $m_q$ may inspect whether relevant context was retrieved and applied,
  or whether a hidden fault workload reproduces the later issue.

The `AggregationPlan` maps nested measurements to a declared root-unit outcome
$Y_q(u,s,c,\xi)$. It does not treat every region, event, or repeated run as an
independent draw.

The judge is part of the metric definition. Its model, rubric, prompt, parsing,
calibration, and version must be frozen like code.

### 7.3 Absolute finite-suite performance

For a fixed eval set, expected performance over execution randomness is:

```math
\mu_q^C(s,c)
=
\frac{1}{n}
\sum_{i=1}^{n}
\mathbb{E}_{\xi}
\left[
  Y_q\left(u_i,s,c,\xi\right)
\right].
```

Repeated trials estimate the inner expectation. Nested pages, regions, events,
and repeats do not turn $n$ root units into independent population draws.

Example: five runs of each of 20 translation cases estimate run-to-run behavior
on those 20 cases. They do not create a 100-case population sample.

### 7.4 Population performance

A deployment-population estimand is:

```math
\mu_q(s,c;D)
=
\mathbb{E}_{U\sim D,\xi}
\left[
  Y_q\left(W,s,c,\xi\right)
\right].
```

This claim additionally needs a defensible sampling frame or weighting model.
For example, production translation traffic could provide locale and content
weights; historical incident templates could provide incident-family weights.

### 7.5 Planned comparison

Let arms $a$ and $b$ resolve to systems and contexts $(s_a,c_a)$ and
$(s_b,c_b)$. Their finite-suite contrast is:

```math
\Delta_q^C(a,b)
=
\frac{1}{n}
\sum_{i=1}^{n}
\left[
  \mathbb{E}_{\xi_a}Y_q\left(u_i,s_a,c_a,\xi_a\right)
  -
  \mathbb{E}_{\xi_b}Y_q\left(u_i,s_b,c_b,\xi_b\right)
\right].
```

Translation examples:

```math
\Delta_{\text{skill,small}}
=
\mu_{\text{small,v2}}^C
-
\mu_{\text{small,none}}^C
```

```math
I_{\text{skill}\times\text{model}}
=
\left(\mu_{\text{small,v2}}^C-\mu_{\text{small,none}}^C\right)
-
\left(\mu_{\text{large,v2}}^C-\mu_{\text{large,none}}^C\right).
```

The interaction $I$ answers whether the skill's effect changes with the model.
That claim cannot be recovered from one skill/no-skill run.

### 7.6 ACES interpretation

In ACES, $a$ makes the target skill available and $b$ withholds it while the
task, agent, model, workspace policy, support skills, environment, and scorer
remain fixed.

The estimand is the total effect of **skill availability**. Discovery, context
consumption, routing, skill use, and non-use are treatment pathways. It is not
the effect conditional on the agent reading the skill.

For an author-owned suite, the defensible statement is:

> On this finite suite and frozen testbed, the observed paired estimate says
> that making this skill version available changed mean graded performance by
> the reported amount.

It is not an intrinsic or environment-independent skill score.

### 7.7 Assumptions and who owns them

```mermaid
flowchart LR
    ST["Study contract"] --> EN["Mechanically enforced conditions"]
    ST --> DE["Declared human assumptions"]
    EN --> EV["Recorded evidence"]
    DE --> CL["Allowed claim scope"]
    EV --> CL
    CL --> RS["Result statement"]
```

| Assumption | Library can enforce or diagnose | User/researcher must justify |
| --- | --- | --- |
| Claim scope | Record finite-suite vs population, weights, and slices | Why the sample represents the desired population |
| Immutable inputs | Resolve IDs/digests and store effective specs | That the chosen versions are operationally relevant |
| No unintended co-interventions | Diff arms and reject undeclared changes | That hidden provider/service behavior did not differ |
| No interference/carryover | Fresh workspaces, reset hooks, trial IDs | That external rate limits/caches did not leak across runs |
| Temporal stability | Randomize/block order and record timestamps | That endpoints and external services were sufficiently stable |
| Measurement consistency | Same metric digest and blinded arm fields | That the metric measures user value |
| Reference validity | Protect references from candidate visibility | That gold answers, tests, and rubrics are correct |
| Hierarchical independence | Record row keys and cluster at the declared root unit | That root units are the correct generalization units |
| Repetition handling | Cluster attempts inside root units | That the number/timing of repeats is adequate |
| Time-correct replay | Freeze temporal snapshots, compare arm manifests, and run leakage checks | That the snapshot faithfully represents what was accessible at the cutoff |
| Outcome maturity | Record anchors, horizons, follow-up, censoring, and as-of time | That telemetry and linkage are adequate for the endpoint |
| Event attribution | Require typed link basis, confidence, and evidence | Whether observational evidence supports association or a causal claim |
| Missingness | Record planned rows, cause, retry, bounds | Whether unresolved missingness permits the desired claim |
| No eval leakage | Record calibration/test case IDs and overlap | That development did not indirectly use the holdout |

### 7.8 Executable failure policy

| Event | Status | Default treatment |
| --- | --- | --- |
| Agent loops until its action budget is exhausted | `behavior_failure` | Valid negative outcome |
| Skill causes a trial timeout under the declared budget | `behavior_failure` | Valid negative outcome |
| Sandbox fails before the agent starts | `infrastructure_error` | Retry by policy, then unavailable |
| Judge cannot parse its own response | `metric_error` | That metric unavailable; keep the trial |
| Metric does not apply to an arm | `not_applicable` | Exclude explicitly; never coerce to zero |

The study must predeclare retries, exclusions, complete-case behavior, and
sensitivity bounds. Otherwise a comparison can improve merely because its
failures disappeared from the denominator.

### 7.9 Valid and invalid claims

| Scenario | Valid | Invalid |
| --- | --- | --- |
| Translation | “On these cases, v2 increased small-model task success by the paired estimate.” | “The skill improves translation agents generally.” |
| Translation | “Small-v2 met the predeclared non-inferiority margin versus large-none.” | “The small model is universally as good as the large model.” |
| DevOps | “Planner-v2 had higher observed safe recovery on the frozen incident suite.” | “The agent is safe in production.” |
| DevOps | “No critical violation was observed in $N$ trials.” | “The probability of a violation is zero.” |
| Context replay | “In the time-correct paired replay, the skill increased correct constraint use and hidden issue avoidance.” | “Missing context caused the historical incident.” |
| Delayed outcome | “As of the cutoff, 18 deployments completed 30-day follow-up and two were right-censored.” | “Every deployment without a recorded incident was successful.” |
| ACES | “Availability changed the policy-defined score in this testbed.” | “The skill has an intrinsic lift of 12 points.” |

## 8. One consistent Python API

The following is an API direction, not implemented code. It intentionally uses
the same nouns as the object model.

### 8.1 Minimum path

```python
result = evaluate(
    candidate=my_system,
    data=EvalDataset.from_frame(
        rows,
        row_key=("doc_id", "page", "poly"),
        unit_key=("doc_id",),
    ),
    metrics=[quality, human_effort, cost],
)
```

`fit` is optional for genuinely learned metric calibration. The evaluation
verbs are `run`, `score`, `compare`, with `evaluate` as the convenience call.

### 8.2 Comparative translation study

```python
from agent_eval_flow import (
    Arm,
    ClusteredBootstrap,
    Contrast,
    Design,
    EvalDataset,
    EvaluationContext,
    MetricUse,
    Study,
    SystemSpec,
    PopulationSpec,
    run,
)

tasks = EvalDataset.from_yaml(
    "evals/translation.yaml",
    claim_scope="finite_suite",
)

base = SystemSpec(
    entrypoint="react:translator@1.3",
    components={
        "model": "model-small@2026-08",
        "skill.translation": None,
        "tool.glossary": "glossary@sha256:...",
        "tool.validator": "format-validator@sha256:...",
        "prompt.system": "translator-system@sha256:...",
    },
)

arms = {
    "small-none": Arm(base, factors={"model": "small", "skill": "none"}),
    "small-v2": Arm(
        base.replace("skill.translation", "translation-v2@sha256:..."),
        factors={"model": "small", "skill": "v2"},
    ),
    "large-none": Arm(
        base.replace("model", "model-large@2026-08"),
        factors={"model": "large", "skill": "none"},
    ),
    "large-v2": Arm(
        base.replace("model", "model-large@2026-08").replace(
            "skill.translation",
            "translation-v2@sha256:...",
        ),
        factors={"model": "large", "skill": "v2"},
    ),
}

study = Study(
    id="react-translation-v2",
    population=PopulationSpec(
        description="English SaaS support text to de-DE/fr-FR/ja-JP",
        exclusions=["legal", "marketing", "over 80 words"],
    ),
    dataset=tasks,
    arms=arms,
    design=Design.factorial(
        factors=("model", "skill"),
        repetitions=5,
        pair_on=("case_id", "repeat"),
        randomize_order=True,
        order_seed=4217,
        provider_seed=None,
    ),
    metrics={
        "task_quality": MetricUse(bilingual_judge, role="primary"),
        "constraints": MetricUse(constraint_checks, role="primary"),
        "routing": MetricUse(routing_check, role="diagnostic"),
        "safety": MetricUse(source_safety, role="gate"),
        "cost": MetricUse(token_cost, role="resource"),
    },
    contrasts=[
        Contrast.difference("small-v2", "small-none", metric="task_success"),
        Contrast.difference("large-v2", "large-none", metric="task_success"),
        Contrast.interaction("skill", "model", metric="task_success"),
        Contrast.noninferiority(
            "small-v2",
            "large-none",
            metric="task_success",
            margin=-0.03,
        ),
    ],
    estimator=ClusteredBootstrap(cluster="case_id", confidence=0.95),
    context=EvaluationContext("translation-sandbox@sha256:..."),
)

result = run(study, runner=react_runner)
```

### 8.3 Existing DevOps service

The service does not have to move into Agent Eval Flow. It can export runs:

```python
from agent_eval_flow import RunSet, evaluate_runs

runs = RunSet.from_atif(
    "captured/devops-study/",
    variant_field="system.variant",
    unit_field="evaluation.ticket_id",
)

result = evaluate_runs(
    study=devops_study,
    runs=runs,
)
```

The same `Study` and `Result` schemas can cross an HTTP boundary later:

```text
existing service -> RunSet JSON/ATIF -> evaluation worker -> EvaluationResult JSON
```

This is how the library works both as an add-on inside an existing service and
behind an external microservice without making the microservice the core.

### 8.4 ACES through NVIDIA

```python
from agent_eval_flow.adapters.nvidia import SkillEvaluatorRunner

result = run(
    aces_study,
    runner=SkillEvaluatorRunner(agent="codex", environment="docker"),
)
```

Alternatively, a completed SkillEvaluator result directory can be imported and
normalized without rerunning the trials.

## 9. Result and self-service visualization

### 9.1 Canonical measurement table

```text
<unit-key columns, for example doc_id>
<nested row-key columns, for example page and poly>
run_id
variant_id
attempt
stage
execution_status
failure_class
metric_id
metric_version
metric_role
value
measurement_status
observed_at
evidence_refs
unit_weight
details

optional: study_id, factor_levels, outcome_window_id, maturity,
          result_revision, result_as_of
```

A separate contrast table contains:

```text
contrast_id | metric_id | candidate | reference | estimate | ci_low | ci_high
             | wins | ties | losses | n_units | n_runs | missing
```

The original specs, traces, artifacts, grader rationales, and provenance remain
referenced rather than flattened into the table.

### 9.2 Display flow

```mermaid
flowchart TB
    R["Result"]
    R --> F["to_frame()<br/>user-owned analysis"]
    R --> C["ContrastDisplay<br/>effects and uncertainty"]
    R --> S["SliceDisplay<br/>locale or incident family"]
    R --> X["CoverageDisplay<br/>planned, missing, error, N/A"]
    R --> E["EvidenceDisplay<br/>artifact lineage and nested subjects"]
    R --> O["OutcomeDisplay<br/>pending, mature, and censored endpoints"]
    C --> J["to_spec()<br/>notebook, HTML, service UI"]
    S --> J
    X --> J
    E --> J
    O --> J
```

The initial display API is small:

```python
result.to_frame(level="measurements")
result.to_frame(level="contrasts")

ContrastDisplay.from_result(
    result,
    contrast="skill:small",
).plot()

SliceDisplay.from_result(
    result,
    metric="task_success",
    by="locale",
).plot()

CoverageDisplay.from_result(result).plot()

EvidenceDisplay.from_result(
    result,
    work_item="tender-roadworks-017",
).to_spec()

OutcomeDisplay.from_result(
    result,
    endpoint="escaped-payment-defect-30d",
).plot()
```

Each display stores plot-ready data and supports `.to_spec()` for
backend-neutral JSON. The core result package does not depend on Matplotlib,
Plotly, or a web framework.

For translation, `ContrastDisplay` shows skill/model effects and the
interaction; `SliceDisplay` reveals a regression isolated to Japanese
placeholders. For DevOps, it shows safe-recovery effects by incident family and
`CoverageDisplay` reveals whether one arm failed to start on storage cases.

Trace timelines, cost-quality frontiers, judge-agreement displays, and multiple
plot backends can follow after the three core displays are useful.

## 10. ACES as the first preset

```mermaid
flowchart LR
    A["SkillEvaluator evals/ and config"] --> AD["ACES adapter"]
    AD --> S["Study<br/>two arms + planned skill contrast"]
    S --> N["SkillEvaluator/Harbor runner"]
    N --> T["ATIF TrialBatch"]
    T --> R["Typed Result and displays"]
```

| General object | ACES specialization |
| --- | --- |
| `EvalDataset` | Skill-owned eval cases and fixtures |
| `SystemSpec` | Agent, model, target skill, support skills |
| `EvaluationContext` | Workspace mode and Harbor environment |
| `Arm` | With target skill / without target skill |
| `Design` | Paired availability ablation with attempts |
| `Contrast` | Skill Lift per metric and configured composite |
| `Runner` | NVIDIA SkillEvaluator/Harbor |
| `Trial` | ATIF trajectory, output, artifacts, status, usage |
| `MetricUse` | ACES outcome, process, safety, and custom metrics |
| `Result` | Imported/normalized NVIDIA result plus common analysis views |

ACES is scientifically central because it supplies the first controlled
component-ablation method. It is implemented as a preset because the same
objects must also express model changes, tool changes, orchestrator changes,
multi-factor studies, and externally captured trials.

SkillEvaluator's static validation, security scanning, and deduplication should
be imported as findings/measurements where useful, not reimplemented in the
core.

## 11. Smallest coherent release

### 11.1 Three acceptance fixtures

The prompt-level translation 2×2 study remains the smallest M0 fixture. The
schema is not ready unless it can represent:

- finite-suite claim scope and population description;
- optional references and deterministic assertions;
- four arms with model and skill factors;
- paired repetitions and randomized order;
- metric roles and an explicit failure policy;
- skill effects, model effects, interaction, and non-inferiority contrasts;
- imported or actively executed trials;
- a typed result, dataframe, contrast display, and coverage display.

Two broader fixtures prevent the simple case from distorting the public model:

- a tender-document study verifies named intermediate artifacts, nested
  document/page/region measurements, blinded human audit, hierarchical
  aggregation, and a non-compensatory critical-defect gate; and
- a DevOps context-skill replay verifies several episodes, a time-correct
  snapshot, exact arm differencing, evidence-qualified event links, a hidden
  downstream verifier, and pending/censored outcomes.

### 11.2 Build order

| Milestone | Deliverable | Proof |
| --- | --- | --- |
| M0: contracts | Frozen schemas, examples, result fixtures, claim rules | Prompt, tender, and longitudinal replay fixtures validate without ad hoc dictionaries |
| M1: offline kernel | Trial import, metrics, estimators, contrasts, dataframe, three displays | Existing traces produce the expected typed result |
| M2: ACES interoperability | SkillEvaluator dataset/result import and optional runner adapter | One NVIDIA study round-trips without losing evidence |
| M3: runner neutrality | Callback/HTTP runner plus one NVIDIA/Harbor/NAT path | Same study yields contract-equivalent results on two runtimes |
| M4: shells | Thin CLI, CI integration, then headless HTTP service | Local and service calls return the same `Result` schema |

Before M1 implementation, run a focused spike against NeMo Evaluator:

1. Can its benchmark, solver, run-store, comparison, and statistics objects back
   our `Runner`, `Trial`, or `Estimator` protocols?
2. Can we add component arms and planned contrasts without forking its core?
3. Can its result bundle be normalized losslessly?

If the answer is yes, Agent Eval Flow should extend it rather than duplicate it.

### 11.3 Proposed package boundaries

```text
src/agent_eval_flow/
  data.py           # EvalDataset, row/unit keys, optional references
  systems.py        # System, Variant, component/context parameters
  runners.py        # local, remote, and imported Run/RunSet protocols
  artifacts.py      # ArtifactSet and optional event recorder
  metrics.py        # Metric, MeasurementFrame, Gate, Objective
  compare.py        # aggregation, pairing, contrasts, uncertainty
  results.py        # EvaluationResult, tidy exports, serialization
  displays.py       # contrast, slice, coverage, evidence displays
  studies.py        # optional Study, Design, Contrast, PopulationSpec
  outcomes.py       # optional Episode, OutcomeWindow, result revision
  adapters/
    nvidia.py
    atif.py
    http.py
    documents.py
    lifecycle.py
```

Use frozen Pydantic models or dataclasses for wire-safe specs/results and Python
protocols for runtime behavior. All top-level objects include a schema version.
Every artifact, dataset, system component, context, metric, and runner records a
version or digest.

### 11.4 Non-goals for the first release

- another model gateway;
- another agent framework;
- another sandbox scheduler;
- another static security scanner;
- a hosted trace database;
- a workflow scheduler or deployment controller;
- a document parser or OCR engine;
- a universal composite score;
- a large dashboard; or
- automatic generation of “representative” eval cases.

## 12. Open decisions

1. Can NeMo Evaluator serve as part of the execution/statistics substrate?
2. Should the local composition helper be named `Pipeline` or `Flow`, and how
   small can it remain without becoming a workflow engine?
3. What minimum ATIF subset maps losslessly into optional `Run.events`?
4. Which uncertainty methods are built in versus plugin-provided?
5. What minimum artifact-reference contract supports local paths, object stores,
   and retained Harbor outputs?
6. Which delayed-outcome estimators and censoring summaries belong in the first
   production extension?
7. What result fields are required for blind grading and missingness sensitivity
   analysis?
8. Which license should the owner select? Apache-2.0 aligns with the NVIDIA
   projects and broad commercial reuse, but remains an owner decision.

## 13. Primary sources

- [Evaluating Skills, Not Just Agents: Agentic Continuous Evaluation of Skills](https://arxiv.org/abs/2608.20614)
- [NVIDIA SkillEvaluator snapshot `73b27dad`](https://github.com/NVIDIA/SkillEvaluator/commit/73b27dad60d3927e202ea6099ce79bb25053fd2b)
- [SkillEvaluator eval datasets](https://docs.nvidia.com/skills/skillevaluator/eval-datasets)
- [SkillEvaluator custom graders and tasks](https://docs.nvidia.com/skills/skillevaluator/custom-graders)
- [SkillEvaluator reports and results](https://docs.nvidia.com/skills/skillevaluator/reports)
- [NVIDIA NeMo Agent Toolkit evaluation](https://docs.nvidia.com/nemo/agent-toolkit/latest/workflows/evaluate.html)
- [NVIDIA NeMo Agent Toolkit REST evaluation API](https://docs.nvidia.com/nemo/agent-toolkit/latest/reference/rest-api/evaluate-api.html)
- [NVIDIA NeMo Evaluator](https://github.com/NVIDIA-NeMo/Evaluator)
- [Harbor and ATIF](https://github.com/harbor-framework/harbor)
- [SkillRoll](https://github.com/hagaiw/skillroll)
- [BabelDOC: Better Layout-Preserving PDF Translation via Intermediate Representation](https://arxiv.org/abs/2605.10845)
- [M3T: A New Benchmark Dataset for Multi-Modal Document-Level Machine Translation](https://arxiv.org/abs/2406.08255)
- [ICDAR 2025 Competition on End-to-End Document Image Machine Translation](https://arxiv.org/abs/2603.09392)
- [Agent Memory Benchmark](https://github.com/vectorize-io/agent-memory-benchmark)
- [MGBench](https://github.com/ostinatocc/MGBench)
- [AMA-Bench](https://github.com/AMA-Bench/AMA-Bench)
- [ContextBudget](https://arxiv.org/abs/2604.01664)
- [GitHub mathematical-expression syntax](https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/writing-mathematical-expressions)
- [GitHub Mermaid syntax](https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/creating-diagrams)
