"""Offline check of the MCP memory example: each build is graded on its own recorded cell."""
import json
import sys

import agent_eval_flow as a
from agent_eval_flow.objects.tool_trace import trace_for_run

from examples import mcp_memory_review


def test_each_build_is_graded_on_its_own_cells(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["mcp_memory_review", "--output", str(tmp_path)])
    mcp_memory_review.main()
    result = a.EvaluationResult.load(tmp_path / "result")
    got = {(s.candidate_id, s.summary_id): s.value.value for s in result.summaries}

    capture = json.loads(mcp_memory_review.SOURCE.read_text(encoding="utf-8"))
    error_calls = 0
    for run in result.runs.runs:
        cell = capture["cells"][int(run.native_refs["cell_index"])]
        trace = trace_for_run(run, mcp_memory_review.BOUNDARY)
        assert len(trace.calls) == len(cell["calls"])
        for actual, recorded in zip(trace.calls, cell["calls"]):
            assert actual.completion == ("error" if recorded["is_error"] else "completed")
            error_calls += recorded["is_error"]
    assert error_calls > 0
    for build in capture["builds"]:
        cells = [c for c in capture["cells"] if c["build"] == build]
        silent = sum(1 for c in cells if c["facts_lost"] and not c["write_reported_error"])
        assert got[(build, "silent_data_loss_total")] == silent
        assert got[(build, "facts_lost_total")] == sum(len(c["facts_lost"]) for c in cells)
    assert got[("main-f46d957", "silent_data_loss_total")] > got[("published-2026.8.31", "silent_data_loss_total")]
    assert got[("fix-0ef28b0", "silent_data_loss_total")] < got[("published-2026.8.31", "silent_data_loss_total")]


def test_agent_runs_are_regraded_from_the_recorded_file(tmp_path, monkeypatch):
    from examples import mcp_memory_agent_review as review

    capture = json.loads(review.SOURCE.read_text(encoding="utf-8"))
    model = "gpt-5.6-luna"
    monkeypatch.setattr(sys, "argv", ["review", "--model", model, "--output", str(tmp_path)])
    review.main()
    result = a.EvaluationResult.load(tmp_path / "result")
    got = {(s.candidate_id, s.summary_id): s.value.value for s in result.summaries}

    seed = {e["name"].lower() for e in capture["seed"]["entities"]}
    for server in capture["servers"]:
        runs = [r for r in capture["runs"] if r["model"] == model and r["server"] == server]
        stored = sum(review.outcomes(r, seed)["fact_stored"] for r in runs) / len(runs)
        assert got[(server, "fact_stored_rate")] == stored
    assert got[("c1", "silent_loss_rate")] < got[("main", "silent_loss_rate")]


def test_agent_import_preserves_recorded_tool_errors(tmp_path):
    from examples import mcp_memory_agent_review as review
    from agent_eval_flow.storage.artifacts import ArtifactCache
    from types import SimpleNamespace

    capture = json.loads(review.SOURCE.read_text(encoding="utf-8"))
    row = next(r for r in capture["runs"] if r["calls"])
    row["calls"][0]["is_error"] = True
    source = tmp_path / "capture.json"
    source.write_text(json.dumps(capture), encoding="utf-8")
    assignment = SimpleNamespace(id="test-assignment", candidate_id=row["server"],
                                 unit={"scenario": row["scenario"]}, repetition=row["rep"])
    imported = review.AgentRunImporter(ArtifactCache(tmp_path / "evidence"), row["model"]).read(
        source, plan=SimpleNamespace(assignments=(assignment,)))
    trace = trace_for_run(imported.runs[0], review.BOUNDARY)
    assert trace.calls[0].completion == "error"
    assert trace.calls[0].result["is_error"] is True
    assert trace.calls[0].result_evidence
