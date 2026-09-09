"""Render a local integration-attempt index from explicit saved evaluations.

python -m examples.openkritt_attempts_report --runs demo-output/openkritt-local demo-output/openkritt-local-run2 demo-output/openkritt-local-run3 --output demo-output/openkritt-results/index.html

No backend is bound, no credentials are accessed, and no model is called.
Repeated setup/execution attempts remain separate; unknown costs stay unknown.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from html import escape
import json
import os
from pathlib import Path

import agent_eval_flow as a
from agent_eval_flow.objects.identity import plain


def _json(value):
    return json.dumps(plain(value), ensure_ascii=False, indent=2, default=lambda item:
                      str(item) if isinstance(item, (datetime, Decimal)) else repr(item))


def _observation(observation, *, decimals=None):
    if observation.status == "unknown" or observation.value is None:
        value = "unknown"
    elif decimals is not None:
        value = f"{observation.value:,.{decimals}f}"
    elif type(observation.value) in {int, float, Decimal}:
        value = f"{observation.value:,}"
    else:
        value = str(observation.value)
    return f'<span class="value">{escape(value)}</span><small>{escape(observation.status)}</small>'


def _link(path, *, output):
    path = Path(path).resolve()
    if not path.exists():
        return None
    try:
        return Path(os.path.relpath(path, output.parent)).as_posix()
    except ValueError:  # Different Windows drive; preserve the known local file.
        return path.as_uri()


def _links(directory, *, output):
    labels = (("overview.html", "Readable run report"), ("report.html", "Full library result"),
              ("evidence.zip", "Download evidence"), ("summary.json", "JSON summary"))
    links = []
    for name, label in labels:
        href = _link(directory / name, output=output)
        if href:
            links.append(f'<a href="{escape(href, quote=True)}">{label}</a>')
    return ' <span aria-hidden="true">·</span> '.join(links)


def _attempt(directory):
    directory = Path(directory).resolve()
    study = a.Study.load(directory / "study")
    result = a.EvaluationResult.load(directory / "result")
    result.validate().raise_for_errors()
    if len(result.runs.runs) != 1:
        raise ValueError("This local attempt index expects one task run per supplied directory")
    run = result.runs.runs[0]
    score = result.explain(run.id).score
    assignment = next(item for item in result.runs.plan.assignments if item.id == run.assignment_id)
    candidate = result.runs.plan.candidates[assignment.candidate_id]
    scan_options = candidate.settings.get("scan_options", {})
    workflow = candidate.settings.get("workflow_provenance", {})
    steps = [event for event in run.events if event.kind == "workflow_step" and event.fields.get("native", {}).get("kind") == "step"]
    completed = sum(event.fields.get("native", {}).get("status") == "completed" for event in steps)
    output = plain(run.output) if run.output_state == "available" else {}
    native_models = sorted({str(execution.effective_config.value["model"]) for execution in run.executions
        if execution.effective_config.value and execution.effective_config.value.get("model")})
    return {"directory": directory, "study": study, "result": result, "run": run, "score": score,
        "model": scan_options.get("model", "not recorded"), "native_models": native_models,
        "workflow_variant": workflow.get("variant", "original frozen workflow"),
        "tools": sum(event.kind == "tool_call" for event in run.events), "completed_steps": completed,
        "captured_steps": len(steps), "output": output, "resources": run.resources(), "duration": run.duration_s()}


def render(run_directories, *, output, assessment=None):
    if not run_directories:
        raise ValueError("Supply the attempt directories explicitly and in chronological order")
    output = Path(output).resolve()
    directories = [Path(path).resolve() for path in run_directories]
    if len(set(directories)) != len(directories):
        raise ValueError("An attempt directory was supplied more than once")
    attempts = [_attempt(path) for path in directories]
    current = _attempt(assessment) if assessment is not None else attempts[-1]
    repackaged = False
    if assessment is not None:
        original_run = attempts[-1]["run"]
        candidate_run = current["run"]
        if plain(candidate_run) != plain(original_run):
            restored = replace(candidate_run, artifacts={**candidate_run.artifacts,
                "openkritt.bundle": original_run.artifacts["openkritt.bundle"]})
            if plain(restored) != plain(original_run):
                raise ValueError("A separate assessment may only replace the portable bundle; all native execution records must remain identical")
            repackaged = True
    run, score = current["run"], current["score"]
    output.parent.mkdir(parents=True, exist_ok=True)
    rows, errors = [], []
    for ordinal, item in enumerate(attempts, 1):
        native_status = item["output"].get("native_status") or "no native scan"
        cost = item["resources"].cost_usd
        rows.append(f'''<tr><td><strong>Attempt {ordinal}</strong><small>{escape(item['directory'].name)}</small>
<small>{escape(item['workflow_variant'])}</small><small>{escape(item['model'])}</small></td>
<td><span class="status {escape(item['run'].status)}">{escape(item['run'].status)}</span><small>Native: {escape(native_status)}</small>
<small>Scan: {escape(item['run'].native_refs.get('scan_id', 'not created'))}</small></td>
<td>{_observation(item['score'].score, decimals=1)}<small>/{item['result'].suite.rubric.max_score:g} · {escape(item['score'].acceptance)}</small></td>
<td>{item['completed_steps']} / {item['captured_steps']}<small>completed / captured</small></td><td>{item['tools']}</td>
<td>{_observation(item['duration'], decimals=1)}</td><td>{_observation(item['resources'].input_tokens)}<small>input</small>
{_observation(item['resources'].output_tokens)}<small>output</small></td><td>{_observation(cost)}</td></tr>
<tr class="links"><td colspan="8">{_links(item['directory'], output=output)}</td></tr>''')
        if item["run"].error:
            errors.append(f'<details><summary>Attempt {ordinal}: retained error</summary><pre>{escape(_json(item["run"].error))}</pre></details>')
    checks = ''.join(f'<tr><td>{escape(contribution.metric)}</td><td>{contribution.earned if contribution.earned is not None else "unknown"} / {contribution.possible:g}</td>'
        f'<td>{escape(contribution.reason)}</td></tr>' for contribution in score.contributions)
    findings = current["output"].get("findings")
    if findings is None:
        finding_note = "Native findings are unavailable in this attempt."
    elif not findings:
        finding_note = "The native workflow returned an empty finding list. The integration accepts an empty list; this is not proof that the source is vulnerability-free."
    else:
        finding_note = f"The native workflow returned {len(findings)} finding record(s). These are agent claims retained for review; this integration score does not establish their correctness."
    serializable = {"attempt_directories": [str(path) for path in directories],
        "assessment_directory": str(current["directory"]) if assessment is not None else None,
        "assessment_repackaged_evidence_only": repackaged,
        "scenario": "Repeated integration attempts of the same local Flaskr review", "latest_scan_id": run.native_refs.get("scan_id"),
        "latest_result_id": current["result"].id, "latest_acceptance": score.acceptance,
        "latest_score": plain(score.score), "score_meaning": "Integration behavior and evidence retention",
        "independent_benchmark_comparison": False, "model_calls_by_this_report": 0,
        "attempts": [{"directory": str(item["directory"]), "run_status": item["run"].status,
            "scan_id": item["run"].native_refs.get("scan_id"), "tools": item["tools"],
            "completed_steps": item["completed_steps"], "captured_steps": item["captured_steps"],
            "duration_s": plain(item["duration"]), "resources": plain(item["resources"]),
            "score": plain(item["score"].score), "model": item["model"], "native_models": item["native_models"],
            "workflow_variant": item["workflow_variant"], "suite_version": item["result"].suite.version} for item in attempts]}
    data_path = output.with_suffix(".json")
    data_path.write_text(_json(serializable), encoding="utf-8")
    assessment_note = ("The latest native execution was reassessed offline using the corrected native-lineage metric. "
        "Its original assessment remains in the execution history below. "
        + ("A derived capture adds complete portable provenance; every native execution, event and output remains identical. " if repackaged else "The capture is identical. ")
        + "The reassessment made zero model calls."
        if assessment is not None else "The latest recorded assessment is shown below.")
    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agent Eval Flow · Local OpenKritt results</title><style>
:root{{color-scheme:light}}*{{box-sizing:border-box}}body{{font:16px/1.55 system-ui,-apple-system,sans-serif;color:#192c3c;background:#f3f6f8;margin:0}}main{{max-width:1250px;margin:auto;padding:42px 28px 64px}}h1{{font-size:clamp(28px,4vw,46px);line-height:1.12;letter-spacing:-1px;margin:12px 0 22px}}h2{{font-size:22px;margin-top:0}}p{{max-width:1000px}}.eyebrow{{font-size:12px;letter-spacing:2px;font-weight:700;color:#247d88}}.muted,small{{color:#536779}}small{{display:block;font-size:12px;margin:3px 0}}section,.flow{{background:white;border:1px solid #dce5ec;border-radius:14px;padding:24px;margin:22px 0}}.flow{{background:#143b52;color:white;font-weight:600}}.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}}.stat{{background:white;border:1px solid #dce5ec;border-radius:12px;padding:18px}}.stat strong{{display:block;font-size:30px;letter-spacing:-.5px}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{text-align:left;vertical-align:top;border-bottom:1px solid #e4ebf0;padding:12px 10px}}th{{color:#486174;font-size:12px;text-transform:uppercase;letter-spacing:.4px}}.links td{{padding-top:0;padding-bottom:18px}}a{{color:#096c85;text-decoration:none;font-weight:600}}a:hover{{text-decoration:underline}}.status{{display:inline-block;padding:3px 8px;border-radius:5px;background:#fce9df;color:#8c481d}}.status.completed{{background:#e2f3eb;color:#226a4c}}.value{{font-weight:700}}details{{margin:14px 0}}summary{{cursor:pointer;font-weight:650;color:#155c78}}pre{{font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere;padding:18px;border-radius:8px;background:#f2f5f7}}.notice{{border-left:4px solid #e0a548;padding-left:16px}}@media(max-width:750px){{.stats{{grid-template-columns:1fr 1fr}}main{{padding:24px 16px}}section{{padding:18px}}}}
</style></head><body><main><div class="eyebrow">AGENT EVAL FLOW · LOCAL END-TO-END EXECUTION</div>
<h1>OpenKritt + Codex<br>One real workflow, all attempts retained</h1>
<p>OpenKritt orchestrated a source review of the pinned Flask tutorial application. Agent Eval Flow captured native workflow attempts, tool choices and results, applied explicit integration checks, and saved usable result objects and reports.</p>
<div class="flow">Flaskr source → application map → authentication and storage branches → findings synthesis → captured evaluation</div>
<div class="stats"><div class="stat"><strong>{escape(run.status)}</strong>Latest run status</div><div class="stat"><strong>{score.score.value if score.score.value is not None else 'unknown'} / {current['result'].suite.rubric.max_score:g}</strong>Integration capture score</div><div class="stat"><strong>{current['tools']}</strong>Actual native tool results</div><div class="stat"><strong>{current['completed_steps']}</strong>Completed native workflow attempts</div></div>
<p class="notice">These are {len(attempts)} attempts to run the same integration scenario. Native step repeats remain part of that workflow. The scores describe execution and evidence capture; they do not rank agent intelligence or establish security accuracy.</p>
<section><h2>Latest result</h2><p>{escape(assessment_note)}</p><p>Configured model: <strong>{escape(current['model'])}</strong>. Native recorded models: <strong>{escape(', '.join(current['native_models']) or 'unavailable')}</strong>.
Workflow: <code>{escape(current['workflow_variant'])}</code>. Evaluation suite: <code>{escape(current['result'].suite.version)}</code>.</p>
<p>{_links(current['directory'], output=output)}</p><p>{escape(finding_note)}</p><details><summary>Inspect the actual delivered findings</summary><pre>{escape(_json(findings))}</pre></details></section>
<section><h2>Execution history</h2><div class="scroll"><table><thead><tr><th>Attempt / configuration</th><th>Status</th><th>Capture score</th><th>Native steps</th><th>Tools</th><th>Seconds</th><th>Tokens</th><th>USD</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<p class="muted">Every failed attempt remains visible. Unknown token quantities and charges stay unknown. Provider token receipts are not a dollar invoice; no total cost is inferred from missing charges.</p>{''.join(errors)}</section>
<section><h2>Why the latest score has this value</h2><div class="scroll"><table><thead><tr><th>Check</th><th>Points</th><th>Recorded reason</th></tr></thead><tbody>{checks}</tbody></table></div>
<p>Acceptance: <strong>{escape(score.acceptance)}</strong>. The full report links each measurement to retained native evidence. Each score term is defined in the saved evaluation suite.</p></section>
<p class="muted">Generated from typed saved EvaluationResult objects. This page made zero model calls. Its scripts, fonts and styles require no network. <a href="{escape(data_path.name, quote=True)}">Structured index data</a>.</p></main></body></html>'''
    output.write_text(page, encoding="utf-8")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", nargs="+", required=True, type=Path, help="Saved attempt directories in chronological order; final entry is the latest result")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--assessment", type=Path, help="Optional offline reassessment of the final exact captured run; never counted as another execution attempt")
    args = parser.parse_args()
    path = render(args.runs, output=args.output, assessment=args.assessment)
    print(json.dumps({"report": str(path), "attempts": len(args.runs), "model_calls": 0}, indent=2), flush=True)


if __name__ == "__main__":
    main()
