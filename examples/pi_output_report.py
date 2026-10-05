"""Verify frozen evidence and render the Pi output-fidelity report offline."""
import hashlib
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape


def main():
    root = Path(__file__).resolve().parent / 'reports' / 'pi-output-fidelity'
    source = root / 'study-data.json'
    data = json.loads(source.read_text(encoding='utf-8'))
    for name, digest in data['evidence_sha256'].items():
        path = root / 'evidence' / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, name
    cohorts = []
    for cohort in data['cohorts']:
        capture = json.loads((root / 'evidence' / cohort / 'results.json').read_text())
        cases = capture['cases']
        assert len(cases) == 6
        assert sum(c['exact_returned_text'] for c in cases) == 2
        assert all(c['success'] and c['exit_code'] == 0 for c in cases)
        assert all(c['actual'] == c['streamed'] for c in cases)
        inputs = capture['configuration']['cases']
        assert len({''.join(c['chunks_hex']) for c in inputs[1:]}) == 1
        if cohorts:
            assert [(c['case'], c['actual']) for c in cases] == [(c['case'], c['actual']) for c in cohorts[0]]
        cohorts.append(cases)
    messages = json.loads((root / 'evidence/latest-ansi-context/messages.json').read_text())['data']['messages']
    assert len(messages) == 6
    assert all(m['role'] == 'bashExecution' and m['output'] == c['actual'] for m, c in zip(messages, cohorts[-1]))
    for cohort, expected_passes in [('source-before', 2), ('source-after', 6)]:
        rows = json.loads((root / 'evidence' / cohort / 'results.json').read_text())['cases']
        retained = json.loads((root / 'evidence' / cohort / 'messages.json').read_text())['data']['messages']
        assert len(rows) == len(retained) == 6
        assert sum(r['exact_returned_text'] for r in rows) == expected_passes
        assert all(r['actual'] == r['streamed'] == m['output'] for r, m in zip(rows, retained))
    env = Environment(loader=FileSystemLoader(root), autoescape=select_autoescape(default=True),
                      trim_blocks=True, lstrip_blocks=True)
    rendered = env.get_template('report.html.j2').render(
        data=data, rows=cohorts[0], data_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    (root / 'index.html').write_text(rendered, encoding='utf-8', newline='\n')
    print(f'Verified {len(data["evidence_sha256"])} evidence files and both native before/after captures; rendered report.')


if __name__ == '__main__':
    main()
