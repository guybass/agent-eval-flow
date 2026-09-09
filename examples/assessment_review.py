"""Offline configuration assessment, persistence and policy review.

Run: python examples/assessment_review.py --output demo-output/assessment
The example checks one explicit document requirement; it does not judge agent quality.
"""
from argparse import ArgumentParser
import hashlib
from pathlib import Path

import agent_eval_flow as a
from agent_eval_flow.adapters import FileConfigurationCollector
from agent_eval_flow.objects.values import observed, zero_resources
from agent_eval_flow.storage.artifacts import ArtifactCache


class UsageSectionCheck:
    ref = a.VersionRef(name='example.usage-section', revision='1')

    async def evaluate(self, request, *, recorder):
        entry = request.capture.snapshot.entries[0]
        # FileConfigurationCollector returns local immutable cache objects.
        content = Path(entry.artifact.uri).read_bytes()
        if hashlib.sha256(content).hexdigest() != entry.artifact.sha256:
            raise a.CaptureValidationError('Retained skill bytes differ from the snapshot')
        text = content.decode('utf-8')
        present = '## Usage' in text
        evidence = a.EvidenceRef(artifact=entry.artifact, description='Captured skill document')
        activity = a.AssessmentActivity(id=request.activity_id, request_id=request.id,
            implementation=self.ref, input_fingerprint=request.input_fingerprint,
            subjects=(request.subject,), phase='configuration_evaluation', status='completed',
            resources=zero_resources(), inventory_complete=observed(True, 'One local deterministic check'))
        assessment = a.Assessment(id=request.id + '/assessment', subject=request.subject,
            origin=a.ConfigurationOrigin(request_id=request.id, check_id=request.check.id,
                check_fingerprint=request.check_fingerprint, evaluator=self.ref),
            status='ok', conclusion='pass' if present else 'fail',
            reason='The captured document ' + ('contains' if present else 'does not contain') + ' a Usage heading',
            values=(a.AssessmentValue(id='usage_section', value=present, status='ok', basis='observed',
                                     reason='Exact heading check', evidence=(evidence,)),),
            findings=() if present else (a.Finding(id='missing-usage', subject=request.subject,
                rule=self.ref, message='Document has no Usage heading', severity='warning',
                basis='observed', evidence=(evidence,)),), evidence=(evidence,),
            activity_refs=(a.ActivityRef(namespace='assessment', id=activity.id),))
        return a.AssessmentOutput(assessment=assessment, activity=activity,
            coverage=a.CheckCoverage(candidate_id=request.subject.candidate_id, check_id=request.check.id,
                request_id=request.id, assessment_id=assessment.id, status='completed',
                inventory_complete=observed(True, 'The one declared heading rule inspected the selected file'),
                requested_rules=('usage-heading',), rule_statuses={'usage-heading': 'completed'},
                omitted_suppressed_findings=False))


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('demo-output/assessment'))
    output = parser.parse_args().output.resolve()
    sources = output / 'sources'
    sources.mkdir(parents=True, exist_ok=True)
    (sources / 'baseline.md').write_text('# Example skill\n\nFormats a response.\n', encoding='utf-8')
    (sources / 'documented.md').write_text('# Example skill\n\n## Usage\n\nAsk for a formatted response.\n', encoding='utf-8')
    collector = FileConfigurationCollector(roots={'example': sources}, artifacts=ArtifactCache(output / 'artifacts'))
    evaluator = UsageSectionCheck()
    candidates = {cid: a.Candidate(id=cid, backend=a.VersionRef(name='example.agent', revision='1'),
        components={}, settings={'skill': cid + '.md'}) for cid in ('baseline', 'documented')}
    plan = a.AssessmentPlan(id='document-review', project_id='offline-example', candidates=candidates,
        configuration={cid: a.ConfigurationSpec(collector=collector.ref,
            params={'files': ({'root': 'example', 'path': cid + '.md', 'logical_path': 'SKILL.md',
                              'source_tool': 'example.agent', 'scope': 'project', 'role': 'skill'},)})
                       for cid in candidates},
        checks=(a.CheckSpec(id='usage', evaluator=evaluator.ref, candidate_ids=tuple(candidates),
                            required_roles=('skill',), output_types={'usage_section': 'bool'}),),
        max_concurrency=2)
    result = a.AssessmentPipeline(plan=plan, collectors={collector.ref.name: collector},
        configuration_evaluators={evaluator.ref.name: evaluator}).eval()
    result.save(output / 'result')
    saved = a.AssessmentResult.load(output / 'result')
    assert saved == result
    policy = a.AssessmentPolicy(id='document-requirement', revision='1',
        configuration_requirements=(a.ConclusionRequirement(check_id='usage'),))
    saved.report(output / 'report.html', policy=policy)
    decision = saved.select(policy)
    for row in decision.rows:
        print(f'{row.candidate_id}: {row.eligibility}')
    print('Report:', output / 'report.html')
    print('Saved result:', output / 'result')
    print('Configuration eligibility only; no behavioral ranking or agent-quality claim.')


if __name__ == '__main__':
    main()
