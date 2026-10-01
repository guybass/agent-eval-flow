"""Toolscore view of a captured DeerFlow run: which tools, how often, against a contract.

The contract is name-level (search queries and URLs legitimately vary): a research
answer should come from at least one web_search and at least one fetched page.
Scored with the pinned tool-scorer package; arguments are not compared.
"""
from __future__ import annotations

from collections import Counter
import json

from .detector import aliases, normalize

CONTRACT = {"schema_version": "1", "id": "deerflow-research", "revision": "1",
            "calls": [{"tool": "web_search"}, {"tool": "web_fetch"}]}


def tool_calls(rows: list[dict]) -> list[dict]:
    """Tool requests in the order the model issued them."""
    return [call for row in rows if row["kind"] == "tool_call" for call in row.get("calls", ())]


def evidence_tool(rows: list[dict], gold: str, question: str = "") -> str | None:
    """The first stage that delivered the gold answer: a tool name, 'subagent' or 'summary'."""
    names = aliases(gold, question)
    for row in rows:
        text = f" {normalize(row.get('content') or '')} "
        if not any(f" {a} " in text for a in names):
            continue
        if row["kind"] == "tool_result":
            return row.get("tool") or "unknown_tool"
        if row["kind"] == "subagent_event":
            return "subagent"
        if row["kind"] == "state_summary":
            return "summary"
    return None


def toolscore_metrics(rows: list[dict]) -> dict:
    import toolscore

    actual = [{"tool": c["tool"], "args": c.get("args") or {}} for c in tool_calls(rows)]
    expected = [dict(call) for call in CONTRACT["calls"]]
    result = toolscore.evaluate(expected=expected, actual=actual, strict=False)
    counts = Counter(c["tool"] for c in actual)
    needed = Counter(c["tool"] for c in expected)
    return {"selection_accuracy": float(result.selection_accuracy),
            "required_call_recall": sum((needed & counts).values()) / len(expected),
            "redundant_rate": float(result.metrics["efficiency_metrics"]["redundant_rate"]),
            "tool_calls": len(actual), "searches": counts["web_search"], "fetches": counts["web_fetch"],
            "subagent_tasks": counts["task"], "toolscore_score": float(result.score),
            # Toolscore's redundant_rate counts calls beyond the contract's per-tool
            # expectation (names only). Identical (tool, args) repeats are the loop signal.
            "identical_repeats": len(actual) - len({(c["tool"], json.dumps(c["args"], sort_keys=True))
                                                    for c in actual})}
