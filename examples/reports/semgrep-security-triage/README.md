# Semgrep security triage and unresolved findings

In a development study of the static scan and triage skills from
[Semgrep Defending Code Harness](https://github.com/semgrep/defending-code-harness/tree/9fe924b2a9acc320e6eec80becce338175e96ba6),
10 of 14 completed vulnerable snapshots produced scan claims matching the known
vulnerability mechanism. None of those claims remained in the evaluation
adapter's final confirmed-finding list. All 11 matching candidates across those
10 cases received three recorded `CANNOT_VERIFY` votes, which the harness's
precision policy converted to `false_positive`.

Across all 32 completed reviews, 61 of 68 candidates followed that uncertainty
conversion. The raw scan and triage artifacts retained the evidence. The loss
occurred in the classification and subsequent confirmed-finding projection.

**Status:** baseline stopped early on October 1, 2026, after a user-requested
diagnostic sufficiency review. Of 48 planned reviews, 32 completed, three were
interrupted, and 13 never started. No Semgrep intervention was tested. These
results describe an outcome-informed development sample, not full-suite
performance or an estimate of generalization.

The [machine-readable results](results-summary.json) contain all 48 task statuses,
68 analyst finding assessments from the completed reviews, recorded vote totals,
source revisions, artifact hashes, and interpretation limits. Full native traces
and local experimental code remain unpublished; the public summary is not a
complete reproduction package.

## What was evaluated

This is a follow-up to the [earlier Anthropic harness study](../security-triage-repair/README.md).
Semgrep's fork was selected because its README explicitly accepts contributions
and it retains the relevant scan and triage workflow. The study pinned revision
`9fe924b2a9acc320e6eec80becce338175e96ba6`; its findings concern that revision.

The evaluated agent performed static security review using the unmodified
`vuln-scan` and `triage` skills through our native Codex adapter. This scope is
distinct from Semgrep's SAST engine and the repository's autonomous Docker/ASAN
pipeline. Neither of those systems was evaluated here.

| Setting | Frozen configuration |
| --- | --- |
| Planned population | The same 24 issue families used in the earlier study, each with vulnerable and repaired snapshots |
| Source scope | Curated source slices associated with known issues, rather than entire repositories |
| Requested model and reasoning | `gpt-6-astra`, `xhigh` |
| Native runtime | Codex 0.153.4 |
| Repeat count | One fresh root per snapshot |
| Root time limit | 2,400 seconds |
| Concurrent roots | Three |
| Triage setting | Automatic mode, precision default, three recorded votes per candidate |
| Split | Development; these cases had already been inspected |

Task inputs, source manifests, harness bytes, adapter and grading sources, model
settings, and budgets were frozen before inference. The study fingerprint is
`e3c0c8e663d7b4b3d1214c17a4754396c6532778db5c6080c24530e1656d85ce`.
Answer keys, maintainer fixes, and grading outputs were kept outside the evaluated
agent's staged inputs. Roots were fresh between independent tasks; complete
child-agent histories and verifier independence are not established by the exports.

The original 48-task population was reused exhaustively as the planned set; it
was not randomly sampled. A two-review compatibility gate preceded the remaining
dispatches. The later stop left an uneven completed subset of 14 vulnerable and
18 repaired snapshots. The stop was not preregistered. Historical plain-Codex and
Anthropic results are available in the earlier research, but are not concurrent
randomized controls for this run.

## Results from completed reviews

| Measure | Vulnerable snapshots | Repaired snapshots | Total |
| --- | ---: | ---: | ---: |
| Completed reviews | 14 | 18 | 32 |
| Scan candidates | 31 | 37 | 68 |
| Recorded cannot-verify majorities | 30 | 31 | 61 |
| Those majorities labeled false positive | 30 | 31 | 61 |
| Candidates retained in the confirmed list | 1 | 4 | 5 |
| Cases with a reference-matching scan claim | 10 | 1 | 11 |
| Cases with a reference-matching final claim | 0 | 1 | 1 |

The one final finding on vulnerable source concerns a different issue from its
reference vulnerability. The repaired-source reference match is the PostCSS
control problem discussed below; it is not an established false positive.

Across the 68 candidates, the saved votes comprise 61 cannot-verify majorities,
five true-positive majorities, and two false-positive majorities. These are
recorded verifier judgments, not independent adjudications of all 68 claims.
Every candidate maps to a saved triage record, and every recorded aggregate
verdict matches the precision rule. The adapter's final list matches exactly the
records labeled `true_positive`.

## How unresolved findings became false positives

The causal chain is visible in the saved artifacts and policy:

1. The scan emits a claim aligned with the known local vulnerability mechanism.
2. The verifier records acknowledge suspicious local behavior but cannot establish
   a required caller, helper, gadget, deployment condition, or end-to-end path.
3. The harness maps a cannot-verify majority to `false_positive` in precision mode.
4. Our evaluation adapter includes only `true_positive` triage records in its
   final confirmed-finding list.

The pinned skill's [automatic defaults](https://github.com/semgrep/defending-code-harness/blob/9fe924b2a9acc320e6eec80becce338175e96ba6/.claude/skills/triage/SKILL.md#L183-L190)
select precision. Its [verdict rule](https://github.com/semgrep/defending-code-harness/blob/9fe924b2a9acc320e6eec80becce338175e96ba6/.claude/skills/triage/SKILL.md#L619-L626)
routes ties and cannot-verify majorities to false positive under that setting.
It also appends a split-vote explanation, including when the recorded votes are
unanimously cannot verify.

The adapter's projection is equivalent to:

```python
final_findings = [
    finding for finding in triage_findings
    if finding.get("verdict") == "true_positive"
]
```

This explains exclusion from the adapter's final confirmed list. It does not
mean the native harness erased its scan or triage files. It also does not mean
all unresolved findings should be promoted to confirmed vulnerabilities.

### Reference claims excluded after scanning

Each candidate below has **0 true-positive, 0 false-positive, and 3 cannot-verify
votes** in the saved records. All were labeled false positive and excluded.

| Project | Scan candidate | Mechanism alignment |
| --- | --- | --- |
| Django REST framework | F-001 | Internal GET during error rendering without the GET permission check |
| MLflow | F-001, F-002 | Redirect and DNS-resolution gaps in webhook SSRF protection |
| TypeORM | F-001 | Incomplete escaping when SQL becomes generated JavaScript or TypeScript |
| gRPC Go | F-001 | Header-case handling that bypasses the intended Host to authority normalization |
| MapLibre GL JS | F-002 | Removing attributes while iterating a live collection skips the next attribute |
| Budibase | F-001 | Linking an incoming unverified email claim to an existing account |
| Composer | F-001 | Perforce URL reaching a command-capable transport |
| Gitea | F-002 | Retargeted pull requests retaining old official approval eligibility |
| NLTK | F-002 | Namespace-based pickle trust permitting a command-execution gadget |
| Keycloak | F-001 | Credential-reset action succeeding without the action-token user check |

Alignment is a single analyst's comparison of the emitted claim with the frozen
reference mechanism. It is not an executed exploit. In particular, the NLTK
candidate proposes a different concrete gadget whose availability is unverified;
the staged source contains neither NumPy's implementation nor the advisory's
alternative `repp.py` gadget. The Keycloak candidate identifies the missing local
authorization predicate but does not reconstruct the complete stale-selector
route from the reference repair. Those limits remain attached to the claims.

## Source exposure and four separate scan omissions

For all 14 completed vulnerable snapshots, distinctive text from the reference
source region appears in a saved root command result before the first recognized
scan JSON write. The audit checked exact source bytes, matching source paths,
artifact hashes, and event order.

This establishes exposure in the saved output. It does not prove attention,
understanding, complete model context, or sufficient surrounding source. Missing
callers and helper implementations were real constraints in several cases.

Four cases emitted no claim matching the reference mechanism:

| Project | Missing reference diagnosis |
| --- | --- |
| aiohttp | Out-of-bounds native error-pointer conversion while formatting a parser error |
| Open WebUI | Shared-folder writer deleting the owner's subtree and chats |
| Angular | URL trimming and origin validation discrepancy |
| SurrealDB | Custom API namespace/database selection without checking the authenticated level's tenant scope |

Those reviews emitted other findings. Preserving unresolved candidates would not
recover the absent reference diagnoses. The root exports do not establish why
the scanner omitted them, so this report does not assign a deeper cognitive cause.

## A problem with the repaired PostCSS control

The snapshot labeled repaired still permits the reference source-map path when
`opts.from` is omitted: the added confinement branch is conditional on `cssFile`,
so the branch is skipped when that value is absent. The source-map annotation can
then reach the filesystem read without directory confinement.

The [original advisory](https://github.com/postcss/postcss/security/advisories/GHSA-r28c-9q8g-f849)
explicitly includes this no-from path. The snapshot was based on
[the recorded fix commit](https://github.com/postcss/postcss/commit/95663d3eb7ba26f4854dd19d3b4f4425760cf56c1).
The scan and final triage retained this claim. Calling it a false positive merely
because its snapshot was labeled repaired would conceal a control-curation issue.

This is a static observation about the frozen snapshot. Exposure depends on a
caller processing untrusted CSS, omitting `from`, having access to a suitable
source map, and exposing map-bearing output. No disclosure was executed, and no
claim is made about current deployed PostCSS. Other proposed residual mechanisms
remain separately unadjudicated. The original labels and grading rules were
preserved, with this limitation reported alongside them.

## Outcome by issue family

Cells show whether a claim matches the reference mechanism during scan and in
the final confirmed list. **No** means the reference claim is absent; a different
finding may still be valid. Interrupted and unstarted tasks are excluded from
outcome denominators. Repaired snapshots were fresh scans, not supplied-allegation
refutation tests.

| Project | Vulnerable scan to final | Repaired scan to final |
| --- | --- | --- |
| aiohttp | No to No | No to No |
| Traefik | Not run | No to No |
| Gitea | Yes to No | No to No |
| Keycloak | Yes to No | No to No |
| PostCSS | Interrupted | Yes to Yes; control caveat above |
| Composer | Yes to No | No to No |
| Open WebUI | No to No | Not run |
| Django REST framework | Yes to No | No to No |
| Glances | Not run | No to No |
| Next.js | Interrupted | Not run |
| TypeORM | Yes to No | No to No |
| Astro | Not run | No to No |
| rclone | Not run | Not run |
| Siyuan | Not run | No to No |
| gRPC Go | Yes to No | No to No |
| Quasar | Not run | Not run |
| Filament | Not run | No to No |
| Grav | Interrupted | No to No |
| MLflow | Yes to No | Not run |
| NLTK | Yes to No | No to No |
| SurrealDB | No to No | No to No |
| Angular | No to No | Not run |
| Budibase | Yes to No | No to No |
| MapLibre GL JS | Yes to No | No to No |

## Grading and evidence limits

The reported mechanism counts come from an unblinded, single-analyst review of
every candidate in the 32 completed tasks. The reference metadata is itself
marked `draft_single_reviewer`. Claim alignment, local code evidence, deployed
exploitability, and native workflow completion are different measurements.

The original frozen location/category grading rules were retained. A host-side
diagnostic applies those rules separately: nearby findings can match a reference
location while describing a different issue. For example, the Budibase vulnerable
case gets a location hit through F-002 while its aligned mechanism is F-001;
the repaired Keycloak F-002 is a different authorization claim. The public JSON
preserves both mappings. The full-suite canonical scoring pass was not run after
the early stop; this report does not present partial diagnostics as a completed
48-task benchmark.

All 32 completed roots produced valid structured outputs and preserved source
and harness bytes. The three requested interruptions were recorded by the runner
as `infrastructure_error`; their cause was the user-directed stop. Partial scans,
traces, and receipts were retained, and the 13 queued tasks were not dispatched.
No failed native root triggered the decision to stop. Some completed roots
self-reported internal verifier retries, including report-contaminated reviews
and model-capacity failures; those statements are not a complete independent
child execution inventory.

The final local integrity audit verified **551 artifact references** across 35
durable attempts, including the interrupted attempts. It found no hash errors.
The 35 saved root traces contain no invalid JSON lines. Their 32 nonzero command
exits are process diagnostics; an unsuccessful search, for example, is not itself
a failed security review. Complete child traces and independent vote provenance
remain unverified.

This publication includes selected derived outcomes, analyst reasoning, vote
counts, and hashes. It excludes full raw captures, local paths, credentials, and
experimental runtime code. Hashes bind the summary to retained local artifacts;
they do not make unavailable artifacts independently auditable. No new inference
was run to publish this report.

All cases are known development data. Public-training overlap is unknown, the
source selection is issue-informed, and there is one repetition per snapshot.
The early stop was informed by observed results. These conditions do not support
a leaderboard, a general false-positive rate, a whole-repository discovery rate,
or a claim of superiority over another harness.

## Repair supported by these observations

The evidence supports preserving `cannot_verify` as an unresolved outcome,
reserving false positive for supported refutation, and exposing unresolved
findings separately from confirmed findings. Local-mechanism evidence should
remain distinct from unverified reachability, with missing dependencies named.

That proposal directly targets the observed classification and projection path.
It has not been implemented or evaluated in this Semgrep study. The four scan
omissions require separate investigation. Any later tuning uses these cases as
development data and requires a newly frozen holdout before a generalization
claim. The earlier Anthropic repair results belong to their own experiment and
must not be presented as Semgrep repair results.
