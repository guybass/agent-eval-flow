"""Full native OpenSRE investigation: downloaded logs -> tools -> report -> AEF.

Acceptance concerns evidence/record transport, not the correctness of a root
cause. A final response without the actual multi-iteration trace cannot pass.
"""
from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
from io import BytesIO
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname
import zipfile

import pytest

from tests.e2e.fixtures.opensre.incident_store import TOOL_NAMES, verify_inputs
from tests.e2e.support import assert_usable_result, make_study, plain, zero_resources

pytestmark = [pytest.mark.live, pytest.mark.profile("opensre_gcp")]
FIXTURES = Path(__file__).parents[1] / "fixtures" / "opensre"
UPSTREAM_REVISION = "1a81e1a1b378ecc9aeb211fcaeecb0f5c93548e4"
ARTIFACT_FILES = {
    "native.turn": "native/turn.json", "native.trace": "native/runtime.jsonl",
    "native.receipt": "native/receipt.json", "native.stderr": "native/stderr.txt",
    "incident.tool_audit": "incident/tool-audit.jsonl", "incident.report": "incident/investigation.json",
    "incident.source_logs": "inputs/HDFS_2k.log", "incident.context": "inputs/context.json",
    "incident.provenance": "inputs/SOURCES.json",
    "incident.license": "inputs/LOGHUB_LICENSE",
}


def _materialized_bytes(artifact):
    """Cloud worker evidence must really be downloaded to a verified local cache."""
    if artifact.uri.startswith("file:"):
        parsed = urlparse(artifact.uri)
        assert parsed.netloc in ("", "localhost")
        path = Path(url2pathname(parsed.path))
    else:
        path = Path(artifact.uri)
    assert path.is_absolute(), "Cloud evidence must be materialized locally"
    content = path.read_bytes()
    assert artifact.sha256 == hashlib.sha256(content).hexdigest()
    return content


def _jsonl(content):
    return [json.loads(line) for line in content.decode().splitlines() if line.strip()]


class InvestigationMetric:
    """Count captured tool receipts with exact source locations; no quality score."""

    def __init__(self, api):
        self.api = api
        self.ref = api.VersionRef(name="e2e.opensre.tool_receipts", revision="v1")

    def compute(self, spec, context, run):
        api = self.api
        audit = _jsonl(_materialized_bytes(run.artifacts["incident.tool_audit"]))
        evidence = tuple(api.EvidenceRef(artifact=run.artifacts["incident.tool_audit"],
            locator=f"line:{index + 1}", description=f"Native call {row['tool_name']}")
            for index, row in enumerate(audit))
        value = context.measurements[spec.depends_on[0]].value + spec.params["add"] if spec.depends_on else len(audit)
        measurement = api.Measurement(run_id=run.id, metric=spec.id, value=value, status="ok",
            basis="observed", reason="Number of recorded tool receipts, with the configured dependency transform",
            evidence=evidence)
        return api.MetricOutput(task=measurement, details=(replace(measurement, key={"row": 0}),),
                                evaluation_resources=zero_resources(api))


def _check_investigation(run, invocation_id, content, source_rows):
    trace, audit = _jsonl(content["native.trace"]), _jsonl(content["incident.tool_audit"])
    assert trace and audit
    assert [row["sequence"] for row in trace] == list(range(len(trace)))
    assert all(row["invocation_id"] == invocation_id and row["loop_id"] for row in trace)
    assert [row["sequence"] for row in audit] == list(range(len(audit)))
    assert all(row["invocation_id"] == invocation_id for row in audit)
    assert {row["tool_name"] for row in audit} == set(TOOL_NAMES)
    assert len(audit) >= 7, "Six tools and a continuation page must actually run"
    starts, ends = {}, {}
    for row in trace:
        event = row["event"]  # Serialized upstream typed RuntimeEvent, unrenamed.
        if event["type"] in ("tool_execution_start", "tool_execution_end"):
            key = (row["loop_id"], event["tool_call_id"])
            target = starts if event["type"] == "tool_execution_start" else ends
            assert key not in target
            target[key] = row
    assert starts.keys() == ends.keys(), "No missing tool completions"
    iterations = {(row["loop_id"], row["event"]["iteration"]) for row in trace
                  if row["event"]["type"] == "provider_request_start"}
    assert len(iterations) >= 3, "Need a real observe-and-follow-up workflow"
    successful_calls = {}
    for key, start in starts.items():
        end = ends[key]
        assert start["sequence"] < end["sequence"]
        assert start["event"]["tool_name"] == end["event"]["tool_name"]
        assert start["event"]["args"] == end["event"]["args"]
        if not end["event"]["is_error"]:
            successful_calls[key] = (start, end)
    receipts = {}
    for row in audit:
        assert row["schema_version"] == "aef-incident-tool-receipt/1"
        start, end = successful_calls[(row["loop_id"], row["tool_call_id"])]
        assert row["tool_name"] == start["event"]["tool_name"]
        assert row["arguments"] == start["event"]["args"]
        # Fixture functions return ordinary dictionaries; the native compatibility
        # payload must retain this dictionary (no final-answer reconstruction).
        assert end["event"]["result"] == row["result"]
        assert row["started_at"] <= row["ended_at"]
        result = row["result"]
        assert result["receipt_id"] not in receipts
        receipts[result["receipt_id"]] = row
    snapshots = {row["result"]["snapshot_id"] for row in audit}
    assert len(snapshots) == 1
    opening = next(row for row in audit if row["tool_name"] == "fixture_incident_open")
    for row in audit:
        if row["tool_name"] != "fixture_incident_open":
            assert row["arguments"]["snapshot_id"] == opening["result"]["snapshot_id"]
    searches = [row for row in audit if row["tool_name"] == "fixture_logs_search"]
    continuation = [row for row in searches if row["arguments"].get("cursor")]
    assert continuation, "A claimed search without a fetched next page is insufficient"
    for page in continuation:
        previous = receipts[page["result"]["previous_receipt_id"]]
        assert page["arguments"]["cursor"] == previous["result"]["next_cursor"]
        assert page["result"]["offset"] == previous["result"]["offset"] + 4
        prev_end = successful_calls[(previous["loop_id"], previous["tool_call_id"])][1]
        next_start = successful_calls[(page["loop_id"], page["tool_call_id"])][0]
        assert prev_end["sequence"] < next_start["sequence"]
        assert (prev_end["loop_id"], prev_end["event"]["iteration"]) != (
            next_start["loop_id"], next_start["event"]["iteration"])
    observed_lines = []
    for page in searches:
        for row in page["result"]["rows"]:
            assert row == source_rows[row["line_id"] - 1]
            observed_lines.append(row["line_id"])
    assert len(set(observed_lines)) >= 8
    metrics = next(row["result"] for row in audit if row["tool_name"] == "fixture_metrics_query")
    context = json.loads(content["incident.context"])
    date = context["window"]["date"]
    expected = Counter((row["time"][:2], row["level"]) for row in source_rows if row["date"] == date)
    assert {(row["hour"], row["level"]): row["count"] for row in metrics["series"]} == expected
    changes = next(row["result"] for row in audit if row["tool_name"] == "fixture_changes_list")
    assert changes["changes"] == context["changes"]
    topology = next(row["result"] for row in audit if row["tool_name"] == "fixture_topology_describe")
    assert len(topology["nodes"]) > 10 and len(topology["components"]) > 1
    report = json.loads(content["incident.report"])
    assert report["incident_id"] == context["incident_id"]
    assert report["summary"].strip()
    for key in ("hypotheses", "timeline", "evidence", "limitations", "next_actions"):
        assert isinstance(report[key], list) and report[key]
    cited = {row["receipt_id"] for row in report["evidence"]}
    assert cited <= receipts.keys()
    assert {receipts[key]["tool_name"] for key in cited} >= {
        "fixture_logs_search", "fixture_metrics_query", "fixture_changes_list", "fixture_topology_describe"}
    writes = [row for row in audit if row["tool_name"] == "fixture_report_write"]
    assert writes[-1]["arguments"]["report"] == report
    assert writes[-1]["result"]["sha256"] == hashlib.sha256(content["incident.report"]).hexdigest()
    normalized_events = [event for event in run.events if "native_sequence" in event.fields]
    normalized = {event.fields["native_sequence"]: event for event in normalized_events}
    assert len(normalized) == len(normalized_events), "Duplicate native event normalization"
    assert set(normalized) == set(range(len(trace)))
    for row in trace:
        event = normalized[row["sequence"]]
        assert plain(event.fields["native"]) == row["event"]
        assert event.execution_id in {execution.id for execution in run.executions}
        assert event.source is not None
        assert event.source.artifact.sha256 == run.artifacts["native.trace"].sha256
        assert event.source.locator == f"line:{row['sequence'] + 1}"
        assert any(ref.artifact.sha256 == run.artifacts["native.trace"].sha256
                   for ref in (*event.inputs, *event.outputs))
    return snapshots.pop(), len(trace), len(audit)


def test_native_opensre_on_gcp_produces_usable_library_objects(api, live_backend, live_profile, tmp_path):
    source_rows = verify_inputs(FIXTURES)
    units = json.loads((FIXTURES / "incidents.json").read_text())
    settings = {**live_profile.get("settings", {}),
                "native_entrypoint": "core.agent_harness.AgentSession.chat_until_goal",
                "native_capture": "typed_runtime_events_and_tool_receipts",
                "required_upstream_revision": UPSTREAM_REVISION,
                "prompt_column": "prompt", "fixture_dir": str(FIXTURES.resolve()),
                "fixture_store": "incident_store.IncidentStore", "fixture_tool_names": TOOL_NAMES,
                "native_max_iterations": 18, "artifact_bundle": "incident.bundle"}
    study, evaluators, reducers = make_study(api, project_id="opensre-hdfs-investigation",
        backend_ref=live_profile["backend_ref"], units=units,
        input_columns={"units": ("task_id", "incident_id", "prompt")}, settings=settings, wall_time_s=600)
    metric = InvestigationMetric(api)
    study = replace(study, suite=replace(study.suite, version="native-tool-receipts-v1",
        metrics=tuple(replace(spec, source=api.EvaluatorSource(ref=metric.ref)) for spec in study.suite.metrics)))
    evaluators = {metric.ref.name: metric}
    result = study.evaluate(backends={live_backend.ref.name: live_backend}, evaluators=evaluators, reducers=reducers)
    assert len(result.runs.runs) == len(study.plan().assignments) == 2
    invocations, snapshots, retained = set(), set(), {}
    for run in result.runs.runs:
        assert run.status == "completed" and run.output_state == "available"
        assert set(ARTIFACT_FILES) | {"incident.bundle"} <= run.artifacts.keys()
        content = {name: _materialized_bytes(run.artifacts[name]) for name in ARTIFACT_FILES}
        native = json.loads(content["native.turn"])
        assert isinstance(run.output, Mapping) and plain(run.output) == native
        assert {"final_intent", "action_result", "assistant_response_text"} <= native.keys()
        action = native["action_result"]
        assert action["accounting_status"] == "completed" and not action["hit_iteration_cap"]
        assert not action["cancelled"]
        assert (native["assistant_response_text"] or action["response_text"]).strip()
        receipt = json.loads(content["native.receipt"])
        assert receipt["schema_version"] == "aef-opensre-investigation-receipt/1"
        assert receipt["agent"]["revision"] == UPSTREAM_REVISION
        assert receipt["agent"]["entrypoint"] == settings["native_entrypoint"]
        assert receipt["capture"]["runtime_callback"] == "core.agent.Agent.on_runtime_event"
        assert receipt["capture"]["complete"] is True
        assert receipt["deployment"]["provider"] == "gcp" and receipt["deployment"]["execution_id"]
        assert receipt["model"]["provider"] == "vertex-ai"
        assert all(receipt["model"][key] for key in ("project_id", "location", "resolved_models"))
        assert receipt["process"]["argv"] and receipt["process"]["exit_code"] == 0
        invocation = receipt["invocation_id"]
        assert invocation not in invocations and run.native_refs["invocation_id"] == invocation
        invocations.add(invocation)
        assert content["incident.source_logs"] == (FIXTURES / "upstream/HDFS_2k.log").read_bytes()
        assert content["incident.context"] == (FIXTURES / "context.json").read_bytes()
        assert content["incident.provenance"] == (FIXTURES / "SOURCES.json").read_bytes()
        assert content["incident.license"] == (FIXTURES / "upstream/LOGHUB_LICENSE").read_bytes()
        snapshot, trace_count, tool_count = _check_investigation(run, invocation, content, source_rows)
        assert snapshot not in snapshots
        snapshots.add(snapshot)
        assert receipt["capture"]["event_count"] == trace_count
        assert receipt["capture"]["tool_receipt_count"] == tool_count
        values = {item.metric: item for item in result.measurements if item.run_id == run.id and item.key is None}
        assert values["wiring"].value == tool_count
        assert len(values["wiring"].evidence) == tool_count
        bundle_bytes = _materialized_bytes(run.artifacts["incident.bundle"])
        with zipfile.ZipFile(BytesIO(bundle_bytes)) as bundle:
            names = bundle.namelist()
            assert len(names) == len(set(names)) and "manifest.json" in names
            manifest = json.loads(bundle.read("manifest.json"))
            assert manifest["invocation_id"] == invocation
            for name, relative_path in ARTIFACT_FILES.items():
                assert bundle.read(relative_path) == content[name]
                assert manifest["files"][relative_path] == run.artifacts[name].sha256
        assert run.started_at is not None and run.ended_at >= run.started_at
        # Typed runtime events do not imply billing. Preserve any separately
        # captured native usage; otherwise retain unknown with an explicit reason.
        for item in (run.resources().cost_usd, run.resources().input_tokens, run.resources().output_tokens):
            if item.status == "unknown":
                assert item.value is None and item.reason
            else:
                assert item.status in ("observed", "estimated") and item.evidence
                if item.status == "estimated":
                    assert item.reason
        retained[run.id] = {name: ref.sha256 for name, ref in run.artifacts.items()}
    loaded = assert_usable_result(api, study, result, root=tmp_path)
    for run in loaded.runs.runs:
        assert {name: ref.sha256 for name, ref in run.artifacts.items()} == retained[run.id]
        for name in (*ARTIFACT_FILES, "incident.bundle"):
            _materialized_bytes(run.artifacts[name])
