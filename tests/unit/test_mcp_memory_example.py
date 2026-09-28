"""Offline check of the MCP memory example: each build is graded on its own recorded cell."""
import json
import sys

import agent_eval_flow as a

from examples import mcp_memory_review


def test_each_build_is_graded_on_its_own_cells(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["mcp_memory_review", "--output", str(tmp_path)])
    mcp_memory_review.main()
    result = a.EvaluationResult.load(tmp_path / "result")
    got = {(s.candidate_id, s.summary_id): s.value.value for s in result.summaries}

    capture = json.loads(mcp_memory_review.SOURCE.read_text(encoding="utf-8"))
    for build in capture["builds"]:
        cells = [c for c in capture["cells"] if c["build"] == build]
        silent = sum(1 for c in cells if c["facts_lost"] and not c["write_reported_error"])
        assert got[(build, "silent_data_loss_total")] == silent
        assert got[(build, "facts_lost_total")] == sum(len(c["facts_lost"]) for c in cells)
    assert got[("main-f46d957", "silent_data_loss_total")] > got[("published-2026.8.31", "silent_data_loss_total")]
    assert got[("fix-0ef28b0", "silent_data_loss_total")] < got[("published-2026.8.31", "silent_data_loss_total")]
