"""Offline native-format contracts; these captures are deliberately test-owned."""
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest

import agent_eval_flow as a
from agent_eval_flow.adapters.codex import CodexDialect
from agent_eval_flow.adapters.claude_code import ClaudeDialect
from agent_eval_flow.adapters.process import NativeProcessCapture, StopEvidence
from agent_eval_flow.adapters.cli import schema_validator
from agent_eval_flow.storage.artifacts import ArtifactCache


def capture(tmp_path, rows, final=..., *, exit_code=0):
    cache = ArtifactCache(tmp_path)
    trace = cache.write_bytes("stdout", b"".join((json.dumps(row) + "\n").encode() for row in rows), "application/jsonl")
    stderr = cache.write_bytes("stderr", b"", "text/plain")
    artifacts = {} if final is ... else {"native.output": cache.write_bytes("final", json.dumps(final).encode(), "application/json")}
    now = datetime.now(timezone.utc)
    return NativeProcessCapture(stdout=trace, stderr=stderr, exit_code=exit_code, started_at=now, ended_at=now,
        stop=StopEvidence(requested=False, confirmed=True, reason="Test process ended"),
        output_complete=a.Observation(value=True, status="observed"), artifacts=artifacts)


def test_codex_tool_pair_keeps_native_ids_and_exact_source_lines(tmp_path):
    rows = [{"type": "thread.started", "thread_id": "thread-original"},
        {"type": "item.started", "item": {"id": "call-7", "type": "command_execution", "command": "python investigate.py"}},
        {"type": "item.completed", "item": {"id": "call-7", "type": "command_execution", "command": "python investigate.py", "aggregated_output": "SyntaxError: evidence retained", "exit_code": 1}},
        {"type": "turn.completed", "usage": {"input_tokens": 42, "output_tokens": 9}}]
    process = capture(tmp_path, rows, {"answer": "failure captured"})
    dialect = CodexDialect()
    parsed = dialect.parse(process)
    events = dialect.events(parsed, "run/native")
    decision, completed = events[1:3]
    assert decision.kind == "model_response" and completed.kind == "tool_call"
    assert completed.fields["decision_event_id"] == decision.id
    assert completed.fields["native_id"] == "call-7"
    assert completed.fields["result"]["exit_code"] == 1
    assert completed.inputs == (decision.source,) and completed.outputs == (completed.source,)
    assert decision.source.locator == "line:2" and completed.source.locator == "line:3"
    assert parsed.native_refs["invocation_id"] == "thread-original"
    assert dialect.resources(parsed).input_tokens.value == 42
    assert dialect.resources(parsed).cost_usd.status == "unknown"


@pytest.mark.parametrize("value,state", [(None, "available"), (..., "unavailable")])
def test_codex_null_is_distinct_from_absent_response(tmp_path, value, state):
    parsed = CodexDialect().parse(capture(tmp_path, [{"type": "turn.completed"}], value))
    assert parsed.output_state == state and parsed.final_output is None


def test_claude_cost_is_estimated_and_tool_results_join_by_id(tmp_path):
    rows = [{"type": "system", "subtype": "init", "session_id": "session-8", "model": "native-model"},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "one", "name": "Read", "input": {"path": "a.py"}},
            {"type": "tool_use", "id": "two", "name": "Bash", "input": {"command": "check"}}]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "two", "content": "result2"},
            {"type": "tool_result", "tool_use_id": "one", "content": "result1"}]}},
        {"type": "result", "subtype": "success", "structured_output": None,
         "total_cost_usd": 0.125, "usage": {"input_tokens": 100, "output_tokens": 17}}]
    dialect = ClaudeDialect()
    parsed = dialect.parse(capture(tmp_path, rows))
    events = dialect.events(parsed, "run/native")
    tools = [event for event in events if event.kind == "tool_call"]
    assert [event.fields["name"] for event in tools] == ["Bash", "Read"]
    assert tools[0].fields["arguments"] == {"command": "check"}
    assert parsed.output_state == "available" and parsed.final_output is None
    assert dialect.resolved_model(parsed) == "native-model"
    cost = dialect.resources(parsed).cost_usd
    assert cost.status == "estimated" and cost.value == Decimal("0.125") and cost.evidence


def test_schema_uses_json_schema_and_never_coerces_bool_to_integer():
    validator = schema_validator({"type": "object", "properties": {"count": {"type": "integer"}},
                                  "required": ["count"], "additionalProperties": False})
    validator.validate({"count": 3})
    with pytest.raises(Exception):
        validator.validate({"count": True})


def test_codex_flags_pass_prompt_only_on_stdin_and_keep_paths_separate(tmp_path):
    executable = tmp_path / "path with spaces" / "codex"
    argv = CodexDialect().argv(executable, tmp_path, {"id": "chosen-model", "provider": "openai"}, {}, {})
    assert argv[0] == str(executable)
    assert argv[-1] == "-" and "--ignore-user-config" in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv
    with pytest.raises(a.ConfigurationError):
        CodexDialect().argv(executable, tmp_path, {"id": "x", "provider": "vertex"}, {}, {})


def test_cli_preserves_malformed_trace_and_reports_projection_issue(tmp_path):
    cache = ArtifactCache(tmp_path)
    valid = capture(tmp_path, [{"type": "turn.completed"}], {})
    from dataclasses import replace
    broken = cache.write_bytes("broken", b'{"type":"thread.started","thread_id":"x"}\nNOT_JSON\n', "application/jsonl")
    parsed = CodexDialect().parse(replace(valid, stdout=broken))
    assert len(parsed.events) == 1 and "line:2" in parsed.issues[0]
    assert Path(broken.uri).read_bytes().endswith(b"NOT_JSON\n")
