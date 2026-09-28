# MCP memory server: two ways an agent's memories are silently lost

Case study on the official knowledge-graph memory server (`modelcontextprotocol/servers`, `src/memory`), run with
Agent Eval Flow and Toolscore together. Two findings, each with a small fix, a regression test and measured
evidence:

1. **One unrelated write deletes lines the server cannot read** (unreleased `main`, regression from #4717).
   In 48 recorded scenario cells, `main` silently lost data in 14 of 16 per build, against 4 for the published
   package and 2 with the fix.
2. **`create_entities` drops new facts about existing entities without saying so** (published and `main`).
   Across three models and 312 recorded agent runs, the fact about an existing entity was stored in
   **69 of 130** runs on `main` and **116 of 130** with a one-line skip report; silent drops went from **47 to 1**.

Neither finding is on the upstream tracker (searched 2026-09-28). Nothing here has been submitted upstream.

```sh
python examples/mcp_memory_review.py --output demo-output/mcp-memory                  # finding 1
python examples/mcp_memory_agent_review.py --model gpt-5.6-luna \
    --output demo-output/mcp-memory-agent/gpt-5.6-luna                                # finding 2 (also gpt-4.1-mini, gpt-5.4-mini)
```

Both scripts import recorded runs and make no MCP, model or network calls. Each writes `report.html`
(outcomes per candidate, with evidence links into the capture) and `tools.html` (Toolscore view of every
MCP tool trace).

## How the findings were reached

1. `toolscore mcp test` against the official reference servers flagged failing grades. Checked by hand, most
   were Toolscore false positives (fixed in yotambraun/Toolscore#3, merged; release 1.9.1 pending) and two filesystem bugs
   that others had already reported (#4643/#4674, #4178). Recorded in `PROTOCOL.md` and `RUNLOG.md`.
2. Reading what `main` changed since the last release (2026.8.31) found finding 1: #4717 (merged 2026-09-03)
   makes `loadGraph()` skip lines that fail validation, while every mutation still rewrites the whole file.
3. Reading the tools found finding 2, and an agent experiment was pre-registered to measure whether it matters
   in practice (`PROTOCOL.md`, Addenda B and C) before any agent run.

Two negative results are kept in the protocol: `mcp-server-fetch`'s default extraction dropped 0 of 70
benchmark answers (Addendum A), and no other change merged since 2026.8.31 showed a defect.

## Finding 1: unreadable lines are deleted by the next write

Three builds (published `@modelcontextprotocol/server-memory@2026.8.31`, `main` at `f46d957`, fix `0ef28b0`),
eight seeded files (a clean file, six kinds of unreadable line taken from real reports, a valid entity with an
extra field), two unrelated writes (`create_entities`, `add_observations`): 48 cells, each in a fresh sandbox
file, driven over MCP stdio by Toolscore's `MCPStdioClient`. `examples/mcp_memory_study/memory_matrix.py`
re-records it; a re-run matched all 48 cells byte for byte, including the resulting files.

| Build | Cells with silent data loss | Facts lost | Unrelated write applied | `read_graph` works |
| --- | --- | --- | --- | --- |
| published 2026.8.31 | 4 / 16 | 4 | 12 / 16 | 6 / 16 |
| **main f46d957** | **14 / 16** | **22** | 16 / 16 | 16 / 16 |
| fix 0ef28b0 | 2 / 16 | 2 | 16 / 16 | 16 / 16 |

| Seeded line (source) | published | main | fix |
| --- | --- | --- | --- |
| `null` observation (#2044) | kept; `read_graph` errors | **deleted** | kept |
| missing `entityType` (#4717) | kept; `read_graph` errors | **deleted** | kept |
| truncated line (#1481) | every call errors | **deleted** | kept; server works |
| two objects on one line (#1819, #3173) | every call errors | **deleted** | kept; server works |
| relation missing `relationType` (#4717) | kept | **deleted** | kept |
| unknown record type | deleted (already released) | deleted | kept |
| extra field on a valid entity | field dropped | field dropped | field dropped (not addressed) |

#4717 turned an outage (the published server fails loudly on these files) into silent data loss. The fix keeps
#4717's availability and writes unreadable lines back unchanged. It does not repair them; the tools still cannot
see or delete them.

## Finding 2: `create_entities` drops facts about existing entities

`create_entities` ignores entities whose name already exists, as the README documents. The agent sees only the
tool description ("Create multiple new entities") and a response listing what was created, so a fact sent
through `create_entities` for an existing entity is dropped with no sign. Agents do this often: `search_nodes`
matches the whole query as one substring, so a natural multi-word query (`"Alice penicillin"`) finds nothing and
the agent concludes the entity is missing; the README's example prompt also says to "Create entities for
recurring organizations, people". (Search semantics were discussed and kept in #2808; the fix does not change
them.)

The fix (`6bda4c1`) keeps the documented behaviour and reports it: `structuredContent.skipped` lists the names
not created, and a second text item says `Skipped 1 entity that already exists: Alice. Its observations were not
added; use add_observations for existing entities.`

### Experiment (pre-registered: `PROTOCOL.md` Addenda B and C)

- Servers built from `main` (`f46d957`, same `create_entities` behaviour as 2026.8.31) and from the fix branch
  (`32a93d3`; the PR commit `6bda4c1` differs only in the notice wording, "exist" → "exists").
- Each run: the server's own `tools/list` as function tools, the README's example memory prompt verbatim, the
  same seeded memory, and one user message: a new fact about an existing entity (10 scenarios) or about a new
  person (2 controls). OpenAI Responses API, at most 8 model calls.
- Outcome: the fact counts as stored if every fact keyword is in the target entity's observations at the end.
  Graded twice, independently (`grade_agent.py` in the study, and this importer's evaluator from the recorded
  file); the two agree on all 1,248 values.
- `examples/mcp_memory_study/agent_memory_eval.py` and `agent_scenarios.json` reproduce the runs.

| Model | Runs (update) per server | Fact stored: main → fix | Fisher exact p | Silent drops: main → fix | Controls (new person): main → fix |
| --- | --- | --- | --- | --- | --- |
| gpt-5.6-luna | 50 | 24 → **46** | 2.1e-6 | 22 → **0** | 8/10 → 10/10 |
| gpt-4.1-mini | 50 | 29 → **47** | 3.6e-5 | 20 → **1** | 5/10 → 6/10 |
| gpt-5.4-mini | 30 | 16 → 23 | 0.10 | 5 → **0** | 6/6 → 6/6 |
| **All** | **130** | **69 (53%) → 116 (89%)** | | **47 → 1** | |

- Mechanism: in 19 of the 24 gpt-5.6-luna runs that received the skip notice, the agent then called
  `add_observations` (in 18 as its very next write). With the fix, the fact was stored somewhere in 50/50 gpt-5.6-luna runs
  (40/50 on `main`); the remaining misses put it on a new entity (e.g. "Acme Corp Series C Round").
- gpt-5.4-mini often wrote nothing at all (9/30 on `main`, 7/30 with the fix), whatever the server did; when it
  wrote, the fact was stored 16/21 on `main` and 23/23 with the fix. Alone, its difference is not significant.
- The one silent drop with the fix (gpt-4.1-mini, u10): the agent received the notice and ignored it.
- Exploratory, not pre-registered: on `main` the agent's reply claimed the fact was saved while it was not in
  58 of 130 update runs ("Noted in my memory: Project Atlas's launch was pushed to November."), 11 with the fix.

## What Agent Eval Flow and Toolscore each did

- **Toolscore**: its scorecard pointed at the reference servers; its `MCPStdioClient` drove every matrix cell and
  every agent tool call; its metrics score each recorded tool trace (`tools.html`).
- **Agent Eval Flow**: builds as candidates over the same units, repeated runs per unit, a `main`→fix contrast,
  complete-population summaries, and an evidence pointer from every number into the capture. It refused to sum a
  metric with not-applicable cells, which forced an explicit silent-loss definition, and its per-build summaries
  exposed a bug in the first version of the finding-1 importer (every build graded on the published build's
  cells). `tests/unit/test_mcp_memory_example.py` guards both importers against the independent grading.

## Limits

- Synthetic seed files and scenarios. The kinds of unreadable line come from real reports, but how many users
  have them is unknown; the agent experiment uses 12 scenarios, three OpenAI models and one prompt.
- A report cannot force an agent to act on it (one run ignored the notice).
- `main` may change before release; everything is pinned to `f46d957`.
- Spend for the agent runs: about $0.46 at list prices (Addendum B's recorded cost used an older, higher output
  price for gpt-5.6-luna).

## Files

- `capture.json`: finding 1, 48 cells (tool calls, responses, file before and after).
- `agent_capture.json`: finding 2, 312 agent runs (tool calls, replies, token counts, final memory file).
- `PROTOCOL.md`, `DECISIONS.md`, `RUNLOG.md`: pre-registration, every decision with a before/after-data label,
  and the run log. Local paths are removed.
- All content is synthetic. Upstream code is MIT-licensed (modelcontextprotocol/servers); none is copied here.
