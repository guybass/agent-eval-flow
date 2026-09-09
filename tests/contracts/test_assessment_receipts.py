"""Real-pipeline regressions for receipt ownership and failed sibling isolation."""
from dataclasses import replace
from decimal import Decimal
import asyncio

import pytest

import agent_eval_flow as a
from agent_eval_flow.objects.values import observed
from tests.contracts.test_assessment_pipeline import Check, Collector, configured, pipeline
from tests.contracts.test_assessment_objects import make_capture


def charged(output, amount, tokens=0, *, status='completed'):
    resources = replace(output.activity.resources, cost_usd=observed(Decimal(amount)),
                        input_tokens=observed(tokens))
    return replace(output, activity=replace(output.activity, resources=resources, status=status))


def test_cumulative_progress_and_equal_terminal_duplicates_charge_once():
    class ProgressCheck(Check):
        async def evaluate(self, request, *, recorder):
            output = await super().evaluate(request, recorder=recorder)
            recorder.record_activity(charged(output, '0.10', 10, status='partial').activity)
            final = charged(output, '0.30', 30)
            recorder.record_activity(final.activity, final=True)
            recorder.record_activity(final.activity, final=True)
            return final

    plan, collector, check = configured(check=ProgressCheck())
    result = pipeline(plan, collector, check).eval()
    assert result.validate().valid
    assert result.check_coverage[0].status == 'completed'
    assert len(result.activities) == 2  # one collection, one evaluator invocation
    assert result.resources().cost_usd.value == Decimal('0.30')
    assert result.resources().input_tokens.value == 30
    assert result.resources(incremental=True).cost_usd.value == Decimal('0.30')


def test_conflicting_terminal_return_becomes_error_and_preserves_original_receipt():
    class ConflictingCheck(Check):
        async def evaluate(self, request, *, recorder):
            output = await super().evaluate(request, recorder=recorder)
            recorder.record_activity(charged(output, '0.20', 20).activity, final=True)
            return charged(output, '0.30', 30)

    plan, collector, check = configured(check=ConflictingCheck())
    result = pipeline(plan, collector, check).eval()
    assert result.assessments[0].status == 'error'
    assert result.assessments[0].conclusion == 'unknown'
    assert result.check_coverage[0].status == 'error'
    assert 'terminal' in result.assessments[0].reason.lower()
    assert result.resources().cost_usd.value == Decimal('0.20')
    assert result.resources().input_tokens.value == 20
    assert len([x for x in result.activities if x.phase == 'configuration_evaluation']) == 1


def test_observed_progress_cannot_retract_usage_after_it_was_acknowledged():
    class RetractingCheck(Check):
        async def evaluate(self, request, *, recorder):
            output = await super().evaluate(request, recorder=recorder)
            recorder.record_activity(charged(output, '0.20', 20, status='partial').activity)
            return charged(output, '0.10', 10)

    plan, collector, check = configured(check=RetractingCheck())
    result = pipeline(plan, collector, check).eval()
    activity = next(x for x in result.activities if x.phase == 'configuration_evaluation')
    assert result.assessments[0].status == 'error'
    assert activity.resources.cost_usd.value == Decimal('0.20')
    assert activity.resources.input_tokens.value == 20
    assert activity.inventory_complete.status == 'unknown'
    assert result.resources().cost_usd.status == 'unknown'


def test_final_collection_duplicate_is_accepted_and_contradiction_preserves_snapshot():
    class DuplicateCollector(Collector):
        def __init__(self, contradictory=False):
            super().__init__()
            self.contradictory = contradictory

        async def collect(self, request, *, recorder):
            capture = await super().collect(request, recorder=recorder)
            recorder.record_capture(capture, final=True)
            recorder.record_capture(capture, final=True)
            return replace(capture, status='partial') if self.contradictory else capture

    for contradictory in (False, True):
        plan, collector, check = configured(collector=DuplicateCollector(contradictory))
        result = pipeline(plan, collector, check).eval()
        capture = result.configuration['A']
        assert capture.snapshot is not None
        assert len(capture.activities) == 1
        assert capture.status == ('error' if contradictory else 'completed')
        assert (capture.error is not None) is contradictory
        assert result.validate().valid


def test_malformed_assessment_identity_is_excluded_and_sibling_receipt_survives():
    class WrongIdentityCheck(Check):
        async def evaluate(self, request, *, recorder):
            output = charged(await super().evaluate(request, recorder=recorder), '0.25', 25)
            recorder.record_activity(output.activity, final=True)
            if request.subject.candidate_id == 'A':
                return replace(output, assessment=replace(output.assessment,
                    subject=replace(request.subject, candidate_id='foreign-candidate')))
            return output

    plan, collector, check = configured(check=WrongIdentityCheck())
    other = replace(plan.candidates['A'], id='B')
    plan = replace(plan, candidates={**plan.candidates, 'B': other},
        configuration={**plan.configuration, 'B': plan.configuration['A']},
        checks=(replace(plan.checks[0], candidate_ids=('A', 'B')),), max_concurrency=2)
    result = pipeline(plan, collector, check).eval()
    rows = {row.subject.candidate_id: row for row in result.assessments}
    assert set(rows) == {'A', 'B'}
    assert rows['A'].status == 'error' and rows['A'].conclusion == 'unknown'
    assert rows['B'].status == 'ok' and rows['B'].conclusion == 'pass'
    assert len(check.calls) == 2 and len(result.activities) == 4
    assert result.resources().cost_usd.value == Decimal('0.50')
    assert not any(subject.candidate_id == 'foreign-candidate'
                   for activity in result.activities for subject in activity.subjects)
    assert result.validate().valid


def test_malformed_payload_retains_valid_receipt_in_its_returned_envelope():
    class WrongIdentityCheck(Check):
        async def evaluate(self, request, *, recorder):
            output = charged(await super().evaluate(request, recorder=recorder), '0.25', 25)
            # No progress callback: the returned envelope is the only usage receipt.
            return replace(output, assessment=replace(output.assessment,
                subject=replace(request.subject, candidate_id='foreign-candidate')))

    plan, collector, check = configured(check=WrongIdentityCheck())
    result = pipeline(plan, collector, check).eval()
    assert result.assessments[0].status == 'error'
    activity = next(row for row in result.activities if row.phase == 'configuration_evaluation')
    assert activity.resources.cost_usd.value == Decimal('0.25')
    assert activity.resources.input_tokens.value == 25
    assert result.resources().cost_usd.value == Decimal('0.25')


def test_declared_dependency_activity_links_preserve_single_owned_costs():
    class DependentCheck(Check):
        async def evaluate(self, request, *, recorder):
            output = charged(await super().evaluate(request, recorder=recorder),
                             '0.10' if request.check.id == 'check' else '0.20')
            refs = tuple(ref for assessment in request.dependencies.values()
                         for ref in assessment.activity_refs) + output.assessment.activity_refs
            return replace(output, assessment=replace(output.assessment, activity_refs=refs))

    plan, collector, check = configured(check=DependentCheck())
    plan = replace(plan, checks=(*plan.checks,
        replace(plan.checks[0], id='dependent', depends_on=('check',))))
    result = pipeline(plan, collector, check).eval()
    second = next(row for row in result.assessments if row.origin.check_id == 'dependent')
    first = next(row for row in result.assessments if row.origin.check_id == 'check')
    assert check.calls[1].dependencies['check'] == first
    assert len(second.activity_refs) == 2
    assert first.activity_refs[0] in second.activity_refs
    assert len(result.activities) == 3
    assert result.resources().cost_usd.value == Decimal('0.30')
    assert result.validate().valid


def test_undeclared_dependency_activity_is_not_admitted_as_valid_provenance():
    class SmuggledActivityCheck(Check):
        previous = None

        async def evaluate(self, request, *, recorder):
            output = await super().evaluate(request, recorder=recorder)
            if request.check.id == 'check':
                self.previous = output.assessment.activity_refs[0]
                return output
            assert self.previous is not None
            return replace(output, assessment=replace(output.assessment,
                activity_refs=(self.previous, *output.assessment.activity_refs)))

    plan, collector, check = configured(check=SmuggledActivityCheck())
    plan = replace(plan, checks=(*plan.checks, replace(plan.checks[0], id='independent')))
    result = pipeline(plan, collector, check).eval()
    rows = {row.origin.check_id: row for row in result.assessments}
    assert rows['check'].status == 'ok'
    assert rows['independent'].status == 'error'
    assert check.previous not in rows['independent'].activity_refs
    assert result.validate().valid


def test_saved_gate_decision_cannot_be_rewritten_without_its_source_evidence(api, toy_backend):
    from tests.e2e.test_toy_pipeline import build
    study, evaluators, reducers = build(api, toy_backend, 'plain')
    plan, collector, check = configured(study=study, check=Check(conclusion='fail'))
    plan = replace(plan, gates=(a.GateSpec(id='gate', candidate_ids=('A',),
        requirements=(a.ConclusionRequirement(check_id='check'),)),))
    result = pipeline(plan, collector, check, backends={toy_backend.ref.name: toy_backend},
                      evaluators=evaluators, reducers=reducers).eval()
    index = next(i for i, branch in enumerate(result.branches) if branch.gate_decisions)
    branch = result.branches[index]
    false_pass = replace(branch.gate_decisions[0], decision='pass', allowed=True)
    changed = list(result.branches)
    changed[index] = replace(branch, gate_decisions=(false_pass,))
    assert not replace(result, branches=tuple(changed)).validate().valid
    changed[index] = replace(branch, gate_decisions=(replace(branch.gate_decisions[0], requirements=()),))
    assert not replace(result, branches=tuple(changed)).validate().valid


def test_evaluator_cancellation_propagates_with_acknowledged_partial_receipts():
    from agent_eval_flow.evaluation.configuration import invoke_configuration_check
    _, _, capture, subject = make_capture()
    check_spec = a.CheckSpec(id='check', evaluator=Check.ref, candidate_ids=(subject.candidate_id,))
    request = a.AssessmentRequest(id='cancel-check', activity_id='cancel-activity', check=check_spec,
                                  subject=subject, capture=capture)
    artifact = a.ArtifactRef(uri='memory:partial-receipt', media_type='application/json', sha256='a' * 64)

    class CancelAfterReceipt(Check):
        stopped = asyncio.CancelledError('Requested cancellation after receipt')
        recorder = None

        async def evaluate(self, request, *, recorder):
            self.recorder = recorder
            output = charged(await super().evaluate(request, recorder=recorder), '0.20', 20, status='partial')
            recorder.record_activity(output.activity)
            recorder.record_artifact('partial.receipt', artifact)
            raise self.stopped

    check = CancelAfterReceipt()
    with pytest.raises(asyncio.CancelledError) as cancelled:
        asyncio.run(invoke_configuration_check(request, check))
    assert cancelled.value is check.stopped
    activity = cancelled.value.assessment_activity
    assert activity.id == request.activity_id and activity.request_id == request.id
    assert activity.status == 'cancelled' and activity.ended_at is None
    assert activity.resources.cost_usd.value == Decimal('0.20')
    assert activity.resources.input_tokens.value == 20
    assert activity.inventory_complete.status == 'unknown'
    assert cancelled.value.artifacts['partial.receipt'] == artifact
    assert check.recorder.closed
    with pytest.raises(a.CaptureValidationError, match='closed'):
        check.recorder.record_artifact('after-close', artifact)
