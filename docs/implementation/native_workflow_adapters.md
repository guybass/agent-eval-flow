# Native workflow adapters

`agent_eval_flow.adapters.opensre` and `agent_eval_flow.adapters.openkritt`
implement native capture and lifecycle handling. Their offline tests use
representative native records and test-owned transports. Live GCP/model
compatibility remains a separate, explicitly selected E2E run.

The [local OpenKritt + Codex demonstration](local_openkritt.md) has additionally
completed with real native execution and retained tool streams. Its
application-owned Docker connection and observer are available under
`examples/integrations`; this does not establish the other live profiles.

## OpenSRE

The [local Docker binding](local_opensre.md) and
`examples.integrations.opensre_local_session:make_session` provide a verified
Codex route for the HDFS investigation. Their application-owned observer
preserves native construction callbacks and tool-call identities. The pinned
native session-goal `frozenset` fields are serialized as deterministic JSON
arrays, allowing the goal result and final turn to be retained together.

`OpenSREBackend(binding=..., runtime=...)` accepts either an embedded native
runtime or `ProcessOpenSRERuntime`. The process runtime invokes the library's
worker entry point under `ProcessSupervisor`, which supplies verified process
containment and the assignment deadline. The worker drives the existing native
`AgentSession.chat_until_goal`; OpenSRE retains its model/tool/goal loops.

The prepared environment supplies one `session_factory="module:callable"`.
Its function receives `request`, `workspace`, and `observer`, and returns a
`SessionHandle`. It connects the native model/provider and tools, attaches the
typed `observer.for_loop(scope)` callback when constructing each native Agent,
and exposes native session ID, effective configuration, deployment/model
observations, usage receipts, artifact files, final native TurnResult accessor,
and session cleanup. These are existing application/runtime bindings; they do
not define a new agent loop. The factory is also where an application registers
the six incident-investigation fixture tools for the live showcase.

The worker verifies the checkout's immutable revision and clean tracked source,
and verifies that the supplied AgentSession class came from that checkout.
Supported revision: `1a81e1a1b378ecc9aeb211fcaeecb0f5c93548e4`.
The pinned [`AgentSession` source](https://github.com/Tracer-Cloud/opensre/blob/1a81e1a1b378ecc9aeb211fcaeecb0f5c93548e4/core/agent_harness/harness.py)
does not expose `on_runtime_event` directly on `start`; the factory installs the
existing [`Agent` callback](https://github.com/Tracer-Cloud/opensre/blob/1a81e1a1b378ecc9aeb211fcaeecb0f5c93548e4/core/agent/agent.py)
at native construction. This is why a generic CLI final-answer parser cannot
satisfy the incident showcase.

Every observed typed event is spooled immediately. The contained worker also
mirrors the stream into a capture file, retaining partial events if it is
stopped. The adapter preserves the native TurnResult, event fields and source
lines, actual process argv/exit code, exposed usage, tool-produced artifacts,
and a self-contained evidence ZIP. Missing billing stays unknown. An embedded
runtime has no invented process exit receipt and advertises no hard wall-time
support; normal Study execution with a deadline requires containment.

The live-profile factory is
`agent_eval_flow.adapters.opensre:make_backend`. Its configuration supplies
`backend_ref` and a `runtime_factory="module:callable"` which constructs the
prepared runtime/connection. Credentials stay in that connection. GCP hosting
and Vertex model routing must be observed independently inside the runtime.

## OpenKritt

`OpenKrittBackend(binding=..., service=...)` stages verified manifest files,
imports the native workflow, submits exactly one scan, polls the acknowledged
scan ID, captures native workflow attempts and tool results, and retrieves
findings and artifacts before releasing owned staged files.

`OpenKrittHTTPConnection` implements the actual routes at supported revision
`1ba10d410b4a0a309bacf5bc2d14ec1c27ed8a09`:

| Operation | Native route |
| --- | --- |
| Import a workflow body | `POST /api/workflows` |
| Read the configured post-script | `GET /api/post-scripts/:id` |
| Submit/read scan | `POST /api/scans`, `GET /api/scans/:id` |
| Read canonical findings | `GET /api/scans/:id/vulnerabilities` |
| Download finding ZIP | `GET /api/scans/:id/export` |

The source verifies a required native **severity ranker**, model/provider/harness
selection, workflow ID and post-script ID. Declare model settings and
`severity_ranker` in `Candidate.settings.scan_options`. The native workflow,
repository, post-script and repetition options are supplied under
`Candidate.settings.openkritt` (the E2E alias `openkritt_e2e` is accepted).
The adapter does not silently choose a ranker or provider.
[Scan validation](https://github.com/Kritt-ai/open-kritt/blob/1ba10d410b4a0a309bacf5bc2d14ec1c27ed8a09/backend/src/lib/validation.js),
[scan routes](https://github.com/Kritt-ai/open-kritt/blob/1ba10d410b4a0a309bacf5bc2d14ec1c27ed8a09/backend/src/routes/scans.js),
[workflow routes](https://github.com/Kritt-ai/open-kritt/blob/1ba10d410b4a0a309bacf5bc2d14ec1c27ed8a09/backend/src/routes/workflows.js).

The prepared service connection supplies three deployment-specific bindings:

- A stager. `LocalRepositoryStager` implements fresh, manifest-checked staging
  into an existing local-repository mount. A remote deployment supplies its
  existing staging transport. Only the exact owned namespace is released.
- A read-only collector returning `WorkflowCapture`: original JSON bytes for
  all scan-scoped native step metadata/results, successful and failed harness
  stdout/stderr, observer revision, and explicit inventory completeness. Native
  HTTP scan/findings routes do not expose this complete history.
- A stop/deadline controller. Confirming an HTTP request is insufficient.
  Hard-limit support requires both confirmed stop and a pre-armed namespace
  deadline, covering a scan submission whose acknowledgment is lost. The
  controller exposes `arm_deadline`, `stop_scan`, and `disarm_deadline`.

The adapter maps native step/repeat/predecessor IDs, outputs and token receipts;
it preserves Codex command executions and Claude tool-use/tool-result pairs with
their original source lines. It never derives dollars from token counts.
Export failure leaves an evidenced completed scan completed while recording
incomplete capture. A lost submission acknowledgment is never retried, and an
unconfirmed stop retains staging instead of deleting inputs under active work.
Native scans/workflows remain in the existing service's history; the library
does not automatically delete those durable records.

The complete evidence ZIP exists even when there are no findings. The native
finding ZIP is retrieved only when findings exist; an empty finding array is
preserved unchanged. The live-profile factory is
`agent_eval_flow.adapters.openkritt:make_backend`, with
`connection_factory="module:callable"` returning the prepared connection.

## Local validation

```text
python -m pytest tests/adapters/test_native_workflows.py -q
```

Thirteen offline cases cover native iteration/tool joins, raw bytes and bundles,
collection failure after completion, acknowledged-scan timeout without retry,
strict source hash validation, incomplete containment capability, typed OpenSRE
event identities, completed/partial contained-worker captures, production recorder
finalization, and late export failures that preserve known terminal outcomes. They do not
claim an OpenSRE model call, an OpenKritt engine run, or GCP deployment occurred.
