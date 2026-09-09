"""Opt-in acceptance of real optional-library boundaries, not protocol fakes.

Factories construct adapters only. Scenarios, identity checks, evidence checks
and result consumption are test-owned. Selected missing prerequisites fail.
"""
from dataclasses import replace
import hashlib
import importlib
import json
from pathlib import Path
import uuid

import pytest

from tests.e2e.support import ScriptedBackend, plain


def load_profile(request, name):
    config_path = request.config.getoption("--aef-profile-config")
    assert config_path, "Selected optional integration requires --aef-profile-config"
    config_path = Path(config_path).resolve()
    profiles = json.loads(config_path.read_text(encoding="utf-8"))
    assert name in profiles, f"Missing selected profile: {name}"
    profile = profiles[name]
    for field in ("factory", "integration_ref", "upstream_ref"):
        assert field in profile, f"{name} requires {field}"
    for field in ("integration_ref", "upstream_ref"):
        assert profile[field].get("name") and profile[field].get("revision")
    assert "REPLACE_" not in json.dumps(profile), "Fill the profile before selecting it"
    return profile, config_path.parent


def construct(profile, workspace):
    module, separator, function = profile["factory"].partition(":")
    assert separator and module and function, "factory must be module:callable"
    value = getattr(importlib.import_module(module), function)(profile, workspace=workspace)
    assert not isinstance(value, ScriptedBackend), "A scripted substitute is not an upstream integration"
    assert value.ref.name == profile["integration_ref"]["name"]
    assert value.ref.revision == profile["integration_ref"]["revision"]
    return value


def local_artifact(reference):
    path = Path(reference.uri)
    assert path.is_absolute() and path.is_file(), "Materialize retained evidence in the test cache"
    assert reference.sha256 and hashlib.sha256(path.read_bytes()).hexdigest() == reference.sha256
    return path


def fixture_paths(profile, config_dir):
    fixture = profile["fixture"]
    root = (config_dir / fixture["root"]).resolve()
    assert root.is_dir(), f"Missing genuine upstream fixture directory: {root}"
    assert fixture["files"], "Fixture needs an explicit immutable source-file manifest"
    for relative, digest in fixture["files"].items():
        path = (root / relative).resolve()
        assert path.is_relative_to(root), "Fixture manifest paths must stay within its root"
        assert path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest
    manifested = {(root / relative).resolve() for relative in fixture["files"]}
    for field in ("study", "runs", "source", "provenance"):
        if field not in fixture:
            continue
        path = (root / fixture[field]).resolve()
        assert path.is_relative_to(root) and path.exists()
        required = {item.resolve() for item in path.rglob("*") if item.is_file()} if path.is_dir() else {path}
        assert required <= manifested, f"Unpinned files in fixture {field}"
    provenance = json.loads((root / fixture["provenance"]).read_text(encoding="utf-8"))
    assert provenance["upstream_ref"] == profile["upstream_ref"]
    return root, fixture


def identity(assignment):
    return assignment.candidate_id, json.dumps(plain(assignment.unit), sort_keys=True), assignment.repetition


def assert_expected_assignments(capture, expected):
    planned = {assignment.id: assignment for assignment in capture.plan.assignments}
    declared = {(row["candidate_id"], json.dumps(row["unit"], sort_keys=True), row["repetition"]): row
                for row in expected}
    assert len(declared) == len(expected) == len(capture.runs)
    assert {identity(assignment) for assignment in planned.values()} == set(declared)
    for run in capture.runs:
        row = declared[identity(planned[run.assignment_id])]
        assert run.status == row["status"] and run.output_state == row["output_state"]
        assert row["native_refs"] and all(run.native_refs.get(key) == value
                                          for key, value in row["native_refs"].items())


def assert_retained_result(api, result, target):
    result.runs.validate().raise_for_errors()
    for run in result.runs.runs:
        assert result.explain(run.id).run_id == run.id
    result.save(target)
    loaded = api.EvaluationResult.load(target)
    assert loaded.runs.runs == result.runs.runs
    assert loaded.runs.native_jobs == result.runs.native_jobs
    assert loaded.runs.native_grades == result.runs.native_grades
    assert loaded.runs.projections == result.runs.projections
    assert loaded.measurements == result.measurements and loaded.activities == result.activities
    assert loaded.performed_activity_ids == result.performed_activity_ids
    assert loaded.evaluation_resources == result.evaluation_resources
    report = loaded.report(target.parent / (target.name + ".html"))
    assert report.is_file()


class CountedJob:
    def __init__(self, delegate):
        self.delegate, self.ref, self.calls = delegate, delegate.ref, []

    def capabilities(self):
        return self.delegate.capabilities()

    async def run_job(self, request, *, recorder):
        self.calls.append(request)
        return await self.delegate.run_job(request, recorder=recorder)


class CountedBatch:
    def __init__(self, delegate):
        self.delegate, self.ref, self.calls = delegate, delegate.ref, []

    async def compute_batch(self, request):
        self.calls.append(request)
        return await self.delegate.compute_batch(request)


@pytest.mark.live
@pytest.mark.profile("harbor_native")
def test_harbor_executes_one_native_job_and_retains_trial_and_verifier_evidence(api, request, tmp_path):
    profile, _ = load_profile(request, "harbor_native")
    job = CountedJob(construct(profile, tmp_path / "harbor-worker"))
    units = [{"task_id": f"receipt-{index}", "text": uuid.uuid4().hex} for index in range(2)]
    dataset = api.EvalDataset.from_records(
        id="harbor-receipt-tasks", units=units, unit_key=("task_id",),
        input_columns={"units": ("task_id", "text")},
    )
    candidate = api.Candidate(
        id="native-receipt-agent", backend=job.ref,
        components={"harness": api.ComponentSpec(kind="harness", ref=api.VersionRef(**profile["upstream_ref"]))},
        settings={"integration_scenario": "write-receipt", "native_agent": profile["native_agent"]},
    )
    verifier = api.VerifierSpec(id="receipt-check", implementation=api.VersionRef(**profile["verifier_ref"]))
    config = api.NativeJobConfig(id="receipt-job", backend=job.ref, candidate_ids=(candidate.id,),
                                verifiers=(verifier,), native=api.NativeConfig(
                                    schema_ref=api.VersionRef(**profile["native_schema_ref"]),
                                    values=profile["native_options"]))
    metric = api.MetricSpec(id="receipt", output_type="bool", role="diagnostic",
                           source=api.NativeGradeSource(channel=verifier.id, native_metric=profile["native_metric"]))
    suite = api.EvalSuite(id="harbor-transport", version="v1", metrics=(metric,), summaries=())
    study = api.Study(id="harbor-job", project_id="optional-harbor", question="Can native records be consumed?",
        dataset=dataset, candidates={candidate.id: candidate}, suite=suite,
        execution=api.ExecutionPolicy(budget=api.Budget(wall_time_s=profile["wall_time_s"]), native_jobs=(config,)))
    result = api.EvaluationPipeline(study=study, evaluators={}, job_backends={job.ref.name: job}).eval()
    assert len(job.calls) == len(result.runs.native_jobs) == 1
    assert len(job.calls[0].requests) == len(result.runs.runs) == len(units)
    record = result.runs.native_jobs[0]
    assert record.native_refs and len(record.links) == len(units)
    receipt = json.loads(local_artifact(record.artifacts["integration.receipt"]).read_text(encoding="utf-8"))
    assert receipt["upstream_ref"] == profile["upstream_ref"]
    assert receipt["native_job_id"] in record.native_refs.values()
    local_artifact(record.artifacts["native.result"])
    expected = {row["task_id"]: row["text"] for row in units}
    assignments = {row.id: row for row in result.runs.plan.assignments}
    for run in result.runs.runs:
        assert run.status == "completed" and run.output_state == "available"
        assert run.output["receipt"] == expected[assignments[run.assignment_id].unit["task_id"]]
        assert run.job_id == record.id and run.native_refs
        local_artifact(run.artifacts["native.result"])
        local_artifact(run.artifacts["native.verifier"])
        assert any(link.run_id == run.id and link.assignment_id == run.assignment_id for link in record.links)
    rows = [row for row in result.measurements if row.metric == metric.id and row.key is None]
    assert len(rows) == len(units) and all(row.status == "ok" and row.evidence for row in rows)
    assert result.runs.native_grades and result.activities
    assert set(result.performed_activity_ids) == {activity.id for activity in result.activities}
    assert_retained_result(api, result, tmp_path / "harbor-result")
    saved = api.EvaluationPipeline(study=study, evaluators={}).eval(runs=result.runs)
    assert len(job.calls) == 1 and saved.measurements == result.measurements
    assert saved.performed_activity_ids == ()


@pytest.mark.live
@pytest.mark.profile("skillevaluator_import")
def test_skillevaluator_import_preserves_real_paired_trial_identities_and_grades(api, request, tmp_path):
    profile, config_dir = load_profile(request, "skillevaluator_import")
    root, fixture = fixture_paths(profile, config_dir)
    study = api.Study.load(root / fixture["study"])
    importer = construct(profile, tmp_path / "skill-import-cache")
    assert len(study.candidates) == 2, "Fixture must contain both native paired-skill arms"
    source = root / fixture["source"]
    assert source.exists()
    capture = api.RunSet.import_from(source, plan=study.plan(), importer=importer)
    assert capture.importer == importer.ref and capture.import_source
    assert_expected_assignments(capture, fixture["assignments"])
    assert any(run.status in {"agent_error", "timed_out", "infrastructure_error", "cancelled"}
               for run in capture.runs), "Retain a genuine failed native trial"
    assert capture.native_grades and capture.projections
    assert any(projection.mapper == importer.ref for projection in capture.projections)
    for projection in capture.projections:
        assert projection.sources
        for reference in projection.sources:
            local_artifact(reference)
    for run in capture.runs:
        assert run.artifacts, "Failed trials must retain their native evidence too"
        local_artifact(run.artifacts["native.result"])
    for bundle in capture.native_grades:
        assert bundle.projection.sources
        for reference in bundle.projection.sources:
            local_artifact(reference)
    assert all(isinstance(metric.source, api.NativeGradeSource) for metric in study.suite.metrics)
    result = api.EvaluationPipeline(study=study, evaluators={}).eval(runs=capture)
    assert result.activities and result.performed_activity_ids == ()
    assert result.incremental_evaluation_resources().cost_usd.value == 0
    assert_retained_result(api, result, tmp_path / "skill-result")
    fixture_paths(profile, config_dir)  # importing must not rewrite the native source fixture


@pytest.mark.live
@pytest.mark.profile("nat_batch")
def test_nat_grades_retained_trajectories_in_one_batch_without_new_agent_runs(api, request, tmp_path):
    profile, config_dir = load_profile(request, "nat_batch")
    root, fixture = fixture_paths(profile, config_dir)
    study = api.Study.load(root / fixture["study"])
    capture = api.RunSet.load(root / fixture["runs"])
    assert len(capture.runs) >= 2, "Batch integration requires multiple genuine captured trajectories"
    assert_expected_assignments(capture, fixture["assignments"])
    for run in capture.runs:
        local_artifact(run.artifacts["native.trajectory"])
    grader = CountedBatch(construct(profile, tmp_path / "nat-grade-cache"))
    metric = api.MetricSpec(id="trajectory-probe", source=api.EvaluatorSource(ref=grader.ref, mode="batch"),
                           output_type=profile["output_type"], role="diagnostic", params=profile["grader_params"])
    suite = api.EvalSuite(id="nat-transport", version="v1", metrics=(metric,), summaries=())
    result = api.EvaluationPipeline(study=replace(study, suite=suite), evaluators={},
                                    batch_evaluators={grader.ref.name: grader}).eval(runs=capture)
    assert len(grader.calls) == 1
    assert {item.run.id for item in grader.calls[0].items} == {run.id for run in capture.runs}
    assert result.runs == capture
    fresh = [activity for activity in result.activities if activity.id in result.performed_activity_ids]
    assert len(fresh) == 1 and fresh[0].id == grader.calls[0].id
    native = fresh[0]
    local_artifact(native.artifacts["native.result"])
    receipt = json.loads(local_artifact(native.artifacts["integration.receipt"]).read_text(encoding="utf-8"))
    assert receipt["upstream_ref"] == profile["upstream_ref"]
    assert receipt["trajectory_hashes"] == sorted(run.artifacts["native.trajectory"].sha256 for run in capture.runs)
    assert receipt["native_invocation_count"] == 1
    assert receipt["agent_invocation_count"] == 0
    rows = [row for row in result.measurements if row.metric == metric.id and row.key is None]
    assert {row.run_id for row in rows} == {run.id for run in capture.runs}
    assert any(row.status == "ok" for row in rows), "At least one native grade must exercise value transport"
    for row in rows:
        assert row.activity_ids == (native.id,) and row.reason and row.evidence
        for evidence in row.evidence:
            local_artifact(evidence.artifact)
        if row.status != "ok":
            assert row.value is None and row.basis is None
    assert_retained_result(api, result, tmp_path / "nat-result")
    fixture_paths(profile, config_dir)
