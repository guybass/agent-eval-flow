# Inspect AI: reuse and architecture red team

Reviewed **2026-09-08** against our [level-2 design](../LLD_LEVEL_2.md)
and [proposed public contract](../contracts/agent_eval_flow.pyi).
This is a research recommendation, not an approved architecture change or an
integration test. All external links below are primary sources. Source checks
use Inspect commit `3fea5104189022519d8e432950bc73f37103848e` (2026-09-07),
resolved from `main`; this is not a release pin. Documentation remains moving.

## Finding

**Inspect already implements much of the execution-and-grading machinery we
have drawn.** Agent Eval Flow should justify the work it owns through convenient
experiment definitions, faithful evidence import, complete assignment coverage,
and comparison across versions of a whole system. Configurable scorers, an
`eval()` entry point, agent support and offline rescoring are existing capabilities,
so they are insufficient differentiation by themselves. This conclusion is an
inference from the capabilities below, not a claim that our API must become
Inspect's API.

## What the existing library supplies

| Concern | Verified Inspect capability | Consequence for our design |
| --- | --- | --- |
| Public composition | `Task` combines dataset, solver and scorer; samples have input, target, ID, metadata and optional files/sandbox setup. | Our `Study` is a different experiment-oriented interface over familiar concepts. [Tasks](https://inspect.aisi.org.uk/tasks.html), [datasets](https://inspect.aisi.org.uk/datasets.html). |
| Extensible grading | Async custom scorers return values, explanations and metadata; metrics aggregate them. Custom values are not restricted to correctness. | Do not portray configurable metric meaning as absent upstream. [Custom scorers](https://inspect.aisi.org.uk/custom-scorers.html), [scorers](https://inspect.aisi.org.uk/scorers.html). |
| Rescoring | Generation can run without scoring; `score(log, scorer)` or `inspect score` can later apply another scorer. | Our saved-run path overlaps directly; its value must include faithful normalization and the surrounding study/result experience. [Scoring workflow](https://inspect.aisi.org.uk/scoring-workflow.html). |
| Execution | Async scheduling bounds models, samples, subprocesses and sandboxes separately. Sample and scoped execution limits already exist. | Avoid independently rebuilding all these controls. [Parallelism](https://inspect.aisi.org.uk/parallelism.html), [limits](https://inspect.aisi.org.uk/setting-limits.html). |
| Evidence | `EvalLog` exposes specification, plan, results, usage, errors and per-sample records. Its public reader supports incremental sample access and native `.eval` files. | An importer should use the log API and retain the native log as evidence. [Log files](https://inspect.aisi.org.uk/eval-logs.html). |
| Extensions | Packaged tasks, solvers, scorers and tools register through decorators and entry points; custom sandboxes have lifecycle contracts. | Reuse public extension seams, not private implementation imports. [Components](https://inspect.aisi.org.uk/extensions-components.html), [sandboxes](https://inspect.aisi.org.uk/extensions-sandboxes.html). |

## Attacks on our proposed boundaries

### 1. A per-assignment wrapper can conflict with the underlying runner

**Source fact:** Inspect's evaluation implementation guards `eval_async` against
concurrent calls within the same process. Its scheduler instead accepts multiple
tasks/samples inside one evaluation invocation.
[Evaluation source, lines 744-753](https://github.com/UKGovernmentBEIS/inspect_ai/blob/3fea5104189022519d8e432950bc73f37103848e/src/inspect_ai/_eval/eval.py#L744-L753).

**Concrete failure scenario:** our executor dispatches two assignments with
`max_concurrency=2`. A naive `InspectBackend.run()` calls Inspect's `eval()` for
each assignment. The overlapping calls reach the guard and one fails. The
adapter can serialize them, but then it discards much of Inspect's scheduling
benefit. Separate processes can isolate calls, at an operational cost.

**Recommendation:** begin with an Inspect log importer. Then prototype a single
Inspect invocation for a batch/plan before claiming a live adapter fits our
per-assignment protocol. Decide whether a plan-level execution seam earns its
complexity from that prototype. Do not introduce two owners of the same retry,
epoch or concurrency policy. This is a structural integration issue; no metric
definition repairs it.

### 2. Ending the wait does not establish that the work ended

**Our design fact:** the current synchronous `BackendAdapter` owns stopping and
cleanup, while the public protocol has no cancellation acknowledgement or resume
handle. The level-2 document explicitly recognizes this limitation.

**Concrete failure scenario:** an OpenKritt scan is executing on a GCP worker.
The adapter's HTTP polling times out after ten minutes; the scan continues for
another fifteen minutes. A returned terminal-looking record must not imply that
the remote job stopped or that its final cost was observed.

Inspect illustrates why runtime lifecycle is substantial existing machinery:
scoped time limits use cooperative cancellation at yield points, sandbox
providers define cleanup, and interrupted evaluations have recovery/retry
support. Those features still do not prove that an arbitrary external cloud
service accepted a cancellation request.
[Limits](https://inspect.aisi.org.uk/setting-limits.html),
[sandbox lifecycle](https://inspect.aisi.org.uk/extensions-sandboxes.html),
[recovery](https://inspect.aisi.org.uk/handling-errors.html).

**Recommendation:** either keep v0's claim explicitly limited to recorded
observations, or add a backend execution handle with status, cancellation request,
acknowledgement and final reconciliation. Preserve native job identity and
incomplete cost observations. Test this lifecycle with a fake remote service;
the test should check facts, not assign a success score.

### 3. A bridge can change the system being evaluated

**Source fact:** Inspect's agent bridges route third-party agents' model calls
through Inspect's configured model provider. Its sandbox bridge supports agents
using several native API protocols and can control provider-hosted capabilities.
[Agent bridge](https://inspect.aisi.org.uk/agent-bridge.html).

**Concrete failure scenario:** we launch an agent through a bridge while labeling
the candidate as its unmodified production configuration. A different provider
route, generation option or granted tool changes execution, but the candidate
fingerprint does not record it. The comparison then misidentifies what changed.

**Recommendation:** offer bridge execution as an explicit candidate/environment
configuration; capture effective settings and native receipts. Preserve a direct
native path when the experiment requires the original provider behavior. A
wrapper is useful, but its semantic effects must be visible.

## Reuse proposal and dependency cost

Our own code should own cross-run IDs and joins, candidate component differences,
evidence references, capture completeness, dependency-aware measurement assembly,
and the result/query/selection contract. Metric authors own interpretations.
These are product responsibilities; this review does not establish that no
other project provides similar features.

Use Inspect optionally for compatible execution and log import; reuse its
scorers only where their input/output contract fits. Avoid loading Inspect when
a caller only reads an Agent Eval Flow result. Its base requirements include
AnyIO, Pydantic, HTTPX, NumPy, FastAPI, filesystem/cloud packages and terminal UI
packages. This is a substantial platform dependency, not a tiny parser.
[Requirements](https://github.com/UKGovernmentBEIS/inspect_ai/blob/3fea5104189022519d8e432950bc73f37103848e/requirements.txt),
[packaging](https://github.com/UKGovernmentBEIS/inspect_ai/blob/main/pyproject.toml).

There is no defensible percentage of reused code yet: we have a design and E2E
contracts, no production implementation. The meaningful next proof is a native
Inspect log imported into `RunSet`, with sample/epoch identities, failure records,
artifacts and usage preserved, followed by two different suites producing usable
results without re-execution. Only then should we commit to a deeper wrapper.
