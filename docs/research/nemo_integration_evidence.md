# NeMo integration: capability, adoption and maturity

Research date: **2026-09-08**. This supplements the [source-level audit](nemo_lld_review.md).
Reading code establishes available mechanisms; downstream implementations,
release history and regression coverage provide different evidence. No upstream
tests, agents, model calls or cloud jobs were run for this review.

## Recommendation

**Integrate NAT for optional trajectory grading and existing NAT workflows. Do
not make NeMo Evaluator's new engine our mandatory execution substrate yet.**

NAT's workflow path has concrete downstream adoption. Its smaller ATIF grading
path is released, documented and tested, but considerably newer and has less
direct downstream-use evidence. Both deserve an integration exercise. The
new NeMo Evaluator provides useful execution machinery, but inspected source,
published release and older launcher deployments must be distinguished.

Our HLD/LLD should permit batch evaluators, native jobs, imported measurements
and versioned native configuration. These are integration requirements, not a
reason to create a universal scheduler. Choose the native system per workflow;
keep the direct tool/skill path available. Successful integration means users
can retain their actual harness, consume detailed results, and change evaluation
or selection without losing provenance.

## Dated evidence by module

| Module/path | Release and maintenance evidence | Actual use evidence and limit | Decision |
| --- | --- | --- | --- |
| NAT full workflow evaluation | Published 1.8.0, June 16; earlier workflow/custom-evaluator path predates April's ATIF addition. Current source pinned at `967a679…`, September 3. | AI-Q uses `nat eval` to generate research reports; vulnerability-analysis pins NAT 1.8.0 and invokes `EvaluationRun` with custom evaluators. These are separate downstream NVIDIA projects, not independent production fleet measurements. | Optional integration for an existing NAT workflow. |
| NAT ATIF-only `EvaluationHarness` / `AtifEvaluator` | Interface introduced March 6; standalone path released in 1.6.0, April 10. PyPI `nvidia-nat-eval` latest stable was 1.8.0, June 17. | Unit tests and upstream interoperability examples exist. This review found no verified independent production consumer of this precise small harness. | Integrate as a bounded optional grading path; avoid claiming broad adoption. |
| NAT ATIF profiler evaluators | August 10 fix corrected an evaluator reading a field that the converter did not emit. | Real compatibility defect in the converter-to-evaluator path; passing synthetic unit inputs had not established that boundary. | Pin evaluator/converter compatibility. Test actual converted records. |
| NeMo Evaluator solver/environment/recovery engine | Source `9758d8d…`, September 1, declares 0.4.0; latest public GitHub/PyPI release found was 0.3.0, June 3. Recovery fixes landed July 17 and 22. | Source includes a NAT HTTP solver and regression tests. Reviewed sources do not establish extensive deployed use of the new recovery path. | Candidate native-job integration, after a pinned execution exercise; defer core dependency. |
| Legacy NeMo Evaluator SDK/Launcher | Separate 0.2.x release history and published Nemotron evaluation configurations. | Evidence for the older launcher is useful for that launcher; it does not certify the rewritten engine. | Import compatible retained results or delegate an existing deployment; never combine version claims. |

Release/package evidence: [NAT 1.6][nat16], [NAT 1.8][nat18],
[published NAT package][nat-pypi], [Evaluator release][nel03],
[published Evaluator package][nel-pypi], [source package metadata][nel-package].

## What the downstream projects actually exercise

At AI-Q commit `bf4e67d…`, the DeepResearch configuration contains the real
research workflow, tools, several model roles, dataset mapping and evaluation
concurrency. Its README instructs report generation through `nat eval`, then
external Deep Research Bench scoring. This verifies adoption of **workflow
execution and output capture**, not adoption of NAT's standalone ATIF grader.
[Configuration][aiq-config], [walkthrough][aiq-readme]

At vulnerability-analysis commit `1cf15d6…`, the package pins NAT 1.8.0. Its
evaluation configuration registers a custom dataset parser and three configured
accuracy evaluators. Its CLI wrapper creates `EvaluationRun`, awaits
`run_and_evaluate()`, imposes an overall deadline, inspects artifacts and records
partial outcomes. This is stronger evidence than a README mention: native
evaluation is wired into application code. The wrapper also imports a private
NAT CLI helper, illustrating dependency on an unstable seam that our integration
should avoid. [Package][vuln-package], [config][vuln-config], [wrapper][vuln-cli]

These are maintained downstream implementations, not proof of their users'
production run volume. No verified non-NVIDIA deployment of the exact ATIF-only
harness or new Evaluator recovery path was identified in this bounded search.
NeMo Platform's similarly named evaluator plugin is not adoption proof either:
its metadata points to a separate workspace `nemo-evaluator-sdk`, rather than
the reviewed `nemo-evaluator` distribution. [Platform metadata][platform-package]

## Tests and fixes: evidence, not a guarantee

NAT's harness tests cover dispatch, successful output identity and omission when
an entire evaluator raises. Its base-evaluator tests exercise bounded concurrent
items and a batch whose length is not divisible by concurrency. The CI definition
contains Linux Python 3.11–3.13 test jobs and calls the repository test runner.
These are useful plumbing tests. We inspected definitions, not a completed CI
run proving every optional integration passed. [Harness tests][nat-tests],
[base tests][nat-base-tests], [CI][nat-ci]

The August ATIF latency fix is particularly relevant to our project. A converted
trajectory stored invocation timing in one location while the evaluator read
another; the score became zero. The fix reads the converter's actual field and
adds coverage. We should reuse the corrected implementation and protect the
conversion boundary, not write another latency estimator. This later fix is not
automatically present in the June 1.8.0 release. [Fix and tests][nat-latency]

NeMo Evaluator's July 22 fix addresses a subtler lifecycle issue: recovery after
inference but before verification cannot always reconnect the prior workspace.
The corrected path re-solves affected cases. Tests assert that reexecution
occurs. Its earlier July fix covers failure attribution, timeout capture and
crash-safe checkpoints. These show active maintenance of relevant behavior,
while also showing why a June release cannot inherit September source claims.
[Workspace recovery][nel-recovery], [capture/recovery corrections][nel-failure]

The inspected Evaluator CI runs offline tests and a separate network lane; its
commands exclude `slow` and `e2e`. A successful ordinary CI badge would therefore
not establish live sandbox recovery on GCP. [CI definition][nel-ci]

## Practical integration seams

**Trajectory and custom grading.** Use the documented asynchronous protocol:
`AtifEvaluator.evaluate_atif_fn(Sequence[AtifEvalSample]) -> EvalOutput`.
`EvaluationHarness.evaluate(evaluators, atif_samples)` dispatches several named
evaluators over the shared batch. Samples preserve item identity, trajectory,
optional expected/actual outputs and metadata. Our adapter maps item IDs to
run IDs and retains each raw result and evidence artifact. Dependency layers in
our suite may require several batches; do not invoke the entire native batch
once per `MetricEvaluator.compute()` call. [Protocol][nat-protocol],
[documented package boundary][nat-guide]

The protocol, harness, base evaluator and output-model source files were also
compared byte-for-byte with the `v1.8.0` tag: all four matched the inspected
September snapshot. This narrow grading recommendation therefore does not
depend on unreleased changes. The profiler latency fix is a separate module.

`AtifBaseEvaluator` is optional. It catches item exceptions into a zero score
with an error in `reasoning`, and computes a rounded numeric mean. The outer
harness omits whole-evaluator failures. Therefore neither an upstream mean nor
the absence of `EvalOutputItem.error` establishes complete successful grading.
Preserve native outputs; explicitly map missing evaluators and known error
shapes. Users still choose measurement semantics. Reusing the protocol does not
require inheriting that base class or its aggregation policy. [Base implementation][nat-base]

**Native workflow execution.** Prefer documented `nat eval` configuration and
output artifacts, or the inspected `EvaluationRun` Python entry point for a
version-pinned integration. Keep native workflow/model/tool configuration intact.
One native job owns repetitions, runtime concurrency and its lifecycle. Our
assignment map records what it did; it must not expand the same repetitions
again. Existing completed jobs can enter through imports first.

**GCP feasibility.** A prepared Linux VM/container can host the Python runtime,
or host `nat serve` for the existing HTTP `/generate/full` path. This is an
engineering inference from its Linux packaging and HTTP transport, not a
verified GCP deployment. The remote handler submits inputs and collects streamed
outputs/intermediate steps. An HTTP timeout does not prove the server stopped;
our profile needs a demonstrated cancellation/termination mechanism before
claiming a hard wall-time capability. GCP hosting also does not imply Vertex
model support. [Transport][nat-remote]

## Changes the design should permit

- An optional native **job** boundary, alongside single-assignment calls and
  imports; preserve native configuration and one owner for retries/repetitions.
- An optional **batch evaluator** boundary, with explicit input/output identity,
  dependency batches and evaluator resource attribution.
- Native grades collected before environment cleanup, plus later grades over
  retained evidence. Native recovery that reruns an agent is execution, not
  `eval(runs=saved)` grading-only reuse.
- Optional dependency groups and separate worker environments. NAT base eval
  needs its ATIF package; full workflow use adds core/loaders. The inspected
  Evaluator requires Python 3.12–3.13 and a substantially wider dependency set.
  [Dependency audit](nemo_lld_review.md#4-reusing-the-full-package-can-cost-more-than-implementing-a-small-seam)

Defer automatic provisioning, a generic recovery platform, and direct adoption
of the new NeMo engine's internals until the chosen workflow benefits from them.
Reject making all agents adopt NAT's agent loop: wrapping execution may preserve
a candidate; reconstructing OpenSRE or OpenKritt inside another loop can change
it. The first exercise should be a saved native trajectory batch with one custom
evaluator, followed by one native workflow job with exact assignment coverage.

[nat16]: https://github.com/NVIDIA/NeMo-Agent-Toolkit/releases/tag/v1.6.0
[nat18]: https://github.com/NVIDIA/NeMo-Agent-Toolkit/releases/tag/v1.8.0
[nat-pypi]: https://pypi.org/project/nvidia-nat-eval/1.8.0/
[nel03]: https://github.com/NVIDIA-NeMo/Evaluator/releases/tag/v0.3.0
[nel-pypi]: https://pypi.org/project/nemo-evaluator/0.3.0/
[nel-package]: https://github.com/NVIDIA-NeMo/Evaluator/blob/9758d8d5508e0d1bea79cb99ed56d595a1c3acdc/pyproject.toml
[aiq-config]: https://github.com/NVIDIA-AI-Blueprints/aiq/blob/bf4e67d1564ef8d2ec8f65b5f9001e512befc095/frontends/benchmarks/deepresearch_bench/configs/config_deep_research_bench.yml
[aiq-readme]: https://github.com/NVIDIA-AI-Blueprints/aiq/blob/bf4e67d1564ef8d2ec8f65b5f9001e512befc095/README.md
[vuln-package]: https://github.com/NVIDIA-AI-Blueprints/vulnerability-analysis/blob/1cf15d671355e5fa8892502909a4121313cfc265/pyproject.toml
[vuln-config]: https://github.com/NVIDIA-AI-Blueprints/vulnerability-analysis/blob/1cf15d671355e5fa8892502909a4121313cfc265/src/vuln_analysis/configs/config-eval.yml
[vuln-cli]: https://github.com/NVIDIA-AI-Blueprints/vulnerability-analysis/blob/1cf15d671355e5fa8892502909a4121313cfc265/src/vuln_analysis/eval/nat_cli.py
[platform-package]: https://github.com/NVIDIA-NeMo/nemo-platform/blob/e93a4c5053802f2be80fde04b266178e2720d019/plugins/nemo-evaluator/pyproject.toml
[nat-tests]: https://github.com/NVIDIA/NeMo-Agent-Toolkit/blob/967a679954ec2557ef4c6b3c4e7446793f6d4a97/packages/nvidia_nat_eval/tests/eval/test_eval_harness.py
[nat-base-tests]: https://github.com/NVIDIA/NeMo-Agent-Toolkit/blob/967a679954ec2557ef4c6b3c4e7446793f6d4a97/packages/nvidia_nat_eval/tests/eval/evaluator/test_atif_base_evaluator.py
[nat-ci]: https://github.com/NVIDIA/NeMo-Agent-Toolkit/blob/967a679954ec2557ef4c6b3c4e7446793f6d4a97/.github/workflows/ci_pipe.yml
[nat-latency]: https://github.com/NVIDIA/NeMo-Agent-Toolkit/commit/f796ae6971c980c766b503d1b9699dd32eb0b2d3
[nel-recovery]: https://github.com/NVIDIA-NeMo/Evaluator/commit/26471bc2f652c7f25ba8fded082ae190341addfc
[nel-failure]: https://github.com/NVIDIA-NeMo/Evaluator/commit/77b114f451a729cb9614578aee5ab26b62d8a3bd
[nel-ci]: https://github.com/NVIDIA-NeMo/Evaluator/blob/9758d8d5508e0d1bea79cb99ed56d595a1c3acdc/.github/workflows/cicd-main.yml
[nat-protocol]: https://github.com/NVIDIA/NeMo-Agent-Toolkit/blob/967a679954ec2557ef4c6b3c4e7446793f6d4a97/packages/nvidia_nat_eval/src/nat/plugins/eval/evaluator/atif_evaluator.py
[nat-guide]: https://github.com/NVIDIA/NeMo-Agent-Toolkit/blob/967a679954ec2557ef4c6b3c4e7446793f6d4a97/docs/source/improve-workflows/evaluate.md
[nat-base]: https://github.com/NVIDIA/NeMo-Agent-Toolkit/blob/967a679954ec2557ef4c6b3c4e7446793f6d4a97/packages/nvidia_nat_eval/src/nat/plugins/eval/evaluator/atif_base_evaluator.py
[nat-remote]: https://github.com/NVIDIA/NeMo-Agent-Toolkit/blob/967a679954ec2557ef4c6b3c4e7446793f6d4a97/packages/nvidia_nat_eval/src/nat/plugins/eval/runtime/remote_workflow.py
