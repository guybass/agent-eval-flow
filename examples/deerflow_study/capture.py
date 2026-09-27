"""Record what DeerFlow's models and tools saw, via public hooks only."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:  # DeerFlow's environment provides LangChain; unit tests run without it.
    from langchain.agents.middleware import AgentMiddleware
except ImportError:  # pragma: no cover - exercised only outside DeerFlow's env
    class AgentMiddleware:  # minimal stand-in with the hooks we use
        def __init__(self):
            pass


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_text(p.get("text", "")) if isinstance(p, dict) else _text(p) for p in content)
    return "" if content is None else str(content)


class TraceWriter:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("w", encoding="utf-8")
        self._seq = 0
        self.coverage = {"lead_model_requests": False, "subagent_model_requests": False,
                         "subagent_events": False, "tool_results": False}

    def record(self, kind: str, **fields: Any) -> None:
        row = {"seq": self._seq, "kind": kind, **fields}
        self._file.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        self._file.flush()
        self._seq += 1

    def close(self) -> None:
        self._file.close()


class CaptureMiddleware(AgentMiddleware):
    """Lead-agent model inputs/outputs. Sub-agents build their own agents and
    may not run this middleware; coverage records what was observed."""

    def __init__(self, writer: TraceWriter):
        super().__init__()
        self.writer = writer

    def before_model(self, state, runtime):
        messages = [{"type": getattr(m, "type", "?"), "text": _text(getattr(m, "content", ""))}
                    for m in state.get("messages", [])]
        self.writer.record("model_request", agent="lead", messages=messages)
        self.writer.coverage["lead_model_requests"] = True
        return None

    def after_model(self, state, runtime):
        last = state.get("messages", [])[-1:] or [None]
        self.writer.record("model_response", agent="lead", text=_text(getattr(last[0], "content", "")))
        return None


SUBAGENT_EVENTS = {"task_started", "task_running", "task_completed", "task_failed",
                   "task_cancelled", "task_timed_out"}


def record_stream_event(writer: TraceWriter, event: Any) -> None:
    """Classify a ``deerflow.client.StreamEvent`` (type + data), as defined at 827acf51.

    messages-tuple/tool -> tool_result; messages-tuple/ai with tool_calls -> tool_call;
    custom task_* -> subagent_event (task_completed carries the sub-agent's result);
    values with summary_text -> state_summary (compaction output); anything else -> stream_event.
    """
    etype = event.get("type") if isinstance(event, dict) else getattr(event, "type", "")
    data = (event.get("data") if isinstance(event, dict) else getattr(event, "data", None)) or {}
    dtype = data.get("type") if isinstance(data, dict) else None
    if etype == "messages-tuple" and dtype == "tool":
        writer.coverage["tool_results"] = True
        writer.record("tool_result", tool=data.get("name"), content=_text(data.get("content")))
    elif etype == "messages-tuple" and dtype == "ai" and data.get("tool_calls"):
        writer.record("tool_call", calls=data.get("tool_calls"))
    elif etype == "custom" and dtype in SUBAGENT_EVENTS:
        writer.coverage["subagent_events"] = True
        message = data.get("message")
        content = data.get("result") if dtype == "task_completed" else \
            _text(message.get("content") if isinstance(message, dict) else getattr(message, "content", message))
        writer.record("subagent_event", event_type=dtype, task_id=data.get("task_id"), content=_text(content))
    elif etype == "values" and isinstance(data, dict) and data.get("summary_text"):
        writer.record("state_summary", content=_text(data["summary_text"]))
    else:
        writer.record("stream_event", event_type=etype, data_type=dtype)
