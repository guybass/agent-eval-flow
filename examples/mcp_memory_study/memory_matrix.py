"""Record the MCP memory scope matrix (examples/data/mcp-memory/capture.json).

    python examples/mcp_memory_study/memory_matrix.py --main PATH/TO/main/src/memory/dist/index.js \
        --fix PATH/TO/fix/src/memory/dist/index.js --out capture-raw.json

Needs Node (npx) and Toolscore with MCP support. The published build is fetched with npx.
Which unreadable lines does each build delete?

Builds: the published npm package (2026.8.31), main (f46d957), and the fix
branch (0ef28b0). Each scenario seeds a memory file with one valid entity plus
one kind of unreadable line seen in real reports, then performs one unrelated
write through MCP (Toolscore's stdio client) and inspects the file.
Everything runs in a throwaway sandbox; no network except npx/uvx installs.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from toolscore.mcp import MCPStdioClient

import argparse
import tempfile

SANDBOX = Path(tempfile.mkdtemp(prefix="mcp-memory-matrix-"))
BUILDS: dict[str, list[str]] = {}

ALICE = json.dumps({"type": "entity", "name": "Alice", "entityType": "person",
                    "observations": ["Works at Acme", "Prefers email"]}, separators=(",", ":"))

# Each scenario: one unreadable (or unusual) line, the facts it carries, and where such lines come from.
SCENARIOS = [
    {"id": "clean", "line": None, "facts": [], "origin": "control: no unreadable line"},
    {"id": "null_observation",
     "line": '{"type":"entity","name":"Bob","entityType":"person","observations":["Allergic to penicillin","Lives in Haifa",null]}',
     "facts": ["Allergic to penicillin", "Lives in Haifa"],
     "origin": "#2044 (Claude Desktop / Claude Code users with undefined fields); #4717 names null observations"},
    {"id": "missing_entity_type",
     "line": '{"type":"entity","name":"Project X","observations":["Deadline 2026-10-01","Budget 40k"]}',
     "facts": ["Deadline 2026-10-01", "Budget 40k"],
     "origin": "#4717 names entities missing entityType as legacy entries"},
    {"id": "truncated_line",
     "line": '{"type":"entity","name":"Dana","entityType":"person","observations":["Speaks Hebrew","Birthday 3 May"',
     "facts": ["Speaks Hebrew", "Birthday 3 May"],
     "origin": "writes interrupted before atomic saves (#4642); #1481"},
    {"id": "two_objects_one_line",
     "line": '{"type":"entity","name":"Eli","entityType":"person","observations":["Owns a cat"]}{"type":"entity","name":"Fay","entityType":"person","observations":["Plays chess"]}',
     "facts": ["Owns a cat", "Plays chess"],
     "origin": "interleaved concurrent writes (#1819); parse error reported in #3173"},
    {"id": "unknown_record_type",
     "line": '{"type":"note","text":"Quarterly review every Friday"}',
     "facts": ["Quarterly review every Friday"],
     "origin": "records written by other tools or future versions"},
    {"id": "relation_missing_type",
     "line": '{"type":"relation","from":"Alice","to":"Alice"}',
     "facts": ['"from":"Alice","to":"Alice"'],
     "origin": "#4717 names relations missing relationType"},
    {"id": "extra_field",
     "line": '{"type":"entity","name":"Gil","entityType":"person","observations":["Vegetarian"],"createdAt":"2026-01-01"}',
     "facts": ["Vegetarian", "createdAt"],
     "origin": "valid entity with an extra field (out of scope for the fix; checked for completeness)"},
]

WRITES = {
    "create_entities": {"entities": [{"name": "Carol", "entityType": "person", "observations": ["New hire"]}]},
    "add_observations": {"observations": [{"entityName": "Alice", "contents": ["Moved to Berlin"]}]},
}


def text_of(result) -> str:
    return "".join(c.get("text", "") for c in result.content if isinstance(c, dict))


def run_cell(build: str, command: list[str], scenario: dict, write: str) -> dict:
    cell_dir = SANDBOX / build / scenario["id"] / write
    shutil.rmtree(cell_dir, ignore_errors=True)
    cell_dir.mkdir(parents=True)
    memory = cell_dir / "memory.jsonl"
    before_lines = [ALICE] + ([scenario["line"]] if scenario["line"] else [])
    memory.write_text("\n".join(before_lines) + "\n", encoding="utf-8")

    calls = []
    started = time.time()
    with MCPStdioClient(command, env=dict(os.environ, MEMORY_FILE_PATH=str(memory)), timeout=60) as client:
        for tool, args in (("read_graph", {}), (write, WRITES[write])):
            r = client.call_tool(tool, args)
            structured = (r.raw.get("result") or {}).get("structuredContent")
            calls.append({"tool": tool, "args": args, "is_error": r.is_error,
                          "text": text_of(r)[:400], "structured": structured})
    after = memory.read_text(encoding="utf-8")

    graph = calls[0]["structured"] or {}
    seen = [e["name"] for e in graph.get("entities", [])] if isinstance(graph, dict) else []
    facts_lost = [f for f in scenario["facts"] if f not in after]
    return {
        "build": build, "scenario": scenario["id"], "write": write, "origin": scenario["origin"],
        "seconds": round(time.time() - started, 2),
        "read_graph": "error" if calls[0]["is_error"] else "ok",
        "entities_seen_by_agent": seen,
        "write_reported_error": calls[1]["is_error"],
        "unreadable_line_kept": (scenario["line"] in after.splitlines()) if scenario["line"] else None,
        "facts_total": len(scenario["facts"]),
        "facts_lost": facts_lost,
        "valid_data_kept": "Works at Acme" in after,
        "write_applied": ("Carol" in after) if write == "create_entities" else ("Moved to Berlin" in after),
        "calls": calls,
        "file_before": "\n".join(before_lines) + "\n",
        "file_after": after,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--main", required=True, help="dist/index.js built from main at f46d957")
    parser.add_argument("--fix", required=True, help="dist/index.js built from the fix branch at 0ef28b0")
    parser.add_argument("--out", type=Path, default=Path("capture-raw.json"))
    args = parser.parse_args()
    global OUT
    OUT = args.out
    BUILDS.update({
        "published-2026.8.31": ["npx", "-y", "@modelcontextprotocol/server-memory@2026.8.31"],
        "main-f46d957": ["node", args.main],
        "fix-0ef28b0": ["node", args.fix],
    })
    cells = [run_cell(b, cmd, s, w) for b, cmd in BUILDS.items() for s in SCENARIOS for w in WRITES]
    OUT.write_text(json.dumps({"builds": BUILDS, "scenarios": SCENARIOS, "writes": WRITES, "cells": cells},
                              indent=1, ensure_ascii=False))
    for c in cells:
        print(f"{c['build']:20} {c['scenario']:22} {c['write']:17} read={c['read_graph']:5} "
              f"write_err={c['write_reported_error']!s:5} applied={c['write_applied']!s:5} "
              f"kept_line={c['unreadable_line_kept']!s:5} lost={len(c['facts_lost'])}/{c['facts_total']}")


if __name__ == "__main__":
    main()
