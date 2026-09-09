"""Compile declarative suites using the standard library dependency sorter."""
from dataclasses import dataclass
from graphlib import TopologicalSorter, CycleError
from types import MappingProxyType
from decimal import Decimal
import math

from .. import objects as o
from ..objects.identity import semantic_fingerprint

PRIMITIVE_TYPES = {
    "run.completed": "bool", "run.duration_s": "float", "run.cost_usd": "decimal",
    "run.input_tokens": "int", "run.output_tokens": "int", "run.human_minutes": "float",
}
POST_TYPES = {"quality.score": "float", "task.accepted": "bool"}
NUMERIC_TYPES = {"bool", "int", "float", "decimal"}


def issue(path, message):
    return o.ValidationIssue(path=path, message=message, severity="error")


def value_type(value):
    return {bool: "bool", int: "int", float: "float", Decimal: "decimal", str: "text"}.get(type(value))


def validate_suite(suite):
    issues = []
    metrics = {metric.id: metric for metric in suite.metrics}
    if len(metrics) != len(suite.metrics):
        issues.append(issue("metrics", "Metric IDs must be unique"))
    types = {**PRIMITIVE_TYPES, **POST_TYPES, **{m.id: m.output_type for m in suite.metrics}}
    if len({s.id for s in suite.summaries}) != len(suite.summaries):
        issues.append(issue("summaries", "Summary IDs must be unique"))
    for metric in suite.metrics:
        if metric.id in PRIMITIVE_TYPES or metric.id in POST_TYPES or metric.id.startswith("run."):
            issues.append(issue("metrics." + metric.id, "Reserved helper ID cannot be redefined"))
        if len(set(metric.depends_on)) != len(metric.depends_on):
            issues.append(issue("metrics." + metric.id, "Dependency IDs must be unique"))
        for dependency in metric.depends_on:
            if dependency not in metrics and dependency not in PRIMITIVE_TYPES:
                issues.append(issue("metrics." + metric.id, "Unknown or post-score dependency: " + dependency))
        if isinstance(metric.source, o.NativeGradeSource) and (metric.params or metric.depends_on):
            issues.append(issue("metrics." + metric.id, "Retained native grades cannot be reconfigured"))
    try:
        tuple(TopologicalSorter({m.id: m.depends_on for m in suite.metrics}).static_order())
    except CycleError as exc:
        issues.append(issue("metrics", "Metric dependency cycle: " + str(exc.args[1])))

    def check_reference(metric, path):
        if metric not in types:
            issues.append(issue(path, "Unknown metric: " + metric))
            return None
        if metric == "quality.score" and suite.rubric is None:
            issues.append(issue(path, "quality.score requires a rubric"))
        return types[metric]

    if suite.rubric is not None:
        for term in suite.rubric.terms:
            if not math.isfinite(term.points) or term.points < 0:
                issues.append(issue("rubric", "Rubric points must be finite and nonnegative"))
            kind = check_reference(term.metric, "rubric")
            if kind not in NUMERIC_TYPES or term.metric in POST_TYPES:
                issues.append(issue("rubric", "Rubric terms require pre-score numeric or boolean metrics"))
    if suite.acceptance is not None:
        for threshold in suite.acceptance.all_of:
            kind = check_reference(threshold.metric, "acceptance")
            other = value_type(threshold.value)
            if threshold.metric == "task.accepted":
                issues.append(issue("acceptance", "Acceptance cannot refer to itself"))
            numeric = kind in {"int", "float", "decimal"} and other in {"int", "float", "decimal"}
            compatible = numeric or (threshold.op in {"==", "!="} and kind == other)
            if not compatible:
                issues.append(issue("acceptance", "Threshold operands have incompatible types"))
    for summary in suite.summaries:
        kind = check_reference(summary.metric, "summaries." + summary.id)
        if isinstance(summary.reducer, str):
            if summary.params:
                issues.append(issue("summaries." + summary.id, "Built-in reducers do not take params"))
            if summary.reducer not in {"mean", "sum", "min", "max", "p95", "rate"}:
                issues.append(issue("summaries." + summary.id, "Unknown built-in reducer"))
            if (summary.reducer == "rate" and kind != "bool") or kind not in NUMERIC_TYPES:
                issues.append(issue("summaries." + summary.id, "Reducer is incompatible with metric type"))
    return o.ValidationReport(issues=tuple(issues))


@dataclass(frozen=True)
class CompiledEvaluation:
    suite: object
    metric_order: tuple
    primitive_ids: tuple
    post_score_ids: tuple
    metrics_by_id: object
    evaluators: object
    batch_evaluators: object
    reducers: object
    metric_definition_fingerprints: object


def compile_suite(suite, evaluators=None, batch_evaluators=None, reducers=None):
    report = validate_suite(suite)
    if not report.valid:
        raise o.ConfigurationError(report)
    evaluators, batch_evaluators, reducers = dict(evaluators or {}), dict(batch_evaluators or {}), dict(reducers or {})
    issues = []

    def bind(ref, registry, path, method):
        callback = registry.get(ref.name)
        if callback is None or getattr(callback, "ref", None) != ref:
            issues.append(issue(path, "Missing or incompatible implementation binding: " + ref.name))
        elif not callable(getattr(callback, method, None)):
            issues.append(issue(path, "Implementation does not provide " + method))

    for metric in suite.metrics:
        if isinstance(metric.source, o.EvaluatorSource):
            batch = metric.source.mode == "batch"
            bind(metric.source.ref, batch_evaluators if batch else evaluators, "metrics." + metric.id,
                 "compute_batch" if batch else "compute")
    for summary in suite.summaries:
        if isinstance(summary.reducer, o.VersionRef):
            bind(summary.reducer, reducers, "summaries." + summary.id, "reduce")
    if issues:
        raise o.ConfigurationError(o.ValidationReport(issues=tuple(issues)))
    requested = list(dict.fromkeys(
        [d for m in suite.metrics for d in m.depends_on]
        + [s.metric for s in suite.summaries]
        + ([t.metric for t in suite.rubric.terms] if suite.rubric else [])
        + ([t.metric for t in suite.acceptance.all_of] if suite.acceptance else [])))
    metric_ids = {m.id for m in suite.metrics}
    order = tuple(m for m in TopologicalSorter({m.id: m.depends_on for m in suite.metrics}).static_order() if m in metric_ids)
    return CompiledEvaluation(suite, order, tuple(m for m in requested if m in PRIMITIVE_TYPES),
        tuple(m for m in requested if m in POST_TYPES), MappingProxyType({m.id: m for m in suite.metrics}),
        MappingProxyType(evaluators), MappingProxyType(batch_evaluators), MappingProxyType(reducers),
        MappingProxyType({m.id: semantic_fingerprint("metric-definition", m) for m in suite.metrics}))
