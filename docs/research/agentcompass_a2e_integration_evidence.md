# AgentCompass and A2E: evidence for an integration choice

**Checked 8 September 2026.** This extends the
[source-to-LLD review](agentcompass_a2e_lld_review.md). It distinguishes implemented
capabilities, reported use and demonstrated maintenance. No installation, upstream
tests, agent execution or cloud deployment was performed.

## Recommendation

Neither project should become a mandatory dependency of our first release on
the evidence available. Both contain useful implemented mechanisms, and both deserve
optional integration paths.

AgentCompass is the stronger candidate of these two for delegating a complete
benchmark/harness/environment job: it exposes an intentional public Python
launcher API and implements native CLI harnesses. A2E is especially relevant
for importing instrumented agent experiments and reusing selected process
evaluators. Its full campaign runtime currently belongs to a large, coordinated
source workspace.

For the initially planned paired skill demonstration, retain SkillEvaluator/
Harbor as the first native-job candidate. Evaluate NeMo's narrower grading path
when its trajectory evaluators meet the scenario. Do not stack all four runtimes
under one execution. AgentCompass could replace that first choice if the actual
workflow needs its benchmark/environment combination; the HLD should permit it.

This is a scope and dependency decision. The research found useful external
experiments and concrete fixes, but did not establish sustained independent
production deployments or a stable release compatibility promise for either
project.

## Release and API evidence

| Evidence | AgentCompass | A2E |
| --- | --- | --- |
| Inspected source | [`c30a5d9472c0ed9afefad7bdabbf096db4c0f92a`](https://github.com/open-compass/AgentCompass/tree/c30a5d9472c0ed9afefad7bdabbf096db4c0f92a), September 8 | [`d524a03c8bad0ccb735202e4c3468cece396f734`](https://github.com/datamllab/A2E/tree/d524a03c8bad0ccb735202e4c3468cece396f734), September 7 |
| GitHub releases/tags | Both inventories returned empty | Both inventories returned empty |
| Package publication check | PyPI `agentcompass` returned 404 | PyPI `ageneval-task-orchestrator`, `a2e-client`, `a2e-evals` returned 404 |
| Usable source API | Public sync/async `run_evaluation_request` and `launch` exports | Public campaign records and lazily imported `CampaignController`; local workspace installation |

Release inventories: [AgentCompass](https://github.com/open-compass/AgentCompass/releases),
[A2E](https://github.com/datamllab/A2E/releases). These are dated observations,
not a claim that source cannot be installed. Package version strings inside
`pyproject.toml` do not establish a published wheel. The queried package metadata
endpoints were [agentcompass](https://pypi.org/pypi/agentcompass/json),
[ageneval-task-orchestrator](https://pypi.org/pypi/ageneval-task-orchestrator/json),
[a2e-client](https://pypi.org/pypi/a2e-client/json) and
[a2e-evals](https://pypi.org/pypi/a2e-evals/json).

AgentCompass's top-level exports provide the correct integration boundary; our
earlier discussion of `UnifiedEvaluationRuntime` identified implementation
overlap, not the API we should import directly.
[Public exports](https://github.com/open-compass/AgentCompass/blob/c30a5d9472c0ed9afefad7bdabbf096db4c0f92a/src/agentcompass/__init__.py),
[launcher](https://github.com/open-compass/AgentCompass/blob/c30a5d9472c0ed9afefad7bdabbf096db4c0f92a/src/agentcompass/launcher.py).

A2E is organized as packages, but its task workspace explicitly resolves shared
server, evaluator, model-gateway, harness and OpenInference members locally.
Some instrumentor versions are overridden there. Installing a generic similarly
named upstream instrumentor is therefore not evidence that it reproduces A2E's
capture behavior. An isolated pinned checkout/container is a more honest first
integration than advertising a simple package extra.
[Workspace configuration](https://github.com/datamllab/A2E/blob/d524a03c8bad0ccb735202e4c3468cece396f734/task/pyproject.toml),
[campaign exports](https://github.com/datamllab/A2E/blob/d524a03c8bad0ccb735202e4c3468cece396f734/task/packages/ageneval-task-orchestrator/src/ageneval/task/orchestrator/__init__.py).

## Evidence of actual use, with limits

AgentCompass has issue reports containing concrete runtime observations from
accounts outside the project's collaborator role. Issue 287 describes an actual
FrontierScience run: URL-array handling failed and retried requests unnecessarily.
The linked repair was merged as `c74125ec`. This is evidence of use plus a
maintainer response; it is not a deployment scale measurement.
[Report](https://github.com/open-compass/AgentCompass/issues/287),
[merged fix](https://github.com/open-compass/AgentCompass/commit/c74125ec689178775a5bc1bb9f68dc97e88739f9).

Issue 288 reports a Terminal-Bench Codex replay containing nine native tool calls
while derived analyzers counted zero. The author describes retained ACTF payloads
and a producer/consumer format mismatch. At our pinned revision,
`BasicMetricAnalyzer` still extracts names through the OpenAI `function.name`
shape. Thus the source supports the reported compatibility concern; we did not
rerun that replay. Native trace preservation and correct downstream interpretation
must be validated separately.
[Report](https://github.com/open-compass/AgentCompass/issues/288),
[pinned consumer](https://github.com/open-compass/AgentCompass/blob/c30a5d9472c0ed9afefad7bdabbf096db4c0f92a/src/agentcompass/analyzers/basic/basic_metric_analyzer.py).

A2E has an external contributor's PR 2 describing Pi/DeepSeek harness experiments
on Terminal-Bench, SWE-bench and sampled QA tasks, with test and span-count
reports. It was **closed without merging** on August 26. This demonstrates a
reported extension attempt and useful downstream experience. It does not make
those harnesses supported by the inspected main branch, nor independently verify
the contributor's experimental results. The report also explains why an earlier
host-tool bridge was inadequate inside benchmark containers.
[Contributor report and PR status](https://github.com/datamllab/A2E/pull/2).

Searches for the exact repository identities mainly found the papers, mirrors,
announcements and these contributions. No independent maintained production
consumer was verified. Absence from this search is not proof of absence. Stars,
fork counts and the projects' own benchmark tables were not treated as deployment
evidence.

## Maintenance and regression evidence

AgentCompass's checked-in workflow runs pre-commit linting. Its merged PR 279
records that a temporary two-test regression harness was removed because the
repository did not yet have a test setup. The fix itself is valuable: analyzer
exceptions become unknown judgments and remain visible in summaries. That is
evidence of maintenance, with a weaker durable regression barrier than committed
tests for the same behavior. No test-running workflow was verified in the GitHub
workflow inventory.
[Lint workflow](https://github.com/open-compass/AgentCompass/blob/c30a5d9472c0ed9afefad7bdabbf096db4c0f92a/.github/workflows/lint.yml),
[merged PR 279](https://github.com/open-compass/AgentCompass/pull/279).

Its August 28 OpenSandbox fix also shows a real adapter obligation: SDK timeout
termination was not represented by the formerly assumed exit code. The patch
uses error metadata and elapsed time to identify it. A shared `timed_out` field
does not eliminate backend-specific interpretation.
[Pinned fix](https://github.com/open-compass/AgentCompass/commit/78f38b4495e60465dcf0d2d0363c37cf18c0d634).

A2E commits meaningful tests for concurrent subprocess work, cancellation,
large results transferred through atomic files, digest mismatch rejection and
stale server identifiers during recovery. These inspect operational behavior,
not whether a model's answer deserves a score. They were read, not executed;
the available workflow inventory showed only GitHub's dependency graph workflow,
so continuous execution of this coverage remains unverified.
[Process tests](https://github.com/datamllab/A2E/blob/d524a03c8bad0ccb735202e4c3468cece396f734/task/packages/ageneval-task-orchestrator/tests/test_trial_process.py),
[recovery test](https://github.com/datamllab/A2E/blob/d524a03c8bad0ccb735202e4c3468cece396f734/task/packages/ageneval-task-orchestrator/tests/test_controller_server_recovery.py).

Two relevant repairs have implementation and tests: preserving array element
types when exposing native tools, and preventing silent LlamaIndex streams from
being expired merely because they are quiet. The latter hardening commit also
addresses async cancellation and blocking tool work. These demonstrate useful
engineering depth; they do not establish that arbitrary remote work can always
be stopped.
[Tool schema fix](https://github.com/datamllab/A2E/commit/d64480cad79f939cec0ab6c30c3cce708cf79d69),
[runtime/capture hardening](https://github.com/datamllab/A2E/commit/8489fb0904cf7c4142daf30018d874d97dfe3f10).

## Module-specific first-build ownership

| Our LLD area | Relevant evidence | Decision |
| --- | --- | --- |
| `pipeline/`, `execution/runner.py` | AgentCompass public launch API; A2E campaign controller | Add an optional whole-job path. One native runtime owns scheduling and retries. Keep the direct dispatcher for small missions. |
| `adapters/process.py`, `worker.py` | Native environment/session lifecycles; A2E subprocess tests | Delegate when using their job. Do not extract private scheduler pieces into a new hybrid runtime. Prepared GCP transport still needs a proven integration. |
| `adapters/codex.py`, `claude_code.py` | AgentCompass native harnesses plus ACTF compatibility report | Prefer its native launcher when conditions fit, but test trace projection independently. The existence of a parser does not guarantee all analyzer compatibility. |
| `execution/capture.py`, `importing.py` | ACTF artifacts; A2E OTel/OpenInference spans and trial identity | Build explicit versioned importers preserving raw artifacts and native IDs. Add missing planned assignments around them. |
| `evaluation/engine.py` | Existing analyzers/graders with different context and lifecycle needs | Wrap selected evaluators. Import already-produced verifier observations without rerunning them. Keep our small dependency graph. |
| `storage/`, `reporting/`, `results/` | Native stores/server/viewers | Own the portable experiment envelope and offline result operations; link native evidence. A2E server remains optional. |

The LLD should consequently show `native_jobs/` or an equivalent execution
boundary, per-format importers, and one native-ID mapping owner. These are proposed
responsibilities, not a demand for more classes. Compatibility manifests should
record the source revision, accepted artifact schema and fields actually observed.

The decisive next proof is one complete native job mapped into our existing
consumer E2Es: preserved repetitions and retries, a nested tool trace, a retained
failure, saved-run regrading and an offline explanation. Include one format-mismatch
fixture inspired by AgentCompass issue 288. If we cannot map a supported native
job without replacing its harness or losing evidence, record that precise failure
before expanding our own runtime.
