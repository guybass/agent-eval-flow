"""Offline: per question, the grade, where the gold answer went (Agent Eval Flow
evidence trace) and which tool path the agent took (Toolscore view).

python -m examples.deerflow_study.report ~/deerflow-study-data/results/stage1.json [...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .detector import trace_gold
from .grade import grade
from .toolscore_view import evidence_tool, toolscore_metrics


def build_table(results: list[dict], gold: dict) -> list[dict]:
    table = []
    for r in results:
        if r["status"] != "ok":
            table.append({"id": r["id"], "status": r["status"]})
            continue
        q = gold[r["id"]]
        rows = [json.loads(line) for line in Path(r["trace"]).read_text(encoding="utf-8").splitlines()]
        row = {"id": r["id"], "status": "ok", "gold": q["answer"], "answer": (r["answer"] or "")[:120],
               "grade": grade(r["answer"], q["answer"], q["question"]),
               **trace_gold(rows, q["answer"], q["question"], r["coverage"]),
               "evidence_tool": evidence_tool(rows, q["answer"], q["question"])}
        try:
            row.update(toolscore_metrics(rows))
        except ImportError:
            row["toolscore"] = "not installed"
        table.append(row)
    return table


def main(paths):
    from .questions import select_questions
    from .run_pilot import CSV_SHA256, DATA, N, SEED
    gold = {q["id"]: q for q in select_questions(DATA / "config" / "simpleqa.csv", SEED, N, CSV_SHA256)}
    results = [r for p in paths for r in json.loads(Path(p).read_text(encoding="utf-8"))]
    print(json.dumps(build_table(results, gold), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])
