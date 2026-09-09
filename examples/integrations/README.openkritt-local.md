# Running the local OpenKritt example

The agent runs inside a prepared local OpenKritt Docker stack. Codex calls its
hosted model using the account configured in that stack. Agent Eval Flow binds
to the local API, reads native PostgreSQL records, and preserves the engine's
actual process streams through the observer. GCP is not required.

The binding expects the pinned checkout, an exclusive `aef-` Compose project,
the engine `/data` bind mount, a local repository mount, and a matching running
`openkritt_observer.py`. The watchdog can stop that dedicated engine and its
identified child runners, so the stack must not host unrelated work.

After preparing the stack and configuring Codex through OpenKritt's own account
setup, create a profile from the actual API catalog:

```powershell
.venv\Scripts\python.exe -m examples.integrations.openkritt_setup `
  --upstream-dir test-artifacts/openkritt-upstream `
  --engine-data-dir test-artifacts/openkritt-upstream/.data/engine `
  --local-repos-dir test-artifacts/openkritt-upstream/local_repos `
  --output test-artifacts/openkritt-local.json
```

The helper defaults to API port `13002`, project `aef-openkritt-local`, and
containers `aef-openkritt-local-db` and `aef-openkritt-local-engine`. It selects
a model returned by the native catalog; `--model` chooses an explicit listed
model. It creates or reuses a named source-only post-script and records the
exact Flask severity policy. It reads no credentials and starts no scan.

Run one complete study into a fresh directory:

```powershell
.venv\Scripts\python.exe -m examples.openkritt_local_review `
  --config test-artifacts/openkritt-local.json `
  --output demo-output/openkritt-my-run
```

The default task uses 22 pinned Flask tutorial files, four native workflow
steps, two cumulative repeats, and an 1,800-second overall deadline. Open the
generated `overview.html` for a readable account of the steps and their actual
outputs. `report.html` is the full library report; `result/` contains typed
saved objects; `evidence.zip` contains source files and native records. The
script also reloads the result and applies a different rubric to the same
capture without another model invocation.

Keep failed attempts and render their shared index explicitly:

```powershell
.venv\Scripts\python.exe -m examples.openkritt_attempts_report `
  --runs demo-output/openkritt-local demo-output/openkritt-local-run2 demo-output/openkritt-local-run3 `
  --assessment demo-output/openkritt-local-complete `
  --output demo-output/openkritt-results/index.html
```

The index labels these as repeated attempts of one integration scenario. It
does not treat step repeats as independent benchmark tasks or turn unknown
provider charges into zero. The capture score checks execution, evidence,
lineage and usable outputs; finding correctness needs a separately defined
evaluation suite.

The final offline assessment and complete portable archive can be reproduced
from the saved successful scan, using a fresh output directory:

```powershell
.venv\Scripts\python.exe -m examples.openkritt_local_regrade `
  --source demo-output/openkritt-local-run3 `
  --output demo-output/openkritt-local-complete `
  --complete-evidence
```

This command reads retained evidence without credentials, a running service or
new model calls. It preserves the original archive and produces a derived
capture whose only run-record change is the portable bundle reference. Both
capture fingerprints are recorded. The original execution attempts remain
separate from this offline reassessment.

The application-owned workflow variants and their two discovered native schema
constraints are documented in [the workflow notes](../workflows/README.md).
