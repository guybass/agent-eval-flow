"""Our adapter mapping/binding tests; these do not certify native integrations.

Native runtime fixtures below supply raw native-shaped records to exercise the
projection boundary. The separate live acceptance profiles require real SDKs.
"""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

import anyio
import pytest

import agent_eval_flow as o
from agent_eval_flow.adapters.common import AdapterBinding
from agent_eval_flow.adapters.harbor import (HarborDialect, HarborGradeChannel, HarborJobAdapter,
    HarborNativeCapture, HarborPreparedJob, HarborTrialCapture, HarborTrialMapper)
from agent_eval_flow.adapters.nat import NATBatchEvaluator, NATDialect, NATNativeResult, NativeATIFRuntime
from agent_eval_flow.adapters.skillevaluator import PairedTrialSource, SkillEvaluatorDialect, SkillEvaluatorImporter
from agent_eval_flow.storage.artifacts import ArtifactCache


def observed(value):
    return o.Observation(value=value, status="observed")


def unknown():
    return o.Observation(value=None, status="unknown", reason="Fixture does not provide this fact")


def resources(cost):
    return o.Resources(cost_scope=("model",), cost_usd=observed(Decimal(cost)),
        input_tokens=observed(1), output_tokens=observed(2), human_minutes=observed(0.0))


def source(cache, value):
    return cache.write_bytes("source", json.dumps(value).encode(), "application/json")


def study():
    data = o.EvalDataset.from_records(id="adapter-input", units=[{"task": "x"}, {"task": "y"}],
        unit_key=("task",), input_columns={"units": ("task",)})
    backend = o.VersionRef(name="harbor-adapter", revision="1")
    return o.Study(id="adapter-test", project_id="unit", question="Check adapter mapping", dataset=data,
        candidates={name: o.Candidate(id=name, backend=backend, components={}) for name in ("baseline", "skill")},
        suite=o.EvalSuite(id="none", version="1", metrics=(), summaries=()),
        execution=o.ExecutionPolicy(budget=o.Budget(wall_time_s=30)))


def requests(value):
    return tuple(o.RunRequest(run_id="run-" + row.id, assignment=row,
        candidate=value.candidates[row.candidate_id], input=value.dataset.agent_input(row.unit),
        policy=value.execution, environment=None) for row in value.plan().assignments)


def trial_payload(native_id="native-1", *, failed=False, multi=False):
    payload = {"id": native_id, "trial_name": native_id, "task_name": "native-task",
        "config": {"agent": {"name": "fixture"}},
        "started_at": "2026-09-09T01:00:00Z", "finished_at": "2026-09-09T01:00:04Z",
        "agent_result": {"cost_usd": 99, "n_input_tokens": 2, "n_output_tokens": 4},
        "agent_execution": {"started_at": "2026-09-09T01:00:01Z", "finished_at": "2026-09-09T01:00:03Z"},
        "verifier_result": {"rewards": {"receipt": 1}},
        "exception_info": {"exception_type": "AgentTimeoutError", "exception_message": "native deadline"} if failed else None}
    if multi:
        payload["step_results"] = [{"step_name": f"step-{i}", "agent_result": {
            "cost_usd": cost, "n_input_tokens": 2, "n_output_tokens": 4}, "agent_execution": payload["agent_execution"]}
            for i, cost in enumerate((0.2, 0.3))]
    return payload


def mapper(cache):
    return HarborTrialMapper(mapper_ref=o.VersionRef(name="mapper", revision="1"),
        source_format=o.VersionRef(name="harbor-trial", revision="fixture"), artifacts=cache,
        validate_trial=lambda value: value,
        grade_channels=(HarborGradeChannel(channel="verifier", evaluator=o.VersionRef(name="verifier", revision="1"),
            native_metric="receipt", reward_name="receipt", output_type="bool"),))


def test_harbor_step_costs_are_exclusive_and_verifier_cost_is_separate(tmp_path):
    cache = ArtifactCache(tmp_path / "cache")
    request = requests(study())[0]
    raw = source(cache, trial_payload(multi=True))
    captured = HarborTrialCapture(run_id=request.run_id, result=raw,
        output=source(cache, {"receipt": "real captured file"}), output_step="step-1",
        execution_inventory_complete=observed(True), verifier_resources=resources("0.07"))
    run, bundles, projection = mapper(cache).map_trial(captured, request)
    assert run.resources().cost_usd.value == Decimal("0.5")  # Parent aggregate 99 is not added.
    assert len(run.executions) == 2 and run.output_sources == (run.executions[1].id,)
    assert bundles[0].activities[0].resources.cost_usd.value == Decimal("0.07")
    assert bundles[0].grades[0].value is True
    assert Path(run.artifacts["native.result"].uri).read_bytes() == Path(raw.uri).read_bytes()
    assert projection.sources and run.environment.status == "unknown"


def test_harbor_failure_retains_output_and_undeclared_usage_stays_unknown(tmp_path):
    cache = ArtifactCache(tmp_path / "cache")
    request = requests(study())[0]
    native = trial_payload(failed=True)
    native["agent_result"] = {}
    captured = HarborTrialCapture(run_id=request.run_id, result=source(cache, native),
        output=source(cache, None), execution_inventory_complete=observed(True))
    run, _, _ = mapper(cache).map_trial(captured, request)
    assert run.status == "timed_out" and run.output is None and run.output_state == "available"
    assert run.resources().cost_usd.status == "unknown" and run.error.evidence


def test_harbor_multiple_rewards_share_one_activity_and_step_grades_keep_keys(tmp_path):
    cache = ArtifactCache(tmp_path / "cache")
    request = requests(study())[0]
    payload = trial_payload(multi=True)
    payload["verifier_result"]["rewards"]["second"] = 0.25
    for step in payload["step_results"]:
        step["verifier_result"] = {"rewards": {"receipt": 1, "second": 0.5}}
    projected = mapper(cache)
    first = projected.grade_channels[0]
    projected.grade_channels = (first, replace(first, native_metric="second", reward_name="second", output_type="float"))
    captured = HarborTrialCapture(run_id=request.run_id, result=source(cache, payload),
        verifier_resources=resources("0.07"), verifier_resources_by_step={"step-0": resources("0.01"), "step-1": resources("0.02")})
    _, bundles, _ = projected.map_trial(captured, request)
    assert len(bundles) == 3 and all(len(bundle.activities) == 1 and len(bundle.grades) == 2 for bundle in bundles)
    assert sum(bundle.activities[0].resources.cost_usd.value for bundle in bundles) == Decimal("0.10")
    assert {grade.key["step"] for bundle in bundles for grade in bundle.grades if grade.key is not None} == {"step-0", "step-1"}


def test_harbor_dispatches_one_job_with_identity_checked_partial_capture(tmp_path):
    cache = ArtifactCache(tmp_path / "cache")
    value, made = study(), []
    reqs = requests(value)
    ref = next(iter(value.candidates.values())).backend
    upstream = o.VersionRef(name="harbor", revision="fixture")
    schema = o.VersionRef(name="harbor-config", revision="fixture")
    config = o.NativeJobConfig(id="native", backend=ref, candidate_ids=tuple(value.candidates),
        native=o.NativeConfig(schema_ref=schema))
    request = o.NativeJobRequest(job_id="job-id", run_set_id="capture-id", planned_job_id="planned",
                                config=config, requests=reqs)
    native = HarborNativeCapture(native_job_id="upstream-job", result=source(cache, {"id": "upstream-job"}),
        trials=(HarborTrialCapture(run_id=reqs[0].run_id, result=source(cache, trial_payload()),
                                  execution_inventory_complete=observed(True)),),
        status="partial", grading_inventory_complete=unknown(), effective_config=observed({"native": True}))
    class Runtime:
        upstream_ref = upstream
        async def run_job(self, prepared, *, request, artifacts_dir, on_capture):
            made.append(prepared)
            on_capture(native)
            return native
        def capabilities(self):
            return o.NativeJobCapabilities(limits=o.BackendCapabilities(wall_time_limit=True,
                token_limit=False, cost_limit=False, reset_state=True), private_verifier_channel=True, fixed_repetitions=True)
    class Recorder:
        def __init__(self): self.jobs, self.runs, self.grades = [], [], []
        def record_job(self, row): self.jobs.append(row)
        def record_run(self, row): self.runs.append(row)
        def record_grade_bundle(self, row): self.grades.append(row)
    adapter = HarborJobAdapter(binding=AdapterBinding(ref=ref, upstream_ref=upstream,
        workspace_root=tmp_path / "work", artifacts=cache, deployment=unknown()), runtime=Runtime(),
        dialect=HarborDialect(schema_ref=schema, upstream_ref=upstream, validate_trial=lambda row: row,
            prepare_job=lambda request, workspace: HarborPreparedJob(config={"native": True}, run_ids=tuple(r.run_id for r in request.requests))))
    recorder = Recorder()
    async def call(): return await adapter.run_job(request, recorder=recorder)
    result = anyio.run(call)
    assert len(made) == 1 and len(recorder.jobs) == 2
    assert len(result.runs) == 1 and len(result.job.assignment_ids) == len(reqs)
    assert result.runs[0].id == reqs[0].run_id and result.runs[0].job_id == request.job_id
    assert result.grading_inventory_complete.status == "unknown"


def test_skill_import_joins_explicit_paired_ids_and_rejects_tampering(tmp_path):
    cache = ArtifactCache(tmp_path / "cache")
    value = study()
    folder = tmp_path / "archive"
    folder.mkdir()
    entries, hashes = [], {}
    for index, assignment in enumerate(reversed(value.plan().assignments)):
        name = f"trial-{index}.json"
        path = folder / name
        path.write_text(json.dumps(trial_payload(f"native-{index}", failed=index == 0)))
        import hashlib
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append(PairedTrialSource(candidate_id=assignment.candidate_id, unit=assignment.unit,
            repetition=assignment.repetition, native_trial_id=f"native-{index}", result_path=name,
            execution_inventory_complete=observed(True)))
    dialect = SkillEvaluatorDialect(entries=tuple(entries), expected_files=hashes, validate_trial=lambda value: value,
        grading_inventory_complete=observed(True), grade_channels=mapper(cache).grade_channels)
    importer = SkillEvaluatorImporter(ref=o.VersionRef(name="skill-import", revision="1"),
        source_format=o.VersionRef(name="skillevaluator-retained", revision="fixture"), dialect=dialect, artifacts=cache)
    result = o.RunSet.import_from(folder, plan=value.plan(), importer=importer)
    assert len(result.runs) == 4 and len(result.native_grades) == 4
    assignments = {row.id: row for row in value.plan().assignments}
    for run in result.runs:
        original = next(row for row in entries if row.native_trial_id == run.native_refs["trial_id"])
        assert assignments[run.assignment_id].candidate_id == original.candidate_id
        assert assignments[run.assignment_id].unit == original.unit
    assert any(run.status == "timed_out" for run in result.runs)
    (folder / entries[0].result_path).write_text("{}")
    with pytest.raises(o.CaptureValidationError, match="hash mismatch"):
        importer.read(folder, plan=value.plan())


def nat_request(cache):
    ref = o.VersionRef(name="nat-adapter", revision="1")
    items = []
    for index in range(2):
        run = o.Run(id=f"run-{index}", assignment_id=f"assignment-{index}", status="completed",
            cost_scope=("model",), output={"answer": index}, output_state="available",
            artifacts={"native.trajectory": source(cache, {"steps": [{"step_id": index, "message": "fixture"}]})},
            executions=(), started_at=None, ended_at=None, environment=unknown(), execution_inventory_complete=unknown())
        context = o.EvaluationContext(unit={"task": index}, inputs=o.AgentInput(unit={"task": index}, tables={}),
            references={"gold": ({"task": index, "expected": index},)})
        items.append(o.EvaluationItem(run=run, context=context))
    return o.BatchMetricRequest(id="activity", config_fingerprint="config-hash", items=tuple(items),
        metric=o.MetricSpec(id="grade", source=o.EvaluatorSource(ref=ref, mode="batch"), output_type="float", role="diagnostic")), ref


@pytest.mark.parametrize("foreign", [False, True])
def test_nat_batches_once_keeps_ids_and_maps_native_error_zero(tmp_path, foreign):
    cache = ArtifactCache(tmp_path / "cache")
    request, ref = nat_request(cache)
    upstream = o.VersionRef(name="nvidia-nat-eval", revision="fixture")
    calls = []
    class Runtime:
        upstream_ref = upstream
        async def grade(self, items, *, configuration, artifacts, cost_scope):
            calls.append(items)
            assert items[1].references["gold"][0]["expected"] == 1
            payload = {"average_score": 0.35, "eval_output_items": [
                {"id": "foreign" if foreign else "run-1", "score": 0.0, "reasoning": {"error": "Evaluator error: timeout"}},
                {"id": "run-0", "score": 0.7, "reasoning": "recorded grade"}]}
            now = datetime.now(timezone.utc)
            return NATNativeResult(result=source(artifacts, payload), resources=resources("0.20"),
                                   started_at=now, ended_at=now, native_refs={"batch": "native-id"})
    evaluator = NATBatchEvaluator(ref=ref, runtime=Runtime(), dialect=NATDialect(upstream_ref=upstream), artifacts=cache)
    async def call(): return await evaluator.compute_batch(request)
    if foreign:
        with pytest.raises(o.CaptureValidationError, match="foreign"):
            anyio.run(call)
    else:
        output = anyio.run(call)
        by_run = {row.run_id: row for row in output.measurements}
        assert by_run["run-1"].status == "error" and by_run["run-1"].value is None
        assert by_run["run-0"].value == 0.7
        assert output.activity.resources.cost_usd.value == Decimal("0.20")
        assert all(row.activity_ids == (request.id,) and row.evidence for row in output.measurements)
        receipt = json.loads(Path(output.activity.artifacts["integration.receipt"].uri).read_bytes())
        assert receipt["native_invocation_count"] == 1 and receipt["agent_invocation_count"] == 0
    assert len(calls) == 1


def test_native_nat_runtime_rejects_wrong_pin_before_optional_import(monkeypatch, tmp_path):
    import agent_eval_flow.adapters.nat as module
    monkeypatch.setattr(module, "version", lambda name: "different")
    runtime = NativeATIFRuntime(evaluator=object(), package_version="1.8.0", configuration={})
    async def call():
        return await runtime.grade((), configuration={}, artifacts=ArtifactCache(tmp_path), cost_scope=("model",))
    with pytest.raises(o.ConfigurationError, match="version mismatch"):
        anyio.run(call)


def test_native_nat_runtime_does_not_equate_boolean_and_integer_configuration(monkeypatch, tmp_path):
    import agent_eval_flow.adapters.nat as module
    monkeypatch.setattr(module, "version", lambda name: "1.8.0")
    runtime = NativeATIFRuntime(evaluator=object(), package_version="1.8.0", configuration={"x": True})
    async def call():
        return await runtime.grade((), configuration={"x": 1}, artifacts=ArtifactCache(tmp_path), cost_scope=("model",))
    with pytest.raises(o.ConfigurationError, match="Metric params differ"):
        anyio.run(call)
