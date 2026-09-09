# Live dependency investigation

This scenario exercises a native agent's investigation of a real, downloaded
dependency: MarkupSafe 3.0.2. A support incident asks why six application call
sites produce different HTML. The agent discovers the files, executes cases,
chooses follow-up source reads from those results, and delivers a report with
reproduction and source citations. It runs independently on Codex, Claude Code,
or the Vertex harness. A/B tags exercise candidate bookkeeping; this scenario
does not claim that one tag is a better agent configuration.

## Actual input, provenance and license

Six files totaling 23,019 bytes were downloaded on 2026-09-08 from
[pallets/markupsafe](https://github.com/pallets/markupsafe/tree/28ace20b140d15c083e1cbc163ee6b7778ba098c):
README, changelog, BSD-3-Clause license, two implementation files, and upstream
escape tests. [SOURCE_MANIFEST.json](SOURCE_MANIFEST.json) records the immutable
commit, exact raw URLs, sizes and SHA-256 hashes. Files are preserved byte for
byte; [the upstream license](downloaded/LICENSE.txt) accompanies every bundle.
`cases.json`, the mission, tool, schema and receipt format are test-owned. The
support incident is a constructed application scenario, not an upstream bug
report. The actual code used by the reproduction is downloaded upstream code.

Case examples include the same HTML passed to `escape(str)` and
`escape(Markup(str))`, quotes, multilingual input, template interpolation and
escaping an already escaped value. The tool returns actual input/output/type
records for all six. It explicitly loads the pinned pure-Python implementation;
it neither installs MarkupSafe nor selects an unrelated installed version.

## Native profile contract

`test_live_workflow.py` supplies `Candidate.settings.workflow_e2e` with fixture
and output-cache paths, input and tool hashes, mission/schema paths and bounded
execution settings (600 seconds and at most 16 observed tool calls). Only the
owned Vertex harness receives a 10-model-call ceiling: it controls and records
every SDK dispatch, including attempts that fail and are retried. A Claude
profile may separately configure its supported native turn bound. Codex command
events do not reveal every hidden model invocation; neither a model-call count
nor an enforceable model-call ceiling is inferred from those events.
The backend must stage the exact input files in each fresh assignment workspace
and materialize remote results into the local output cache. An unselected live
profile skips. A selected unsupported or unimplemented profile fails; an owned
scripted conversation cannot stand in for the native run.

Expose the three tools described in [MISSION.md](MISSION.md). Each action runs
the actual `investigation_tool.py` subprocess:

```text
python -B investigation_tool.py --fixture STAGED_FIXTURE --receipts RUN_RECEIPTS --invocation REQUEST_RUN_ID --nonce TASK_NONCE
```

Stdin is `{"action":"run_cases","arguments":{"case_ids":[...]}}`, or the
corresponding inventory/read action. Stdout is the unmodified UTF-8 JSON receipt.
The program also writes that receipt to `RUN_RECEIPTS/<receipt_id>.json`. The
runtime binds workspace/nonce/invocation; the model supplies action arguments.
CLI terminal calls and Vertex function calls can use this same execution tool.
Record genuine choices and results, including any failed/retried calls. This
tool creates no agent conversation, fake model decisions, or native events.

Each successful tool call becomes a normal `Event(kind="tool_call")`:

| Field | Meaning |
| --- | --- |
| `fields.name` | `workflow.inventory`, `workflow.run_cases`, or `workflow.read_source` |
| `fields.arguments` | The exact action arguments |
| `fields.result` | The complete original tool receipt |
| `fields.decision_event_id` | ID of the captured model-response event that selected this tool |
| `source` | Location of that tool result in the unchanged native trace |
| `inputs`, `outputs` | References to captured tool arguments/results |

The linked `Event(kind="model_response")` has a source pointing to the real
model response/native command selection. This is a normalization name, not a
claim that a native CLI exposes its hidden model round. Its execution ID matches the tool
event; it precedes the tool result. Source locators use `line:N` for a native
JSONL record or `json:/pointer` for a nested JSON record. Preserve native call,
session and iteration IDs when exposed; never fabricate unsupported native IDs.
If a provider lacks an ID, source locations and observed execution boundaries
carry the linkage. Capturing private reasoning is neither required nor desired.

For Vertex, the owned harness records actual requests/responses as native JSONL
records; those are original SDK data plus clearly identified harness metadata.
For native CLIs, preserve their original streams. The test resolves the source
and checks the native record type and pairing, in addition to receipt contents:

- Codex: `item.started` and `item.completed` for `command_execution`, matching
  item IDs, fixture-tool command and successful exit; receipt in the completed
  command's `aggregated_output`.
- Claude: assistant `tool_use` paired with user `tool_result` by native tool ID;
  receipt in that result, not an assistant text block.
- Vertex: `sdk.response` retains the original SDK
  `model_dump(mode="json")`, including the selected `function_call`. The owned
  `harness.tool_result` envelope has `model_call_id`, `result` (the complete tool
  receipt), and an explicitly harness-generated relationship to the response's
  `call_id`. The call ID describes observed harness dispatch; it is not presented
  as an ID returned by the model provider. Each real attempt starts with an
  `sdk.request` envelope containing that unique `call_id` and original request.
  The test counts these attempts to check the enforceable Vertex ceiling. The
  adapter records failed attempts too; a retry is another dispatch, not a rewrite
  of the previous request record.

The Codex scenario exposes the fixture through its native terminal. Claude may
use its terminal or configured function tools. The fixture's native command and
function arguments remain inspectable. A final answer quoting an invented
receipt cannot satisfy these typed native event checks.

The important iteration is semantic: a `run_cases` receipt is produced, then a
later model response selects a source read and names that receipt in
`based_on_receipt_id`, then the final report cites both receipts. Parallel reads,
grouped reproductions and variable numbers of rounds are allowed. For native
CLIs, the claim is an observed sequence of command/tool selections and results;
it does not reconstruct hidden model rounds.

## Outputs someone can inspect

Every run returns these `ArtifactRef` entries, with local files and verified
SHA-256 hashes. The adapter collects and downloads remote files before returning.

| Artifact | Contents |
| --- | --- |
| `native.trace` | Unmodified native conversation/tool events or SDK records |
| `native.receipt` | Existing live-profile receipt: native ID, runtime/model and deployment |
| `workflow.calls` | JSON array of original successful tool receipts in observed order |
| `workflow.inputs` | Exact `cases.json` used for the run |
| `workflow.source_manifest` | Exact pinned source manifest |
| `workflow.report` | Final schema-shaped report, exactly equal to `Run.output` |
| `workflow.bundle` | Portable ZIP containing all the above plus the downloaded source/docs/license |

The ZIP's `manifest.json` contains `run_id`, `native_id` and an `artifacts` list
of `{"artifact":"native.trace","path":"trace.jsonl","sha256":"..."}`.
Other artifacts use their own relative paths. Input source files live under
`input/downloaded/<upstream path>`. The bundle must preserve every recorded byte
and exclude itself from the manifest's artifact list. No credentials belong in
the bundle. This is a downloaded deliverable, not a log path on an inaccessible
worker or a mocked sample transcript.

## What the evaluation establishes

The custom `evidence_integrity` evaluator produces one diagnostic row per case
and the fraction of report rows whose values equal actual executed results and
whose source citations resolve to later tool receipts. It records reasons and
artifact references. The positive E2E expects all six links to survive and the
score to equal 1.0. It makes no assertion about explanation quality, preferred
agent, best tool, or minimum model accuracy. The test also verifies the actual
workflow events, round linkage, fresh invocation IDs, downloaded inputs, local
bundle contents, persistence and inspection via the library's public objects.
Missing cost/tokens remain unknown. Cost is not derived from the number of tools.

The earlier fixed 101/108 probes still exercise shared consumer plumbing; this
scenario additionally requires real trace and report evidence. A fabricated
report, a one-message answer, stale receipts, or an unexecuted model-directed
source read cannot satisfy the live workflow contract.

The fixture-only check needs no model, API library or cloud:

```text
python -m pytest tests/e2e/test_live_workflow.py -q
```

It checks the downloaded inputs and invokes the real reproduction/source tool;
the three live tests remain unselected. That success is fixture validation,
not a claim of an implemented Agent Eval Flow backend. Select a live profile
using the existing `--aef-live` and `--aef-profile-config` flags after adapters
exist. No model calls or cloud runs were made while writing this scenario.
