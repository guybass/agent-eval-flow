"""Candidate-scoped check invocation with explicit reference projection."""
from dataclasses import replace
from uuid import uuid4

from agent_eval_flow import objects as o
from agent_eval_flow.execution.capture import capture_error, unknown
from agent_eval_flow.execution.configuration_assessment import AssessmentBuffer, _equal
from agent_eval_flow.execution.snapshot_assessment import candidate_subject
from .compiler import value_type


def make_assessment_request(plan, bindings, check, capture, dependencies):
    references = {}
    for name, selected in check.reference_columns.items():
        table = bindings.references[name]
        columns = tuple(dict.fromkeys((*table.key, *selected)))
        references[name] = tuple(
            {column: row[column] for column in columns}
            for row in table.rows if row['candidate_id'] == capture.candidate_id
        )
    return o.AssessmentRequest(
        id='check/' + uuid4().hex, activity_id='assessment/' + uuid4().hex,
        check=check, subject=candidate_subject(plan.candidates[capture.candidate_id], capture.snapshot),
        capture=capture, references=references, dependencies=dependencies,
        reference_sets={name: plan.reference_sets[name] for name in check.reference_columns},
        reference_keys={name: bindings.references[name].key for name in check.reference_columns},
    )


def validate_output(output, request):
    if not isinstance(output, o.AssessmentOutput):
        raise capture_error('assessment.output', 'Evaluator must return AssessmentOutput')
    report = output.validate()
    if not report.valid:
        raise o.CaptureValidationError(report)
    assessment, coverage = output.assessment, output.coverage
    origin = o.ConfigurationOrigin(request_id=request.id, check_id=request.check.id,
                                   check_fingerprint=request.check_fingerprint,
                                   evaluator=request.check.evaluator)
    if not _equal(assessment.subject, request.subject) or not _equal(assessment.origin, origin):
        raise capture_error('assessment.output', 'Assessment differs from allocated subject/check/request')
    if (coverage.candidate_id != request.subject.candidate_id or coverage.check_id != request.check.id
            or coverage.request_id != request.id or coverage.assessment_id != assessment.id):
        raise capture_error('assessment.coverage', 'Coverage differs from allocated request or assessment')
    values = {value.id: value for value in assessment.values}
    if len(values) != len(assessment.values) or set(values) != set(request.check.output_types):
        raise capture_error('assessment.values', 'Returned values must match every declared output exactly once')
    for name, value in values.items():
        if value.status == 'ok' and value_type(value.value) != request.check.output_types[name]:
            raise capture_error('assessment.values.' + name, 'Value does not match its strict declared type')
    allowed = {('assessment', request.activity_id)}
    for dependency in request.dependencies.values():
        allowed.update((ref.namespace, ref.id) for ref in dependency.activity_refs)
    actual = {(ref.namespace, ref.id) for ref in assessment.activity_refs}
    if ('assessment', request.activity_id) not in actual or not actual <= allowed:
        raise capture_error('assessment.activity_refs', 'Assessment references an unallocated or undeclared activity')
    entries = {entry.id for entry in request.capture.snapshot.entries} if request.capture.snapshot else set()
    for finding in assessment.findings:
        subject = finding.subject
        candidate = subject.candidate if isinstance(subject, o.ComponentSubject) else subject
        if not _equal(candidate, request.subject):
            raise capture_error('assessment.findings', 'Finding refers to a foreign subject')
        if isinstance(subject, o.ComponentSubject) and subject.entry_id is not None and subject.entry_id not in entries:
            raise capture_error('assessment.findings', 'Finding refers to an absent snapshot entry')


async def invoke_configuration_check(request, evaluator):
    recorder = AssessmentBuffer(request)
    try:
        output = await evaluator.evaluate(request, recorder=recorder)
        # Preserve attributed raw receipts even if the normalized payload is rejected.
        if isinstance(output, o.AssessmentOutput):
            for name, artifact in output.artifacts.items():
                recorder.record_artifact(name, artifact)
            # The receipt remains useful even if a normalized value or subject is invalid.
            recorder.record_activity(output.activity, final=True)
        validate_output(output, request)
        return replace(output, activity=recorder.activity, artifacts=recorder.artifacts)
    except Exception as exc:
        error = o.ErrorRecord(code=type(exc).__name__, message=str(exc) or type(exc).__name__)
        activity = recorder.failure_activity(error)
        assessment = o.Assessment(
            id=request.id + '/error', subject=request.subject,
            origin=o.ConfigurationOrigin(request_id=request.id, check_id=request.check.id,
                check_fingerprint=request.check_fingerprint, evaluator=request.check.evaluator),
            status='error', conclusion='unknown', reason=error.message,
            values=tuple(o.AssessmentValue(id=name, value=None, status='error', basis=None,
                         reason=error.message) for name in request.check.output_types),
            activity_refs=(o.ActivityRef(namespace='assessment', id=activity.id),),
        )
        return o.AssessmentOutput(
            assessment=assessment, activity=activity, artifacts=recorder.artifacts,
            coverage=o.CheckCoverage(candidate_id=request.subject.candidate_id, check_id=request.check.id,
                request_id=request.id, assessment_id=assessment.id, status='error', reason=error.message,
                inventory_complete=unknown('Evaluator failed; requested coverage is not established')),
        )
    except BaseException as exc:
        error = o.ErrorRecord(code=type(exc).__name__, message=str(exc) or type(exc).__name__)
        setattr(exc, 'assessment_activity', recorder.failure_activity(error, cancelled=True))
        setattr(exc, 'artifacts', dict(recorder.artifacts))
        raise
    finally:
        recorder.closed = True
