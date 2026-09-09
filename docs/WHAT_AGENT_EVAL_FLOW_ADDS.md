# What Agent Eval Flow adds to current work

Design discussion, 2026-09-05. Under 1,000 words. Repository documentation and
selected source files were reviewed; no integrations or experiments were run.

**Agent Eval Flow should help identify which skills, flows, models, and harness
configurations improve an agent's work, where they hurt, and which combinations
work well together.** The complete agent system remains the candidate. Changing
one component creates another candidate to test.

Outcome metrics provide evidence for that decision. For a tender, preserved
amounts and usable layouts tell us whether a harness change helped. For an
incident, correct diagnosis and useful evidence serve that purpose. The result
we want is a supported engineering conclusion about the system and its parts.

## What current work already does

There is substantial overlap. We should be explicit about it:

| Current work | Existing capability | Our proposed emphasis |
| --- | --- | --- |
| [ACES / SkillEvaluator](https://arxiv.org/abs/2608.20614) | Paired live evaluations with and without a skill | Extend the experiment vocabulary to flows, models, memory, complete harnesses, and their combinations. |
| [Harness-Bench](https://arxiv.org/html/2605.27922v1) | Compares model–harness configurations using outcomes, traces, and resource use | Support experiments inside a harness, using a team's own projects and tasks. Its paper explicitly separates configuration comparisons from decomposing individual mechanisms. |
| [AgentCompass](https://github.com/open-compass/AgentCompass) | Combines models, benchmarks, harnesses, and environments; handles execution and analysis | Reuse execution support and make declared changes, planned comparisons, and their conclusions the main user workflow. |
| [A²E](https://arxiv.org/html/2608.07346v1) | Captures trajectories and evaluates process, outcome, and runtime properties | Connect those measurements to specific component experiments and the evidence for adopting or rejecting a change. |
| [HarnessLens](https://arxiv.org/html/2608.27311v1) | Proposes harness changes and verifies their effects using relevant tasks and traces | Offer an evaluation contract that developers and optimizers can both use, independently of how changes are proposed. |

Full-harness evaluation, process analysis, and controlled changes already exist.
Our proposed contribution is making them a coherent, reusable Python workflow
across real projects. This is a product and integration hypothesis, not a claim
that we invented a new evaluation science. Existing frameworks can support
parts of it; our implementation must demonstrate useful added value.

## How OpenSRE and OpenKritt make the idea concrete

These are two complementary testbeds with different jobs.

**OpenSRE:** investigate production incidents. Its existing synthetic PostgreSQL
suite runs the production investigation pipeline against telemetry snapshots,
including misleading alerts and missing evidence. It already has diagnosis and
trajectory checks. We can reuse those to test changes to diagnostic guidance,
tool selection, context handling, or the model. The static suite establishes
performance on those scenarios; live recovery requires separate validation.
[OpenSRE incident suite](https://github.com/Tracer-Cloud/opensre/tree/main/tests/synthetic/rds_postgres).

**OpenKritt:** find and validate vulnerabilities in code. It exposes workflow
stages, skills, and model/harness choices, including separate post-processing
configuration. We can test whether focused skills improve discovery, whether a
validation flow reduces false positives while retaining real findings, and
whether those effects depend on the model. Use fixed repository snapshots with
known issues and independent verification.
[Model and harness configuration](https://github.com/Kritt-ai/open-kritt/blob/main/docs-site/scans/model-and-harness.mdx),
[skills](https://github.com/Kritt-ai/open-kritt/blob/main/docs-site/skills/add-and-use.mdx).

OpenKritt also describes its own private benchmarking and workflow-improvement
loop. We would make a shared experiment layer available across projects; we
cannot assume access to that private benchmark.
[OpenKritt launch](https://kritt.ai/open-kritt-launch/).

Compare versions within each project first. Their different purposes do not
justify one OpenSRE-versus-OpenKritt leaderboard.

## The experiment is the central feature

Suppose we suspect that an OpenSRE diagnostic instruction helps avoid mistaking
a symptom for the cause. Run the same incident cases under four configurations:

| Candidate | Model | Targeted guidance |
| --- | --- | --- |
| A | Model 1 | Absent |
| B | Model 1 | Present |
| C | Model 2 | Absent |
| D | Model 2 | Present |

Keep the other settings fixed. B versus A estimates the guidance's effect with
Model 1; D versus C estimates it with Model 2. Comparing those effects reveals
whether the guidance's value depends on the model. Use the same pattern for a
skill or workflow change in OpenKritt. Compare harnesses only where compatible
model configurations and equivalent task access are available.

Repeat independent runs, retain failures and costs, and check promising changes
on held-out cases. OpenKritt's internal `repeat_runs` builds on earlier outputs;
it is part of the candidate's search strategy. Independent evaluation repeats
require fresh scans.
[Workflow repeat semantics](https://github.com/Kritt-ai/open-kritt/blob/main/docs-site/workflows/depth-and-siblings.mdx).

The report should identify improvements, regressions, affected task types,
resource tradeoffs, and uncertainty. It might conclude that a skill helps one
model but harms another, or that a validation stage removes false alarms while
also discarding genuine findings. These are examples of desired conclusions,
not measured results. Traces suggest explanations; controlled interventions
test them. We should not assign causal credit from a plausible trace alone.

## What we should build first

Build one controlled experiment using OpenSRE's existing incident suite, then
express an OpenKritt skill or workflow experiment through the same interface.
Reuse native execution, artifacts, and suitable checks. The first result should
say which change helped, on which tasks, at what cost, with what confidence.

The existing data/candidate/metric/result foundation remains useful. Its purpose
is to support those experiments. If existing infrastructure plus a small
extension delivers this experience, that is a valid implementation of Agent
Eval Flow.
