# E2Es worth inspecting

An end-to-end example should show an agent doing work: encountering evidence,
choosing a tool, reading its result, continuing the investigation and producing
an artifact that can be traced back to that work. The original echo/one-response
cases remain useful smoke tests. They are no longer the showcase scenarios.

| Scenario | Actual input | Work and evidence required |
| --- | --- | --- |
| [Published SWE-agent run](fixtures/sweagent_archive/README.md) | Downloaded, immutable Pydicom bug-fix trajectory: 12 actions, tool observations, failed edits, corrections, final patch and historical usage | Import the original actions and observations, attach exact source pointers, measure the captured history, save/load, inspect and re-evaluate without executing an agent |
| [OpenSRE investigation](fixtures/opensre/README.md) | Downloaded historical HDFS log sample with 2,000 lines, log-derived context and explicitly synthetic incident/change information | Investigate through real OpenSRE tools and iterations; retain native runtime events, tool audit, observations, structured incident report and retrieved evidence bundle |
| [OpenKritt review](fixtures/openkritt/README.md) | Downloaded, pinned Flask tutorial app: authentication, blog routes, database/schema, templates and upstream tests | Map the app, review different concerns, reconcile the outputs across repeated workflow passes; retain native step lineage, harness tool activity, findings and exported artifacts |
| [Codex / Claude / Vertex investigation](fixtures/workflow/README.md) | Downloaded, pinned MarkupSafe implementation and tests, plus six concrete escaping cases | Execute the cases, inspect relevant source in a follow-up tool call, then deliver a structured report whose values and citations resolve to those actual tool results |

OpenSRE and OpenKritt remain separate studies. Different domains do not become
competing candidates. Candidate tags in these integration cases establish
separate native runs and correct assignment bookkeeping, not a claim that one
candidate is better.

## What counts as an output

For a live showcase, the backend must retrieve the real output files into the
local evidence cache. A success string, final answer, normalized event count or
adapter receipt alone is insufficient. The scenario-specific contracts require:

1. Original runtime events or native step/harness output, retained unchanged.
2. Tool inputs and results, correlated to the actual operation and invocation.
3. Workflow/iteration identity and the evidence used by later steps.
4. The delivered report/findings, with links to supporting captured work.
5. An inspectable bundle containing source inputs, native evidence and outputs,
   with hashes verified after retrieval. A bundle exists even if a review finds
   no vulnerabilities; this is distinct from OpenKritt's conditional findings ZIP.
6. The normal library objects, explanations, measurements, saved result and HTML
   report. Loaded records must still resolve their evidence.

We check observable actions and returned observations. We do not demand hidden
model reasoning, invent tool-choice explanations, or turn message count into
an intelligence score. Native usage/timestamps are retained when available;
missing values remain unknown.

## What the assertions mean

The richer tests exercise actual data: a source line, a tool's returned value,
a workflow predecessor ID, an action's observation or a report citation. Custom
evaluators can measure the consistency/completeness of these records through
the same public API as any user-defined metric. Existing fixture arithmetic
remains a small independent check of evaluator dispatch and dependencies.

This is still library acceptance, not a benchmark of model ability. A submitted
patch is not assumed correct. An empty security findings list can be valid.
An investigation can retain uncertainty. But a required tool path that never
happened, a lost stage, an altered native result or an unresolved evidence link
cannot be presented as a successful integration.

## What exists now

The pinned external inputs and SWE-agent history are downloaded and checked in
with source URLs, hashes and upstream licenses/notices. The SWE-agent archive
has an [action-by-action inspection page](fixtures/sweagent_archive/INSPECT.md)
you can read now. It is a published historical run, not a new run from our lib.

The acceptance tests are written before implementation. The production package,
live adapters and prepared cloud profiles remain to be built. No live showcase
bundle or model-quality result is fabricated. [Runtime profiles](PROFILES.md)
describe the capture prerequisites; each fixture README spells out the exact
native fields and the additional observer/export work needed.

## Adapter implications for LLD review

The six public objects do not change. The stronger cases reveal necessary work
inside the adapters and prepared runtime bindings:

- OpenSRE's CLI JSON envelope is insufficient for this showcase. Use the native
  AgentSession harness with observed runtime/tool events, preserving the same
  upstream agent loop. This requires an explicit event observer in the prepared
  profile; it is not an invented trace field in CLI output.
- OpenKritt's public scan/findings response is insufficient for workflow history.
  Export native step metadata/results through the prepared worker and preserve
  successful harness output before upstream discards it. The selected harness
  must actually expose tool events. Native post-script configuration is also
  required by the pinned scan API.
- Codex/Claude/Vertex profiles need enough turns and tool output capacity for
  a real investigation, plus source-linked native capture and artifact retrieval.
  The old three-call/256-token tiny-harness settings are not showcase defaults.
- The archived SWE-agent case uses a test-owned importer implementation to
  exercise the existing extension boundary. It does not add a mandatory new
  production backend or execute anything from the downloaded archive.

These refinements are requirements for the relevant showcase profiles. They do
not force every library user, backend or metric to use these domain schemas.
