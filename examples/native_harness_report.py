"""Render the public harness study from its curated data; no agent or model calls.

Run: python examples/native_harness_report.py
"""
from collections import Counter
import hashlib
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape


def main():
    root = Path(__file__).resolve().parent / 'reports' / 'native-harness'
    source = root / 'study-data.json'
    data = json.loads(source.read_text(encoding='utf-8'))
    baseline = data['baseline']
    assert len(baseline['runs']) == baseline['attempts'] == 48
    assert len({r['case'] for r in baseline['runs']}) == baseline['issue_families'] == 8
    assert all(n == 3 for n in Counter((r['agent'], r['case']) for r in baseline['runs']).values())
    for summary in baseline['summaries']:
        rows = [r for r in baseline['runs'] if r['agent'] == summary['agent']]
        assert summary['attempts'] == len(rows)
        assert summary['selected_checks_passed'] == sum(r['issue_resolved'] is True for r in rows)
        assert summary['complete_workflows'] == sum(r['native_session_complete'] is True for r in rows)
    env = Environment(loader=FileSystemLoader(root), autoescape=select_autoescape(default=True),
                      trim_blocks=True, lstrip_blocks=True)
    template = env.get_template('report.html.j2')
    html = template.render(data=data, data_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    output = root / 'index.html'
    output.write_text(html, encoding='utf-8', newline='\n')
    print(f'Rendered {output}')


if __name__ == '__main__':
    main()
