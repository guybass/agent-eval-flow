"""Validation for assessment 0.1; v0.4 identities retain their own validators."""
from __future__ import annotations

from dataclasses import fields
from decimal import Decimal
from pathlib import PurePosixPath
import re

from .identity import canonical_bytes, semantic_fingerprint
from .records import ValidationIssue, ValidationReport


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _distinct(values, name, *, nonempty=False):
    _require(not nonempty or bool(values), f'{name} must not be empty')
    _require(all(isinstance(v, str) and v.strip() for v in values), f'{name} requires nonempty strings')
    _require(len(values) == len(set(values)), f'{name} must contain distinct values')


def _resolved(ref, name):
    _require(ref.revision is not None and bool(ref.revision.strip()), f'{name} requires a resolved revision')


def _set_fingerprint(record, name, expected):
    current = getattr(record, name)
    _require(not current or current == expected, f'{name} does not match the supplied definition')
    object.__setattr__(record, name, expected)


def validate_local(record):
    """Constructor-time invariants; relational checks use validate()."""
    from . import assessment as a
    name = type(record).__name__
    for f in fields(record):
        value = getattr(record, f.name)
        if isinstance(value, str) and (f.name.endswith('_id') or f.name in (
            'project_id', 'candidate_fingerprint', 'check_fingerprint', 'snapshot_fingerprint',
            'study_fingerprint', 'population_fingerprint', 'params_fingerprint', 'revision',
            'metric', 'locator', 'source_tool', 'scope', 'role',
        )):
            if name == 'AssessmentRequest' and f.name == 'check_fingerprint':
                continue
            _require(bool(value.strip()), f'{f.name} must not be empty')
    for field_name in ('candidate_ids', 'depends_on', 'required_roles', 'run_ids', 'job_ids'):
        if hasattr(record, field_name):
            _distinct(getattr(record, field_name), field_name,
                      nonempty=field_name == 'candidate_ids')
    if name in ('ConfigurationSpec', 'CandidateSnapshot'):
        _resolved(record.collector, 'collector')
    if name in ('CheckSpec', 'ConfigurationOrigin'):
        _resolved(record.evaluator, 'evaluator')
    if name == 'SnapshotEntry':
        path = record.path.replace('\\', '/')
        _require(bool(path) and not path.startswith('/') and not re.match(r'^[A-Za-z]:', path),
                 'Snapshot path must be logical and relative')
        _require('..' not in PurePosixPath(path).parts, 'Snapshot path must not traverse its logical mount')
        _require(bool(record.artifact.sha256) and bool(re.fullmatch('[0-9a-fA-F]{64}', record.artifact.sha256)),
                 'Snapshot entries require captured SHA-256 content hashes')
        object.__setattr__(record, 'path', str(PurePosixPath(path)))
    if name == 'CandidateSnapshot':
        ids = [entry.id for entry in record.entries]
        _require(len(ids) == len(set(ids)), 'Snapshot entry IDs must be unique')
        locations = [(e.source_tool, e.scope, e.path) for e in record.entries]
        _require(len(locations) == len(set(locations)), 'Snapshot logical locations must be unique within tool/scope')
        _require(not (record.omissions and record.inventory_complete.status == 'observed'
                      and record.inventory_complete.value is True),
                 'A snapshot with omissions cannot claim complete inventory')
        _set_fingerprint(record, 'fingerprint', a.snapshot_fingerprint(record))
    if name == 'ComponentSubject':
        _require(record.entry_id is not None or bool(record.mapping_issue),
                 'An unresolved component locator requires a mapping issue')
    if name == 'StudySubject':
        _require(bool(record.population), 'A study subject requires an explicit population')
    if name == 'AssessmentValue':
        if record.status == 'ok':
            _require(record.value is not None and record.basis is not None,
                     'An ok assessment value requires value and basis')
        else:
            _require(record.value is None and record.basis is None and bool(record.reason.strip()),
                     'Unavailable assessment values require null value/basis and a reason')
    if name == 'Assessment':
        _require(record.status == 'ok' or (record.conclusion == 'unknown' and bool(record.reason.strip())),
                 'Unavailable assessments require unknown conclusion and a reason')
        for attr in ('values', 'findings'):
            ids = [v.id for v in getattr(record, attr)]
            _require(len(ids) == len(set(ids)), f'Assessment {attr} IDs must be unique')
        refs = [(r.namespace, r.id) for r in record.activity_refs]
        _require(len(refs) == len(set(refs)), 'Assessment activity references must be unique')
        if record.origin.kind == 'configuration':
            _require(record.subject.kind == 'candidate', 'Configuration checks dispatch only candidate subjects')
        else:
            _require(record.subject.kind == 'run', 'Behavioral projections require a run subject')
    if name == 'CheckSpec':
        for table, columns in record.reference_columns.items():
            _require(bool(table.strip()), 'Reference table names must not be empty')
            _distinct(columns, 'reference columns')
        _distinct(tuple(record.output_types), 'output value IDs')
    if name == 'CheckCoverage':
        if record.status != 'completed':
            _require(bool(record.reason.strip()), 'Incomplete check coverage requires a reason')
        if record.requested_rules is not None:
            _distinct(record.requested_rules, 'requested rule IDs')
            _require(set(record.rule_statuses) <= set(record.requested_rules), 'Rule statuses contain unrequested rules')
        if record.status in ('blocked', 'not_applicable'):
            _require(record.request_id is None, 'Unstarted checks cannot claim an invocation request')
    if name == 'AssessmentActivity':
        _resolved(record.implementation, 'activity implementation')
        _require(bool(record.input_fingerprint.strip()), 'Activity input fingerprint must not be empty')
        _require(bool(record.subjects), 'An activity must identify its subjects')
        _require(len({canonical_bytes(s) for s in record.subjects}) == len(record.subjects),
                 'Activity subjects must be unique')
        _require(all(subject.kind == 'candidate' for subject in record.subjects),
                 'Initial assessment activities operate on candidate subjects only')
    if name == 'ConfigurationCapture':
        if record.status == 'completed':
            _require(record.snapshot is not None, 'Completed collection requires a snapshot')
        if record.snapshot is not None:
            _require((record.snapshot.candidate_id, record.snapshot.candidate_fingerprint)
                     == (record.candidate_id, record.candidate_fingerprint),
                     'Capture and snapshot candidate identities must agree')
        _require(len({v.id for v in record.activities}) == len(record.activities),
                 'Capture activity IDs must be unique')
    if name == 'SnapshotBinding' and record.status == 'verified':
        _require(bool(record.evidence) and bool(record.run_ids or record.job_ids),
                 'Verified parity requires actual evidence and applicable runtime identities')
    if name == 'NoFindingsRequirement' and record.allowed_tiers is not None:
        _distinct(record.allowed_tiers, 'allowed_tiers')
    if name == 'ValueRequirement' and record.op not in ('==', '!='):
        _require(type(record.expected_value) in (int, float, Decimal),
                 'Ordered value requirements require numeric values, never bool')
    if name == 'GateSpec':
        _require(bool(record.requirements), 'A launch gate requires explicit requirements')
    if name == 'GateDecision':
        _require(record.decision != 'fail' or not record.allowed, 'A failed gate cannot authorize launch')
    if name == 'AssessmentPlan':
        _require(record.max_concurrency > 0, 'max_concurrency must be positive')
    if name == 'ConfigurationCollectRequest':
        _set_fingerprint(record, 'input_fingerprint', a.collection_input_fingerprint(record))
    if name == 'AssessmentRequest':
        _set_fingerprint(record, 'check_fingerprint', record.check.fingerprint())
        _require(record.subject.candidate_id in record.check.candidate_ids, 'Check does not select this candidate')
        _require((record.subject.candidate_id, record.subject.candidate_fingerprint)
                 == (record.capture.candidate_id, record.capture.candidate_fingerprint),
                 'Request subject and capture identities disagree')
        actual_snapshot = record.capture.snapshot.fingerprint if record.capture.snapshot else None
        _require(record.subject.snapshot_fingerprint == actual_snapshot,
                 'Request subject must identify exactly the retained snapshot')
        _require(set(record.dependencies) == set(record.check.depends_on),
                 'Request must include the exact declared dependencies')
        for check_id, dependency in record.dependencies.items():
            _require(dependency.subject == record.subject and dependency.origin.kind == 'configuration'
                     and dependency.origin.check_id == check_id,
                     'Dependencies must resolve to same-candidate prerequisite checks')
        _require(set(record.references) == set(record.check.reference_columns),
                 'Request must contain exactly the selected reference tables')
        _require(set(record.reference_sets) == set(record.references),
                 'Selected references require exact versioned reference-set identities')
        _require(set(record.reference_keys) <= set(record.references),
                 'Reference keys cannot expose unselected tables')
        for keys in record.reference_keys.values():
            _distinct(keys, 'reference join keys')
            _require('candidate_id' in keys, 'Reference join keys must include candidate_id')
        for ref in record.reference_sets.values():
            _resolved(ref, 'reference set')
        for table, rows in record.references.items():
            allowed = set(record.check.reference_columns[table]) | {'candidate_id'} | set(record.reference_keys.get(table, ()))
            for row in rows:
                _require(row.get('candidate_id') == record.subject.candidate_id,
                         'Projected references must belong to the requested candidate')
                _require(set(row) <= allowed, 'Projected references contain unselected columns')
                _require(set(record.check.reference_columns[table]) | {'candidate_id'} <= set(row),
                         'Projected references are missing selected columns')
                for key in record.reference_keys.get(table, ('candidate_id',)):
                    _require(key in row and type(row[key]) in (str, int),
                             'Projected reference join keys require string or integer values')
        _set_fingerprint(record, 'input_fingerprint', a.check_input_fingerprint(record))
    if name == 'SnapshotCapabilities':
        _require(bool(record.backends), 'Snapshot capabilities must identify supported backends')
        for ref in record.backends:
            _resolved(ref, 'supported backend')
        _distinct(record.scopes, 'supported scopes')
    if name == 'SnapshotBindRequest':
        _require((record.run_request is None) != (record.native_job_request is None),
                 'Snapshot binding requires exactly one direct or native dispatch unit')
        _require(bool(record.snapshots) and set(record.snapshots) == set(record.requirements),
                 'Snapshot binding requirements must identify exactly the supplied snapshots')
        for candidate_id, snapshot in record.snapshots.items():
            _require(candidate_id == snapshot.candidate_id, 'Snapshot map key differs from its candidate')
        units = record.native_job_request.requests if record.native_job_request is not None else (record.run_request,)
        candidates = {unit.candidate.id: unit.candidate for unit in units}
        _require(set(record.snapshots) <= set(candidates), 'Snapshots must belong to the complete dispatch unit')
        for candidate_id, snapshot in record.snapshots.items():
            _require(snapshot.candidate_fingerprint == candidates[candidate_id].fingerprint(),
                     'Snapshot candidate differs from its runtime dispatch definition')


class _Issues:
    def __init__(self):
        self.items = []

    def error(self, path, message):
        self.items.append(ValidationIssue(path=path, message=message, severity='error'))

    def extend(self, report, prefix=''):
        self.items.extend(ValidationIssue(path=prefix + i.path, message=i.message, severity=i.severity)
                          for i in report.issues)

    def report(self):
        return ValidationReport(issues=tuple(self.items))


def validate_assessment_plan(plan):
    issues = _Issues()
    if not plan.candidates:
        issues.error('/candidates', 'An assessment plan requires candidates')
    for key, candidate in plan.candidates.items():
        if key != candidate.id:
            issues.error('/candidates/' + key, 'Candidate mapping key must equal its ID')
    if not plan.configuration and plan.behavior is None:
        issues.error('/', 'At least one assessment branch must be configured')
    for candidate_id in plan.configuration:
        if candidate_id not in plan.candidates:
            issues.error('/configuration/' + candidate_id, 'Configuration target is not a declared candidate')
    if plan.behavior is not None:
        issues.extend(plan.behavior.validate(), '/behavior')
        if plan.behavior.project_id != plan.project_id:
            issues.error('/behavior/project_id', 'Behavioral and assessment projects disagree')
        for key, candidate in plan.behavior.candidates.items():
            if key not in plan.candidates or canonical_bytes(candidate) != canonical_bytes(plan.candidates[key]):
                issues.error('/behavior/candidates/' + key, 'Behavioral candidate must equal its outer definition')
    checks = {}
    for index, check in enumerate(plan.checks):
        path = f'/checks/{index}'
        if check.id in checks:
            issues.error(path, 'Duplicate check ID')
        checks[check.id] = check
        for candidate_id in check.candidate_ids:
            if candidate_id not in plan.configuration:
                issues.error(path, f'Check candidate {candidate_id} has no configuration branch')
        for table in check.reference_columns:
            if table not in plan.reference_sets:
                issues.error(path, f'Undeclared candidate reference set {table}')
    for name, ref in plan.reference_sets.items():
        if not name.strip() or not ref.revision:
            issues.error('/reference_sets/' + name, 'Reference sets require names and resolved revisions')
    for check in plan.checks:
        for dep in check.depends_on:
            if dep not in checks:
                issues.error('/checks/' + check.id, f'Unknown prerequisite {dep}')
            elif not set(check.candidate_ids) <= set(checks[dep].candidate_ids):
                issues.error('/checks/' + check.id, 'Every dependency must cover the same candidate targets')
    active, visited = set(), set()
    def visit(check_id):
        if check_id in active:
            issues.error('/checks/' + check_id, 'Check dependency cycle')
            return
        if check_id in visited or check_id not in checks:
            return
        active.add(check_id)
        for dependency in checks[check_id].depends_on:
            visit(dependency)
        active.remove(check_id)
        visited.add(check_id)
    for check_id in checks:
        visit(check_id)
    gate_ids = set()
    for gate in plan.gates:
        path = '/gates/' + gate.id
        if gate.id in gate_ids:
            issues.error(path, 'Duplicate gate ID')
        gate_ids.add(gate.id)
        for candidate_id in gate.candidate_ids:
            if plan.behavior is None or candidate_id not in plan.behavior.candidates:
                issues.error(path, 'A launch gate must target behavioral candidates')
            for requirement in gate.requirements:
                check = checks.get(requirement.check_id)
                if check is None or candidate_id not in check.candidate_ids:
                    issues.error(path, 'Gate requirement does not resolve to a check for this candidate')
                elif requirement.kind == 'value':
                    kind = check.output_types.get(requirement.value_id)
                    numeric = kind in ('int', 'float', 'decimal') and type(requirement.expected_value) in (int, float, Decimal)
                    compatible = numeric or (kind is not None and requirement.op in ('==', '!=')
                                             and value_matches_type(requirement.expected_value, kind))
                    if not compatible:
                        issues.error(path, 'Gate expected value must be compatible with its declared check value type')
    return issues.report()


def validate_configuration_capture(capture, *, candidate, spec):
    issues = _Issues()
    if (capture.candidate_id, capture.candidate_fingerprint) != (candidate.id, candidate.fingerprint()):
        issues.error('/', 'Capture candidate identity/fingerprint does not match the requested candidate')
    snapshot = capture.snapshot
    if snapshot is not None:
        from .assessment import snapshot_fingerprint
        if snapshot.collector != spec.collector:
            issues.error('/snapshot/collector', 'Snapshot collector revision differs from the configured collector')
        if snapshot.params_fingerprint != semantic_fingerprint('configuration-params', spec.params):
            issues.error('/snapshot/params_fingerprint', 'Snapshot collection parameters are stale')
        if snapshot.fingerprint != snapshot_fingerprint(snapshot):
            issues.error('/snapshot/fingerprint', 'Snapshot fingerprint disagrees with captured content')
    for index, activity in enumerate(capture.activities):
        path = f'/activities/{index}'
        if activity.phase != 'collection' or activity.request_id != capture.request_id:
            issues.error(path, 'Capture activities must belong to the source collection request')
        if activity.implementation != spec.collector:
            issues.error(path, 'Collection activity implementation differs from the configured collector')
        expected_input = semantic_fingerprint('assessment:0.1:collection-input', {
            'candidate': candidate.fingerprint(), 'collector': spec.collector, 'params': spec.params})
        if activity.input_fingerprint != expected_input:
            issues.error(path, 'Collection activity input fingerprint differs from the requested source')
        if len(activity.subjects) != 1 or activity.subjects[0].kind != 'candidate' or (
            activity.subjects[0].candidate_id, activity.subjects[0].candidate_fingerprint
        ) != (candidate.id, candidate.fingerprint()):
            issues.error(path, 'Collection activity must identify exactly its candidate')
    return issues.report()


def _subject_candidate(subject):
    return subject.candidate if subject.kind == 'component' else subject


def _validate_subject(subject, result, issues, path):
    candidate_subject = _subject_candidate(subject)
    if subject.kind == 'study':
        study = result.plan.behavior
        if study is None or (subject.study_id, subject.study_fingerprint) != (study.id, study.fingerprint()):
            issues.error(path, 'Study subject does not resolve to the retained behavioral definition')
        return
    candidate = result.plan.candidates.get(candidate_subject.candidate_id)
    if candidate is None or candidate_subject.candidate_fingerprint != candidate.fingerprint():
        issues.error(path, 'Subject candidate identity/fingerprint does not resolve')
        return
    if subject.kind in ('candidate', 'component'):
        capture = result.configuration.get(candidate.id)
        snapshot = capture.snapshot if capture is not None else None
        if candidate_subject.snapshot_fingerprint is not None and (
            snapshot is None or candidate_subject.snapshot_fingerprint != snapshot.fingerprint
        ):
            issues.error(path, 'Subject snapshot fingerprint does not resolve to its candidate capture')
        if subject.kind == 'component' and subject.entry_id is not None:
            entries = {entry.id: entry for entry in snapshot.entries} if snapshot is not None else {}
            entry = entries.get(subject.entry_id)
            if entry is None:
                issues.error(path, 'Component entry does not exist in the retained snapshot')
            elif (subject.source_tool, subject.scope) != (entry.source_tool, entry.scope):
                issues.error(path, 'Component source-tool/scope differs from the resolved entry')
    elif subject.kind == 'run':
        behavior = result.behavior_result
        if behavior is None or behavior.runs.id != subject.run_set_id:
            issues.error(path, 'Run subject requires its retained behavioral run set')
            return
        run = next((run for run in behavior.runs.runs if run.id == subject.run_id), None)
        assignment = next((a for a in behavior.runs.plan.assignments if a.id == subject.assignment_id), None)
        if run is None or assignment is None or run.assignment_id != assignment.id or (
            assignment.candidate_id, assignment.candidate_fingerprint
        ) != (subject.candidate_id, subject.candidate_fingerprint):
            issues.error(path, 'Run subject run/assignment/candidate join does not resolve')


def validate_behavior_projection(assessment, *, behavior):
    """A projection is an exact typed source view, never an independent grade."""
    issues = _Issues()
    if assessment.origin.kind != 'behavior' or assessment.subject.kind != 'run':
        issues.error('/', 'Behavior projection requires a run subject and behavioral origin')
        return issues.report()
    origin = assessment.origin
    rows = [row for row in behavior.measurements
            if row.run_id == origin.run_id and row.metric == origin.metric
            and canonical_bytes(row.key) == canonical_bytes(origin.key)]
    if len(rows) != 1:
        issues.error('/origin', 'Behavioral projection locator must resolve to exactly one measurement')
        return issues.report()
    source = rows[0]
    if assessment.subject.run_id != source.run_id or assessment.subject.run_set_id != behavior.runs.id:
        issues.error('/subject', 'Projected subject differs from its measurement source')
    if assessment.status != source.status or assessment.reason != source.reason:
        issues.error('/', 'Projection must preserve measurement status and reason')
    if len(assessment.values) != 1:
        issues.error('/values', 'A measurement projection must contain exactly its source value')
    else:
        value = assessment.values[0]
        actual = (value.id, value.value, value.status, value.basis, value.reason, value.evidence)
        expected = (source.metric, source.value, source.status, source.basis, source.reason, source.evidence)
        if canonical_bytes(actual) != canonical_bytes(expected):
            issues.error('/values/0', 'Projection must preserve exact typed value, status, basis, reason and evidence')
    if canonical_bytes(assessment.evidence) != canonical_bytes(source.evidence) or assessment.findings:
        issues.error('/evidence', 'Projection must preserve source evidence without inventing findings')
    refs = tuple((ref.namespace, ref.id) for ref in assessment.activity_refs)
    if refs != tuple(('behavior', activity_id) for activity_id in source.activity_ids):
        issues.error('/activity_refs', 'Projection must preserve the source behavioral activity references')
    conclusion = 'unknown'
    if source.key is None:
        task = next((score for score in behavior.task_scores if score.run_id == source.run_id), None)
        if task is not None:
            if source.metric == 'task.accepted':
                conclusion = task.acceptance
            else:
                decisions = [gate.decision for gate in task.gates if gate.threshold.metric == source.metric]
                if decisions:
                    conclusion = 'fail' if 'fail' in decisions else ('unknown' if 'unknown' in decisions else 'pass')
    if assessment.conclusion != conclusion:
        issues.error('/conclusion', 'Projected conclusion must come from an explicit originating gate')
    return issues.report()


def validate_assessment_result(result):
    issues = _Issues()
    issues.extend(validate_assessment_plan(result.plan), '/plan')
    plan = result.plan
    for key, capture in result.configuration.items():
        path = '/configuration/' + key
        if key not in plan.configuration or key not in plan.candidates:
            issues.error(path, 'Undeclared configuration capture')
        else:
            issues.extend(validate_configuration_capture(capture, candidate=plan.candidates[key],
                                                        spec=plan.configuration[key]), path)
    branches_by_cell = {}
    for index, branch in enumerate(result.branches):
        path = f'/branches/{index}'
        enabled = set(plan.configuration) if branch.kind == 'configuration' else (
            set(plan.behavior.candidates) if plan.behavior is not None else set())
        for candidate_id in branch.candidate_ids:
            cell = branch.kind, candidate_id
            if candidate_id not in enabled or cell in branches_by_cell:
                issues.error(path, 'Branch outcomes must cover each enabled candidate exactly once')
            branches_by_cell[cell] = branch
        for decision in branch.gate_decisions:
            gate = next((g for g in plan.gates if g.id == decision.gate_id), None)
            if branch.kind != 'behavior' or gate is None or decision.candidate_id not in gate.candidate_ids:
                issues.error(path + '/gate_decisions', 'Gate decision does not resolve to a behavioral launch gate')
            elif decision.candidate_id not in branch.candidate_ids:
                issues.error(path + '/gate_decisions', 'Gate decision candidate is outside its branch')
            elif decision.allowed != (decision.decision == 'pass' or (
                decision.decision == 'unknown' and gate.on_unknown == 'allow')):
                issues.error(path + '/gate_decisions', 'Gate operational authorization differs from recorded policy')
    expected_branches = {('configuration', c) for c in plan.configuration}
    if plan.behavior is not None:
        expected_branches |= {('behavior', c) for c in plan.behavior.candidates}
    if set(branches_by_cell) != expected_branches:
        issues.error('/branches', 'Branch outcomes must account for every enabled candidate branch')
    for candidate_id in plan.configuration:
        if candidate_id not in result.configuration:
            branch = branches_by_cell.get(('configuration', candidate_id))
            if branch is None or branch.status not in ('error', 'cancelled', 'not_run', 'unknown'):
                issues.error('/configuration/' + candidate_id, 'Absent capture requires an explicit unstarted or failed branch')
    behavior = result.behavior_result
    if behavior is not None:
        if plan.behavior is None:
            issues.error('/behavior_result', 'Behavioral result requires an enabled behavioral plan')
        else:
            issues.extend(behavior.validate(), '/behavior_result')
            from ..pipeline.preflight import check_capture
            issues.extend(check_capture(plan.behavior, behavior.runs), '/behavior_result/runs')
            if behavior.suite_fingerprint != plan.behavior.suite.fingerprint():
                issues.error('/behavior_result/suite', 'Behavioral suite differs from the outer plan')
    elif plan.behavior is not None:
        if any(branches_by_cell.get(('behavior', c)) is None or
               branches_by_cell[('behavior', c)].status in ('completed', 'partial')
               for c in plan.behavior.candidates):
            issues.error('/behavior_result', 'Absent behavioral payload requires explicit failure or unstarted outcomes')
    activities = {}
    for index, activity in enumerate(result.activities):
        path = f'/activities/{index}'
        if activity.id in activities:
            issues.error(path, 'Assessment activities must have unique canonical IDs')
        activities[activity.id] = activity
        for subject_index, subject in enumerate(activity.subjects):
            _validate_subject(subject, result, issues, path + f'/subjects/{subject_index}')
    for key, capture in result.configuration.items():
        for activity in capture.activities:
            canonical = activities.get(activity.id)
            if canonical is None or canonical_bytes(canonical) != canonical_bytes(activity):
                issues.error('/configuration/' + key + '/activities',
                             'Retained collection activities must equal their canonical outer copies')
    behavior_activities = {activity.id: activity for activity in behavior.activities} if behavior else {}
    performed = set()
    for index, ref in enumerate(result.performed_activity_refs):
        key = ref.namespace, ref.id
        registry = activities if ref.namespace == 'assessment' else behavior_activities
        if key in performed or ref.id not in registry:
            issues.error(f'/performed_activity_refs/{index}', 'Performed references must be distinct existing namespaced activities')
        performed.add(key)
    checks = {check.id: check for check in plan.checks}
    expected_checks = {(candidate_id, check.id) for check in plan.checks for candidate_id in check.candidate_ids}
    coverage = {}
    for index, cell in enumerate(result.check_coverage):
        key = cell.candidate_id, cell.check_id
        if key not in expected_checks or key in coverage:
            issues.error(f'/check_coverage/{index}', 'Duplicate or unrequested check coverage')
        coverage[key] = cell
    if set(coverage) != expected_checks:
        issues.error('/check_coverage', 'Exactly one terminal coverage cell is required for every requested check')
    assessment_ids, configuration_cells, behavioral_locators = set(), {}, set()
    request_ids = set()
    for index, assessment in enumerate(result.assessments):
        path = f'/assessments/{index}'
        if assessment.id in assessment_ids:
            issues.error(path, 'Assessment IDs must be unique')
        assessment_ids.add(assessment.id)
        _validate_subject(assessment.subject, result, issues, path + '/subject')
        for fi, finding in enumerate(assessment.findings):
            _validate_subject(finding.subject, result, issues, path + f'/findings/{fi}/subject')
            if assessment.subject.kind == 'candidate' and (
                finding.subject.kind not in ('candidate', 'component') or
                _subject_candidate(finding.subject).candidate_id != assessment.subject.candidate_id or
                _subject_candidate(finding.subject).snapshot_fingerprint != assessment.subject.snapshot_fingerprint
            ):
                issues.error(path + f'/findings/{fi}', 'Configuration finding must belong to the assessed candidate snapshot')
        for ri, ref in enumerate(assessment.activity_refs):
            registry = activities if ref.namespace == 'assessment' else behavior_activities
            activity = registry.get(ref.id)
            if activity is None:
                issues.error(path + f'/activity_refs/{ri}', 'Activity reference does not resolve in its namespace')
            elif ref.namespace == 'behavior':
                if assessment.subject.kind != 'run' or assessment.subject.run_id not in activity.run_ids:
                    issues.error(path + f'/activity_refs/{ri}', 'Behavior activity does not cover this run')
            elif not any(s.kind == 'candidate' and assessment.subject.kind == 'candidate'
                         and s.candidate_id == assessment.subject.candidate_id
                         and s.candidate_fingerprint == assessment.subject.candidate_fingerprint
                         for s in activity.subjects):
                issues.error(path + f'/activity_refs/{ri}', 'Assessment activity does not cover this candidate')
        if assessment.origin.kind == 'behavior':
            locator = canonical_bytes(assessment.origin)
            if locator in behavioral_locators:
                issues.error(path + '/origin', 'Duplicate behavioral source projection')
            behavioral_locators.add(locator)
            if behavior is None:
                issues.error(path + '/origin', 'Behavioral projection has no retained source result')
            else:
                issues.extend(validate_behavior_projection(assessment, behavior=behavior), path)
            continue
        origin = assessment.origin
        check = checks.get(origin.check_id)
        key = assessment.subject.candidate_id, origin.check_id
        if key in configuration_cells:
            issues.error(path, 'Only one configuration assessment is allowed per requested check/candidate')
        configuration_cells[key] = assessment
        if origin.request_id in request_ids:
            issues.error(path + '/origin/request_id', 'Configuration requests must identify distinct invocations')
        request_ids.add(origin.request_id)
        if check is None or key not in expected_checks or origin.check_fingerprint != check.fingerprint() or origin.evaluator != check.evaluator:
            issues.error(path + '/origin', 'Configuration assessment origin differs from its requested check definition')
        else:
            for vi, value in enumerate(assessment.values):
                kind = check.output_types.get(value.id)
                if kind is None or (value.status == 'ok' and not value_matches_type(value.value, kind)):
                    issues.error(path + f'/values/{vi}', 'Assessment value does not match a declared strict output type')
            if assessment.status == 'ok' and {v.id for v in assessment.values} != set(check.output_types):
                issues.error(path + '/values', 'Successful check must explicitly account for each declared value')
        cell = coverage.get(key)
        if cell is None or cell.assessment_id != assessment.id or cell.request_id != origin.request_id:
            issues.error(path, 'Configuration assessment must resolve to its exact terminal coverage cell')
        own = [activities[ref.id] for ref in assessment.activity_refs
               if ref.namespace == 'assessment' and ref.id in activities
               and activities[ref.id].request_id == origin.request_id]
        if len(own) != 1 or own[0].phase != 'configuration_evaluation' or own[0].implementation != origin.evaluator:
            issues.error(path + '/activity_refs', 'Configuration assessment must link its one matching evaluator invocation')
    for key, cell in coverage.items():
        assessment = configuration_cells.get(key)
        if cell.status in ('completed', 'partial', 'error') and assessment is None:
            issues.error('/check_coverage', 'Completed or failed checks require an explicit assessment')
        if cell.assessment_id is not None and (assessment is None or cell.assessment_id != assessment.id):
            issues.error('/check_coverage', 'Coverage assessment locator does not resolve')
        if cell.status == 'completed' and assessment is not None and assessment.status != 'ok':
            issues.error('/check_coverage', 'Completed check coverage requires a valid assessment')
    if behavior is not None:
        expected_locators = {canonical_bytes({'kind': 'behavior', 'run_id': row.run_id, 'metric': row.metric, 'key': row.key})
                             for row in behavior.measurements}
        if behavioral_locators != expected_locators:
            issues.error('/assessments', 'Every retained behavioral measurement must have exactly one projection')
    # A persisted launch decision must retain the predicates and evidence that
    # justify it. Merely matching the gate ID and its unknown policy is not proof.
    if not issues.items:
        from ..results.assessment_selection import evaluate_requirement_inputs, conjunction
        for index, branch in enumerate(result.branches):
            for di, decision in enumerate(branch.gate_decisions):
                gate = next(gate for gate in plan.gates if gate.id == decision.gate_id)
                expected = tuple(evaluate_requirement_inputs(plan, result.assessments, result.check_coverage,
                    decision.candidate_id, requirement) for requirement in gate.requirements)
                if canonical_bytes(decision.requirements) != canonical_bytes(expected):
                    issues.error(f'/branches/{index}/gate_decisions/{di}/requirements',
                                 'Gate requirement receipts differ from their retained source assessments')
                if decision.decision != conjunction(item.decision for item in expected):
                    issues.error(f'/branches/{index}/gate_decisions/{di}/decision',
                                 'Gate conclusion differs from its explicit requirements')
    for index, binding in enumerate(result.snapshot_bindings):
        path = f'/snapshot_bindings/{index}'
        candidate = plan.candidates.get(binding.candidate_id)
        capture = result.configuration.get(binding.candidate_id)
        if candidate is None or binding.candidate_fingerprint != candidate.fingerprint() or capture is None or capture.snapshot is None:
            issues.error(path, 'Snapshot binding requires its candidate and captured snapshot')
        elif binding.snapshot_fingerprint != capture.snapshot.fingerprint:
            issues.error(path, 'Snapshot binding refers to a different captured snapshot')
        if binding.status == 'verified' and (not binding.evidence or not (binding.run_ids or binding.job_ids)):
            issues.error(path, 'Verified binding requires actual evidence and applicable runtime identities')
        if behavior is not None:
            assignments = {a.id: a for a in behavior.runs.plan.assignments}
            runs = {r.id: r for r in behavior.runs.runs}
            jobs = {j.id: j for j in behavior.runs.native_jobs}
            for run_id in binding.run_ids:
                run = runs.get(run_id)
                if run is None or assignments[run.assignment_id].candidate_id != binding.candidate_id:
                    issues.error(path + '/run_ids', 'Binding run must resolve to its candidate')
            for job_id in binding.job_ids:
                job = jobs.get(job_id)
                if job is None or not any(assignments[a].candidate_id == binding.candidate_id for a in job.assignment_ids):
                    issues.error(path + '/job_ids', 'Binding job must resolve to its candidate')
        elif binding.run_ids or binding.job_ids:
            issues.error(path, 'Binding runtime identities cannot resolve without retained behavior')
    return issues.report()


def value_matches_type(value, output_type):
    return type(value) is {'bool': bool, 'int': int, 'float': float, 'decimal': Decimal, 'text': str}[output_type]


def validate_configuration_references(plan, references):
    """Validate candidate-keyed tables without admitting task-private references."""
    issues = _Issues()
    needed = {name for check in plan.checks for name in check.reference_columns}
    for name in needed:
        table = references.get(name)
        if table is None:
            issues.error('/references/' + name, 'Missing required candidate reference table')
            continue
        issues.extend(table.validate(), '/references/' + name)
        if 'candidate_id' not in table.key or table.schema.get('candidate_id') != 'str':
            issues.error('/references/' + name, 'Candidate references require a string candidate_id join key')
        for check in plan.checks:
            if name not in check.reference_columns:
                continue
            if not set(check.reference_columns[name]) <= set(table.schema):
                issues.error('/references/' + name, 'Selected reference column is absent')
            targets = {row.get('candidate_id') for row in table.rows}
            if not set(check.candidate_ids) <= targets:
                issues.error('/references/' + name, 'Reference table does not cover every requested candidate')
    return issues.report()


def validate(record):
    name = type(record).__name__
    if name == 'AssessmentPlan':
        return validate_assessment_plan(record)
    if name == 'AssessmentResult':
        return validate_assessment_result(record)
    return ValidationReport(issues=())
