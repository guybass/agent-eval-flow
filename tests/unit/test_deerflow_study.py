"""Offline checks for the DeerFlow evidence-loss study tooling (no model calls)."""
import csv
import hashlib

import pytest

from examples.deerflow_study.questions import select_questions


def _csv(tmp_path, rows):
    path = tmp_path / "q.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["metadata", "problem", "answer"])
        writer.writeheader()
        for question, answer in rows:
            writer.writerow({"metadata": "{}", "problem": question, "answer": answer})
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_selection_is_seeded_and_hash_checked(tmp_path):
    path, digest = _csv(tmp_path, [(f"Q{i}?", f"A{i}") for i in range(50)])
    first = select_questions(path, seed=20260928, n=5, expected_sha256=digest)
    again = select_questions(path, seed=20260928, n=5, expected_sha256=digest)
    assert first == again and len(first) == 5
    assert all(set(q) == {"id", "question", "answer"} for q in first)
    with pytest.raises(ValueError, match="sha256"):
        select_questions(path, seed=20260928, n=5, expected_sha256="0" * 64)


import json

from examples.deerflow_study.capture import TraceWriter, record_stream_event


def test_trace_writer_jsonl_and_order(tmp_path):
    w = TraceWriter(tmp_path / "run.jsonl")
    w.record("tool_result", tool="web_search", content="Paul Anastas won")
    w.record("final_answer", content="Chirik")
    w.close()
    rows = [json.loads(line) for line in (tmp_path / "run.jsonl").read_text().splitlines()]
    assert [r["kind"] for r in rows] == ["tool_result", "final_answer"]
    assert [r["seq"] for r in rows] == [0, 1]


class Ev:  # the shape of deerflow.client.StreamEvent
    def __init__(self, type, data):
        self.type, self.data = type, data


def test_coverage_flag_when_no_subagent_capture(tmp_path):
    w = TraceWriter(tmp_path / "run.jsonl")
    record_stream_event(w, Ev("custom", {"type": "task_running", "task_id": "t1", "message": {"content": "x"}}))
    w.close()
    assert w.coverage["subagent_model_requests"] is False   # only sub-agent messages, not model inputs
    assert w.coverage["subagent_events"] is True


def test_real_stream_shapes_are_classified(tmp_path):
    w = TraceWriter(tmp_path / "run.jsonl")
    record_stream_event(w, Ev("messages-tuple", {"type": "tool", "name": "web_search", "content": "Anastas page"}))
    record_stream_event(w, Ev("messages-tuple", {"type": "ai", "content": "", "tool_calls": [{"name": "task"}]}))
    record_stream_event(w, Ev("custom", {"type": "task_completed", "task_id": "t1", "result": "Anastas won"}))
    record_stream_event(w, Ev("values", {"messages": [], "summary_text": "Earlier: searched awards"}))
    record_stream_event(w, Ev("values", {"messages": [], "summary_text": None}))
    w.close()
    rows = [json.loads(line) for line in (tmp_path / "run.jsonl").read_text().splitlines()]
    assert [r["kind"] for r in rows] == ["tool_result", "tool_call", "subagent_event", "state_summary", "stream_event"]
    assert rows[0]["content"] == "Anastas page" and rows[0]["tool"] == "web_search"
    assert rows[2]["content"] == "Anastas won"
    assert rows[3]["content"] == "Earlier: searched awards"
    assert w.coverage["tool_results"] and w.coverage["subagent_events"]


from examples.deerflow_study.detector import aliases, normalize, trace_gold


def _rows(*items):
    return [{"seq": i, **item} for i, item in enumerate(items)]


def test_detects_loss_between_retrieval_and_final_request():
    r = _rows({"kind": "tool_result", "content": "Paul Anastas won the 2016 award"},
              {"kind": "model_request", "agent": "lead", "messages": [{"type": "human", "text": "Q"},
                                                                      {"type": "tool", "text": "Chirik EPA award"}]},
              {"kind": "final_answer", "content": "Chirik"})
    out = trace_gold(r, "Anastas", "Who won?", {"lead_model_requests": True})
    assert out["retrieved"] and out["loss"] and out["in_final_request"] is False
    assert out["loss_after"]["kind"] == "tool_result"


def test_alias_and_normalization():
    assert "anastas" in aliases("Paul Anastas") and "paul anastas" in aliases("Paul Anastas")
    assert "1000" in aliases("1,000")
    assert normalize("Vázquez  García!") == "vazquez garcia"


def test_question_stage_ignored():
    q = "Was it Anastas?"
    r = _rows({"kind": "model_request", "agent": "lead",
               "messages": [{"type": "human", "text": q + "\nAnswer concisely."}]},
              {"kind": "final_answer", "content": "No idea"})
    out = trace_gold(r, "Anastas", q, {"lead_model_requests": True})
    assert out["retrieved"] is False and out["loss"] is False


def test_unknown_coverage_is_not_loss():
    r = _rows({"kind": "tool_result", "content": "Anastas"}, {"kind": "final_answer", "content": "Chirik"})
    out = trace_gold(r, "Anastas", "Who?", {"lead_model_requests": False})
    assert out["loss"] is False and "final_model_request" in out["unknown_stages"]


def test_compaction_summary_is_a_stage():
    r = _rows({"kind": "state_summary", "content": "Found: Anastas (RSC)"},
              {"kind": "model_request", "agent": "lead", "messages": [{"type": "ai", "text": "Chirik"}]},
              {"kind": "final_answer", "content": "Chirik"})
    out = trace_gold(r, "Anastas", "Who?", {"lead_model_requests": True})
    assert out["loss"] and out["last_seen"]["kind"] == "state_summary"
