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


from examples.deerflow_study.grade import grade
from examples.deerflow_study.run_pilot import run_batch, run_one


class FakeClient:
    """Emits deerflow.client.StreamEvent-shaped events."""

    def __init__(self, fail=False):
        self.fail = fail

    def stream(self, message, thread_id=None, **kwargs):
        if self.fail:
            raise RuntimeError("rate limited")
        yield Ev("messages-tuple", {"type": "tool", "name": "web_search", "content": "Anastas"})
        yield Ev("messages-tuple", {"type": "ai", "id": "m1", "content": "Chi"})
        yield Ev("messages-tuple", {"type": "ai", "id": "m1", "content": "rik"})
        yield Ev("end", {"usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}})


QUESTION = {"id": 1, "question": "Who won?", "answer": "Anastas"}


def test_answer_assembled_from_deltas_and_usage_from_end(tmp_path):
    row = run_one(lambda writer: FakeClient(), QUESTION, tmp_path)
    assert row["status"] == "ok" and row["answer"] == "Chirik"
    assert row["usage"] == {"input_tokens": 10, "output_tokens": 2}
    kinds = [json.loads(line)["kind"] for line in open(row["trace"], encoding="utf-8")]
    assert kinds[0] == "tool_result" and kinds[-1] == "final_answer"


def test_failed_run_recorded_not_scored(tmp_path):
    row = run_one(lambda writer: FakeClient(fail=True), QUESTION, tmp_path)
    assert row["status"] == "error" and "rate limited" in row["error"] and row["answer"] is None


def test_budget_stop(tmp_path):
    questions = [dict(QUESTION, id=i) for i in range(3)]
    rows = run_batch(questions, lambda writer: FakeClient(), tmp_path, spent_usd=3.99, limit_usd=4.00,
                     cost_fn=lambda usage: 0.02)
    assert len(rows) == 1  # stops before starting a question once spend >= limit


def test_grade():
    assert grade("It was Paul Anastas.", "Anastas") == "correct"
    assert grade("Chirik", "Anastas") == "incorrect"
    assert grade("", "Anastas") == "review"


from examples.deerflow_study.toolscore_view import CONTRACT, evidence_tool, tool_calls, toolscore_metrics


def _stream_rows(tmp_path, events):
    w = TraceWriter(tmp_path / "run.jsonl")
    for event in events:
        record_stream_event(w, event)
    w.close()
    return [json.loads(line) for line in (tmp_path / "run.jsonl").read_text().splitlines()]


def test_tool_calls_keep_ids_names_and_args_in_request_order(tmp_path):
    rows = _stream_rows(tmp_path, [
        Ev("messages-tuple", {"type": "ai", "content": "", "tool_calls": [
            {"id": "c1", "name": "web_search", "args": {"query": "2016 green chemistry award"}},
            {"id": "c2", "name": "web_fetch", "args": {"url": "https://rsc.org/x"}}]}),
        Ev("messages-tuple", {"type": "tool", "name": "web_fetch", "tool_call_id": "c2", "content": "Paul Anastas"}),
        Ev("messages-tuple", {"type": "tool", "name": "web_search", "tool_call_id": "c1", "content": "results"})])
    assert tool_calls(rows) == [{"id": "c1", "tool": "web_search", "args": {"query": "2016 green chemistry award"}},
                                {"id": "c2", "tool": "web_fetch", "args": {"url": "https://rsc.org/x"}}]
    assert rows[1]["tool_call_id"] == "c2"
    assert evidence_tool(rows, "Anastas") == "web_fetch"


def test_toolscore_metrics_against_name_level_contract(tmp_path):
    pytest.importorskip("toolscore")
    rows = _stream_rows(tmp_path, [Ev("messages-tuple", {"type": "ai", "content": "", "tool_calls": [
        {"id": "a", "name": "web_search", "args": {"query": "q"}},
        {"id": "b", "name": "web_search", "args": {"query": "q"}}]})])
    m = toolscore_metrics(rows)
    assert CONTRACT["calls"] == [{"tool": "web_search"}, {"tool": "web_fetch"}]
    assert m["required_call_recall"] == 0.5          # searched but never fetched a page
    assert m["searches"] == 2 and m["fetches"] == 0
    assert 0.0 <= m["redundant_rate"] <= 1.0


from examples.deerflow_study.report import build_table


def test_report_joins_grade_evidence_loss_and_tool_path(tmp_path):
    trace = tmp_path / "t.jsonl"
    w = TraceWriter(trace)
    record_stream_event(w, Ev("messages-tuple", {"type": "ai", "content": "", "tool_calls": [
        {"id": "c1", "name": "web_search", "args": {"query": "q"}}]}))
    record_stream_event(w, Ev("messages-tuple", {"type": "tool", "name": "web_search", "tool_call_id": "c1",
                                                 "content": "Paul Anastas won"}))
    w.record("model_request", agent="lead", messages=[{"type": "ai", "text": "Chirik"}])
    w.record("final_answer", content="Chirik")
    w.close()
    results = [{"id": 7, "status": "ok", "answer": "Chirik", "trace": str(trace),
                "coverage": {"lead_model_requests": True}},
               {"id": 8, "status": "error", "answer": None, "trace": str(trace), "coverage": {}}]
    gold = {7: {"question": "Who won?", "answer": "Anastas"}, 8: {"question": "Q?", "answer": "X"}}
    table = build_table(results, gold)
    row = table[0]
    assert row["grade"] == "incorrect" and row["loss"] is True and row["evidence_tool"] == "web_search"
    assert row["searches"] == 1 and row["fetches"] == 0
    assert table[1] == {"id": 8, "status": "error"}


class ErrorAnswerClient:
    """DeerFlow surfaces provider failures as AI text; that is an error, not an answer."""

    def stream(self, message, thread_id=None, **kwargs):
        yield Ev("messages-tuple", {"type": "ai", "id": "m1",
                                    "content": "LLM request failed: Error code: 400 - {...}"})
        yield Ev("end", {"usage": {"input_tokens": 0, "output_tokens": 0}})


def test_provider_error_text_is_recorded_as_error_not_answer(tmp_path):
    row = run_one(lambda writer: ErrorAnswerClient(), QUESTION, tmp_path)
    assert row["status"] == "error" and row["error"].startswith("LLM request failed")


def test_date_gold_matches_other_day_month_orders():
    assert grade("William Croft was born on 13 November 1956.", "November 13, 1956") == "correct"
    assert grade("Born 1956-11-13", "November 13, 1956") == "correct"
    assert grade("Born 14 November 1956", "November 13, 1956") == "incorrect"


class KwargsRecordingClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.kwargs = None

    def stream(self, message, thread_id=None, **kwargs):
        self.kwargs = kwargs
        yield from super().stream(message, thread_id=thread_id)


def test_stream_kwargs_are_passed_through(tmp_path):
    client = KwargsRecordingClient()
    run_one(lambda writer: client, QUESTION, tmp_path, stream_kwargs={"recursion_limit": 300})
    assert client.kwargs == {"recursion_limit": 300}


def test_identical_repeats_are_counted_separately_from_toolscore_redundancy(tmp_path):
    pytest.importorskip("toolscore")
    rows = _stream_rows(tmp_path, [Ev("messages-tuple", {"type": "ai", "content": "", "tool_calls": [
        {"id": "a", "name": "web_search", "args": {"query": "q1"}},
        {"id": "b", "name": "web_search", "args": {"query": "q2"}},
        {"id": "c", "name": "web_search", "args": {"query": "q1"}}]})])
    m = toolscore_metrics(rows)
    assert m["identical_repeats"] == 1          # only the second "q1" repeats a call exactly
    assert m["redundant_rate"] > 0.5            # Toolscore: calls beyond the contract's one search


def test_selection_supports_tsv_columns_and_short_answer_filter(tmp_path):
    path = tmp_path / "f.tsv"
    lines = ["\tPrompt\tAnswer"] + [f"{i}\tQ{i}?\t{'A' * (5 if i % 2 else 60)}" for i in range(40)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    picked = select_questions(path, seed=7, n=5, expected_sha256=digest, question_col="Prompt",
                              answer_col="Answer", delimiter="\t", max_answer_chars=40)
    assert len(picked) == 5 and all(len(q["answer"]) <= 40 for q in picked)
    assert all(q["question"] == f"Q{q['id']}?" for q in picked)


def test_run_batch_passes_stream_kwargs_to_every_run(tmp_path):
    clients = []

    def factory(writer):
        clients.append(KwargsRecordingClient())
        return clients[-1]

    run_batch([dict(QUESTION, id=i) for i in range(2)], factory, tmp_path, spent_usd=0, limit_usd=1,
              cost_fn=lambda usage: 0.0, stream_kwargs={"recursion_limit": 300})
    assert [c.kwargs for c in clients] == [{"recursion_limit": 300}] * 2


from examples.deerflow_study.curate import curate_run


def test_curated_run_keeps_observations_not_bodies_or_paths(tmp_path):
    trace = tmp_path / "raw" / "q7.jsonl"
    w = TraceWriter(trace)
    record_stream_event(w, Ev("messages-tuple", {"type": "ai", "content": "", "tool_calls": [
        {"id": "c1", "name": "web_fetch", "args": {"url": "https://example.org/a"}}]}))
    record_stream_event(w, Ev("messages-tuple", {"type": "tool", "name": "web_fetch", "tool_call_id": "c1",
                                                 "content": "SECRET PAGE BODY about Paul Anastas " * 20}))
    w.record("model_request", agent="lead", messages=[{"type": "system", "text": "SYSTEM PROMPT"}])
    w.record("final_answer", content="Paul Anastas")
    w.close()
    result = {"id": 7, "run_id": "q7-x", "status": "ok", "answer": "Paul Anastas", "trace": str(trace),
              "usage": {"input_tokens": 10, "output_tokens": 2}, "elapsed_s": 3.0, "error": None,
              "coverage": {"lead_model_requests": True}}
    row = curate_run(result, {"question": "Who?", "answer": "Anastas"}, experiment="simpleqa",
                     recursion_limit=100)
    text = json.dumps(row)
    assert "SECRET PAGE BODY" not in text and "SYSTEM PROMPT" not in text and str(tmp_path) not in text
    assert row["tool_calls"] == [{"id": "c1", "tool": "web_fetch", "args": {"url": "https://example.org/a"}}]
    assert row["tool_results"][0]["gold_present"] is True and row["tool_results"][0]["chars"] > 100
    assert row["grade"] == "correct" and row["turns"] == 1 and len(row["raw_trace_sha256"]) == 64


def test_deerflow_review_offline_roundtrip(tmp_path):
    pytest.importorskip("toolscore")
    import subprocess
    import sys
    out = subprocess.run([sys.executable, "examples/deerflow_review.py", "--output", str(tmp_path)],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr[-2000:]
    summary = json.loads(out.stdout)
    assert summary["runs"] == 30 and summary["needed_more_than_default_in_primary_runs"] == 5
    assert (tmp_path / "report.html").exists() and (tmp_path / "tools.html").exists()
