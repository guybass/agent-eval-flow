# Your first Agent Eval Flow report

Inspect a historical agent run, read the evidence behind its diagnostics, and
regrade it without running the agent again. This tutorial uses a bundled public
SWE-agent trajectory and local deterministic checks. After installation, it
needs no network connection, model credentials, or live agent runtime.

This tutorial uses the repository checkout, including changes marked
[Unreleased](CHANGELOG.md#unreleased) that have not yet shipped to PyPI.

[Project overview](README.md) · [Toolscore guide](examples/TOOLSCORE.md) ·
[Contributor setup](CONTRIBUTING.md)

## 1. Generate a report

Use Python 3.11 or later. Run these commands from a terminal:

```bash
git clone https://github.com/guybass/agent-eval-flow.git
cd agent-eval-flow
python -m pip install -e ".[cli]"
python examples/archive_review.py --output demo-output/archive
```

A virtual environment is recommended; the [contribution guide](CONTRIBUTING.md#set-up-a-checkout)
includes setup for Windows, macOS, and Linux.

Open `demo-output/archive/report.html` in your browser. The output folder contains:

| Path | What it contains |
| --- | --- |
| `report.html` | The generated report |
| `evidence/` | A cached copy of the source trajectory, identified by its hash |
| `study/` | The saved tasks, candidate configuration, suite, and execution policy |
| `result/` | The saved run capture and evaluation result |
| `regraded/` | A second evaluation of the same capture using a smaller suite |

These are local outputs in the ignored `demo-output/` directory. Keep the
generated folder in place while inspecting results: a saved result can reference
artifact locations outside its own directory.

## 2. Read what the result establishes

The pinned input contains **one historical task and 12 captured actions**.
Three action observations contain `SyntaxError`. The example also counts
21,095 bytes of observation text. These values describe the retained trace;
they do not rerun the agent's commands or verify that its submitted patch
passes the original project's tests.

Use the report's run details and linked evidence to inspect those observations.
Unknown environment and coverage fields remain unknown because the historical
archive did not retain everything the current schema can represent. An agent's
reported cost is an estimate here, not a provider invoice.

This is an inspection example for a known development case. Its action count
is not 12 independent task trials, and the report is not a held-out benchmark.

## 3. Load and inspect the result in Python

Start Python from the checkout root, or save this block as a script there:

```python
from agent_eval_flow import EvaluationResult

result = EvaluationResult.load("demo-output/archive/result")

for row in result.summary():
    print(row.summary_id, row.value.value)

run = result.runs.runs[0]
explanation = result.explain(run.id)
print("Captured events:", len(run.events))
print("Diagnostic measurements:", len(explanation.measurements))

result.report("demo-output/archive/reopened.html")
```

The summary values are `12.0`, `3.0`, and `21095.0` in the example's metric
order. Loading the result and rendering another report make no model calls.
`explain()` returns the task score, measurements, and evidence references for
one run, so your application can inspect the same records as the report.

## Regrade the same capture

A **study** declares the evaluation. A **run set** holds the captured runs. An
**evaluation result** combines that capture with the suite's measurements.
Keeping them separate lets you evaluate retained evidence with a revised suite.

The next block narrows the example to the action-count diagnostic and its
summary. `TraceDiagnostics` is the example's own small evaluator, imported from
the checkout; it is not a built-in quality metric.

```python
from dataclasses import replace
from pathlib import Path

import agent_eval_flow as a
from examples.archive_review import TraceDiagnostics

output = Path("demo-output/archive")
study = a.Study.load(output / "study")
result = a.EvaluationResult.load(output / "result")
metric = TraceDiagnostics()

suite = replace(
    study.suite,
    version="tutorial-v2",
    metrics=study.suite.metrics[:1],
    summaries=study.suite.summaries[:1],
)
regraded = a.EvaluationPipeline(
    study=replace(study, suite=suite),
    evaluators={metric.ref.name: metric},
).eval(runs=result.runs)

regraded.save(output / "tutorial-regraded")
regraded.report(output / "tutorial-regraded.html")
assert regraded.runs.id == result.runs.id
```

Only the requested grading runs here; there is no agent backend binding.
The new result has one summary, `actions_per_task`, with value `12.0`.
Regrading cannot recover evidence that was never captured. Custom evaluators
can call models or external services, so their costs and behavior depend on
the evaluator you register.

Changing a suite after inspecting a case is development work. Freeze task
inputs, grading rules, source revisions, split assignments, and hashes before
using a fresh held-out set to assess an improvement.

## Choose your next workflow

| Goal | Next step |
| --- | --- |
| Understand the importer and evaluator used above | Read [archive_review.py](examples/archive_review.py) |
| Check configuration before an agent runs | Run [assessment_review.py](examples/assessment_review.py) |
| Score captured tool requests | Follow the [Toolscore integration guide](examples/TOOLSCORE.md) |
| Configure a local OpenKritt workflow | Read the [workflow guide](examples/workflows/README.md) |
| Capture and inspect a native agent run | Follow the [OpenKritt local integration](examples/integrations/README.openkritt-local.md) |
| Contribute an importer, check, example, or fix | Read [CONTRIBUTING.md](CONTRIBUTING.md) |

Choose tasks that match your agent's domain. Keep outcome quality, capture
coverage, and process diagnostics separate, and preserve unknowns when the
evidence cannot support a conclusion.
