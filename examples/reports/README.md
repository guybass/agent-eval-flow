# Example reports

[Open the gallery](https://guybass.github.io/agent-eval-flow/) to read these
OpenSRE, OpenKritt, GPT Researcher and coding-agent research in your browser.

| Example | Comparison | Task and fix walkthrough |
| --- | --- | --- |
| OpenSRE | [Report](https://guybass.github.io/agent-eval-flow/reports/01-opensre.html) · [PNG](01-opensre.png) | [Walkthrough](https://guybass.github.io/agent-eval-flow/reports/01-opensre-walkthrough.html) · [PNG](01-opensre-walkthrough.png) |
| OpenKritt | [Report](https://guybass.github.io/agent-eval-flow/reports/02-openkritt.html) · [PNG](02-openkritt.png) | [Walkthrough](https://guybass.github.io/agent-eval-flow/reports/02-openkritt-walkthrough.html) · [PNG](02-openkritt-walkthrough.png) |
| GPT Researcher | [Recorded pilot](03-gpt-researcher.html) | [Failure trace, fix and offline reproduction](../data/gpt-researcher/README.md) |
| Pi / OpenCode | [Inside the coding-agent harness](https://guybass.github.io/agent-eval-flow/reports/native-harness/) | [Method, outcomes and separate follow-up](native-harness/README.md) · [Curated data](native-harness/study-data.json) |
| DeerFlow | [Recorded research runs](../data/deerflow/README.md) | [Regenerate outcome and tool reports](../deerflow_review.py) |
| MCP memory | [Server and agent case studies](../data/mcp-memory/README.md) | [Server report](../mcp_memory_review.py) · [Agent report](../mcp_memory_agent_review.py) |
| Security triage | [Research results](security-triage-repair/README.md) | [Uncertainty loss, targeted repair and evidence limits](security-triage-repair/README.md#what-failed) · [Recorded outcomes](security-triage-repair/results-summary.json) |

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

GPT Researcher completed three selected SimpleQA cases, with two correct target
answers. The failed trace found the correct lead during query planning, then lost
it before writing after follow-up retrieval errors. An upstream source-preservation
patch passes 37 targeted offline tests. This is not a measured improvement in
live answer accuracy. [Regenerate its report offline](../gpt_researcher_review.py).

Selection, splits and repeat counts are documented in each study. OpenSRE,
OpenKritt and GPT Researcher use one development trial per case and candidate.
The Pi/OpenCode report separates an original frozen eight-issue, three-repeat
experiment from subsequent development, fresh validation and synthetic controls.
The reports summarize retained native runs; they do not establish a general
reliability rate or an overall security false-positive rate. Raw local captures,
private experimental harnesses and social-media drafts are not distributed.
The DeerFlow and MCP memory examples include curated captures and study scripts;
their original protocols, decisions and evidence limitations remain documented.

To try the library with reproducible public inputs, run
[archive review](../archive_review.py) or
[configuration assessment](../assessment_review.py). See the
[quickstart](../../README.md#try-an-offline-example).
