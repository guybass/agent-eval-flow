# E2E runtime profiles

These profiles run the same library acceptance tests through different real
backends. They check that outputs, traces, custom measurements, persistence and
reports can be used. They do not require an agent to solve a benchmark or achieve
a quality score. A reported agent failure can be a correctly handled library
result; missing evidence that a required execution path happened is not a pass.

This is a test-first contract. The live factories are **not implemented yet**.
No model calls, cloud provisioning or authentication changes were performed when
writing this document. The offline subprocess harness is test-owned and scripted;
it does not demonstrate a working live integration.

The [showcase revision](SHOWCASE.md) requires full investigations and reviews
in addition to tiny smoke tests. Inputs are already downloaded with immutable
provenance. During live execution, profiles must retrieve native traces, tool
results, final artifacts and the scenario evidence bundle into the local cache.
The checked-in inputs are not fabricated live outputs.

Factories must follow the [revision-0.4 data contract](../../docs/DATA_CONTRACT.md)
and [typed signatures](../../docs/contracts/agent_eval_flow.pyi). In particular,
every returned `Run` supplies `output_state`: captured output is `available`
(including a valid JSON null); proven absent output is `unavailable`; capture
uncertainty is `unknown`. Execution status alone does not determine this field.
Direct native CLI/API adapters remain supported; a profile need not adopt the
optional whole-job interface. Credentials/clients remain runtime bindings.

## Selecting a profile

The default test run selects no live profiles. Select them explicitly:

```text
python -m pytest tests/e2e --aef-live codex_local --aef-profile-config profiles.local.json
python -m pytest tests/e2e --aef-live vertex_gcp --aef-profile-config profiles.local.json
python -m pytest tests/e2e --aef-live opensre_gcp --aef-live openkritt_gcp --aef-profile-config profiles.local.json
```

An unselected live profile is skipped. A selected profile with missing settings,
credentials, dependencies, artifacts or an unavailable factory fails with an
actionable setup error. It must not silently fall back to the scripted harness.

| Profile | Execution host | Agent/harness | Model connection |
| --- | --- | --- | --- |
| `codex_local` | Local machine | Codex CLI | Its explicitly configured provider |
| `claude_local` | Local machine | Claude Code CLI | Anthropic or its documented Vertex integration |
| `vertex_gcp` | GCP worker | Test harness with instrumented tool loop | Google Gen AI SDK with Vertex/Agent Platform |
| `opensre_gcp` | GCP worker | Pinned OpenSRE checkout | Its native `vertex-ai` provider for the first scenario |
| `openkritt_gcp` | GCP worker | Pinned OpenKritt checkout | Provider supported by that checkout |

**Hosting on GCP and using Vertex are separate settings.** Running Codex on a
GCP machine does not establish Vertex model support. OpenSRE and OpenKritt have
separate scenarios and results; these tests do not compare their quality.

The profile file is a JSON object keyed by profile name. This example is a
template, not a ready-to-run configuration:

```json
{
  "vertex_gcp": {
    "factory": "your_test_integrations.vertex:make_backend",
    "backend_ref": {
      "name": "tiny-vertex-harness",
      "revision": "REPLACE_WITH_IMMUTABLE_REVISION"
    },
    "settings": {
      "sdk_version": "REPLACE_WITH_PINNED_GOOGLE_GENAI_VERSION",
      "model_call_limit": 24,
      "output_token_limit_per_call": 4096,
      "supervisor_timeout_s": 600
    },
    "deployment": {
      "kind": "gcp",
      "project": "REPLACE_WITH_TEST_PROJECT",
      "location": "REPLACE_WITH_LOCATION",
      "worker_image": "REPLACE_WITH_IMAGE_DIGEST"
    },
    "model": {
      "provider": "vertex",
      "id": "REPLACE_WITH_ENABLED_MODEL_ID"
    }
  }
}
```

Each future factory has this interface:

```python
def make_backend(config: dict, *, workspace: Path) -> BackendAdapter: ...
```

The factory connects to a prepared runtime. It does not implicitly provision a
project or upgrade dependencies. Local deployments use `deployment.kind="local"`.
Models, executable versions and upstream revisions are supplied before a live
run; placeholders must fail validation. Keep credentials outside this JSON file.

## What every live profile must preserve

Each assignment receives a fresh workspace and scenario state. Record the
candidate fingerprint, actual CLI/SDK and model versions, loaded skill/tool
hashes, resolved settings, deployment identity and reset evidence. Record a
configuration value as unknown when the provider does not expose it.

Retain native stdout/events or response JSON, stderr, exit/finish reason, native
session/request IDs and produced artifacts as linked evidence. The normalized
`Run` references those records. Tool inputs and outputs need their own evidence;
the final answer saying it used a tool is insufficient. Preserve retries and
subruns with stable IDs. Capture wall time at the supervising process boundary.

Usage fields remain native observations with declared scope. A token count does
not become a bill, missing usage does not become zero, and model spend does not
silently include GCP compute. Cost or quality interpretations belong to the
chosen metrics. Custom metrics can select their required evidence.

The tiny harness explicitly records skill injection, tool execution and flow
stages. A CLI test records the corresponding native operation when it exposes
one. If a requested tool path was never exercised, report that test as failed or
unsupported rather than accepting an empty trace as proof of tool integration.

## Toy test normalization contract

`test_live_toy.py` supplies `Candidate.settings.e2e_mode` (`plain`, `skill`,
`tool` or `flow`), `fixture_dir`, `mission` and `response_schema`. Its public task
has `task_id` and a fresh receipt string in `text`. The factory receives the
profile once; the backend reads these scenario settings from each run request.
Produce a completed run whose output has the same `task_id`, a string `message`
and an array of strings `items`. This verifies a deliberately small protocol;
it does not grade the usefulness of the message.

Every run must expose `run.artifacts["native.trace"]` and
`run.artifacts["native.receipt"]`. Materialize remote evidence into the local
test cache: both `ArtifactRef.uri` values must be absolute filesystem paths to
existing files, with matching SHA-256 hashes. The trace contains the original
native events/response. The receipt is adapter-produced JSON with these keys:

```json
{
  "profile": "vertex_gcp",
  "native_id": "UPSTREAM_INVOCATION_ID",
  "runtime_revision": "ACTUAL_RUNTIME_REVISION",
  "model": {"provider": "vertex", "id": "ACTUAL_MODEL_ID"},
  "deployment": {"kind": "gcp", "project": "ACTUAL_GCP_PROJECT"}
}
```

`profile`, `native_id`, `runtime_revision` and `model.provider/id` are required
for every toy runtime. `deployment.kind/project` are asserted for `vertex_gcp`;
the project must match the selected profile. Use a distinct native ID per run,
also stored as `run.native_refs["invocation_id"]`. The receipt describes the
native trace; it does not replace that trace.

For `skill` and `flow`, record a `skill_loaded` event with
`fields.name="fixture-format"` and `fields.sha256` matching the candidate's
`skill.format.content.sha256`. For `tool` and `flow`, record a `tool_call` event
with `fields.name="fixture.echo"`, `fields.arguments.task_id` matching the task
and a mapping in `fields.result`. Its `message` must equal this request's fresh
`text`, so a stale tool receipt cannot satisfy the test. Each such event needs evidence in `inputs` or
`outputs`. In `flow`, the skill event must precede the tool event. The adapter
must derive these events from actual loading/calls, not from requested mode or
the model's assertion. Unknown cost/duration observations retain `value=None`
and a reason. The common consumer checks then exercise result inspection and
persistence without accuracy, cost or latency thresholds.

## Real dependency investigation

[test_live_workflow.py](test_live_workflow.py) adds a substantive scenario to
each of `codex_local`, `claude_local` and `vertex_gcp`. Candidate settings under
`workflow_e2e` declare downloaded inputs, mission, limits and evidence destination.
The agent reproduces six cases against pinned MarkupSafe source, then reads
relevant implementation lines through an actual follow-up tool call. The final
report references case results and source receipts.

Follow the exact [workflow fixture contract](fixtures/workflow/README.md).
Native capture must distinguish actual tool output from assistant text quoting
a tool result. Preserve the native invocation context, actual call identifiers,
requests/results and their source locations before projecting library events.
Never invent hidden reasoning. Download the complete `workflow.bundle`, including
input provenance/license, intermediate receipts and report, and verify its manifest.

The tiny three-call/256-token example limits were replaced above because they
cannot reasonably support this investigation. These are explicit configurable
bounds, not quality thresholds. The prepared runtime must enforce the declared
assignment limits and expose its effective settings.

## Codex CLI

The future adapter can invoke an argv equivalent to:

```text
codex exec --cd WORKSPACE --sandbox read-only --ignore-user-config --ephemeral --json --model MODEL_ID --output-schema SCHEMA_PATH --output-last-message FINAL_JSON_PATH -
```

Pass the mission on stdin; capture stdout as JSONL and stderr separately. JSONL
includes thread/turn/item events and turn usage; the final-message file holds
the schema-constrained response. Preserve both. Use a dedicated fixture
workspace with only declared context and retain loaded configuration evidence.
`--ephemeral` controls session persistence, not every external state source.
Use `workspace-write` only for a scenario that requires workspace writes.
[Non-interactive execution](https://learn.chatgpt.com/docs/non-interactive-mode),
[CLI reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli).

Authentication must already be available through the CLI's supported auth path.
Do not copy credential files into artifacts. For a skill scenario, explicitly
load the fixture skill and preserve its content hash; do not assume that a
prompt mentioning a skill proves it loaded. Local read-only inspection found
`codex-cli 0.153.4` and confirmed these flags with `codex exec --help` on
2026-09-07. No Codex task was invoked.

## Claude Code CLI

The future adapter can use this argv shape:

```text
claude -p MISSION --output-format stream-json --verbose --json-schema SCHEMA_JSON --model MODEL_ID --max-turns 3
```

Retain the complete stream and final result event. Alternatively, JSON mode
returns a single envelope containing the result and session metadata, with
schema output under `structured_output`. The documented JSON output also
includes `total_cost_usd`. Do not discard metadata when extracting the output.
[Programmatic execution](https://code.claude.com/docs/en/headless).

Use an isolated fixture workspace and explicit tool configuration. `--tools`
restricts built-in tools; `--allowedTools` controls which calls can run without
prompting. Neither substitutes for isolation. `--bare` skips automatic skill
discovery, so a skill test must arrange explicit fixture loading rather than
adding that flag blindly. [CLI reference](https://code.claude.com/docs/en/cli-reference).

For Vertex, the documented configuration uses `CLAUDE_CODE_USE_VERTEX=1`,
`CLOUD_ML_REGION`, `ANTHROPIC_VERTEX_PROJECT_ID`, Google credentials and access
to the requested model. Record the effective project/region because environment
precedence can override the declared values. This does not require the CLI
itself to run on GCP. [Claude Code on Google Cloud](https://code.claude.com/docs/en/google-vertex-ai).

## Small Vertex harness

Use a pinned `google-genai` client with explicit project, location and model ID.
Current SDK documentation calls this service Gemini Enterprise Agent Platform;
its client still accepts `vertexai=True` as a legacy alias of `enterprise=True`.
The profile retains the user-facing name `vertex_gcp`.
[SDK client source](https://github.com/googleapis/python-genai/blob/main/google/genai/client.py).

For the structured-response mission, configure `response_mime_type` and a
response schema, then preserve the full response alongside parsed output.
For the tool mission, the tiny harness executes the declared local tool and
records each model call and tool result. Disable hidden automatic tool loops or
instrument every automatic call; a final response alone cannot show the path.
[Structured output](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/control-generated-output),
[SDK function calling](https://googleapis.github.io/python-genai/).

Prerequisites are a prepared test project, billing/API enablement, appropriate
IAM/model access and Application Default Credentials. On a developer machine,
Google documents `gcloud auth application-default login`; a GCP worker should
use its configured workload/service identity. These are setup prerequisites,
not commands this test-writing task has executed.
[Google setup](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/start).

## OpenSRE and OpenKritt on GCP

Use the scenario-specific native capture contracts in the E2E tests. Pin each
upstream revision and runtime image. Run their substantive scenarios against dedicated
fixtures, preserving native artifacts before normalization. Provider support is
checked against the selected checkout; it is never inferred from the host.
The OpenSRE showcase explicitly uses its native `vertex-ai` provider through
the native AgentSession harness with runtime/tool observation. Final CLI JSON
alone cannot satisfy this trace contract. OpenKritt requires read-only native
step exports, retained successful harness output and a configured native
`post_script_id`; these are prepared-profile bindings, not public upstream APIs.
Both profiles must produce the same library-level objects and support normal
inspection, evaluation, save/load and report operations. No minimum incident
diagnosis or vulnerability-detection score is part of this plumbing contract.

Cloud workers, model access and private fixture services are provisioned before
these tests. The profile/backend owner supplies a fresh assignment namespace,
enforces the configured runtime bound, and cleans temporary processes, files,
containers and per-run remote resources in `finally` handling, including after
failed assertions or cancellation. Retain the evidence cache until assertions
and report generation finish. The E2E fixtures do not create or delete a GCP
project, and they do not assume a local pytest temporary directory cleans up a
remote worker. Persistent shared infrastructure remains the provisioning
owner's responsibility; per-run cleanup must not delete it.
