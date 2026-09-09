"""Offline checks of the showcase's result consumer; these do not run Codex."""
from dataclasses import replace
import json
import io
from zipfile import ZipFile

import anyio
import pytest

import agent_eval_flow as a
from agent_eval_flow.objects.values import observed
from examples.openkritt_local_review import build_study, inspect_capture, save_outputs, tools_preserved, verify_bundle, _json
from agent_eval_flow.adapters.openkritt import json_bytes
from tests.adapters.test_native_workflows import Recorder, create_kritt


def test_local_showcase_reports_incomplete_workflow_honestly_and_round_trips(tmp_path):
    backend, request, *_ = create_kritt(tmp_path)
    captured = anyio.run(backend.arun, request, Recorder())
    study, metric = build_study({"backend_ref": {"name": backend.ref.name, "revision": backend.ref.revision},
        "post_script_id": 2, "settings": dict(request.candidate.settings)}, namespace="owned-repo")
    assignment = study.plan().assignments[0]
    capture = a.RunSet(id="test-owned-native-capture", plan=study.plan(),
        runs=(replace(captured, assignment_id=assignment.id),), grading_inventory_complete=observed(True))
    result = a.EvaluationPipeline(study=study, evaluators={metric.ref.name: metric}).eval(runs=capture)
    # This fixture has two attempts and no four-step branch graph. A completed
    # scan must not make those absent structural checks pass.
    output = tmp_path / "review"
    output.mkdir()
    summary = save_outputs(study, result, metric, output)
    assert summary["capture_contract_score"] == 75
    assert summary["capture_acceptance"] == "fail"
    assert summary["metrics"]["native_tool_results"]["value"] == 2
    assert summary["metrics"]["cost_usd"]["status"] == "unknown"
    assert summary["saved_result_round_trip"] and summary["regraded_same_capture"]
    assert summary["new_agent_invocations_during_regrade"] == 0
    assert summary["reweighted_score"] == 80
    assert json.loads((output / "summary.json").read_text())["capture_contract_score"] == 75
    assert (output / "evidence.zip").is_file()
    page = (output / "overview.html").read_text(encoding="utf-8")
    assert "Flaskr source review" in page and "vulnerability accuracy" in page


def test_missing_native_capture_cannot_earn_completeness_checks(tmp_path):
    backend, request, *_ = create_kritt(tmp_path, collection_error=True)
    captured = anyio.run(backend.arun, request, Recorder())
    checks, metadata, tools = inspect_capture(captured, repeats=2)
    assert captured.status == "completed"  # Native status and capture success are separate.
    assert not checks["native_completed"]
    assert not checks["inventory_complete"]
    assert not checks["native_traces_complete"]
    assert not checks["tool_receipts_present"]
    assert not checks["workflow_attempts_complete"]
    assert not checks["branch_inputs_preserved"]
    assert checks["output_matches_native"]
    assert not metadata and not tools


def test_tool_free_synthesis_keeps_valid_receipts_but_lost_native_command_fails(tmp_path):
    backend, request, *_ = create_kritt(tmp_path)
    captured = anyio.run(backend.arun, request, Recorder())
    capture = _json(captured, "openkritt.capture")
    cache = backend.binding.artifacts
    stdout = cache.write_bytes("synthesis.stdout", json_bytes({"type": "item.completed", "item": {
        "id": "summary", "type": "agent_message", "text": "Reconciled supplied source evidence without new commands"}}), "application/x-ndjson")
    stderr = cache.write_bytes("synthesis.stderr", b"", "text/plain")
    synthesis = replace(captured.executions[-1], id="synthesis-without-tools",
                        native_refs={"step_metadata_id": "23"})
    captured = replace(captured, executions=(*captured.executions, synthesis),
        artifacts={**captured.artifacts, "synthesis.stdout": stdout, "synthesis.stderr": stderr})
    capture["traces"].append({"metadata_id": "23", "protocol": "codex-jsonl",
                              "stdout_artifact": "synthesis.stdout", "stderr_artifact": "synthesis.stderr"})
    assert tools_preserved(captured, capture)
    lost_tool = next(event for event in captured.events if event.kind == "tool_call")
    missing = replace(captured, events=tuple(event for event in captured.events if event.id != lost_tool.id))
    assert not tools_preserved(missing, capture)
    altered = replace(lost_tool, fields={**lost_tool.fields, "result": {"output": "invented", "exit_code": 0}})
    changed = replace(captured, events=tuple(altered if event.id == lost_tool.id else event for event in captured.events))
    assert not tools_preserved(changed, capture)


@pytest.mark.parametrize("damage", ["omit", "locator"])
def test_internally_valid_zip_without_exact_nested_provenance_fails_capture_check(tmp_path, damage):
    from agent_eval_flow.adapters.openkritt import artifact_bytes
    backend, request, *_ = create_kritt(tmp_path)
    captured = anyio.run(backend.arun, request, Recorder())
    assert verify_bundle(captured)
    with ZipFile(io.BytesIO(artifact_bytes(captured.artifacts["openkritt.bundle"]))) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    index = json.loads(contents["manifest.json"])
    assert index["provenance"]
    if damage == "omit":
        index["provenance"].pop()
    else:
        index["provenance"][0]["locator"] = "wrong:123"
    contents["manifest.json"] = json_bytes(index)
    data = io.BytesIO()
    with ZipFile(data, "w") as archive:
        for name, payload in contents.items():
            archive.writestr(name, payload)
    with ZipFile(io.BytesIO(data.getvalue())) as archive:
        assert archive.testzip() is None
    broken = backend.binding.artifacts.write_bytes("incomplete-provenance", data.getvalue(), "application/zip")
    changed = replace(captured, artifacts={**captured.artifacts, "openkritt.bundle": broken})
    assert not verify_bundle(changed)
