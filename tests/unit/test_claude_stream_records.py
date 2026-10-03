"""Claude stream records other than assistant/user messages must not break projection.

Live ``claude -p --output-format stream-json`` runs emit ``system`` records (for
example ``permission_denied``) whose ``message`` is plain text. The projector
rejected the whole stream with "invalid message" before it checked the record type.
"""
import json

import pytest

from agent_eval_flow.adapters.tool_trace import claude_stream_tool_trace
from agent_eval_flow.storage.artifacts import ArtifactCache


def project(tmp_path, records):
    data = ("\n".join(json.dumps(row) for row in records) + "\n").encode()
    artifact = ArtifactCache(tmp_path).write_bytes("stream", data, "application/x-ndjson")
    return claude_stream_tool_trace(data, artifact=artifact, execution_id="main", boundary="requests")


def tool_use(call_id):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": call_id, "name": "Bash", "input": {"command": "ls"}}]}}


def tool_result(call_id):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": call_id, "content": "denied", "is_error": True}]}}


def test_text_message_on_system_record_is_retained_not_interpreted(tmp_path):
    trace = project(tmp_path, [
        {"type": "system", "subtype": "init", "message": "session started"},
        tool_use("t1"),
        {"type": "system", "subtype": "permission_denied", "message": "Permission to use Bash was denied"},
        tool_result("t1"),
        {"type": "result", "subtype": "success", "result": "done"},
    ])
    assert [(c.call_id, c.completion) for c in trace.calls] == [("t1", "error")]


def test_unsupported_content_in_an_assistant_message_still_fails(tmp_path):
    with pytest.raises(ValueError, match="unsupported message content"):
        project(tmp_path, [{"type": "assistant", "message": {"content": "plain text"}}])
