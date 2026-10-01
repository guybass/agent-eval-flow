# Security triage: preserving unresolved evidence

A selected development study of the Codex static-review port of
[Anthropic's Defending Code Reference Harness](https://github.com/anthropics/defending-code-reference-harness/tree/d3bea6b5793b5f3d59a75ebe69a58efa88383145).
The observed failure was a reporting and verification policy that discarded
unresolved evidence. This report records the failure, the targeted intervention,
and the completed regression results. It does not change Agent Eval Flow or
install a security harness.

**Result:** 26 offline checks passed, and all 9 final-policy development cases
passed: four previously discarded diagnoses retained, the same four allegations
refuted on repaired source, and one previously successful PostCSS control retained.
All five positive reference diagnoses have conditional exposure; none establishes
a deployed exploit.

The [machine-readable summary](results-summary.json) includes the 23 durable
attempts, the nine final outcomes, all 31 explicit finding assessments, hashes of
retained artifacts, and the evidence limits described below.

## What failed

The original comparison used 24 issue families, each with vulnerable and repaired
source snapshots, and plain Codex versus the harness port: 96 native root tasks.
In the harness's 24 vulnerable-snapshot scans, there were 60 candidate findings.
Their saved triage records contained **47 CANNOT_VERIFY majorities**, seven
false-positive majorities and six true-positive majorities.

Under the original precision policy, a CANNOT_VERIFY majority became
`false_positive`. The evaluation adapter then retained only `true_positive`
findings. Four reference-matching scan diagnoses disappeared through this path:
Gitea approval retargeting, DRF internal GET permissions, Next.js singleton-locale
matching, and TypeORM generated-source interpolation. Each had three recorded
CANNOT_VERIFY votes.

The saved outputs exposed relevant vulnerable code, and the four triage rationales
identified local behavior while citing missing callers, configuration or runtime
components. This establishes an evidence-handling failure; the appearance of a
source line alone does not establish that the model understood it. It also does
not establish that every unresolved candidate was a real bug.

A separate grading issue mattered: location proximity credited a Traefik claim
whose mechanism differed from the reference defect. Explicit mechanism review
was therefore kept separate from location matching and structural output validity.
This triage intervention does not recover that discovery miss.

## Changes tied to the evidence

The revised verifier records two judgments: whether the local mechanism is
supported, refuted or unresolved, and whether attacker exposure is established,
conditional or unknown. Each vote preserves a concrete witness, source references,
assumptions and missing context. Refutation requires counterevidence; missing
callers alone cannot refute a locally supported mechanism.

Host-side aggregation independently derives dispositions and checks that every
scanner candidate is accounted for. Unresolved findings and verification errors
remain visible instead of becoming false positives or confirmed bugs. Duplicates
retain an explicit link to their canonical finding. Legacy replay preserves all
60 records as 47 unresolved, seven historical refutations and six historical
positives; it does not promote the 47 unresolved claims.

The first revision still left DRF unresolved. All three verifiers required the
missing `override_method` body to exclude a hidden permission gate. But the
available source already showed other callers explicitly checking permissions
inside that same wrapper. The final revision asks the verifier to weigh actual
same-revision definitions, documentation and call sites before deciding that an
unseen helper body is essential. It must label the contract inference, preserve
the missing body and assumptions, and keep attacker exposure separate. A name
alone is insufficient.

This rule did not promote every unknown: the final Gitea review retained an
attachment-reassignment allegation as unresolved with three unresolved votes.
An ownerless issue-access allegation remained unresolved with two unresolved
votes and one supporting vote because the available comment did not establish
whether wiki-only team access satisfied the missing permission predicate.

## Final-policy outcomes

| Reference mechanism | Vulnerable source | Same allegation on repaired source |
| --- | --- | --- |
| Gitea: approval authority survives branch retargeting | Supported locally; conditional exposure | Refuted by review recalculation |
| DRF: internal GET bypasses the normal permission check | Supported locally; conditional exposure | Refuted by the explicit permission check |
| Next.js: singleton locale omitted from matcher generation | Supported locally; conditional exposure | Refuted by the i18n-presence condition |
| TypeORM: interpolation survives SQL-to-source encoding | Supported locally; conditional exposure | Refuted by escaping on both paths |
| PostCSS: map-file path traversal, prior success control | Supported locally; conditional exposure | Not included in the paired controls |

The four recovered diagnoses each have three recorded supporting votes under
the final policy. Their repaired-source controls each have three refuting votes
with source counterevidence. Additional Gitea and PostCSS allegations are retained
but do not count as reference-case hits or independently validated new exploits.

The archived repaired-source scans for DRF, Next.js and TypeORM were empty, so
simply rerunning triage on those scans could not test false-positive rejection.
The meaningful negative controls instead present the unchanged original
allegation against the paired repaired source. Their inputs and wrapper hashes
were frozen before inference; answer keys and the other snapshot were not staged.

## Execution, failures and audit

| Batch | Durable outcome |
| --- | --- |
| First revised-policy pilot | 9 completed, including the DRF abstention and three empty-scan controls |
| First revised-policy repaired-claim controls | 4 completed |
| Final-policy DRF pair | 2 completed |
| Final-policy remaining regression batch | 2 completed, one setup failure; four further assignments were not dispatched |
| Explicit recovery batch | 5 completed, covering that failed setup and the four undispatched assignments |

Across revisions, **22 native root reviews completed with structurally valid
outputs**, plus **one preserved pre-inference setup failure**. The latter was a
Windows artifact-publication error. A concurrent check-then-replace race in the
shared cache was reproduced in an offline test; serializing publication resolved
the reproduced race while preserving genuine permission and corruption errors.
The exact OS lock holder in the original failure was not captured. The failed
attempt was not overwritten or silently relabeled as completed.

One verifier in the first Gitea revision matched scanner JSON during a broad
search, reported the scope deviation, and was excluded and replaced. Both its
record and the replacement remain in the raw triage archive. This does not
establish answer-key exposure.

The final audit checked 286 artifact references, frozen inputs, all 31 finding
assessments and 93 accepted vote records, with one excluded verifier attempt.
There were no audit errors. The 26 offline checks cover the observed uncertainty
drops, wrong-mechanism matching, missing or malformed votes, candidate accounting,
paired-source staging, storage behavior and the final-policy entrypoint.

## Experimental conditions and limits

- The final nine tasks are one repeat each on selected **development data**:
  four inspected failure families and one prior success. Repaired variants stay
  with their vulnerable family. No generalization or fresh held-out result is
  claimed, and 9/9 does not mean all 24 families were repaired.
- The model remained `gpt-6-astra`, reasoning `xhigh`, native Codex version
  `0.153.4`, with the original 40-minute root budget. Source snapshots and fixed
  scanner allegations remained unchanged. Every new root began with fresh state.
- This tests triage conditional on saved candidates. Discovery was not rerun.
  Traefik and Glances discovery misses remain outside the intervention.
- Historical roots included preceding scan context; the new roots began with
  archived scan files. This is not a randomized estimate of the isolated policy
  effect. Batches overlapped, peaking at eight root intervals; no latency,
  throughput or cost advantage is claimed.
- Structural validation is separate from semantic correctness. Reference
  diagnoses had one reviewer, and post-run assessments were made by Codex in the
  same task, not by an independent human panel. Complete child/retry inventory
  and context independence remain unverified; vote counts are recorded votes,
  not audited independent executions.
- The native Claude workflow and autonomous Docker pipeline were not evaluated.
  No target exploit was executed during these static reviews. Model training
  contamination cannot be ruled out.
- Original scanner severity and confidence values remain scanner claims, not
  a new assessment of deployment severity. Local support does not imply
  established attacker reachability.

## Evidence availability and provenance

The upstream revision is `d3bea6b5793b5f3d59a75ebe69a58efa88383145`.
The original triage skill SHA-256 is
`a4b5bb808f066ce3dfde31974b62caaee28b16407c8d5a804a8d2bc241f0a146`;
the final experimental policy SHA-256 is
`b5f75e9a18f2705ee492dcfcc4fb42af7270a3df74d42fc362bfe3058eec147a`.
The summary records individual attempts and artifact identities rather than
collapsing unsuccessful intermediate versions into the final result.

The complete frozen sources, raw native traces, original attempts, policy variants
and audit receipts remain in the local experiment archive. This contribution
publishes a scrubbed research summary, not those raw captures or the experimental
harness implementation. Hashes identify retained objects but do not substitute
for access to them. The original 26-check suite depends on those local frozen
assets; this report is not a standalone public reproduction package. Publication
checks validate the summary against the local receipts and validate its links;
they are not new native runs.

[Back to example reports](../README.md)
