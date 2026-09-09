"""Owned subprocess fixtures and immutable configuration adapter boundaries."""
from contextlib import asynccontextmanager
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import anyio
import pytest

from agent_eval_flow import objects as o
from agent_eval_flow.adapters import (
    CallbackSnapshotBinder, FileConfigurationCollector, HarnessEvalConfigurationEvaluator,
)
from agent_eval_flow.storage.artifacts import ArtifactCache


class Recorder:
    def __init__(self):
        self.artifacts, self.activities, self.captures = {}, [], []
    def record_artifact(self, name, artifact):
        self.artifacts[name] = artifact
    def record_activity(self, activity, *, final=False):
        self.activities.append(activity)
    def record_capture(self, capture, *, final=False):
        self.captures.append(capture)


def candidate():
    return o.Candidate(id="candidate", backend=o.VersionRef(name="fixture", revision="1"), components={})


async def collect(tmp_path, *, missing=False):
    live = tmp_path / "live"
    live.mkdir(exist_ok=True)
    (live / "AGENTS.md").write_bytes(b"Original instruction\n")
    cache = ArtifactCache(tmp_path / "cache")
    collector = FileConfigurationCollector(roots={"project": live}, artifacts=cache)
    files = [{"root": "project", "path": "AGENTS.md", "source_tool": "codex"}]
    if missing:
        files.append({"root": "project", "path": "missing.md"})
    spec = o.ConfigurationSpec(collector=collector.ref, params={"files": files})
    request = o.ConfigurationCollectRequest(id="collection", activity_id="collection-activity",
                                            candidate=candidate(), spec=spec)
    recorder = Recorder()
    capture = await collector.collect(request, recorder=recorder)
    return cache, capture, recorder, live


def test_collection_freezes_bytes_and_keeps_missing_source_coverage(tmp_path):
    async def exercise():
        cache, capture, recorder, live = await collect(tmp_path, missing=True)
        (live / "AGENTS.md").write_text("Changed live configuration", encoding="utf-8")
        entry = capture.snapshot.entries[0]
        assert Path(entry.artifact.uri).read_text() == "Original instruction\n"
        assert entry.artifact.sha256 == hashlib.sha256(b"Original instruction\n").hexdigest()
        assert capture.status == "partial"
        assert capture.snapshot.inventory_complete.value is False
        assert "missing.md" in capture.snapshot.omissions[0]
        assert recorder.captures == [capture]
        assert len(recorder.activities) == 1
        assert capture.activities[0].resources.cost_usd.status == "unknown"
        assert not cache.verify(entry.artifact).issues
    anyio.run(exercise)


@pytest.mark.parametrize("path", ["../outside.md", "C:/outside.md", "/outside.md"])
def test_file_selectors_reject_escaping_paths_before_collection(tmp_path, path):
    async def exercise():
        collector = FileConfigurationCollector(roots={"project": tmp_path}, artifacts=ArtifactCache(tmp_path / "cache"))
        request = o.ConfigurationCollectRequest(id="bad", activity_id="bad-activity", candidate=candidate(),
            spec=o.ConfigurationSpec(collector=collector.ref, params={"files": [{"root": "project", "path": path}]}))
        recorder = Recorder()
        with pytest.raises(o.ConfigurationError):
            await collector.collect(request, recorder=recorder)
        assert not recorder.artifacts
    anyio.run(exercise)


SCANNER = r'''
import json, pathlib, sys, time
variant, log = sys.argv[1:3]
args = sys.argv[3:]
with open(log, "a", encoding="utf-8") as out:
    out.write(json.dumps(args) + "\n")
if args == ["--version"]:
    print("harness-eval, version " + ("9.0.0" if variant == "wrong-version" else "7.15.0"))
    raise SystemExit(0)
if variant == "corrupt":
    print("not JSON")
    raise SystemExit(0)
if variant == "timeout":
    print("partial scanner output", flush=True)
    print("partial scanner diagnostic", file=sys.stderr, flush=True)
    time.sleep(30)
if args[0] == "harness-review":
    print(json.dumps({"setup":"fixture", "component_count":1, "rubric":[
        {"component":"AGENTS", "type":"claude_md", "issues":[
            {"category":"clarity", "description":"Unclear instruction", "evidence":"source text",
             "suggestion":"Clarify", "impact":"less ambiguity"}], "summary":"review", "verdict":"needs_work"}],
        "metadata":{"version":"7.15.0", "provider":args[args.index("--provider")+1],
                    "model":args[args.index("--model")+1]}}))
    raise SystemExit(0)
source = pathlib.Path(args[1]) / "AGENTS.md"
assert source.read_text(encoding="utf-8") == "Original instruction\n"
finding = {"rule":"test/overlap", "severity":"warning", "message":"Possible overlap",
           "file":str(source) if variant != "foreign" else str(source.parent.parent.parent / "foreign.md"), "line":1}
component = {"name":"AGENTS", "findings":[] if variant == "empty" else [finding]}
if variant != "empty":
    component["rules"] = [{"rule":"test/overlap", "result":"fail"}]
print(json.dumps({"setup":"fixture", "component_count":1, "findings":[],
    "inspection":{"summary":{"total":1,"errors":0,"warnings":0 if variant == "empty" else 1},
                  "instructions":[component]}, "metadata":{"version":"7.15.0"}}))
raise SystemExit(0 if variant == "empty" else 1)
'''


async def scan(tmp_path, *, variant="findings", params=None):
    cache, capture, _, live = await collect(tmp_path)
    (live / "AGENTS.md").write_text("Changed after snapshot", encoding="utf-8")
    script = tmp_path / "scanner.py"
    script.write_text(SCANNER, encoding="utf-8")
    log = tmp_path / "argv.jsonl"
    # The deadline includes interpreter startup and applies to --version too.
    # Give both invocations the normal fixture startup allowance; the timeout
    # fixture sleeps for 30 seconds only after flushing its report evidence.
    evaluator = HarnessEvalConfigurationEvaluator(command=(str(Path(sys.executable).resolve()), str(script), variant, str(log)),
        artifacts=cache, workspace_root=tmp_path / "scanner-work", timeout_s=10,
        environment={"GEMINI_API_KEY": "fixture-only-unused"})
    check = o.CheckSpec(id="inspection", evaluator=evaluator.ref, candidate_ids=("candidate",), params=params or {})
    subject = o.CandidateSubject(candidate_id="candidate", candidate_fingerprint=capture.candidate_fingerprint,
                                 snapshot_fingerprint=capture.snapshot.fingerprint)
    request = o.AssessmentRequest(id="assessment", activity_id="assessment-activity", check=check,
                                   subject=subject, capture=capture)
    recorder = Recorder()
    output = await evaluator.evaluate(request, recorder=recorder)
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return output, calls, recorder


def test_real_scanner_subprocess_retains_findings_exit_one_and_frozen_sources(tmp_path):
    async def exercise():
        output, calls, recorder = await scan(tmp_path)
        assert output.activity.status == "completed"
        assert output.assessment.status == "ok"
        finding = output.assessment.findings[0]
        assert finding.subject.entry_id is not None
        assert finding.rule.revision == "7.15.0"
        assert finding.basis == "inferred"
        assert len(finding.evidence) == 2
        assert output.coverage.inventory_complete.status == "unknown"
        assert output.coverage.rule_statuses
        assert calls[0] == ["--version"]
        assert calls[1][0] == "harness-lint"
        assert "--fix" not in calls[1] and "--rules-from-target" not in calls[1]
        assert all("harness-review" not in call for call in calls)
        assert len(recorder.activities) == 1
        settings = Path(output.artifacts["harness.settings"].uri).read_text()
        assert "fixture-only-unused" not in settings
        assert not any((tmp_path / "scanner-work").iterdir())
    anyio.run(exercise)


@pytest.mark.parametrize("variant", ["corrupt", "wrong-version", "timeout"])
def test_process_errors_preserve_raw_evidence_without_passing(tmp_path, variant):
    async def exercise():
        output, calls, recorder = await scan(tmp_path, variant=variant)
        assert output.activity.status == "error"
        assert output.assessment.conclusion == "unknown"
        assert output.coverage.status == "error"
        assert recorder.artifacts
        if variant == "wrong-version":
            assert len(calls) == 1
        if variant == "timeout":
            assert len(calls) == 2, "The version probe must finish before the report timeout is exercised"
            assert calls[0] == ["--version"]
            assert calls[1][0] == "harness-lint"
            version_receipt = json.loads(Path(output.artifacts["harness.version.process"].uri).read_text())
            assert version_receipt["exit_code"] == 0
            assert version_receipt["failure"] is None
            assert b"partial scanner output" in Path(output.artifacts["harness.report.stdout"].uri).read_bytes()
            assert b"partial scanner diagnostic" in Path(output.artifacts["harness.report.stderr"].uri).read_bytes()
            receipt = json.loads(Path(output.artifacts["harness.report.process"].uri).read_text())
            assert output.activity.error.code == "TimeoutError"
            assert receipt["failure"] == "TimeoutError"
            assert receipt["direct_child_stopped"] is True
            assert receipt["exit_code"] is not None
            assert receipt["descendants_stopped"] is None
            assert not any((tmp_path / "scanner-work").iterdir())
    anyio.run(exercise)


def test_no_findings_without_rule_inventory_remains_unknown(tmp_path):
    async def exercise():
        output, _, _ = await scan(tmp_path, variant="empty")
        assert output.activity.status == "completed"
        assert not output.assessment.findings
        assert output.assessment.conclusion == "unknown"
        assert output.coverage.inventory_complete.status == "unknown"
    anyio.run(exercise)


def test_explicit_semantic_review_and_unmapped_locations(tmp_path):
    async def exercise():
        review_dir = tmp_path / "review"
        review_dir.mkdir()
        output, calls, _ = await scan(review_dir, params={"mode":"review", "provider":"gemini", "model":"fixture-model"})
        assert output.activity.status == "completed"
        assert calls[1][0] == "harness-review"
        assert "fixture-model" in calls[1]
        assert output.assessment.findings[0].rule.name == "clarity"
        foreign_dir = tmp_path / "foreign"
        foreign_dir.mkdir()
        output, _, _ = await scan(foreign_dir, variant="foreign")
        assert output.assessment.findings[0].subject.entry_id is None
        assert output.assessment.findings[0].subject.mapping_issue
        assert len(output.assessment.findings[0].evidence) == 1
    anyio.run(exercise)


def run_request(cand):
    assignment = o.Assignment(id="assignment", candidate_id=cand.id,
        candidate_fingerprint=cand.fingerprint(), unit={"case":"one"}, repetition=0)
    return o.RunRequest(run_id="run", assignment=assignment, candidate=cand,
        input=o.AgentInput(unit={"case":"one"}, tables={}),
        policy=o.ExecutionPolicy(budget=o.Budget(wall_time_s=10.0)), environment=None)


def test_callback_binder_checks_revision_dispatch_identity_and_cleanup(tmp_path):
    async def exercise():
        _, capture, _, _ = await collect(tmp_path)
        request = run_request(candidate())
        bind = o.SnapshotBindRequest(id="bind", snapshots={"candidate":capture.snapshot},
            requirements={"candidate":"verified"}, run_request=request)
        events = []
        class Backend:
            ref = request.candidate.backend
            def capabilities(self):
                return o.BackendCapabilities(wall_time_limit=True, token_limit=False, cost_limit=False, reset_state=True)
            def run(self, actual, *, recorder):
                events.append("run")
                return "native-result"
        proof = o.EvidenceRef(artifact=capture.snapshot.entries[0].artifact, description="Fixture verification evidence")
        @asynccontextmanager
        async def prepare(actual):
            events.append("enter")
            try:
                yield SimpleNamespace(bindings=(o.SnapshotBinding(candidate_id="candidate",
                    candidate_fingerprint=capture.candidate_fingerprint, snapshot_fingerprint=capture.snapshot.fingerprint,
                    status="verified", reason="Explicit fixture proof", run_ids=("run",), evidence=(proof,)),),
                    backend=Backend(), job_backend=None)
            finally:
                events.append("cleanup")
        binder = CallbackSnapshotBinder(ref=o.VersionRef(name="fixture-binder", revision="1"),
            capabilities=o.SnapshotCapabilities(backends=(candidate().backend,), scopes=("project",),
                                                direct=True, native=False, verified=True), prepare=prepare)
        session = await binder.prepare(bind)
        assert not events
        async with session:
            assert session.backend.run(request, recorder=Recorder()) == "native-result"
            with pytest.raises(o.ConfigurationError):
                session.backend.run(replace(request, run_id="different"), recorder=Recorder())
        assert events == ["enter", "run", "cleanup"]
        with pytest.raises(o.ConfigurationError):
            session.backend.run(request, recorder=Recorder())
        unsupported = CallbackSnapshotBinder(ref=binder.ref,
            capabilities=replace(binder.capabilities(), backends=(o.VersionRef(name="fixture", revision="2"),)), prepare=prepare)
        with pytest.raises(o.ConfigurationError):
            await unsupported.prepare(bind)
    anyio.run(exercise)


def test_snapshot_binder_native_job_membership_and_failed_enter_cleanup(tmp_path):
    async def exercise():
        _, capture, _, _ = await collect(tmp_path)
        first = run_request(candidate())
        second = replace(first, run_id="run-2", assignment=replace(first.assignment, id="assignment-2", repetition=1))
        config = o.NativeJobConfig(id="group", backend=candidate().backend, candidate_ids=("candidate",))
        job = o.NativeJobRequest(job_id="job", run_set_id="runs", planned_job_id="planned", config=config,
                                 requests=(first, second))
        bind = o.SnapshotBindRequest(id="native-bind", snapshots={"candidate": capture.snapshot},
            requirements={"candidate": "verified"}, native_job_request=job)
        events = []
        class JobBackend:
            ref = candidate().backend
            def capabilities(self):
                return None
            async def run_job(self, actual, *, recorder):
                events.append(tuple(item.run_id for item in actual.requests))
                return "native-job-result"
        @asynccontextmanager
        async def prepare(actual):
            try:
                yield SimpleNamespace(bindings=(o.SnapshotBinding(candidate_id="candidate",
                    candidate_fingerprint=capture.candidate_fingerprint, snapshot_fingerprint=capture.snapshot.fingerprint,
                    status="verified", reason="Owned fixture verification", run_ids=("run", "run-2"), job_ids=("job",),
                    evidence=(o.EvidenceRef(artifact=capture.snapshot.entries[0].artifact),)),),
                    backend=None, job_backend=JobBackend())
            finally:
                events.append("cleanup")
        binder = CallbackSnapshotBinder(ref=o.VersionRef(name="native-binder", revision="1"),
            capabilities=o.SnapshotCapabilities(backends=(candidate().backend,), scopes=("project",),
                                                direct=False, native=True, verified=True), prepare=prepare)
        async with await binder.prepare(bind) as session:
            assert await session.job_backend.run_job(job, recorder=Recorder()) == "native-job-result"
            with pytest.raises(o.ConfigurationError):
                await session.job_backend.run_job(replace(job, requests=(first,)), recorder=Recorder())
        assert events == [("run", "run-2"), "cleanup"]
        @asynccontextmanager
        async def invalid(actual):
            try:
                yield SimpleNamespace(bindings=(), backend=None, job_backend=JobBackend())
            finally:
                events.append("invalid-cleanup")
        broken = CallbackSnapshotBinder(ref=binder.ref, capabilities=binder.capabilities(), prepare=invalid)
        with pytest.raises(o.CaptureValidationError):
            async with await broken.prepare(bind):
                pytest.fail("Invalid binding must not enter")
        assert events[-1] == "invalid-cleanup"
    anyio.run(exercise)


def test_public_pipeline_file_collector_and_runtime_binder_lifetime(api, toy_backend, tmp_path):
    from tests.e2e.test_toy_pipeline import build
    study, evaluators, reducers = build(api, toy_backend, 'plain')
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'AGENTS.md').write_bytes(b'fixture configuration')
    cache = ArtifactCache(tmp_path / 'artifacts')
    collector = FileConfigurationCollector(roots={'project': source}, artifacts=cache)
    config = o.ConfigurationSpec(collector=collector.ref, params={'files': [
        {'root': 'project', 'path': 'AGENTS.md', 'source_tool': 'fixture', 'role': 'instruction'}]})
    plan = o.AssessmentPlan(id='bound-study', project_id=study.project_id, candidates=study.candidates,
        behavior=study, configuration={cid: config for cid in study.candidates})
    entered, cleaned = [], []
    @asynccontextmanager
    async def prepare(request):
        native_request = request.run_request
        snapshot = request.snapshots[native_request.candidate.id]
        artifact = snapshot.entries[0].artifact
        expected = Path(artifact.uri).read_bytes()
        active = True
        class BoundBackend:
            ref = toy_backend.ref
            def capabilities(self):
                return toy_backend.capabilities()
            def run(self, actual, *, recorder):
                assert active and Path(artifact.uri).read_bytes() == expected
                return toy_backend.run(actual, recorder=recorder)
        entered.append(native_request.run_id)
        try:
            yield SimpleNamespace(backend=BoundBackend(), job_backend=None,
                bindings=(o.SnapshotBinding(candidate_id=native_request.candidate.id,
                    candidate_fingerprint=snapshot.candidate_fingerprint, snapshot_fingerprint=snapshot.fingerprint,
                    status='verified', reason='Owned fixture delegate verifies and consumes retained configuration',
                    run_ids=(native_request.run_id,), evidence=(o.EvidenceRef(artifact=artifact),)),))
        finally:
            active = False
            assert any(item.run_id == native_request.run_id for item in toy_backend.calls)
            cleaned.append(native_request.run_id)
    binder = CallbackSnapshotBinder(ref=o.VersionRef(name='runtime-binding', revision='1'),
        capabilities=o.SnapshotCapabilities(backends=(toy_backend.ref,), scopes=('project',),
                                            direct=True, native=False, verified=True), prepare=prepare)
    result = api.AssessmentPipeline(plan=plan, collectors={collector.ref.name: collector},
        snapshot_binders={toy_backend.ref.name: binder}, backends={toy_backend.ref.name: toy_backend},
        evaluators=evaluators, reducers=reducers).eval()
    assert result.validate().valid and result.behavior_result is not None
    assert len(entered) == len(cleaned) == len(toy_backend.calls) == 4
    assert set(entered) == set(cleaned)
    assert all(binding.status == 'verified' for binding in result.snapshot_bindings)
    assert all(capture.status == 'completed' for capture in result.configuration.values())
    assert all(activity.subjects[0].snapshot_fingerprint is None for activity in result.activities)


def test_invalid_native_export_preserves_terminal_receipts_grades_and_sibling(api, toy_backend):
    from tests.contracts.test_assessment_pipeline import configured, pipeline
    from tests.contracts.test_native_jobs import native_study
    from tests.e2e.test_toy_pipeline import build
    study, native = native_study(api, toy_backend, private=True)
    _, evaluators, reducers = build(api, toy_backend, 'plain')
    direct = replace(study.candidates['A'], id='C', backend=toy_backend.ref)
    study = replace(study, candidates={**study.candidates, 'C': direct})
    class InvalidExport:
        ref = native.ref
        def capabilities(self):
            return native.capabilities()
        async def run_job(self, request, *, recorder):
            output = await native.run_job(request, recorder=recorder)
            # The native run really completed and its receipts are retained;
            # corruption affects only the later normalized export.
            return replace(output, runs=(replace(output.runs[0], id='foreign-run'), *output.runs[1:]))
    plan, collector, check = configured(study=study)
    result = pipeline(plan, collector, check, backends={toy_backend.ref.name: toy_backend},
        job_backends={native.ref.name: InvalidExport()}, evaluators=evaluators, reducers=reducers).eval()
    assert result.behavior_result is not None and result.validate().valid
    captured = result.behavior_result.runs
    assert captured.native_jobs[0].status == 'completed'
    assert captured.native_grades, 'A malformed later export cannot delete recorded verifier costs/results'
    assert captured.grading_inventory_complete.status == 'unknown'
    assert all(run.status == 'completed' for run in captured.for_candidate('C'))
    assert all(run.status == 'completed' for cid in ('A', 'B') for run in captured.for_candidate(cid))
    assert any(branch.kind == 'behavior' and branch.error is not None for branch in result.branches)


def test_invalid_native_run_graph_isolated_before_complete_capture_validation(api, toy_backend):
    from tests.contracts.test_assessment_pipeline import configured, pipeline
    from tests.contracts.test_native_jobs import native_study
    from tests.e2e.test_toy_pipeline import build
    from tests.e2e.test_data_contract import ProcessReceipt
    study, native = native_study(api, toy_backend, private=False)
    _, evaluators, reducers = build(api, toy_backend, 'plain')
    direct = replace(study.candidates['A'], id='C', backend=toy_backend.ref)
    study = replace(study, candidates={**study.candidates, 'C': direct})
    class InvalidGraph:
        ref = native.ref
        def capabilities(self):
            return native.capabilities()
        async def run_job(self, request, *, recorder):
            runs = []
            for item in request.requests:
                run = toy_backend.run(item, recorder=ProcessReceipt())
                # Invalid lineage is accepted by dataclass construction but
                # rejected by the common Run graph validator.
                execution = replace(run.executions[0], parent_id='unavailable-parent')
                runs.append(replace(run, job_id=request.job_id, executions=(execution,)))
            return o.NativeJobOutput(job=native.record(request, runs, 'completed'), runs=tuple(runs),
                grading_inventory_complete=o.Observation(value=True, status='observed'))
    plan, collector, check = configured(study=study)
    result = pipeline(plan, collector, check, backends={toy_backend.ref.name: toy_backend},
        job_backends={native.ref.name: InvalidGraph()}, evaluators=evaluators, reducers=reducers).eval()
    assert result.behavior_result is not None and result.validate().valid
    assert all(run.status == 'completed' for run in result.behavior_result.runs.for_candidate('C'))
    assert all(run.status == 'unobserved' and not run.executions
               for cid in ('A', 'B') for run in result.behavior_result.runs.for_candidate(cid))
    assert any(branch.error and branch.artifacts for branch in result.branches if branch.kind == 'behavior')


def test_public_pipeline_cancelled_native_session_gets_cancellation_and_partial_receipts(api, toy_backend, tmp_path):
    from tests.contracts.test_assessment_pipeline import configured, pipeline
    from tests.contracts.test_native_jobs import native_study
    from tests.e2e.test_toy_pipeline import build
    study, native = native_study(api, toy_backend, private=False)
    _, evaluators, reducers = build(api, toy_backend, 'plain')
    plan, collector, check = configured(study=study, strict=True)
    proof = ArtifactCache(tmp_path / 'cache').write_bytes('receipt', b'owned native receipt', 'text/plain')
    entered, cleanup = [], []
    class WaitingNative:
        ref = native.ref
        def capabilities(self):
            return native.capabilities()
        async def run_job(self, request, *, recorder):
            recorder.record_job(replace(native.record(request, [], 'running'), artifacts={'receipt': proof}))
            entered.append(request.job_id)
            await anyio.sleep_forever()
    @asynccontextmanager
    async def prepare(request):
        unit = request.native_job_request
        bindings = tuple(o.SnapshotBinding(candidate_id=cid,
            candidate_fingerprint=snapshot.candidate_fingerprint, snapshot_fingerprint=snapshot.fingerprint,
            status='verified', reason='Owned fixture verification',
            run_ids=tuple(item.run_id for item in unit.requests if item.candidate.id == cid), job_ids=(unit.job_id,),
            evidence=(o.EvidenceRef(artifact=proof),)) for cid, snapshot in request.snapshots.items())
        try:
            yield SimpleNamespace(bindings=bindings, backend=None, job_backend=WaitingNative())
        except BaseException as exc:
            cleanup.append((type(exc).__name__, getattr(exc, 'artifacts', {}),
                            getattr(exc, 'native_job_receipt', None)))
            raise
    binder = CallbackSnapshotBinder(ref=o.VersionRef(name='waiting-binder', revision='1'),
        capabilities=o.SnapshotCapabilities(backends=(native.ref,), scopes=(), direct=False, native=True, verified=True),
        prepare=prepare)
    flow = pipeline(plan, collector, check, job_backends={native.ref.name: WaitingNative()},
        snapshot_binders={native.ref.name: binder}, evaluators=evaluators, reducers=reducers)
    async def exercise():
        with anyio.move_on_after(0.2) as scope:
            await flow.aeval()
            pytest.fail('Cancellation cannot return success')
        assert scope.cancel_called
    anyio.run(exercise)
    assert len(entered) == len(cleanup) == 1
    assert 'Cancel' in cleanup[0][0]
    assert proof in cleanup[0][1].values()
    assert cleanup[0][2].status == 'running'


def test_malformed_collection_return_keeps_independently_valid_expense_receipt(tmp_path):
    from decimal import Decimal
    from agent_eval_flow.objects.values import observed
    from tests.contracts.test_assessment_pipeline import Collector, configured, pipeline
    artifact = ArtifactCache(tmp_path / 'cache').write_bytes('raw', b'original collector report', 'text/plain')
    class MalformedCollector(Collector):
        async def collect(self, request, *, recorder):
            capture = await super().collect(request, recorder=recorder)
            activity = replace(capture.activities[0],
                resources=replace(capture.activities[0].resources, cost_usd=observed(Decimal('0.75'))))
            return replace(capture, request_id='foreign-request', activities=(activity,), artifacts={'raw': artifact})
    plan, collector, check = configured(collector=MalformedCollector())
    result = pipeline(plan, collector, check).eval()
    capture = result.configuration['A']
    assert result.validate().valid and capture.status == 'error'
    assert capture.snapshot is None
    assert capture.artifacts['raw'] == artifact
    assert capture.activities[0].status == 'completed'
    assert result.resources().cost_usd.value == Decimal('0.75')
    assert result.check_coverage[0].status == 'blocked'


def test_cancelled_execution_aggregates_child_receipts_on_propagated_exception(api, toy_backend, tmp_path):
    from tests.contracts.test_native_jobs import native_study
    from agent_eval_flow.execution.preflight import prepare_execution
    from agent_eval_flow.execution.dispatch_assessment import execute_assessment_behavior
    study, native = native_study(api, toy_backend, private=False)
    artifact = ArtifactCache(tmp_path / 'cache').write_bytes('partial', b'partial native evidence', 'text/plain')
    class WaitingNative:
        ref = native.ref
        def capabilities(self):
            return native.capabilities()
        async def run_job(self, request, *, recorder):
            recorder.record_job(replace(native.record(request, [], 'running'), artifacts={'partial': artifact}))
            await anyio.sleep_forever()
    prepared = prepare_execution(study, job_backends={native.ref.name: WaitingNative()})
    plan = o.AssessmentPlan(id='cancel-progress', project_id=study.project_id,
                            candidates=study.candidates, behavior=study)
    captured = []
    async def gates(candidate_ids):
        return ()
    async def exercise():
        with anyio.move_on_after(0.1) as scope:
            try:
                await execute_assessment_behavior(prepared, plan=plan, captures={}, binders={}, resolve_gates=gates)
                pytest.fail('Cancellation must propagate')
            except BaseException as exc:
                captured.append(exc.behavior_progress)
                raise
        assert scope.cancel_called
    anyio.run(exercise)
    progress = captured[0]
    assert progress['plan'] == prepared.plan
    assert len(progress['allocated_run_ids']) == len(prepared.plan.assignments)
    assert progress['allocated_job_ids']
    assert progress['runs'] == () and progress['native_outputs'] == ()
    receipt = progress['cancellation_receipts'][0]
    assert receipt['native_job_receipt'].status == 'running'
    assert artifact in receipt['artifacts'].values()
