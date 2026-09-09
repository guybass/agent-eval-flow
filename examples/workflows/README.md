# Local OpenKritt workflow

`openkritt_flaskr_local_v1.json` is an explicitly named native-compatible variant
of `tests/e2e/fixtures/openkritt/workflow.json`. The first live import exposed a
native rule that the original acceptance fixture missed: output keys must be
unique across workflow depths. OpenKritt returned HTTP 422 before creating a
scan because `review_area` and `files_read` appeared in both the map and review
schemas.

The local variant uses `map_area` and `map_files_read` at depth 0, then
`review_area` and `review_files_read` at depth 1. Its four prompts, branch/merge
topology, and terminal finding schema remain unchanged. Native repeat count is
still configured by the caller. The adjacent provenance file records the exact
original and derived hashes and the reason for the change. Frozen acceptance
fixtures remain intact.

The default is now `openkritt_flaskr_local_v2.json`. The first actual Codex scan
then exposed a second boundary: upstream `schema.py` translates free-form
`object` fields into `additionalProperties: true`, which Codex's structured
output endpoint rejected. Version 2 changes `observations` and `evidence` to
arrays of explanatory/source-citation strings. Native arrays already have
`items: {type: "string"}`; the wrapper and result objects remain closed and
fully required. Prompts and topology remain unchanged. Version 1 and the first
failed scan's submitted configuration are retained for inspection.

The local Codex example rejects these known unsupported free-form object fields
before execution. This is a boundary rule for the pinned OpenKritt/Codex pairing,
not a restriction on Agent Eval Flow's general data model. Tests invoke the
actual native schema builder and verify that the resulting version-2 schemas
contain no open objects and have typed array items. The running engine's own
schema builder is also suitable for checking a prepared configuration before
any model call.

The local runner copies the selected workflow and provenance into its output
directory before creating the study. The native adapter includes the exact
submitted portable workflow in the evidence ZIP. Callers can choose another
workflow explicitly with `--workflow` or the profile's `workflow_file` field.

`tests/unit/test_openkritt_workflow_variant.py` checks that prompts and topology
remain intact. When Node and the pinned upstream checkout are available, it
also invokes upstream's actual `validateWorkflow`: the original fixture is
rejected for duplicate keys, and this variant passes. This is native validation,
not a reimplementation of OpenKritt's rules.

For the local capture score, every completed attempt must retain its native
harness stream. A synthesis or repeat can legitimately consume supplied earlier
outputs without calling another tool. The tool check requires actual source
inspection somewhere in the workflow and verifies that every observed native
command result maps exactly to its retained event, including input, output,
execution identity, and source line. Missing or altered receipts fail the check.
This tool rule was introduced in evaluator revision 2. Revision 3 also corrects
the cumulative-repeat lineage check: native OpenKritt wraps each prior answer
as `{"repeat_run": 1, "result": {...}}`. The first assessment expected a bare
answer array and falsely marked the real mapping/storage repeat inputs missing.
The actual branch and repeat inputs were present. Revision 3 parses the native
JSON blocks and verifies exact answer content, repeat identity, producer step,
and input lineage. A regression fixture retains public-field excerpts from all
eight actual workflow attempts and the original artifact hashes; removing or
changing parent/repeat data fails the check. The current suite is
`3-native-repeat-lineage` for that correction. Revision 4 additionally requires
every nested EvidenceRef's original URI, hash, media type, locator, description
and exact bytes to be represented in the portable ZIP. The first archive had
all named artifacts but omitted inventory/deployment provenance available as
nested references. The current suite is `4-complete-evidence-lineage`.
`python -m examples.openkritt_local_regrade --source <saved-run> --output <fresh-directory>`
can apply the current revision to existing capture without a backend or new model
call. The combined attempt index accepts `--assessment <regraded-directory>` to
show an offline reassessment separately from the native execution attempts.

Add `--complete-evidence` to reconstruct a complete archive from retained local
evidence. This preserves the original ZIP inside the new archive and creates a
derived RunSet with a different fingerprint. Native executions, events, output
and resource receipts remain identical. The saved summary records both capture
fingerprints and distinguishes offline repackaging from another agent execution.
