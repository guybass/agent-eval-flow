# LLD reuse audit: ACES, SkillEvaluator, Harbor and ATIF

Research date: 2026-09-08. This reviews the proposed [level-2 LLD](../LLD_LEVEL_2.md)
and [public contract](../contracts/agent_eval_flow.pyi). Recommendations below
are not implemented design changes. No upstream code or cloud agents were run.

## The decision

The strongest challenge to our LLD is that our original plan already selected
substantial reusable agent-evaluation machinery, but our module tree stopped
showing how it would be used. [PROJECT_PLAN sections 4 and 10](../PROJECT_PLAN.md)
explicitly placed SkillEvaluator/Harbor below a common experiment and analysis
layer. The current LLD instead names native Codex/Claude adapters and a custom
execution coordinator, without an explicit SkillEvaluator or Harbor integration.

I would retain our candidate definitions, assignment/evidence joins, configurable
measurement contract and result interface. I would not approve rebuilding paired
skill staging, container lifecycle, native agent trace converters or ACES graders
until an integration demonstrates a concrete missing capability. Our executor
can remain small for direct callbacks and tools; full native jobs need a separate
reuse path. This is a boundary correction, not a rejection of the library.

## What actually exists

[ACES](https://arxiv.org/html/2608.20614v1) evaluates live skill availability,
including support skills and bundles, through paired conditions and trajectory
checks. Its reusable interfaces are evaluation assets, task materialization and
ATIF traces. It is directly relevant to agent behavior, tool use and workflows.
The paper supplies methodology; its named implementation is NVIDIA
SkillEvaluator. The paper alone is not a package API or a compatibility guarantee.

At the previously selected
[SkillEvaluator commit 73b27dad](https://github.com/NVIDIA/SkillEvaluator/commit/73b27dad60d3927e202ea6099ce79bb25053fd2b),
[`EvaluationService.evaluate`](https://github.com/NVIDIA/SkillEvaluator/blob/73b27dad60d3927e202ea6099ce79bb25053fd2b/src/skillevaluator/evaluation/service.py)
accepts `EvaluationOptions` and returns the native results dictionary. This is a
real in-process facade, not merely a CLI to scrape.
[`EvaluationOptions`](https://github.com/NVIDIA/SkillEvaluator/blob/73b27dad60d3927e202ea6099ce79bb25053fd2b/src/skillevaluator/evaluation/options.py)
includes agents, model, attempts, concurrency, baseline inclusion, support skills,
workspace and grading modes, output location and artifact retention.

At separately inspected
[Harbor commit 71c39eaf](https://github.com/harbor-framework/harbor/commit/71c39eafbd134d43ae3f489b5e6488b2a157de65),
[`Job.create` / `Job.run`](https://github.com/harbor-framework/harbor/blob/71c39eafbd134d43ae3f489b5e6488b2a157de65/src/harbor/job.py)
resolve and execute sets of trials. `TrialQueue` owns bounded concurrency and
retry configuration; hooks expose agent, verification and trial lifecycle events.
This is agent-evaluation infrastructure we would otherwise implement ourselves.

## Mapping our services to existing machinery

| Our proposed unit | Existing mechanism | Defensible ownership |
| --- | --- | --- |
| `pipeline/api.py`, `preflight.py` | SkillEvaluator's service facade; Harbor job creation and native preflight | **Custom composition:** validate our experiment and resolve the selected execution path. Delegate runtime-specific checks. |
| `execution/planning.py` | SkillEvaluator paired-arm/task/attempt expansion; Harbor job-to-trial expansion | **Custom mapping:** our IDs and assignment coverage. Do not expand the same native repetitions twice. |
| `execution/runner.py` | Harbor `Job`, `TrialQueue`, trial lifecycle | **Reuse for native jobs; small own fallback** for direct callback/tool missions. Two competing schedulers are not justified. |
| `adapters/codex.py`, `claude_code.py`, `process.py` | Harbor installed agents and environment interfaces | **Wrap first** where Harbor's execution conditions fit. Own local-client support only for an explicitly different user environment. |
| `objects/runset.py`, `execution/capture.py` | ATIF `Trajectory`, `Step`, tool observations, subagent references and validators | **Reuse validation + custom envelope:** retain native traces, add our assignment joins and observations. Avoid inventing another complete trajectory format. |
| `evaluation/engine.py`, `scoring.py` | SkillEvaluator default/custom grading and Harbor verifiers | **Reuse grader implementations; custom normalization** into measurements with evidence. A small dependency executor can serve our independently chosen callbacks. |
| `execution/importing.py`, `storage/artifacts.py` | Native result trees, retained trajectories and rewards | **Own adapter and integrity mapping:** preserve original artifact bytes and map native IDs. |
| `results/query.py`, `comparison.py`, `selection.py` | Native reports, arm summaries and skill-lift results | **Own common interface**, but reuse native summaries when their declared meaning matches. Changing selection preferences is our separate result operation. |

The native task and agent extension seams are documented in
[Harbor's agent guide](https://www.harborframework.com/docs/agents). Our direct
adapters should identify which capability they add beyond those seams before
we accept the continuing maintenance cost.

## Four concrete attacks on the LLD

**1. A one-assignment interface does not naturally wrap a paired study.**
Imagine 10 tasks, two skill conditions and three attempts: 60 assignments.
SkillEvaluator's
[`run_harbor_eval`](https://github.com/NVIDIA/SkillEvaluator/blob/73b27dad60d3927e202ea6099ce79bb25053fd2b/src/skillevaluator/tier3/harbor/runner.py)
already expands that matrix. Calling its full evaluation once per assignment
would launch the matrix repeatedly. A careful adapter could partition datasets
and disable the extra arms/attempts, but then it bypasses useful native study
coordination and adds staging overhead. A hidden batch cache inside `run()` would
also violate our promise that each invocation independently executes its request.

**Resolution:** either add a clearly owned batch-execution seam before level 3,
or initially run SkillEvaluator explicitly and import its capture. Test that one
native job yields exactly the declared 60 assignment mappings. This is a software
contract test; none of the 60 tasks needs a good agent score.

**2. We separate grading too late for live environment checks.**
A tool mission creates a database row. A native verifier checks that row before
the sandbox closes. Our later `EvaluationEngine.compute(context, run)` cannot
query the destroyed database. Reimplementing the verifier as a post-run callback
would lose the observation regardless of how good its metric definition is.
Harbor's
[`Trial`](https://github.com/harbor-framework/harbor/blob/71c39eafbd134d43ae3f489b5e6488b2a157de65/src/harbor/trial/trial.py)
already has verifier phases, artifact collection and separate agent/verifier
timeouts. SkillEvaluator's
[BYOG/BYOT contract](https://docs.nvidia.com/skills/skillevaluator/custom-graders)
allows custom code and complete native tasks.

**Resolution:** let the native lifecycle collect the reward and supporting
artifact, then map that observation into our measurement result. Saved-run
evaluation can reuse the captured observation; it must not pretend to repeat a
live-state check. Test retained reward provenance and zero new agent invocations.

**3. A generic event dictionary can accidentally become a second ATIF.**
Two subagents can share a session ID while having different trajectory IDs.
ATIF already expresses this and validates embedded identities and tool-call
references in its
[`Trajectory` model](https://github.com/harbor-framework/harbor/blob/71c39eafbd134d43ae3f489b5e6488b2a157de65/src/harbor/models/trajectories/trajectory.py).
Flattening everything into `Event.fields` and rebuilding those rules creates
maintenance work without adding capability. Conversely, replacing `RunSet`
entirely with a trajectory loses our planned-assignment and missing-capture
envelope.

**Resolution:** keep the original ATIF artifact authoritative, reuse its validator,
and project only documented fields into our cross-run view. Test a tool call and
two embedded subagents with colliding session IDs. The
[ATIF documentation](https://www.harborframework.com/docs/agents/trajectory-format)
also provides existing typed models and native-agent converters.

**4. “Import this library” has a real version boundary.**
The pinned SkillEvaluator
[`pyproject.toml`](https://github.com/NVIDIA/SkillEvaluator/blob/73b27dad60d3927e202ea6099ce79bb25053fd2b/pyproject.toml)
requires Python `>=3.12,<3.14` and its Tier 3 extra pins `harbor==0.13.2`.
Our contract currently uses Python 3.11-style typing, without a chosen production
Python floor. Installing latest Harbor alongside that SkillEvaluator revision is
not a verified combination; the separately inspected Harbor commit is not claimed
to be its dependency.

**Resolution:** optional, version-tested integration extras or an isolated worker
with a versioned artifact contract. Core saved-result inspection should not
require live-agent dependencies. Test installation/import boundaries separately
from model execution.

## What would convince us

Before creating more runtime classes, require one retained native skill study to
round-trip through our result API; one tool/environment task to preserve a native
verifier observation; and one direct local mission to demonstrate the small
fallback path. If these need only adapters and result joins, our original shared
analysis-layer idea is justified. If they require rewriting native schedulers,
task staging and trajectory parsers, the LLD boundary is wrong. A reuse percentage
would conceal this distinction: a short adapter can delegate most operational
complexity, while many small record classes can still represent worthwhile code.
