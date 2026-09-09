"""Run the real pinned OpenSRE harness on the downloaded HDFS incident sample.

python -m examples.opensre_local_review --config path/to/local.json --output demo-output/opensre-local

The eight checks concern usable outputs and evidence transport, not whether an
incident diagnosis is correct. Regrading reads retained evidence without a model.
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
from zipfile import BadZipFile, ZipFile

import agent_eval_flow as a
from agent_eval_flow.adapters.openkritt import artifact_bytes
from agent_eval_flow.adapters.opensre import ENTRYPOINT, UPSTREAM_REVISION, make_backend
from agent_eval_flow.objects.identity import plain
from agent_eval_flow.objects.values import zero_resources
from tests.e2e.fixtures.opensre.incident_store import TOOL_NAMES, verify_inputs


FIXTURE = Path(__file__).resolve().parents[1] / "tests/e2e/fixtures/opensre"
CHECKS = {
    "native_completed": "Native OpenSRE completed with an explicitly complete execution capture",
    "multi_iteration_flow": "At least three real provider iterations are recorded",
    "tool_receipts_preserved": "Six observability tools actually ran and every successful call matches its independent receipt",
    "pagination_lineage": "A later iteration fetched a real continuation cursor and retained at least eight source lines",
    "source_integrity": "Downloaded inputs and tool-derived counts, topology and changes match their source bytes",
    "native_trace_mapping": "Every native event maps once to a usable event with its exact evidence location",
    "report_usable": "The saved report, evidence citations, native turn and report-write receipt agree",
    "bundle_verified": "The portable ZIP contains every retained artifact with verified bytes and hashes",
}
INPUTS = {"incident.source_logs": "upstream/HDFS_2k.log", "incident.context": "context.json",
          "incident.provenance": "SOURCES.json", "incident.license": "upstream/LOGHUB_LICENSE"}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _json(run, name, default=None):
    ref = run.artifacts.get(name)
    return json.loads(artifact_bytes(ref)) if ref is not None else default


def _jsonl(run, name):
    ref = run.artifacts.get(name)
    return [json.loads(line) for line in artifact_bytes(ref).splitlines() if line.strip()] if ref is not None else []


def verify_bundle(run):
    _require("incident.bundle" in run.artifacts, "The incident evidence ZIP is missing")
    with ZipFile(io.BytesIO(artifact_bytes(run.artifacts["incident.bundle"]))) as archive:
        names = archive.namelist()
        _require(len(names) == len(set(names)), "ZIP contains duplicate paths")
        for name in names:
            path = PurePosixPath(name)
            _require(not path.is_absolute() and ".." not in path.parts and "\\" not in name and ":" not in name,
                     "Unsafe ZIP path: " + name)
        manifest = json.loads(archive.read("manifest.json"))
        _require(manifest["invocation_id"] == run.native_refs["invocation_id"], "ZIP invocation identity differs")
        _require(set(manifest["files"]) == set(names) - {"manifest.json"}, "ZIP inventory is incomplete")
        retained = Counter(ref.sha256 for name, ref in run.artifacts.items() if name != "incident.bundle")
        _require(Counter(manifest["files"].values()) == retained, "ZIP inventory differs from retained artifacts")
        by_hash = {ref.sha256: artifact_bytes(ref) for name, ref in run.artifacts.items() if name != "incident.bundle"}
        for path, digest in manifest["files"].items():
            data = archive.read(path)
            _require(hashlib.sha256(data).hexdigest() == digest and data == by_hash[digest], "ZIP bytes differ: " + path)
    return True


def inspect_capture(run, *, fixture=FIXTURE):
    """Inspect immutable receipts; a missing or inconsistent record fails its check."""
    trace, audit = _jsonl(run, "native.trace"), _jsonl(run, "incident.tool_audit")
    native, receipt = _json(run, "native.turn", {}), _json(run, "native.receipt", {})
    report = _json(run, "incident.report", {})
    invocation = run.native_refs.get("invocation_id")
    iterations = {(row.get("loop_id"), row.get("event", {}).get("iteration")) for row in trace
                  if row.get("event", {}).get("type") == "provider_request_start"}
    starts, ends = {}, {}
    duplicate_tools = False
    for row in trace:
        event = row["event"]
        if event["type"] in {"tool_execution_start", "tool_execution_end"}:
            target = starts if event["type"] == "tool_execution_start" else ends
            key = (row["loop_id"], event["tool_call_id"])
            duplicate_tools |= key in target
            target[key] = row
    receipts = {row["result"]["receipt_id"]: row for row in audit}

    def native_completed():
        _require(run.status == "completed" and run.error is None, "Native run or evidence capture did not complete")
        _require(run.execution_inventory_complete.status == "observed" and run.execution_inventory_complete.value is True,
                 "Execution inventory is not explicitly complete")
        _require(receipt["agent"]["revision"] == UPSTREAM_REVISION and receipt["agent"]["entrypoint"] == ENTRYPOINT,
                 "Native revision or entrypoint differs")
        _require(receipt["invocation_id"] == invocation and receipt["capture"]["runtime_callback"] == "core.agent.Agent.on_runtime_event",
                 "Native receipt invocation or callback differs")
        _require(receipt["capture"]["complete"] is True and receipt["capture"]["artifacts_complete"] is True,
                 "Native callback or artifact capture is incomplete")
        _require(receipt["capture"]["event_count"] == len(trace) and receipt["capture"]["tool_receipt_count"] == len(audit),
                 "Native receipt counts differ from captured rows")
        if "native.docker.receipt" in run.artifacts:
            docker = _json(run, "native.docker.receipt")
            process = receipt["process"]
            _require(docker["invocation_id"] == invocation and docker["stopped"] is True
                     and not docker["deadline_exceeded"] and docker["container_exit_code"] == 0,
                     "Docker lifecycle did not confirm this completed invocation")
            _require(process["container"] == docker["container"] and process["image_id"] == docker["image_id"]
                     and process["exit_code"] == docker["container_exit_code"],
                     "Native process identity differs from the Docker lifecycle receipt")

    def multi_iteration_flow():
        _require(len(iterations) >= 3, f"Only {len(iterations)} provider iterations were captured")

    def tool_receipts_preserved():
        _require(bool(trace) and len(audit) >= 7 and {row["tool_name"] for row in audit} == set(TOOL_NAMES),
                 "Expected six real tool types and at least seven successful receipts")
        _require(not duplicate_tools and starts.keys() == ends.keys(), "Tool starts/completions are missing or duplicated")
        _require([row["sequence"] for row in audit] == list(range(len(audit))), "Tool receipt order differs")
        _require(len(receipts) == len(audit), "Tool receipt IDs are duplicated")
        successful = {key for key, row in ends.items() if not row["event"]["is_error"]}
        _require({(row["loop_id"], row["tool_call_id"]) for row in audit} == successful,
                 "Successful native calls and independent receipts differ")
        snapshots = {row["result"]["snapshot_id"] for row in audit}
        _require(len(snapshots) == 1, "Tool calls used different incident snapshots")
        for row in audit:
            start, end = starts[(row["loop_id"], row["tool_call_id"])], ends[(row["loop_id"], row["tool_call_id"])]
            _require(row["schema_version"] == "aef-incident-tool-receipt/1" and row["invocation_id"] == invocation,
                     "Tool receipt identity differs")
            _require(start["sequence"] < end["sequence"] and row["started_at"] <= row["ended_at"], "Tool completion precedes its start")
            _require(start["event"]["tool_name"] == end["event"]["tool_name"] == row["tool_name"]
                and start["event"]["args"] == end["event"]["args"] == row["arguments"]
                and end["event"]["result"] == row["result"], "Native tool arguments/results differ from receipt")
            if row["tool_name"] != "fixture_incident_open":
                _require(row["arguments"]["snapshot_id"] in snapshots, "Tool input does not use the opened snapshot")

    def pagination_lineage():
        searches = [row for row in audit if row["tool_name"] == "fixture_logs_search"]
        pages = [row for row in searches if row["arguments"].get("cursor")]
        _require(bool(pages), "No continuation cursor was actually fetched")
        lines = {line["line_id"] for row in searches for line in row["result"]["rows"]}
        _require(len(lines) >= 8, f"Only {len(lines)} distinct source lines were retrieved")
        for row in pages:
            previous = receipts[row["result"]["previous_receipt_id"]]
            _require(row["arguments"]["cursor"] == previous["result"]["next_cursor"]
                and row["result"]["offset"] == previous["result"]["offset"] + 4, "Continuation cursor or offset differs")
            before, after = ends[(previous["loop_id"], previous["tool_call_id"])], starts[(row["loop_id"], row["tool_call_id"])]
            _require(before["sequence"] < after["sequence"] and (before["loop_id"], before["event"]["iteration"]) !=
                     (after["loop_id"], after["event"]["iteration"]), "Continuation was not chosen after observing the previous page")

    def source_integrity():
        rows = verify_inputs(fixture)
        for name, relative in INPUTS.items():
            _require(artifact_bytes(run.artifacts[name]) == (fixture / relative).read_bytes(), "Retained input differs: " + name)
        for page in audit:
            if page["tool_name"] == "fixture_logs_search":
                for row in page["result"]["rows"]:
                    _require(row == rows[row["line_id"] - 1], "A retrieved log row differs from source")
        context = _json(run, "incident.context")
        metrics = [row["result"] for row in audit if row["tool_name"] == "fixture_metrics_query"]
        changes = [row["result"] for row in audit if row["tool_name"] == "fixture_changes_list"]
        topologies = [row["result"] for row in audit if row["tool_name"] == "fixture_topology_describe"]
        _require(bool(metrics) and bool(changes) and bool(topologies), "Some derived source tools were not called")
        counts = Counter((row["time"][:2], row["level"]) for row in rows if row["date"] == context["window"]["date"])
        hosts = Counter(host for row in rows for host in row["hosts"])
        for result in metrics:
            _require({(row["hour"], row["level"]): row["count"] for row in result["series"]} == counts, "Sample-derived counts differ")
        for result in changes:
            _require(result["changes"] == context["changes"], "Synthetic change history differs")
        for result in topologies:
            _require({row["address"]: row["sample_mentions"] for row in result["nodes"]} == hosts
                and set(result["components"]) == {row["component"] for row in rows}, "Sample-derived topology differs")

    def native_trace_mapping():
        _require(bool(trace) and [row["sequence"] for row in trace] == list(range(len(trace))), "Native event order is incomplete")
        _require(all(row["invocation_id"] == invocation and row["loop_id"] for row in trace), "Native event identity differs")
        events = [event for event in run.events if "native_sequence" in event.fields]
        normalized = {event.fields["native_sequence"]: event for event in events}
        _require(len(normalized) == len(events) == len(trace), "Native event mapping lost or duplicated records")
        for row in trace:
            event = normalized[row["sequence"]]
            _require(plain(event.fields["native"]) == row["event"], "Normalized event changed its native payload")
            _require(event.execution_id in {execution.id for execution in run.executions}, "Event execution link is missing")
            _require(event.source is not None and event.source.artifact == run.artifacts["native.trace"]
                and event.source.locator == f"line:{row['sequence'] + 1}", "Event evidence location differs")
            _require(any(ref.artifact == run.artifacts["native.trace"] for ref in (*event.inputs, *event.outputs)),
                     "Event lacks its input/output evidence reference")

    def report_usable():
        _require(run.output_state == "available" and plain(run.output) == native, "Usable output differs from native TurnResult")
        action = native["action_result"]
        _require(action["accounting_status"] == "completed" and not action.get("cancelled") and not action.get("hit_iteration_cap"),
                 "Native turn did not finish")
        _require(bool((native.get("assistant_response_text") or action.get("response_text") or "").strip()), "Native answer is empty")
        _require(report["incident_id"] == _json(run, "incident.context")["incident_id"] and bool(report["summary"].strip()), "Report identity or summary is missing")
        for key in ("hypotheses", "timeline", "evidence", "limitations", "next_actions"):
            _require(isinstance(report[key], list) and bool(report[key]), "Report section is empty: " + key)
        cited = {row["receipt_id"] for row in report["evidence"]}
        _require(cited <= receipts.keys(), "Report cites an unknown receipt")
        _require({receipts[key]["tool_name"] for key in cited} >= {
            "fixture_logs_search", "fixture_metrics_query", "fixture_changes_list", "fixture_topology_describe"},
            "Report does not cite the observed logs, metrics, changes and topology")
        writes = [row for row in audit if row["tool_name"] == "fixture_report_write"]
        _require(bool(writes) and writes[-1]["arguments"]["report"] == report
            and writes[-1]["result"]["sha256"] == run.artifacts["incident.report"].sha256, "Report-write bytes differ")

    checks, failures = {}, {}
    functions = (native_completed, multi_iteration_flow, tool_receipts_preserved, pagination_lineage,
                 source_integrity, native_trace_mapping, report_usable, lambda: verify_bundle(run))
    for name, function in zip(CHECKS, functions):
        try:
            function()
            checks[name] = True
        except (AssertionError, OSError, ValueError, KeyError, IndexError, TypeError, BadZipFile) as exc:
            checks[name] = False
            failures[name] = str(exc) or type(exc).__name__
    return {"checks": checks, "failures": failures, "trace": trace, "audit": audit,
            "receipt": receipt, "report": report, "iterations": len(iterations)}


class CaptureMetrics:
    ref = a.VersionRef(name="example.opensre-local-capture", revision="2")

    def compute(self, spec, context, run):
        data = inspect_capture(run, fixture=Path(spec.params.get("fixture_dir", FIXTURE)))
        evidence = tuple(a.EvidenceRef(artifact=ref, description="Retained " + name) for name, ref in run.artifacts.items()
                         if name in {"native.receipt", "native.trace", "incident.tool_audit", "incident.report", "incident.bundle"})
        details = ()
        if spec.id in CHECKS:
            value = data["checks"][spec.id]
            reason = CHECKS[spec.id] + ("; verified" if value else "; " + data["failures"][spec.id])
        elif spec.id == "tool_receipts":
            value, reason = len(data["audit"]), "Actual successful tool receipts; one detail row per invocation"
            details = tuple(a.Measurement(run_id=run.id, metric=spec.id, key={"receipt": row["result"]["receipt_id"]}, value=1,
                status="ok", basis="observed", reason=row["tool_name"], evidence=(a.EvidenceRef(
                    artifact=run.artifacts["incident.tool_audit"], locator=f"line:{index + 1}", description="Independent tool receipt"),))
                for index, row in enumerate(data["audit"]))
        elif spec.id == "provider_iterations":
            value, reason = data["iterations"], "Actual native provider request iterations in this incident investigation"
        else:
            raise ValueError("Unknown metric: " + spec.id)
        return a.MetricOutput(task=a.Measurement(run_id=run.id, metric=spec.id, value=value, status="ok", basis="observed",
            reason=reason, evidence=evidence), details=details, evaluation_resources=zero_resources())


def build_study(config, *, fixture=FIXTURE, wall_time_s=1200):
    rows = verify_inputs(fixture)
    units = json.loads((fixture / "incidents.json").read_bytes())
    metric = CaptureMetrics()
    settings = {**config.get("settings", {}), "native_entrypoint": ENTRYPOINT,
        "native_capture": "typed_runtime_events_and_tool_receipts", "required_upstream_revision": UPSTREAM_REVISION,
        "prompt_column": "prompt", "fixture_dir": str(fixture.resolve()), "fixture_store": "incident_store.IncidentStore",
        "fixture_tool_names": TOOL_NAMES, "artifact_bundle": "incident.bundle"}
    settings.setdefault("native_max_iterations", 18)
    candidate = a.Candidate(id="opensre-codex-local", backend=a.VersionRef(**config["backend_ref"]), components={}, settings=settings)
    dataset = a.EvalDataset.from_records(id="pinned-hdfs-incident", units=units, unit_key=("task_id",),
        input_columns={"units": ("task_id", "incident_id", "prompt")})
    dataset = replace(dataset, description=f"{len(rows)} real Loghub HDFS lines, explicit synthetic change context, six working observability tools")
    specs = tuple(a.MetricSpec(id=name, source=a.EvaluatorSource(ref=metric.ref), output_type="bool" if name in CHECKS else "int",
        role="diagnostic", params={"fixture_dir": str(fixture.resolve())}) for name in (*CHECKS, "tool_receipts", "provider_iterations"))
    suite = a.EvalSuite(id="local-opensre-evidence-contract", version="2-process-identity", metrics=specs,
        rubric=a.Rubric(terms=tuple(a.ScoreTerm(metric=name, points=12.5) for name in CHECKS)),
        acceptance=a.AcceptanceRule(all_of=tuple(a.Threshold(metric=name, op="==", value=True) for name in CHECKS)),
        summaries=tuple(a.SummarySpec(id=label, metric=name, reducer=reducer) for label, name, reducer in (
            ("capture_contract_score", "quality.score", "mean"), ("native_tool_receipts", "tool_receipts", "sum"),
            ("provider_iterations", "provider_iterations", "sum"), ("elapsed_seconds", "run.duration_s", "mean"),
            ("input_tokens", "run.input_tokens", "sum"), ("output_tokens", "run.output_tokens", "sum"), ("cost_usd", "run.cost_usd", "sum"))))
    return a.Study(id="OpenSRE + Codex local HDFS investigation", project_id="opensre-local", dataset=dataset,
        question="Can Agent Eval Flow execute a real iterative incident investigation and preserve usable outputs and source-linked evidence?",
        candidates={candidate.id: candidate}, suite=suite,
        execution=a.ExecutionPolicy(budget=a.Budget(wall_time_s=wall_time_s), infrastructure_retries=0)), metric


def write_overview(result, output, *, fixture=FIXTURE):
    run = result.runs.runs[0]
    score, data = result.explain(run.id).score, inspect_capture(run, fixture=fixture)
    resources, elapsed = run.resources(), run.duration_s()
    timeline = []
    for index, row in enumerate(data["audit"]):
        start = next((item for item in data["trace"] if item["event"].get("tool_call_id") == row["tool_call_id"]
            and item["loop_id"] == row["loop_id"] and item["event"]["type"] == "tool_execution_start"), {})
        timeline.append(f'<article><h3>{index + 1}. {escape(row["tool_name"])}</h3><p>Iteration {start.get("event", {}).get("iteration", "unknown")} · receipt <code>{escape(row["result"]["receipt_id"])}</code></p>'
            '<details><summary>Actual tool input</summary><pre>' + escape(json.dumps(row["arguments"], indent=2, ensure_ascii=False)) + '</pre></details>'
            '<details><summary>Actual tool result</summary><pre>' + escape(json.dumps(row["result"], indent=2, ensure_ascii=False)) + '</pre></details></article>')
    contributions = ''.join(f'<tr><td>{escape(CHECKS[item.metric])}'
        + (f'<p class="failure">{escape(data["failures"][item.metric])}</p>' if item.metric in data["failures"] else '')
        + f'</td><td>{item.earned} / {item.possible}</td></tr>' for item in score.contributions)
    report = data["report"]
    page = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenSRE + Codex · local E2E</title><style>body{{font:16px/1.6 system-ui;margin:40px auto;max-width:1100px;padding:0 24px;color:#182230;background:#f4f6f8}}h1{{font-size:38px;line-height:1.15}}.flow,article,section{{background:white;border:1px solid #d9e1e8;border-radius:12px;padding:22px;margin:18px 0}}.flow{{font-weight:650;color:#174b6b}}.stats{{display:flex;gap:24px;flex-wrap:wrap}}.stats strong{{font-size:30px;display:block}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f8fa;padding:16px;font-size:12px}}table{{width:100%;border-collapse:collapse}}td{{padding:10px;border-bottom:1px solid #e6edf2}}summary,a{{cursor:pointer;color:#145d88}}small{{color:#536577}}.failure{{color:#9c302b}}</style>
<p>AGENT EVAL FLOW · REAL LOCAL RUN</p><h1>OpenSRE + Codex<br>HDFS incident investigation</h1>
<p>One task, one configured native harness. OpenSRE chooses the observability tools and follow-up calls. The 100-point score checks integration behavior and usable evidence. It does not judge the incident diagnosis.</p>
<div class="flow">2,000 downloaded HDFS log lines → incident snapshot → paginated search + metrics + topology + change history → cited investigation report → Agent Eval Flow</div>
<section class="stats"><div><strong>{score.score.value}</strong>Capture contract / 100</div><div><strong>{escape(run.status)}</strong>Native run</div><div><strong>{data['iterations']}</strong>Provider iterations</div><div><strong>{len(data['audit'])}</strong>Tool receipts</div><div><strong>{round(elapsed.value, 1) if elapsed.value is not None else 'unknown'}</strong>Seconds</div></section>
<p>Input tokens: {resources.input_tokens.value if resources.input_tokens.value is not None else 'unknown'} · Output tokens: {resources.output_tokens.value if resources.output_tokens.value is not None else 'unknown'} · Dollar charge: {resources.cost_usd.value if resources.cost_usd.value is not None else 'unknown'}. Native usage receipts do not establish a bill.</p>
<p><a href="report.html">Full library report</a> · <a href="evidence.zip">Download all evidence and source logs</a> · <a href="summary.json">Machine-readable summary</a> · <a href="regraded-report.html">Same evidence, changed weights</a></p>
<section><h2>Delivered incident report</h2><p>{escape(report.get('summary', 'No structured report was delivered.'))}</p><details><summary>Complete structured report with citations and limitations</summary><pre>{escape(json.dumps(report, indent=2, ensure_ascii=False))}</pre></details><p>The HDFS logs are a historical sample. Change tickets are explicitly synthetic exercise context. Root-cause claims remain hypotheses for human review.</p></section>
<section><h2>Why this score</h2><table>{contributions}</table><p>Acceptance: {score.acceptance}. Each check has 12.5 points; the saved evaluation retains the exact rules and evidence references.</p></section>
<h2>What the agent actually did</h2>{''.join(timeline)}
<section><h2>Native final answer</h2><pre>{escape(json.dumps(plain(run.output), indent=2, ensure_ascii=False))}</pre></section>
<p><small>Invocation {escape(invocation if (invocation := run.native_refs.get('invocation_id')) else 'unavailable')}. Every displayed value comes from the saved EvaluationResult. No external scripts or assets.</small></p></html>'''
    path = output / "overview.html"
    path.write_text(page, encoding="utf-8")
    return path


def save_outputs(study, result, metric, output, *, fixture=FIXTURE):
    result.validate().raise_for_errors()
    result.save(output / "result")
    loaded = a.EvaluationResult.load(output / "result")
    loaded.validate().raise_for_errors()
    _require(loaded.id == result.id and plain(loaded.runs) == plain(result.runs), "Persisting the result changed its capture")
    loaded.report(output / "report.html")
    weights = {name: 20.0 if name in {"tool_receipts_preserved", "native_trace_mapping"} else 10.0 for name in CHECKS}
    regrade_study = replace(study, suite=replace(study.suite, version="2-trace-priority",
        rubric=a.Rubric(terms=tuple(a.ScoreTerm(metric=name, points=points) for name, points in weights.items()))))
    regraded = a.EvaluationPipeline(study=regrade_study, evaluators={metric.ref.name: metric}).eval(runs=loaded.runs)
    regraded.save(output / "regraded")
    regraded.report(output / "regraded-report.html")
    run = loaded.runs.runs[0]
    if "incident.bundle" in run.artifacts:
        (output / "evidence.zip").write_bytes(artifact_bytes(run.artifacts["incident.bundle"]))
    overview = write_overview(loaded, output, fixture=fixture)
    data, score = inspect_capture(run, fixture=fixture), loaded.explain(run.id).score
    summary = {"overview": str(overview), "report": str(output / "report.html"), "saved_result": str(output / "result"),
        "invocation_id": run.native_refs.get("invocation_id"), "run_status": run.status, "capture_error": plain(run.error),
        "capture_contract_score": score.score.value, "capture_acceptance": score.acceptance,
        "score_is": "Structural integration and usable retained evidence; not root-cause accuracy", "checks": data["checks"], "check_failures": data["failures"],
        "metrics": {row.summary_id: {"value": plain(row.value.value), "status": row.value.status, "reason": row.value.reason} for row in loaded.summary()},
        "native_event_count": len(data["trace"]), "tool_types": sorted({row["tool_name"] for row in data["audit"]}),
        "source_log_lines": 2000, "planned_agent_tasks": len(study.plan().assignments), "saved_result_round_trip": True,
        "regraded_same_capture": regraded.runs.id == loaded.runs.id, "new_agent_invocations_during_regrade": 0,
        "reweighted_score": regraded.explain(run.id).score.score.value}
    (output / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="Prepared local runtime JSON; credentials stay in the runtime")
    parser.add_argument("--output", type=Path, default=Path("demo-output/opensre-local"))
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--wall-time-s", type=int, default=1200)
    args = parser.parse_args()
    if args.wall_time_s < 1:
        parser.error("Wall-time seconds must be positive")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    config.setdefault("backend_ref", {"name": "opensre-local", "revision": "aef-0.4.0"})
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / "result").exists():
        parser.error("Choose a fresh output directory to preserve the previous run")
    study, metric = build_study(config, fixture=args.fixture.resolve(), wall_time_s=args.wall_time_s)
    study.save(output / "study")
    backend = make_backend(config, workspace=output / "workspace")
    print(json.dumps({"starting": "Real native OpenSRE + Codex investigation", "output": str(output), "task_count": 1,
        "source_log_lines": 2000, "available_tool_types": len(TOOL_NAMES), "timeout_seconds": args.wall_time_s}), flush=True)
    result = a.EvaluationPipeline(study=study, backends={backend.ref.name: backend}, evaluators={metric.ref.name: metric}).eval()
    summary = save_outputs(study, result, metric, output, fixture=args.fixture.resolve())
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str), flush=True)
    return 0 if summary["capture_acceptance"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
