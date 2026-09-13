# Native agent evaluation: example reports

These curated human reports show native OpenSRE and OpenKritt runs on synthetic,
controlled tasks. Agent Eval Flow retained the runs and applied explicit checks;
review of the results informed the changes and comparisons. The reports
illustrate evaluating complete agents under plausible usage, including imperfect
instructions and integration behavior.

Click a report preview to read it at full size.

| OpenSRE: recovery and stopping | OpenKritt: evidence and impact |
| --- | --- |
| [![OpenSRE report: two safe recoveries retained, late tool calls reduced from one to zero](01-opensre.png)](01-opensre.png) | [![OpenKritt report: custom grounding passes after a workflow change, severity still disagrees](02-openkritt.png)](02-openkritt.png) |

| Example | Before/after report | Task and behavior walkthrough |
| --- | --- | --- |
| OpenSRE | [PNG](01-opensre.png) · [HTML](01-opensre.html) | [PNG](01-opensre-walkthrough.png) · [HTML](01-opensre-walkthrough.html) |
| OpenKritt | [PNG](02-openkritt.png) · [HTML](02-openkritt.html) | [PNG](02-openkritt-walkthrough.png) · [HTML](02-openkritt-walkthrough.html) |

Open the PNGs on GitHub, or download and open the self-contained HTML files in a
browser. Displayed counts were checked against the retained typed results when
these copies were prepared. Raw captures, experimental harnesses and post drafts
are excluded from this publication. This directory therefore supports reading
the findings, rather than independently rerunning these particular experiments.

For runnable public examples, start with [archive review](../../../examples/archive_review.py)
or [configuration assessment](../../../examples/assessment_review.py), following
the [installation instructions](../../../README.md#try-it). Those offline examples
make no model calls and demonstrate different tasks.

## OpenSRE

Two checkout incidents share a recent deployment but require different safe
actions: roll back an API regression, or increase worker capacity for a queue
bottleneck. A deterministic service simulator checks recovery and order safety.
Both baseline runs recovered safely and submitted accepted reports. One then
attempted a wait against the closed incident and received HTTP 400.

Our task mentioned waits after reporting, while the simulator closed the incident.
Our demo tool binding also omitted OpenSRE's existing native termination signal.
The revision uses that signal after report acceptance and guards already-queued
late calls. Safe recovery stayed **2/2**, accepted reports stayed **2/2**, and late
tool attempts fell **1 → 0**. The baseline task wording was unchanged.

Two earlier guidance revisions were rejected. A later delivery audit found the
native assembler clipped the initial literal task to 512 characters in this
tested headless path; the `a02` queue run never received its appended guidance.
That result cannot establish that the model ignored the proposed instruction.
The delivery gap and conflicting historical wording remain outside this measured
fix. The report is retained without an additional final chat answer.

This is two development cases, one trial per candidate, with adaptive changes.
It establishes the observed lifecycle result, not improved diagnosis or general
reliability. The original native revision was
`1a81e1a1b378ecc9aeb211fcaeecb0f5c93548e4`; model `gpt-5.6-luna`, 600 seconds per
case. Comparison: baseline `b03` against demo-binding revision `a03`, with both
graded by the same lifecycle suite.

| Case | Baseline run | Revised run |
| --- | --- | --- |
| Queue capacity | `68e99af4-405d-46ec-a2ec-ae7fede5351a` | `198c5844-4b92-48a9-b89d-34c12044eff0` |
| API regression | `84037fa3-429b-451a-b669-f0a83f4832a5` | `8fce4d73-853a-45b4-87c7-6de54a59c509` |

## OpenKritt

The fictional LedgerDock application has a seeded tenant-boundary flaw: a
tenant-local billing user can refund another tenant's invoice. Its repaired
twin denies that request. Both workflows received the same source and task
hints; this is boundary verification rather than blind vulnerability discovery.

Source-only review was allowed initially. The baseline supplied a request that
independent replay reproduced, but its first stage called unexecuted effects
"demonstrated". The final stage labeled them inferred. Its fields also differed
from the demo grader's partly implicit types and balance-sign convention; that
mismatch alone cannot establish factual error in every field.

The revised workflow requires local execution, defines the observation fields,
and carries measured evidence to the final report. Its native request returned
HTTP 200 and reduced the other tenant's balance from 25,000 to 24,000 cents. The
final proof matched that 1,000-cent request. The baseline requested 25,000 cents;
each proof was checked against its own request, not compared by raw delta size.

Independent proof passed **1/1 → 1/1**; the custom grounding contract passed
**0/1 → 1/1**. This measures a combined workflow and contract clarification,
not an isolated general-accuracy improvement. Severity agreement stayed
**0/1 → 0/1**: the revised report said critical, while the declared demo policy
expected high. Original-case elapsed time rose **296 → 532 seconds**. Some
generated control receipts remained imperfect even though the seeded proof
matched execution.

On the repaired twin, seeded-boundary false alarms stayed **0 → 0**, and one
other claim per workflow remained unadjudicated. These are not overall
false-positive rates. The baseline uses corrected grading of its original
capture; a repaired output parser was not counted as agent improvement.

One trial per workflow on each side of one source pair; two workflow stages;
model `gpt-5.6-luna`, low reasoning effort, 900 seconds per scan. Native OpenKritt
revision: `1ba10d410b4a0a309bacf5bc2d14ec1c27ed8a09`.

| Source | Baseline run | Revised run |
| --- | --- | --- |
| Original (`b02` → `a01`) | `ed7cad59-a9b8-40db-b89b-f1dac125371a` | `a7e135be-1cda-46de-ac14-ba52533bd199` |
| Repaired (`b03` → `a02`) | `95c66499-d891-4dd2-92a2-f471cb99b05d` | `ce94cc7f-90ad-4774-bb08-73d199c5b341` |

Run identifiers are provenance metadata; the underlying capture bundles are not
included here. All four HTML files retain the measured report copy with only
public navigation substituted for unavailable local evidence links.
