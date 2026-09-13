[![Agent Eval Flow — capture evidence, apply checks, compare a change](https://raw.githubusercontent.com/guybass/agent-eval-flow/main/docs/assets/agent-eval-flow-banner.png)](https://guybass.github.io/agent-eval-flow/)

# Agent Eval Flow

[![Release](https://img.shields.io/github/v/release/guybass/agent-eval-flow?include_prereleases&label=release&color=245c50)](https://github.com/guybass/agent-eval-flow/releases)
[![Tests and package](https://github.com/guybass/agent-eval-flow/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/guybass/agent-eval-flow/actions/workflows/tests.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-49659c?logo=python&logoColor=white)](https://github.com/guybass/agent-eval-flow/blob/main/pyproject.toml)
[![Example reports](https://img.shields.io/badge/reports-OpenSRE%20%2B%20OpenKritt-786495)](https://guybass.github.io/agent-eval-flow/)

**Turn agent runs into evidence you can use to improve the system.**

Evaluate complete agent setups: models, instructions, skills, tools, loops,
memory and environment. Run through an existing runtime or import saved logs,
apply your checks, and compare changes in a human-readable report.

**[Browse the reports](https://guybass.github.io/agent-eval-flow/)** ·
**[Try the offline example](#try-it)** ·
**[Read the design](https://github.com/guybass/agent-eval-flow/blob/main/docs/lld/README.md)** ·
**[Report an issue](https://github.com/guybass/agent-eval-flow/issues)**

This is a developer preview. Missing evidence stays unknown; configuration
inspection and behavioral evaluation can run in parallel. The Python package
is `agent_eval_flow`.

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
[example source](https://github.com/guybass/agent-eval-flow/blob/main/examples/archive_review.py) shows an application-owned importer
and custom metrics using the production library.

Requires Python 3.11 or later. The default tests and archive example need no
model credentials. Native agent runs require the integration-specific setup below.

## Example reports

Two real agent workflows, controlled synthetic tasks, and specific changes
measured from retained runs. Click a report to open it in your browser.

| OpenSRE: recovery and stopping | OpenKritt: executed proof |
| --- | --- |
| [![OpenSRE before-and-after evaluation report](https://raw.githubusercontent.com/guybass/agent-eval-flow/main/docs/examples/agent-evaluation/01-opensre.png)](https://guybass.github.io/agent-eval-flow/examples/agent-evaluation/01-opensre.html) | [![OpenKritt before-and-after evaluation report](https://raw.githubusercontent.com/guybass/agent-eval-flow/main/docs/examples/agent-evaluation/02-openkritt.png)](https://guybass.github.io/agent-eval-flow/examples/agent-evaluation/02-openkritt.html) |
| Safe recovery stayed **2/2**; post-report calls fell **1 → 0** after a terminal-tool binding fix. | The demo grounding contract passed **0/1 → 1/1** after requiring execution and clarifying the reporting contract. |
| [Read the report](https://guybass.github.io/agent-eval-flow/examples/agent-evaluation/01-opensre.html) · [Task and fix walkthrough](https://guybass.github.io/agent-eval-flow/examples/agent-evaluation/01-opensre-walkthrough.html) | [Read the report](https://guybass.github.io/agent-eval-flow/examples/agent-evaluation/02-openkritt.html) · [Task and fix walkthrough](https://guybass.github.io/agent-eval-flow/examples/agent-evaluation/02-openkritt-walkthrough.html) |

These are small development experiments with synthetic tasks, not general
reliability benchmarks. The gallery contains presentation reports; raw local
run captures and unpublished social drafts are excluded. The offline example
above is the reproducible starting point for trying the evaluation API.
See the [case details, run identifiers and limitations](https://github.com/guybass/agent-eval-flow/blob/main/docs/examples/agent-evaluation/README.md).

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
stays unknown. See the [runtime evidence guide](https://github.com/guybass/agent-eval-flow/blob/main/docs/implementation/runtime_evidence.md).

## Runtime integrations

Concrete adapter modules cover Codex, Claude Code, OpenSRE, OpenKritt, Harbor,
SkillEvaluator imports and NeMo Agent Toolkit batch grading. Their native
capture/lifecycle code is separate from the core. Prepared service connections,
version-specific configuration and deployment credentials remain runtime bindings.
See [implementation status and setup](https://github.com/guybass/agent-eval-flow/blob/main/docs/IMPLEMENTATION.md) for the supported
boundaries and the live checks still required.

OpenSRE and OpenKritt are separate studies: compare candidate versions **within**
each project. Their GCP showcase tests require prepared native runtimes and rich
capture observers. Default offline test success does not establish live compatibility.

A [live local OpenKritt + Codex example](https://github.com/guybass/agent-eval-flow/blob/main/docs/implementation/local_openkritt.md)
has now completed against 22 real Flaskr files: eight workflow executions, four
post-processing executions, 20 tool results and two candidate findings. It saves
native evidence, verifies the result round trip, and regrades without new model
calls. Its score checks integration behavior, not security accuracy.

The separate [local OpenSRE + Codex example](https://github.com/guybass/agent-eval-flow/blob/main/docs/implementation/local_opensre.md)
has completed an investigation of the downloaded HDFS sample: six ReAct
iterations, eight real tool calls and a cited incident report. It retains native
events and CLI receipts, then saves, reloads and regrades the captured result.

## Verification and design

Release checks cover the offline suite, archived-run import and regrading,
package contents, and installed-wheel reporting. See the
[developer-preview release notes](https://github.com/guybass/agent-eval-flow/blob/main/docs/RELEASE.md) for the verified release scope.

CI runs on **Windows and Ubuntu with Python 3.11, 3.12 and 3.13**. Checks cover
native-format fixtures, evidence transport, missing outcomes, persistence,
regrading and package contents. All **90 frozen acceptance/fixture files** are
verified by hash. Live model tests are opt-in; offline success does not establish
compatibility with every deployed agent runtime. Historical checkpoints remain
in [implementation status](https://github.com/guybass/agent-eval-flow/blob/main/docs/IMPLEMENTATION.md).

- [Data structures and configuration](https://github.com/guybass/agent-eval-flow/blob/main/docs/DATA_CONTRACT.md)
- [Unified assessment flow: configuration and execution in parallel](https://github.com/guybass/agent-eval-flow/blob/main/docs/UNIFIED_ASSESSMENT_FLOW.md)
- [Module tree and communication graph](https://github.com/guybass/agent-eval-flow/blob/main/docs/lld/README.md)
- [Real-workflow E2E scenarios](https://github.com/guybass/agent-eval-flow/blob/main/tests/e2e/SHOWCASE.md)
- [Visual pipeline and score examples](https://github.com/guybass/agent-eval-flow/blob/main/docs/AGENT_EVAL_FLOW_VISION.md)
- [Library reuse decisions](https://github.com/guybass/agent-eval-flow/blob/main/docs/INTEGRATION_DECISION.md)
- [What this adds to existing work](https://github.com/guybass/agent-eval-flow/blob/main/docs/WHAT_AGENT_EVAL_FLOW_ADDS.md)

The older design and test-writing checkpoints are retained as historical records.

For changes and bug reports, see [CONTRIBUTING.md](https://github.com/guybass/agent-eval-flow/blob/main/CONTRIBUTING.md).
