"""Test-owned backend and metric plugins. All library behavior uses the real API.

No fake Study, RunSet, EvalSuite, serializer, report or ranking implementation
belongs here. This file only supplies inputs and public extension protocols.
"""
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import sys


TOY = Path(__file__).parent / "fixtures" / "toy"


def plain(value):
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(item) for item in value]
    return value


def artifact(api, path, media_type="application/json"):
    path = Path(path).resolve()
    return api.ArtifactRef(uri=str(path), media_type=media_type,
                           sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def observed(api, value, evidence=()):
    return api.Observation(value=value, status="observed", reason="Owned E2E fixture observation", evidence=evidence)


def zero_resources(api):
    return api.Resources(cost_usd=observed(api, Decimal("0")), cost_scope=("model",),
                         input_tokens=observed(api, 0), output_tokens=observed(api, 0),
                         human_minutes=observed(api, 0.0))


class ScriptedBackend:
    """Deterministic subprocess backend; never substitutes for a live profile."""

    def __init__(self, api, root):
        self.api, self.root = api, Path(root)
        self.calls = []
        self.ref = api.VersionRef(name="e2e.scripted", revision="fixture-v1")

    def capabilities(self):
        return self.api.BackendCapabilities(wall_time_limit=True, token_limit=True,
                                             cost_limit=True, reset_state=True)

    def run(self, request, *, recorder):
        a = self.api
        self.calls.append(request)
        work = self.root / hashlib.sha256(request.run_id.encode()).hexdigest()
        work.mkdir(parents=True, exist_ok=False)
        task = plain(request.input.tables["units"][0])
        payload = {"task": task, "mode": request.candidate.settings.get("toy_mode", "plain"),
                   "variant": request.candidate.id}
        for component in request.candidate.components.values():
            if component.kind in ("skill", "tool") and component.content is not None:
                payload[component.kind + "_path"] = component.content.uri
        started = datetime.now(timezone.utc)
        try:
            process = subprocess.run(
                [sys.executable, str(TOY / "toy_agent.py")], input=json.dumps(payload),
                capture_output=True, text=True, timeout=request.policy.budget.wall_time_s,
            )
            stdout, stderr = process.stdout, process.stderr
            status = "completed" if process.returncode == 0 else "agent_error"
            code = str(process.returncode)
        except subprocess.TimeoutExpired:
            # subprocess.run kills and waits for this single-process fixture.
            stdout, stderr, status, code = "", "fixture process killed and reaped", "timed_out", "timeout"
        ended = datetime.now(timezone.utc)
        (work / "stdout.json").write_text(stdout, encoding="utf-8")
        (work / "stderr.txt").write_text(stderr, encoding="utf-8")
        artifacts = {"native.stdout": artifact(a, work / "stdout.json"),
                     "native.stderr": artifact(a, work / "stderr.txt", "text/plain")}
        source = a.EvidenceRef(artifact=artifacts["native.stdout"], description="Actual fixture process output")
        native = json.loads(stdout) if status == "completed" else {"output": None, "events": []}
        error = None if status == "completed" else a.ErrorRecord(code=code, message=stderr)
        execution_id = request.run_id + "/main"
        execution = a.Execution(
            id=execution_id, slot="main", retry_index=0, parent_id=None, status=status,
            started_at=started, ended_at=ended, resources=zero_resources(a), role="main",
            effective_config=observed(a, {"variant": request.candidate.id,
                                          "settings": plain(request.candidate.settings)}), error=error,
        )
        events = tuple(a.Event(id=f"{execution_id}/{index}", execution_id=execution_id,
                              kind=row["kind"], at=ended, fields=row, outputs=(source,))
                       for index, row in enumerate(native["events"]))
        for name, reference in artifacts.items():
            recorder.record_artifact(name, reference)
        recorder.record_execution(execution)
        for event in events:
            recorder.record_event(event)
        return a.Run(
            id=request.run_id, assignment_id=request.assignment.id, status=status,
            cost_scope=request.policy.cost_scope, output=native["output"], artifacts=artifacts,
            output_state="available" if status == "completed" else "unknown",
            executions=(execution,), started_at=started, ended_at=ended,
            environment=observed(a, {"scopes": {"workspace": {"namespace": str(work),
                "expected_state": "empty", "observed_state": "empty", "reset_method": "new-directory"}}}),
            execution_inventory_complete=observed(a, True), events=events, error=error,
            output_sources=(execution_id,) if native["output"] is not None else (),
        )


class WiringMetric:
    """Known values test dispatch, dependencies and evidence, not metric science."""

    def __init__(self, api):
        self.api = api
        self.ref = api.VersionRef(name="e2e.wiring", revision="fixture-v1")
        self.calls = Counter()

    def compute(self, spec, context, run):
        self.calls[(run.id, spec.id)] += 1
        if spec.depends_on:
            assert set(context.measurements) == set(spec.depends_on)
            value = context.measurements[spec.depends_on[0]].value + spec.params["add"]
        else:
            value = run.output.get("probe_value", 101) if isinstance(run.output, Mapping) else 101
        evidence = tuple(self.api.EvidenceRef(artifact=ref, description="Raw integration output")
                         for ref in run.artifacts.values())
        task = self.api.Measurement(run_id=run.id, metric=spec.id, value=value, status="ok",
                                   basis="observed", reason="Injected fixture value; not a quality judgment",
                                   evidence=evidence)
        detail = self.api.Measurement(run_id=run.id, metric=spec.id, value=value, status="ok",
                                     basis="observed", reason="Keyed diagnostic transport",
                                     key={"row": 0}, evidence=evidence)
        return self.api.MetricOutput(task=task, details=(detail,), evaluation_resources=zero_resources(self.api))


class WiringReducer:
    def __init__(self, api):
        self.api = api
        self.ref = api.VersionRef(name="e2e.reducer", revision="fixture-v1")
        self.calls = 0

    def reduce(self, spec, *, candidate_id, assignments, runs, measurements, task_scores):
        self.calls += 1
        assert {run.assignment_id for run in runs} == {row.id for row in assignments}
        assert all(row.candidate_id == candidate_id for row in assignments)
        assert all(row.unit and row.repetition >= 0 for row in assignments)
        values = [m.value for m in measurements if m.metric == spec.metric and m.key is None]
        assert len(values) == len(runs)
        value = sum(values) + spec.params["offset"]
        return self.api.CandidateSummary(
            candidate_id=candidate_id, summary_id=spec.id, value=observed(self.api, value),
            planned=len(runs), observed=len(runs), missing=0, errors=0, not_applicable=0,
            included_run_ids=tuple(run.id for run in runs), exclusions={}, reason="Custom reducer wiring probe",
        )


def make_study(api, *, project_id, backend_ref, units, records=None, input_columns=None,
               components=None, settings=None, repetitions=1, wall_time_s=180):
    dataset = api.EvalDataset.from_records(
        id=project_id + "/data", units=units, unit_key=("task_id",), records=records,
        input_columns=input_columns or {"units": ("task_id", "text")},
        references={"private_oracle": api.DataTable(
            rows=tuple({"task_id": row["task_id"], "private_marker": "EVALUATOR_ONLY"} for row in units),
            key=("task_id",), schema={"task_id": "str", "private_marker": "str"})},
    )
    base = api.Candidate(id="A", backend=api.VersionRef(**backend_ref),
                         components=components or {}, settings={**(settings or {}), "e2e_variant": "A"})
    challenger = base.derive(id="B", settings={"e2e_variant": "B"})
    metric, reducer = WiringMetric(api), WiringReducer(api)
    suite = api.EvalSuite(
        id=project_id + "/suite", version="wiring-v1", rubric=None,
        metrics=(api.MetricSpec(id="wiring", source=api.EvaluatorSource(ref=metric.ref), output_type="int", role="diagnostic"),
                 api.MetricSpec(id="derived", source=api.EvaluatorSource(ref=metric.ref), output_type="int", role="diagnostic",
                                depends_on=("wiring",), params={"add": 7})),
        acceptance=api.AcceptanceRule(all_of=(api.Threshold(metric="wiring", op=">", value=0),)),
        summaries=(api.SummarySpec(id="mean_probe", metric="wiring", reducer="mean"),
                   api.SummarySpec(id="custom_probe", metric="derived", reducer=reducer.ref, params={"offset": 3})),
    )
    study = api.Study(id=project_id + "/study", project_id=project_id,
                      question="Can a caller use the output objects end to end?", dataset=dataset,
                      candidates={"A": base, "B": challenger}, suite=suite,
                      execution=api.ExecutionPolicy(budget=api.Budget(wall_time_s=wall_time_s),
                                                   repetitions=repetitions, infrastructure_retries=0))
    return study, {metric.ref.name: metric}, {reducer.ref.name: reducer}


def assert_usable_result(api, study, result, *, root):
    """Use the public objects, not just isinstance/shape checks."""
    assert isinstance(result, api.EvaluationResult)
    result.runs.validate().raise_for_errors()
    plan = study.plan()
    assert {run.assignment_id for run in result.runs.runs} == {row.id for row in plan.assignments}
    assert len({run.id for run in result.runs.runs}) == len(plan.assignments)
    assert len(result.runs.rows("runs")) == len(plan.assignments)
    assert result.runs.coverage().planned == len(plan.assignments)
    assert result.suite_fingerprint == study.suite.fingerprint()
    assert len(result.summary()) == len(study.candidates) * len(study.suite.summaries)
    for run in result.runs.runs:
        assert result.runs.get(run.id).assignment_id == run.assignment_id
        explanation = result.explain(run.id)
        assert explanation.run_id == run.id and explanation.score.run_id == run.id
        assert explanation.measurements and explanation.evidence
        task_values = {m.metric: m.value for m in result.measurements if m.run_id == run.id and m.key is None}
        assert task_values["derived"] == task_values["wiring"] + 7
        assert any(m.run_id == run.id and m.key == {"row": 0} for m in result.measurements)
    for candidate in study.candidates:
        assert result.runs.for_candidate(candidate)
    if len(study.candidates) >= 2:
        comparison = result.compare("A", "B", metrics=("mean_probe",))
        assert comparison.baseline_id == "A" and comparison.challenger_id == "B"
        assert {row.metric for row in comparison.rows} == {"mean_probe"}
        for direction in ("minimize", "maximize"):
            selection = result.select(api.SelectionPolicy(id=direction, objectives=(
                api.ObjectiveTerm(metric="mean_probe", direction=direction),)))
            assert selection.result_id == result.id and selection.rows
            assert selection.selected_ids, "Known fixture objectives must produce a choice or tie"
            assert set(selection.selected_ids) <= study.candidates.keys()
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    study.save(root / "study")
    assert api.Study.load(root / "study").fingerprint() == study.fingerprint()
    result.runs.save(root / "runs")
    loaded_runs = api.RunSet.load(root / "runs")
    assert {run.id for run in loaded_runs.runs} == {run.id for run in result.runs.runs}
    result.save(root / "result")
    loaded = api.EvaluationResult.load(root / "result")
    assert loaded.id == result.id and loaded.suite_fingerprint == result.suite_fingerprint
    assert [plain(run.output) for run in loaded.runs.runs] == [plain(run.output) for run in result.runs.runs]
    assert {(m.run_id, m.metric, repr(plain(m.key)), m.value) for m in loaded.measurements} == {
        (m.run_id, m.metric, repr(plain(m.key)), m.value) for m in result.measurements}
    for run in loaded.runs.runs:
        assert loaded.explain(run.id).evidence
        original = result.runs.get(run.id)
        if isinstance(original.resources().cost_usd.value, Decimal):
            assert isinstance(run.resources().cost_usd.value, Decimal)
        assert run.resources().cost_usd == original.resources().cost_usd
    if len(study.candidates) >= 2:
        assert loaded.compare("A", "B", metrics=("mean_probe",)).rows
        assert loaded.select(api.SelectionPolicy(id="loaded", objectives=(
            api.ObjectiveTerm(metric="mean_probe", direction="maximize"),))).rows
    report = loaded.report(root / "report.html")
    assert report.is_file() and "<html" in report.read_text(encoding="utf-8").lower()
    return loaded
