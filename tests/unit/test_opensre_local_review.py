"""Offline report-consumer tests using real fixture tools and test-owned events.

No model is called here; this does not substitute for the local native E2E.
"""
from dataclasses import replace
from datetime import datetime, timezone
import io
import json
from zipfile import ZipFile

import agent_eval_flow as a
from agent_eval_flow.adapters.openkritt import artifact_bytes, json_bytes
from agent_eval_flow.adapters.opensre import OpenSREBackend, RuntimeEventRecorder, SessionCapture, UPSTREAM_REVISION
from agent_eval_flow.objects.values import observed, unknown
from examples.opensre_local_review import FIXTURE, INPUTS, build_study, inspect_capture, save_outputs
from tests.adapters.test_native_workflows import Recorder, binding, request_for
from tests.e2e.fixtures.opensre.incident_store import IncidentStore


def captured_incident(tmp_path):
    bind = binding(tmp_path, UPSTREAM_REVISION)
    study, metric = build_study({"backend_ref": {"name": bind.ref.name, "revision": bind.ref.revision}})
    candidate = next(iter(study.candidates.values()))
    request = replace(request_for(bind, candidate.settings), candidate=candidate, assignment=study.plan().assignments[0])
    store = IncidentStore(FIXTURE, tmp_path / "incident", invocation_id="test-owned-invocation")
    observer = RuntimeEventRecorder(cache=bind.artifacts, invocation_id=store.invocation_id)
    emit = observer.for_loop("test-owned-loop")
    count = 0

    def call(name, arguments, iteration):
        nonlocal count
        count += 1
        identifier = f"test-call-{count}"
        common = {"tool_call_id": identifier, "tool_name": name, "args": arguments, "iteration": iteration}
        emit({"type": "tool_execution_start", **common})
        result = store.call(name, arguments, tool_call_id=identifier, loop_id="test-owned-loop")
        emit({"type": "tool_execution_end", **common, "result": result, "is_error": False})
        return result

    emit({"type": "provider_request_start", "iteration": 1, "message_count": 2})
    opened = call("fixture_incident_open", {"incident_id": "HDFS-2008-11-09"}, 1)
    snapshot = {"snapshot_id": opened["snapshot_id"]}
    emit({"type": "provider_request_start", "iteration": 2, "message_count": 4})
    page = call("fixture_logs_search", {**snapshot, "query": {"level": "WARN"}}, 2)
    metrics = call("fixture_metrics_query", snapshot, 2)
    topology = call("fixture_topology_describe", snapshot, 2)
    changes = call("fixture_changes_list", snapshot, 2)
    emit({"type": "provider_request_start", "iteration": 3, "message_count": 12})
    call("fixture_logs_search", {**snapshot, "cursor": page["next_cursor"]}, 3)
    report = {"incident_id": "HDFS-2008-11-09", "summary": "Test-owned report, not an agent diagnosis.",
        "hypotheses": [{"description": "Test field"}], "timeline": [{"description": "Test field"}],
        "evidence": [{"receipt_id": result["receipt_id"], "note": "Test-owned citation"} for result in (page, metrics, topology, changes)],
        "limitations": ["Historical sample; no diagnosis test."], "next_actions": ["Review source receipts."]}
    emit({"type": "provider_request_start", "iteration": 4, "message_count": 14})
    call("fixture_report_write", {**snapshot, "report": report}, 4)
    now = datetime.now(timezone.utc)
    turn = {"final_intent": "answer", "action_result": {"accounting_status": "completed", "cancelled": False,
        "hit_iteration_cap": False}, "assistant_response_text": "Test-owned native-shaped answer."}
    usage = a.Resources(cost_scope=("model",), cost_usd=unknown("Offline fixture has no bill"),
        input_tokens=unknown("Offline fixture has no provider usage"), output_tokens=unknown("Offline fixture has no provider usage"),
        human_minutes=unknown("No effort receipt"))
    capture = SessionCapture(invocation_id=store.invocation_id, turn=json_bytes(turn), status="completed", started_at=now,
        ended_at=now, upstream_ref=bind.upstream_ref, effective_config=observed({"kind": "offline-consumer-test"}),
        model=observed({"provider": "test-owned"}), deployment=bind.deployment, resources=usage,
        inventory_complete=observed(True), trace_complete=observed(True), artifacts={
            **{name: FIXTURE / relative for name, relative in INPUTS.items()},
            "incident.tool_audit": store.work / "tool-audit.jsonl", "incident.report": store.work / "investigation.json"})
    run = OpenSREBackend(binding=bind, runtime=None).map_capture(request, capture, observer.commit(), recorder=Recorder())
    return study, metric, run, bind


def test_source_linked_capture_saves_reloads_regrades_without_backend(tmp_path):
    study, metric, run, _ = captured_incident(tmp_path)
    inspected = inspect_capture(run)
    assert all(inspected["checks"].values()), inspected["failures"]
    assert inspected["iterations"] == 4 and len(inspected["audit"]) == 7
    captures = a.RunSet(id="test-owned-sre-capture", plan=study.plan(), runs=(run,), grading_inventory_complete=observed(True))
    result = a.EvaluationPipeline(study=study, evaluators={metric.ref.name: metric}).eval(runs=captures)
    output = tmp_path / "showcase"
    output.mkdir()
    summary = save_outputs(study, result, metric, output)
    assert summary["capture_contract_score"] == 100 and summary["capture_acceptance"] == "pass"
    assert summary["saved_result_round_trip"] and summary["regraded_same_capture"]
    assert summary["new_agent_invocations_during_regrade"] == 0
    assert summary["metrics"]["cost_usd"]["status"] == "unknown"
    assert len(summary["tool_types"]) == 6
    assert (output / "evidence.zip").is_file()
    assert "Test-owned report" in (output / "overview.html").read_text(encoding="utf-8")
    assert json.loads((output / "summary.json").read_text())["native_event_count"] == len(run.events)


def test_container_identity_cannot_be_overwritten_by_an_artifact_name(tmp_path):
    _, _, run, bind = captured_incident(tmp_path)
    docker = {"invocation_id": run.native_refs["invocation_id"], "container": "aef-opensre-real-worker",
              "image_id": "sha256:real-image", "stopped": True, "deadline_exceeded": False, "container_exit_code": 0}
    receipt = json.loads(artifact_bytes(run.artifacts["native.receipt"]))
    receipt["process"] = {"container": docker["container"], "image_id": docker["image_id"], "exit_code": 0}
    run = replace(run, artifacts={**run.artifacts,
        "native.docker.receipt": bind.artifacts.write_bytes("docker", json_bytes(docker), "application/json"),
        "native.receipt": bind.artifacts.write_bytes("native", json_bytes(receipt), "application/json")})
    assert inspect_capture(run)["checks"]["native_completed"]
    receipt["process"]["container"] = "opensre_local.Dockerfile"
    broken = replace(run, artifacts={**run.artifacts,
        "native.receipt": bind.artifacts.write_bytes("wrong-container", json_bytes(receipt), "application/json")})
    assert not inspect_capture(broken)["checks"]["native_completed"]


def test_completed_native_run_does_not_hide_missing_events_or_report(tmp_path):
    _, _, run, _ = captured_incident(tmp_path)
    broken = replace(run, events=run.events[:-1], artifacts={key: value for key, value in run.artifacts.items() if key != "incident.report"})
    data = inspect_capture(broken)
    assert data["checks"]["native_completed"]
    assert not data["checks"]["native_trace_mapping"]
    assert not data["checks"]["report_usable"]
    assert not data["checks"]["bundle_verified"]


def test_json_reformatting_is_valid_but_changed_tool_receipt_is_not(tmp_path):
    _, _, run, bind = captured_incident(tmp_path)
    rows = [json.loads(line) for line in artifact_bytes(run.artifacts["incident.tool_audit"]).splitlines()]
    rewritten = b"\n".join(json.dumps(row, separators=(",", ":")).encode() for row in rows)
    alternate = bind.artifacts.write_bytes("format-only", rewritten, "application/x-ndjson")
    changed = replace(run, artifacts={**run.artifacts, "incident.tool_audit": alternate})
    assert inspect_capture(changed)["checks"]["tool_receipts_preserved"]
    rows[1]["result"]["rows"][0]["message"] = "Fabricated tool result"
    damaged = bind.artifacts.write_bytes("damaged-receipt", b"\n".join(json_bytes(row) for row in rows), "application/x-ndjson")
    changed = replace(run, artifacts={**run.artifacts, "incident.tool_audit": damaged})
    data = inspect_capture(changed)
    assert not data["checks"]["tool_receipts_preserved"]
    assert not data["checks"]["source_integrity"]


def test_valid_zip_cannot_drop_an_artifact_and_still_pass(tmp_path):
    _, _, run, bind = captured_incident(tmp_path)
    with ZipFile(io.BytesIO(artifact_bytes(run.artifacts["incident.bundle"]))) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(files["manifest.json"])
    del files["native/runtime.jsonl"]
    del manifest["files"]["native/runtime.jsonl"]
    files["manifest.json"] = json_bytes(manifest)
    content = io.BytesIO()
    with ZipFile(content, "w") as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    incomplete = bind.artifacts.write_bytes("incomplete-zip", content.getvalue(), "application/zip")
    changed = replace(run, artifacts={**run.artifacts, "incident.bundle": incomplete})
    assert not inspect_capture(changed)["checks"]["bundle_verified"]
