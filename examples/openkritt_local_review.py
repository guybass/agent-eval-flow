"""Run the pinned OpenKritt Flaskr review with a prepared local Docker service.

python -m examples.openkritt_local_review --config path/to/local.json --output demo-output/openkritt-local

The score measures this integration's evidence contract. It does not judge
whether a reported vulnerability is correct. One candidate is one workflow
configuration; its native step repeats are not independent benchmark tasks.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
import hashlib
from html import escape
import io
import json
from pathlib import Path, PurePosixPath
import re
from uuid import uuid4
from zipfile import ZipFile

import agent_eval_flow as a
from agent_eval_flow.adapters.openkritt import UPSTREAM_REVISION, artifact_bytes, evidence_refs, make_backend
from agent_eval_flow.objects.identity import plain
from agent_eval_flow.objects.values import zero_resources


FIXTURE = Path(__file__).resolve().parents[1] / "tests/e2e/fixtures/openkritt"
LOCAL_WORKFLOW = Path(__file__).resolve().parent / "workflows/openkritt_flaskr_local_v2.json"
CHECKS = {
    "native_completed": "The native scan completed and has no capture error",
    "workflow_attempts_complete": "All four native workflow steps completed every requested repeat",
    "inventory_complete": "The native execution inventory is explicitly complete",
    "native_traces_complete": "Every completed workflow attempt has its real harness stream",
    "tool_receipts_present": "Actual source inspection occurred and every native tool result retains its exact mapped evidence",
    "branch_inputs_preserved": "Both review branches and the merge received the actual earlier outputs",
    "bundle_verified": "The downloadable bundle contains all declared input and native evidence bytes",
    "output_matches_native": "The usable output object agrees with the native scan and findings",
}


def verify_fixture(fixture=FIXTURE):
    manifest = json.loads((fixture / "source_manifest.json").read_bytes())
    workflow = json.loads((fixture / "workflow.json").read_bytes())
    root = (fixture / "review_target").resolve()
    for row in manifest["files"]:
        path = (root / row["path"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Fixture manifest path escaped its source root")
        data = path.read_bytes()
        if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("Fixture source differs from its pinned manifest: " + row["path"])
    return manifest, workflow


def _json(run, name, default=None):
    ref = run.artifacts.get(name)
    return json.loads(artifact_bytes(ref)) if ref is not None else default


def tools_preserved(run, capture):
    """Check Codex completion records against mapped events, allowing tool-free steps."""
    expected = Counter()
    executions = {item.native_refs.get("step_metadata_id"): item.id for item in run.executions}
    for trace in capture.get("traces", []):
        if trace.get("protocol") != "codex-jsonl":
            return False  # This local showcase explicitly configures native Codex.
        ref = run.artifacts.get(trace["stdout_artifact"])
        if ref is None:
            return False
        for number, line in enumerate(artifact_bytes(ref).decode("utf-8", errors="replace").splitlines(), 1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            item = record.get("item", {})
            if record.get("type") != "item.completed" or not isinstance(item, dict) or item.get("type") != "command_execution":
                continue
            fields = {"native_id": str(item["id"]), "name": "command_execution", "arguments": {"command": item["command"]},
                      "result": {"output": item["aggregated_output"], "exit_code": item["exit_code"]}}
            expected[(executions.get(str(trace["metadata_id"])), json.dumps(fields, sort_keys=True),
                      ref.uri, f"line:{number}")] += 1
    actual = Counter()
    for event in run.events:
        if event.kind != "tool_call":
            continue
        if event.source is None or event.outputs != (event.source,) or event.inputs != (event.source,):
            # The Codex dialect puts invocation and completion in the same record.
            # Descriptions differ, so compare the actual source addresses below.
            if event.source is None or len(event.inputs) != 1 or len(event.outputs) != 1:
                return False
            if any(item.artifact != event.source.artifact or item.locator != event.source.locator
                   for item in (*event.inputs, *event.outputs)):
                return False
        actual[(event.execution_id, json.dumps(plain(event.fields), sort_keys=True),
                event.source.artifact.uri, event.source.locator)] += 1
    return bool(expected) and actual == expected


def _rendered_json_reference(template, prompt, reference):
    """Decode the JSON substituted at one known template reference, not arbitrary text."""
    matches = list(re.finditer(r"\{\{\s*" + re.escape(reference) + r"\s*\}\}", template))
    if len(matches) != 1:
        raise ValueError("Expected one native batch reference")
    match = matches[0]
    prefix = template[:match.start()].rsplit("}}", 1)[-1]
    suffix = template[match.end():].split("{{", 1)[0]
    if not prefix or prompt.count(prefix) != 1:
        raise ValueError("Rendered batch reference is missing or ambiguous")
    remaining = prompt[prompt.index(prefix) + len(prefix):].lstrip()
    value, end = json.JSONDecoder().raw_decode(remaining)
    if not remaining[end:].startswith(suffix):
        raise ValueError("Decoded batch does not occupy the actual template reference")
    return value


def _repeat_json(prompt):
    marker = "Results from earlier repeats:\n```json\n"
    if prompt.count(marker) != 1:
        raise ValueError("Native cumulative-repeat result block is missing or ambiguous")
    text = prompt.split(marker, 1)[1].lstrip()
    value, end = json.JSONDecoder().raw_decode(text)
    if not text[end:].lstrip().startswith("```"):
        raise ValueError("Native repeat JSON block is not terminated")
    return value


def lineage_preserved(metadata, results, workflow):
    """Compare actual native batch arrays and per-input cumulative-repeat envelopes."""
    steps = {str(row["id"]): row for row in workflow.get("steps", [])}
    completed = [row for row in metadata if row.get("kind") == "step" and row.get("status") == "completed"]
    if len(steps) != 4 or not completed:
        return False
    def same_input(left, right):
        return (str(left["step_id"]) == str(right["step_id"])
            and (left.get("prev_id") or 0) == (right.get("prev_id") or 0)
            and left.get("prev_table") == right.get("prev_table"))
    def bag(values):
        return Counter(json.dumps(value, sort_keys=True, ensure_ascii=False) for value in values)
    try:
        for row in completed:
            step = steps[str(row["step_id"])]
            depth = step["depth"]
            if depth:
                if not step.get("consumesAll"):
                    return False  # This showcase explicitly declares both consume-all boundaries.
                previous_ids = {identifier for identifier, previous in steps.items() if previous["depth"] == depth - 1}
                persisted = [result for result in results if str(result["step_id"]) in previous_ids]
                expected = [result["json_answer"] for result in persisted]
                produced = [answer for parent in completed if str(parent["step_id"]) in previous_ids
                            for answer in parent["output_json"]["results"]]
                if not expected or bag(expected) != bag(produced):
                    return False
                for result in persisted:
                    producer = [parent for parent in completed if same_input(parent, result)
                                and parent["repeat_run"] == result["repeat_run"]]
                    if len(producer) != 1 or bag([result["json_answer"]]) - bag(producer[0]["output_json"]["results"]):
                        return False
                actual = _rendered_json_reference(step["content"], row["prompt_filled"], f"multi_output_depth_{depth - 1}")
                if not isinstance(actual, list) or bag(actual) != bag(expected):
                    return False
            if row["repeat_run"] > 1:
                prior = [previous for previous in completed if same_input(previous, row)
                         and previous["repeat_run"] < row["repeat_run"]]
                if {previous["repeat_run"] for previous in prior} != set(range(1, row["repeat_run"])):
                    return False
                expected = [{"repeat_run": previous["repeat_run"], "result": answer}
                            for previous in prior for answer in previous["output_json"]["results"]]
                actual = _repeat_json(row["prompt_filled"])
                if not isinstance(actual, list) or bag(actual) != bag(expected):
                    return False
        return True
    except (ValueError, KeyError, TypeError):
        return False


def verify_bundle(run):
    ref = run.artifacts.get("openkritt.bundle")
    if ref is None:
        return False
    with ZipFile(io.BytesIO(artifact_bytes(ref))) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            return False
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
                return False
        index = json.loads(archive.read("manifest.json"))
        listed = {row["path"]: row for row in index["files"]}
        if len(listed) != len(index["files"]) or set(listed) != set(names) - {"manifest.json"}:
            return False
        for name, row in listed.items():
            data = archive.read(name)
            if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
                return False
        mapping = index["artifacts"]
        if set(mapping) != set(run.artifacts) - {"openkritt.bundle"}:
            return False
        if any(archive.read(path) != artifact_bytes(run.artifacts[name]) for name, path in mapping.items()):
            return False
        manifest = json.loads(archive.read("inputs/source_manifest.json"))
        if any(hashlib.sha256(archive.read("inputs/review_target/" + row["path"])).hexdigest() != row["sha256"]
               for row in manifest["files"]):
            return False
        def signature(artifact, locator, description):
            return json.dumps({"artifact": artifact, "locator": locator, "description": description}, sort_keys=True)
        expected = {signature(plain(source.artifact), source.locator, source.description): source for source in evidence_refs(run)}
        provenance = index.get("provenance", [])
        entries = {signature(row["artifact"], row["locator"], row["description"]): row for row in provenance}
        if len(entries) != len(provenance) or set(entries) != set(expected):
            return False
        for key, source in expected.items():
            path = entries[key]["path"]
            if path not in listed or archive.read(path) != artifact_bytes(source.artifact):
                return False
        return index["scan_id"] == run.native_refs.get("scan_id") and index["run_id"] == run.id


def inspect_capture(run, repeats):
    metadata = _json(run, "openkritt.step_metadata", [])
    results = _json(run, "openkritt.step_results", [])
    capture = _json(run, "openkritt.capture", {})
    workflow = _json(run, "openkritt.workflow", {})
    scan = _json(run, "openkritt.scan", {})
    findings = _json(run, "openkritt.findings")
    steps = {str(row["id"]): row for row in workflow.get("steps", [])}
    completed = [row for row in metadata if row.get("kind") == "step" and row.get("status") == "completed"]
    expected = Counter({(step_id, repeat): 1 for step_id in steps for repeat in range(1, repeats + 1)})
    actual = Counter((str(row["step_id"]), row["repeat_run"]) for row in completed)
    completed_ids = {str(row["id"]) for row in completed}
    trace_ids = {str(row["metadata_id"]) for row in capture.get("traces", [])}
    tools = [event for event in run.events if event.kind == "tool_call"]
    try:
        bundle_valid = verify_bundle(run)
    except (OSError, ValueError, KeyError):
        bundle_valid = False
    output = plain(run.output) if run.output_state == "available" else {}
    checks = {
        "native_completed": run.status == "completed" and run.error is None and not capture.get("capture_error"),
        "workflow_attempts_complete": len(steps) == 4 and actual == expected,
        "inventory_complete": run.execution_inventory_complete.status == "observed" and run.execution_inventory_complete.value is True,
        "native_traces_complete": bool(completed_ids) and completed_ids <= trace_ids,
        "tool_receipts_present": tools_preserved(run, capture),
        "branch_inputs_preserved": lineage_preserved(metadata, results, workflow),
        "bundle_verified": bundle_valid,
        "output_matches_native": bool(scan) and isinstance(findings, list) and output.get("scan_id") == str(scan.get("id"))
            and output.get("native_status") == scan.get("status") and output.get("findings") == findings,
    }
    return checks, metadata, tools


class CaptureMetrics:
    ref = a.VersionRef(name="example.openkritt-local-capture", revision="4")

    def compute(self, spec, context, run):
        checks, metadata, tools = inspect_capture(run, int(spec.params.get("repeat_runs", 2)))
        evidence = tuple(a.EvidenceRef(artifact=ref, description="Native " + name) for name, ref in run.artifacts.items()
                         if name in {"openkritt.capture", "openkritt.scan", "openkritt.step_metadata", "openkritt.step_results", "openkritt.bundle"})
        details = ()
        if spec.id in CHECKS:
            value, reason = checks[spec.id], CHECKS[spec.id] + "; integration behavior, not vulnerability accuracy"
        elif spec.id == "tool_results":
            value, reason = len(tools), "Actual completed native tool receipts; detailed rows retain command, result and source"
            details = tuple(a.Measurement(run_id=run.id, metric=spec.id, key={"event": event.id}, value=1,
                status="ok", basis="observed", reason=str(event.fields.get("arguments", {})), evidence=(event.source,)) for event in tools)
        elif spec.id == "completed_attempts":
            value = sum(row.get("kind") == "step" and row.get("status") == "completed" for row in metadata)
            reason = "Native step attempts; repeated and branched executions belong to one task"
        else:
            raise ValueError("Unknown metric: " + spec.id)
        return a.MetricOutput(task=a.Measurement(run_id=run.id, metric=spec.id, value=value, status="ok",
            basis="observed", reason=reason, evidence=evidence), details=details, evaluation_resources=zero_resources())


def build_study(config, *, fixture=FIXTURE, namespace=None, repeats=2, wall_time_s=1800, workflow_file=None):
    manifest, workflow = verify_fixture(fixture)
    namespace = namespace or "aef-local-" + uuid4().hex
    metric = CaptureMetrics()
    backend_ref = a.VersionRef(**config["backend_ref"])
    settings = dict(config.get("settings", {}))
    workflow_file = Path(workflow_file or config.get("workflow_file") or LOCAL_WORKFLOW).resolve()
    selected_workflow = json.loads(workflow_file.read_bytes())
    if selected_workflow.get("kind") != "open-kritt-workflow" or selected_workflow.get("version") != 2:
        raise ValueError("Selected local workflow must use the native portable version-2 format")
    if settings.get("scan_options", {}).get("harness") == "codex":
        for level in selected_workflow["workflow"]["levels"]:
            open_fields = [key for key, kind in level["outputFormat"].items() if kind == "object"]
            if open_fields:
                raise ValueError("This pinned OpenKritt Codex showcase cannot use free-form object fields: "
                    + ", ".join(open_fields) + ". Native schema.py generates additionalProperties=true; "
                    "choose array-of-string or string fields in an explicit local workflow variant.")
    provenance_file = workflow_file.with_suffix(".provenance.json")
    provenance = json.loads(provenance_file.read_bytes()) if provenance_file.exists() else {
        "variant": "explicit-caller-workflow", "derived_sha256": hashlib.sha256(workflow_file.read_bytes()).hexdigest()}
    if provenance["derived_sha256"] != hashlib.sha256(workflow_file.read_bytes()).hexdigest():
        raise ValueError("Local workflow differs from its recorded derivation hash")
    settings["workflow_provenance"] = provenance
    settings["openkritt"] = {"upstream_revision": UPSTREAM_REVISION,
        "fixture_dir": str((fixture / "review_target").resolve()), "source_manifest": str((fixture / "source_manifest.json").resolve()),
        "workflow_file": str(workflow_file), "repo_full": namespace,
        "post_script_id": config["post_script_id"], "dependencies": [], "scan_configuration": {"repeat_runs": repeats}}
    candidate = a.Candidate(id="openkritt-codex-local", backend=backend_ref, components={}, settings=settings)
    dataset = a.EvalDataset.from_records(id="pinned-flaskr-review", units=[{
        "task_id": "flaskr-auth-storage-review", "repo_kind": "local", "repo_full": namespace,
        "repo_scope": "Pinned Flaskr tutorial: flaskr/ application, SQL schema, templates and tests/. Review source only; trace authentication, author-only edits and request/database/rendering dataflow."}],
        unit_key=("task_id",), input_columns={"units": ("task_id", "repo_kind", "repo_full", "repo_scope")})
    dataset = replace(dataset, description=f"{len(manifest['files'])} upstream Flask tutorial files at {manifest['revision']}; four workflow stages and {repeats} native repeats")
    specs = tuple(a.MetricSpec(id=name, source=a.EvaluatorSource(ref=metric.ref), output_type="bool" if name in CHECKS else "int",
        role="diagnostic", params={"repeat_runs": repeats}) for name in (*CHECKS, "completed_attempts", "tool_results"))
    suite = a.EvalSuite(id="local-openkritt-evidence-contract", version="4-complete-evidence-lineage", metrics=specs,
        rubric=a.Rubric(terms=tuple(a.ScoreTerm(metric=name, points=12.5) for name in CHECKS)),
        acceptance=a.AcceptanceRule(all_of=tuple(a.Threshold(metric=name, op="==", value=True) for name in CHECKS)),
        summaries=(a.SummarySpec(id="capture_contract_score", metric="quality.score", reducer="mean"),
            a.SummarySpec(id="completed_workflow_attempts", metric="completed_attempts", reducer="sum"),
            a.SummarySpec(id="native_tool_results", metric="tool_results", reducer="sum"),
            a.SummarySpec(id="elapsed_seconds", metric="run.duration_s", reducer="mean"),
            a.SummarySpec(id="input_tokens", metric="run.input_tokens", reducer="sum"),
            a.SummarySpec(id="output_tokens", metric="run.output_tokens", reducer="sum"),
            a.SummarySpec(id="cost_usd", metric="run.cost_usd", reducer="sum")))
    study = a.Study(id="OpenKritt + Codex local Flaskr review", project_id="openkritt-local", dataset=dataset,
        question="Can Agent Eval Flow execute a real branched agent workflow, preserve its native evidence, and evaluate the saved result?",
        candidates={candidate.id: candidate}, suite=suite,
        execution=a.ExecutionPolicy(budget=a.Budget(wall_time_s=wall_time_s), infrastructure_retries=0))
    return study, metric


def write_overview(result, output):
    run = result.runs.runs[0]
    score = result.explain(run.id).score
    workflow = _json(run, "openkritt.workflow", {})
    names = {str(row["id"]): row["name"] for row in workflow.get("steps", [])}
    metadata = _json(run, "openkritt.step_metadata", [])
    tools = Counter(event.execution_id for event in run.events if event.kind == "tool_call")
    executions = {item.native_refs.get("step_metadata_id"): item.id for item in run.executions}
    cards = []
    for row in metadata:
        if row.get("kind") != "step":
            continue
        title = names.get(str(row.get("step_id")), "Native workflow attempt")
        cards.append(f'<article><h3>{escape(title)} · repeat {row.get("repeat_run")}</h3><p>{escape(str(row.get("status")))} · '
            f'{tools[executions.get(str(row["id"]))]} tool results · {row.get("run_time_ms")} ms</p>'
            '<details><summary>Actual structured step output</summary><pre>' + escape(json.dumps(row.get("output_json"), indent=2, ensure_ascii=False)) + '</pre></details>'
            '<details><summary>Actual filled prompt, including upstream outputs</summary><pre>' + escape(row.get("prompt_filled") or "") + '</pre></details></article>')
    contributions = ''.join(f'<tr><td>{escape(CHECKS[item.metric])}</td><td>{item.earned} / {item.possible}</td></tr>' for item in score.contributions)
    resources = run.resources()
    elapsed = run.duration_s()
    billing = "unknown" if resources.cost_usd.value is None else str(resources.cost_usd.value)
    page = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenKritt + Codex · local E2E</title><style>body{{font:16px/1.6 system-ui;margin:40px auto;max-width:1100px;padding:0 24px;color:#182230;background:#f4f6f8}}h1{{font-size:38px;line-height:1.15}}.flow,article,section{{background:white;border:1px solid #d9e1e8;border-radius:12px;padding:22px;margin:18px 0}}.flow{{font-weight:650;color:#174b6b}}.stats{{display:flex;gap:24px;flex-wrap:wrap}}.stats strong{{font-size:30px;display:block}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f8fa;padding:16px;font-size:12px}}table{{width:100%;border-collapse:collapse}}td{{padding:10px;border-bottom:1px solid #e6edf2}}summary,a{{cursor:pointer;color:#145d88}}small{{color:#536577}}</style>
<p>AGENT EVAL FLOW · REAL LOCAL RUN</p><h1>OpenKritt + Codex<br>Flaskr source review</h1>
<p>One task, one configured harness, four native workflow steps with repeats. This score checks the evaluation integration and evidence capture. It does not measure vulnerability accuracy or compare models.</p>
<div class="flow">22 pinned Flask files → application map → authentication and storage reviews → reconcile findings → Agent Eval Flow</div>
<section class="stats"><div><strong>{score.score.value}</strong>Capture contract / 100</div><div><strong>{escape(run.status)}</strong>Native run</div><div><strong>{sum(tools.values())}</strong>Native tool results</div><div><strong>{round(elapsed.value, 1) if elapsed.value is not None else 'unknown'}</strong>Seconds</div></section>
<p>Input tokens: {resources.input_tokens.value if resources.input_tokens.value is not None else 'unknown'} · Output tokens: {resources.output_tokens.value if resources.output_tokens.value is not None else 'unknown'} · Dollar charge: {billing}. Tokens are native receipts; they are not a bill.</p>
<p><a href="report.html">Full library report</a> · <a href="evidence.zip">Download all native evidence and source files</a> · <a href="summary.json">Machine-readable summary</a></p>
<section><h2>Why this score</h2><table>{contributions}</table><p>Acceptance: {score.acceptance}. Each check has 12.5 points. The saved evaluation contains the exact rules and evidence references.</p></section>
<h2>What the agent actually did</h2>{''.join(cards)}
<section><h2>Native delivered output</h2><pre>{escape(json.dumps(plain(run.output), indent=2, ensure_ascii=False))}</pre></section>
<p><small>Scan {escape(run.native_refs.get('scan_id', 'unavailable'))}. All displayed values come from the saved EvaluationResult. No external scripts or assets.</small></p></html>'''
    path = output / "overview.html"
    path.write_text(page, encoding="utf-8")
    return path


def save_outputs(study, result, metric, output):
    result.validate().raise_for_errors()
    result.save(output / "result")
    loaded = a.EvaluationResult.load(output / "result")
    loaded.validate().raise_for_errors()
    if loaded.id != result.id or plain(loaded.runs) != plain(result.runs):
        raise AssertionError("Persisted evaluation changed the capture")
    loaded.report(output / "report.html")
    # Demonstrate changing the rubric over the same native evidence, without a backend.
    new_terms = tuple(a.ScoreTerm(metric=name, points=20.0 if name in {"native_traces_complete", "tool_receipts_present"} else 10.0) for name in CHECKS)
    regrade_study = replace(study, suite=replace(study.suite, version="2-trace-priority", rubric=a.Rubric(terms=new_terms)))
    regraded = a.EvaluationPipeline(study=regrade_study, evaluators={metric.ref.name: metric}).eval(runs=loaded.runs)
    regraded.save(output / "regraded")
    regraded.report(output / "regraded-report.html")
    run = loaded.runs.runs[0]
    if "openkritt.bundle" in run.artifacts:
        (output / "evidence.zip").write_bytes(artifact_bytes(run.artifacts["openkritt.bundle"]))
    overview = write_overview(loaded, output)
    score = loaded.explain(run.id).score
    summary = {"overview": str(overview), "report": str(output / "report.html"), "saved_result": str(output / "result"),
        "scan_id": run.native_refs.get("scan_id"), "run_status": run.status, "capture_error": plain(run.error),
        "capture_contract_score": score.score.value, "capture_acceptance": score.acceptance,
        "score_is": "Structural integration and retained evidence; not security accuracy",
        "metrics": {row.summary_id: {"value": plain(row.value.value), "status": row.value.status, "reason": row.value.reason} for row in loaded.summary()},
        "planned_agent_tasks": len(study.plan().assignments), "saved_result_round_trip": True,
        "regraded_same_capture": regraded.runs.id == loaded.runs.id, "new_agent_invocations_during_regrade": 0,
        "reweighted_score": regraded.explain(run.id).score.score.value}
    (output / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="Prepared local connection JSON; credentials remain in the runtime")
    parser.add_argument("--base-url", help="Override the configured local OpenKritt HTTP URL")
    parser.add_argument("--output", type=Path, default=Path("demo-output/openkritt-local"))
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--workflow", type=Path, help="Explicit native portable workflow; default is the named Codex-schema-compatible Flaskr v2 variant")
    parser.add_argument("--repeat-runs", type=int, default=2)
    parser.add_argument("--wall-time-s", type=int, default=1800)
    args = parser.parse_args()
    if args.repeat_runs < 1 or args.wall_time_s < 1:
        parser.error("Repeat runs and wall-time seconds must be positive")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if args.base_url:
        config["base_url"] = args.base_url
    config.setdefault("connection_factory", "examples.integrations.openkritt_local:make_connection")
    config.setdefault("backend_ref", {"name": "openkritt-local", "revision": "aef-0.4.0"})
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / "result").exists():
        parser.error("Choose a fresh output directory to preserve the previous run")
    selected_workflow = Path(args.workflow or config.get("workflow_file") or LOCAL_WORKFLOW).resolve()
    configuration_dir = output / "configuration"
    configuration_dir.mkdir(exist_ok=True)
    copied_workflow = configuration_dir / "workflow.json"
    copied_workflow.write_bytes(selected_workflow.read_bytes())
    provenance_file = selected_workflow.with_suffix(".provenance.json")
    if provenance_file.exists():
        copied_workflow.with_suffix(".provenance.json").write_bytes(provenance_file.read_bytes())
    study, metric = build_study(config, fixture=args.fixture.resolve(), repeats=args.repeat_runs,
                               wall_time_s=args.wall_time_s, workflow_file=copied_workflow)
    study.save(output / "study")
    backend = make_backend(config, workspace=output / "workspace")
    print(json.dumps({"starting": "Real native OpenKritt + Codex review", "output": str(output),
        "task_count": 1, "workflow_steps": 4, "native_repeats": args.repeat_runs,
        "timeout_seconds": args.wall_time_s, "model": config.get("settings", {}).get("scan_options", {}).get("model")}), flush=True)
    result = a.EvaluationPipeline(study=study, backends={backend.ref.name: backend}, evaluators={metric.ref.name: metric}).eval()
    summary = save_outputs(study, result, metric, output)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str), flush=True)
    return 0 if summary["capture_acceptance"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
