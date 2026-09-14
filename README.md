[![Agent Eval Flow](https://raw.githubusercontent.com/guybass/agent-eval-flow/main/examples/assets/agent-eval-flow-banner.png)](https://guybass.github.io/agent-eval-flow/)

# Agent Eval Flow

[![PyPI](https://img.shields.io/pypi/v/agent-eval-flow?color=245c50)](https://pypi.org/project/agent-eval-flow/)
[![Tests](https://github.com/guybass/agent-eval-flow/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/guybass/agent-eval-flow/actions/workflows/tests.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-49659c)](https://github.com/guybass/agent-eval-flow/blob/main/pyproject.toml)

**Evaluate agent runs, understand failures, and compare changes.**

Use your existing agent runtime or import retained runs into a shared evidence
format. Apply your checks, inspect an HTML report, change the system, and compare
another run. The target can include models, instructions, skills, tools, loops,
memory and environment.

[Example reports](https://guybass.github.io/agent-eval-flow/) ·
[Releases](https://github.com/guybass/agent-eval-flow/releases) ·
[Report an issue](https://github.com/guybass/agent-eval-flow/issues)

## Install

Requires Python 3.11 or later.

```bash
python -m pip install agent-eval-flow
```

## Try an offline example

```bash
git clone https://github.com/guybass/agent-eval-flow.git
cd agent-eval-flow
python -m pip install -e ".[cli]"
python examples/archive_review.py --output demo-output/archive
python examples/assessment_review.py --output demo-output/assessment
```

Open `report.html` inside either output directory. These examples make no model
calls.

- [Archived-run evaluation](https://github.com/guybass/agent-eval-flow/blob/main/examples/archive_review.py)
  imports a published SWE-agent trajectory, checks its evidence, saves the
  result, and regrades the same capture without running the agent again.
- [Configuration assessment](https://github.com/guybass/agent-eval-flow/blob/main/examples/assessment_review.py)
  compares two local instruction files against an explicit requirement.

## What you can evaluate

- Outcomes and execution evidence with application-defined metrics.
- Configuration and observed runtime behavior, with explicit coverage and
  expected versus observed values.
- Candidate changes using saved results, comparisons and selection policies.

Missing evidence stays unknown. Runtime-specific importers and collectors map
native logs into the shared records; arbitrary logs are not interpreted
automatically. Native adapters keep the agent's own execution loop.

## Example reports

| OpenSRE | OpenKritt |
| --- | --- |
| [![OpenSRE report](https://raw.githubusercontent.com/guybass/agent-eval-flow/main/examples/reports/01-opensre.png)](https://guybass.github.io/agent-eval-flow/reports/01-opensre.html) | [![OpenKritt report](https://raw.githubusercontent.com/guybass/agent-eval-flow/main/examples/reports/02-openkritt.png)](https://guybass.github.io/agent-eval-flow/reports/02-openkritt.html) |
| Safe recovery stayed **2/2**; late tool calls fell **1 → 0** after a stopping-contract fix. | The demo grounding check passed **0/1 → 1/1** after requiring execution and clarifying the reporting contract. |

These are small native-agent experiments on controlled synthetic tasks. They
demonstrate specific changes, not general reliability or security accuracy.
[Read the reports, walkthroughs and limits](https://github.com/guybass/agent-eval-flow/blob/main/examples/reports/README.md).

## Development

This is a developer preview. Native integrations require their own runtime setup
and credentials; offline test success does not establish live compatibility.

See [Contributing](https://github.com/guybass/agent-eval-flow/blob/main/CONTRIBUTING.md)
for checks, [the test guide](https://github.com/guybass/agent-eval-flow/blob/main/tests/README.md)
for live profiles, and [third-party notices](https://github.com/guybass/agent-eval-flow/blob/main/THIRD_PARTY_NOTICES.md)
for fixture provenance and licenses.
