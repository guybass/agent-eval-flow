"""Toolscore boundary, missingness, lifecycle, and portable saved reporting."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from html.parser import HTMLParser
from urllib.parse import unquote, urlparse

import pytest

import agent_eval_flow as a
from agent_eval_flow.adapters.tool_trace import claude_stream_tool_trace
from agent_eval_flow.evaluation import toolscore as integration
from agent_eval_flow.objects.identity import plain
from agent_eval_flow.objects.tool_trace import ToolRequest, ToolTrace, decode_tool_trace
from agent_eval_flow.objects.values import observed, unknown, zero_resources
from agent_eval_flow.storage.artifacts import ArtifactCache


HAS_TOOLSCORE = importlib.util.find_spec("toolscore") is not None
requires_toolscore = pytest.mark.skipif(not HAS_TOOLSCORE, reason="Install the toolscore extra for upstream scoring tests")


@pytest.fixture
def scenario(tmp_path):
    cache = ArtifactCache(tmp_path / "evidence")
    artifact = cache.write_bytes("capture", b"complete synthetic evidence", "text/plain")
    evidence = a.EvidenceRef(artifact=artifact, description="Fixture coverage and calls")
    now = datetime(2026, 9, 23, tzinfo=timezone.utc)
    execution = a.Execution(id="main", slot="main", retry_index=0, parent_id=None, status="completed",
        started_at=now, ended_at=now, effective_config=unknown("fixture"), resources=zero_resources())
    run = a.Run(id="run", assignment_id="assignment", status="completed", cost_scope=("model",), output=None,
        output_state="unknown", artifacts={"capture": artifact}, executions=(execution,), started_at=now,
        ended_at=now, environment=unknown("fixture"), execution_inventory_complete=observed(True))
    evaluator = integration.ToolscoreEvaluator(artifacts=cache)

    def evaluate(expected, actual, *, coverage=True, missing_trace=False, missing_contract=False,
                 metric="score", alternatives=(), **settings):
        calls = tuple(ToolRequest(call_id=f"call-{i}", execution_id="main", tool=call["tool"],
            arguments=call.get("args"), evidence=(evidence,)) for i, call in enumerate(actual))
        trace = ToolTrace(collector=a.VersionRef(name="fixture", revision="1"), boundary="requests", scope="main fixture",
            coverage=unknown("Not captured") if coverage is None else observed(coverage, "Fixture coverage", (evidence,)),
            calls=calls)
        saved = replace(run, events=() if missing_trace else (trace.to_event(id="trace", execution_id="main"),))
        context = SimpleNamespace(unit={"task_id": "task"}, evaluation_cost_scope=("model",), references={} if missing_contract else {
            "tool_expectations": ({"contract": {"id": "test", "revision": "1", "calls": expected,
                "alternatives": list(alternatives)}},)})
        spec = a.MetricSpec(id="tools." + metric, source=a.EvaluatorSource(ref=evaluator.ref), output_type="float",
            role="diagnostic", params={"boundary": "requests", "metric": metric, **settings})
        output = evaluator.compute(spec, context, saved)
        ref = next(ref for ref in output.task.evidence if ref.artifact.media_type == integration.RECEIPT_TYPE)
        receipt = json.loads(Path(ref.artifact.uri).read_text(encoding="utf-8"))
        return output.task, receipt, saved

    return evaluate, evidence, cache


@requires_toolscore
@pytest.mark.parametrize("actual,metric,value", [
    ([{"tool": "search", "args": {"q": "x"}}], "score", 1.0),
    ([{"tool": "wrong", "args": {"q": "x"}}], "selection_accuracy", 0.0),
    ([{"tool": "search", "args": {"q": "wrong"}}], "argument_f1", 0.0),
    ([], "required_call_recall", 0.0),
    ([{"tool": "search", "args": {"q": "x"}}, {"tool": "extra", "args": {}}], "selection_accuracy", 0.5),
])
def test_real_upstream_metrics_for_known_traces(scenario, actual, metric, value):
    row, receipt, _ = scenario[0]([{"tool": "search", "args": {"q": "x"}}], actual, metric=metric)
    assert row.status == "ok" and row.value == pytest.approx(value)
    assert receipt["toolscore_version"] == integration.SUPPORTED_VERSION
    assert receipt["settings"]["strict"] is True
    assert receipt["contract_fingerprint"] and receipt["run_fingerprint"]
    assert receipt["trace"]["calls"] == [] or receipt["trace"]["calls"][0]["completion"] == "unknown"


@pytest.mark.parametrize("options", [{"missing_trace": True}, {"missing_contract": True},
                                      {"coverage": False}, {"coverage": None}])
def test_missing_evidence_never_scores_as_empty(scenario, monkeypatch, options):
    monkeypatch.setattr(integration, "_load_toolscore", lambda: pytest.fail("Incomplete evidence must not be scored"))
    row, receipt, _ = scenario[0]([], [], **options)
    assert row.status == "missing" and row.value is None and row.basis is None
    assert receipt["status"] == "missing" and receipt["metrics"] == {}


@pytest.mark.parametrize("arguments", [None, "{broken json", ["not", "an", "object"]])
def test_malformed_arguments_preserved_in_error_receipt(scenario, arguments):
    row, receipt, _ = scenario[0]([{"tool": "search"}], [{"tool": "search", "args": arguments}])
    assert row.status == "error" and row.value is None
    assert receipt["trace"]["calls"][0]["arguments"] == arguments


def test_unavailable_dependency_is_explicit_error(scenario, monkeypatch):
    def unavailable():
        raise integration.metadata.PackageNotFoundError("tool-scorer")
    monkeypatch.setattr(integration.metadata, "version", lambda _: unavailable())
    row, receipt, _ = scenario[0]([], [])
    assert row.status == "error" and "Install agent-eval-flow[toolscore]" in row.reason
    assert receipt["metrics"] == {}
    assert receipt["toolscore_version"] is None


def test_unsupported_dependency_version_is_explicit_error(scenario, monkeypatch):
    monkeypatch.setattr(integration.metadata, "version", lambda _: "99.0")
    row, _, _ = scenario[0]([], [])
    assert row.status == "error" and "Unsupported" in row.reason


@requires_toolscore
def test_no_tool_contract_is_successful_invocation_without_misleading_composite(scenario):
    row, receipt, _ = scenario[0]([], [])
    assert row.status == "not_applicable" and row.value is None
    assert receipt["metrics"]["invocation_accuracy"]["value"] == 1.0
    assert receipt["alternatives"][0]["raw_score"] == pytest.approx(0.7)


@requires_toolscore
def test_repeated_calls_require_multiplicity_and_retries_are_retained(scenario):
    call = {"tool": "read", "args": {"id": 1}}
    row, receipt, _ = scenario[0]([call, call], [call], metric="required_call_recall")
    assert row.value == 0.5
    assert receipt["metrics"]["selection_accuracy"]["value"] == 1.0
    _, receipt, _ = scenario[0]([call], [call, call])
    assert len(receipt["actual"]) == 2
    assert receipt["metrics"]["redundant_rate"]["value"] > 0


@requires_toolscore
def test_parallel_order_and_reviewed_alternatives_are_explicit(scenario):
    calls = [{"tool": "a", "args": {}}, {"tool": "b", "args": {}}]
    ordered, _, _ = scenario[0](calls, list(reversed(calls)))
    unordered, receipt, _ = scenario[0](calls, list(reversed(calls)), ordering="unordered")
    assert ordered.value < unordered.value and unordered.value == pytest.approx(1.0)
    assert receipt["settings"]["weights"]["sequence_accuracy"] == 0
    assert receipt["metrics"]["sequence_accuracy"]["status"] == "not_applicable"
    _, receipt, _ = scenario[0](calls, [calls[1]], alternatives=([calls[1]],))
    assert receipt["selected_alternative"] == 1 and len(receipt["alternatives"]) == 2


@requires_toolscore
def test_arguments_omitted_empty_and_strict_types_are_distinct(scenario):
    actual = [{"tool": "read", "args": {"count": 1.0}}]
    row, _, _ = scenario[0]([{"tool": "read"}], actual, metric="argument_f1")
    assert row.value == 1.0
    row, _, _ = scenario[0]([{"tool": "read", "args": {}}], actual, metric="argument_f1")
    assert row.value == 0.0
    row, _, _ = scenario[0]([{"tool": "read", "args": {"count": 1}}], actual, metric="argument_f1")
    assert row.value == 0.0
    row, _, _ = scenario[0]([{"tool": "read", "args": {"count": 1}}], actual, metric="argument_f1", strict=False)
    assert row.value == 1.0


def project_stream(cache, records, **kwargs):
    data = ("\n".join(json.dumps(row) for row in records) + "\n").encode()
    artifact = cache.write_bytes("stream", data, "application/x-ndjson")
    return claude_stream_tool_trace(data, artifact=artifact, execution_id="main", boundary="requests", **kwargs)


def test_projection_retains_request_order_unfinished_and_failed_calls(scenario):
    trace = project_stream(scenario[2], [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "a", "name": "read", "input": {"id": 1}},
            {"type": "tool_use", "id": "b", "name": "read", "input": {"id": 2}},
            {"type": "tool_use", "id": "c", "name": "write", "input": {}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "b", "content": "bad", "is_error": True},
            {"type": "tool_result", "tool_use_id": "a", "content": "ok"}]}}])
    assert [call.call_id for call in trace.calls] == ["a", "b", "c"]
    assert [call.completion for call in trace.calls] == ["completed", "error", "unknown"]
    assert trace.coverage.status == "unknown"
    event = trace.to_event(id="trace", execution_id="main")
    assert decode_tool_trace(replace(event, fields=plain(event.fields))) == trace
    with pytest.raises(ValueError, match="detached"):
        decode_tool_trace(replace(event, inputs=(), source=None))


@pytest.mark.parametrize("blocks,match", [
    ([{"type": "tool_result", "tool_use_id": "absent"}], "no retained request"),
    ([{"type": "tool_use", "id": "a", "name": "x", "input": {}},
      {"type": "tool_use", "id": "a", "name": "x", "input": {}}], "duplicate"),
])
def test_projection_rejects_orphans_and_duplicates(scenario, blocks, match):
    with pytest.raises(ValueError, match=match):
        project_stream(scenario[2], [{"type": "assistant", "message": {"content": blocks}}])


def test_duplicate_boundaries_and_foreign_execution_are_errors(scenario):
    evaluate, _, cache = scenario
    _, _, run = evaluate([], [], coverage=None)
    context = SimpleNamespace(unit={"task_id": "task"}, evaluation_cost_scope=("model",), references={})
    evaluator = integration.ToolscoreEvaluator(artifacts=cache)
    spec = integration.toolscore_metrics(boundary="requests")[0]
    duplicate = replace(run, events=(run.events[0], replace(run.events[0], id="duplicate")))
    assert "Ambiguous" in evaluator.compute(spec, context, duplicate).task.reason
    trace = decode_tool_trace(run.events[0])
    call = ToolRequest(call_id="x", execution_id="unretained-child", tool="read", arguments={}, evidence=(scenario[1],))
    event = replace(trace, calls=(call,)).to_event(id="trace", execution_id="main")
    assert "absent execution" in evaluator.compute(spec, context, replace(run, events=(event,))).task.reason


@pytest.mark.parametrize("settings", [
    {"weights": {"argument_f1": 1.0}},
    {"weights": {key: 0.0 for key in integration.DEFAULT_WEIGHTS}},
    {"boundary": "unused"},
])
def test_invalid_settings_cannot_silently_use_defaults(scenario, settings):
    row, _, _ = scenario[0]([], [], **settings)
    assert row.status in ("error", "missing") and row.value is None


def load_example():
    path = Path(__file__).resolve().parents[2] / "examples/toolscore_review.py"
    spec = importlib.util.spec_from_file_location("toolscore_example", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@requires_toolscore
def test_pipeline_persistence_portable_reports_and_assessment(tmp_path, monkeypatch):
    study, result = load_example().build_example(tmp_path / "original")
    assert not any(row.status == "error" for row in result.measurements)
    result.save(tmp_path / "saved")
    loaded = a.EvaluationResult.load(tmp_path / "saved")
    before = loaded.fingerprint()
    monkeypatch.setattr(integration, "_load_toolscore", lambda: pytest.fail("Rendering must not score"))
    report = loaded.report(tmp_path / "bundle/report.html", tools=True)
    html = report.read_text(encoding="utf-8")
    tools = report.with_name("tools.html").read_text(encoding="utf-8")
    data = json.loads(report.with_name("tools.json").read_text(encoding="utf-8"))
    assert len(data["runs"]) == 4
    assert all(len(row["receipts"]) == 1 for row in data["runs"])
    assert 'href="tools.html"' in html and 'href="report.html#run-0"' in tools
    assert "Completion unknown" in tools and "No calls expected or observed" in tools
    assert "Tool latency: unknown" in tools and "Toolscore 1.8.1" in tools
    assert all(entry["bundled_uri"] for entry in data["evidence"])
    assert loaded.fingerprint() == before
    from agent_eval_flow.evaluation.assessment_projection import wrap_behavior_result
    assessment = wrap_behavior_result(loaded, plan=a.AssessmentPlan(id="tools-assessment",
        project_id=study.project_id, candidates=study.candidates, behavior=study))
    assessment.report(tmp_path / "assessment/report.html", tools=True)
    assert 'href="tools.html"' in (tmp_path / "assessment/report.html").read_text(encoding="utf-8")
    moved = tmp_path / "moved"
    shutil.copytree(report.parent, moved)
    for entry in data["evidence"]:
        assert hashlib.sha256((moved / entry["bundled_uri"]).read_bytes()).hexdigest() == entry["sha256"]
    assert_bundle_links(moved)


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs, self.ids = [], set()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "href" in attributes:
            self.hrefs.append(attributes["href"])
        if "id" in attributes:
            self.ids.add(attributes["id"])


def assert_bundle_links(root):
    for source in root.glob("*.html"):
        page = Links()
        page.feed(source.read_text(encoding="utf-8"))
        for href in page.hrefs:
            parsed = urlparse(href)
            assert not parsed.scheme and not parsed.netloc, href
            target = root / unquote(parsed.path) if parsed.path else source
            assert target.is_file(), href
            if parsed.fragment:
                destination = Links()
                destination.feed(target.read_text(encoding="utf-8"))
                assert parsed.fragment in destination.ids, href


@requires_toolscore
def test_tampered_receipts_are_visible_and_not_scored_in_report(tmp_path):
    _, result = load_example().build_example(tmp_path / "original")
    receipt = next(ref.artifact for row in result.measurements for ref in row.evidence
                   if ref.artifact.media_type == integration.RECEIPT_TYPE)
    Path(receipt.uri).write_text("{}", encoding="utf-8")
    result.report(tmp_path / "bundle/report.html", tools=True)
    html = (tmp_path / "bundle/tools.html").read_text(encoding="utf-8")
    assert "Receipt unavailable or invalid" in html and "differs from its recorded hash" in html
    data = json.loads((tmp_path / "bundle/tools.json").read_text(encoding="utf-8"))
    assert sum(row["error"] for row in data["summaries"]) == 1


@requires_toolscore
def test_tool_arguments_are_escaped_and_filename_links_are_encoded(tmp_path):
    example = load_example()
    study, result = example.build_example(tmp_path / "original")
    run = next(run for run in result.runs.runs if decode_tool_trace(run.events[0]).calls)
    trace = decode_tool_trace(run.events[0])
    payload = '<script id="injected">alert(1)</script>'
    call = replace(trace.calls[0], arguments={"city": payload})
    trace = replace(trace, calls=(call, *trace.calls[1:]))
    modified = replace(run, events=(trace.to_event(id=run.events[0].id, execution_id=run.executions[0].id),))
    capture = replace(result.runs, runs=tuple(modified if item.id == run.id else item for item in result.runs.runs))
    evaluator = integration.ToolscoreEvaluator(artifacts=ArtifactCache(tmp_path / "receipts"))
    outcome = example.FixtureOutcome()
    result = a.EvaluationPipeline(study=study, evaluators={evaluator.ref.name: evaluator,
        outcome.ref.name: outcome}).eval(runs=capture)
    result.report(tmp_path / "bundle/report #1.html", tools=True)
    html = (tmp_path / "bundle/report #1-tools.html").read_text(encoding="utf-8")
    assert payload not in html and "&lt;script" in html
    assert_bundle_links(tmp_path / "bundle")


@requires_toolscore
def test_missing_source_is_reported_without_fetching_or_losing_scores(tmp_path):
    _, result = load_example().build_example(tmp_path / "original")
    artifact = result.runs.runs[0].artifacts["stream"]
    Path(artifact.uri).unlink()
    result.report(tmp_path / "bundle/report.html", tools=True)
    data = json.loads((tmp_path / "bundle/tools.json").read_text(encoding="utf-8"))
    assert any(row["uri"] == artifact.uri and row["error"] for row in data["evidence"])
    assert data["runs"][0]["receipts"][0]["receipt"]["status"] == "ok"


def test_invalid_receipt_shapes_are_rejected(scenario):
    from agent_eval_flow.reporting.toolscore import _receipt
    _, _, run = scenario[0]([], [], missing_trace=True)
    artifact = scenario[2].write_bytes("broken-receipt", b"[]", integration.RECEIPT_TYPE)
    with pytest.raises(ValueError, match="Unsupported"):
        _receipt(artifact, run)


def test_optional_modules_import_without_toolscore():
    code = """
import sys
class BlockToolscore:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'toolscore' or fullname.startswith('toolscore.'):
            raise ImportError('Toolscore intentionally unavailable')
sys.meta_path.insert(0, BlockToolscore())
import agent_eval_flow
import agent_eval_flow.evaluation.toolscore
import agent_eval_flow.reporting.toolscore
assert 'toolscore' not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)
