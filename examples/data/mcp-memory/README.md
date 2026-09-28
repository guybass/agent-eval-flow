# MCP memory server: one unrelated write deletes memories the server cannot read

The official knowledge-graph memory server (`modelcontextprotocol/servers`, `src/memory`) stores memories as
JSON lines. On unreleased `main`, a line the server cannot read (for example an entity with one `null`
observation) is skipped on load and then **deleted from disk by the next unrelated write**, while every tool
call reports success. An entity with one bad field loses all of its valid observations.

```sh
python examples/mcp_memory_review.py --output demo-output/mcp-memory
```

Open `demo-output/mcp-memory/report.html` (outcomes per build) and `tools.html` (Toolscore view of each MCP
tool trace). The script imports the recorded matrix below; it makes no MCP, model or network calls.

## How it was found

1. `toolscore mcp test` against the official reference servers flagged failing grades. Checking each by hand
   showed most were Toolscore false positives (fixed in Toolscore, PR yotambraun/Toolscore#3) and two real
   filesystem bugs that others had already reported.
2. Reading what changed on `main` since the last release (2026.8.31) turned up the interaction: #4717
   (merged 2026-09-03) makes `loadGraph()` skip lines that fail validation, and every mutation still rewrites
   the whole file from the in-memory graph in `saveGraph()`.
3. The mechanism was reproduced over MCP stdio, then scoped with the matrix below. The scenarios were designed
   **after** the bug was found, to bound it; they are not a prevalence estimate.

## The matrix

Three builds: the published npm package `@modelcontextprotocol/server-memory@2026.8.31`, `main` at `f46d957`,
and the fix branch at `0ef28b0`. Eight scenarios: a clean file, six kinds of unreadable line taken from real
reports (#2044, #4717, #1481, #1819, #3173), and a valid entity with an extra field. Two unrelated writes
(`create_entities`, `add_observations`). 48 cells, each run in a fresh sandbox file, driven by Toolscore's
`MCPStdioClient`. `examples/mcp_memory_study/memory_matrix.py` re-records it; a re-run matched all 48 cells
byte for byte, including the resulting files.

| Build | Runs with silent data loss | Facts lost | Unrelated write applied | `read_graph` works |
| --- | --- | --- | --- | --- |
| published 2026.8.31 | 4 / 16 | 4 | 12 / 16 | 6 / 16 |
| **main f46d957** | **14 / 16** | **22** | 16 / 16 | 16 / 16 |
| fix 0ef28b0 | 2 / 16 | 2 | 16 / 16 | 16 / 16 |

"Silent data loss" = a fact from the seeded line is missing from the file after the write, and the write
reported success.

Per kind of line:

| Seeded line (source) | published | main | fix |
| --- | --- | --- | --- |
| `null` observation (#2044) | kept; `read_graph` errors | **deleted** | kept |
| missing `entityType` (#4717) | kept; `read_graph` errors | **deleted** | kept |
| truncated line (#1481) | server unusable: every call errors | **deleted** | kept, server works |
| two objects on one line (#1819, #3173) | server unusable | **deleted** | kept, server works |
| relation missing `relationType` (#4717) | kept | **deleted** | kept |
| unknown record type | deleted (already released) | deleted | kept |
| extra field on a valid entity | field dropped | field dropped | field dropped (not addressed) |

In short: #4717 turned an outage (the published server fails loudly on these files) into silent data loss.
The fix keeps #4717's availability and writes unreadable lines back unchanged.

## What Agent Eval Flow and Toolscore each did

- **Toolscore** drove every scenario over MCP (`MCPStdioClient`) and scores each recorded tool trace
  (`tools.html`). Its scorecard sweep is what pointed at the reference servers in the first place.
- **Agent Eval Flow** holds the three builds as candidates over the same 16 units, grades each run from the
  recorded file with evidence pointers into `capture.json`, and reports complete-population summaries. It
  refused to sum a metric with not-applicable cells, which made the silent-loss definition explicit, and the
  per-build summaries exposed a bug in this example's first version (every build graded on the published
  build's cells); `tests/unit/test_mcp_memory_example.py` now guards that.

## Limits

- Synthetic seed files; real memory files with such lines exist (the linked issues), but how many users have
  them is unknown.
- The fix keeps unreadable lines; it does not repair them, and the tools still cannot see or delete them.
- Extra fields on valid entities are dropped by every build; that is a separate, pre-existing behaviour.
- `main` may change before the next release; the finding is pinned to `f46d957`.

## Files

- `capture.json`: the 48 recorded cells (tool calls, responses, file before and after). All content is
  synthetic; no local paths.
- Upstream code is MIT-licensed (modelcontextprotocol/servers); none of it is copied here.
