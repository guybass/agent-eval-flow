"""Real process capture -> common evidence -> saved, backend-free evaluation."""
from dataclasses import replace

from agent_eval_flow.objects.identity import plain
from agent_eval_flow.objects.values import observed
from agent_eval_flow.objects.runtime_evidence import RuntimeObservation
from agent_eval_flow.evaluation.runtime_checks import RuntimeEvidenceEvaluator
from tests.e2e.test_toy_pipeline import build


def test_collected_tool_evidence_survives_import_save_and_regrade(api, toy_backend, tmp_path):
    study, _, _ = build(api, toy_backend, "tool")
    native = study.run(backends={toy_backend.ref.name: toy_backend})
    calls = len(toy_backend.calls)
    original = plain(native)
    evaluator = RuntimeEvidenceEvaluator()
    metric = api.MetricSpec(id="tool.used", source=api.EvaluatorSource(ref=evaluator.ref),
        output_type="bool", role="diagnostic", params={"subject": "tool.name",
            "phase": "execution", "boundary": "tool_runner", "expected": "fixture.echo"})
    study = replace(study, suite=api.EvalSuite(id="runtime-evidence", version="1", metrics=(metric,), summaries=()))

    class Importer:
        ref = api.VersionRef(name="test.native-tool-import", revision="1")

        def read(self, source, *, plan):
            captured = api.RunSet.load(source)
            mapped = []
            for run in captured.runs:
                tool = next(e for e in run.events if e.kind == "tool_call")
                observation = RuntimeObservation(collector=self.ref, subject="tool.name", phase="execution",
                    boundary="tool_runner", component_id="tool.echo", call_id=tool.id,
                    declared=observed("fixture.echo", evidence=tool.outputs),
                    observed=observed(tool.fields["name"], evidence=tool.outputs), coverage="complete")
                mapped.append(replace(run, events=(*run.events, observation.to_event(
                    id=tool.id + "/observation", execution_id=tool.execution_id))))
            return api.ImportedCapture(runs=tuple(mapped), grading_inventory_complete=captured.grading_inventory_complete)

    native.save(tmp_path / "native")
    imported = api.RunSet.import_from(tmp_path / "native", plan=study.plan(), importer=Importer())
    assert plain(native) == original
    pipeline = api.EvaluationPipeline(study=study, evaluators={evaluator.ref.name: evaluator})
    result = pipeline.eval(runs=imported)
    assert len([m for m in result.measurements if m.metric == "tool.used" and m.key is None]) == len(imported.runs)
    assert all(m.value is True for m in result.measurements if m.metric == "tool.used")
    result.save(tmp_path / "result")
    loaded = api.EvaluationResult.load(tmp_path / "result")
    assert plain(loaded.runs) == plain(imported)
    changed = replace(metric, params={**metric.params, "expected": "different.tool"})
    regrade = api.EvaluationPipeline(study=replace(study, suite=replace(study.suite, version="2", metrics=(changed,))),
        evaluators={evaluator.ref.name: evaluator}).eval(runs=loaded.runs)
    assert len([m for m in regrade.measurements if m.metric == "tool.used" and m.key is None]) == len(imported.runs)
    assert all(m.value is False for m in regrade.measurements if m.metric == "tool.used")
    assert plain(regrade.runs) == plain(loaded.runs)
    regrade.report(tmp_path / "report.html")
    assert "Runtime evidence checks" in (tmp_path / "report.html").read_text(encoding="utf-8")
    assert len(toy_backend.calls) == calls
