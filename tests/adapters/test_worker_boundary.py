"""Prepared transport contract: no providers, provisioning or scientific grader."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import anyio
import pytest

from agent_eval_flow import objects as o
from agent_eval_flow.adapters.common import RuntimeCapabilities, StagedAsset
from agent_eval_flow.adapters.process import StopEvidence
from agent_eval_flow.adapters.worker import PreparedWorkerClient, WorkerHandle, WorkerSubmission, WorkerUpdate
from agent_eval_flow.execution.planning import build_plan
from agent_eval_flow.storage.artifacts import ArtifactCache
from tests.e2e.test_toy_pipeline import build
from tests.e2e.support import observed, zero_resources


TRACE = b'{"iteration":1,"tool":"read","arguments":{"path":"app.py"},"result":["line1","line2"]}\n'


def setup_request(api, backend):
    study, _, _ = build(api, backend, "plain")
    assignment = build_plan(study).assignments[0]
    request = o.RunRequest(run_id="worker-run", assignment=assignment,
        candidate=study.candidates[assignment.candidate_id], input=study.dataset.agent_input(assignment.unit),
        policy=study.execution, environment=None)
    source = o.ArtifactRef(uri="https://prepared.invalid/trace.jsonl", media_type="application/x-ndjson",
                           sha256=sha256(TRACE).hexdigest())
    evidence = o.EvidenceRef(artifact=source, locator="/0", description="Native tool iteration")
    execution = o.Execution(id="worker-execution", slot="main", retry_index=0, parent_id=None,
        status="completed", started_at=None, ended_at=None, effective_config=observed(api, {"model": "fixture"}),
        resources=zero_resources(api))
    run = o.Run(id=request.run_id, assignment_id=assignment.id, status="completed", cost_scope=("model",),
        output={"report": {"tool_iterations": 1, "files": ["app.py"]}}, output_state="available",
        artifacts={"native.trace": source}, executions=(execution,), output_sources=(execution.id,),
        events=(o.Event(id="worker-event", execution_id=execution.id, kind="tool_call", at=None,
                        fields={"name": "read"}, source=evidence, outputs=(evidence,)),),
        started_at=None, ended_at=None, environment=o.Observation(value={"platform": "prepared"},
            status="observed", evidence=(evidence,)), execution_inventory_complete=observed(api, True))
    expected = RuntimeCapabilities(upstream_ref=o.VersionRef(name="fixture-runtime", revision="1"),
        deployment=observed(api, {"platform": "prepared"}), limits=backend.capabilities())
    return request, run, expected


class OwnedWorker:
    def __init__(self, expected, run, *, fail_submit=False, tamper=False, block=False, fail_release=False):
        self.expected, self.run = expected, run
        self.fail_submit, self.tamper, self.block, self.fail_release = fail_submit, tamper, block, fail_release
        self.submits = self.polls = self.fetches = self.releases = self.stops = 0
    async def describe(self):
        return self.expected
    async def submit(self, submission):
        self.submits += 1
        if self.fail_submit:
            raise ConnectionError("Acknowledgment lost")
        return WorkerHandle(id="owned-handle", request_id=submission.request_id, native_refs={"native": "opaque-123"})
    async def poll(self, handle, *, cursor):
        self.polls += 1
        if self.block:
            await anyio.sleep_forever()
        return WorkerUpdate(cursor="done", state="completed", capture=self.run)
    async def fetch(self, source, *, destination):
        self.fetches += 1
        destination.write_bytes(b"tampered" if self.tamper else TRACE)
        return o.ArtifactRef(uri=destination.as_uri(), media_type=source.media_type, sha256=source.sha256)
    async def stop(self, handle, *, reason):
        self.stops += 1
        return StopEvidence(requested=True, confirmed=True, reason="Owned fixture released its work")
    async def release(self, handle):
        self.releases += 1
        if self.fail_release:
            raise RuntimeError("temporary cleanup failed")


def test_materializes_nested_evidence_once_and_preserves_native_source_provenance(api, toy_backend, tmp_path):
    request, run, expected = setup_request(api, toy_backend)
    worker = OwnedWorker(expected, run)
    cache = ArtifactCache(tmp_path / "cache")
    client = PreparedWorkerClient(worker=worker, expected=expected, artifacts=cache, poll_interval_s=0)
    callbacks = []
    result = anyio.run(lambda: client.invoke(WorkerSubmission(request_id=request.run_id,
        kind="direct", request=request), record_partial=callbacks.append))
    source = result.artifacts["native.trace"]
    assert worker.submits == worker.fetches == worker.releases == 1
    assert result.events[0].source.artifact == source
    assert result.events[0].outputs[0].artifact == source
    assert result.environment.evidence[0].artifact == source
    assert result.output == run.output and result.id == run.id
    cache.verify(source).raise_for_errors()
    assert Path(url2pathname(urlparse(source.uri).path)).read_bytes() == TRACE
    provenance = result.artifacts["aef.worker.provenance"]
    data = json.loads(Path(url2pathname(urlparse(provenance.uri).path)).read_text())
    assert data["sources"][run.artifacts["native.trace"].uri]["local_uri"] == source.uri
    assert callbacks == [result] and worker.stops == 0


def test_ambiguous_submission_is_never_retried(api, toy_backend, tmp_path):
    request, run, expected = setup_request(api, toy_backend)
    worker = OwnedWorker(expected, run, fail_submit=True)
    client = PreparedWorkerClient(worker=worker, expected=expected, artifacts=ArtifactCache(tmp_path / "cache"))
    with pytest.raises(ConnectionError, match="Acknowledgment lost"):
        anyio.run(lambda: client.invoke(WorkerSubmission(request_id=request.run_id, kind="direct", request=request),
                                      record_partial=lambda value: None))
    assert worker.submits == 1 and worker.polls == worker.releases == 0


def test_hash_failure_retains_native_capture_and_releases_once(api, toy_backend, tmp_path):
    request, run, expected = setup_request(api, toy_backend)
    worker = OwnedWorker(expected, run, tamper=True)
    client = PreparedWorkerClient(worker=worker, expected=expected, artifacts=ArtifactCache(tmp_path / "cache"))
    callbacks = []
    with pytest.raises(o.StorageError, match="hash mismatch"):
        anyio.run(lambda: client.invoke(WorkerSubmission(request_id=request.run_id, kind="direct", request=request),
                                      record_partial=callbacks.append))
    assert callbacks == [run] and worker.submits == worker.releases == 1


def test_wrong_revision_rejected_before_submission(api, toy_backend, tmp_path):
    request, run, expected = setup_request(api, toy_backend)
    worker = OwnedWorker(replace(expected, upstream_ref=o.VersionRef(name="fixture-runtime", revision="wrong")), run)
    client = PreparedWorkerClient(worker=worker, expected=expected, artifacts=ArtifactCache(tmp_path / "cache"))
    with pytest.raises(o.ConfigurationError):
        anyio.run(lambda: client.invoke(WorkerSubmission(request_id=request.run_id, kind="direct", request=request),
                                      record_partial=lambda value: None))
    assert worker.submits == 0


def test_cancellation_stops_acknowledged_work_and_releases_once(api, toy_backend, tmp_path):
    request, run, expected = setup_request(api, toy_backend)
    worker = OwnedWorker(expected, run, block=True)
    client = PreparedWorkerClient(worker=worker, expected=expected, artifacts=ArtifactCache(tmp_path / "cache"))
    async def exercise():
        caught = None
        with anyio.move_on_after(0.02):
            try:
                await client.invoke(WorkerSubmission(request_id=request.run_id, kind="direct", request=request),
                                    record_partial=lambda value: None)
            except anyio.get_cancelled_exc_class() as exc:
                caught = exc
                raise
        assert caught.stop_evidence.confirmed
    anyio.run(exercise)
    assert worker.submits == worker.stops == worker.releases == 1


def test_cleanup_failure_preserves_completed_native_output(api, toy_backend, tmp_path):
    request, run, expected = setup_request(api, toy_backend)
    worker = OwnedWorker(expected, run, fail_release=True)
    client = PreparedWorkerClient(worker=worker, expected=expected, artifacts=ArtifactCache(tmp_path / "cache"))
    result = anyio.run(lambda: client.invoke(WorkerSubmission(request_id=request.run_id, kind="direct", request=request),
                                          record_partial=lambda value: None))
    assert result.status == "completed" and result.output == run.output
    assert "aef.worker.cleanup" in result.artifacts and worker.releases == 1


def test_asset_channel_and_path_reject_private_leak_or_escape(api, toy_backend):
    request, run, _ = setup_request(api, toy_backend)
    source = run.artifacts["native.trace"]
    with pytest.raises(o.ConfigurationError):
        StagedAsset(source=source, relative_path="../outside", channel="agent")
    private = StagedAsset(source=source, relative_path="answer.json", channel="verifier")
    with pytest.raises(o.ConfigurationError):
        WorkerSubmission(request_id=request.run_id, kind="direct", request=request, assets=(private,))


def test_local_asset_hash_is_checked_before_worker_submission(api, toy_backend, tmp_path):
    request, run, expected = setup_request(api, toy_backend)
    source = tmp_path / "input.json"
    source.write_bytes(b"changed after configuration")
    asset = StagedAsset(source=o.ArtifactRef(uri=str(source), media_type="application/json",
        sha256=sha256(b"original input").hexdigest()), relative_path="input.json", channel="agent")
    worker = OwnedWorker(expected, run)
    client = PreparedWorkerClient(worker=worker, expected=expected, artifacts=ArtifactCache(tmp_path / "cache"))
    with pytest.raises(o.ConfigurationError):
        anyio.run(lambda: client.invoke(WorkerSubmission(request_id=request.run_id, kind="direct",
            request=request, assets=(asset,)), record_partial=lambda value: None))
    assert worker.submits == 0
