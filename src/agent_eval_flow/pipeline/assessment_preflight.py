"""Validate the entire assessment request before calling any producer."""
from dataclasses import dataclass
from types import MappingProxyType

from agent_eval_flow import objects as o
from agent_eval_flow.execution.preflight import PreparedExecution, _resolve, configuration_error
from agent_eval_flow.objects.identity import semantic_fingerprint
from agent_eval_flow.objects.assessment_validation import validate_configuration_capture
from .preflight import prepare_evaluation


@dataclass(frozen=True)
class PreparedAssessment:
    plan: object
    bindings: object
    behavior: object
    configuration: object
    collectors: object
    evaluators: object
    binders: object


def validate_configuration(plan, configuration):
    if set(configuration) != set(plan.configuration):
        raise configuration_error('configuration', 'Supplied captures must exactly match configured candidates')
    for cid, capture in configuration.items():
        if not isinstance(capture, o.ConfigurationCapture):
            raise configuration_error('configuration.' + cid, 'Expected ConfigurationCapture')
        report = capture.validate()
        if not report.valid:
            raise o.CaptureValidationError(report)
        candidate, spec = plan.candidates[cid], plan.configuration[cid]
        compatibility = validate_configuration_capture(capture, candidate=candidate, spec=spec)
        if not compatibility.valid:
            raise o.CaptureCompatibilityError(compatibility)
        if capture.candidate_id != cid or capture.candidate_fingerprint != candidate.fingerprint():
            raise o.CaptureCompatibilityError('Configuration capture differs from requested candidate')
        if capture.snapshot is not None:
            source = capture.snapshot
            if source.collector != spec.collector or source.params_fingerprint != semantic_fingerprint('configuration-params', spec.params):
                raise o.CaptureCompatibilityError('Configuration collector or parameters differ from the plan')


def prepare_assessment(plan, bindings, runs=None, configuration=None):
    if not isinstance(plan, o.AssessmentPlan):
        raise configuration_error('plan', 'Expected AssessmentPlan')
    report = plan.validate()
    if not report.valid:
        raise o.ConfigurationError(report)
    if runs is not None and plan.behavior is None:
        raise configuration_error('runs', 'Saved runs require a behavioral study')
    if configuration is not None:
        validate_configuration(plan, configuration)
        configuration = MappingProxyType(dict(configuration))

    collectors = {} if configuration is not None else {
        cid: _resolve(spec.collector, bindings.collectors, 'configuration.' + cid)
        for cid, spec in plan.configuration.items()
    }
    evaluators = {check.id: _resolve(check.evaluator, bindings.configuration_evaluators,
                                    'checks.' + check.id) for check in plan.checks}
    for check in plan.checks:
        for name, columns in check.reference_columns.items():
            table = bindings.references.get(name)
            if name not in plan.reference_sets or not isinstance(table, o.DataTable):
                raise configuration_error('references.' + name, 'A selected versioned candidate-reference table is required')
            table_report = table.validate()
            if not table_report.valid:
                raise o.ConfigurationError(table_report)
            if 'candidate_id' not in table.key or table.schema.get('candidate_id') != 'str':
                raise configuration_error('references.' + name, 'Reference table key must include candidate_id (str)')
            if not set(columns) <= set(table.schema):
                raise configuration_error('references.' + name, 'Selected reference column is absent')
            if not set(check.candidate_ids) <= {row['candidate_id'] for row in table.rows}:
                raise configuration_error('references.' + name, 'A selected candidate has no reference row')

    behavior = None if plan.behavior is None else prepare_evaluation(plan.behavior, bindings.behavior, runs)
    binders = {}
    if behavior is not None and isinstance(behavior.source, PreparedExecution):
        native_candidates = {cid for job in behavior.source.plan.native_jobs for cid in job.config.candidate_ids}
        for cid, spec in plan.configuration.items():
            if cid not in plan.behavior.candidates:
                continue
            ref = plan.candidates[cid].backend
            binder = bindings.snapshot_binders.get(ref.name)
            if binder is None:
                if spec.snapshot_requirement == 'verified':
                    raise configuration_error('snapshot_binders.' + ref.name, 'Fresh verified execution requires a compatible SnapshotBinder')
                continue
            try:
                caps = binder.capabilities()
                if not binder.ref.revision or not isinstance(caps, o.SnapshotCapabilities):
                    raise ValueError('Expected versioned binder and SnapshotCapabilities')
                if ref not in caps.backends:
                    raise ValueError('Snapshot binder does not support the full target backend revision')
                if (cid in native_candidates and not caps.native) or (cid not in native_candidates and not caps.direct):
                    raise ValueError('Snapshot binder does not support this dispatch-unit kind')
                if spec.snapshot_requirement == 'verified' and not caps.verified:
                    raise ValueError('Snapshot binder cannot verify configuration parity')
            except Exception as exc:
                raise configuration_error('snapshot_binders.' + ref.name, str(exc)) from exc
            binders[ref.name] = binder
    return PreparedAssessment(plan, bindings, behavior, configuration,
                              MappingProxyType(collectors), MappingProxyType(evaluators),
                              MappingProxyType(binders))
