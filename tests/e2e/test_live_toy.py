"""The same tiny missions on native Codex, Claude Code or a Vertex harness."""
from collections.abc import Mapping, Sequence
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from tests.e2e.support import TOY, assert_usable_result, make_study
from tests.e2e.test_toy_pipeline import toy_components


PROFILES = [pytest.param(name, marks=pytest.mark.profile(name))
            for name in ("codex_local", "claude_local", "vertex_gcp")]


@pytest.mark.live
@pytest.mark.parametrize("profile_name", PROFILES)
@pytest.mark.parametrize("mode", ["plain", "skill", "tool", "flow"])
def test_native_small_mission_returns_usable_objects(
    api, live_backend, live_profile, tmp_path, profile_name, mode
):
    assert live_profile["id"] == profile_name
    units = [{"task_id": "live-toy-1", "text": "receipt-" + uuid4().hex}]
    settings = {
        **live_profile["settings"], "e2e_mode": mode, "fixture_dir": str(TOY.resolve()),
        "mission": "Return task_id, a message string, and items (an array of strings). "
                   "For skill mode load fixture-format; for tool mode invoke fixture.echo with the task. "
                   "For flow mode load that skill, then invoke that tool, then return the receipt.",
        "response_schema": {
            "type": "object", "required": ["task_id", "message", "items"],
            "properties": {"task_id": {"type": "string"}, "message": {"type": "string"},
                           "items": {"type": "array", "items": {"type": "string"}}},
            "additionalProperties": False,
        },
    }
    study, evaluators, reducers = make_study(
        api, project_id=profile_name + "/" + mode, backend_ref=live_profile["backend_ref"],
        units=units, settings=settings, components=toy_components(api, mode), wall_time_s=180,
    )
    result = study.evaluate(backends={live_backend.ref.name: live_backend},
                            evaluators=evaluators, reducers=reducers)
    native_ids = set()
    for run in result.runs.runs:
        assert run.status == "completed", f"Integration did not complete: {run.error}"
        assert isinstance(run.output, Mapping)
        assert run.output["task_id"] == units[0]["task_id"]
        assert isinstance(run.output["message"], str)
        assert isinstance(run.output["items"], Sequence) and not isinstance(run.output["items"], str)
        assert all(isinstance(item, str) for item in run.output["items"])
        assert {"native.trace", "native.receipt"} <= run.artifacts.keys()
        for name in ("native.trace", "native.receipt"):
            ref = run.artifacts[name]
            path = Path(ref.uri)  # profile materializes remote evidence into a local cache
            assert path.is_absolute() and path.is_file()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == ref.sha256
        receipt = json.loads(Path(run.artifacts["native.receipt"].uri).read_text(encoding="utf-8"))
        assert receipt["profile"] == profile_name and receipt["native_id"]
        assert receipt["native_id"] not in native_ids
        native_ids.add(receipt["native_id"])
        assert receipt["runtime_revision"] and receipt["model"]["provider"] and receipt["model"]["id"]
        assert run.native_refs["invocation_id"] == receipt["native_id"]
        if profile_name == "vertex_gcp":
            assert receipt["deployment"]["kind"] == "gcp"
            assert receipt["deployment"]["project"] == live_profile["deployment"]["project"]
            assert receipt["model"]["provider"] == "vertex"
        if mode in ("skill", "flow"):
            skills = [e for e in run.events if e.kind == "skill_loaded" and e.fields.get("name") == "fixture-format"]
            assert skills and all(e.inputs or e.outputs for e in skills)
            assert skills[0].fields["sha256"] == study.candidates["A"].components["skill.format"].content.sha256
        if mode in ("tool", "flow"):
            calls = [e for e in run.events if e.kind == "tool_call" and e.fields.get("name") == "fixture.echo"]
            assert calls and all(e.inputs or e.outputs for e in calls)
            assert calls[0].fields["arguments"]["task_id"] == units[0]["task_id"]
            assert isinstance(calls[0].fields["result"], Mapping)
            assert calls[0].fields["result"]["message"] == units[0]["text"], "Tool receipt must match this request, not a stale run"
        if mode == "flow":
            assert run.events.index(skills[0]) < run.events.index(calls[0])
        # Unknown billing is valid capture; no accuracy/cost/latency threshold.
        for observation in (run.resources().cost_usd, run.duration_s()):
            if observation.status == "unknown":
                assert observation.value is None and observation.reason
    assert_usable_result(api, study, result, root=tmp_path / "consumer")
