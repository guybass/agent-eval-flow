"""Observable unified-flow behavior, through the real public pipeline."""
import asyncio
from dataclasses import replace
from threading import Event

import anyio
import pytest

import agent_eval_flow as a
from agent_eval_flow.objects.identity import semantic_fingerprint
from agent_eval_flow.objects.values import observed, zero_resources
from tests.e2e.test_toy_pipeline import build
from tests.contracts.test_native_jobs import native_study


class Collector:
    ref = a.VersionRef(name='test.collector', revision='1')

    def __init__(self):
        self.calls = []

    async def collect(self, request, *, recorder):
        self.calls.append(request)
        subject = a.CandidateSubject(candidate_id=request.candidate.id,
                                     candidate_fingerprint=request.candidate.fingerprint())
        activity = a.AssessmentActivity(id=request.activity_id, request_id=request.id,
            implementation=self.ref, input_fingerprint=request.input_fingerprint, subjects=(subject,),
            phase='collection', status='completed', resources=zero_resources(),
            inventory_complete=observed(True))
        return a.ConfigurationCapture(id=request.id + '/capture', request_id=request.id,
            candidate_id=request.candidate.id, candidate_fingerprint=request.candidate.fingerprint(),
            snapshot=a.CandidateSnapshot(candidate_id=request.candidate.id,
                candidate_fingerprint=request.candidate.fingerprint(), entries=(), collector=self.ref,
                params_fingerprint=semantic_fingerprint('configuration-params', request.spec.params),
                inventory_complete=observed(True)), status='completed', activities=(activity,))


class Check:
    ref = a.VersionRef(name='test.check', revision='1')

    def __init__(self, *, conclusion='pass', fail=False):
        self.calls = []
        self.conclusion = conclusion
        self.fail = fail

    async def evaluate(self, request, *, recorder):
        self.calls.append(request)
        if self.fail:
            raise RuntimeError('test scanner disconnected')
        activity = a.AssessmentActivity(id=request.activity_id, request_id=request.id,
            implementation=self.ref, input_fingerprint=request.input_fingerprint,
            subjects=(request.subject,), phase='configuration_evaluation', status='completed',
            resources=zero_resources(), inventory_complete=observed(True))
        assessment = a.Assessment(id=request.id + '/assessment', subject=request.subject,
            origin=a.ConfigurationOrigin(request_id=request.id, check_id=request.check.id,
                check_fingerprint=request.check_fingerprint, evaluator=self.ref),
            status='ok', conclusion=self.conclusion, reason='Fixture explicit conclusion',
            activity_refs=(a.ActivityRef(namespace='assessment', id=activity.id),))
        return a.AssessmentOutput(assessment=assessment, activity=activity,
            coverage=a.CheckCoverage(candidate_id=request.subject.candidate_id, check_id=request.check.id,
                request_id=request.id, assessment_id=assessment.id, status='completed',
                inventory_complete=observed(True), omitted_suppressed_findings=False))


def configured(*, study=None, collector=None, check=None, strict=False, **changes):
    collector, check = collector or Collector(), check or Check()
    candidates = study.candidates if study else {'A': a.Candidate(id='A',
        backend=a.VersionRef(name='unused', revision='1'), components={})}
    plan = a.AssessmentPlan(id='unified', project_id=study.project_id if study else 'project',
        candidates=candidates, behavior=study,
        configuration={cid: a.ConfigurationSpec(collector=collector.ref,
            snapshot_requirement='verified' if strict else 'record_only') for cid in candidates},
        checks=(a.CheckSpec(id='check', evaluator=check.ref, candidate_ids=tuple(candidates)),))
    return replace(plan, **changes), collector, check


def pipeline(plan, collector, check, **bindings):
    return a.AssessmentPipeline(plan=plan, collectors={collector.ref.name: collector},
        configuration_evaluators={check.ref.name: check}, **bindings)


def test_configuration_only_reuse_and_registry_snapshot(tmp_path):
    plan, collector, check = configured()
    collectors, checks = {collector.ref.name: collector}, {check.ref.name: check}
    flow = a.AssessmentPipeline(plan=plan, collectors=collectors, configuration_evaluators=checks)
    collectors.clear()
    checks.clear()
    assert not collector.calls and not check.calls
    first = flow.eval()
    assert first.behavior_result is None
    assert len(first.assessments) == 1 and len(first.activities) == 2
    first.save(tmp_path / 'assessment')
    restored = a.AssessmentResult.load(tmp_path / 'assessment')
    assert restored == first
    reused = a.AssessmentPipeline(plan=plan, configuration_evaluators={check.ref.name: check}).eval(
        configuration=restored.configuration)
    assert len(collector.calls) == 1 and len(check.calls) == 2
    assert len(reused.activities) == 2 and len(reused.performed_activity_refs) == 1
    assert reused.configuration == first.configuration
    assert reused.assessments[0].origin.request_id != first.assessments[0].origin.request_id


@pytest.mark.parametrize('problem', ['missing_evaluator', 'cycle', 'missing_capture', 'stale_capture'])
def test_invalid_requests_fail_before_work(problem):
    plan, collector, check = configured()
    supplied = None
    if problem in ('missing_capture', 'stale_capture'):
        first = pipeline(plan, collector, check).eval()
        collector.calls.clear()
        check.calls.clear()
        supplied = {} if problem == 'missing_capture' else first.configuration
        if problem == 'stale_capture':
            plan = replace(plan, candidates={'A': plan.candidates['A'].derive(id='A', settings={'changed': True})})
    elif problem == 'cycle':
        plan = replace(plan, checks=(replace(plan.checks[0], depends_on=('check',)),))
    flow = a.AssessmentPipeline(plan=plan, collectors={collector.ref.name: collector},
        configuration_evaluators={} if problem == 'missing_evaluator' else {check.ref.name: check})
    with pytest.raises(a.ValidationError):
        flow.eval(configuration=supplied)
    assert not collector.calls and not check.calls


def test_sync_in_event_loop_rejected_before_work():
    plan, collector, check = configured()
    async def exercise():
        with pytest.raises(a.ConfigurationError, match='aeval'):
            pipeline(plan, collector, check).eval()
    asyncio.run(exercise())
    assert not collector.calls and not check.calls


def test_candidate_reference_projection_is_explicit_and_separate_from_task_answers(api, toy_backend):
    study, evaluators, reducers = build(api, toy_backend, 'plain')
    plan, collector, check = configured(study=study)
    spec = replace(plan.checks[0], reference_columns={'facts': ('expected',)})
    plan = replace(plan, checks=(spec,), reference_sets={'facts': a.VersionRef(name='facts', revision='v1')})
    refs = a.DataTable(key=('candidate_id', 'fact_id'),
        schema={'candidate_id': 'str', 'fact_id': 'str', 'expected': 'str', 'private': 'str'},
        rows=tuple({'candidate_id': cid, 'fact_id': 'one', 'expected': cid, 'private': 'DO_NOT_EXPOSE'}
                   for cid in plan.candidates))
    result = pipeline(plan, collector, check, references={'facts': refs},
        backends={toy_backend.ref.name: toy_backend}, evaluators=evaluators, reducers=reducers).eval()
    assert result.behavior_result is not None
    for request in check.calls:
        assert set(request.references) == {'facts'}
        assert set(request.references['facts'][0]) == {'candidate_id', 'fact_id', 'expected'}
        assert 'DO_NOT_EXPOSE' not in repr(request)
        assert 'EVALUATOR_ONLY' not in repr(request)
    for request in toy_backend.calls:
        assert 'DO_NOT_EXPOSE' not in repr(request) and 'EVALUATOR_ONLY' not in repr(request)


def test_ungated_configuration_check_and_behavior_overlap(api, toy_backend):
    scanner_entered, runtime_entered = Event(), Event()
    class BarrierCheck(Check):
        async def evaluate(self, request, *, recorder):
            scanner_entered.set()
            assert await anyio.to_thread.run_sync(lambda: runtime_entered.wait(5)), 'Runtime was serialized after scanner'
            return await super().evaluate(request, recorder=recorder)
    class Backend:
        ref = toy_backend.ref
        def capabilities(self):
            return toy_backend.capabilities()
        def run(self, request, *, recorder):
            runtime_entered.set()
            assert scanner_entered.wait(5), 'Scanner was serialized after runtime'
            return toy_backend.run(request, recorder=recorder)
    study, evaluators, reducers = build(api, toy_backend, 'plain')
    plan, collector, check = configured(study=study, check=BarrierCheck())
    result = pipeline(plan, collector, check, backends={toy_backend.ref.name: Backend()},
                      evaluators=evaluators, reducers=reducers).eval()
    assert scanner_entered.is_set() and runtime_entered.is_set()
    assert result.behavior_result is not None
    assert all(run.status == 'completed' for run in result.behavior_result.runs.runs)
    assert all(row.status == 'completed' for row in result.check_coverage)
    assert all(binding.status == 'unknown' for binding in result.snapshot_bindings)


def test_strict_combined_requires_binder_before_collection_or_execution(api, toy_backend):
    study, evaluators, reducers = build(api, toy_backend, 'plain')
    plan, collector, check = configured(study=study, strict=True)
    with pytest.raises(a.ConfigurationError, match='SnapshotBinder'):
        pipeline(plan, collector, check, backends={toy_backend.ref.name: toy_backend},
                 evaluators=evaluators, reducers=reducers).eval()
    assert not collector.calls and not check.calls and not toy_backend.calls


def test_gate_blocks_whole_native_group_but_independent_direct_candidate_runs(api, toy_backend):
    study, native = native_study(api, toy_backend, private=False)
    _, evaluators, reducers = build(api, toy_backend, 'plain')
    direct = replace(study.candidates['A'], id='C', backend=toy_backend.ref)
    study = replace(study, candidates={**study.candidates, 'C': direct})
    plan, collector, check = configured(study=study, check=Check(conclusion='fail'))
    plan = replace(plan, checks=(replace(plan.checks[0], candidate_ids=('A',)),),
        gates=(a.GateSpec(id='inspection', candidate_ids=('A',),
                         requirements=(a.ConclusionRequirement(check_id='check'),)),))
    result = pipeline(plan, collector, check, backends={toy_backend.ref.name: toy_backend},
        job_backends={native.ref.name: native}, evaluators=evaluators, reducers=reducers).eval()
    assert result.behavior_result is not None
    runs = result.behavior_result.runs
    assert not native.calls and len(toy_backend.calls) == 2
    assert len(runs.runs) == len(study.plan().assignments) == 6
    assert all(run.status == 'not_run' and not run.executions for cid in ('A', 'B') for run in runs.for_candidate(cid))
    assert all(run.status == 'completed' for run in runs.for_candidate('C'))
    assert not runs.native_jobs, 'Unstarted job is not a fabricated native invocation'
    assert next(b for b in result.branches if b.kind == 'behavior' and b.candidate_ids == ('A',)).gate_decisions[0].decision == 'fail'


def test_failed_scanner_blocks_dependency_but_preserves_behavior(api, toy_backend):
    study, evaluators, reducers = build(api, toy_backend, 'plain')
    plan, collector, check = configured(study=study, check=Check(fail=True))
    dependent = replace(plan.checks[0], id='dependent', depends_on=('check',))
    plan = replace(plan, checks=(*plan.checks, dependent))
    result = pipeline(plan, collector, check, backends={toy_backend.ref.name: toy_backend},
        evaluators=evaluators, reducers=reducers).eval()
    assert result.behavior_result is not None
    assert len(toy_backend.calls) == 4 and len(check.calls) == 2
    assert all(cell.status == 'blocked' for cell in result.check_coverage if cell.check_id == 'dependent')
    assert all(cell.status == 'error' for cell in result.check_coverage if cell.check_id == 'check')
    assert not result.performed_inventory_complete.value


def test_saved_combined_inputs_never_reexecute_or_recollect(api, toy_backend):
    study, evaluators, reducers = build(api, toy_backend, 'plain')
    plan, collector, check = configured(study=study)
    first = pipeline(plan, collector, check, backends={toy_backend.ref.name: toy_backend},
                     evaluators=evaluators, reducers=reducers).eval()
    strict = replace(plan, configuration={cid: replace(spec, snapshot_requirement='verified')
                                         for cid, spec in plan.configuration.items()})
    second = a.AssessmentPipeline(plan=strict, configuration_evaluators={check.ref.name: check},
        evaluators=evaluators, reducers=reducers).eval(runs=first.behavior_result.runs, configuration=first.configuration)
    assert len(toy_backend.calls) == 4 and len(collector.calls) == 2 and len(check.calls) == 4
    assert second.behavior_result.runs.id == first.behavior_result.runs.id
    assert all(binding.status == 'unknown' for binding in second.snapshot_bindings)
    assert second.validate().valid


def test_parent_cancellation_propagates_and_cleans_check_lifecycle():
    entered, cleaned = [], []
    class WaitingCheck(Check):
        async def evaluate(self, request, *, recorder):
            entered.append(request.id)
            try:
                await anyio.sleep_forever()
            finally:
                cleaned.append(request.id)
    plan, collector, check = configured(check=WaitingCheck())
    async def exercise():
        with anyio.move_on_after(0.05) as cancel:
            await pipeline(plan, collector, check).aeval()
            pytest.fail('Cancellation must not return a successful AssessmentResult')
        assert cancel.cancel_called
    asyncio.run(exercise())
    assert len(entered) == 1 and entered == cleaned


@pytest.mark.parametrize('on_unknown,expected_calls', [('block', 0), ('allow', 4)])
def test_unknown_launch_gate_keeps_unknown_conclusion(api, toy_backend, on_unknown, expected_calls):
    study, evaluators, reducers = build(api, toy_backend, 'plain')
    plan, collector, check = configured(study=study, check=Check(conclusion='unknown'))
    plan = replace(plan, gates=(a.GateSpec(id='gate', candidate_ids=tuple(plan.candidates),
        requirements=(a.ConclusionRequirement(check_id='check'),), on_unknown=on_unknown),))
    result = pipeline(plan, collector, check, backends={toy_backend.ref.name: toy_backend},
                      evaluators=evaluators, reducers=reducers).eval()
    assert len(toy_backend.calls) == expected_calls
    assert result.behavior_result is not None
    for branch in result.branches:
        if branch.kind == 'behavior':
            assert branch.gate_decisions[0].decision == 'unknown'
            assert branch.gate_decisions[0].allowed is (on_unknown == 'allow')


def test_configuration_invocations_respect_separate_concurrency_budget():
    class ConcurrentCheck(Check):
        active = peak = 0
        async def evaluate(self, request, *, recorder):
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                await anyio.sleep(0.01)
                return await super().evaluate(request, recorder=recorder)
            finally:
                self.active -= 1
    plan, collector, check = configured(check=ConcurrentCheck())
    candidates = {str(i): plan.candidates['A'].derive(id=str(i)) for i in range(6)}
    plan = replace(plan, candidates=candidates, max_concurrency=2,
        configuration={cid: plan.configuration['A'] for cid in candidates},
        checks=(replace(plan.checks[0], candidate_ids=tuple(candidates)),))
    result = pipeline(plan, collector, check).eval()
    assert len(result.assessments) == 6 and check.peak == 2 and check.active == 0


def test_public_cancellation_preserves_child_receipts_across_task_groups(tmp_path):
    path = tmp_path / 'partial.txt'
    path.write_text('acknowledged partial inspection', encoding='utf-8')
    artifact = a.ArtifactRef(uri=str(path), media_type='text/plain')
    class WaitingCheck(Check):
        async def evaluate(self, request, *, recorder):
            output = await super().evaluate(request, recorder=recorder)
            recorder.record_activity(replace(output.activity, status='partial'))
            recorder.record_artifact('partial', artifact)
            self.entered.set()
            await anyio.sleep_forever()
    plan, collector, check = configured(check=WaitingCheck())
    async def exercise():
        check.entered = asyncio.Event()
        task = asyncio.create_task(pipeline(plan, collector, check).aeval())
        await check.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        progress = caught.value.assessment_progress
        assert len(progress['configuration']) == 1
        assert len(progress['activities']) == 2
        invocation = next(row for row in progress['activities'] if row.phase == 'configuration_evaluation')
        assert invocation.status == 'cancelled' and invocation.ended_at is None
        assert invocation.inventory_complete.status == 'unknown'
        assert artifact in progress['artifacts'].values()
    asyncio.run(exercise())
