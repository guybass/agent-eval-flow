"""Addendum B (PROTOCOL.md): a real agent using the memory server.

    python agent_memory_eval.py --runs 5 [--only u01] [--servers main,c1]

The agent (gpt-5.6-luna, OpenAI Responses API) gets the server's own tools/list as
function tools and the README's example memory prompt, verbatim. Each run starts
from the same seeded memory file. Every tool call and the final file are recorded
to data/agent_runs.jsonl. Spend is checked before every model call.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path

from openai import OpenAI
from toolscore.mcp import MCPStdioClient

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "agent_runs.jsonl"  # gpt-5.6-luna (Addendum B); other models get their own file
SANDBOX = ROOT / "sandbox" / "agent"
# Built servers: set MEMORY_MAIN_JS / MEMORY_C1_JS to each build's src/memory/dist/index.js.
SERVERS = {
    "main": ["node", os.environ.get("MEMORY_MAIN_JS", str(ROOT / "builds/main/src/memory/dist/index.js"))],
    "c1": ["node", os.environ.get("MEMORY_C1_JS", str(ROOT / "builds/c1/src/memory/dist/index.js"))],
}
MODEL = "gpt-5.6-luna"
PRICE_IN, PRICE_OUT = 0.20 / 1e6, 1.20 / 1e6  # Addendum B used the older recorded output price
BUDGET_USD = 1.00
REPLICATION = {  # Addendum C: model -> (input $/1M, output $/1M, cap $)
    "gpt-4.1-mini": (0.40, 1.60, 0.60),
    "gpt-5.4-mini": (0.75, 4.50, 0.90),
}
MAX_CALLS = 8

for line in Path(os.environ.get("ENV_FILE", ".env")).read_text().splitlines():
    if line.startswith("OPENAI_API_KEY="):  # never printed
        os.environ["OPENAI_API_KEY"] = line.split("=", 1)[1].strip().strip('"')


def readme_prompt() -> str:
    readme = (Path(SERVERS["main"][1]).parents[1] / "README.md").read_text(encoding="utf-8")
    block = readme.split("### System Prompt", 1)[1]
    return re.search(r"```\n(.*?)```", block, re.S).group(1).strip()


def spent_so_far() -> float:
    if not OUT.exists():
        return 0.0
    return sum(json.loads(line)["cost_usd"] for line in OUT.open())


def seed_file(path: Path, seed: dict) -> None:
    lines = [json.dumps({"type": "entity", **e}) for e in seed["entities"]]
    lines += [json.dumps({"type": "relation", **r}) for r in seed["relations"]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def text_of(result) -> str:
    return "\n".join(c.get("text", "") for c in result.content if isinstance(c, dict))


def run_once(client: OpenAI, server: str, scenario: dict, seed: dict, rep: int, system: str) -> dict:
    memory = SANDBOX / server / f"{scenario['id']}-{rep}.jsonl"
    memory.parent.mkdir(parents=True, exist_ok=True)
    seed_file(memory, seed)
    calls, usage_in, usage_out, final_text, error = [], 0, 0, "", None
    started = time.time()
    with MCPStdioClient(SERVERS[server], env=dict(os.environ, MEMORY_FILE_PATH=str(memory)), timeout=60) as mcp:
        tools = [{"type": "function", "name": t.name, "description": t.description,
                  "parameters": t.input_schema, "strict": False} for t in mcp.list_tools()]
        pending, previous = [{"role": "user", "content": scenario["message"]}], None
        for _ in range(MAX_CALLS):
            if spent_so_far() + (usage_in * PRICE_IN + usage_out * PRICE_OUT) >= BUDGET_USD:
                error = "budget reached"
                break
            resp = client.responses.create(model=MODEL, instructions=system, input=pending, tools=tools,
                                           previous_response_id=previous)
            usage_in += resp.usage.input_tokens
            usage_out += resp.usage.output_tokens
            previous, pending = resp.id, []
            fcalls = [o for o in resp.output if o.type == "function_call"]
            if not fcalls:
                final_text = resp.output_text
                break
            for fc in fcalls:
                try:
                    args = json.loads(fc.arguments or "{}")
                except json.JSONDecodeError:
                    args = {"_unparsed": fc.arguments}
                r = mcp.call_tool(fc.name, args)
                out = text_of(r)
                calls.append({"tool": fc.name, "args": args, "is_error": r.is_error, "result": out[:2000]})
                pending.append({"type": "function_call_output", "call_id": fc.call_id, "output": out})
        else:
            error = error or "max model calls reached"
    return {
        "model": MODEL, "server": server, "scenario": scenario["id"], "rep": rep, "kind": scenario["kind"],
        "target": scenario["target"], "keywords": scenario["keywords"],
        "calls": calls, "final_text": final_text[:2000], "error": error,
        "input_tokens": usage_in, "output_tokens": usage_out,
        "cost_usd": usage_in * PRICE_IN + usage_out * PRICE_OUT,
        "seconds": round(time.time() - started, 1),
        "file_after": memory.read_text(encoding="utf-8"),
    }


def main() -> None:
    global MODEL, PRICE_IN, PRICE_OUT, BUDGET_USD, OUT
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--only")
    parser.add_argument("--servers", default="main,c1")
    parser.add_argument("--model", default=MODEL)
    args = parser.parse_args()
    if args.model != MODEL:
        pin, pout, cap = REPLICATION[args.model]
        MODEL, PRICE_IN, PRICE_OUT, BUDGET_USD = args.model, pin / 1e6, pout / 1e6, cap
        OUT = ROOT / "data" / f"agent_runs_{args.model}.jsonl"
    spec = json.loads((ROOT / "agent_scenarios.json").read_text(encoding="utf-8"))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    system = readme_prompt()
    client = OpenAI(max_retries=4, timeout=120)
    done = set()
    if OUT.exists():
        done = {(r["server"], r["scenario"], r["rep"]) for r in map(json.loads, OUT.open())}
    for rep in range(args.runs):  # interleave servers and scenarios so a stop leaves a balanced sample
        for scenario in spec["scenarios"]:
            if args.only and scenario["id"] != args.only:
                continue
            for server in args.servers.split(","):
                if (server, scenario["id"], rep) in done:
                    continue
                if spent_so_far() >= BUDGET_USD:
                    print("budget reached; stopping", flush=True)
                    return
                row = run_once(client, server, scenario, spec["seed"], rep, system)
                with OUT.open("a") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(f"{server:5} {scenario['id']} r{rep} calls={[c['tool'] for c in row['calls']]} "
                      f"${row['cost_usd']:.4f} total ${spent_so_far():.3f} {row['error'] or ''}", flush=True)


if __name__ == "__main__":
    main()
