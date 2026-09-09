"""Real multi-stage OpenKritt review; native iterations and tool evidence survive.

The output/settings names below define OUR proposed adapter contract. They are
not claimed to be native OpenKritt fields. See the fixture README for mapping.
"""
from __future__ import annotations

import hashlib
import io
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse
from urllib.request import url2pathname
from uuid import uuid4
from zipfile import ZipFile

import pytest

from tests.e2e.support import assert_usable_result, make_study, zero_resources


pytestmark = [pytest.mark.live, pytest.mark.profile("openkritt_gcp")]

UPSTREAM_REVISION = "1ba10d410b4a0a309bacf5bc2d14ec1c27ed8a09"
SOURCE_REVISION = "2c1b30d0503cfb064f1cb252e6614a06915a362a"
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "openkritt"


def _bytes(ref) -> bytes:
    """The live fixture must materialize evidence locally before returning."""
    parsed = urlparse(ref.uri)
    if parsed.scheme == "file":
        assert parsed.netloc in ("", "localhost"), "No remote file shares in this fixture."
        path = Path(url2pathname(parsed.path))
    else:
        # Plain Windows drive paths also have a one-letter parsed scheme.
        path = Path(ref.uri)
        assert path.is_absolute(), "Profile must materialize cloud evidence locally."
    payload = path.read_bytes()
    assert ref.sha256 == hashlib.sha256(payload).hexdigest()
    return payload


def _json_shape(value):
    """Compare immutable library containers with ordinary native JSON values."""
    if isinstance(value, Mapping):
        return {key: _json_shape(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_shape(item) for item in value]
    return value


def verify_inputs():
    """Offline integrity check; never imports or executes downloaded source."""
    manifest = json.loads((FIXTURE / "source_manifest.json").read_text(encoding="utf-8"))
    assert manifest["revision"] == SOURCE_REVISION
    assert manifest["license"] == "BSD-3-Clause"
    root = FIXTURE / "review_target"
    expected = {entry["path"] for entry in manifest["files"]}
    assert len(expected) == len(manifest["files"]) == 22
    assert {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()} == expected
    for entry in manifest["files"]:
        relative = PurePosixPath(entry["path"])
        assert not relative.is_absolute() and ".." not in relative.parts
        payload = (root / entry["path"]).read_bytes()
        assert len(payload) == entry["bytes"]
        assert hashlib.sha256(payload).hexdigest() == entry["sha256"]
        assert entry["url"] == (
            f"https://raw.githubusercontent.com/pallets/flask/{SOURCE_REVISION}/"
            f"examples/tutorial/{entry['path']}"
        )
    workflow = json.loads((FIXTURE / "workflow.json").read_text(encoding="utf-8"))
    assert workflow["kind"] == "open-kritt-workflow" and workflow["version"] == 2
    levels = workflow["workflow"]["levels"]
    assert [level["depth"] for level in levels] == [0, 1, 2]
    assert [len(level["steps"]) for level in levels] == [1, 2, 1]
    assert [level["consumesAll"] for level in levels] == [False, True, True]
    assert {step["clientId"] for level in levels for step in level["steps"]} == {
        "map-application", "review-authz", "review-storage", "synthesize-findings"
    }
    for level in levels[1:]:
        assert all(f"{{{{multi_output_depth_{level['depth'] - 1}}}}}" in step["content"]
                   for step in level["steps"])
    return manifest, workflow


def _records(ref):
    """Retain line numbers in native streams, including any non-JSON diagnostics."""
    records = []
    for number, line in enumerate(_bytes(ref).decode("utf-8").splitlines(), 1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append((number, record))
    return records


def _native_tool_calls(ref, protocol):
    """Two declared native stream dialects; other harnesses need a real decoder."""
    records = _records(ref)
    calls = []
    if protocol == "codex-jsonl":
        for line, record in records:
            item = record.get("item", {})
            if (record.get("type") == "item.completed" and isinstance(item, dict)
                    and item.get("type") == "command_execution"):
                calls.append({
                    "native_id": item["id"], "name": "command_execution",
                    "arguments": {"command": item["command"]},
                    "result": {"output": item["aggregated_output"], "exit_code": item["exit_code"]},
                    "input_line": line, "output_line": line,
                })
    elif protocol == "claude-stream-json":
        invocations = {}
        for line, record in records:
            message = record.get("message", {})
            content = message.get("content", []) if isinstance(message, dict) else []
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    invocations[block["id"]] = (line, block)
                elif block.get("type") == "tool_result":
                    assert block["tool_use_id"] in invocations, "Tool result lost its invocation."
                    input_line, invocation = invocations[block["tool_use_id"]]
                    calls.append({
                        "native_id": invocation["id"], "name": invocation["name"],
                        "arguments": invocation["input"],
                        "result": {"content": block["content"], "is_error": block.get("is_error", False)},
                        "input_line": input_line, "output_line": line,
                    })
    else:
        pytest.fail(f"Unsupported real tool-trace protocol: {protocol}")
    return calls


class WorkflowAttemptsMetric:
    """A caller-defined observable activity count, not a detection-quality score."""

    def __init__(self, api):
        self.api = api
        self.ref = api.VersionRef(name="e2e.openkritt.completed_workflow_attempts", revision="v1")

    def compute(self, spec, context, run):
        api = self.api
        ref = run.artifacts["openkritt.step_metadata"]
        metadata = json.loads(_bytes(ref))
        completed = [(index, row) for index, row in enumerate(metadata)
                     if row["kind"] == "step" and row["status"] == "completed"]
        evidence = tuple(api.EvidenceRef(artifact=ref, locator=f"/{index}",
                         description=f"Completed native step {row['step_id']}, repeat {row['repeat_run']}")
                         for index, row in completed)
        value = (context.measurements[spec.depends_on[0]].value + spec.params["add"]
                 if spec.depends_on else len(completed))
        task = api.Measurement(run_id=run.id, metric=spec.id, value=value, status="ok", basis="observed",
            reason="Completed native workflow attempts, with the configured dependency transform", evidence=evidence)
        # The existing +7 metric remains a dependency-wiring probe. The base
        # measurement now comes from actual scan activity, not injected 101.
        details = (replace(task, key={"row": 0}),) if spec.depends_on else tuple(
            api.Measurement(run_id=run.id, metric=spec.id, value=1, status="ok", basis="observed",
                reason="This captured workflow attempt completed", key={"row": index}, evidence=(source,))
            for index, source in enumerate(evidence)
        )
        return api.MetricOutput(task=task, details=details, evaluation_resources=zero_resources(api))


def _assert_downloadable_bundle(run, source_manifest):
    """A self-contained download exists even if native findings/export are empty."""
    with ZipFile(io.BytesIO(_bytes(run.artifacts["openkritt.bundle"]))) as archive:
        infos = archive.infolist()
        paths = [info.filename for info in infos]
        assert len(paths) == len(set(paths)), "Ambiguous duplicate archive member."
        for path in paths:
            parts = PurePosixPath(path)
            assert not parts.is_absolute() and ".." not in parts.parts
            assert "\\" not in path and ":" not in path and str(parts) == path.rstrip("/")
        files = {info.filename for info in infos if not info.is_dir()}
        assert "manifest.json" in files
        index = json.loads(archive.read("manifest.json"))
        assert index["schema_version"] == "aef-openkritt-evidence-bundle/1"
        assert index["scan_id"] == run.native_refs["scan_id"]
        assert index["run_id"] == run.id and index["assignment_id"] == run.assignment_id
        listed = {row["path"]: row for row in index["files"]}
        assert len(listed) == len(index["files"])
        assert set(listed) == files - {"manifest.json"}
        for path, row in listed.items():
            payload = archive.read(path)
            assert hashlib.sha256(payload).hexdigest() == row["sha256"]
            assert len(payload) == row["bytes"]

        assert archive.read("inputs/source_manifest.json") == (FIXTURE / "source_manifest.json").read_bytes()
        assert archive.read("configuration/workflow.json") == (FIXTURE / "workflow.json").read_bytes()
        for entry in source_manifest["files"]:
            assert archive.read("inputs/review_target/" + entry["path"]) == (
                FIXTURE / "review_target" / entry["path"]
            ).read_bytes()
        # Every artifact including the capture index and every referenced
        # stdout/stderr is available without an external URI or the cloud host.
        mapping = index["artifacts"]
        assert set(mapping) == set(run.artifacts) - {"openkritt.bundle"}
        assert len(set(mapping.values())) == len(mapping)
        for key, path in mapping.items():
            assert path in listed
            assert archive.read(path) == _bytes(run.artifacts[key])
        capture = json.loads(archive.read(mapping["openkritt.capture"]))
        for trace in capture["traces"]:
            assert trace["stdout_artifact"] in mapping and trace["stderr_artifact"] in mapping


def _assert_trajectory(run, manifest, workflow):
    """Verify actual native payloads, branch joins, repeats and raw tool results."""
    capture = json.loads(_bytes(run.artifacts["openkritt.capture"]))
    assert capture["schema_version"] == "aef-openkritt-capture/2"
    assert str(capture["scan_id"]) == run.native_refs["scan_id"]
    assert capture["source_manifest_sha256"] == hashlib.sha256(
        (FIXTURE / "source_manifest.json").read_bytes()
    ).hexdigest()
    assert capture["workflow_sha256"] == hashlib.sha256((FIXTURE / "workflow.json").read_bytes()).hexdigest()
    assert {row["path"]: row["sha256"] for row in capture["staged_files"]} == {
        row["path"]: row["sha256"] for row in manifest["files"]
    }
    assert capture["metadata_inventory_complete"] is True
    assert capture["observer"]["name"] and capture["observer"]["revision"]

    native_workflow = json.loads(_bytes(run.artifacts["openkritt.workflow"]))
    steps = native_workflow["steps"]
    expected_steps = {step["name"]: (level, step)
                      for level in workflow["workflow"]["levels"] for step in level["steps"]}
    assert len(steps) == len(expected_steps) == 4
    assert {step["name"] for step in steps} == set(expected_steps)
    by_id = {str(step["id"]): step for step in steps}
    for step in steps:
        level, expected = expected_steps[step["name"]]
        assert step["depth"] == level["depth"]
        assert step["content"] == expected["content"]
        assert step["consumesAll"] == level["consumesAll"]
        assert step["outputFormat"] == level["outputFormat"]

    # These are row_to_json exports of the existing PostgreSQL tables, hence
    # snake_case native columns. They are not a fictional HTTP trajectory API.
    metadata = json.loads(_bytes(run.artifacts["openkritt.step_metadata"]))
    results = json.loads(_bytes(run.artifacts["openkritt.step_results"]))
    assert isinstance(metadata, list) and isinstance(results, list)
    metadata_by_id = {str(row["id"]): row for row in metadata}
    assert len(metadata_by_id) == len(metadata)
    assert all(str(row["scan_id"]) == run.native_refs["scan_id"] for row in metadata + results)
    executions = {execution.native_refs["step_metadata_id"]: execution
                  for execution in run.executions if "step_metadata_id" in execution.native_refs}
    assert set(executions) == set(metadata_by_id), "Include retries and post-processing metadata too."
    assert len(executions) == sum("step_metadata_id" in item.native_refs for item in run.executions)
    assert run.execution_inventory_complete.status == "observed"
    assert run.execution_inventory_complete.value is True

    workflow_attempts = [row for row in metadata if row["kind"] == "step"]
    completed = [row for row in workflow_attempts if row["status"] == "completed"]
    # Native cumulative repeats are DIFFERENT from AEF's independent repetitions.
    assert Counter((str(row["step_id"]), row["repeat_run"]) for row in completed) == Counter({
        (step_id, repeat): 1 for step_id in by_id for repeat in (1, 2)
    })
    for row in workflow_attempts:
        execution = executions[str(row["id"])]
        assert execution.native_refs["step_id"] == str(row["step_id"])
        assert execution.native_refs["repeat_run"] == str(row["repeat_run"])
        assert row["model"] and row["harness"] and row["model_provider"]
        if row["status"] != "completed":
            assert execution.status != "completed"
            continue
        assert execution.status == "completed"
        assert row["prompt_filled"] and row["prompt_template"]
        assert row["run_started_at"] and row["run_time_ms"] is not None
        assert float(row["run_time_ms"]) >= 0
        payload = row["output_json"]
        assert isinstance(payload, dict) and isinstance(payload["results"], list)
        assert payload["stub"] == row["stub"]
        if row["stub"]:
            assert not payload["results"] and payload["stub_explanation"].strip()
        else:
            assert payload["results"]
        if row["repeat_run"] == 2:
            assert "This is repeat run 2." in row["prompt_filled"]
            first = next(previous for previous in completed
                         if str(previous["step_id"]) == str(row["step_id"])
                         and previous["repeat_run"] == 1)
            assert json.dumps(first["output_json"]["results"], sort_keys=True, indent=2) in row["prompt_filled"]
        events = [event for event in run.events if event.kind == "workflow_step"
                  and event.fields.get("step_metadata_id") == str(row["id"])]
        assert len(events) == 1
        event = events[0]
        assert event.execution_id == execution.id
        assert _json_shape(event.fields["output"]) == payload
        assert event.fields["repeat_run"] == row["repeat_run"]
        assert event.source is not None
        assert event.source.artifact == run.artifacts["openkritt.step_metadata"]
        assert event.source.locator == f"/{metadata.index(row)}"

    for step_id, step in by_id.items():
        step_results = [row for row in results if str(row["step_id"]) == step_id]
        payload_results = [answer for row in completed if str(row["step_id"]) == step_id
                           for answer in row["output_json"]["results"]]
        if step["depth"] < 2:
            assert step_results, "Maps and review records exist even when there are no findings."
            assert Counter(json.dumps(row["json_answer"], sort_keys=True) for row in step_results) == Counter(
                json.dumps(answer, sort_keys=True) for answer in payload_results
            )
            for result_row in step_results:
                producer = next(row for row in completed
                                if str(row["step_id"]) == step_id
                                and row["repeat_run"] == result_row["repeat_run"]
                                and row["prev_id"] == result_row["prev_id"]
                                and row["prev_table"] == result_row["prev_table"])
                assert result_row["json_answer"] in producer["output_json"]["results"]
            for answer in payload_results:
                assert isinstance(answer["files_read"], list) and answer["files_read"]
                assert all(path in {entry["path"] for entry in manifest["files"]}
                           for path in answer["files_read"])
        if step["depth"] > 0:
            prior_ids = {identifier for identifier, previous in by_id.items()
                         if previous["depth"] == step["depth"] - 1}
            previous_answers = [row["json_answer"] for row in results if str(row["step_id"]) in prior_ids]
            assert previous_answers
            # Native queue.py waits for ALL previous-depth repeats before
            # building consume-all input; therefore even downstream repeat 1
            # receives previous-depth repeats 1+2. Native render_value uses
            # sorted-key JSON for that array.
            # This checks that the real previous outputs reached both branches
            # and the merge; a list of stage labels cannot satisfy it.
            for row in completed:
                if str(row["step_id"]) == step_id:
                    assert all(json.dumps(answer, sort_keys=True) in row["prompt_filled"]
                               for answer in previous_answers)

    traces = capture["traces"]
    assert len({str(item["metadata_id"]) for item in traces}) == len(traces)
    assert {str(item["metadata_id"]) for item in traces} >= {str(row["id"]) for row in completed}
    tool_execution_ids = set()
    tool_count = 0
    for trace in traces:
        execution = executions[str(trace["metadata_id"])]
        ref = run.artifacts[trace["stdout_artifact"]]
        _bytes(run.artifacts[trace["stderr_artifact"]])
        native_calls = _native_tool_calls(ref, trace["protocol"])
        recorded = [event for event in run.events if event.kind == "tool_call"
                    and event.execution_id == execution.id and event.source is not None
                    and event.source.artifact == ref]
        # Additional native tool types may be recorded too. This decoder checks
        # the supported command/tool-use subset without rejecting richer traces.
        native_ids = {call["native_id"] for call in native_calls}
        recorded = [event for event in recorded if event.fields.get("native_id") in native_ids]
        assert len(recorded) == len(native_calls), "Every supported native tool result must survive normalization."
        assert len({event.fields["native_id"] for event in recorded}) == len(recorded)
        for native in native_calls:
            event = next(event for event in recorded if event.fields["native_id"] == native["native_id"])
            for key in ("name", "arguments", "result"):
                assert _json_shape(event.fields[key]) == native[key]
            assert event.source.locator == f"line:{native['input_line']}"
            assert any(evidence.artifact == ref and evidence.locator == f"line:{native['output_line']}"
                       for evidence in event.outputs)
            tool_execution_ids.add(execution.id)
            tool_count += 1
    assert tool_count >= 3, "This showcase needs actual repository inspection, not a final string."
    for step_id, step in by_id.items():
        if step["depth"] < 2:
            assert any(executions[str(row["id"])].id in tool_execution_ids for row in completed
                       if str(row["step_id"]) == step_id), "Each map/review branch must inspect source through tools."


def test_openkritt_scan_round_trips_native_outputs_on_gcp(
    api, live_backend, live_profile, tmp_path
):
    manifest, workflow = verify_inputs()
    namespace = f"aef-owned-{uuid4().hex}"
    artifact_dir = tmp_path / "native-artifacts"
    settings = dict(live_profile.get("settings", {}))
    settings["openkritt_e2e"] = {
        "upstream_revision": UPSTREAM_REVISION,
        "fixture_dir": str(FIXTURE / "review_target"),
        "source_manifest": str(FIXTURE / "source_manifest.json"),
        "workflow_file": str(FIXTURE / "workflow.json"),
        "artifact_dir": str(artifact_dir),
        "repo_full": namespace,
        "dependencies": [],
        "post_script_id": live_profile["post_script_id"],
        "scan_configuration": {"repeat_runs": 2},
        "capture_contract": "aef-openkritt-capture/2",
        "require_tool_trace": True,
        "artifact_bundle": "openkritt.bundle",
    }
    study, evaluators, reducers = make_study(
        api,
        project_id="openkritt-integration",
        backend_ref=live_profile["backend_ref"],
        units=[{
            "task_id": "flaskr-auth-storage-review",
            "repo_kind": "local",
            "repo_full": namespace,
            "repo_scope": "Pinned Flaskr tutorial: flaskr/ application, SQL schema, templates and tests/. "
                          "Trace authentication, author-only edits and request/database/rendering dataflow.",
        }],
        input_columns={"units": ("task_id", "repo_kind", "repo_full", "repo_scope")},
        settings=settings,
        wall_time_s=1800,
    )
    metric = WorkflowAttemptsMetric(api)
    study = replace(study, suite=replace(study.suite, version="native-workflow-attempts-v1",
        metrics=tuple(replace(spec, source=api.EvaluatorSource(ref=metric.ref)) for spec in study.suite.metrics)))
    evaluators = {metric.ref.name: metric}

    result = study.evaluate(
        backends={live_backend.ref.name: live_backend},
        evaluators=evaluators,
        reducers=reducers,
    )

    assert_usable_result(api, study, result, root=tmp_path / "portable-result")
    assert len(result.runs.runs) == len(study.plan().assignments)
    scan_ids = set()
    for run in result.runs.runs:
        # Completion is this plumbing scenario's expectation, not a core rule
        # about whether the scan found the correct security issues.
        assert run.status == "completed", run.error
        assert run.environment.status == "observed"
        assert run.environment.value["platform"] == "gcp"
        assert run.environment.value["resource_id"]
        assert run.environment.value["upstream_revision"] == UPSTREAM_REVISION
        assert run.environment.evidence, "GCP provenance needs a captured source."

        output = _json_shape(run.output)
        raw_scan = json.loads(_bytes(run.artifacts["openkritt.scan"]))
        raw_findings = json.loads(_bytes(run.artifacts["openkritt.findings"]))
        assert output["scan_id"] == str(raw_scan["id"])
        assert run.native_refs["scan_id"] == output["scan_id"]
        assert output["scan_id"] not in scan_ids, "Candidates require fresh native scans."
        scan_ids.add(output["scan_id"])
        assert output["native_status"] == raw_scan["status"] == "completed"
        assert raw_scan["repoKind"] == "local"
        assert raw_scan["repoFull"] == namespace
        assert raw_scan["configuration"]["repeat_runs"] == 2
        post_script = json.loads(_bytes(run.artifacts["openkritt.post_script"]))
        assert str(post_script["id"]) == str(live_profile["post_script_id"])
        assert str(raw_scan["postScriptId"]) == str(post_script["id"])
        assert isinstance(raw_findings, list)
        assert output["findings"] == raw_findings
        _assert_trajectory(run, manifest, workflow)
        _assert_downloadable_bundle(run, manifest)
        values = {measurement.metric: measurement.value for measurement in result.measurements
                  if measurement.run_id == run.id and measurement.key is None}
        assert values["wiring"] == 8 and values["derived"] == 15
        assert run.output_sources
        assert set(run.output_sources) <= {execution.id for execution in run.executions}

        # Inspect the same rich trajectory after persistence, not only in memory.
        saved = tmp_path / ("native-runset-" + output["scan_id"])
        result.runs.save(saved)
        loaded = api.RunSet.load(saved)
        loaded_run = loaded.get(run.id)
        assert _json_shape(loaded_run.output) == output
        _assert_trajectory(loaded_run, manifest, workflow)
        _assert_downloadable_bundle(loaded_run, manifest)

        if not raw_findings:
            # Native OpenKritt refuses a ZIP when there are no findings.
            assert output["export"]["status"] == "unavailable"
            assert output["export"]["reason"]
            assert "openkritt.export" not in run.artifacts
            continue

        assert output["export"]["status"] == "available"
        with ZipFile(io.BytesIO(_bytes(run.artifacts["openkritt.export"]))) as archive:
            names = archive.namelist()
            manifests = [name for name in names if PurePosixPath(name).name == "manifest.json"]
            assert len(manifests) == 1
            manifest_path = PurePosixPath(manifests[0])
            export_manifest = json.loads(archive.read(manifests[0]))
            assert str(export_manifest["scan"]["id"]) == output["scan_id"]
            assert export_manifest["scan"]["status"] == "completed"
            assert export_manifest["scan"]["completeness"] == "complete"
            assert {str(item["id"]) for item in export_manifest["findings"]} == {
                str(item["id"]) for item in raw_findings
            }
            for item in export_manifest["findings"]:
                target = manifest_path.parent / item["files"]["structuredFinding"]
                assert str(target) in names
                assert isinstance(json.loads(archive.read(str(target))), dict)
