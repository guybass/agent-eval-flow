# Example reports

[Open the gallery](https://guybass.github.io/agent-eval-flow/) to read these
OpenSRE and OpenKritt examples in your browser.

| Example | Comparison | Task and fix walkthrough |
| --- | --- | --- |
| OpenSRE | [Report](https://guybass.github.io/agent-eval-flow/reports/01-opensre.html) · [PNG](01-opensre.png) | [Walkthrough](https://guybass.github.io/agent-eval-flow/reports/01-opensre-walkthrough.html) · [PNG](01-opensre-walkthrough.png) |
| OpenKritt | [Report](https://guybass.github.io/agent-eval-flow/reports/02-openkritt.html) · [PNG](02-openkritt.png) | [Walkthrough](https://guybass.github.io/agent-eval-flow/reports/02-openkritt-walkthrough.html) · [PNG](02-openkritt-walkthrough.png) |

## OpenSRE

Both synthetic checkout incidents recovered safely. One baseline run attempted
a wait after its report was accepted and received HTTP 400. The demo binding
revision used OpenSRE's existing termination signal, keeping recovery at **2/2**
while late calls fell **1 → 0** under the original task wording.

The task's conflicting cues and a separate native instruction-delivery gap
limit attribution. Two unsuccessful guidance revisions are described in the
walkthrough. This result does not show improved diagnosis.

## OpenKritt

A hinted synthetic billing flaw was independently reproducible before and after
the change. Requiring the agent to execute a proof and clarifying its reporting
contract changed the demo grounding check from **0/1 → 1/1**.

This combines workflow and contract changes. Severity still disagreed with the
declared policy, and the original-case runtime increased **296 → 532 seconds**.
The patched twin addresses one seeded flaw; other findings remain unadjudicated.

## Scope and reproduction

These are selected development cases, with one trial per case and candidate.
The reports summarize retained native runs; they do not establish a general
reliability rate or an overall security false-positive rate. Raw local captures,
experimental harnesses and social-media drafts are not distributed.

To try the library with reproducible public inputs, run
[archive review](../archive_review.py) or
[configuration assessment](../assessment_review.py). See the
[quickstart](../../README.md#try-an-offline-example).
