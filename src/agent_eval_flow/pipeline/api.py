"""The configured public evaluation boundary and shared synchronous bridge."""
from __future__ import annotations

from typing import Awaitable, Callable, TypeVar

import anyio

from agent_eval_flow.execution.capture import observed
from agent_eval_flow.execution.preflight import PreparedExecution, configuration_error
from agent_eval_flow.execution.runner import RunExecutor
from .bindings import snapshot
from .preflight import prepare_evaluation

T = TypeVar("T")


def run_sync(operation: Callable[[], Awaitable[T]]) -> T:
    try:
        anyio.get_current_task()
    except anyio.NoEventLoopError:
        pass
    else:
        raise configuration_error("execution", "A loop is already running; use await pipeline.aeval(...) instead")
    return anyio.run(operation, backend="asyncio")


class EvaluationPipeline:
    def __init__(self, *, study, evaluators, backends=None, reducers=None,
                 job_backends=None, batch_evaluators=None):
        self._study = study
        self._bindings = snapshot(backends, job_backends, evaluators, batch_evaluators, reducers)

    @property
    def study(self):
        return self._study

    def eval(self, *, runs=None):
        return run_sync(lambda: self.aeval(runs=runs))

    async def aeval(self, *, runs=None):
        from agent_eval_flow.evaluation.engine import aevaluate
        prepared = prepare_evaluation(self._study, self._bindings, runs)
        if isinstance(prepared.source, PreparedExecution):
            captured = await RunExecutor().execute(prepared.source)
            performed = tuple(dict.fromkeys(activity.id for bundle in captured.native_grades
                                             for activity in bundle.activities))
            inventory = captured.grading_inventory_complete
        else:
            captured = prepared.source
            performed = ()
            inventory = observed(True, "This evaluation performed no new native grading")
        return await aevaluate(
            dataset=prepared.dataset, runs=captured, suite=self._study.suite,
            evaluators=self._bindings.evaluators, reducers=self._bindings.reducers,
            batch_evaluators=self._bindings.batch_evaluators, compiled=prepared.evaluation,
            performed_native_activity_ids=performed, native_grading_inventory_complete=inventory,
        )


def evaluate(candidate, data, metrics, *, backend, evaluators, execution,
             reducers=None, rubric=None, acceptance=None, summaries=()):
    from agent_eval_flow.objects import EvalSuite, Study
    suite = EvalSuite(id="evaluation", version="1", metrics=tuple(metrics), summaries=tuple(summaries),
                      rubric=rubric, acceptance=acceptance)
    study = Study(id="evaluation", project_id=data.id, question="Evaluate the configured candidate",
                  dataset=data, candidates={candidate.id: candidate}, suite=suite, execution=execution)
    return EvaluationPipeline(study=study, backends={candidate.backend.name: backend},
                              evaluators=evaluators, reducers=reducers).eval()
