"""Whole-request preflight and saved capture compatibility."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_eval_flow.objects import (
    CaptureCompatibilityError, CaptureValidationError, ConfigurationError,
    EvalDataset, RunSet, ValidationIssue, ValidationReport,
)
from agent_eval_flow.objects.identity import canonical_bytes, typed_key
from agent_eval_flow.execution.planning import build_plan
from agent_eval_flow.execution.preflight import PreparedExecution, prepare_execution

if TYPE_CHECKING:
    from agent_eval_flow.evaluation.compiler import CompiledEvaluation


@dataclass(frozen=True)
class PreparedEvaluation:
    dataset: EvalDataset
    evaluation: CompiledEvaluation
    source: PreparedExecution | RunSet


def check_capture(study, runs):
    expected = build_plan(study)
    actual, issues = runs.plan, []

    def check(path, left, right):
        if left != right:
            issues.append(ValidationIssue(path=path, severity="error",
                                          message="Saved capture differs from requested execution"))

    check("project_id", expected.project_id, actual.project_id)
    check("dataset.fingerprint", expected.dataset.fingerprint, actual.dataset.fingerprint)
    check("dataset.unit_key", expected.dataset.unit_key, actual.dataset.unit_key)
    check("dataset.cluster_by", expected.dataset.cluster_by, actual.dataset.cluster_by)
    check("candidates", {key: value.fingerprint() for key, value in expected.candidates.items()},
          {key: value.fingerprint() for key, value in actual.candidates.items()})
    # Native settings are arbitrary JSON: Python mapping equality would wrongly
    # conflate True with 1 (and 1 with 1.0) in behavior-changing options.
    check("execution", canonical_bytes(expected.execution), canonical_bytes(actual.execution))
    check("environment", canonical_bytes(expected.environment), canonical_bytes(actual.environment))

    def assignments(plan):
        return Counter((item.candidate_id, typed_key(item.unit, plan.dataset.unit_key), item.repetition)
                       for item in plan.assignments)

    check("assignments", assignments(expected), assignments(actual))
    return ValidationReport(issues=tuple(issues))


def prepare_evaluation(study, bindings, runs=None):
    from agent_eval_flow.evaluation.compiler import compile_suite
    report = study.validate()
    if not report.valid:
        raise ConfigurationError(report)
    compiled = compile_suite(study.suite, evaluators=bindings.evaluators,
                             batch_evaluators=bindings.batch_evaluators, reducers=bindings.reducers)
    if runs is None:
        source = prepare_execution(study, bindings.backends, bindings.job_backends)
    else:
        report = runs.validate()
        if not report.valid:
            raise CaptureValidationError(report)
        report = check_capture(study, runs)
        if not report.valid:
            raise CaptureCompatibilityError(report)
        source = runs
    return PreparedEvaluation(study.dataset, compiled, source)
