"""Where did the gold answer last appear before a wrong final answer?

Stages, in capture order: tool results, sub-agent events, compaction summaries
(retrieval side) and lead-agent model requests. The user question and system
prompt are never evidence. A stage with unknown capture coverage is never a
loss point.
"""
from __future__ import annotations

import re
import unicodedata

RETRIEVAL_KINDS = {"tool_result", "subagent_event", "state_summary"}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"(?<=\d)[,\s](?=\d{3}\b)", "", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december"]
_DATE = re.compile(r"^(?:(?P<m1>[a-z]+) (?P<d1>\d{1,2})|(?P<d2>\d{1,2}) (?P<m2>[a-z]+)) (?P<y>\d{4})$")


def _date_forms(base: str) -> set[str]:
    """Equivalent spellings of a full calendar date derived from the gold string itself."""
    m = _DATE.match(base)
    month = m and (m.group("m1") or m.group("m2"))
    if not m or month not in MONTHS:
        return set()
    day, year = int(m.group("d1") or m.group("d2")), m.group("y")
    num = MONTHS.index(month) + 1
    return {f"{month} {day} {year}", f"{day} {month} {year}", f"{year} {num:02d} {day:02d}"}


def aliases(gold: str) -> set[str]:
    base = normalize(gold)
    out = {base} | _date_forms(base)
    words = base.split()
    if not _date_forms(base) and len(words) > 1 and words[-1].isalpha() and len(words[-1]) >= 4:
        out.add(words[-1])          # surname for person names
    return {a for a in out if a}


def _contains(text: str, names: set[str]) -> bool:
    padded = f" {normalize(text)} "
    return any(f" {a} " in padded for a in names)


def _request_text(row: dict, question: str) -> str:
    q = normalize(question)
    parts = [m.get("text", "") for m in row.get("messages", [])
             if m.get("type") != "system" and not normalize(m.get("text", "")).startswith(q)]
    return "\n".join(parts)


def trace_gold(rows: list[dict], gold: str, question: str, coverage: dict) -> dict:
    names = aliases(gold)
    seen = []
    for row in rows:
        if row["kind"] in RETRIEVAL_KINDS:
            text = row.get("content") or ""
        elif row["kind"] == "model_request":
            text = _request_text(row, question)
        else:
            continue
        if _contains(text, names):
            seen.append({"seq": row["seq"], "kind": row["kind"]})
    requests = [r for r in rows if r["kind"] == "model_request"]
    final = next((r for r in reversed(rows) if r["kind"] == "final_answer"), None)
    unknown = []
    if not coverage.get("lead_model_requests") or not requests:
        unknown.append("final_model_request")
        in_final = None
    else:
        in_final = _contains(_request_text(requests[-1], question), names)
    answer_ok = bool(final) and _contains(final.get("content", ""), names)
    retrieved = bool(seen)
    loss = retrieved and not answer_ok and in_final is False
    return {"retrieved": retrieved, "first_seen": seen[0] if seen else None,
            "last_seen": seen[-1] if seen else None, "in_final_request": in_final,
            "final_answer_contains": answer_ok, "loss": loss,
            "loss_after": seen[-1] if loss else None, "unknown_stages": unknown}
