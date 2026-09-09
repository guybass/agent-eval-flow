# Agent Eval Flow

Evaluate complete agent systems: models, skills, tools, flows and harness settings.
Run candidates through their existing runtimes, retain the execution evidence,
apply your checks, and compare outcomes under different priorities.

The Python package is `agent_eval_flow`. This developer preview follows the
[revision 0.4 data contract](docs/DATA_CONTRACT.md) and
[HLD/LLD](docs/lld/README.md). It is an initial implementation, not a production
maturity claim.

Version 0.5 adds [parallel configuration inspection and behavioral
evaluation](docs/implementation/unified_assessment.md), feeding shared assessments
and explicit decision policies. `AssessmentPipeline` wraps the existing
behavioral API, which retains its v0.4 data contract. Try the offline flow with
`python examples/assessment_review.py --output demo-output/assessment`.

## Try it

```bash
git clone https://github.com/guybass/agent-eval-flow.git
cd agent-eval-flow
python -m pip install -e ".[test,cli]"
python -m pytest
python examples/archive_review.py --output demo-output
```

The example imports a **real, downloaded SWE-agent repair history** containing
12 actions, failed edits, corrections and a submitted patch. It produces:

- `demo-output/report.html`: task results, diagnostics and linked native evidence;
- `demo-output/result/`: the typed saved evaluation;
- `demo-output/regraded/`: a changed evaluation over the same saved execution.

It makes no model calls and never executes commands from the archive. The
[example source](examples/archive_review.py) shows an application-owned importer
and custom metrics using the production library.

Requires Python 3.11 or later. The default tests and archive example need no
model credentials. Native agent runs require the integration-specific setup below.

## Example reports

Read the [OpenSRE and OpenKritt report gallery](docs/examples/agent-evaluation/README.md)
for two controlled before/after experiments: preserving safe incident recovery
while preventing post-report actions, and requiring executed evidence for a
billing-security finding. Each report describes the task, observed behavior,
change and remaining limitations.

These are small development experiments with synthetic tasks, not general
reliability benchmarks. The gallery contains presentation reports; raw local
run captures and unpublished social drafts are excluded. The offline example
above is the reproducible starting point for trying the evaluation API.

## Configure once, then evaluate

```python
from agent_eval_flow import EvaluationPipeline, EvaluationResult

pipeline = EvaluationPipeline(
    study=study,                 # data, candidates, execution policy and suite
    backends=backends,            # existing agent runtimes
    evaluators=evaluators,        # your metric implementations
)
result = pipeline.eval()
result.save("results/experiment")
result.report("results/experiment.html")

saved = EvaluationResult.load("results/experiment")
explanation = saved.explain(saved.runs.runs[0].id)
comparison = saved.compare("baseline", "challenger", metrics=("success_rate",))
selection = saved.select(policy)  # change cost/latency/quality priorities

# Regrade captured runs without binding or invoking an agent backend.
regraded = EvaluationPipeline(study=changed_study, evaluators=evaluators).eval(runs=saved.runs)
```

In an async application, use `await pipeline.aeval()`. Construction and planning
do not start an agent. Changing the selection policy does not rerun agents or
metrics. Comparisons currently provide descriptive differences; they do not
invent confidence intervals.

## The six objects

| Object | Responsibility |
| --- | --- |
| `Study` | Question, candidates, execution conditions and evaluation suite |
| `EvalDataset` | Keyed task tables, public inputs and private evaluator references |
| `Candidate` | Model, skill, tool, flow and harness configuration |
| `RunSet` | Every assignment, output, execution, event, resource observation and failure |
| `EvalSuite` | Versioned metrics, dependencies, score rules and summaries |
| `EvaluationResult` | Saved measurements, explanations, comparisons and selection |

Pydantic validates the shared records. AnyIO coordinates runtime calls. Jinja2
renders self-contained reports. Native agent frameworks keep their loops and
schedulers. Our code owns the common evidence and comparison contracts.

Missing usage remains unknown, failed work remains in the assignment inventory,
and shared grading activities are counted once. A captured JSON `null` is distinct
from absent output. Detail rows explain tasks; they do not inflate the sample size.

## Runtime evidence checks

Use the same optional observation contract to check instructions, tools, model
selection, loops, memory and environment state. Versioned collectors retain
declared and observed values with phase, boundary, coverage and evidence.
Reusable checks run through the existing evaluation pipeline, including saved
run regrading; reports show the expected and observed values. Missing evidence
stays unknown. See the [runtime evidence guide](docs/implementation/runtime_evidence.md).

## Runtime integrations

Concrete adapter modules cover Codex, Claude Code, OpenSRE, OpenKritt, Harbor,
SkillEvaluator imports and NeMo Agent Toolkit batch grading. Their native
capture/lifecycle code is separate from the core. Prepared service connections,
version-specific configuration and deployment credentials remain runtime bindings.
See [implementation status and setup](docs/IMPLEMENTATION.md) for the supported
boundaries and the live checks still required.

OpenSRE and OpenKritt are separate studies: compare candidate versions **within**
each project. Their GCP showcase tests require prepared native runtimes and rich
capture observers. Default offline test success does not establish live compatibility.

A [live local OpenKritt + Codex example](docs/implementation/local_openkritt.md)
has now completed against 22 real Flaskr files: eight workflow executions, four
post-processing executions, 20 tool results and two candidate findings. It saves
native evidence, verifies the result round trip, and regrades without new model
calls. Its score checks integration behavior, not security accuracy.

The separate [local OpenSRE + Codex example](docs/implementation/local_opensre.md)
has completed an investigation of the downloaded HDFS sample: six ReAct
iterations, eight real tool calls and a cited incident report. It retains native
events and CLI receipts, then saves, reloads and regrades the captured result.

## Verification and design

Release checks cover the offline suite, archived-run import and regrading,
package contents, and installed-wheel reporting. See the
[developer-preview release notes](docs/RELEASE.md) for the verified release scope.

The earlier unified-assessment checkpoint passed **400 tests**, with **21
platform/live-integration skips**. Its v0.5 distributions built and the installed
wheel loaded/reported both result formats. See the historical
[assessment checkpoint](docs/implementation/unified_assessment.md#verification-checkpoint).

The original acceptance suite was written before production code. Its **207
offline tests now pass**, with **20 live cases unselected**. The 90 files in the
frozen showcase checkpoint are unchanged. Additional adapter tests cover native
formats, evidence transport and failure handling. The full verification record
is in [implementation status](docs/IMPLEMENTATION.md).

- [Data structures and configuration](docs/DATA_CONTRACT.md)
- [Unified assessment flow: configuration and execution in parallel](docs/UNIFIED_ASSESSMENT_FLOW.md)
- [Module tree and communication graph](docs/lld/README.md)
- [Real-workflow E2E scenarios](tests/e2e/SHOWCASE.md)
- [Visual pipeline and score examples](docs/AGENT_EVAL_FLOW_VISION.md)
- [Library reuse decisions](docs/INTEGRATION_DECISION.md)
- [What this adds to existing work](docs/WHAT_AGENT_EVAL_FLOW_ADDS.md)

The older design and test-writing checkpoints are retained as historical records.

For changes and bug reports, see [CONTRIBUTING.md](CONTRIBUTING.md).
