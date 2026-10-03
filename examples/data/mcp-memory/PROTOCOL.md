# MCP reference-server scorecard: protocol (written before any run, 2026-09-28)

## Question
Do the official MCP reference servers (modelcontextprotocol/servers, 90.6k stars) pass
Toolscore's deterministic health-check, and where they don't, is it a real defect a
maintainer would merge a fix for?

## Servers (pinned)
| Server | Package | Version | Launch |
| --- | --- | --- | --- |
| everything | npm @modelcontextprotocol/server-everything | 2026.8.31 | npx -y pkg@ver |
| memory | npm @modelcontextprotocol/server-memory | 2026.8.31 | MEMORY_FILE_PATH=sandbox/memory.jsonl |
| filesystem | npm @modelcontextprotocol/server-filesystem | 2026.8.31 | allowed dir = sandbox/fs only |
| sequential-thinking | npm @modelcontextprotocol/server-sequential-thinking | 2026.8.31 | |
| time | PyPI mcp-server-time | 2026.8.18 | uvx pkg==ver |
| git | PyPI mcp-server-git | 2026.8.18 | --repository sandbox/repo (throwaway repo) |
| fetch | PyPI mcp-server-fetch | 2026.8.18 | makes real HTTP GETs to generated URLs; run last |

Tool: Toolscore 1.9.0, `toolscore mcp test "<cmd>" --report json -o reports/<name>.json`
(defaults: 3 happy-path cases per tool, edge cases on).

## Safety
Only servers with no account credentials. Every writable resource is a throwaway path
under study root/sandbox. No server is pointed at a real repo or home dir.

## What counts as a finding
A scorecard issue is only a *finding* after manual confirmation:
1. reproduce it with a direct, minimal MCP call (not only via the scorecard);
2. read the server source at the pinned version and name the line;
3. rule out a Toolscore false positive (the generator or linter being wrong).
Toolscore false positives are recorded separately and become Toolscore fixes.

## Outputs
reports/*.json (raw), RUNLOG.md (every command, date, outcome), FINDINGS.md (confirmed only).

## Addendum A (2026-09-28, before any fetch-server run): does mcp-server-fetch's default extraction drop answers?
- Server: PyPI mcp-server-fetch==2026.8.18 via uvx, driven over MCP stdio by Toolscore's MCPStdioClient.
- Questions: the 71 REACHABLE SimpleQA questions from research-agents-study Stage A (gold string present in the
  page text scraped by GPT Researcher). Pages: each question's en.wikipedia.org reference URLs (deduplicated).
- Per page, two tool calls: default (`raw: false`, readability + markdown) and `raw: true` (control), both with
  `max_length: 999999` so pagination cannot cut content. Sequential, 1 s apart.
- Gold matching: the same aliases()/first_offset() as Stage A (accent/punctuation tolerant).
- Classes per question: KEPT (gold in the default output of some page), LOST (gold in the raw output of some page
  but in no default output), ABSENT (gold in no raw output), FETCH_FAIL.
- Primary: LOST / (KEPT + LOST), Wilson 95%. Every LOST case is then checked by hand: where the gold sits in
  the page (infobox, table, caption, body) and whether markdown conversion or readability removed it.

## Addendum B (2026-09-28, pre-registered before any agent run): does create_entities silently lose an agent's facts, and does reporting `skipped` fix it?

- Servers: memory `main` @ f46d957 (same create_entities behaviour as published 2026.8.31) vs `c1` @ 32a93d3
  (main + create_entities reports skipped entities). Both built from source, run over MCP stdio.
- Agent: gpt-5.6-luna via the OpenAI Responses API, function tools generated from the server's own `tools/list`
  (names, descriptions, input schemas unchanged). System prompt: the README's example memory prompt, verbatim.
  At most 8 model calls per run. Tool results are passed back as the text content the server returned.
- Seeded memory (identical for every run): default_user, Alice (colleague), Bob, Project Atlas, Acme Corp, Dana,
  with observations and relations.
- Scenarios (data/agent_scenarios.json, fixed now): 10 UPDATE (new facts about an existing entity, varied phrasing),
  2 NEW (facts about new people; control). 5 runs per scenario per server: 120 runs.
- Primary outcome: fact stored = every fact keyword appears in the observations of the target entity (name match,
  case-insensitive) at the end of the run. Rate per server over UPDATE runs, Wilson 95%.
- Secondary: keyword stored anywhere in the graph; silent loss = the agent sent the fact in create_entities for an
  existing entity and the fact is stored nowhere at the end; tool choice and identical repeats (Toolscore).
- Decision rule: the c1 change is supported only if UPDATE fact-stored rate is higher for c1 and NEW is not lower.
  If main already stores facts reliably, report that C1 has no measurable impact for this model.
- Budget: stop when estimated spend reaches $1.00 ($0.20 / $1.20 per 1M input / output tokens), checked before
  every model call.

## Addendum C (2026-09-28, after Addendum B results, before any replication run): cross-model replication
- Same scenarios, servers, prompt, harness and grader as Addendum B; only the model changes.
- gpt-4.1-mini ($0.40 / $1.60 per 1M): 5 runs per scenario per server (120 runs), cap $0.60.
- gpt-5.4-mini ($0.75 / $4.50 per 1M): 3 runs per scenario per server (72 runs), cap $0.90.
- Same decision rule per model. Prices read from developers.openai.com/api/docs/pricing on 2026-09-28
  (gpt-5.6-luna is now listed at $0.20 / $0.75, so Addendum B's recorded spend is an overestimate).
