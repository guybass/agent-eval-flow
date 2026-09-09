"""Real library/stream/cache invocation with owned native-protocol receipts.

Python supplies the actual --version check. The contained protocol fixture
supplies documented JSONL shapes, not a real Codex/Claude model or a compatibility
claim. Live provider compatibility remains covered by selected external E2Es.
"""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys

import pytest

from agent_eval_flow import objects as o
from agent_eval_flow.adapters.common import AdapterBinding
from agent_eval_flow.adapters.process import CliConnection, ProcessSupervisor
from agent_eval_flow.adapters.codex import CodexBackend
from agent_eval_flow.adapters.claude_code import ClaudeCodeBackend
from agent_eval_flow.storage.artifacts import ArtifactCache
from tests.adapters.test_process_boundary import OwnedContainedProcess, ReceiptStream, contents
from tests.e2e.support import artifact, observed
from tests.e2e.test_toy_pipeline import build


class TraceLauncher:
    hard_wall_time_limit = True
    def __init__(self, dialect, *, terminal=True, malformed=False, text=False, blocked=False, confirmed=True):
        self.dialect, self.terminal, self.malformed, self.text = dialect, terminal, malformed, text
        self.blocked, self.confirmed = blocked, confirmed
        self.calls, self.processes = [], []

    async def start(self, launch):
        self.calls.append(launch)
        native_id = "owned-native-" + str(len(self.calls))
        report = {"files": ["app.py", "tests.py"], "iterations": [{"tool": "read", "lines": [1, 2]}]}
        if self.dialect == "codex":
            rows = [
                {"type": "thread.started", "thread_id": native_id},
                {"type": "item.started", "item": {"id": "tool-1", "type": "command_execution", "command": "read app.py"}},
                {"type": "item.completed", "item": {"id": "tool-1", "type": "command_execution", "command": "read app.py",
                    "aggregated_output": "line1\nline2", "exit_code": 0}},
            ]
            if self.terminal:
                rows.append({"type": "turn.completed", "usage": None if self.malformed else {"input_tokens": 21, "output_tokens": 8}})
            (launch.workspace / "final.json").write_text("Actual plain native final answer" if self.text else json.dumps(report), encoding="utf-8")
        else:
            rows = [
                {"type": "system", "subtype": "init", "session_id": native_id, "model": "test-model"},
                {"type": "assistant", "session_id": native_id, "message": {"content": [
                    {"type": "tool_use", "id": "tool-1", "name": "Read", "input": {"file_path": "app.py"}}]}},
                {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": "line1\nline2"}]}},
            ]
            if self.terminal:
                rows.append({"type": "result", "subtype": "success", "session_id": native_id, "is_error": False,
                    "structured_output": report, "total_cost_usd": 0.125,
                    "usage": None if self.malformed else {"input_tokens": 21, "output_tokens": 8}})
        encoded = b"".join(json.dumps(row).encode() + b"\n" for row in rows)
        if self.malformed:
            encoded = b"not a native JSON event\n" + encoded
        process = OwnedContainedProcess(blocked=self.blocked, confirm_stop=self.confirmed)
        process.stdout = ReceiptStream([encoded[:20], encoded[20:]])
        process.stderr = ReceiptStream([b"owned runtime diagnostic"])
        self.processes.append(process)
        return process


def configured(api, toy_backend, tmp_path, dialect, *, launcher_options=None, settings=None,
               scenario=None, extra_environment=None):
    study, evaluators, reducers = build(api, toy_backend, "plain")
    cache = ArtifactCache(tmp_path / "cache")
    launcher = TraceLauncher(dialect, **(launcher_options or {}))
    version = ".".join(map(str, sys.version_info[:3]))
    ref = o.VersionRef(name="owned-cli." + dialect, revision="1")
    binding = AdapterBinding(ref=ref, upstream_ref=o.VersionRef(name="test-python-version-check", revision=version),
        workspace_root=tmp_path / "runs", artifacts=cache, deployment=observed(api, {"platform": "protocol-fixture"}))
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("CLAUDE_CODE_USE_", "ANTHROPIC_BASE_URL"))}
    environment.update(extra_environment or {})
    connection = CliConnection(executable=Path(sys.executable).resolve(), environment=environment,
        supervisor=ProcessSupervisor(launcher=launcher, artifacts=cache, workspace_root=binding.workspace_root))
    cls = CodexBackend if dialect == "codex" else ClaudeCodeBackend
    backend = cls(binding=binding, connection=connection, scenario=scenario)
    candidate = replace(study.candidates["A"], backend=ref,
        settings={"model": {"id": "test-model", "provider": "openai" if dialect == "codex" else "anthropic"},
                  "mission": "Inspect the two files and return an evidence report.", **(settings or {})})
    dataset = study.dataset.select(({"task_id": "toy-1"},))
    study = replace(study, dataset=dataset, candidates={"A": candidate},
                    execution=replace(study.execution, budget=o.Budget(wall_time_s=0.03 if (launcher_options or {}).get("blocked") else 3)))
    return study, backend, launcher, evaluators, reducers


@pytest.mark.parametrize("dialect", ["codex", "claude"])
def test_native_cli_boundary_returns_real_capture_and_grading_objects(api, toy_backend, tmp_path, dialect):
    study, backend, launcher, evaluators, reducers = configured(api, toy_backend, tmp_path, dialect)
    result = study.evaluate(backends={backend.ref.name: backend}, evaluators=evaluators, reducers=reducers)
    run = result.runs.runs[0]
    result.runs.validate().raise_for_errors()
    assert run.status == "completed" and run.output["files"] == ("app.py", "tests.py")
    assert run.output["iterations"][0]["tool"] == "read"
    tool = next(event for event in run.events if event.kind == "tool_call")
    decision = next(event for event in run.events if event.id == tool.fields["decision_event_id"])
    assert decision.kind == "model_response" and tool.inputs and tool.outputs and tool.source
    assert run.executions[0].resources.input_tokens.value == 21
    assert run.executions[0].resources.output_tokens.value == 8
    assert run.resources().cost_usd.status == "unknown", "Child inventory is not exposed by these native schemas"
    assert run.executions[0].resources.cost_usd.status == ("estimated" if dialect == "claude" else "unknown")
    assert len(launcher.calls) == 1 and not toy_backend.calls
    assert b"Public task input" in launcher.processes[0].stdin
    assert contents(run.artifacts["native.stderr"]) == b"owned runtime diagnostic"
    receipt = json.loads(contents(run.artifacts["native.receipt"]))
    assert receipt["model_basis"].startswith("explicit CLI request")
    assert receipt["native_id"] == run.native_refs["invocation_id"]
    assert result.explain(run.id).measurements
    assert not launcher.calls[0].workspace.exists(), "Successful owned workspace is cleaned after evidence retention"


def test_plain_final_text_remains_available_and_schema_reports_separate_diagnostic(api, toy_backend, tmp_path):
    study, backend, _, _, _ = configured(api, toy_backend, tmp_path, "codex",
        launcher_options={"text": True}, settings={"response_schema": {"type": "object"}})
    run = study.run(backends={backend.ref.name: backend}).runs[0]
    assert run.output == "Actual plain native final answer" and run.output_state == "available"
    assert run.status == "completed" and "Response schema" in run.error.message


@pytest.mark.parametrize("dialect", ["codex", "claude"])
def test_missing_terminal_retains_executed_work_without_invalid_unobserved_status(api, toy_backend, tmp_path, dialect):
    study, backend, _, _, _ = configured(api, toy_backend, tmp_path, dialect, launcher_options={"terminal": False})
    capture = study.run(backends={backend.ref.name: backend})
    capture.validate().raise_for_errors()
    run = capture.runs[0]
    assert run.status == "infrastructure_error" and len(run.executions) == 1
    assert run.events and run.artifacts["native.trace"]


def test_unconfirmed_deadline_preserves_execution_without_claiming_stopped_duration(api, toy_backend, tmp_path):
    study, backend, _, _, _ = configured(api, toy_backend, tmp_path, "codex",
        launcher_options={"blocked": True, "confirmed": False})
    run = study.run(backends={backend.ref.name: backend}).runs[0]
    assert run.status == "infrastructure_error" and run.duration_s().status == "unknown"
    assert run.executions and "unconfirmed" in run.error.message


@pytest.mark.parametrize("dialect", ["codex", "claude"])
def test_malformed_native_usage_retains_trace_and_unknown_tokens(api, toy_backend, tmp_path, dialect):
    study, backend, _, _, _ = configured(api, toy_backend, tmp_path, dialect, launcher_options={"malformed": True})
    run = study.run(backends={backend.ref.name: backend}).runs[0]
    assert run.status == "completed" and run.output_state == "available"
    assert run.executions[0].resources.input_tokens.status == "unknown"
    assert contents(run.artifacts["native.trace"]).startswith(b"not a native JSON event")
    assert "line:1" in run.error.message


def test_broader_cost_scope_is_unknown_without_losing_observed_tokens(api, toy_backend, tmp_path):
    study, backend, _, _, _ = configured(api, toy_backend, tmp_path, "claude")
    study = replace(study, execution=replace(study.execution, cost_scope=("model", "compute")))
    run = study.run(backends={backend.ref.name: backend}).runs[0]
    resources = run.executions[0].resources
    assert resources.cost_scope == ("model", "compute") and resources.cost_usd.status == "unknown"
    assert resources.input_tokens.value == 21


def test_scenario_projects_events_before_their_final_receipt_is_recorded(api, toy_backend, tmp_path):
    class Scenario:
        def prepare(self, request, workspace, artifacts):
            return {}
        def finish(self, request, prepared, run, artifacts):
            return replace(run, events=tuple(replace(event, fields={**event.fields, "fixture_projection": True})
                if event.kind == "tool_call" else event for event in run.events))
    study, backend, _, _, _ = configured(api, toy_backend, tmp_path, "codex", scenario=Scenario())
    run = study.run(backends={backend.ref.name: backend}).runs[0]
    assert next(event for event in run.events if event.kind == "tool_call").fields["fixture_projection"] is True


def test_scenario_export_failure_keeps_native_execution_events_and_raw_artifacts(api, toy_backend, tmp_path):
    class Scenario:
        def prepare(self, request, workspace, artifacts):
            return {}
        def finish(self, request, prepared, run, artifacts):
            raise RuntimeError("owned export failed")
    study, backend, launcher, _, _ = configured(api, toy_backend, tmp_path, "codex", scenario=Scenario())
    run = study.run(backends={backend.ref.name: backend}).runs[0]
    assert run.status == "infrastructure_error" and "owned export failed" in run.error.message
    assert run.executions[0].status == "completed"
    assert any(event.kind == "tool_call" for event in run.events) and "native.receipt" in run.artifacts
    assert launcher.calls[0].workspace.exists(), "Failure workspace remains available for investigation"


def test_conflicting_claude_provider_route_fails_before_launch(api, toy_backend, tmp_path):
    study, backend, launcher, _, _ = configured(api, toy_backend, tmp_path, "claude",
        extra_environment={"CLAUDE_CODE_USE_VERTEX": "1"})
    with pytest.raises(o.ConfigurationError, match="CLAUDE_CODE_USE_VERTEX"):
        study.run(backends={backend.ref.name: backend})
    assert launcher.calls == []


def test_changed_skill_bytes_fail_configuration_before_native_launch(api, toy_backend, tmp_path):
    study, backend, launcher, _, _ = configured(api, toy_backend, tmp_path, "codex")
    source = tmp_path / "SKILL.md"
    source.write_text("Original skill instructions", encoding="utf-8")
    skill = o.ComponentSpec(kind="skill", ref=o.VersionRef(name="owned-skill", revision="1"),
                            content=artifact(api, source, "text/markdown"))
    candidate = study.candidates["A"].derive(id="A", components={"skill.read": skill})
    source.write_text("Changed instructions after the candidate was pinned", encoding="utf-8")
    study = replace(study, candidates={"A": candidate})
    with pytest.raises(o.ConfigurationError, match="content hash"):
        study.run(backends={backend.ref.name: backend})
    assert launcher.calls == []


def test_unsupported_behavior_setting_fails_before_native_launch(api, toy_backend, tmp_path):
    study, backend, launcher, _, _ = configured(api, toy_backend, tmp_path, "codex",
        settings={"unsupported_reasoning_magic": "high"})
    with pytest.raises(o.ConfigurationError, match="unsupported_reasoning_magic"):
        study.run(backends={backend.ref.name: backend})
    assert launcher.calls == []


def test_bookkeeping_metadata_is_not_sent_as_native_behavior(api, toy_backend, tmp_path):
    study, backend, launcher, _, _ = configured(api, toy_backend, tmp_path, "codex",
        settings={"metadata": {"tracking_label": "DO_NOT_PROMPT_THIS_VALUE"}})
    study.run(backends={backend.ref.name: backend})
    assert b"DO_NOT_PROMPT_THIS_VALUE" not in launcher.processes[0].stdin


def test_scenario_nested_workspace_evidence_is_materialized_before_cleanup(api, toy_backend, tmp_path):
    class Scenario:
        accepted_settings = frozenset({"owned_scenario_option"})
        def prepare(self, request, workspace, artifacts):
            return {}
        def finish(self, request, prepared, run, artifacts):
            path = prepared.launch.workspace / "owned-report.json"
            path.write_text('{"iterations":[1,2],"tools":["read","inspect"]}', encoding="utf-8")
            ref = artifact(api, path, "application/json")
            evidence = o.EvidenceRef(artifact=ref, locator="/iterations")
            return replace(run, artifacts={**run.artifacts, "owned.report": ref},
                events=tuple(replace(event, outputs=(*event.outputs, evidence))
                             if event.kind == "tool_call" else event for event in run.events))
    study, backend, launcher, _, _ = configured(api, toy_backend, tmp_path, "codex", scenario=Scenario(),
        settings={"owned_scenario_option": "declared"})
    run = study.run(backends={backend.ref.name: backend}).runs[0]
    assert not launcher.calls[0].workspace.exists()
    ref = run.artifacts["owned.report"]
    assert json.loads(contents(ref))["tools"] == ["read", "inspect"]
    assert any(evidence.artifact == ref for event in run.events for evidence in event.outputs)
