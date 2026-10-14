[![Agent Eval Flow](https://raw.githubusercontent.com/guybass/agent-eval-flow/main/examples/assets/agent-eval-flow-banner.png)](https://guybass.github.io/agent-eval-flow/)

# Agent Eval Flow

[![PyPI](https://img.shields.io/pypi/v/agent-eval-flow?color=245c50)](https://pypi.org/project/agent-eval-flow/)
[![Tests](https://github.com/guybass/agent-eval-flow/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/guybass/agent-eval-flow/actions/workflows/tests.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-49659c)](https://github.com/guybass/agent-eval-flow/blob/main/pyproject.toml)

**Evaluate agent runs. Inspect the evidence. Compare what changed.**

Agent Eval Flow (AEF) is a Python library for evaluating complete agent systems:
models, instructions, skills, tools, execution loops, memory, and environment.
Use your existing runtime or import retained runs, apply explicit checks, and
open a report that connects results to their evidence.

[Quickstart](#quickstart) · [Tutorial](https://github.com/guybass/agent-eval-flow/blob/main/TUTORIAL.md) ·
[Example reports](https://guybass.github.io/agent-eval-flow/) ·
[Toolscore integration](https://github.com/guybass/agent-eval-flow/blob/main/examples/TOOLSCORE.md) · [Contributing](https://github.com/guybass/agent-eval-flow/blob/main/CONTRIBUTING.md)

## Why Agent Eval Flow?

- **Keep your agent's own loop.** Runtime adapters capture native execution;
  importers let you evaluate retained runs without another agent invocation.
- **Make results inspectable.** Preserve source-linked events, outputs,
  failures, and unknowns alongside application-defined checks.
- **Separate capture from grading.** Save a run once, reopen its report, or
  apply a different evaluation suite to the retained evidence.
- **Compare system changes.** Review candidate outcomes, diagnostics, resource
  observations, and explicit selection policies together.

<a id="try-an-offline-example"></a>

## Quickstart

Requires **Python 3.11+**. Install the library with
`python -m pip install agent-eval-flow`, or use a checkout to try the bundled
offline example. The checkout includes unreleased changes; PyPI installs the
latest published release.

```bash
git clone https://github.com/guybass/agent-eval-flow.git
cd agent-eval-flow
python -m pip install -e ".[cli]"
python examples/archive_review.py --output demo-output/archive
```

Open **`demo-output/archive/report.html`**. No model credentials or model calls
are needed to run this example after installation.

The example imports a pinned, published SWE-agent repair trajectory, evaluates
its captured observations, saves the result, and regrades the same capture.
The bundled input produces:

| What you see | Result |
| --- | --- |
| Historical tasks / captured actions | 1 / 12 |
| Observations containing `SyntaxError` | 3 |
| Same capture reused for regrading | `true` |
| New agent invocations | 0 |

These are trace diagnostics, not a new test of whether the patch fixes the
original issue. [Walk through the report and the code →](https://github.com/guybass/agent-eval-flow/blob/main/TUTORIAL.md)

### Use the saved result in Python

After running the quickstart, run this from the checkout root:

```python
from agent_eval_flow import EvaluationResult

result = EvaluationResult.load("demo-output/archive/result")
for row in result.summary():
    print(row.summary_id, row.value.value)

result.report("demo-output/archive/reopened.html")
```

Loading and reporting use saved results. The [tutorial](https://github.com/guybass/agent-eval-flow/blob/main/TUTORIAL.md#regrade-the-same-capture)
shows how to change a suite and regrade without executing the agent again.

## How it fits together

![AEF workflow: define a study, capture native runs or import retained evidence, evaluate, and inspect or compare saved results](https://raw.githubusercontent.com/guybass/agent-eval-flow/main/examples/assets/evaluation-workflow.svg)

An evaluation starts with a **study**: tasks, candidates, checks, and execution
policy. Capture produces a **run set**, evaluation produces a **result**, and
reports and comparisons use that result. Configuration assessment can also run
independently of an agent execution.

| I want to… | Start here |
| --- | --- |
| Inspect a retained run and learn the API | [Offline tutorial](https://github.com/guybass/agent-eval-flow/blob/main/TUTORIAL.md) |
| Check instruction files against a requirement | [Configuration assessment](https://github.com/guybass/agent-eval-flow/blob/main/examples/assessment_review.py) |
| Diagnose tool selection, arguments, order, and repeated calls | [Toolscore integration](https://github.com/guybass/agent-eval-flow/blob/main/examples/TOOLSCORE.md) |
| Configure a local OpenKritt workflow | [Workflow guide](https://github.com/guybass/agent-eval-flow/blob/main/examples/workflows/README.md) |
| Capture and inspect a native agent run | [OpenKritt local integration](https://github.com/guybass/agent-eval-flow/blob/main/examples/integrations/README.openkritt-local.md) |
| Browse worked examples and their limits | [Report gallery](https://github.com/guybass/agent-eval-flow/blob/main/examples/reports/README.md) |

## Tool calling report

Add deterministic [Toolscore](https://github.com/yotambraun/Toolscore)
diagnostics to captured runs and generate a linked tools report:

```bash
python -m pip install -e ".[toolscore]"
python examples/toolscore_review.py --output demo-output/toolscore
```

The offline example writes linked `report.html` and `tools.html`, `tools.json`,
and verified evidence files. It uses synthetic retained streams and makes no
agent or model calls. Toolscore evaluates requested tools and arguments;
task outcomes remain separate checks. Missing capture stays unknown.

Use `result.report(path, tools=True)` after configuring the optional evaluator.
See [the integration guide](https://github.com/guybass/agent-eval-flow/blob/main/examples/TOOLSCORE.md) for trace coverage, expected
calls, scoring rules, and supported formats.

## Example reports

| OpenSRE | OpenKritt |
| --- | --- |
| [![OpenSRE report](https://raw.githubusercontent.com/guybass/agent-eval-flow/main/examples/reports/01-opensre.png)](https://guybass.github.io/agent-eval-flow/reports/01-opensre.html) | [![OpenKritt report](https://raw.githubusercontent.com/guybass/agent-eval-flow/main/examples/reports/02-openkritt.png)](https://guybass.github.io/agent-eval-flow/reports/02-openkritt.html) |
| Safe recovery stayed **2/2**; late tool calls fell **1 → 0** after a stopping-contract fix. | The demo grounding check passed **0/1 → 1/1** after requiring execution and clarifying the reporting contract. |

These are small native-agent experiments on controlled synthetic tasks. They
demonstrate specific changes, not general reliability or security accuracy.
[Read the reports, walkthroughs and limits](https://github.com/guybass/agent-eval-flow/blob/main/examples/reports/README.md).

## Contributing

Contributions are welcome, including documentation fixes, offline examples,
regression tests, adapters, and report improvements. You can get started without
model credentials. Small fixes can go straight to a pull request; for larger
features, [open an issue](https://github.com/guybass/agent-eval-flow/issues/new/choose)
to discuss the approach.

Start with [the contribution guide](https://github.com/guybass/agent-eval-flow/blob/main/CONTRIBUTING.md) for setup on Windows,
macOS, or Linux, a code map, local checks, and the steps to your first pull request.

## Status and boundaries

This is a developer preview. Native integrations require their own runtime setup
and credentials; offline test success does not establish live compatibility.
Runtime-specific importers and collectors map native logs into shared records;
arbitrary logs are not interpreted automatically. Missing evidence stays unknown,
and tool-call diagnostics remain separate from task outcomes.

Use held-out tasks to evaluate generalization and keep development cases separate.
The small examples here demonstrate workflows; they are not a leaderboard.

See [the test guide](https://github.com/guybass/agent-eval-flow/blob/main/tests/README.md)
for live profiles, and [third-party notices](https://github.com/guybass/agent-eval-flow/blob/main/THIRD_PARTY_NOTICES.md)
for fixture provenance and licenses.

[Changelog](https://github.com/guybass/agent-eval-flow/blob/main/CHANGELOG.md) · [Releases](https://github.com/guybass/agent-eval-flow/releases) ·
[Report an issue](https://github.com/guybass/agent-eval-flow/issues/new/choose)
