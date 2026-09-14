# Local OpenKritt workflow

[openkritt_flaskr_local_v2.json](openkritt_flaskr_local_v2.json) is the default
workflow for the local OpenKritt/Codex example. It preserves the four prompts,
branch/merge topology, and terminal finding schema of the source workflow.

The output keys are unique across workflow depths. `observations` and `evidence`
use arrays of strings, with typed items and closed result objects, to match the
pinned native OpenKritt/Codex schema requirements. The local example rejects
unsupported free-form object fields before execution; the library's general
record model remains unrestricted by this integration-specific rule.

The adjacent [v2 provenance](openkritt_flaskr_local_v2.provenance.json) records source/derived hashes and field changes.
[v1 provenance](openkritt_flaskr_local_v1.provenance.json) covers the earlier variant; the original acceptance fixture is unchanged.

## Use a workflow

Follow the [local runtime setup](../integrations/README.openkritt-local.md).
Override the default with `--workflow path/to/workflow.json` when running
`python -m examples.openkritt_local_review`, or set the profile's `workflow_file`.
The runner retains the selected workflow and provenance in its output; the
submitted portable workflow is included in the evidence bundle.

## Regrade saved evidence

From the repository root, use an existing saved run and fresh output directories:

```bash
python -m examples.openkritt_local_regrade --source demo-output/openkritt-local --output demo-output/openkritt-regraded
python -m examples.openkritt_local_regrade --source demo-output/openkritt-local --output demo-output/openkritt-complete --complete-evidence
```

Both commands evaluate retained evidence without a backend or new model calls.
`--complete-evidence` rebuilds the portable bundle from retained bytes, keeps the
original ZIP, and produces a derived RunSet with a new fingerprint. Native
executions, events, outputs and resource receipts remain unchanged.
The checks cover capture integrity and lineage, not security accuracy.

Tests cover [workflow compatibility](../../tests/unit/test_openkritt_workflow_variant.py),
[capture evaluation](../../tests/unit/test_openkritt_local_review.py), and [repeat lineage](../../tests/unit/test_openkritt_lineage.py).
