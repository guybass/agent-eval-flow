# Changelog

Changes to Agent Eval Flow are recorded here before the next release. Published
versions and their notes are available on [GitHub Releases](https://github.com/guybass/agent-eval-flow/releases).

## Unreleased

### Getting started

- Reorganize the README around one offline example, its report, and a visual
  explanation of the capture, evaluation, and comparison workflow.
- Add a [tutorial](TUTORIAL.md) for inspecting saved evidence, regrading a
  capture, and choosing an integration.
- Add [citation metadata](CITATION.cff) and include the public guides in source
  distributions.

### Toolscore integration

- Upgrade the optional `tool-scorer` dependency from 1.9.0 to 1.10.0.
- Retain Toolscore's versioned result payload and recorded tool outcomes for
  failure and retry diagnostics. Incomplete outcome capture remains explicit.
- Preserve AEF's existing request-count `required_call_recall` and default
  composite weights. Tool diagnostics remain separate from task outcomes.

See the [Toolscore guide](examples/TOOLSCORE.md) for metric definitions and
upgrade notes. Saved evaluations retain their original evaluator provenance;
an explicit regrade creates a new evaluation. Do not compare grades from
different evaluator versions as though their scoring rules were identical.
