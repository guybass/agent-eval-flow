# Inside the coding-agent harness

[Read the public report](https://guybass.github.io/agent-eval-flow/reports/native-harness/).

This curated public edition covers the September 30 Pi/OpenCode coding-agent
experiment and clearly separated subsequent development, fresh validation and
synthetic controls. It reports every original held-out outcome, including
interrupted workflows, and retains the negative results and limitations.

The 48 original workflows concern eight selected issues, three repeats and two
native agents. They are not 48 independent bugs. The separate first follow-up
contains 16 development and eight fresh-validation attempts. Later development
does not revise or replace the frozen original scores.

The v5.4 identifier correction belongs to our experimental harness in the
separate local tuning project (commit `3458382`). It changes our orchestration
and validation code, including its generated instructions, without changing
the native Pi or OpenCode packages. Publishing this report does not publish
that correction as an Agent Eval Flow library release.

## Files and reproduction

- `index.html`: self-contained report with filterable original outcome rows.
- `study-data.json`: explicitly curated outcomes, source revisions, split
  assignments, follow-up audit fields and source-document fingerprints.
- `report.html.j2`: presentation template.

From the repository root, with the package dependencies installed:

```bash
python examples/native_harness_report.py
python scripts/build_report_site.py
```

Rendering validates the baseline counts and makes no model calls. Only the HTML
and curated JSON are allowlisted for the Pages deployment. Raw captures, local
paths, credentials, hidden test code, unused holdout answers and experimental
agent-tuning workspaces are not part of the public export. Archived document
hashes identify retained evidence but cannot substitute for access to those
unpublished captures. This is not a complete public replay bundle.
