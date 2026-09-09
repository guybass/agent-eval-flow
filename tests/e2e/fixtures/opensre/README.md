# OpenSRE: investigate a real log sample and retain the entire execution

This is a future native E2E acceptance test. The source pack and owned
observability tools exist now; the Agent Eval Flow library, native profile and
GCP integration are not implemented. No model call or cloud provisioning was
performed to create this fixture.

The mission is an HDFS incident investigation, using **2,000 downloaded log
lines**, rather than a prompt containing the answer. OpenSRE must obtain a
snapshot, search and paginate logs, inspect related signals, and deliver a
structured report. The returned library objects must let a reader inspect which
tools ran, their arguments/results, the model iterations, and the source of every
reported observation. The test checks these mechanics, not diagnostic skill.

## Downloaded data and provenance

[`upstream/HDFS_2k.log`](upstream/HDFS_2k.log) is an unmodified sample downloaded
from [Loghub](https://github.com/logpai/loghub), pinned to
`dd61d0952749ee7963bde24220d1be5ede023033`. It contains 1,920 INFO and 80 WARN
records mentioning 202 host addresses. The chosen window contains 20 WARN
records about block-serving exceptions, mixed with other HDFS activity.

[`SOURCES.json`](SOURCES.json) records each downloaded URL, immutable revision,
byte count, SHA-256 and retrieval date. The original
[`LOGHUB_LICENSE`](upstream/LOGHUB_LICENSE) is retained and must accompany the
downloadable evidence bundle. The dataset's stated terms allow research or
academic work with attribution; it is not relabeled as Apache-licensed data.
Citation: Jieming Zhu, Shilin He, Pinjia He, Jinyang Liu and Michael R. Lyu,
*Loghub: A Large Collection of System Log Datasets for AI-driven Log Analytics*,
ISSRE 2023.

The window and change tickets in [`context.json`](context.json) are **owned
exercise context**. The change tickets are explicitly synthetic. Log-level
counts and host/component inventories are computed from the downloaded bytes.
There are no invented historical production metrics or claimed causal links.
The sample is not a complete cluster history, and its timestamps have no
declared timezone. These limitations are visible to the agent.

## Concrete input and flow

[`incidents.json`](incidents.json) contains one task and its investigation
mission. A/B candidates each receive a fresh native session, fixture store,
snapshot and artifact directory. They are two assignments of OpenSRE, not a
comparison with OpenKritt. The test allows 600 seconds per assignment and asks
the native profile for an 18-iteration cap.

[`incident_store.py`](incident_store.py) provides six ordinary function tools:

| Tool | Arguments | Actual response |
| --- | --- | --- |
| `fixture_incident_open` | `incident_id` | Alert context, limitations, fresh `snapshot_id` |
| `fixture_logs_search` | Snapshot and a level/text query, or returned cursor | Four exact source rows, line IDs/hashes, total count, next cursor |
| `fixture_metrics_query` | Snapshot | Counts by hour and log level, with source line IDs |
| `fixture_topology_describe` | Snapshot | Observed addresses and sample counts; component names |
| `fixture_changes_list` | Snapshot | Three structured, explicitly synthetic change tickets |
| `fixture_report_write` | Snapshot and report object | Written report path/hash and evidence count |

The profile registers these through OpenSRE's native tool provider. The fixture
does not choose tools, call a model, orchestrate an agent, or supply a diagnosis.
`IncidentStore.call(..., tool_call_id=..., loop_id=...)` receives the real native
call identity from execution hooks. Every successful call writes an independent
receipt containing its exact arguments/result and start/end time. A continuation
cursor is generated only when its first page has actually been requested.

The report contains `incident_id`, `summary`, `hypotheses`, `timeline`,
`evidence`, `limitations`, and `next_actions`. Each evidence item cites a returned
receipt ID. The test requires citations for logs, metrics, topology and changes;
it does not require a particular hypothesis, recommendation, or wording.

## Native interface and capture seam

The native revision is `1a81e1a1b378ecc9aeb211fcaeecb0f5c93548e4`.
Downloaded source evidence is retained under [`upstream/opensre`](upstream/opensre)
with its original Apache 2.0 license. Relevant primary sources:

- [`AgentSession.start(..., tools=...)`](https://github.com/Tracer-Cloud/opensre/blob/1a81e1a1b378ecc9aeb211fcaeecb0f5c93548e4/core/agent_harness/harness.py)
  and `chat_until_goal` run the complete native session harness.
- [`ToolProvider`](https://github.com/Tracer-Cloud/opensre/blob/1a81e1a1b378ecc9aeb211fcaeecb0f5c93548e4/core/agent_harness/ports.py)
  exposes action tools, resources and the tuple observer.
- [`Agent(..., on_runtime_event=...)`](https://github.com/Tracer-Cloud/opensre/blob/1a81e1a1b378ecc9aeb211fcaeecb0f5c93548e4/core/agent/agent.py)
  accepts the typed runtime callback.
- [`RuntimeEvent`](https://github.com/Tracer-Cloud/opensre/blob/1a81e1a1b378ecc9aeb211fcaeecb0f5c93548e4/core/events.py)
  defines actual provider request, tool execution and iteration events.
- [`ReactLoop`](https://github.com/Tracer-Cloud/opensre/blob/1a81e1a1b378ecc9aeb211fcaeecb0f5c93548e4/core/agent/react_loop.py)
  emits those events around real model and tool calls.
- [`TurnResult`](https://github.com/Tracer-Cloud/opensre/blob/1a81e1a1b378ecc9aeb211fcaeecb0f5c93548e4/core/agent_harness/turns/turn_results.py)
  supplies `final_intent`, `action_result` and `assistant_response_text`.

The old `opensre --json ask` envelope suppresses intermediate events. This test
therefore uses the embedded **complete `AgentSession`**, with its native
planning/tool loop and Vertex client. It does not replace OpenSRE with a generic
function-calling toy loop. The prepared profile must wire the typed callback at
native Agent construction; the session's tuple observer alone does not retain
every typed event. This version-specific observer wiring is proposed adapter
work, not a claimed CLI flag or a completed upstream feature. Missing event
capture is a failed selected live profile.

## Outputs that must be downloaded

Each `Run.output` is the unmodified serialized final native `TurnResult`. Its
artifacts include the following verified local files and a ZIP containing them:

| Artifact key | Bundle member |
| --- | --- |
| `native.turn` | `native/turn.json` |
| `native.trace` | `native/runtime.jsonl` |
| `native.receipt` | `native/receipt.json` |
| `native.stderr` | `native/stderr.txt` |
| `incident.tool_audit` | `incident/tool-audit.jsonl` |
| `incident.report` | `incident/investigation.json` |
| `incident.source_logs` | `inputs/HDFS_2k.log` |
| `incident.context` | `inputs/context.json` |
| `incident.provenance` | `inputs/SOURCES.json` |
| `incident.license` | `inputs/LOGHUB_LICENSE` |
| `incident.bundle` | Downloaded ZIP with the above and `manifest.json` |

Runtime JSONL lines use our capture envelope:
`{"sequence": 0, "invocation_id": "...", "loop_id": "...", "event": {...}}`.
`event` is the serialized upstream typed dataclass, with native field names.
The other fields identify/order records; they are not invented OpenSRE fields.
Keep every event, including errors and additional iterations. `loop_id` scopes
native tool-call IDs so different loops cannot collide. Each normalized library
Event preserves `fields.native`, `fields.native_sequence`, and a `source`
pointing to the exact `line:N` in `native.trace`.

The adapter-produced `native.receipt` has schema
`aef-opensre-investigation-receipt/1`, fresh `invocation_id`, `agent.revision`
and `agent.entrypoint`, `deployment.provider/execution_id`, Vertex
`model.provider/project_id/location/resolved_models`, and actual
`process.argv/exit_code`. Its `capture` contains `complete`, `event_count`,
`tool_receipt_count`, and `runtime_callback="core.agent.Agent.on_runtime_event"`.
The ZIP manifest maps each member path to its SHA-256 and records invocation ID.
All files are fetched from the worker, hash-checked and retained after cleanup.

## What makes this an E2E acceptance test

At least three real provider iterations and all six tools must execute. At
least eight distinct log lines must be fetched over multiple pages. The test
joins native start/end events to independent tool receipts, validates cursor
dependencies across iterations, checks downloaded lines against source bytes,
checks derived counts, resolves report citations, and verifies ZIP contents.
An answer claiming tool use cannot satisfy those checks.

The library evaluator counts actual captured tool receipts and attaches one
source locator per call. It also exercises a dependent metric, summaries,
comparison, selection, explanation, report generation and save/load. The shared
helper's legacy `wiring`/`mean_probe` IDs now contain actual tool-call counts in
this scenario, not the former fixed 101. Counts are diagnostics; more calls are
not declared better. Native cost/token observations are retained when captured;
missing observations stay unknown with a reason.

Run this fixture's input verification without the future library or cloud:

```text
python -c "from tests.e2e.fixtures.opensre.incident_store import verify_inputs; print(len(verify_inputs()))"
```

Selecting `opensre_gcp` still requires a prepared GCP worker/profile, native
checkout, Vertex access and the future adapter. Collection never provisions
infrastructure or substitutes a canned trace for native execution.
