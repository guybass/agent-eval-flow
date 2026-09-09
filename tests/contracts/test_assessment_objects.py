"""Identity, scope, uncertainty and ownership contracts for assessment records."""
from dataclasses import replace
from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError as SchemaError

from agent_eval_flow import objects as o
from agent_eval_flow.objects import assessment as a
from agent_eval_flow.objects.assessment_validation import (
    validate_behavior_projection, validate_configuration_capture,
    validate_configuration_references,
)
from agent_eval_flow.objects.identity import semantic_fingerprint
from agent_eval_flow.objects.values import observed, zero_resources


def make_capture(candidate=None, *, entries=()):
    candidate = candidate or o.Candidate(id='a', backend=o.VersionRef(name='native', revision='1'), components={})
    spec = a.ConfigurationSpec(collector=o.VersionRef(name='files', revision='1'), params={'root': 'project'})
    request = a.ConfigurationCollectRequest(id='collect-request', activity_id='collection', candidate=candidate, spec=spec)
    snapshot = a.CandidateSnapshot(candidate_id=candidate.id, candidate_fingerprint=candidate.fingerprint(),
        collector=spec.collector, params_fingerprint=semantic_fingerprint('configuration-params', spec.params),
        entries=entries, inventory_complete=observed(True))
    subject = a.CandidateSubject(candidate_id=candidate.id, candidate_fingerprint=candidate.fingerprint(),
                                snapshot_fingerprint=snapshot.fingerprint)
    activity = a.AssessmentActivity(id=request.activity_id, request_id=request.id,
        implementation=spec.collector, input_fingerprint=request.input_fingerprint, subjects=(subject,),
        phase='collection', status='completed', resources=zero_resources(), inventory_complete=observed(True))
    capture = a.ConfigurationCapture(id='capture', request_id=request.id, candidate_id=candidate.id,
        candidate_fingerprint=candidate.fingerprint(), snapshot=snapshot, status='completed', activities=(activity,))
    return candidate, spec, capture, subject


def make_result():
    candidate, spec, capture, subject = make_capture()
    check = a.CheckSpec(id='lint', evaluator=o.VersionRef(name='lint', revision='1'),
                       candidate_ids=(candidate.id,), output_types={'valid': 'bool'})
    plan = a.AssessmentPlan(id='plan', project_id='project', candidates={candidate.id: candidate},
        configuration={candidate.id: spec}, checks=(check,))
    request = a.AssessmentRequest(id='inspect-request', activity_id='inspection', check=check,
                                  subject=subject, capture=capture)
    activity = a.AssessmentActivity(id=request.activity_id, request_id=request.id,
        implementation=check.evaluator, input_fingerprint=request.input_fingerprint, subjects=(subject,),
        phase='configuration_evaluation', status='completed', resources=zero_resources(), inventory_complete=observed(True))
    assessment = a.Assessment(id='assessment', subject=subject,
        origin=a.ConfigurationOrigin(request_id=request.id, check_id=check.id,
            check_fingerprint=check.fingerprint(), evaluator=check.evaluator),
        status='ok', conclusion='pass', values=(a.AssessmentValue(id='valid', value=True,
            status='ok', basis='observed', reason='All declared checks completed'),),
        activity_refs=(a.ActivityRef(namespace='assessment', id=activity.id),))
    coverage = a.CheckCoverage(candidate_id=candidate.id, check_id=check.id, status='completed',
        request_id=request.id, assessment_id=assessment.id, inventory_complete=observed(True),
        omitted_suppressed_findings=False)
    return a.AssessmentResult(id='result', plan=plan, configuration={candidate.id: capture},
        assessments=(assessment,), check_coverage=(coverage,), activities=capture.activities + (activity,),
        branches=(a.BranchOutcome(kind='configuration', candidate_ids=(candidate.id,), status='completed', reason='Captured and inspected'),),
        performed_activity_refs=tuple(a.ActivityRef(namespace='assessment', id=act.id) for act in capture.activities + (activity,)))


def test_new_roots_preserve_configuration_scope_without_a_run():
    result = make_result()
    assert result.validate().valid
    assert result.behavior_result is None
    assert result.assessments[0].subject.kind == 'candidate'
    assert not hasattr(result.assessments[0], 'run_id')


def test_discriminated_subjects_reject_partial_and_wrong_variants():
    adapter = TypeAdapter(a.SubjectRef)
    with pytest.raises(SchemaError):
        adapter.validate_json('{"kind":"run","candidate_id":"a","candidate_fingerprint":"fp"}')
    with pytest.raises(SchemaError):
        adapter.validate_json('{"kind":"candidate","candidate_id":"a","candidate_fingerprint":"fp","run_id":"made-up"}')
    _, _, _, subject = make_capture()
    with pytest.raises(SchemaError, match='mapping issue'):
        a.ComponentSubject(candidate=subject, locator='skills/missing', source_tool='native', scope='project')


def test_unavailable_values_and_failed_assessments_cannot_be_successes():
    with pytest.raises(SchemaError):
        a.AssessmentValue(id='valid', value=False, status='missing', basis=None, reason='No scan')
    with pytest.raises(SchemaError):
        a.AssessmentValue(id='valid', value=None, status='ok', basis='observed', reason='')
    assessment = make_result().assessments[0]
    with pytest.raises(SchemaError, match='unknown conclusion'):
        replace(assessment, status='error', reason='Scanner failed')
    assert replace(assessment, status='error', conclusion='unknown', reason='Scanner failed').conclusion == 'unknown'


def test_definitions_are_detached_and_deeply_immutable():
    params = {'nested': {'selected': ['one']}}
    spec = a.ConfigurationSpec(collector=o.VersionRef(name='files', revision='1'), params=params)
    params['nested']['selected'].append('two')
    assert spec.params['nested']['selected'] == ('one',)
    with pytest.raises(TypeError):
        spec.params['nested']['changed'] = True


def test_snapshot_identity_survives_relocation_but_tracks_bytes_and_scope():
    entry = a.SnapshotEntry(id='skill', path='project/skill.md', source_tool='native', scope='project',
        role='skill', artifact=o.ArtifactRef(uri='file:///first/skill.md', media_type='text/markdown', sha256='a' * 64))
    _, _, capture, _ = make_capture(entries=(entry,))
    snapshot = capture.snapshot
    relocated = replace(entry, artifact=replace(entry.artifact, uri='file:///elsewhere/skill.md'))
    assert replace(snapshot, entries=(relocated,), fingerprint='').fingerprint == snapshot.fingerprint
    changed = replace(entry, artifact=replace(entry.artifact, sha256='b' * 64))
    assert replace(snapshot, entries=(changed,), fingerprint='').fingerprint != snapshot.fingerprint
    assert replace(snapshot, entries=(replace(entry, scope='user'),), fingerprint='').fingerprint != snapshot.fingerprint
    with pytest.raises(SchemaError, match='fingerprint'):
        replace(snapshot, entries=(changed,))
    with pytest.raises(SchemaError, match='omissions'):
        replace(snapshot, omissions=('Unreadable source',), fingerprint='')


def test_same_component_name_in_different_scopes_does_not_collapse():
    artifact = o.ArtifactRef(uri='memory:skill', media_type='text/plain', sha256='a' * 64)
    entries = tuple(a.SnapshotEntry(id=scope, path='SKILL.md', source_tool='native', scope=scope,
        role='skill', artifact=artifact) for scope in ('user', 'project'))
    assert len(make_capture(entries=entries)[2].snapshot.entries) == 2
    with pytest.raises(SchemaError, match='logical locations'):
        make_capture(entries=(entries[0], replace(entries[0], id='duplicate')))


def test_plan_rejects_cycles_unconfigured_targets_and_incompatible_behavior(api, toy_backend):
    result = make_result()
    first = result.plan.checks[0]
    second = replace(first, id='second', depends_on=(first.id,))
    cyclic = replace(result.plan, checks=(replace(first, depends_on=(second.id,)), second))
    assert any('cycle' in i.message for i in cyclic.validate().issues)
    wrong = replace(result.plan, checks=(replace(first, candidate_ids=('foreign',)),))
    assert not wrong.validate().valid
    from tests.e2e.test_toy_pipeline import build
    study, _, _ = build(api, toy_backend, 'plain')
    assert not replace(result.plan, behavior=study).validate().valid
    assert not a.AssessmentPlan(id='empty', project_id='project', candidates=result.plan.candidates).validate().valid


def test_capture_rejects_changed_parameters_and_foreign_collection_receipt():
    candidate, spec, capture, _ = make_capture()
    assert validate_configuration_capture(capture, candidate=candidate, spec=spec).valid
    assert validate_configuration_capture(capture, candidate=candidate,
        spec=replace(spec, snapshot_requirement='record_only')).valid
    assert not validate_configuration_capture(capture, candidate=candidate,
        spec=replace(spec, params={'root': 'elsewhere'})).valid
    corrupted = replace(capture, activities=(replace(capture.activities[0], request_id='another-request'),))
    assert not validate_configuration_capture(corrupted, candidate=candidate, spec=spec).valid


def test_check_input_fingerprint_preserves_typed_refs_and_reference_revision():
    _, _, capture, subject = make_capture()
    check = a.CheckSpec(id='oracle', evaluator=o.VersionRef(name='oracle', revision='1'),
        candidate_ids=(subject.candidate_id,), reference_columns={'answers': ('expected',)})
    request = a.AssessmentRequest(id='r1', activity_id='a1', check=check, subject=subject,
        capture=capture, references={'answers': ({'candidate_id': subject.candidate_id, 'expected': True},)},
        reference_sets={'answers': o.VersionRef(name='answers', revision='1')})
    assert replace(request, id='r2', activity_id='a2').input_fingerprint == request.input_fingerprint
    integer = replace(request, references={'answers': ({'candidate_id': subject.candidate_id, 'expected': 1},)}, input_fingerprint='')
    changed_revision = replace(request, reference_sets={'answers': o.VersionRef(name='answers', revision='2')}, input_fingerprint='')
    assert len({request.input_fingerprint, integer.input_fingerprint, changed_revision.input_fingerprint}) == 3
    with pytest.raises(SchemaError, match='unselected'):
        replace(request, references={'answers': ({'candidate_id': subject.candidate_id, 'expected': True, 'private': 'secret'},)}, input_fingerprint='')
    with pytest.raises(SchemaError, match='requested candidate'):
        replace(request, references={'answers': ({'candidate_id': 'other', 'expected': True},)}, input_fingerprint='')


def test_candidate_references_allow_explicit_compound_keys():
    result = make_result()
    check = replace(result.plan.checks[0], reference_columns={'facts': ('expected',)})
    plan = replace(result.plan, checks=(check,), reference_sets={'facts': o.VersionRef(name='facts', revision='1')})
    table = o.DataTable(key=('candidate_id', 'fact'), schema={'candidate_id': 'str', 'fact': 'int', 'expected': 'bool'},
        rows=({'candidate_id': 'a', 'fact': 1, 'expected': True}, {'candidate_id': 'a', 'fact': 2, 'expected': False}))
    assert validate_configuration_references(plan, {'facts': table}).valid
    subject = result.assessments[0].subject
    request = a.AssessmentRequest(id='r', activity_id='v', check=check, subject=subject,
        capture=result.configuration['a'], references={'facts': table.rows}, reference_sets=plan.reference_sets,
        reference_keys={'facts': table.key})
    assert request.references['facts'][1]['fact'] == 2


def test_result_rejects_duplicate_population_incorrect_types_and_foreign_activities():
    result = make_result()
    assert not replace(result, check_coverage=result.check_coverage * 2).validate().valid
    assert not replace(result, check_coverage=()).validate().valid
    assert not replace(result, activities=result.activities * 2).validate().valid
    value = replace(result.assessments[0].values[0], value=1)
    wrong_type = replace(result.assessments[0], values=(value,))
    assert any('strict output type' in i.message for i in replace(result, assessments=(wrong_type,)).validate().issues)
    wrong_ref = replace(result.assessments[0], activity_refs=(a.ActivityRef(namespace='behavior', id='inspection'),))
    assert not replace(result, assessments=(wrong_ref,)).validate().valid
    assert not replace(result, performed_activity_refs=(a.ActivityRef(namespace='assessment', id='unstarted'),)).validate().valid


def test_behavior_projection_rejects_type_change_and_invented_conclusion(api, toy_backend):
    from tests.contracts.test_results_storage import evaluate_table
    from agent_eval_flow.evaluation.assessment_projection import project_behavioral_assessments
    _, behavior, _ = evaluate_table(api, toy_backend,
        {'A': {'quality': 1.0}, 'B': {'quality': 0.5}}, {'quality': 'float'})
    assessment = next(item for item in project_behavioral_assessments(behavior) if item.origin.metric == 'quality')
    assert validate_behavior_projection(assessment, behavior=behavior).valid
    wrong_type = replace(assessment, values=(replace(assessment.values[0], value=Decimal('1.0')),))
    assert not validate_behavior_projection(wrong_type, behavior=behavior).valid
    assert not validate_behavior_projection(replace(assessment, conclusion='pass'), behavior=behavior).valid
