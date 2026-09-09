"""Offline E2E: real library + real child process + test-owned extension code."""
from dataclasses import replace
import json

import pytest

from tests.e2e.support import TOY, artifact, assert_usable_result, make_study


def toy_components(api, mode):
    components = {}
    if mode in ("skill", "flow"):
        components["skill.format"] = api.ComponentSpec(
            kind="skill", ref=api.VersionRef(name="fixture.format", revision="v1"),
            content=artifact(api, TOY / "SKILL.md", "text/markdown"))
    if mode in ("tool", "flow"):
        components["tool.echo"] = api.ComponentSpec(
            kind="tool", ref=api.VersionRef(name="fixture.echo", revision="v1"),
            content=artifact(api, TOY / "toy_tool.py", "text/x-python"))
    return components


def build(api, backend, mode, *, repetitions=1, wall_time_s=10):
    return make_study(
        api, project_id="toy-" + mode,
        backend_ref={"name": backend.ref.name, "revision": backend.ref.revision},
        units=[{"task_id": "toy-1", "text": "hello"}, {"task_id": "toy-2", "text": "world"}],
        settings={"toy_mode": mode}, components=toy_components(api, mode),
        repetitions=repetitions, wall_time_s=wall_time_s,
    )


@pytest.mark.parametrize("mode", ["plain", "skill", "tool", "flow"])
def test_small_harness_to_consumable_result(api, toy_backend, tmp_path, mode):
    study, evaluators, reducers = build(api, toy_backend, mode, repetitions=2)
    study.validate().raise_for_errors()
    assert len(study.plan().assignments) == 8
    assert toy_backend.calls == []  # planning cannot start a backend

    pipeline = api.EvaluationPipeline(study=study, backends={toy_backend.ref.name: toy_backend},
                                      evaluators=evaluators, reducers=reducers)
    assert pipeline.study.fingerprint() == study.fingerprint()
    assert toy_backend.calls == []  # binding runtime dependencies cannot start work
    result = pipeline.eval()

    assert len(toy_backend.calls) == 8
    assignments = {row.id: row for row in study.plan().assignments}
    for request in toy_backend.calls:
        assert set(request.input.tables) == {"units"}
        assert all(set(row) == {"task_id", "text"} for row in request.input.tables["units"])
        assert "EVALUATOR_ONLY" not in repr(request.input)
    expected_events = {"plain": [], "skill": ["skill_loaded"], "tool": ["tool_call"],
                       "flow": ["skill_loaded", "tool_call"]}
    for run in result.runs.runs:
        assert run.status == "completed"
        assert run.output["task_id"] == assignments[run.assignment_id].unit["task_id"]
        assert isinstance(run.output["message"], str) and run.output["items"]
        assert [event.kind for event in run.events] == expected_events[mode]
        if mode in ("skill", "flow"):
            assert run.events[0].fields["sha256"] == study.candidates["A"].components["skill.format"].content.sha256
        if mode in ("tool", "flow"):
            event = next(event for event in run.events if event.kind == "tool_call")
            assert event.fields["name"] == "fixture.echo" and event.outputs
            assert event.fields["result"]["task_id"] == run.output["task_id"]
        assert len(run.executions) == 1, "Recorder and final snapshot must reconcile by ID"
        assert len({event.id for event in run.events}) == len(run.events)

    metric = evaluators["e2e.wiring"]
    assert len(metric.calls) == 16 and set(metric.calls.values()) == {1}
    assert reducers["e2e.reducer"].calls == 2
    summaries = {(row.candidate_id, row.summary_id): row.value.value for row in result.summary()}
    for candidate_id, value in (("A", 11), ("B", 29)):
        assert summaries[candidate_id, "mean_probe"] == value
        assert summaries[candidate_id, "custom_probe"] == 4 * (value + 7) + 3
    comparison = result.compare("A", "B", metrics=("mean_probe",))
    assert comparison.rows[0].delta.value == 18
    for direction, expected in (("minimize", "A"), ("maximize", "B")):
        selection = result.select(api.SelectionPolicy(id=direction, objectives=(
            api.ObjectiveTerm(metric="mean_probe", direction=direction),)))
        assert selection.status == "selected" and tuple(selection.selected_ids) == (expected,)
    assert_usable_result(api, study, result, root=tmp_path / "consumer")
    assert len(toy_backend.calls) == 8, "Inspection, persistence and selection cannot rerun agents"
    assert set(metric.calls.values()) == {1}, "Reading or re-ranking must not re-evaluate"


def test_rescore_saved_runs_without_rerunning_backend(api, toy_backend, tmp_path):
    study, evaluators, reducers = build(api, toy_backend, "plain")
    first = study.evaluate(backends={toy_backend.ref.name: toy_backend}, evaluators=evaluators, reducers=reducers)
    first.runs.save(tmp_path / "capture")
    restored = api.RunSet.load(tmp_path / "capture")
    changed_metric = replace(study.suite.metrics[1], params={"add": 13})
    changed_suite = replace(study.suite, version="wiring-v2", metrics=(study.suite.metrics[0], changed_metric))

    changed_study = replace(study, suite=changed_suite)
    pipeline = api.EvaluationPipeline(study=changed_study, evaluators=evaluators, reducers=reducers)
    second = pipeline.eval(runs=restored)  # deliberately no backend binding

    assert len(toy_backend.calls) == len(study.plan().assignments)
    assert second.id != first.id and second.suite_fingerprint != first.suite_fingerprint
    assert second.runs.id == restored.id
    assert second.runs.plan.study_fingerprint == first.runs.plan.study_fingerprint
    assert {run.id for run in second.runs.runs} == {run.id for run in first.runs.runs}
    assert first.suite.version == "wiring-v1" and second.suite.version == "wiring-v2"
    for run in second.runs.runs:
        values = {m.metric: m.value for m in second.measurements if m.run_id == run.id and m.key is None}
        assert values["derived"] == values["wiring"] + 13


@pytest.mark.parametrize("mode,status,limit", [("fail", "agent_error", 10), ("timeout", "timed_out", 0.25)])
def test_process_failures_remain_usable_records(api, toy_backend, tmp_path, mode, status, limit):
    study, evaluators, reducers = build(api, toy_backend, mode, wall_time_s=limit)
    result = study.evaluate(backends={toy_backend.ref.name: toy_backend}, evaluators=evaluators, reducers=reducers)
    assert len(result.runs.runs) == len(study.plan().assignments)
    assert all(run.status == status and run.error is not None for run in result.runs.runs)
    assert all(run.output is None and run.artifacts for run in result.runs.runs)
    assert all(run.duration_s().value is not None for run in result.runs.runs)
    # The fixture acceptance policy is wiring > 0; status adds no hidden gate.
    assert all(score.acceptance == "pass" for score in result.task_scores)
    assert_usable_result(api, study, result, root=tmp_path / "consumer")


def test_import_protocol_preserves_missing_assignments(api, toy_backend, tmp_path):
    study, evaluators, reducers = build(api, toy_backend, "plain")
    runs = study.run(backends={toy_backend.ref.name: toy_backend})
    runs.save(tmp_path / "saved-native-capture")
    kept = [run.id for run in runs.runs[:-1]]
    source = tmp_path / "import-manifest.json"
    source.write_text(json.dumps({"capture": str(tmp_path / "saved-native-capture"), "keep": kept}), encoding="utf-8")

    class SavedCaptureImporter:
        ref = api.VersionRef(name="e2e.saved-capture-import", revision="fixture-v1")

        def read(self, source, *, plan):
            manifest = json.loads(source.read_text(encoding="utf-8"))
            capture = api.RunSet.load(manifest["capture"])
            return api.ImportedCapture(
                runs=tuple(run for run in capture.runs if run.id in manifest["keep"]),
                grading_inventory_complete=capture.grading_inventory_complete,
            )

    imported = api.RunSet.import_from(source, plan=study.plan(), importer=SavedCaptureImporter())
    imported.validate().raise_for_errors()
    assert len(imported.runs) == len(study.plan().assignments)
    assert sum(run.status == "unobserved" for run in imported.runs) == 1
    assert imported.coverage().unavailable == 1
    assert len(toy_backend.calls) == len(study.plan().assignments)
    missing = next(run for run in imported.runs if run.status == "unobserved")
    assert missing.resources().cost_usd.status == "unknown"
    assert missing.resources().cost_usd.value is None
    grading = api.EvaluationPipeline(study=study, evaluators=evaluators, reducers=reducers)
    result = grading.eval(runs=imported)
    assert result.runs.id == imported.id
    assert result.runs.get(missing.id).status == "unobserved"
    assert len(toy_backend.calls) == len(study.plan().assignments), "Grading a partial capture must not fill it by execution"


@pytest.mark.parametrize("invalid", ["missing_evaluator", "missing_reducer", "wrong_evaluator_revision"])
def test_pipeline_preflight_fails_before_any_backend_call(api, toy_backend, invalid):
    study, evaluators, reducers = build(api, toy_backend, "plain")
    expected_name = "e2e.reducer" if invalid == "missing_reducer" else "e2e.wiring"
    if invalid == "missing_evaluator":
        evaluators = {}
    elif invalid == "missing_reducer":
        reducers = {}
    else:
        evaluators["e2e.wiring"].ref = api.VersionRef(name="e2e.wiring", revision="unregistered-revision")
    pipeline = api.EvaluationPipeline(study=study, backends={toy_backend.ref.name: toy_backend},
                                      evaluators=evaluators, reducers=reducers)
    # Level 2 names the preflight error; its message identifies the bad extension.
    with pytest.raises(api.ConfigurationError, match=expected_name.replace(".", r"\.")):
        pipeline.eval()
    assert toy_backend.calls == [], "Invalid evaluation configuration must fail before agent work"
    assert all(not evaluator.calls for evaluator in evaluators.values())
    assert all(reducer.calls == 0 for reducer in reducers.values())


def test_pipeline_reuse_starts_fresh_runs_without_hidden_cache(api, toy_backend):
    study, evaluators, reducers = build(api, toy_backend, "plain")
    pipeline = api.EvaluationPipeline(study=study, backends={toy_backend.ref.name: toy_backend},
                                      evaluators=evaluators, reducers=reducers)
    first = pipeline.eval()
    second = pipeline.eval()
    assert first.id != second.id and first.runs.id != second.runs.id
    assert {run.id for run in first.runs.runs}.isdisjoint(run.id for run in second.runs.runs)
    assert len(toy_backend.calls) == 2 * len(study.plan().assignments)


def test_one_call_convenience_returns_the_same_public_result_contract(api, toy_backend, tmp_path):
    study, evaluators, reducers = build(api, toy_backend, "plain")
    result = api.evaluate(
        study.candidates["A"], study.dataset, study.suite.metrics,
        backend=toy_backend, evaluators=evaluators, reducers=reducers,
        execution=study.execution, acceptance=study.suite.acceptance,
        summaries=study.suite.summaries,
    )
    assert isinstance(result, api.EvaluationResult)
    result.runs.validate().raise_for_errors()
    assert len(result.runs.runs) == len(study.dataset.units.rows)
    assert set(result.runs.plan.candidates) == {"A"}
    assert len(toy_backend.calls) == len(result.runs.runs)
    assert len(result.summary()) == 2
    assert all(result.explain(run.id).measurements for run in result.runs.runs)
    result.save(tmp_path / "convenience-result")
    loaded = api.EvaluationResult.load(tmp_path / "convenience-result")
    assert loaded.id == result.id
    assert all(loaded.explain(run.id).evidence for run in loaded.runs.runs)
