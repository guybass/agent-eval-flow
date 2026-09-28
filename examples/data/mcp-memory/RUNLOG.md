# Run log

| Time (UTC) | Command | Outcome |
| --- | --- | --- |
| 2026-09-28T07:06:17Z | toolscore mcp test "npx -y @modelcontextprotocol/server-everything@2026.8.31" | exit 0 |
| 2026-09-28T07:06:19Z | toolscore mcp test "npx -y @modelcontextprotocol/server-sequential-thinking@2026.8.31" | exit 1 |
| 2026-09-28T07:09:07Z | install Toolscore from fix/mcp-union-types ce711a4 (1.9.0 + union-type/seed fix) | 1.9.0 crashed on sequential-thinking: 'unhashable type: list' (JSON-Schema union type); fixed first, all servers re-run with this build |
| 2026-09-28T07:10:14Z | everything: .venv/bin/toolscore mcp test npx -y @modelcontextprotocol/server-everything@2026.8.31 --report json -o reports/everything.json | exit 0 |
| 2026-09-28T07:10:15Z | sequential-thinking: .venv/bin/toolscore mcp test npx -y @modelcontextprotocol/server-sequential-thinking@2026.8.31 --report json -o reports/sequential-thinking.json | exit 0 |
| 2026-09-28T07:10:19Z | memory: MEMORY_FILE_PATH=sandbox/memory.jsonl .venv/bin/toolscore mcp test npx -y @modelcontextprotocol/server-memory@2026.8.31 --report json -o reports/memory.json | exit 0 |
| 2026-09-28T07:10:21Z | filesystem: .venv/bin/toolscore mcp test npx -y @modelcontextprotocol/server-filesystem@2026.8.31 sandbox/fs --report json -o reports/filesystem.json | exit 0 |
| 2026-09-28T07:10:24Z | time: .venv/bin/toolscore mcp test uvx mcp-server-time==2026.8.18 --report json -o reports/time.json | exit 0 |
| 2026-09-28T07:10:26Z | git: .venv/bin/toolscore mcp test uvx mcp-server-git==2026.8.18 --repository sandbox/repo --report json -o reports/git.json | exit 0 |
| 2026-09-28T07:18:57Z | manual verification of all scorecard failures; upstream search | see FINDINGS.md: S1-S2 real but already reported (#4643, #4674, #4178); T3-T6 Toolscore false positives |
| 2026-09-28T07:21:10Z | v2 (toolscore 9cf9911) everything | exit 0 |
| 2026-09-28T07:21:11Z | v2 (toolscore 9cf9911) sequential-thinking | exit 0 |
| 2026-09-28T07:21:12Z | v2 (toolscore 9cf9911) memory | exit 0 |
| 2026-09-28T07:21:14Z | v2 (toolscore 9cf9911) filesystem | exit 0 |
| 2026-09-28T07:21:15Z | v2 (toolscore 9cf9911) time | exit 0 |
| 2026-09-28T07:21:17Z | v2 (toolscore 9cf9911) git | exit 0 |
| 2026-09-28T15:59:22Z | memory: reproduce data loss on main build; fix + test on branch fix/memory-keep-unreadable-lines | reproduced; fixed; 88/88 |
| 2026-09-28 | Addendum A pre-registered: fetch-server extraction loss on 71 SimpleQA questions | Evidence-loss analogue of Guy's method on an official MCP server | before any fetch run |
| 2026-09-28T16:11:22Z | fetch_loss.py run with research-agents-study/.venv (+ Toolscore fix/mcp-union-types 40ca160) so stage_a matching is shared | started |
| 2026-09-28T16:18:52Z | Addendum A result: fetch default extraction KEPT 70/70 answers (LOST 0, 95% upper 5.2%); 1 ABSENT. Hypothesis not supported | done |
| 2026-09-28T16:23:02Z | memory_matrix.py: 3 builds x 8 scenarios x 2 writes | running |
| 2026-09-28T16:47:29Z | Addendum B pre-registered; data/agent_scenarios.json fixed (10 UPDATE + 2 NEW) | before any agent run |
| 2026-09-28T16:48:01Z | agent smoke: --runs 1 --only u01 (main, c1) | running |
| 2026-09-28T16:48:37Z | agent smoke ok ($0.003); full run --runs 5 | running |
| 2026-09-28T17:06:24Z | agent full run finished | exit 0 |
| 2026-09-28T17:08:51Z | Addendum C pre-registered (gpt-4.1-mini x5, gpt-5.4-mini x3) | before replication |
| 2026-09-28T17:25:30Z | Addendum C replications finished | 4.1-mini 120 runs, 5.4-mini 72 runs |
