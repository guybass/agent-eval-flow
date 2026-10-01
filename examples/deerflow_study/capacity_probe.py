"""Deterministic, offline: how many tool rounds complete within a recursion_limit?

Builds DeerFlow's real lead agent with a scripted fake model (N tool calls, then
an answer) and a stub web_search tool, so no API key, network or cost is involved.

Run inside DeerFlow's environment with a study config whose web_search entry uses
`examples.deerflow_study.stub_tools:web_search_tool`, web_fetch removed and title off:

  DEER_FLOW_CONFIG_PATH=<probe config> PYTHONPATH=<deer-flow>/backend/packages/harness:<deer-flow>/backend:<agent-eval-flow> \
    uv run python -m examples.deerflow_study.capacity_probe --limit 100

Result at 827acf51 (study config, limit 100): pristine completes with <= 5 tool rounds
(<= 6 model calls); with this study's CaptureMiddleware attached, <= 4 tool rounds.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import tempfile

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class ScriptedModel(BaseChatModel):
    tool_rounds: int = 0
    calls: int = 0

    @property
    def _llm_type(self):
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        if self.calls <= self.tool_rounds:
            message = AIMessage(content="", tool_calls=[{"name": "web_search", "args": {"query": f"q{self.calls}"},
                                                         "id": f"c{self.calls}"}])
        else:
            message = AIMessage(content="final answer")
        return ChatResult(generations=[ChatGeneration(message=message)])


def run(tool_rounds: int, limit: int, with_capture: bool) -> tuple[str, int]:
    import deerflow.agents.lead_agent.agent as lead
    import deerflow.client as client_module
    from deerflow.client import DeerFlowClient

    model = ScriptedModel(tool_rounds=tool_rounds)
    lead.create_chat_model = client_module.create_chat_model = lambda *a, **k: model
    middlewares = None
    if with_capture:
        from .capture import CaptureMiddleware, TraceWriter
        middlewares = [CaptureMiddleware(TraceWriter(pathlib.Path(tempfile.mkdtemp()) / "t.jsonl"))]
    client = DeerFlowClient(config_path=os.environ["DEER_FLOW_CONFIG_PATH"], thinking_enabled=False,
                            subagent_enabled=True, middlewares=middlewares)
    try:
        for _ in client.stream("probe", recursion_limit=limit):
            pass
        return "ok", model.calls
    except Exception as exc:  # GraphRecursionError is the expected failure
        return type(exc).__name__, model.calls


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    for with_capture in (False, True):
        for rounds in range(3, 9):
            status, calls = run(rounds, args.limit, with_capture)
            label = "with capture" if with_capture else "pristine"
            print(f"{label:12} tool_rounds={rounds} -> {status} (model calls made: {calls})")


if __name__ == "__main__":
    main()
