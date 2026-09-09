"""Assessment persistence, policy and reporting preserve source evidence."""
from dataclasses import replace
from decimal import Decimal
import json

import pytest

from agent_eval_flow import objects as o
from agent_eval_flow.objects import assessment as a
from agent_eval_flow.objects.identity import semantic_fingerprint
from agent_eval_flow.objects.values import observed, unknown, zero_resources
from agent_eval_flow.evaluation.assessment_projection import project_behavioral_assessments, wrap_behavior_result
from agent_eval_flow.results.assessment_query import assessment_resources, activity_inventory
from agent_eval_flow.results.assessment_selection import evaluate_requirement, evaluate_gate, decide_assessments
from agent_eval_flow.results.assessment_comparison import compare_candidate_assessments
from agent_eval_flow.storage.assessment_codec import encode_assessment_root, decode_assessment_root
from agent_eval_flow.storage.assessment_manifests import save_assessment, load_assessment
from agent_eval_flow.reporting.assessment_html import report_assessment
from tests.e2e.test_toy_pipeline import build
from tests.contracts.test_results_storage import evaluate_table


def config_result(api, backend, *, finding=False, partial=False, tier="advisory", behavior=None, study=None,
                  amount=Decimal("0.10"), amount_type="decimal"):
    if study is None:
        study, _, _ = build(api, backend, "plain")
    spec = a.ConfigurationSpec(collector=o.VersionRef(name="fixture.collector", revision="1"),
                               snapshot_requirement="record_only")
    check = a.CheckSpec(id="scan", evaluator=o.VersionRef(name="fixture.scan", revision="1"),
                       candidate_ids=tuple(study.candidates), output_types={"amount": amount_type})
    plan = a.AssessmentPlan(id="assessment-fixture", project_id=study.project_id,
        candidates=study.candidates, configuration={key: spec for key in study.candidates},
        checks=(check,), behavior=study if behavior is not None else None)
    captures, activities, assessments, coverage = {}, [], [], []
    for cid, candidate in study.candidates.items():
        snap = a.CandidateSnapshot(candidate_id=cid, candidate_fingerprint=candidate.fingerprint(),
            entries=(), collector=spec.collector,
            params_fingerprint=semantic_fingerprint("configuration-params", spec.params),
            inventory_complete=observed(True))
        subject = a.CandidateSubject(candidate_id=cid, candidate_fingerprint=candidate.fingerprint(),
                                     snapshot_fingerprint=snap.fingerprint)
        collect = a.AssessmentActivity(id="collection-" + cid, request_id="collect-" + cid,
            implementation=spec.collector,
            input_fingerprint=a.ConfigurationCollectRequest(id="collect-" + cid,
                activity_id="collection-" + cid, candidate=candidate, spec=spec).input_fingerprint,
            subjects=(subject,),
            phase="collection", status="completed", resources=zero_resources(), inventory_complete=observed(True))
        capture = a.ConfigurationCapture(id="capture-" + cid, request_id=collect.request_id,
            candidate_id=cid, candidate_fingerprint=candidate.fingerprint(), snapshot=snap,
            status="completed", activities=(collect,))
        aid = behavior.activities[0].id if behavior is not None and cid == "A" else "scan-" + cid
        activity = a.AssessmentActivity(id=aid, request_id="request-" + cid,
            implementation=check.evaluator, input_fingerprint="scan-input", subjects=(subject,),
            phase="configuration_evaluation", status="partial" if partial else "completed",
            resources=replace(zero_resources(), cost_usd=observed(Decimal("0.25"))), inventory_complete=observed(True))
        findings = ()
        if finding and cid == "A":
            findings = (a.Finding(id="finding-A", subject=subject, rule=check.evaluator,
                message='<script>alert("bad")</script>', severity="error", basis="inferred", evidence_tier=tier,
                evidence=(o.EvidenceRef(artifact=o.ArtifactRef(uri="javascript:alert(1)", media_type="text/plain"),
                                       description="Unsafe source link"),)),)
        assessment = a.Assessment(id="assessment-" + cid, subject=subject,
            origin=a.ConfigurationOrigin(request_id=activity.request_id, check_id=check.id,
                check_fingerprint=check.fingerprint(), evaluator=check.evaluator),
            status="ok", conclusion="unknown", findings=findings,
            values=(a.AssessmentValue(id="amount", value=amount, status="ok", basis="observed", reason="fixture"),),
            activity_refs=(a.ActivityRef(namespace="assessment", id=activity.id),))
        cell = a.CheckCoverage(candidate_id=cid, check_id=check.id,
            status="partial" if partial else "completed", reason="Partial fixture" if partial else "",
            request_id=activity.request_id, assessment_id=assessment.id,
            inventory_complete=observed(not partial), omitted_suppressed_findings=False)
        captures[cid] = capture
        activities.extend((collect, activity))
        assessments.append(assessment)
        coverage.append(cell)
    if behavior is not None:
        assessments.extend(project_behavioral_assessments(behavior))
    return a.AssessmentResult(id="outer-result", plan=plan, configuration=captures,
        activities=tuple(activities), assessments=tuple(assessments), check_coverage=tuple(coverage),
        behavior_result=behavior,
        branches=(a.BranchOutcome(kind="configuration", candidate_ids=tuple(study.candidates),
            status="partial" if partial else "completed", reason="fixture"),)
            + ((a.BranchOutcome(kind="behavior", candidate_ids=tuple(study.candidates), status="completed", reason="fixture"),)
               if behavior is not None else ()),
        performed_activity_refs=tuple(a.ActivityRef(namespace="assessment", id=activity.id) for activity in activities))


def test_config_only_roundtrip_preserves_decimal_findings_and_separate_format(api, toy_backend):
    result = config_result(api, toy_backend, finding=True)
    payload = encode_assessment_root(result)
    document = json.loads(payload)
    assert document["format"] == "agent-eval-flow-assessment" and document["schema_version"] == "0.1"
    assert document["data"]["behavior_result"] is None
    assert document["data"]["assessments"][0]["values"][0]["value"] == {"type": "decimal", "value": "0.10"}
    restored = decode_assessment_root(payload, a.AssessmentResult)
    assert restored == result
    assert type(restored.assessments[0].values[0].value) is Decimal
    from agent_eval_flow.storage.codec import decode_root
    with pytest.raises(o.StorageError):
        decode_root(payload, o.EvaluationResult)


def test_partial_scan_and_unknown_tier_are_not_passes(api, toy_backend):
    partial = config_result(api, toy_backend, partial=True)
    requirement = a.NoFindingsRequirement(check_id="scan")
    assert evaluate_requirement(partial, "A", requirement).decision == "unknown"
    unknown_tier = config_result(api, toy_backend, finding=True, tier=None)
    filtered = replace(requirement, allowed_tiers=("validated",))
    assert evaluate_requirement(unknown_tier, "A", filtered).decision == "unknown"
    assert evaluate_requirement(unknown_tier, "A", requirement).decision == "fail"
    gate = a.GateSpec(id="gate", candidate_ids=("A",), requirements=(filtered,), on_unknown="allow")
    outcome = evaluate_gate(unknown_tier, "A", gate)
    assert outcome.decision == "unknown" and outcome.allowed


def test_value_requirement_compares_numeric_values_without_coercion_and_requires_scope(api, toy_backend):
    complete = config_result(api, toy_backend)
    exact = a.ValueRequirement(check_id="scan", value_id="amount", op="==", expected_value=Decimal("0.10"))
    assert evaluate_requirement(complete, "A", exact).decision == "pass"
    cross_type = replace(exact, op=">=", expected_value=0)
    assert evaluate_requirement(complete, "A", cross_type).decision == "pass"
    # Decimal('0.10') and the binary float 0.10 remain different stored numbers.
    assert evaluate_requirement(complete, "A", replace(exact, expected_value=0.10)).decision == "fail"
    bool_vs_int = config_result(api, toy_backend, amount=1, amount_type="int")
    assert evaluate_requirement(bool_vs_int, "A", replace(exact, expected_value=True)).decision == "unknown"
    text = config_result(api, toy_backend, amount="1", amount_type="text")
    assert evaluate_requirement(text, "A", cross_type).decision == "unknown"
    assert evaluate_requirement(config_result(api, toy_backend, partial=True), "A", exact).decision == "unknown"


@pytest.mark.parametrize("second_state", [None, "skipped", "error", "unknown"])
def test_explicit_rule_inventory_cannot_hide_uninspected_rules(api, toy_backend, second_state):
    result = config_result(api, toy_backend)
    statuses = {"one": "completed"}
    if second_state is not None:
        statuses["two"] = second_state
    result = replace(result, check_coverage=tuple(replace(cell,
        requested_rules=("one", "two"), rule_statuses=statuses) for cell in result.check_coverage))
    requirement = a.NoFindingsRequirement(check_id="scan")
    assert evaluate_requirement(result, "A", requirement).decision == "unknown"
    assert not compare_candidate_assessments(result, "A", "B", check_ids=("scan",))["rows"][0]["comparable"]


def test_explicit_tier_selection_and_noncompensatory_ranking(api, toy_backend):
    study, behavior, check = evaluate_table(api, toy_backend,
        {"A": {"quality": 1.0}, "B": {"quality": 0.5}}, {"quality": "float"})
    result = config_result(api, toy_backend, finding=True, behavior=behavior, study=study)
    policy = a.AssessmentPolicy(id="choose", revision="1",
        configuration_requirements=(a.NoFindingsRequirement(check_id="scan", allowed_tiers=("advisory",)),),
        behavior_policy=o.SelectionPolicy(id="quality", objectives=(o.ObjectiveTerm(metric="mean_quality", direction="maximize"),)))
    calls = len(check.calls)
    choice = decide_assessments(result, policy)
    assert choice.selected_ids == ("B",)
    assert next(row for row in choice.rows if row.candidate_id == "A").eligibility == "fail"
    assert len(check.calls) == calls


def test_unique_namespaced_activity_costs_and_behavior_projection(api, toy_backend):
    study, behavior, check = evaluate_table(api, toy_backend,
        {"A": {"quality": 1.0}, "B": {"quality": 0.5}}, {"quality": "float"})
    result = config_result(api, toy_backend, behavior=behavior, study=study)
    assert len(activity_inventory(result)) == len(result.activities) + len(behavior.activities)
    assert assessment_resources(result).cost_usd.value == Decimal("0.50")
    rows = [row for row in result.assessments if row.origin.kind == "behavior"]
    assert len(rows) == len(behavior.measurements)
    assert all(row.conclusion == "unknown" for row in rows)
    assert [row.values[0].value for row in rows] == [row.value for row in behavior.measurements]


def test_missing_performed_inventory_never_becomes_known_zero(api, toy_backend):
    result = replace(config_result(api, toy_backend), performed_activity_refs=(),
        performed_inventory_complete=unknown("Native grader may have run without a receipt"))
    total = assessment_resources(result, incremental=True)
    assert total.cost_usd.status == "unknown" and total.cost_usd.value is None
    assert assessment_resources(result).input_tokens.status == "unknown"


def test_imported_collection_without_receipt_keeps_historical_cost_unknown(api, toy_backend):
    result = config_result(api, toy_backend)
    captures = {cid: replace(capture, activities=()) for cid, capture in result.configuration.items()}
    activities = tuple(item for item in result.activities if item.phase != "collection")
    imported = replace(result, configuration=captures, activities=activities, performed_activity_refs=())
    assert assessment_resources(imported).cost_usd.status == "unknown"
    assert assessment_resources(imported, incremental=True).cost_usd.value == 0


def test_nested_v04_envelopes_wrap_without_new_work_and_roundtrip(api, toy_backend):
    study, behavior, check = evaluate_table(api, toy_backend,
        {"A": {"quality": 1.0}, "B": {"quality": 0.5}}, {"quality": "float"})
    plan = a.AssessmentPlan(id="wrapped", project_id=study.project_id, candidates=study.candidates, behavior=study)
    calls = len(check.calls), len(toy_backend.calls)
    result = wrap_behavior_result(behavior, plan=plan)
    payload = encode_assessment_root(result)
    document = json.loads(payload)
    assert document["data"]["behavior_result"]["schema_version"] == "0.4"
    assert document["data"]["plan"]["behavior"]["kind"] == "study"
    restored = decode_assessment_root(payload, a.AssessmentResult)
    assert restored.behavior_result == behavior and restored.performed_activity_refs == ()
    assert assessment_resources(restored, incremental=True).cost_usd.value == 0
    assert calls == (len(check.calls), len(toy_backend.calls))


def test_html_escapes_provider_content_and_has_no_fake_behavior(api, toy_backend, tmp_path):
    result = config_result(api, toy_backend, finding=True)
    path = report_assessment(result, tmp_path / "report.html")
    html = path.read_text(encoding="utf-8")
    assert "Configuration-only assessment" in html
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert 'href="javascript:' not in html
    assert "Behavioral results</h2>" not in html
    assert "No retained findings" in html


def test_atomic_publication_preserves_previous_manifest(api, toy_backend, tmp_path, monkeypatch):
    result = config_result(api, toy_backend)
    destination = tmp_path / "bundle"
    save_assessment(result, destination)
    old = (destination / "manifest.json").read_bytes()
    import agent_eval_flow.storage.assessment_manifests as module
    def denied(*args):
        raise OSError("publication denied")
    monkeypatch.setattr(module.os, "replace", denied)
    with pytest.raises(o.StorageError):
        save_assessment(result, destination)
    assert (destination / "manifest.json").read_bytes() == old
    assert load_assessment(destination, a.AssessmentResult) == result


def test_comparison_preserves_unknown_scope_and_no_statistics(api, toy_backend):
    result = config_result(api, toy_backend, partial=True)
    comparison = compare_candidate_assessments(result, "A", "B", check_ids=("scan",))
    assert not comparison["rows"][0]["comparable"]
    assert comparison["rows"][0]["values"][0]["delta"] is None


def test_public_result_methods_use_pure_consumers(api, toy_backend, tmp_path):
    result = config_result(api, toy_backend)
    path = tmp_path / "saved"
    result.save(path)
    restored = a.AssessmentResult.load(path)
    assert restored.summary()[0]["candidate"].id == "A"
    assert restored.explain("A")["coverage"][0].check_id == "scan"
    assert restored.compare("A", "B", check_ids=("scan",))["rows"][0]["comparable"]
    selection = restored.select(a.AssessmentPolicy(id="eligibility", revision="1",
        configuration_requirements=(a.NoFindingsRequirement(check_id="scan"),)))
    assert selection.status == "eligibility_only" and selection.selected_ids == ()
    assert restored.resources().cost_usd.value == Decimal("0.50")
    assert restored.report(tmp_path / "report.html").exists()


def test_loading_rejects_changed_behavioral_projection(api, toy_backend):
    study, behavior, _ = evaluate_table(api, toy_backend,
        {"A": {"quality": 1.0}, "B": {"quality": 0.5}}, {"quality": "float"})
    plan = a.AssessmentPlan(id="wrapped", project_id=study.project_id, candidates=study.candidates, behavior=study)
    document = json.loads(encode_assessment_root(wrap_behavior_result(behavior, plan=plan)))
    document["data"]["assessments"][0]["values"][0]["value"]["value"] = 0.25
    with pytest.raises((o.ValidationError, o.StorageError)):
        decode_assessment_root(json.dumps(document), a.AssessmentResult)
