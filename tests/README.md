# Tests

The suite checks the public Python API, execution and evaluation boundaries,
saved evidence, adapters, and reports. Run commands from the repository root.

## Install and run offline

Use Python 3.11 or later:

```bash
python -m pip install -e ".[test,cli]"
python -m pytest
```

No live profiles are selected by default. Offline tests use deterministic
fixtures, local subprocesses, and retained upstream inputs; they do not call
live models. Platform-specific or unselected integration cases may skip.

For a focused run, select a folder or test file, for example:

```bash
python -m pytest tests/contracts tests/unit
python -m pytest tests/e2e/test_archived_trajectory.py
```

## Coverage

| Folder | Main responsibilities |
| --- | --- |
| [contracts](contracts/) | Public records, planning, evaluation, selection, persistence, and failure handling |
| [unit](unit/) | Focused implementation and regression checks |
| [adapters](adapters/) | Native formats, configuration inspection, subprocesses, and evidence transport |
| [e2e](e2e/) | Complete offline workflows, archived-run import, and explicitly selected native scenarios |
| [integrations](integrations/) | Explicitly selected Harbor, SkillEvaluator, and NeMo Agent Toolkit integrations |

## Select a live integration explicitly

Prepare a compatible runtime, factory, and local configuration before selecting
a profile. Live execution can invoke models or use remote resources.

```bash
python -m pytest tests/e2e --aef-live codex_local --aef-profile-config profiles.local.json
```

Available names are `codex_local`, `claude_local`, `vertex_gcp`, `opensre_gcp`,
`openkritt_gcp`, `harbor_native`, `skillevaluator_import`, and `nat_batch`.
Repeat `--aef-live` to select more than one. Use `tests/integrations` for the last
three; their required configuration is checked in [the integration tests](integrations/test_optional_libraries.py).

Start with the [E2E profile template](e2e/profiles.example.json) or the
[local example bindings](../examples/profiles.local.example.json). Copy the
appropriate template to an ignored local file and replace its placeholders.
Keep credentials outside committed configuration, using the runtime's supported
authentication mechanism. A selected profile must use its real runtime or
importer: missing prerequisites fail, and a scripted fallback cannot substitute.

## Preserve fixture integrity

```bash
python scripts/verify_acceptance_checkpoint.py
```

Keep the pinned test code, fixture bytes, source manifests, and retained licenses
intact. Add new fixtures separately. The upstream files are deliberate test
inputs with their own terms; see [third-party notices](../THIRD_PARTY_NOTICES.md).
Fixture checks establish integrity and transport, not general agent quality.

Keep local captures and scratch output in ignored `demo-output/` or
`test-artifacts/`. Share only deliberately reviewed, scrubbed evidence.
