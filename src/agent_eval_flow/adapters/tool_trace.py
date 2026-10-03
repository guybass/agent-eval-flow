"""Explicit projection of retained Claude stream JSON; never runs an agent."""
from dataclasses import replace
import hashlib
import json

from .. import objects as o
from ..objects.tool_trace import ToolRequest, ToolTrace
from ..objects.values import unknown


def claude_stream_tool_trace(data: bytes, *, artifact: o.ArtifactRef, execution_id: str,
                             boundary: str, coverage=None):
    """Preserve request order, unmatched requests, raw arguments and result IDs.

    The caller must supply evidence-backed coverage for the named stream scope.
    A terminal message alone cannot establish complete nested-agent capture.
    Invalid JSON, duplicate identities and orphan results fail explicitly.
    """
    digest = hashlib.sha256(data).hexdigest()
    if artifact.sha256 != digest:
        raise ValueError("Claude stream bytes must match the retained artifact hash")
    calls, positions = [], {}
    for number, line in enumerate(data.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError(f"Claude stream line {number} is not an object")
        if record.get("type") not in ("assistant", "user"):
            # Requests and results live only in assistant/user messages. Other
            # records (system init, permission_denied, result) may carry a text
            # ``message`` and are retained in the stream, not interpreted here.
            continue
        message = record.get("message", {})
        if not isinstance(message, dict):
            raise ValueError(f"Claude stream line {number} has an invalid message")
        content = message.get("content", [])
        if not isinstance(content, list):
            raise ValueError(f"Claude stream line {number} has unsupported message content")
        for index, block in enumerate(content):
            if not isinstance(block, dict):
                raise ValueError(f"Claude stream line {number} contains an invalid block")
            ref = o.EvidenceRef(artifact=artifact, locator=f"line:{number}/content/{index}",
                description="Retained Claude tool stream")
            if block.get("type") == "tool_use":
                call_id = block.get("id")
                if not isinstance(call_id, str) or not call_id.strip() or call_id in positions:
                    raise ValueError("Missing or duplicate Claude tool request ID")
                positions[call_id] = len(calls)
                calls.append(ToolRequest(call_id=call_id, execution_id=execution_id,
                    tool=block["name"], arguments=block.get("input"), evidence=(ref,)))
            elif block.get("type") == "tool_result":
                call_id = block.get("tool_use_id")
                if call_id not in positions:
                    raise ValueError("Claude tool result has no retained request")
                position = positions[call_id]
                if calls[position].completion != "unknown":
                    raise ValueError("Duplicate Claude tool result")
                is_error = block.get("is_error", False)
                if type(is_error) is not bool:
                    raise ValueError("Claude tool result is_error must be a boolean")
                calls[position] = replace(calls[position],
                    completion="error" if is_error else "completed", result=block.get("content"),
                    result_evidence=(ref,))
    return ToolTrace(collector=o.VersionRef(name="agent-eval-flow.claude-tool-stream", revision="1"),
        boundary=boundary, coverage=coverage if coverage is not None else unknown("Stream completeness was not declared"),
        calls=tuple(calls), scope=f"Tool requests visible in the supplied Claude stream for {execution_id}; "
            "nested work outside this stream is excluded")
