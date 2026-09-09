# Does Agent Eval Flow survive real agents?

Research date: **7 September 2026**. Source inspection and saved-evidence probes;
no agent runs, benchmark scores or working adapters are claimed.

## Verdict — under 200 words

**Keep the six objects. Preserve facts and let evaluation definitions vary.**

The source review of browser-use, OpenHands, DeepAgents and SWE-agent produced
useful examples. It initially overstated several of them as library failures.
Selected-attempt cost and whole-workflow cost are both legitimate metrics.
Patch correctness and completion before a deadline are different requirements.
The library should support these choices without imposing one interpretation.

A real library failure would be discarding the evidence a metric needs, denying
the evaluator access to it, hard-coding a status-to-success rule, or preventing
a custom aggregate. The proposal now separates those structural responsibilities
from optional helpers and evaluation policies.

The six objects remain a plausible foundation for comparing full agent
configurations. Existing runners, graders and evaluation platforms already
cover much of this work; our value depends on making such comparisons easier
and traceable across projects. That is still a product hypothesis.

The next proof is two integrations using several explicitly different metric
definitions over the same captured evidence. OpenSRE and OpenKritt remain
separate intended studies. Source and fixture inspection do not establish
working adapters or measured agent performance.

## What is a metric choice, and what is a library limitation?

| Example | Evaluation choice | Actual library requirement |
| --- | --- | --- |
| Retry and review spend | Measure selected attempt, all attempts, model spend or operating spend | Preserve available resource evidence and output lineage; expose them to custom metrics |
| Patch followed by timeout | Grade the patch, normal completion, a deadline, or their combination | Keep artifact and stopping status separate; add no hidden success rule |
| Missing observations | Report unknown, apply a declared penalty, estimate, or aggregate available cases | Retain original missingness and record how the derived value was obtained |
| Model-dependent harness | Compare the effective bundle or design a more isolated intervention | Preserve declared/effective configuration and explain the comparison's scope |

The initial per-run metric interface already supports many of these choices.
The more relevant restrictions were mandatory core acceptance behavior and
fixed aggregation. The revised contract adds reusable metric dependencies and
versioned custom reducers. Linear rubrics and threshold gates remain convenient
presets; arbitrary scores and composite success conditions can be metrics.
Pairwise judging across candidates, live continuation, and corrected-reference
rescoring still require explicit interface work; this review does not claim
unlimited extensibility.

## What was challenged

Stars establish the requested popularity threshold, not quality or suitability.
Counts below were observed on the research date and will change.

| Project | GitHub stars | Why it stresses our design |
| --- | ---: | --- |
| [browser-use](https://github.com/browser-use/browser-use) | 112,888 | Browser state, self-reported success, optional usage capture |
| [OpenHands](https://github.com/OpenHands/OpenHands) | about 86,400 | Coding workspaces, condensation, critic attempts, external grading |
| [DeepAgents](https://github.com/langchain-ai/deepagents) | about 29,100 | Scoped subagents, persistent memory, human interrupts, model-dependent harnesses |
| [SWE-agent](https://github.com/SWE-agent/SWE-agent) | 20,269 | Submission review, adaptive attempts, selection and partial cost accounting |

OpenHands’ current main repository is Agent Canvas; the relevant first-party
execution backend is its [software-agent-sdk](https://github.com/OpenHands/software-agent-sdk),
not every agent Canvas can launch. SWE-agent’s README says mini-swe-agent has
superseded it: it is a useful mature stress case, not our recommended new runner.

Pinned source snapshots: [browser-use `e25ab65`](https://github.com/browser-use/browser-use/commit/e25ab65e699af3031a1f2d348526de2844be0e89),
[SWE-agent `3ea751c`](https://github.com/SWE-agent/SWE-agent/commit/3ea751c087f32b16e039a2233dd6eefecef325d5),
[DeepAgents `07d2952`](https://github.com/langchain-ai/deepagents/commit/07d2952d346d81d06bd181db8c560a77f2b51bc8),
and [OpenHands SDK `49ea745`](https://github.com/OpenHands/software-agent-sdk/commit/49ea74587c376b90700f6eff128c3d9b57585d27).
Linked documentation and OpenHands benchmark sources on `main` are observations
on the research date, not frozen specifications.

## Four actual harness experiments we should be able to express

These are proposed experiments, **not measured improvements**. In every row,
the dataset fixes the tasks and initial state; a candidate fixes the complete
configuration; the run set preserves all work; the suite grades task outcomes;
the result compares the declared change and its tradeoffs.

| Project | A → B change within that project | Agent outputs and independent evaluation |
| --- | --- | --- |
| SWE-agent | Disable the supplied submission-review message → enable it. Keep model, tools and task snapshots fixed. | Patch, trajectory, stopping reason, usage and review events. Existing SWE-bench grading determines issue resolution; review activation is diagnostic. |
| OpenHands | Change `LLMSummarizingCondenser.max_size` from 240 → 80; keep both agent and condenser models fixed. | Patch, conversation events, condensation events and usage. Existing patch tests establish resolution; include condenser cost. |
| DeepAgents | Add a citation-checking skill to one named research subagent. Fix the model, other agents, source corpus and initial memory. | Report, citations, files, subagent traces and usage. Independent required-fact and citation checks grade each complete report. |
| browser-use | Compare `use_vision=False` → `True` on the same frozen local web application and task sample. | Delivered files/answers, actions, screenshots and usage. External application state and artifact checks verify task completion. |

The SWE-agent example uses a real [review configuration](https://github.com/SWE-agent/SWE-agent/blob/3ea751c087f32b16e039a2233dd6eefecef325d5/config/benchmarks/250225_anthropic_filemap_simple_review.yaml)
and [submit tool](https://github.com/SWE-agent/SWE-agent/blob/3ea751c087f32b16e039a2233dd6eefecef325d5/tools/review_on_submit_m/bin/submit).
OpenHands’ native runner exposes the [condenser configuration](https://github.com/OpenHands/benchmarks/blob/main/benchmarks/swebench/run_infer.py).
DeepAgents documents [subagent skill scope](https://docs.langchain.com/oss/python/deepagents/subagents).
Browser-use documents that disabling vision also removes the screenshot tool;
that experiment is an observation/tool configuration change, not an isolated
image-input effect. [Agent parameters](https://docs.browser-use.com/open-source/customize/agent/all-parameters).

For these proposed coding studies, we choose **100 for independently resolved,
0 for independently unresolved, unknown when grading evidence is missing**.
Store the test report and patch as the explanation. A saved `submitted` status
cannot supply those 100 points under that definition. A separate metric may
measure agent-declared success. With repeated runs, one issue remains the root
task; internal retries are neither extra tasks nor extra independent repetitions.

The same scored result can select the cheapest candidate meeting a declared
resolution requirement, or the fastest meeting that same requirement. It
cannot invent a winner if required spend or latency is unknown. Reweighting
preferences does not demonstrate that a skill caused the improvement; that
claim also needs a controlled contrast and adequate independent tasks.

## Source examples that evaluation definitions must be able to handle

### 1. “The agent stopped with an error, therefore the task failed”

SWE-agent attempts autosubmission after certain errors, including budget exits.
A resulting patch remains eligible for grading. Our blanket rule that
`agent_error` or `timed_out` forces task failure was too strong.
[Autosubmission source](https://github.com/SWE-agent/SWE-agent/blob/3ea751c087f32b16e039a2233dd6eefecef325d5/sweagent/agent/agents.py).

**Evaluation choice:** grade the patch, the stopping behavior, or both.
**Library responsibility:** preserve the artifact and stopping reason and avoid
an automatic status-to-acceptance rule. A passing intermediate-artifact or
handoff metric can coexist with a pending workflow; it does not mark that
workflow completed.

### 2. “The saved final result tells us what the whole harness cost”

Three independent export traps contradict that assumption:

- SWE-agent’s chooser invokes a model but its review-statistics property returns
  empty statistics; the reported aggregate does not establish chooser spend.
  Its retry cost limits also exclude some final selection/review work.
  [Reviewer/chooser source](https://github.com/SWE-agent/SWE-agent/blob/3ea751c087f32b16e039a2233dd6eefecef325d5/sweagent/agent/reviewer.py).
- OpenHands’ iterative aggregation selects a preferred attempt per issue;
  the final selected row does not sum every earlier attempt’s metrics.
  [Aggregation source](https://github.com/OpenHands/benchmarks/blob/main/benchmarks/utils/iterative.py).
- Browser-use’s history object has a `usage` field, but the inspected
  `model_dump()` serializes only `history`; `save_to_file()` uses that method.
  A history export alone cannot establish preserved aggregate usage.
  [Pinned source](https://github.com/browser-use/browser-use/blob/e25ab65e699af3031a1f2d348526de2844be0e89/browser_use/agent/views.py).

**Evaluation choice:** selected-attempt cost, workflow cost, model spend and
model-plus-compute spend answer different questions. Missing usage can lead to
an unknown total or a declared estimate. **Library responsibility:** retain the
available evidence, identify its scope and expose it to the evaluator. Our
conservative resource helper is one convenient definition; a custom metric
can use a different scope without changing or concealing the captured facts.

### 3. “Only one configuration field changed, so this measures that component”

DeepAgents derives harness profiles from the model for the main agent and
subagents. Switching models can therefore also change prompts, tool behavior
or middleware. Skill inheritance additionally depends on subagent type.
[Pinned construction source](https://github.com/langchain-ai/deepagents/blob/07d2952d346d81d06bd181db8c560a77f2b51bc8/libs/deepagents/deepagents/graph.py).

**Study choice:** compare the effective bundle, or hold the other components
fixed for an isolated intervention. **Library responsibility:** preserve the
configuration evidence, including scope, inheritance and fallbacks, so the
chosen comparison can be interpreted accurately.

### 4. “A fresh run is independent, and a returned call is finished”

DeepAgents can suspend for human decisions and resume the same checkpoint.
A new thread does not clear a persistent store. OpenHands likewise distinguishes
paused and waiting-for-confirmation states.
[Human interrupts](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop),
[persistent backends](https://docs.langchain.com/oss/python/deepagents/backends),
[OpenHands states](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/conversation/state.py).

OpenHands critic attempts selectively rerun failures and can change temperature;
they are not independent repetitions. Its orchestrator also documents that an
instance timeout can cancel waiting while an SDK thread continues running.
[Execution source](https://github.com/OpenHands/benchmarks/blob/main/benchmarks/utils/evaluation.py).

**Library boundary:** preserve suspended states and native references and keep
native attempt identity distinct from experiment assignments. A requested hard
execution limit needs actual enforcement; a metric assessing elapsed time
does not provide that enforcement. V0 does not claim general live human resume
or persistent learning across assignments. These lifecycle/capability gaps
are structural; choosing how to score a timeout is an evaluation decision.

### 5. “If we can ingest it, we can judge it”

Browser-use’s `is_successful()` reads the agent’s final success declaration.
OpenHands’ inference `test_result` can contain the patch; separate evaluation
runs the SWE-bench harness. Neither field name guarantees independent success.
[Browser-use source](https://github.com/browser-use/browser-use/blob/e25ab65e699af3031a1f2d348526de2844be0e89/browser_use/agent/views.py),
[OpenHands grading](https://github.com/OpenHands/benchmarks/blob/main/benchmarks/swebench/eval_infer.py).

**Evaluation choice:** measure native declarations or independently verified
outcomes, with clearly different definitions. There is no universal quality
grader. **Actual structural limit:** our existing
`MetricEvaluator.compute(..., run)` also cannot perform pairwise judging of two
answers; comparing independently graded summaries is a narrower capability.

## What we actually checked locally

The [SWE-agent probe](research/agent_redteam/swe_agent_fixture_probe.json) parses
a pinned, existing upstream test trajectory: five steps, a submitted patch,
7,141 input tokens, 243 output tokens and reported model cost
`0.019520000000000006` USD. Root wall-clock duration, independent correctness
and complete system cost remain unknown. This is a historical fixture, not
a current performance result. Its [probe script](research/agent_redteam/probe_swe_agent_fixture.py)
only fetches and parses JSON; it does not run the patch or agent.

The [browser-use probe](research/agent_redteam/browser_use_source_probe.json)
inspects the pinned source syntax for the history-export and success-declaration
behavior. It executes no upstream code. Both probes retain source identity and
checksums. They establish concrete input-contract traps; they do not establish
end-to-end adapter correctness.

## The strongest objection: this may already be enough elsewhere

[Browser-use’s benchmark](https://github.com/browser-use/benchmark) already
compares frameworks/models with task rubrics and cost. [LangSmith](https://docs.langchain.com/langsmith/evaluation-concepts)
already offers datasets, experiments, evaluators and comparisons, including
pairwise evaluation. [DeepAgents’ evaluation package](https://github.com/langchain-ai/deepagents/tree/main/libs/evals)
and [OpenHands benchmarks](https://github.com/OpenHands/benchmarks) already
provide substantial execution and evaluation machinery.

Consequently, “we can run agents and display scores” is insufficient. Our
product hypothesis is that **one coherent, portable experiment and evidence
contract makes comparing complete configurations easier across distinct
projects**, while retaining domain-specific graders. That benefit is unproven.
If two adapters are mostly framework-specific exceptions, or users can obtain
the same faithful comparison more easily with an existing tool, we should
integrate with that tool and reduce this library’s scope.

## What would convince me enough to build further

1. Import native evidence from two different execution engines and match their
   independent task grades exactly. Explain every intentional difference from
   a native dashboard, such as omitted failures or narrower cost scope.
2. Account for every planned assignment, adaptive attempt and selector charge;
   where the source lacks data, show unknown. Verify export/load preserves it.
3. Challenge the adapters with a valid patch after error, missing attempt,
   missing chooser usage, pause, model fallback, dirty persistent state and
   unconfirmed timeout. Each must produce the promised outcome or limitation.
4. Change cost/latency priorities using saved measurements. The task grades
   and evidence must stay identical; only the preference decision changes.
5. Run a small controlled A/B study through a supported backend, then repeat
   the exercise in the second testbed without adding another major object.
   Measure integration effort as well as whether the comparison is useful.

Use OpenSRE and OpenKritt for our intended product studies, separately. Use the
popular-agent evidence above as adversarial adapter fixtures. The immediate
build should prove importing, independent grading and explanation before
expanding orchestration. The updated [object model](LIBRARY_OBJECT_MODEL.md)
and [typed proposal](contracts/agent_eval_flow.pyi) capture the corrections;
implementation and adoption value remain to be demonstrated.
