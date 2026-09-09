"""Exercise real downloaded tool bytes through the application-owned recipe.

Native protocol messages here are test-owned; this does not run an LLM.
"""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import zipfile

import agent_eval_flow as a
from agent_eval_flow.adapters.codex import CodexDialect
from agent_eval_flow.adapters.process import NativeProcessCapture, StopEvidence
from agent_eval_flow.objects.values import observed, unknown, zero_resources
from agent_eval_flow.storage.artifacts import ArtifactCache
from examples.integrations.local_cli import ShowcaseRecipe


FIXTURE = Path(__file__).resolve().parents[1] / "e2e/fixtures/workflow"


def test_recipe_executes_source_tools_and_packages_actual_receipts(tmp_path):
    def sha(name):
        return hashlib.sha256((FIXTURE / name).read_bytes()).hexdigest()
    cache = ArtifactCache(tmp_path / "cache")
    workspace = tmp_path / "work"
    workspace.mkdir()
    candidate = a.Candidate(id="A", backend=a.VersionRef(name="codex", revision="test-owned"), components={},
        settings={"sandbox": "workspace-write", "workflow_e2e": {
            "fixture_dir": str(FIXTURE), "source_manifest_sha256": sha("SOURCE_MANIFEST.json"),
            "cases_sha256": sha("cases.json"), "tool_sha256": sha("investigation_tool.py"),
            "max_tool_calls": 3, "mission_file": str(FIXTURE / "MISSION.md"),
            "response_schema_file": str(FIXTURE / "report.schema.json")}})
    assignment = a.Assignment(id="task-1", candidate_id="A", candidate_fingerprint=candidate.fingerprint(),
        unit={"task_id": "source-investigation"}, repetition=0)
    request = a.RunRequest(run_id="run-source", assignment=assignment, candidate=candidate,
        input=a.AgentInput(unit=assignment.unit, tables={"units": ({"task_id": "source-investigation", "nonce": "fresh-owned-nonce"},)}),
        policy=a.ExecutionPolicy(budget=a.Budget(wall_time_s=60)), environment=None)
    recipe = ShowcaseRecipe()
    metadata = recipe.prepare(request, workspace, cache)
    records = [{"type": "thread.started", "thread_id": "test-owned-native-1"}]
    calls = []
    case_ids = [case["id"] for case in json.loads((FIXTURE / "cases.json").read_text(encoding="utf-8"))["cases"]]
    actions = [("inventory", {}), ("run_cases", {"case_ids": case_ids})]
    for index in range(3):
        if index == 2:
            reproduction = calls[1]
            arguments = {**reproduction["result"]["source_hints"][0], "based_on_receipt_id": reproduction["receipt_id"]}
            action = "read_source"
        else:
            action, arguments = actions[index]
        argv = [sys.executable, str(workspace / "run_investigation_tool.py"), action, json.dumps(arguments)]
        completed = subprocess.run(argv, cwd=workspace, capture_output=True, text=True, encoding="utf-8", timeout=20, check=True)
        calls.append(json.loads(completed.stdout))
        command = subprocess.list2cmdline(argv)
        records.extend([
            {"type": "item.started", "item": {"id": f"call-{index}", "type": "command_execution", "command": command}},
            {"type": "item.completed", "item": {"id": f"call-{index}", "type": "command_execution", "command": command,
                                                  "exit_code": 0, "aggregated_output": completed.stdout}}])
    refused = subprocess.run([sys.executable, str(workspace / "run_investigation_tool.py"), "inventory", "{}"],
                             cwd=workspace, capture_output=True, text=True, timeout=20)
    assert refused.returncode != 0 and "budget exhausted" in refused.stderr
    assert len(list((workspace / "receipts").glob("*.json"))) == 3
    trace = cache.write_bytes("trace", b"".join((json.dumps(row) + "\n").encode() for row in records), "application/jsonl")
    stderr = cache.write_bytes("stderr", b"", "text/plain")
    now = datetime.now(timezone.utc)
    capture = NativeProcessCapture(stdout=trace, stderr=stderr, exit_code=0, started_at=now, ended_at=now,
        stop=StopEvidence(requested=False, confirmed=True, reason="Test fixture done"), output_complete=observed(True))
    dialect = CodexDialect()
    parsed = dialect.parse(capture)
    execution = a.Execution(id="run-source/native", slot="native", retry_index=0, parent_id=None, status="completed",
        started_at=now, ended_at=now, effective_config=unknown("Test-owned source protocol"), resources=zero_resources())
    run = a.Run(id=request.run_id, assignment_id=assignment.id, status="completed", cost_scope=("model",),
        output={"task_id": "source-investigation", "nonce": "fresh-owned-nonce"}, output_state="available",
        artifacts={**metadata["artifacts"], "native.trace": trace, "native.stderr": stderr},
        executions=(execution,), started_at=now, ended_at=now, environment=unknown("Test-owned source protocol"),
        execution_inventory_complete=observed(True), native_refs={"invocation_id": "test-owned-native-1"},
        output_sources=(execution.id,), events=dialect.events(parsed, execution.id))
    finished = recipe.finish(request, SimpleNamespace(metadata=metadata), run, cache)
    finished.validate().raise_for_errors()
    tool_events = [event for event in finished.events if event.kind == "tool_call"]
    assert [event.fields["name"] for event in tool_events] == ["workflow.inventory", "workflow.run_cases", "workflow.read_source"]
    assert [event.fields["result"]["receipt_id"] for event in tool_events] == [row["receipt_id"] for row in calls]
    assert tool_events[2].fields["arguments"]["based_on_receipt_id"] == calls[1]["receipt_id"]
    assert all(event.source.artifact == trace and event.inputs and event.outputs for event in tool_events)
    with zipfile.ZipFile(finished.artifacts["workflow.bundle"].uri) as archive:
        index = json.loads(archive.read("manifest.json"))
        assert index["run_id"] == request.run_id
        for row in index["artifacts"]:
            assert hashlib.sha256(archive.read(row["path"])).hexdigest() == row["sha256"]
        assert "input/downloaded/LICENSE.txt" in archive.namelist()
    assert json.loads(Path(finished.artifacts["workflow.calls"].uri).read_text(encoding="utf-8")) == calls
