"""Render explicit local OpenSRE attempts without starting a runtime or model."""
from __future__ import annotations

import argparse
from html import escape
import json
import os
from pathlib import Path

import agent_eval_flow as a
from agent_eval_flow.objects.identity import plain


def render(directories, output):
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    items, cards = [], []
    for index, directory in enumerate(directories, 1):
        directory = Path(directory).resolve()
        result = a.EvaluationResult.load(directory / "result")
        result.validate().raise_for_errors()
        run = result.runs.runs[0]
        score = result.explain(run.id).score
        values = {row.summary_id: plain(row.value) for row in result.summary()}
        base = Path(os.path.relpath(directory, output.parent)).as_posix()
        links = ' · '.join(f'<a href="{escape(base, quote=True)}/{name}">{label}</a>' for name, label in (
            ("overview.html", "Readable investigation"), ("report.html", "Full evaluation"),
            ("evidence.zip", "Download evidence"), ("summary.json", "JSON summary")))
        item = {"directory": str(directory), "run_id": run.id, "invocation_id": run.native_refs.get("invocation_id"),
                "status": run.status, "score": plain(score.score), "acceptance": score.acceptance,
                "error": plain(run.error), "metrics": values}
        items.append(item)
        error = f'<details><summary>Retained failure</summary><pre>{escape(json.dumps(plain(run.error), indent=2))}</pre></details>' if run.error else ''
        cards.append(f'<section><h2>Attempt {index}: {escape(run.status)}</h2><p><strong>{score.score.value} / 100</strong> integration checks · {escape(score.acceptance)} · '
            f'{run.duration_s().value:.1f} seconds</p><p>{links}</p>{error}</section>')
    latest = items[-1]
    payload = {"scenario": "Local OpenSRE investigation of the HDFS sample", "attempts": items,
               "latest_run_id": latest["run_id"], "independent_benchmark_comparison": False, "model_calls_by_this_report": 0}
    output.with_suffix(".json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    page = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent Eval Flow · OpenSRE results</title><style>body{{font:16px/1.6 system-ui;max-width:1000px;margin:40px auto;padding:0 24px;background:#f3f6f8;color:#182f40}}h1{{font-size:42px;line-height:1.15}}section{{background:white;border:1px solid #dbe4eb;padding:24px;margin:22px 0;border-radius:14px}}a{{color:#006c86}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}.flow{{background:#163d53;color:white;padding:22px;border-radius:12px}}small{{color:#536779}}</style>
<p>AGENT EVAL FLOW · REAL LOCAL EXECUTION</p><h1>OpenSRE + Codex<br>HDFS incident investigation</h1>
<div class="flow">2,000 log lines → incident → paginated search → metrics, topology and changes → cited report</div>
<p>These are attempts at one real integration scenario. The checks establish usable execution evidence and result objects. Root-cause claims remain hypotheses, and synthetic change history is identified as exercise context.</p>
<p>The latest result is attempt {len(items)}. Its readable investigation shows the actual tool inputs, results and delivered report. Saving, reloading and changing score weights use the same captured execution. Unavailable token and dollar totals remain unknown.</p>
{''.join(reversed(cards))}<p><small>Every earlier failure is retained. This page made zero model calls. <a href="{escape(output.with_suffix('.json').name, quote=True)}">Structured attempt index</a></small></p></html>'''
    output.write_text(page, encoding="utf-8")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", required=True, nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not args.runs:
        parser.error("Supply at least one recorded attempt")
    print(render(args.runs, args.output))


if __name__ == "__main__":
    main()
