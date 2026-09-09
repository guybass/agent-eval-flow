"""Runtime evidence contracts: facts and coverage, without domain judgments."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

import agent_eval_flow as a
from agent_eval_flow.objects.identity import plain
from agent_eval_flow.objects.values import observed, unknown
from agent_eval_flow.objects.runtime_evidence import RuntimeObservation, decode_runtime_observation
from agent_eval_flow.evaluation.runtime_checks import RuntimeEvidenceEvaluator


@pytest.fixture
def evidence(tmp_path):
    from agent_eval_flow.storage.artifacts import ArtifactCache
    ref = ArtifactCache(tmp_path / "evidence").write_bytes("raw", b"retained native record", "text/plain")
    return a.EvidenceRef(artifact=ref, locator="line:1", description="Native observation")


def event(evidence, *, actual="complete task", expected="complete task", subject="task.instructions",
          phase="first_action", boundary="provider_cli_input", id="event-1", coverage="complete", **kw):
    record = RuntimeObservation(collector=a.VersionRef(name="test.native", revision="1"),
        subject=subject, phase=phase, boundary=boundary,
        declared=observed(expected, evidence=(evidence,)),
        observed=observed(actual, evidence=(evidence,)), coverage=coverage, **kw)
    return record.to_event(id=id, execution_id="execution-1")


def check(events, **params):
    evaluator = RuntimeEvidenceEvaluator()
    spec = a.MetricSpec(id="delivery", source=a.EvaluatorSource(ref=evaluator.ref),
        output_type="bool", role="diagnostic", params={"subject": "task.instructions",
            "phase": "first_action", "boundary": "provider_cli_input", **params})
    return evaluator.compute(spec, SimpleNamespace(evaluation_cost_scope=("model",)),
        SimpleNamespace(id="run-1", events=tuple(events)))


def test_instruction_after_512_is_observed_violation(evidence):
    task = "x" * 512 + " Preserve queued orders."
    output = check([event(evidence, actual=task[:512], expected=task)])
    assert output.task.status == "ok" and output.task.value is False
    assert output.task.basis == "observed"
    assert output.details[0].key == {"event": "event-1"}
    assert output.task.evidence == (evidence,)
    assert "provider_cli_input" in output.task.reason


@pytest.mark.parametrize("component,subject,expected,actual,operator,value", [
    ("tool.search", "tools.available", ["read", "search"], ["read"], "includes", False),
    ("environment.worker", "environment.image", "sha256:expected", "sha256:old", "equals", False),
    ("flow.main", "loop.post_terminal_calls", 0, 1, "at_most", False),
    ("model.primary", "model.id", "model-a", "model-a", "equals", True),
    ("memory.session", "memory.tenant", "cedar", "birch", "equals", False),
])
def test_same_contract_covers_components(evidence, component, subject, expected, actual, operator, value):
    output = check([event(evidence, subject=subject, expected=expected, actual=actual, component_id=component)],
        subject=subject, component_id=component, operator=operator)
    assert output.task.status == "ok" and output.task.value is value


def test_allowed_transformation_uses_explicit_requirement(evidence):
    row = event(evidence, actual="Wrapper: Keep orders intact.", expected="Original task with formatting",
        transformations=("Native wrapper and whitespace normalization",))
    assert check([row], operator="contains", expected="Keep orders intact.").task.value is True
    assert decode_runtime_observation(row).transformations


@pytest.mark.parametrize("coverage", ["partial", "unknown"])
def test_partial_capture_cannot_prove_presence_or_absence(evidence, coverage):
    output = check([event(evidence, coverage=coverage)])
    assert output.task.status == "missing" and output.task.value is None and output.task.basis is None


def test_phase_boundary_and_call_are_real_selectors(evidence):
    late = event(evidence, phase="after_discovery", actual=["read", "search"], expected=["search"],
        subject="tools.available", call_id="call-2")
    assert check([late], subject="tools.available", operator="includes").task.status == "missing"
    assert check([late], subject="tools.available", phase="after_discovery", call_id="call-2",
        operator="includes").task.value is True
    assert check([late], subject="tools.available", phase="after_discovery", boundary="remote_request").task.status == "missing"


def test_empty_old_capture_is_unknown_and_native_events_stay_opaque():
    native = a.Event(id="native", execution_id="execution-1", kind="upstream.custom", at=None, fields={"anything": [1, 2]})
    assert decode_runtime_observation(native) is None
    assert check([native]).task.status == "missing"


def test_first_last_mean_recorded_order_not_future_delivery(evidence):
    before = event(evidence, actual="clipped", id="before")
    after = event(evidence, id="after")
    assert check([before, after], selection="first").task.value is False
    assert check([before, after], selection="last").task.value is True
    assert check([before, after]).task.value is False


def test_incomplete_inventory_requirement_cannot_pass(evidence):
    assert check([event(evidence)], expected_count=2).task.status == "missing"


def test_excess_population_is_error_even_with_counterexample(evidence):
    rows = [event(evidence, actual="clipped"), event(evidence, id="extra")]
    assert check(rows, expected_count=1).task.status == "error"


@pytest.mark.parametrize("selection", ["first", "last"])
def test_malformed_unselected_record_cannot_satisfy_expected_count(evidence, selection):
    valid = event(evidence)
    bad = replace(event(evidence, id="unparseable"), fields={"phase": "first_action", "boundary": "provider_cli_input"})
    rows = [valid, bad] if selection == "first" else [bad, valid]
    result = check(rows, selection=selection, expected_count=2)
    assert result.task.status == "error" and result.task.value is None
    assert any(row.key == {"event": "unparseable"} and row.status == "error" for row in result.details)


def test_estimated_value_does_not_become_observed(evidence):
    original = decode_runtime_observation(event(evidence))
    record = replace(original, observed=a.Observation(value="complete task", status="estimated",
        reason="Inferred from configuration", evidence=(evidence,)))
    assert check([record.to_event(id="estimate", execution_id="execution-1")]).task.status == "missing"


def test_known_violation_survives_an_unknown_observation(evidence):
    rows = [event(evidence, actual="clipped"), event(evidence, id="unknown", coverage="unknown")]
    assert check(rows).task.value is False
    assert {r.status for r in check(rows).details} == {"ok", "missing"}


def test_wrong_type_and_malformed_payload_are_evaluator_errors(evidence):
    assert check([event(evidence, actual=True, expected=1)]).task.value is False
    assert check([event(evidence, actual=True, expected=1)], operator="at_most").task.status == "error"
    row = event(evidence)
    malformed = replace(row, fields={**plain(row.fields), "coverage": "invented"})
    assert check([malformed]).task.status == "error"


def test_decoding_after_json_roundtrip_preserves_evidence(evidence):
    row = event(evidence, component_id="prompt.main", call_id="call-1")
    decoded = decode_runtime_observation(replace(row, fields=plain(row.fields)))
    assert decoded.observed.evidence == (evidence,)
    assert decoded.component_id == "prompt.main" and decoded.call_id == "call-1"


def test_observed_values_require_provenance(evidence):
    with pytest.raises(ValueError, match="evidence"):
        RuntimeObservation(collector=a.VersionRef(name="test", revision="1"), subject="tools.available",
            phase="startup", boundary="tool_registry", declared=unknown("No declaration"),
            observed=observed(["read"]), coverage="complete")
