"""A real subprocess fixture, not an LLM and not a library implementation."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time


def main():
    request = json.load(sys.stdin)
    mode = request["mode"]
    task = request["task"]
    if mode == "timeout":
        time.sleep(30)
    if mode == "fail":
        print("intentional fixture process failure", file=sys.stderr)
        return 7
    events = []
    output = {"task_id": task["task_id"], "message": task["text"], "items": [task["text"]]}
    if mode in ("skill", "flow"):
        raw = Path(request["skill_path"]).read_bytes()
        events.append({"kind": "skill_loaded", "sha256": hashlib.sha256(raw).hexdigest()})
    if mode in ("tool", "flow"):
        tool_spec = importlib.util.spec_from_file_location("owned_fixture_tool", request["tool_path"])
        tool = importlib.util.module_from_spec(tool_spec)
        tool_spec.loader.exec_module(tool)
        output = tool.execute(task)
        events.append({"kind": "tool_call", "name": "fixture.echo", "arguments": task,
                       "result": output})
    output["probe_value"] = 11 if request["variant"] == "A" else 29
    json.dump({"output": output, "events": events, "seen_input_keys": sorted(task)}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
