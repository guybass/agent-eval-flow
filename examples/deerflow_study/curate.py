"""Curate raw DeerFlow study traces into a publishable projection.

Kept: questions and gold answers (public datasets), short answers, statuses,
turn counts, token usage, tool names and arguments (queries and URLs), per
tool-result character counts and whether the gold answer appeared, detector
and Toolscore observations, and the SHA-256 of each raw trace.
Omitted: page bodies, prompts, system text, full reports and local paths.

python -m examples.deerflow_study.curate   # writes examples/data/deerflow/capture.json
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from .detector import aliases, normalize, trace_gold
from .grade import grade
from .toolscore_view import evidence_tool, tool_calls, toolscore_metrics

ANSWER_CHARS = 200


def _gold_present(text: str, gold: str, question: str) -> bool:
    padded = f" {normalize(text)} "
    return any(f" {a} " in padded for a in aliases(gold, question))


def curate_run(result: dict, question: dict, *, experiment: str, recursion_limit: int,
               manual_review: dict | None = None) -> dict:
    raw = Path(result["trace"]).read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
    turns = sum(1 for r in rows if r["kind"] == "model_request")
    ok = result["status"] == "ok"
    # Approximate: the trace file's last write, minus the runner's elapsed time.
    ended = datetime.fromtimestamp(Path(result["trace"]).stat().st_mtime, timezone.utc)
    started = ended - timedelta(seconds=float(result.get("elapsed_s") or 0))
    row = {
        "case_id": f"{experiment}-{result['id']}", "experiment": experiment, "row_index": result["id"],
        "query": question["question"], "expected_answer": question["answer"],
        "recursion_limit": recursion_limit, "status": result["status"],
        "error": (result.get("error") or "")[:200] or None,
        "answer": (result.get("answer") or "")[:ANSWER_CHARS] if ok else None,
        "grade": grade(result["answer"], question["answer"], question["question"]) if ok else None,
        "turns": turns, "elapsed_seconds": result.get("elapsed_s"),
        "started_at": started.isoformat(), "ended_at": ended.isoformat(), "timing": "approximate",
        "usage": result.get("usage"), "usage_complete": ok,
        "tool_calls": tool_calls(rows),
        "tool_results": [{"tool_call_id": r.get("tool_call_id"), "tool": r.get("tool"),
                          "chars": len(r.get("content") or ""),
                          "gold_present": _gold_present(r.get("content") or "", question["answer"], question["question"])}
                         for r in rows if r["kind"] == "tool_result"],
        "subagent_events": sum(1 for r in rows if r["kind"] == "subagent_event"),
        "compaction_summaries": sum(1 for r in rows if r["kind"] == "state_summary"),
        "raw_trace_sha256": hashlib.sha256(raw).hexdigest(),
    }
    if ok:
        row["grade_basis"] = "automatic"
        if manual_review:
            row.update(grade_automatic=row["grade"], grade=manual_review["grade"], grade_basis="manual review",
                       manual_review_reason=manual_review["reason"])
        detected = trace_gold(rows, question["answer"], question["question"], result.get("coverage") or {})
        row["evidence"] = {k: detected[k] for k in ("retrieved", "in_final_request", "loss")}
        row["evidence"]["first_tool"] = evidence_tool(rows, question["answer"], question["question"])
    try:
        metrics = toolscore_metrics(rows)
        row["toolscore"] = {k: metrics[k] for k in ("required_call_recall", "redundant_rate", "tool_calls",
                                                    "searches", "fetches", "subagent_tasks", "identical_repeats")}
    except ImportError:
        row["toolscore"] = None
    return row


def main():
    from .questions import select_questions
    from .run_pilot import (CSV_SHA256, DATA, FRAMES_MAX_ANSWER, FRAMES_N, FRAMES_RECURSION_LIMIT,
                            FRAMES_SEED, FRAMES_SHA256, N, SEED)
    simpleqa = {q["id"]: q for q in select_questions(DATA / "config/simpleqa.csv", SEED, N, CSV_SHA256)}
    frames = {q["id"]: q for q in select_questions(DATA / "config/frames_test.tsv", FRAMES_SEED, FRAMES_N,
                                                    FRAMES_SHA256, question_col="Prompt", answer_col="Answer",
                                                    delimiter="\t", max_answer_chars=FRAMES_MAX_ANSWER)}
    reviews = json.loads((Path(__file__).resolve().parents[1] / "data" / "deerflow" / "MANUAL_REVIEW.json")
                         .read_text(encoding="utf-8"))["reviews"]
    runs = []
    for name, gold, limit, experiment in (("stage1", simpleqa, 100, "simpleqa"),
                                          ("stage2", simpleqa, 100, "simpleqa"),
                                          ("followup_rl300", simpleqa, 300, "simpleqa-followup"),
                                          ("frames", frames, FRAMES_RECURSION_LIMIT, "frames")):
        for result in json.loads((DATA / "results" / f"{name}.json").read_text(encoding="utf-8")):
            case_id = f"{experiment}-{result['id']}"
            runs.append(curate_run(result, gold[result["id"]], experiment=experiment, recursion_limit=limit,
                                   manual_review=reviews.get(case_id)))
    capture = {
        "schema": "agent-eval-flow.deerflow-case-study/1",
        "upstream": {"repository": "bytedance/deer-flow", "commit": "827acf51dc4a713d99632f0319a62ee487598c77",
                     "entry_point": "deerflow.client.DeerFlowClient (embedded, backend only)"},
        "configuration": {"model": "gpt-5.6-luna", "api": "OpenAI Responses (langchain_openai.ChatOpenAI)",
                          "web_search": "DDG", "web_fetch": "Jina", "subagent_enabled": True,
                          "thinking_enabled": False, "memory_enabled": False, "summarization_enabled": True},
        "lead_agent_graph": {"super_steps_per_turn": 14, "super_steps_per_invocation": 9,
                             "turns_within_recursion_limit_100": 6,
                             "shipped_config_subagents_off": {"super_steps_per_turn": 13, "turns": 7}},
        "datasets": {
            "simpleqa": {"file": "Simple QA Test Set.csv @ gpt-researcher 6f998577", "sha256": CSV_SHA256,
                         "seed": SEED, "n": N, "license": "MIT (openai/simple-evals)"},
            "frames": {"file": "google/frames-benchmark test.tsv", "sha256": FRAMES_SHA256, "seed": FRAMES_SEED,
                       "n": FRAMES_N, "max_answer_chars": FRAMES_MAX_ANSWER, "license": "Apache-2.0"}},
        "runs": runs,
    }
    out = Path(__file__).resolve().parents[1] / "data" / "deerflow" / "capture.json"
    out.write_text(json.dumps(capture, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out} with {len(runs)} runs")


if __name__ == "__main__":
    main()
