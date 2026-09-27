"""Run pre-registered questions through DeerFlow with capture and a budget stop.

python -m examples.deerflow_study.run_pilot --stage 1 --price-in 0.20 --price-out 1.20
python -m examples.deerflow_study.run_pilot --stage 2 --price-in 0.20 --price-out 1.20 --spent <real $>

Run from the agent-eval-flow checkout with the pinned DeerFlow backend on PYTHONPATH,
inside DeerFlow's environment (see examples/data/deerflow/PROTOCOL.md). The OpenAI key
is read from <repo>/.env (git-ignored) and never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from pathlib import Path

from .capture import CaptureMiddleware, TraceWriter, record_stream_event

REPO = Path(__file__).resolve().parents[2]
DATA = Path.home() / "deerflow-study-data"
CSV_SHA256 = "feee3f7e7db3617e94e8fcf1977b756ec420ef8568f4e0fcbbe0e92e9d5fc032"
SEED, N = 20260928, 15
LIMIT_USD = 4.00


def _event_parts(event):
    etype = event.get("type") if isinstance(event, dict) else getattr(event, "type", "")
    data = (event.get("data") if isinstance(event, dict) else getattr(event, "data", None)) or {}
    return etype, data


def run_one(client_factory, question: dict, out_dir: Path) -> dict:
    run_id = f"q{question['id']}-{uuid.uuid4().hex[:8]}"
    writer = TraceWriter(Path(out_dir) / "raw" / f"{run_id}.jsonl")
    row = {"id": question["id"], "run_id": run_id, "status": "ok", "answer": None,
           "usage": {"input_tokens": 0, "output_tokens": 0}, "trace": str(writer.path), "error": None}
    started = time.time()
    texts, order = {}, []
    try:
        client = client_factory(writer)
        for event in client.stream(question["question"] + "\nAnswer concisely.", thread_id=run_id):
            record_stream_event(writer, event)
            etype, data = _event_parts(event)
            if etype == "messages-tuple" and data.get("type") == "ai" and data.get("content"):
                key = data.get("id") or f"anon-{len(order)}"
                if key not in texts:
                    order.append(key)
                    texts[key] = ""
                texts[key] += str(data["content"])
            elif etype == "end":
                usage = data.get("usage") or {}
                for k in ("input_tokens", "output_tokens"):
                    row["usage"][k] = int(usage.get(k) or 0)
        row["answer"] = texts[order[-1]] if order else ""
        writer.record("final_answer", content=row["answer"])
    except Exception as exc:  # recorded, never scored
        row.update(status="error", error=f"{type(exc).__name__}: {exc}")
        writer.record("error", message=row["error"])
    finally:
        row["coverage"] = dict(writer.coverage)
        row["elapsed_s"] = round(time.time() - started, 1)
        writer.close()
    return row


def run_batch(questions, client_factory, out_dir, spent_usd, limit_usd, cost_fn):
    rows = []
    for question in questions:
        if spent_usd >= limit_usd:
            break
        row = run_one(client_factory, question, Path(out_dir))
        row["cost_usd_est"] = cost_fn(row["usage"])
        spent_usd += row["cost_usd_est"]
        rows.append(row)
    return rows


def _load_env(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit(f"OPENAI_API_KEY is empty: paste your key into {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=int, choices=(1, 2), required=True)
    parser.add_argument("--price-in", type=float, required=True, help="$ per 1M input tokens (DECISIONS.md)")
    parser.add_argument("--price-out", type=float, required=True, help="$ per 1M output tokens (DECISIONS.md)")
    parser.add_argument("--spent", type=float, default=0.0, help="spend so far, from the OpenAI usage page")
    args = parser.parse_args()
    _load_env(REPO / ".env")
    config = str(DATA / "config" / "config.yaml")
    os.environ["DEER_FLOW_CONFIG_PATH"] = config

    from deerflow.client import DeerFlowClient
    from .questions import select_questions

    questions = select_questions(DATA / "config" / "simpleqa.csv", SEED, N, CSV_SHA256)
    questions = questions[:5] if args.stage == 1 else questions[5:]

    def factory(writer):
        return DeerFlowClient(config_path=config, thinking_enabled=False, subagent_enabled=True,
                              plan_mode=False, middlewares=[CaptureMiddleware(writer)])

    def cost(usage):
        return usage["input_tokens"] / 1e6 * args.price_in + usage["output_tokens"] / 1e6 * args.price_out

    rows = run_batch(questions, factory, DATA, args.spent, LIMIT_USD, cost)
    out = DATA / "results" / f"stage{args.stage}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps([{k: r.get(k) for k in ("id", "status", "answer", "cost_usd_est", "elapsed_s")}
                      for r in rows], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
