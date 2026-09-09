"""Reducer contracts use declared fixture arithmetic, not metric science."""
from dataclasses import replace

import pytest

from tests.e2e.support import WiringReducer
from tests.e2e.test_toy_pipeline import build


@pytest.mark.parametrize("reducer,expected", [
    ("mean", {"A": 11, "B": 29}),
    ("sum", {"A": 22, "B": 58}),
    ("min", {"A": 11, "B": 29}),
    ("max", {"A": 11, "B": 29}),
    # Constant samples establish transport without choosing a quantile method.
    ("p95", {"A": 11, "B": 29}),
    ("rate", {"A": 0.0, "B": 1.0}),
])
def test_builtin_reducers_use_task_rows_and_preserve_coverage(api, toy_backend, reducer, expected):
    study, evaluators, _ = build(api, toy_backend, "plain")
    capture = study.run(backends={toy_backend.ref.name: toy_backend})
    summary = api.SummarySpec(id="fixture_aggregate",
        metric="task.accepted" if reducer == "rate" else "wiring", reducer=reducer)
    suite = replace(study.suite, summaries=(summary,), acceptance=api.AcceptanceRule(
        all_of=(api.Threshold(metric="wiring", op=">", value=20),)))
    result = suite.evaluate(dataset=study.dataset, runs=capture, evaluators=evaluators)

    rows = {row.candidate_id: row for row in result.summary()}
    assert set(rows) == {"A", "B"}
    for candidate_id, row in rows.items():
        own_ids = {run.id for run in capture.for_candidate(candidate_id)}
        assert row.summary_id == summary.id
        assert row.value.status == "observed" and row.value.value == expected[candidate_id]
        assert (row.planned, row.observed, row.missing, row.errors, row.not_applicable) == (2, 2, 0, 0, 0)
        assert set(row.included_run_ids) == own_ids and row.exclusions == {}
    # Wiring emits both task and detail rows. Details must not double the sum
    # or increase the candidate's population from two tasks to four polygons.
    assert sum(row.metric == "wiring" and row.key is None for row in result.measurements) == 4
    assert sum(row.metric == "wiring" and row.key is not None for row in result.measurements) == 4
    assert len(toy_backend.calls) == 4
    assert len(evaluators["e2e.wiring"].calls) == 8
    assert set(evaluators["e2e.wiring"].calls.values()) == {1}


class InvalidReducer(WiringReducer):
    """A real extension callback deliberately violates its output contract."""

    def __init__(self, api, invalid):
        super().__init__(api)
        self.invalid, self.received = invalid, []

    def reduce(self, spec, *, candidate_id, assignments, runs, measurements, task_scores):
        self.received.append((spec, candidate_id, assignments, runs, measurements, task_scores))
        result = super().reduce(spec, candidate_id=candidate_id, assignments=assignments,
                                runs=runs, measurements=measurements, task_scores=task_scores)
        if self.invalid == "exception":
            raise RuntimeError("Owned custom reducer failed after receiving complete source coverage")
        if self.invalid == "candidate_id":
            return replace(result, candidate_id="outside-this-study")
        if self.invalid == "summary_id":
            return replace(result, summary_id="not-the-requested-summary")
        if self.invalid == "source_counts":
            # The counts still add up, but contradict the actual source rows.
            return replace(result, observed=0, missing=result.planned)
        if self.invalid == "foreign_included_id":
            return replace(result, included_run_ids=("outside-this-capture", *result.included_run_ids[1:]))
        return replace(result, exclusions={result.included_run_ids[0]: "Also included: invalid partition"})


@pytest.mark.parametrize("invalid", ["exception", "candidate_id", "summary_id",
                                      "source_counts", "foreign_included_id", "overlapping_exclusion"])
def test_failed_or_malformed_custom_reducer_cannot_publish_a_known_summary(api, toy_backend, invalid):
    study, evaluators, _ = build(api, toy_backend, "plain")
    capture = study.run(backends={toy_backend.ref.name: toy_backend})
    reducer = InvalidReducer(api, invalid)
    spec = study.suite.summaries[1]
    suite = replace(study.suite, summaries=(spec,))
    result = suite.evaluate(dataset=study.dataset, runs=capture, evaluators=evaluators,
                            reducers={reducer.ref.name: reducer})

    assert reducer.calls == len(reducer.received) == 2
    assert {receipt[1] for receipt in reducer.received} == {"A", "B"}
    for received_spec, candidate_id, assignments, runs, measurements, task_scores in reducer.received:
        own_ids = {run.id for run in capture.for_candidate(candidate_id)}
        assert received_spec == spec
        assert {run.id for run in runs} == own_ids
        assert {assignment.id for assignment in assignments} == {run.assignment_id for run in runs}
        assert all(assignment.candidate_id == candidate_id for assignment in assignments)
        assert {assignment.unit["task_id"] for assignment in assignments} == {"toy-1", "toy-2"}
        assert {assignment.repetition for assignment in assignments} == {0}
        assert {score.run_id for score in task_scores} == own_ids
        assert {row.run_id for row in measurements} == own_ids
        sources = [row for row in measurements if row.metric == spec.metric and row.key is None]
        assert len(sources) == 2 and all(row.status == "ok" for row in sources)

    summaries = {row.candidate_id: row for row in result.summary()}
    assert set(summaries) == {"A", "B"}
    for candidate_id, summary in summaries.items():
        own_ids = {run.id for run in capture.for_candidate(candidate_id)}
        assert summary.summary_id == spec.id
        assert summary.value.status == "unknown" and summary.value.value is None
        assert summary.value.reason and summary.reason
        assert (summary.planned, summary.observed, summary.missing,
                summary.errors, summary.not_applicable) == (2, 2, 0, 0, 0)
        included, excluded = set(summary.included_run_ids), set(summary.exclusions)
        assert len(included) == len(summary.included_run_ids)
        assert not included & excluded and included | excluded == own_ids
        assert all(summary.exclusions.values())
    selection = result.select(api.SelectionPolicy(id="must-not-rank-invalid-reduction", objectives=(
        api.ObjectiveTerm(metric=spec.id, direction="maximize"),)))
    assert selection.selected_ids == ()
    assert result.runs == capture
    assert all(run.status == "completed" for run in result.runs.runs)
    assert all(row.status == "ok" for row in result.measurements if row.metric == spec.metric)
    assert len(toy_backend.calls) == 4 and reducer.calls == 2
    assert len(evaluators["e2e.wiring"].calls) == 8
    assert set(evaluators["e2e.wiring"].calls.values()) == {1}
