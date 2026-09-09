"""Reports explain saved runtime checks without rerunning acquisition or grading."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib

import agent_eval_flow as a
from agent_eval_flow.objects.values import observed, unknown, zero_resources


class SavedCheck:
    ref = a.VersionRef(name="agent-eval-flow.runtime-evidence", revision="1")

    def __init__(self, *, include_detail=True, missing=False):
        self.calls = 0
        self.include_detail, self.missing = include_detail, missing

    def compute(self, spec, context, run):
        self.calls += 1
        row = a.Measurement(run_id=run.id, metric=spec.id,
            value=None if self.missing else False,
            status="missing" if self.missing else "ok",
            basis=None if self.missing else "observed",
            reason="Saved result: required instruction absent" if not self.missing else "CLI input was not captured",
            evidence=(run.events[0].source,) if run.events else ())
        return a.MetricOutput(task=row,
            details=(replace(row, key={"event": run.events[0].id}),) if self.include_detail and run.events else (),
            evaluation_resources=zero_resources())


def saved_result(tmp_path, *, expected_override=False, missing=False, opt_in=True, malformed=False,
                 extra_observation=False):
    evidence_path = tmp_path / "input.txt"
    evidence_path.write_text("short input", encoding="utf-8")
    artifact = a.ArtifactRef(uri=evidence_path.as_uri(), media_type="text/plain",
        sha256=hashlib.sha256(evidence_path.read_bytes()).hexdigest())
    source = a.EvidenceRef(artifact=artifact, locator="line:1", description="Actual CLI input")
    payload = '<script id="runtime-injection">unsafe()</script>'
    fields = {
        "schema_version": "1", "collector": {"name": "capture.cli", "revision": "1"},
        "subject": "task.instructions", "phase": "first_action", "boundary": "provider_cli.stdin",
        "component_id": "task.prompt", "call_id": "call-1",
        "declared": {"value": "full instruction " + payload, "status": "observed", "reason": "Declared task", "evidence": [
            {"artifact": {"uri": artifact.uri, "media_type": artifact.media_type, "sha256": artifact.sha256},
             "locator": "line:1", "description": "Actual CLI input"}]},
        "observed": {"value": "short input", "status": "observed", "reason": "Captured CLI input", "evidence": [
            {"artifact": {"uri": artifact.uri, "media_type": artifact.media_type, "sha256": artifact.sha256},
             "locator": "line:1", "description": "Actual CLI input"}]},
        "coverage": "partial", "transformations": ["native prefix limit " + payload],
    }
    if malformed:
        fields["schema_version"] = "unsupported"
    event = a.Event(id="event-1", execution_id="execution-1", kind="aef.runtime.observation",
        at=None, fields=fields, source=source, inputs=(source,), outputs=(source,))
    events = (event,)
    if extra_observation:
        fields = {**fields, "call_id": "unselected-call", "observed": {**fields["observed"], "value": "Unselected observation"}}
        events += (replace(event, id="event-2", fields=fields),)
    metric = SavedCheck(include_detail=not missing, missing=missing)
    if not opt_in:
        metric.ref = a.VersionRef(name="example.other-evaluator", revision="1")
    params = {"subject": "task.instructions", "phase": "first_action", "boundary": "provider_cli.stdin"}
    if expected_override:
        params["expected"] = "explicit expected override"
    spec = a.MetricSpec(id="delivery", source=a.EvaluatorSource(ref=metric.ref), output_type="bool",
        role="diagnostic", params=params)
    dataset = a.EvalDataset.from_records(id="task", units=[{"task_id": "one", "prompt": "instruction"}],
        unit_key=("task_id",), input_columns={"units": ("task_id", "prompt")})
    candidate = a.Candidate(id="agent", backend=a.VersionRef(name="unused", revision="1"), components={})
    study = a.Study(id="Runtime report", project_id="report", dataset=dataset, question="Check delivery",
        candidates={candidate.id: candidate}, suite=a.EvalSuite(id="checks", version="1", metrics=(spec,), summaries=()),
        execution=a.ExecutionPolicy(budget=a.Budget(wall_time_s=10)))
    plan = study.plan()
    now = datetime.now(timezone.utc)
    execution = a.Execution(id="execution-1", slot="native", retry_index=0, parent_id=None,
        status="completed", started_at=now, ended_at=now,
        effective_config=unknown("No effective config capture"), resources=zero_resources())
    run = a.Run(id="run-1", assignment_id=plan.assignments[0].id, status="completed", cost_scope=("model",),
        output="saved response", output_state="available", artifacts={"input": artifact}, executions=(execution,),
        started_at=now, ended_at=now, environment=unknown("Not captured"), execution_inventory_complete=observed(True),
        events=() if missing else events)
    capture = a.RunSet(id="capture", plan=plan, runs=(run,), grading_inventory_complete=observed(True))
    result = a.EvaluationPipeline(study=study, evaluators={metric.ref.name: metric}).eval(runs=capture)
    result.save(tmp_path / "saved")
    return a.EvaluationResult.load(tmp_path / "saved"), metric, payload


def test_saved_runtime_report_shows_context_and_evidence_without_regrading(tmp_path):
    result, metric, payload = saved_result(tmp_path)
    before = result.fingerprint()
    rendered = result.report(tmp_path / "report.html").read_text(encoding="utf-8")
    section = rendered.split("Runtime evidence checks", 1)[1].split("<h4>Measurements", 1)[0]
    assert "Expected" in section and "Observed" in section
    assert "full instruction" in section and "short input" in section
    assert "first_action" in section and "provider_cli.stdin" in section
    assert "partial" in section and "capture.cli" in section
    assert "task.prompt" in section and "call-1" in section and "event-1" in section
    assert "Saved result: required instruction absent" in section
    assert "Actual CLI input" in section and "line:1" in section
    assert payload not in rendered and "&lt;script" in section
    assert "native prefix limit" in section
    assert metric.calls == 1 and result.fingerprint() == before


def test_runtime_report_uses_explicit_expected_override(tmp_path):
    result, _, _ = saved_result(tmp_path, expected_override=True)
    rendered = result.report(tmp_path / "report.html").read_text(encoding="utf-8")
    section = rendered.split("Runtime evidence checks", 1)[1].split("<h4>Measurements", 1)[0]
    assert "explicit expected override" in section and "Configured expectation" in section


def test_runtime_report_explains_missing_observation_without_inventing_delivery(tmp_path):
    result, metric, _ = saved_result(tmp_path, missing=True)
    rendered = result.report(tmp_path / "report.html").read_text(encoding="utf-8")
    section = rendered.split("Runtime evidence checks", 1)[1].split("<h4>Measurements", 1)[0]
    assert "CLI input was not captured" in section
    assert "No runtime observation is linked to this saved check" in section
    assert "missing" in section and "unknown" in section
    assert metric.calls == 1


def test_runtime_context_is_opt_in_even_when_runtime_events_exist(tmp_path):
    result, _, _ = saved_result(tmp_path, opt_in=False)
    rendered = result.report(tmp_path / "report.html").read_text(encoding="utf-8")
    assert "Runtime evidence checks" not in rendered


def test_malformed_linked_runtime_payload_is_visible_without_breaking_report(tmp_path):
    result, metric, _ = saved_result(tmp_path, malformed=True)
    rendered = result.report(tmp_path / "report.html").read_text(encoding="utf-8")
    section = rendered.split("Runtime evidence checks", 1)[1].split("<h4>Measurements", 1)[0]
    assert "Cannot decode the linked runtime observation" in section
    assert "event-1" in section and "Saved result: required instruction absent" in section
    assert metric.calls == 1


def test_runtime_report_joins_only_observations_named_by_saved_details(tmp_path):
    result, _, _ = saved_result(tmp_path, extra_observation=True)
    rendered = result.report(tmp_path / "report.html").read_text(encoding="utf-8")
    section = rendered.split("Runtime evidence checks", 1)[1].split("<h4>Measurements", 1)[0]
    assert "event-1" in section
    assert "event-2" not in section and "Unselected observation" not in section
    assert "event-2" in rendered  # The original trace remains inspectable elsewhere.
