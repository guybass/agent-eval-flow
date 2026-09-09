"""Score/result consumers and versioned persistence; fixture arithmetic only."""
from dataclasses import replace
from decimal import Decimal
import importlib
import json

import pytest

from tests.e2e.support import plain, zero_resources
from tests.e2e.test_toy_pipeline import build


class TableMetric:
    """A user evaluator supplying declared fixture values, never a scoring engine."""

    def __init__(self, api, capture, values, states=None):
        self.api, self.values, self.states = api, values, states or {}
        self.ref = api.VersionRef(name="test.table-metric", revision="v1")
        self.candidates = {row.id: row.candidate_id for row in capture.plan.assignments}
        self.calls = []

    def compute(self, spec, context, run):
        self.calls.append((run.id, spec.id))
        candidate = self.candidates[run.assignment_id]
        status, basis = self.states.get((candidate, spec.id), ("ok", "observed"))
        value = self.values[candidate][spec.id] if status == "ok" else None
        evidence = tuple(self.api.EvidenceRef(artifact=ref, description="Owned process output")
                         for ref in run.artifacts.values())
        return self.api.MetricOutput(
            task=self.api.Measurement(run_id=run.id, metric=spec.id, value=value,
                status=status, basis=basis if status == "ok" else None,
                reason="Explicit fixture measurement", evidence=evidence),
            evaluation_resources=zero_resources(self.api),
        )


def evaluate_table(api, backend, values, types, *, states=None, rubric=None, acceptance=None):
    study, _, _ = build(api, backend, "plain")
    capture = study.run(backends={backend.ref.name: backend})
    check = TableMetric(api, capture, values, states)
    suite = api.EvalSuite(
        id="table-suite", version="v1",
        metrics=tuple(api.MetricSpec(id=name,
            source=api.EvaluatorSource(ref=check.ref), output_type=kind, role="diagnostic")
            for name, kind in types.items()),
        summaries=tuple(api.SummarySpec(id="mean_" + name, metric=name, reducer="mean")
                        for name, kind in types.items() if kind != "text"),
        rubric=rubric, acceptance=acceptance,
    )
    study = replace(study, suite=suite)
    result = api.EvaluationPipeline(study=study, evaluators={check.ref.name: check}).eval(runs=capture)
    return study, result, check


def test_rubric_keeps_explained_contributions_and_no_implicit_acceptance(api, toy_backend):
    rubric = api.Rubric(terms=(api.ScoreTerm(metric="a", points=60.0),
                              api.ScoreTerm(metric="b", points=40.0)))
    _, result, check = evaluate_table(api, toy_backend,
        {"A": {"a": 0.5, "b": 1.0}, "B": {"a": 1.0, "b": 0.0}},
        {"a": "float", "b": "float"}, rubric=rubric)
    assignments = {item.id: item for item in result.runs.plan.assignments}
    for run in result.runs.runs:
        score = result.explain(run.id).score
        assert score.score.value == {"A": 70.0, "B": 60.0}[assignments[run.assignment_id].candidate_id]
        assert sum(item.earned for item in score.contributions) == score.score.value
        assert all(item.evidence and item.reason for item in score.contributions)
        assert score.acceptance == "unknown"
    assert len(check.calls) == 8


@pytest.mark.parametrize("state", ["missing", "error", "not_applicable"])
def test_incomplete_rubric_keeps_known_contributions_without_renormalizing(api, toy_backend, state):
    rubric = api.Rubric(terms=(api.ScoreTerm(metric="a", points=60.0),
                              api.ScoreTerm(metric="b", points=40.0)))
    _, result, _ = evaluate_table(api, toy_backend,
        {"A": {"a": 0.5, "b": 1.0}, "B": {"a": 0.5, "b": 1.0}},
        {"a": "float", "b": "float"}, rubric=rubric,
        states={(candidate, "b"): (state, None) for candidate in ("A", "B")})
    for score in result.task_scores:
        assert score.score.status == "unknown" and score.score.value is None
        contributions = {item.metric: item for item in score.contributions}
        assert contributions["a"].earned == 30.0
        assert contributions["b"].earned is None and contributions["b"].possible == 40.0


@pytest.mark.parametrize("known_value,missing,expected", [(1, False, "pass"), (0, False, "fail"),
                                                          (1, True, "unknown"), (0, True, "fail")])
def test_acceptance_conjunction_preserves_unknown_and_known_failure(api, toy_backend, known_value, missing, expected):
    rule = api.AcceptanceRule(all_of=(api.Threshold(metric="known", op=">", value=0),
                                      api.Threshold(metric="other", op=">", value=0)))
    states = {(candidate, "other"): ("missing", None) for candidate in ("A", "B")} if missing else None
    _, result, _ = evaluate_table(api, toy_backend,
        {candidate: {"known": known_value, "other": 1} for candidate in ("A", "B")},
        {"known": "int", "other": "int"}, acceptance=rule, states=states)
    assert all(score.acceptance == expected for score in result.task_scores)


def test_estimated_measurement_basis_propagates_to_score_acceptance_and_summary(api, toy_backend):
    _, result, _ = evaluate_table(api, toy_backend,
        {candidate: {"fraction": 1.0} for candidate in ("A", "B")}, {"fraction": "float"},
        states={(candidate, "fraction"): ("ok", "estimated") for candidate in ("A", "B")},
        rubric=api.Rubric(terms=(api.ScoreTerm(metric="fraction", points=100.0),)),
        acceptance=api.AcceptanceRule(all_of=(api.Threshold(metric="quality.score", op=">=", value=80),)))
    assert all(score.score.status == "estimated" and score.acceptance_basis == "estimated"
               for score in result.task_scores)
    assert all(summary.value.status == "estimated" for summary in result.summary())


@pytest.mark.parametrize("mode,expected", [("lexicographic", ("B",)), ("weighted", ("A",)),
                                           ("pareto", ("A", "B"))])
def test_selection_modes_use_the_same_recorded_summaries(api, toy_backend, mode, expected):
    _, result, check = evaluate_table(api, toy_backend,
        {"A": {"quality": 0.8, "cost": 0.1}, "B": {"quality": 1.0, "cost": 0.9}},
        {"quality": "float", "cost": "float"})
    policy = api.SelectionPolicy(id=mode, mode=mode, objectives=(
        api.ObjectiveTerm(metric="mean_quality", direction="maximize", weight=1.0, bounds=(0.0, 1.0)),
        api.ObjectiveTerm(metric="mean_cost", direction="minimize", weight=3.0, bounds=(0.0, 1.0)),
    ))
    choice = result.select(policy)
    assert set(choice.selected_ids) == set(expected)
    assert choice.status == ("frontier" if mode == "pareto" else "selected")
    assert {row.candidate_id for row in choice.rows} == {"A", "B"}
    assert len(check.calls) == 8 and len(toy_backend.calls) == 4


def test_ties_and_no_eligible_candidate_are_explicit(api, toy_backend):
    _, result, _ = evaluate_table(api, toy_backend,
        {candidate: {"value": 1.0} for candidate in ("A", "B")}, {"value": "float"})
    policy = api.SelectionPolicy(id="same", objectives=(
        api.ObjectiveTerm(metric="mean_value", direction="maximize"),))
    tied = result.select(policy)
    assert tied.status == "tie" and set(tied.selected_ids) == {"A", "B"}
    none = result.select(replace(policy, requirements=(api.Threshold(metric="mean_value", op=">", value=2),)))
    assert none.status == "none_eligible" and none.selected_ids == ()
    assert all(row.eligibility == "fail" and row.reasons for row in none.rows)


@pytest.mark.parametrize("basis,allow,selected", [("estimated", False, ()), ("estimated", True, ("A", "B")),
                                                ("missing", True, ())])
def test_unknown_objectives_cannot_win_and_estimates_require_opt_in(api, toy_backend, basis, allow, selected):
    states = {(candidate, "value"): ("missing", None) if basis == "missing" else ("ok", basis)
              for candidate in ("A", "B")}
    _, result, _ = evaluate_table(api, toy_backend,
        {candidate: {"value": 1.0} for candidate in ("A", "B")}, {"value": "float"}, states=states)
    choice = result.select(api.SelectionPolicy(id="uncertain", allow_estimates=allow,
        objectives=(api.ObjectiveTerm(metric="mean_value", direction="minimize"),)))
    assert set(choice.selected_ids) == set(selected)
    assert all(row.reasons for row in choice.rows if row.candidate_id not in selected)


def test_full_population_summary_does_not_use_available_value_for_selection(api, toy_backend):
    study, _, _ = build(api, toy_backend, "plain")
    capture = study.run(backends={toy_backend.ref.name: toy_backend})
    check = TableMetric(api, capture, {candidate: {"probe": 1.0} for candidate in ("A", "B")})
    original_compute = check.compute
    missing_id = capture.runs[0].id

    def compute(spec, context, run):
        output = original_compute(spec, context, run)
        if run.id == missing_id:
            return replace(output, task=replace(output.task, value=None, status="missing", basis=None))
        return output

    check.compute = compute
    suite = api.EvalSuite(id="partial", version="1", metrics=(api.MetricSpec(
        id="probe", source=api.EvaluatorSource(ref=check.ref), output_type="float", role="diagnostic"),),
        summaries=(api.SummarySpec(id="mean_probe", metric="probe", reducer="mean"),))
    result = suite.evaluate(dataset=study.dataset, runs=capture, evaluators={check.ref.name: check})
    summary = next(row for row in result.summary() if row.missing)
    assert summary.planned == 2 and summary.observed == 1 and summary.missing == 1
    assert summary.value.status == "unknown" and summary.value.value is None
    assert summary.available_value == 1.0
    choice = result.select(api.SelectionPolicy(id="available", objectives=(
        api.ObjectiveTerm(metric="mean_probe", direction="maximize"),)))
    assert summary.candidate_id not in choice.selected_ids


def test_comparison_reports_unavailable_intervals_without_regrading(api, toy_backend):
    _, result, check = evaluate_table(api, toy_backend,
        {"A": {"value": 1.0}, "B": {"value": 3.0}}, {"value": "float"})
    comparison = result.compare("A", "B", metrics=("mean_value",), confidence=0.95)
    assert comparison.rows[0].delta.value == 2.0
    assert comparison.rows[0].interval is None and comparison.rows[0].interval_note
    assert comparison.task_count == 2 and comparison.cluster_count == 2
    assert len(check.calls) == 4 and len(toy_backend.calls) == 4


@pytest.mark.parametrize("invalid", ["unknown_summary", "weighted_without_bounds", "invalid_confidence"])
def test_invalid_result_requests_fail_explicitly(api, toy_backend, invalid):
    _, result, _ = evaluate_table(api, toy_backend,
        {candidate: {"value": 1.0} for candidate in ("A", "B")}, {"value": "float"})
    with pytest.raises((api.ValidationError, importlib.import_module("pydantic").ValidationError)):
        if invalid == "invalid_confidence":
            result.compare("A", "B", metrics=("mean_value",), confidence=1.5)
        else:
            result.select(api.SelectionPolicy(id="bad", mode="weighted" if invalid == "weighted_without_bounds" else "lexicographic",
                objectives=(api.ObjectiveTerm(metric="absent" if invalid == "unknown_summary" else "mean_value",
                                              direction="maximize"),)))


def test_typed_wire_roundtrip_preserves_metric_unions_and_arbitrary_json(api, toy_backend, tmp_path):
    values = {"flag": True, "count": 1, "ratio": 1.5, "money": Decimal("0.20"), "label": "1"}
    study, result, check = evaluate_table(api, toy_backend, {"A": values, "B": values},
        {"flag": "bool", "count": "int", "ratio": "float", "money": "decimal", "label": "text"})
    literal = {"type": "decimal", "value": "not-a-number", "nested": [None, True, 1, "1"]}
    runs = replace(result.runs, runs=tuple(replace(run, output=literal, output_state="available")
                                         for run in result.runs.runs))
    result = replace(result, runs=runs)
    result.save(tmp_path / "result")
    manifest = json.loads((tmp_path / "result" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format"] == "agent-eval-flow" and manifest["schema_version"] == "0.4"
    assert manifest["kind"] == "evaluation_result"
    loaded = api.EvaluationResult.load(tmp_path / "result")
    for row in loaded.measurements:
        assert type(row.value) is type(values[row.metric])
        assert row.value == values[row.metric]
    assert all(plain(run.output) == literal for run in loaded.runs.runs)
    assert loaded.activities == result.activities
    assert loaded.performed_activity_ids == result.performed_activity_ids
    study.dataset.save(tmp_path / "dataset")
    assert api.EvalDataset.load(tmp_path / "dataset").fingerprint() == study.dataset.fingerprint()
    assert len(check.calls) == 20


@pytest.mark.parametrize("corruption", ["future_version", "wrong_kind", "unknown_outer_field", "dangling_activity"])
def test_loading_invalid_manifests_fails_instead_of_guessing(api, toy_backend, tmp_path, corruption):
    study, checks, reducers = build(api, toy_backend, "plain")
    result = study.evaluate(backends={toy_backend.ref.name: toy_backend}, evaluators=checks, reducers=reducers)
    folder = tmp_path / "corrupt"
    result.save(folder)
    path = folder / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if corruption == "future_version":
        manifest["schema_version"] = "999.0"
    elif corruption == "wrong_kind":
        manifest["kind"] = "dataset"
    elif corruption == "unknown_outer_field":
        manifest["data"]["undeclared"] = "reject-me"
    else:
        manifest["data"]["measurements"][0]["activity_ids"] = ["no-such-activity"]
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises((api.StorageError, api.ValidationError)):
        api.EvaluationResult.load(folder)


def test_html_report_escapes_untrusted_explanations_and_reads_saved_records(api, toy_backend, tmp_path):
    study, checks, reducers = build(api, toy_backend, "plain")
    result = study.evaluate(backends={toy_backend.ref.name: toy_backend}, evaluators=checks, reducers=reducers)
    payload = '<script id="fixture-injection">alert("fixture")</script>'
    result = replace(result, measurements=tuple(replace(row, reason=payload) for row in result.measurements))
    path = result.report(tmp_path / "report.html")
    rendered = path.read_text(encoding="utf-8")
    assert "<html" in rendered.lower()
    assert "fixture-injection" in rendered and payload not in rendered
    assert "&lt;script" in rendered
    assert len(toy_backend.calls) == 4
    assert all(count == 1 for check in checks.values() for count in check.calls.values())
