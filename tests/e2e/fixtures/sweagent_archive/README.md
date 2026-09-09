# A downloaded, inspectable agent execution

This is SWE-agent's published `pydicom__pydicom-1458` test trajectory, downloaded
unchanged from the immutable repository revision in [PROVENANCE.json](PROVENANCE.json).
Its [MIT license](LICENSE) and upstream [format documentation](native-format.md)
are retained. It is historical source evidence, not an execution produced by this
project, and not proof of current SWE-agent compatibility or benchmark success.

The agent reproduces a source-code bug, locates and opens the relevant handler,
makes edits, receives syntax-error feedback, corrects its edits, reruns the
reproducer and submits a patch. The actual archive has **12 actions**, complete
observations, message history, the submitted diff and aggregate model usage.
Open [INSPECT.md](INSPECT.md) for an action-by-action view and
[the original JSON](pydicom__pydicom-1458.traj) for the complete evidence.

The [archive E2E](../../test_archived_trajectory.py) supplies a fixture-specific
implementation of the public importer protocol. The library must perform real
planning, import reconciliation, evaluation, saving/loading and reporting. No
backend is bound and no archived command is executed. The test checks exact
actions/observations and source pointers, 24 step measurements, the retained
patch, unknown timestamps and the historical aggregate cost. It then changes
the suite and evaluates the same saved capture again.

Native `submitted` means a patch was delivered; it does not establish a correct
fix. Model usage is a historical source observation, not a current price or a
new bill. No per-step latency, per-step token allocation, complete child
execution inventory or model configuration is invented. Full native history
stays in the raw artifact; public events project observable actions/results.

This fixture is already downloaded. Running the acceptance test later needs no
network or credentials, but still needs the future `agent_eval_flow` package.
