"""Public evaluation boundary contracts, written before the implementation.

Fixture values exercise data transport, dependency reuse and resource records.
They do not assess the scientific validity of a metric or a real agent.
"""
from collections import Counter
from dataclasses import replace
from decimal import Decimal
import importlib

import pytest

from tests.e2e.support import artifact, observed, zero_resources
from tests.e2e.test_toy_pipeline import build


def validation_errors(api):
    return api.ValidationError, importlib.import_module("pydantic").ValidationError


@pytest.fixture
def captured(api, toy_backend):
    study, _, _ = build(api, toy_backend, "plain")
    return study, study.run(backends={toy_backend.ref.name: toy_backend})


def specification(api, ref, *, id="probe", depends_on=(), mode="single"):
    return api.MetricSpec(
        id=id, source=api.EvaluatorSource(ref=ref, mode=mode),
        output_type="int", role="diagnostic", depends_on=depends_on,
    )


def suite_for(api, metrics, **kwargs):
    return api.EvalSuite(id="contract.evaluation", version="v1",
                         metrics=tuple(metrics), summaries=(), **kwargs)


def evaluate(api, captured, suite, *, single=None, batch=None):
    study, runs = captured
    return suite.evaluate(dataset=study.dataset, runs=runs,
                          evaluators=single or {}, batch_evaluators=batch or {})


def task_rows(result, metric="probe"):
    return {row.run_id: row for row in result.measurements
            if row.metric == metric and row.key is None}


class SingleProbe:
    """Test-owned plugin, including deliberately malformed plugin responses."""

    def __init__(self, api, mode="valid"):
        self.api, self.mode = api, mode
        self.ref = api.VersionRef(name="contract.single", revision="v1")
        self.calls = Counter()
        self.contexts = {}

    def compute(self, spec, context, run):
        a = self.api
        self.calls[(run.id, spec.id)] += 1
        self.contexts[(run.id, spec.id)] = context
        if self.mode == "raise" or (self.mode == "dependency_error" and spec.id == "source"):
            raise RuntimeError("Owned evaluator failure, not an agent failure")
        value = 7
        if spec.depends_on:
            # A consumer defines its own missing-input behavior. The engine must
            # deliver the source status rather than suppressing this callback.
            value = sum(row.value if row.status == "ok" else -1
                        for row in context.measurements.values())
        row = a.Measurement(run_id=run.id, metric=spec.id, value=value,
                            status="ok", basis="observed", reason="Owned transport value")
        details = ()
        if self.mode == "foreign_run":
            row = replace(row, run_id="not-a-requested-run")
        elif self.mode == "foreign_metric":
            row = replace(row, metric="not-a-requested-metric")
        elif self.mode == "bool_for_int":
            row = replace(row, value=True)
        elif self.mode == "duplicate_details":
            detail = replace(row, key={"page": 1})
            details = (detail, detail)
        return a.MetricOutput(task=row, details=details,
                              evaluation_resources=zero_resources(a))


class BatchProbe:
    def __init__(self, api, mode="valid"):
        self.api, self.mode = api, mode
        self.ref = api.VersionRef(name="contract.batch", revision="v1")
        self.calls = []

    async def compute_batch(self, request):
        a = self.api
        self.calls.append(request)
        if self.mode == "raise":
            raise RuntimeError("Owned batch transport failed")
        rows = tuple(a.Measurement(
            run_id=item.run.id, metric=request.metric.id, value=11,
            status="ok", basis="observed", reason="Owned batch value",
            activity_ids=(request.id,),
        ) for item in reversed(request.items))
        activity = a.EvaluationActivity(
            id=request.id, evaluator=self.ref, config_fingerprint=request.config_fingerprint,
            run_ids=tuple(item.run.id for item in request.items), status="completed",
            phase="post_run", resources=replace(zero_resources(a),
                cost_usd=observed(a, Decimal("0.08"))),
        )
        if self.mode == "foreign_activity":
            activity = replace(activity, id="not-the-requested-activity")
        elif self.mode == "wrong_fingerprint":
            activity = replace(activity, config_fingerprint="different-configuration")
        elif self.mode == "wrong_run_scope":
            activity = replace(activity, run_ids=("not-a-requested-run",))
        elif self.mode == "foreign_metric":
            rows = tuple(replace(row, metric="not-the-requested-metric") for row in rows)
        elif self.mode == "duplicate_tasks":
            rows += (rows[0],)
        elif self.mode == "missing_task":
            rows = rows[1:]
        return a.BatchMetricOutput(measurements=rows, activity=activity)


@pytest.mark.parametrize("problem", [
    "unknown_dependency", "cycle", "quality_score_dependency",
    "task_accepted_dependency", "reserved_metric_id",
])
def test_invalid_metric_graph_is_rejected(api, problem):
    ref = api.VersionRef(name="contract.single", revision="v1")
    source = specification(api, ref, id="source")
    dependency = {
        "unknown_dependency": "undeclared", "cycle": "source",
        "quality_score_dependency": "quality.score",
        "task_accepted_dependency": "task.accepted",
        "reserved_metric_id": "source",
    }[problem]
    with pytest.raises(validation_errors(api)):
        if problem == "cycle":
            metrics = (replace(source, depends_on=("consumer",)),
                       specification(api, ref, id="consumer", depends_on=(dependency,)))
        elif problem == "reserved_metric_id":
            metrics = (replace(source, id="run.cost_usd"),)
        else:
            metrics = (source, specification(api, ref, id="consumer", depends_on=(dependency,)))
        suite_for(api, metrics).validate().raise_for_errors()


@pytest.mark.parametrize("field", ["params", "depends_on"])
def test_retained_grade_source_cannot_reconfigure_past_work(api, field):
    with pytest.raises(validation_errors(api)):
        metric = api.MetricSpec(
            id="probe", source=api.NativeGradeSource(channel="historical", native_metric="value"),
            output_type="int", role="diagnostic",
            **{field: {"new_setting": True} if field == "params" else ("run.completed",)},
        )
        suite_for(api, (metric,)).validate().raise_for_errors()


def test_diamond_dependencies_dispatch_once_and_expose_only_declared_inputs(api, captured):
    callback = SingleProbe(api)
    metrics = (
        specification(api, callback.ref, id="last", depends_on=("left", "right")),
        specification(api, callback.ref, id="right", depends_on=("source",)),
        specification(api, callback.ref, id="source"),
        specification(api, callback.ref, id="left", depends_on=("source",)),
    )
    result = evaluate(api, captured, suite_for(api, metrics), single={callback.ref.name: callback})
    assert set(callback.calls.values()) == {1}
    assert len(callback.calls) == len(captured[1].runs) * len(metrics)
    for metric in metrics:
        for run in captured[1].runs:
            context = callback.contexts[(run.id, metric.id)]
            assert set(context.measurements) == set(metric.depends_on)
            assert all(row.run_id == run.id for row in context.measurements.values())
            assert context.unit == context.inputs.unit
            assert context.references["private_oracle"][0]["private_marker"] == "EVALUATOR_ONLY"
            assert context.evaluation_cost_scope == ("model",)
    assert {row.value for row in task_rows(result, "last").values()} == {14}


def test_failed_dependency_reaches_consumer_without_a_hidden_penalty(api, captured):
    callback = SingleProbe(api, "dependency_error")
    metrics = (specification(api, callback.ref, id="source"),
               specification(api, callback.ref, id="consumer", depends_on=("source",)))
    result = evaluate(api, captured, suite_for(api, metrics), single={callback.ref.name: callback})
    assert {row.status for row in task_rows(result, "source").values()} == {"error"}
    assert {row.value for row in task_rows(result, "consumer").values()} == {-1}
    assert set(callback.calls.values()) == {1}
    assert result.runs == captured[1]


@pytest.mark.parametrize("malformation", [
    "foreign_run", "foreign_metric", "bool_for_int", "duplicate_details",
])
def test_invalid_single_output_becomes_an_error_for_the_requested_cell(api, captured, malformation):
    callback = SingleProbe(api, malformation)
    result = evaluate(api, captured, suite_for(api, (specification(api, callback.ref),)),
                      single={callback.ref.name: callback})
    rows = task_rows(result)
    assert set(rows) == {run.id for run in captured[1].runs}
    assert all(row.status == "error" and row.value is None and row.basis is None
               and row.reason for row in rows.values())
    assert not any(row.run_id == "not-a-requested-run" or row.metric == "not-a-requested-metric"
                   for row in result.measurements)
    assert result.runs == captured[1]


def test_single_exception_keeps_agent_status_and_records_unknown_grading_usage(api, captured):
    callback = SingleProbe(api, "raise")
    result = evaluate(api, captured, suite_for(api, (specification(api, callback.ref),)),
                      single={callback.ref.name: callback})
    assert result.runs == captured[1]
    assert {row.status for row in task_rows(result).values()} == {"error"}
    assert len(result.activities) == len(captured[1].runs)
    assert all(activity.status == "error" and activity.resources.cost_usd.status == "unknown"
               for activity in result.activities)
    assert result.incremental_evaluation_resources().cost_usd.value is None
    assert result.performed_grading_inventory_complete.value is True


@pytest.mark.parametrize("malformation", [
    "foreign_activity", "wrong_fingerprint", "wrong_run_scope", "foreign_metric", "duplicate_tasks",
])
def test_invalid_batch_output_does_not_enter_results_as_valid_measurements(api, captured, malformation):
    callback = BatchProbe(api, malformation)
    result = evaluate(api, captured, suite_for(api, (specification(api, callback.ref, mode="batch"),)),
                      batch={callback.ref.name: callback})
    assert len(callback.calls) == 1
    rows = task_rows(result)
    assert set(rows) == {run.id for run in captured[1].runs}
    assert all(row.status == "error" and row.value is None and row.reason for row in rows.values())
    assert result.runs == captured[1]
    assert {activity.id for activity in result.activities} == {callback.calls[0].id}


def test_missing_batch_row_is_explicit_without_discarding_other_rows(api, captured):
    callback = BatchProbe(api, "missing_task")
    result = evaluate(api, captured, suite_for(api, (specification(api, callback.ref, mode="batch"),)),
                      batch={callback.ref.name: callback})
    request = callback.calls[0]
    missing_id = request.items[-1].run.id  # callback deliberately reversed then removed one row
    rows = task_rows(result)
    assert len(rows) == len(request.items)
    assert rows[missing_id].status == "missing" and rows[missing_id].value is None
    assert rows[missing_id].reason
    assert all(row.status == "ok" and row.value == 11
               for run_id, row in rows.items() if run_id != missing_id)
    assert result.evaluation_resources.cost_usd.value == Decimal("0.08")


def test_batch_exception_has_one_error_activity_and_one_error_per_requested_run(api, captured):
    callback = BatchProbe(api, "raise")
    result = evaluate(api, captured, suite_for(api, (specification(api, callback.ref, mode="batch"),)),
                      batch={callback.ref.name: callback})
    assert len(callback.calls) == len(result.activities) == 1
    activity = result.activities[0]
    assert activity.id == callback.calls[0].id and activity.status == "error"
    assert set(activity.run_ids) == {run.id for run in captured[1].runs}
    assert {row.status for row in task_rows(result).values()} == {"error"}
    assert result.incremental_evaluation_resources().cost_usd.status == "unknown"
    assert result.runs == captured[1]


def native_bundle(api, captured, tmp_path, *, id="pass-one", evaluator="grader-one", value=7,
                  cost="0.03", channel="historical", details=False, empty=False):
    runs = captured[1].runs
    receipt = tmp_path / (id + ".json")
    receipt.write_text('{"fixture": "native grade receipt"}', encoding="utf-8")
    source = artifact(api, receipt)
    evidence = (api.EvidenceRef(artifact=source, locator="/fixture", description="Native source"),)
    activity = api.EvaluationActivity(
        id=id, evaluator=api.VersionRef(name=evaluator, revision="v1"), config_fingerprint=None,
        run_ids=tuple(run.id for run in runs), status="completed", phase="native_verifier",
        resources=replace(zero_resources(api), cost_usd=observed(api, Decimal(cost))),
        artifacts={"native.grades": source},
    )
    grades = []
    for run in (() if empty else runs):
        grade = api.NativeGrade(
            id=id + "/" + run.id, run_id=run.id, native_metric="value", activity_id=id,
            value=value, status="ok", basis="observed", reason="Native fixture value", evidence=evidence,
        )
        grades.append(grade)
        if details:
            grades.append(replace(grade, id=grade.id + "/page-1", key={"page": 1}))
    return api.NativeGradeBundle(
        id="bundle-" + id, channel=channel, grades=tuple(grades), activities=(activity,),
        projection=api.ProjectionReport(
            mapper=api.VersionRef(name="contract.native-mapper", revision="v1"),
            source_format=api.VersionRef(name="contract.receipt", revision="v1"), sources=(source,),
        ),
    )


def native_suite(api, **selector):
    return suite_for(api, (api.MetricSpec(
        id="probe", source=api.NativeGradeSource(channel="historical", native_metric="value", **selector),
        output_type="int", role="diagnostic",
    ),))


def with_bundles(captured, *bundles):
    return captured[0], replace(captured[1], native_grades=tuple(bundles))


def test_missing_native_grade_stays_missing_without_launching_a_verifier(api, captured):
    result = evaluate(api, captured, native_suite(api))
    assert {row.status for row in task_rows(result).values()} == {"missing"}
    assert all(row.value is None and row.reason for row in task_rows(result).values())
    assert result.activities == result.performed_activity_ids == ()
    assert result.incremental_evaluation_resources().cost_usd.value == Decimal("0")


def test_ambiguous_native_grades_report_matching_ids_instead_of_selecting_latest(api, captured, tmp_path):
    first = native_bundle(api, captured, tmp_path)
    second = native_bundle(api, captured, tmp_path, id="pass-two", value=99)
    result = evaluate(api, with_bundles(captured, first, second), native_suite(api))
    for row in task_rows(result).values():
        assert row.status == "error" and row.value is None
        assert first.activities[0].id in row.reason and second.activities[0].id in row.reason
    assert result.performed_activity_ids == ()
    assert result.evaluation_resources.cost_usd.value == Decimal("0.06")


@pytest.mark.parametrize("filter_by", ["evaluator", "activity_id"])
def test_native_selector_picks_only_the_explicit_historical_pass(api, captured, tmp_path, filter_by):
    first = native_bundle(api, captured, tmp_path)
    second = native_bundle(api, captured, tmp_path, id="pass-two", evaluator="grader-two", value=99)
    selected = second.activities[0]
    selector = {filter_by: selected.evaluator if filter_by == "evaluator" else selected.id}
    result = evaluate(api, with_bundles(captured, first, second), native_suite(api, **selector))
    assert {row.value for row in task_rows(result).values()} == {99}
    assert all(row.activity_ids == (selected.id,) for row in task_rows(result).values())
    # A selector chooses measurements, not which real grading charges happened.
    assert result.evaluation_resources.cost_usd.value == Decimal("0.06")
    assert result.performed_activity_ids == ()


def test_native_detail_rows_follow_the_selected_task_pass_and_preserve_evidence(api, captured, tmp_path):
    first = native_bundle(api, captured, tmp_path, details=True)
    second = native_bundle(api, captured, tmp_path, id="pass-two", value=99, details=True)
    result = evaluate(api, with_bundles(captured, first, second),
                      native_suite(api, activity_id=second.activities[0].id))
    assert len(task_rows(result)) == len(captured[1].runs)
    details = [row for row in result.measurements if row.metric == "probe" and row.key is not None]
    assert len(details) == len(captured[1].runs)
    assert all(row.key == {"page": 1} and row.value == 99 and
               row.activity_ids == (second.activities[0].id,) for row in details)
    expected = {(grade.run_id, grade.key is None): grade for grade in second.grades}
    assert all(row.evidence == expected[(row.run_id, row.key is None)].evidence
               for row in result.measurements if row.metric == "probe")


@pytest.mark.parametrize("violation", ["absent_activity", "outside_activity_scope", "conflicting_activity"])
def test_inconsistent_native_grade_capture_fails_structural_validation(api, captured, tmp_path, violation):
    bundle = native_bundle(api, captured, tmp_path)
    with pytest.raises(validation_errors(api)):
        if violation == "absent_activity":
            bundle = replace(bundle, activities=())
            bundles = (bundle,)
        elif violation == "outside_activity_scope":
            bundle = replace(bundle, activities=(replace(bundle.activities[0], run_ids=()),))
            bundles = (bundle,)
        else:
            conflicting = replace(bundle.activities[0], resources=replace(
                zero_resources(api), cost_usd=observed(api, Decimal("500"))))
            bundles = (bundle, replace(bundle, id="conflict", grades=(), activities=(conflicting,)))
        replace(captured[1], native_grades=bundles).validate().raise_for_errors()


def test_identical_activity_references_across_bundles_are_charged_once(api, captured, tmp_path):
    bundle = native_bundle(api, captured, tmp_path)
    second_channel = replace(bundle, id="another-channel", channel="other-check", grades=tuple(
        replace(grade, id=grade.id + "/other-check") for grade in bundle.grades))
    result = evaluate(api, with_bundles(captured, bundle, second_channel), native_suite(api))
    assert result.activities == bundle.activities
    assert result.evaluation_resources.cost_usd.value == Decimal("0.03")
    assert result.incremental_evaluation_resources().cost_usd.value == Decimal("0")


@pytest.mark.parametrize("inventory", ["known_incomplete", "unknown"])
def test_retained_inventory_gap_does_not_contaminate_complete_fresh_inventory(api, captured, tmp_path, inventory):
    bundle = native_bundle(api, captured, tmp_path)
    complete = observed(api, False) if inventory == "known_incomplete" else api.Observation(
        value=None, status="unknown", reason="Native exporter did not declare grading completeness")
    study, runs = with_bundles(captured, bundle)
    runs = replace(runs, grading_inventory_complete=complete)
    callback = BatchProbe(api)
    result = evaluate(api, (study, runs), suite_for(api, (specification(api, callback.ref, mode="batch"),)),
                      batch={callback.ref.name: callback})
    assert len(result.activities) == 2
    assert result.evaluation_resources.cost_usd.status == "unknown"
    assert result.evaluation_resources.cost_usd.value is None
    assert result.performed_grading_inventory_complete.value is True
    assert result.incremental_evaluation_resources().cost_usd.value == Decimal("0.08")
    assert set(result.performed_activity_ids) == {callback.calls[0].id}


def test_grading_scope_mismatch_preserves_native_scope_and_other_known_quantities(api, captured, tmp_path):
    bundle = native_bundle(api, captured, tmp_path)
    activity = replace(bundle.activities[0], resources=replace(
        bundle.activities[0].resources, cost_scope=("compute",), input_tokens=observed(api, 13)))
    bundle = replace(bundle, activities=(activity,))
    result = evaluate(api, with_bundles(captured, bundle), native_suite(api))
    assert result.activities[0].resources.cost_scope == ("compute",)
    assert result.activities[0].resources.cost_usd.value == Decimal("0.03")
    assert result.evaluation_resources.cost_scope == ("model",)
    assert result.evaluation_resources.cost_usd.status == "unknown"
    assert result.evaluation_resources.cost_usd.reason
    assert result.evaluation_resources.input_tokens.value == 13


def test_unused_and_failed_native_grading_still_counts_with_fresh_grading(api, captured, tmp_path):
    unused = native_bundle(api, captured, tmp_path, channel="unused-channel", cost="0.03")
    failed = native_bundle(api, captured, tmp_path, id="failed-pass", cost="0.02", empty=True)
    failed = replace(failed, activities=(replace(failed.activities[0], status="error",
        error=api.ErrorRecord(code="grader_failed", message="Failed after recorded spend")),))
    callback = BatchProbe(api)
    result = evaluate(api, with_bundles(captured, unused, failed),
        suite_for(api, (specification(api, callback.ref, mode="batch"),)),
        batch={callback.ref.name: callback})
    assert len(result.activities) == 3
    assert result.evaluation_resources.cost_usd.value == Decimal("0.13")
    assert result.incremental_evaluation_resources().cost_usd.value == Decimal("0.08")
    assert result.runs.runs == captured[1].runs


def test_user_check_can_accept_an_existing_artifact_without_an_implicit_completion_gate(api, captured):
    study, runs = captured
    statuses = ("timed_out", "agent_error", "paused", "cancelled")
    changed = tuple(replace(
        run, status=status, ended_at=None if status == "paused" else run.ended_at,
        executions=tuple(replace(execution, status=status,
            ended_at=None if status == "paused" else execution.ended_at)
            for execution in run.executions),
    ) for run, status in zip(runs.runs, statuses))
    runs = replace(runs, runs=changed)
    callback = SingleProbe(api)
    suite = suite_for(api, (specification(api, callback.ref),), acceptance=api.AcceptanceRule(
        all_of=(api.Threshold(metric="probe", op="==", value=7),)))
    result = evaluate(api, (study, runs), suite, single={callback.ref.name: callback})
    assert {score.acceptance for score in result.task_scores} == {"pass"}
    assert tuple(run.status for run in result.runs.runs) == statuses
    assert {row.value for row in task_rows(result).values()} == {7}
