# Harbor and SkillEvaluator: integration and maturity evidence

Research date: **2026-09-08**. This extends the [earlier reuse audit](skill_harbor_lld_review.md). The HLD/LLD remain proposals: the recommendations below are **not implemented**. Evidence consists of inspected public source, release metadata, tests, documentation and downstream reports. **No upstream package, agent, benchmark or cloud job was executed.**

## Recommendation

Use **Harbor as the first optional native job-execution path**, and **SkillEvaluator first through imports and a paired-skill preset**. Keep Agent Eval Flow's experiment configuration, cross-backend result interface and selection preferences above them. Use an isolated SkillEvaluator runtime only when executing that workflow. OpenSRE and OpenKritt should retain native adapters; neither needs a forced migration into Harbor. This delegates substantial operational work without making either upstream project our universal domain model.

Harbor has stronger external operational evidence. SkillEvaluator has concrete deployment and research evidence, but its short public release history and ongoing integration fixes warrant a narrower initial commitment. Neither stars nor the existence of tests establishes reliability for OpenSRE, OpenKritt or our GCP setup.

## Evidence of actual use

| Primary evidence | What it establishes | What it does not establish |
| --- | --- | --- |
| LangChain's [February 17 harness-engineering report](https://www.langchain.com/blog/improving-deep-agents-with-harness-engineering) describes using Harbor and Daytona while improving DeepAgents. | An external agent developer reports actually using Harbor to run harness experiments. | That every Harbor backend or later version behaved identically. |
| LangChain's [June 30 integration guide](https://www.langchain.com/blog/unified-stack-for-evaluating-agents) connects native DeepAgents, sandbox execution and LangSmith experiments to Harbor. | A concrete downstream integration pattern: Harbor executes; another product owns result inspection. | That we have exercised that integration, or that OpenSRE/OpenKritt already have it. |
| NVIDIA's [published catalog artifact, commit 738d79e](https://github.com/NVIDIA/skills/blob/738d79edf4336404e1922b7321e959bdb81b6910/benchmarks.json) contains schema-versioned skill/harness results and task, attempt and environment metadata. | Inspectable outputs accompany the vendor's claimed SkillEvaluator use. Its 3,115 result rows are dimension summaries, **not 3,115 independent trials**. | A complete downloadable raw trajectory archive or independent reproduction. |
| NVIDIA's [August 19 report](https://developer.nvidia.com/blog/evaluating-ai-agent-skill-performance-with-nvidia-skillevaluator/) identifies a ClawHub Tier 3 pilot and a Hermes install-time security-scanning pilot. | Named early integrations, with different scopes. | Long production history; Hermes security scanning does not prove Hermes live-evaluation support. |
| NVFlare's [pinned premerge workflow](https://github.com/NVIDIA/NVFlare/blob/49968b2ede43dab779ab8f53e22688f1e8c2f0b8/.github/workflows/premerge.yml) installs SkillEvaluator's security extra in an isolated environment. | Actual downstream CI configuration; isolation explicitly avoids incompatible Click requirements. | That NVFlare uses SkillEvaluator's live-agent tier. |

The [ACES paper](https://arxiv.org/html/2608.20614v1) additionally reports paired experiments across production skills and native harnesses. That is evidence of the authors using the method, not an independent maturity assessment or a substitute for inspecting its implementation.

## Versions are an integration constraint

| Component | Release and inspected source | Consequence |
| --- | --- | --- |
| Harbor | Latest GitHub release observed: [v0.22.0](https://github.com/harbor-framework/harbor/releases/tag/v0.22.0), August 22; tag commit `4407eb5227a2ff4f0d3f16b2eb48849382fdf276`. Current source inspected: `9a2e3b135cc8fb1e41131020f83370cb2f12ae93`, September 8. | Pin the selected runtime; current-source capabilities need not exist in older dependencies. |
| SkillEvaluator | Only GitHub release observed: [v0.1.0](https://github.com/NVIDIA/SkillEvaluator/releases/tag/v0.1.0), August 5, commit `4975c97d49e3623eeab739248e52d83c4aa8f582`. September 8 source `ff349e0d9f03868fc27d1e2bbd62eb849cba66c9` declares version `0.2.1`. | A version declaration is not a published release. Later fixes require a deliberately selected source pin. |

The inspected SkillEvaluator [package definition](https://github.com/NVIDIA/SkillEvaluator/blob/ff349e0d9f03868fc27d1e2bbd62eb849cba66c9/pyproject.toml) requires Python `>=3.12,<3.14` and its Tier 3 extra still pins **Harbor `0.13.2`**. Harbor v0.22.0 requires Python `>=3.12`. Installing both latest lines together is therefore not the proposed solution. Use separate, locked worker environments for SkillEvaluator's supported stack and general Harbor execution. Saved-result inspection should not import either live runtime.

## What can be delegated

**Native execution.** Harbor's [`Job.create` and `Job.run`](https://github.com/harbor-framework/harbor/blob/9a2e3b135cc8fb1e41131020f83370cb2f12ae93/src/harbor/job.py) operate on configured jobs, with native trial scheduling, environments and verification. Its [agent extension interfaces](https://www.harborframework.com/docs/agents) support external agents and agents installed inside the environment. We can provide a task bundle and an adapter that invokes the real OpenSRE or OpenKritt entry point. Whether their dependencies and workflows fit that sandbox is an integration question still to test; replacing their agent loop would not demonstrate the intended harness.

**Skill experiments.** SkillEvaluator's [`EvaluationService.evaluate(options)`](https://github.com/NVIDIA/SkillEvaluator/blob/ff349e0d9f03868fc27d1e2bbd62eb849cba66c9/src/skillevaluator/evaluation/service.py) is a Python facade returning native results. Its [options](https://github.com/NVIDIA/SkillEvaluator/blob/ff349e0d9f03868fc27d1e2bbd62eb849cba66c9/src/skillevaluator/evaluation/options.py) cover baseline inclusion, attempts, support skills, harnesses, models, concurrency and retention. Delegate the complete paired study; do not call that workflow once per already-expanded assignment. Set artifact retention explicitly: native job directories are otherwise normally cleaned up.

**Verification.** Keep environment-dependent checks inside the native lifecycle. SkillEvaluator supports [custom graders and complete native tasks](https://docs.nvidia.com/skills/skillevaluator/custom-graders). Capture their reward files and supporting evidence before teardown, then expose those observations through our result API. Independently supplied post-run checks remain useful when their inputs are captured artifacts.

**Import and regrade.** Preserve native result trees and [ATIF trajectories](https://www.harborframework.com/docs/agents/trajectory-format), adding our identity/evidence mapping. Harbor's [v0.21.0 release notes](https://github.com/harbor-framework/harbor/releases/tag/v0.21.0) include regrading; its [current documented contract](https://www.harborframework.com/docs/run-jobs/regrade) creates a new result directory and starts only a separate verifier environment. It requires a completed single-step source, readable result and artifact manifest, and the required retained bytes. Shared-environment and multistep replay are unsupported. It preserves original agent statistics; importing the original and regrade must not count that same agent spend twice. These inspected current restrictions need confirmation against the selected release. Regrading must not be assumed available through SkillEvaluator's Harbor `0.13.2` pin.

**Local/GCP.** Start with a prepared Linux GCP VM running Docker. This is a proposed deployment of ordinary Linux execution, not a tested GCP integration. Harbor's current [GKE environment](https://github.com/harbor-framework/harbor/blob/9a2e3b135cc8fb1e41131020f83370cb2f12ae93/src/harbor/environments/gke.py) also accepts an existing cluster, region and project; it obtains Kubernetes credentials and assumes one cluster per process. It does not provision our project or establish Vertex model access. SkillEvaluator's native Windows local mode is explicitly unsupported; its CI checks the WSL2/Docker diagnostic. Our Windows development host therefore needs a supported worker path.

## Regression evidence that matters

| Concrete upstream problem and resolution | Why it changes our design judgment |
| --- | --- |
| Harbor [issue #2127](https://github.com/harbor-framework/harbor/issues/2127) reported Daytona sandboxes left running after deletion returned authorization errors. [Merged PR #2166](https://github.com/harbor-framework/harbor/pull/2166), July 3, added a stop fallback for 401/403 and handled already-deleted resources. | Cleanup has provider-specific failure paths. Delegate that lifecycle, but verify cleanup on our chosen provider rather than trusting an abstract `close()`. |
| SkillEvaluator [commit 31391b5](https://github.com/NVIDIA/SkillEvaluator/commit/31391b5a0670cc11072a3a48edfdf72d579f611b), August 29, detects Docker result directories invisible to the daemon. Agents could run while host-side artifacts and rewards were missing. | A successful process is insufficient. Require retained artifact availability and provenance in our integration tests. This fix postdates v0.1.0. |
| SkillEvaluator [PR #111 / commit 73b27dad](https://github.com/NVIDIA/SkillEvaluator/pull/111), September 3, handles Codex tools wrapped inside JavaScript `exec`, with explicit uncertain evidence cases and shared parsing for verification. | Native trace conversion changes with harness formats. Reusing upstream parsers avoids owning that maintenance ourselves; retaining raw traces permits later correction. |

Harbor's pinned [pytest workflow](https://github.com/harbor-framework/harbor/blob/9a2e3b135cc8fb1e41131020f83370cb2f12ae93/.github/workflows/pytest.yml) separates ordinary tests from Linux Docker/Podman runtime tests and Windows container coverage. Relevant source tests include [regrade preservation and rejection cases](https://github.com/harbor-framework/harbor/blob/9a2e3b135cc8fb1e41131020f83370cb2f12ae93/tests/unit/test_regrade.py) and [cleanup under repeated cancellation](https://github.com/harbor-framework/harbor/blob/9a2e3b135cc8fb1e41131020f83370cb2f12ae93/tests/unit/test_trial_cleanup.py).

SkillEvaluator's [CI](https://github.com/NVIDIA/SkillEvaluator/blob/ff349e0d9f03868fc27d1e2bbd62eb849cba66c9/.github/workflows/ci.yml) checks ordinary tests, wheel packaging and platform contracts; its default pytest selection excludes `live` and `integration`. Its [retention tests](https://github.com/NVIDIA/SkillEvaluator/blob/ff349e0d9f03868fc27d1e2bbd62eb849cba66c9/tests/test_harbor_artifact_retention.py) exercise cleanup, explicit retention and partial failures. These are useful test designs, not evidence that every current CI run passed or that live providers were exercised.

## Proposed LLD changes and the next proof

Allow native **job execution** alongside direct per-assignment callbacks. A worker submits one native job, observes its lifecycle and collects a versioned capture; our planner maps native trials and attempts back to study assignments. Give one scheduler ownership of native concurrency and retries. Keep retry history distinct from intended experimental repetitions, and distinguish missing capture from an observed zero.

Add explicit Harbor/SkillEvaluator integration modules and a native-observation import path. Keep configuration, common evidence references, result queries and preference changes in our library. Avoid building a second sandbox manager, skill stager, native trace parser or universal grader before demonstrating an actual gap.

The next acceptance proof should import a retained paired-skill fixture, preserve a deterministic native verifier result, represent a failed trial without losing its artifacts, and regrade a supported capture with zero new agent invocations. Only then run the same integration with a small native agent on prepared GCP. These tests establish software usability and ownership boundaries; they need no claim that our chosen scores are scientifically meaningful.
