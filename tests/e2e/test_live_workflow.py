"""A real dependency investigation through each native live runtime.

Small smoke tests remain elsewhere. This scenario requires actual reproduction,
follow-up source inspection, source-backed native events and a portable bundle.
No live backend is substituted and no model/cloud calls occur by default.
"""
from collections.abc import Mapping
from dataclasses import replace
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
import zipfile

import pytest

from tests.e2e.support import assert_usable_result, make_study, plain, zero_resources


FIXTURE = Path(__file__).parent / "fixtures/workflow"
REVISION = "28ace20b140d15c083e1cbc163ee6b7778ba098c"
PROFILES = [pytest.param(name, marks=pytest.mark.profile(name))
            for name in ("codex_local", "claude_local", "vertex_gcp")]


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_artifact(run, name):
    ref = run.artifacts[name]
    path = Path(ref.uri)
    assert path.is_absolute() and path.is_file(), name
    assert sha(path) == ref.sha256, name
    return read_json(path)


def native_record(reference):
    """Resolve an actual raw JSONL record or JSON Pointer, never a fake trace."""
    assert reference is not None and reference.locator
    path = Path(reference.artifact.uri)
    assert sha(path) == reference.artifact.sha256
    locator = reference.locator
    if locator.startswith("line:"):
        number = int(locator.removeprefix("line:"))
        assert number >= 1
        return json.loads(path.read_text(encoding="utf-8").splitlines()[number - 1])
    assert locator.startswith("json:"), "Use line:<1-based> or json:<JSON Pointer>"
    value = read_json(path)
    pointer = locator.removeprefix("json:")
    assert pointer.startswith("/")
    for segment in pointer.split("/")[1:]:
        token = segment.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def contains_receipt(value, receipt):
    """Native transports may encode tool stdout as a string or JSON object."""
    if value == receipt:
        return True
    if isinstance(value, str):
        try:
            return contains_receipt(json.loads(value), receipt)
        except (json.JSONDecodeError, RecursionError):
            return False
    if isinstance(value, Mapping):
        return any(contains_receipt(item, receipt) for item in value.values())
    if isinstance(value, (tuple, list)):
        return any(contains_receipt(item, receipt) for item in value)
    return False


def assert_native_tool_pair(profile, decision, completed, receipt):
    """Check native event roles and IDs, not text quoted by a final answer."""
    action = receipt["action"]
    if profile == "codex_local":
        # This scenario exposes the fixture tools through the native terminal.
        assert decision["type"] == "item.started" and completed["type"] == "item.completed"
        before, after = decision["item"], completed["item"]
        assert before["type"] == after["type"] == "command_execution"
        assert before["id"] == after["id"] and before["id"]
        assert action in before["command"] and "investigation_tool.py" in before["command"]
        assert after["exit_code"] == 0
        assert contains_receipt(after["aggregated_output"], receipt)
    elif profile == "claude_local":
        assert decision["type"] == "assistant" and completed["type"] == "user"
        assert decision["message"]["role"] == "assistant"
        assert completed["message"]["role"] == "user"
        results = [part for part in completed["message"]["content"]
                   if part.get("type") == "tool_result" and contains_receipt(part.get("content"), receipt)]
        assert len(results) == 1 and not results[0].get("is_error", False)
        choices = [part for part in decision["message"]["content"] if part.get("type") == "tool_use"
                   and part["id"] == results[0]["tool_use_id"]]
        assert len(choices) == 1 and action in choices[0]["name"] + json.dumps(choices[0]["input"])
    else:
        # These envelope types are explicitly owned by the small Vertex harness.
        # The response field preserves the original SDK model_dump(mode="json").
        assert profile == "vertex_gcp"
        assert decision["type"] == "sdk.response" and completed["type"] == "harness.tool_result"
        assert decision["call_id"] == completed["model_call_id"] and decision["call_id"]
        assert completed["result"] == receipt
        choices = [part["function_call"] for candidate in decision["response"]["candidates"]
                   for part in candidate["content"]["parts"] if part.get("function_call")]
        assert any(action in part["name"] and part["args"] == receipt["arguments"] for part in choices)


def case_checks(output, receipts):
    """Check data lineage only; explanatory prose has no accuracy oracle."""
    incident = read_json(FIXTURE / "cases.json")
    expected = {case["id"] for case in incident["cases"]}
    rows = output.get("case_results", ()) if isinstance(output, Mapping) else ()
    by_case = {}
    for row in rows:
        if isinstance(row, Mapping):
            by_case.setdefault(row.get("case_id"), []).append(row)
    by_receipt = {row["receipt_id"]: row for row in receipts}
    checks = []
    for case_id in sorted(expected):
        candidates = by_case.get(case_id, [])
        valid, reason = False, "Missing, duplicate or unresolved case evidence"
        if len(candidates) == 1:
            row = candidates[0]
            reproduction = by_receipt.get(row.get("receipt_id"), {})
            native_rows = reproduction.get("result", {}).get("case_results", ())
            native = next((item for item in native_rows if item["case_id"] == case_id), None)
            citation = row.get("source", {})
            source = by_receipt.get(citation.get("receipt_id"), {})
            source_data = source.get("result", {})
            relevant_spans = reproduction.get("result", {}).get("source_hints", ())
            valid = (
                reproduction.get("action") == "run_cases" and native is not None
                and row.get("observed") == native["observed"]
                and source.get("action") == "read_source"
                and source.get("arguments", {}).get("based_on_receipt_id") == reproduction["receipt_id"]
                and citation.get("path") == source_data.get("path")
                and citation.get("start_line") == source_data.get("start_line")
                and citation.get("end_line") == source_data.get("end_line")
                and any(citation.get("path") == hint["path"]
                        and citation.get("start_line", 0) <= hint["end_line"]
                        and citation.get("end_line", 0) >= hint["start_line"] for hint in relevant_spans)
                and isinstance(row.get("explanation"), str) and bool(row["explanation"].strip())
            )
            if valid:
                reason = "Reported value equals executed case; citation resolves to follow-up source read"
        checks.append((case_id, bool(valid), reason))
    if set(by_case) != expected:
        checks.append(("__unexpected_cases__", False, "Report case IDs differ from input case IDs"))
    return checks


class WorkflowEvidenceMetric:
    """An ordinary custom evaluator over captured real artifacts, not constants."""
    def __init__(self, api):
        self.api = api
        self.ref = api.VersionRef(name="e2e.workflow-evidence", revision="v1")
        self.calls = 0

    def compute(self, spec, context, run):
        self.calls += 1
        receipts = read_artifact(run, "workflow.calls")
        checks = case_checks(run.output, receipts)
        evidence = tuple(self.api.EvidenceRef(artifact=run.artifacts[name], description=name)
                         for name in ("workflow.calls", "workflow.report", "native.trace"))
        details = tuple(self.api.Measurement(
            run_id=run.id, metric=spec.id, value=float(valid), status="ok", basis="observed",
            reason=reason, key={"case_id": case_id}, evidence=evidence,
        ) for case_id, valid, reason in checks)
        score = sum(valid for _, valid, _ in checks) / len(checks)
        return self.api.MetricOutput(
            task=self.api.Measurement(run_id=run.id, metric=spec.id, value=score, status="ok",
                                      basis="observed", reason="Fraction of case rows with intact evidence links",
                                      evidence=evidence),
            details=details, evaluation_resources=zero_resources(self.api),
        )


def test_downloaded_workflow_inputs_execute_real_dependency(tmp_path):
    """Fixture preparation check only: this does not claim the library works."""
    manifest = read_json(FIXTURE / "SOURCE_MANIFEST.json")
    assert manifest["revision"] == REVISION and len(manifest["files"]) == 6
    assert manifest["license"] == "BSD-3-Clause"
    for file in manifest["files"]:
        path = FIXTURE / "downloaded" / file["path"]
        assert path.stat().st_size == file["bytes"] and sha(path) == file["sha256"]
        assert REVISION in file["url"]
    case_ids = [row["id"] for row in read_json(FIXTURE / "cases.json")["cases"]]
    command = [sys.executable, "-B", str(FIXTURE / "investigation_tool.py"), "--fixture", str(FIXTURE),
               "--receipts", str(tmp_path), "--invocation", "fixture-preparation", "--nonce", uuid4().hex]
    process = subprocess.run(command, input=json.dumps({"action": "run_cases", "arguments": {"case_ids": case_ids}}),
                             capture_output=True, text=True, encoding="utf-8", timeout=20, check=True)
    receipt = json.loads(process.stdout)
    assert receipt["result"]["implementation"] == "downloaded-python-fallback"
    assert {row["case_id"] for row in receipt["result"]["case_results"]} == set(case_ids)
    assert len({row["observed"] for row in receipt["result"]["case_results"]}) > 1
    hint = receipt["result"]["source_hints"][0]
    process = subprocess.run(command, input=json.dumps({"action": "read_source", "arguments": {
        **hint, "based_on_receipt_id": receipt["receipt_id"]}}), capture_output=True,
        text=True, encoding="utf-8", timeout=20, check=True)
    source_receipt = json.loads(process.stdout)
    source = source_receipt["result"]
    assert source["lines"] == (FIXTURE / "downloaded" / source["path"]).read_text(encoding="utf-8").splitlines()[
        source["start_line"] - 1:source["end_line"]]
    # Verifier self-check with clearly test-owned records, never a mock live run.
    report = {"case_results": [{"case_id": row["case_id"], "observed": row["observed"],
        "receipt_id": receipt["receipt_id"], "explanation": "Fixture verifier self-check",
        "source": {"receipt_id": source_receipt["receipt_id"], "path": source["path"],
                   "start_line": source["start_line"], "end_line": source["end_line"]}}
        for row in receipt["result"]["case_results"]]}
    receipts = [receipt, source_receipt]
    assert all(valid for _, valid, _ in case_checks(report, receipts))
    changed = deepcopy(report)
    changed["case_results"][0]["observed"] = "altered after capture"
    assert sum(valid for _, valid, _ in case_checks(changed, receipts)) == 5
    assert not any(valid for _, valid, _ in case_checks(report, [receipt]))
    changed = deepcopy(report)
    changed["case_results"][0]["receipt_id"] = "receipt-from-another-run"
    assert sum(valid for _, valid, _ in case_checks(changed, receipts)) == 5


@pytest.mark.live
@pytest.mark.parametrize("profile_name", PROFILES)
def test_native_dependency_investigation_preserves_workflow_and_deliverables(
    api, live_backend, live_profile, tmp_path, profile_name,
):
    assert live_profile["id"] == profile_name
    nonce = uuid4().hex
    task_id = "markupsafe-callsite-investigation"
    settings = {**live_profile["settings"], "workflow_e2e": {
        "schema_version": "aef-live-workflow/1", "fixture_dir": str(FIXTURE.resolve()),
        "artifact_dir": str((tmp_path / "downloaded-native-bundles").resolve()),
        "source_manifest_sha256": sha(FIXTURE / "SOURCE_MANIFEST.json"),
        "cases_sha256": sha(FIXTURE / "cases.json"),
        "tool_sha256": sha(FIXTURE / "investigation_tool.py"),
        "tool_names": ["workflow.inventory", "workflow.run_cases", "workflow.read_source"],
        "mission_file": str((FIXTURE / "MISSION.md").resolve()),
        "response_schema_file": str((FIXTURE / "report.schema.json").resolve()),
        "max_tool_calls": 16,
    }}
    if profile_name == "vertex_gcp":
        # The owned harness controls every SDK dispatch. Native CLI command
        # selections do not establish the number of hidden model invocations.
        settings["workflow_e2e"]["max_model_calls"] = 10
    study, evaluators, reducers = make_study(
        api, project_id=profile_name + "/dependency-investigation", backend_ref=live_profile["backend_ref"],
        units=[{"task_id": task_id, "nonce": nonce, "incident_file": "cases.json"}],
        input_columns={"units": ("task_id", "nonce", "incident_file")}, settings=settings,
        wall_time_s=600,
    )
    metric = WorkflowEvidenceMetric(api)
    evaluators[metric.ref.name] = metric
    suite = replace(study.suite, metrics=(*study.suite.metrics, api.MetricSpec(
        id="evidence_integrity", source=api.EvaluatorSource(ref=metric.ref), output_type="float", role="diagnostic")),
        summaries=(*study.suite.summaries, api.SummarySpec(id="evidence_integrity_mean", metric="evidence_integrity", reducer="mean")))
    study = replace(study, suite=suite)
    result = study.evaluate(backends={live_backend.ref.name: live_backend}, evaluators=evaluators, reducers=reducers)
    assert len(result.runs.runs) == 2  # A/B bookkeeping; no superiority hypothesis.
    required = {"native.trace", "native.receipt", "workflow.report", "workflow.calls", "workflow.inputs",
                "workflow.source_manifest", "workflow.bundle"}
    native_ids, all_receipt_ids = set(), set()
    for run in result.runs.runs:
        assert run.status == "completed" and run.output_state == "available", run.error
        assert required <= run.artifacts.keys()
        assert run.output["task_id"] == task_id and run.output["nonce"] == nonce
        assert plain(run.output) == read_artifact(run, "workflow.report")
        assert read_artifact(run, "workflow.inputs") == read_json(FIXTURE / "cases.json")
        assert read_artifact(run, "workflow.source_manifest") == read_json(FIXTURE / "SOURCE_MANIFEST.json")
        receipt = read_artifact(run, "native.receipt")
        assert receipt["profile"] == profile_name and receipt["native_id"] not in native_ids
        native_ids.add(receipt["native_id"])
        assert receipt["native_id"] == run.native_refs["invocation_id"]
        assert receipt["runtime_revision"] and receipt["model"]["provider"] and receipt["model"]["id"]
        if profile_name == "vertex_gcp":
            assert receipt["deployment"]["kind"] == "gcp"
            assert receipt["deployment"]["project"] == live_profile["deployment"]["project"]
            assert receipt["model"]["provider"] == "vertex"
            native_rows = [json.loads(line) for line in Path(run.artifacts["native.trace"].uri)
                           .read_text(encoding="utf-8").splitlines() if line.strip()]
            requests = [row for row in native_rows if row.get("type") == "sdk.request"]
            assert 0 < len(requests) <= settings["workflow_e2e"]["max_model_calls"]
            assert len({row["call_id"] for row in requests}) == len(requests)
            assert all(row["call_id"] for row in requests)
        calls = read_artifact(run, "workflow.calls")
        assert calls and len(calls) <= settings["workflow_e2e"]["max_tool_calls"]
        by_receipt = {row["receipt_id"]: row for row in calls}
        assert len(by_receipt) == len(calls) and not all_receipt_ids.intersection(by_receipt)
        all_receipt_ids.update(by_receipt)
        all_tools = [event for event in run.events if event.kind == "tool_call"
                     and event.fields.get("name", "").startswith("workflow.")]
        assert len(all_tools) <= settings["workflow_e2e"]["max_tool_calls"]
        events = [event for event in all_tools if isinstance(event.fields.get("result"), Mapping)
                  and event.fields["result"].get("schema_version") == "aef-workflow-tool/1"]
        assert len(events) == len(calls), "A tool receipt maps to exactly one completed tool event"
        assert {event.fields["result"]["receipt_id"] for event in events} == set(by_receipt)
        event_by_id = {event.id: event for event in run.events}
        event_by_receipt = {event.fields["result"]["receipt_id"]: event for event in events}
        actions = {row["action"] for row in calls}
        assert {"inventory", "run_cases", "read_source"} <= actions
        for call in calls:
            assert call["invocation_id"] == run.id and call["nonce"] == nonce
            assert call["source_manifest_sha256"] == sha(FIXTURE / "SOURCE_MANIFEST.json")
            assert call["tool_sha256"] == sha(FIXTURE / "investigation_tool.py")
            event = event_by_receipt[call["receipt_id"]]
            assert plain(event.fields["result"]) == call and event.inputs and event.outputs
            assert event.fields["name"] == "workflow." + call["action"]
            assert plain(event.fields["arguments"]) == call["arguments"]
            assert event.source.artifact == run.artifacts["native.trace"]
            decision = event_by_id[event.fields["decision_event_id"]]
            assert decision.kind == "model_response" and decision.source is not None
            assert decision.source.artifact == run.artifacts["native.trace"]
            assert_native_tool_pair(profile_name, native_record(decision.source), native_record(event.source), call)
            assert run.events.index(decision) < run.events.index(event)
            assert decision.execution_id == event.execution_id
            if call["action"] == "read_source":
                based_on = call["arguments"].get("based_on_receipt_id")
                if based_on:
                    preceding = event_by_receipt[based_on]
                    assert by_receipt[based_on]["action"] == "run_cases"
                    assert run.events.index(preceding) < run.events.index(decision)
                source = call["result"]
                path = (FIXTURE / "downloaded" / source["path"]).resolve()
                assert path.is_relative_to((FIXTURE / "downloaded").resolve())
                assert source["sha256"] == sha(path)
                assert source["lines"] == path.read_text(encoding="utf-8").splitlines()[
                    source["start_line"] - 1:source["end_line"]]
        assert all(valid for _, valid, _ in case_checks(run.output, calls)), case_checks(run.output, calls)
        measurements = [m for m in result.measurements if m.run_id == run.id and m.metric == "evidence_integrity"]
        assert len([m for m in measurements if m.key is not None]) == 6
        assert next(m for m in measurements if m.key is None).value == 1.0
        assert all(m.evidence and m.reason for m in measurements)
        bundle = run.artifacts["workflow.bundle"]
        assert sha(bundle.uri) == bundle.sha256
        with zipfile.ZipFile(bundle.uri) as archive:
            names = archive.namelist()
            assert len(names) == len(set(names))
            assert all(not Path(name).is_absolute() and ".." not in Path(name).parts for name in names)
            index = json.loads(archive.read("manifest.json"))
            assert index["run_id"] == run.id and index["native_id"] == receipt["native_id"]
            entries = {row["artifact"]: row for row in index["artifacts"]}
            assert required - {"workflow.bundle"} <= entries.keys()
            for name in required - {"workflow.bundle"}:
                entry = entries[name]
                assert hashlib.sha256(archive.read(entry["path"])).hexdigest() == run.artifacts[name].sha256 == entry["sha256"]
            for row in read_json(FIXTURE / "SOURCE_MANIFEST.json")["files"]:
                assert hashlib.sha256(archive.read("input/downloaded/" + row["path"])).hexdigest() == row["sha256"]
        for observation in (run.resources().cost_usd, run.duration_s()):
            if observation.status == "unknown":
                assert observation.value is None and observation.reason
    assert metric.calls == 2
    loaded = assert_usable_result(api, study, result, root=tmp_path / "consumer")
    assert [plain(run.events) for run in loaded.runs.runs] == [plain(run.events) for run in result.runs.runs]
    for run in loaded.runs.runs:
        assert run.artifacts["workflow.bundle"] == result.runs.get(run.id).artifacts["workflow.bundle"]
        assert any(m.metric == "evidence_integrity" for m in loaded.explain(run.id).measurements)
