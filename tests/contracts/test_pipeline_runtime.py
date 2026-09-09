"""Observable orchestration, binding and compatibility contracts."""
import asyncio
from dataclasses import replace
from decimal import Decimal
import threading

import pytest

from tests.e2e.test_toy_pipeline import build


def pipeline(api, study, backend, evaluators, reducers):
    return api.EvaluationPipeline(
        study=study, backends={backend.ref.name: backend},
        evaluators=evaluators, reducers=reducers,
    )


def test_async_facade_runs_and_regrades_without_nested_event_loop(api, toy_backend):
    study, checks, reducers = build(api, toy_backend, "plain")
    configured = pipeline(api, study, toy_backend, checks, reducers)

    async def exercise():
        first = await configured.aeval()
        second = await api.EvaluationPipeline(
            study=study, evaluators=checks, reducers=reducers,
        ).aeval(runs=first.runs)
        assert first.id != second.id
        assert first.runs.id == second.runs.id
        assert len(toy_backend.calls) == 4
        return second

    assert asyncio.run(exercise()).summary()


def test_sync_facade_inside_event_loop_rejects_before_work(api, toy_backend):
    study, checks, reducers = build(api, toy_backend, "plain")
    configured = pipeline(api, study, toy_backend, checks, reducers)

    async def exercise():
        with pytest.raises(api.ConfigurationError, match="aeval"):
            configured.eval()

    asyncio.run(exercise())
    assert not toy_backend.calls
    assert all(not item.calls for item in checks.values())


def test_registry_maps_are_snapshotted_at_configuration(api, toy_backend):
    study, checks, reducers = build(api, toy_backend, "plain")
    backends = {toy_backend.ref.name: toy_backend}
    configured = api.EvaluationPipeline(study=study, backends=backends,
                                         evaluators=checks, reducers=reducers)
    backends.clear()
    checks.clear()
    reducers.clear()
    assert configured.eval().summary()
    assert len(toy_backend.calls) == 4


@pytest.mark.parametrize("change", ["public_input", "private_reference", "candidate", "policy", "project"])
def test_saved_capture_incompatibility_fails_without_new_work(api, toy_backend, change):
    study, checks, reducers = build(api, toy_backend, "plain")
    capture = study.run(backends={toy_backend.ref.name: toy_backend})
    if change == "public_input":
        units = replace(study.dataset.units, rows=tuple(
            {**row, "text": "changed"} for row in study.dataset.units.rows))
        changed = replace(study, dataset=replace(study.dataset, units=units))
    elif change == "private_reference":
        private = study.dataset.references["private_oracle"]
        private = replace(private, rows=tuple(
            {**row, "private_marker": "changed-reference"} for row in private.rows))
        changed = replace(study, dataset=replace(study.dataset,
                         references={"private_oracle": private}))
    elif change == "candidate":
        changed = replace(study, candidates={**study.candidates,
                          "A": study.candidates["A"].derive(id="A", settings={"changed": True})})
    elif change == "policy":
        changed = replace(study, execution=replace(study.execution, repetitions=2))
    else:
        changed = replace(study, project_id="a-different-project")
    with pytest.raises(api.CaptureCompatibilityError):
        api.EvaluationPipeline(study=changed, evaluators=checks,
                               reducers=reducers).eval(runs=capture)
    assert len(toy_backend.calls) == 4
    assert all(not item.calls for item in checks.values())


@pytest.mark.parametrize("capability", ["wall_time_limit", "token_limit", "cost_limit", "reset_state"])
def test_unsupported_execution_condition_fails_before_dispatch(api, toy_backend, capability):
    study, checks, reducers = build(api, toy_backend, "plain")
    budget = study.execution.budget
    if capability == "token_limit":
        budget = replace(budget, max_tokens=100)
    elif capability == "cost_limit":
        budget = replace(budget, max_cost_usd=Decimal("0.10"))
    study = replace(study, execution=replace(study.execution, budget=budget))

    class RestrictedBackend:
        ref = toy_backend.ref

        def capabilities(self):
            return replace(toy_backend.capabilities(), **{capability: False})

        def run(self, request, *, recorder):
            return toy_backend.run(request, recorder=recorder)

    with pytest.raises(api.ConfigurationError):
        pipeline(api, study, RestrictedBackend(), checks, reducers).eval()
    assert not toy_backend.calls


def test_direct_dispatch_respects_configured_concurrency_and_keeps_requests_separate(api, toy_backend):
    study, checks, reducers = build(api, toy_backend, "plain")
    study = replace(study, execution=replace(study.execution, max_concurrency=2))
    lock = threading.Lock()

    class ConcurrentBackend:
        ref = toy_backend.ref
        active = 0
        peak = 0

        def capabilities(self):
            return toy_backend.capabilities()

        def run(self, request, *, recorder):
            with lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            try:
                return toy_backend.run(request, recorder=recorder)
            finally:
                with lock:
                    self.active -= 1

    backend = ConcurrentBackend()
    result = pipeline(api, study, backend, checks, reducers).eval()
    assert 1 <= backend.peak <= 2 and backend.active == 0
    assert len({run.id for run in result.runs.runs}) == 4
    assert all(run.status == "completed" for run in result.runs.runs)
    assignments = {item.id: item for item in result.runs.plan.assignments}
    for run in result.runs.runs:
        assert run.output["task_id"] == assignments[run.assignment_id].unit["task_id"]
