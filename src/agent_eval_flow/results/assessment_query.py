"""Read-only assessment views and unique source-activity accounting."""
from .. import objects as o
from ..objects import assessment as a
from ..objects.identity import freeze_json
from ..objects.values import observed, unknown, combine_inventory, aggregate_resources
from .query import invalid


def subject_candidate(subject):
    return subject.candidate.candidate_id if subject.kind == "component" else getattr(subject, "candidate_id", None)


def activity_inventory(result):
    """One entry per namespaced source, never one entry per finding reference."""
    inventory = {}
    for activity in result.activities:
        key = a.ActivityRef(namespace="assessment", id=activity.id)
        if key in inventory and inventory[key] != activity:
            invalid("Conflicting assessment activity identity")
        inventory[key] = activity
    if result.behavior_result is not None:
        for activity in result.behavior_result.activities:
            key = a.ActivityRef(namespace="behavior", id=activity.id)
            if key in inventory:
                invalid("Duplicate behavioral activity identity")
            inventory[key] = activity
    return inventory


def assessment_resources(result, *, cost_scope=("model",), incremental=False):
    result.validate().raise_for_errors()
    inventory = activity_inventory(result)
    selected = set(result.performed_activity_refs) if incremental else set(inventory)
    activities = [activity for ref, activity in inventory.items() if ref in selected]
    completeness = [result.performed_inventory_complete]
    completeness.extend(activity.inventory_complete for ref, activity in inventory.items()
                        if ref in selected and ref.namespace == "assessment")
    if not incremental and any(not capture.activities for capture in result.configuration.values()):
        completeness.append(unknown("A retained collection has no source activity receipt"))
    behavior = result.behavior_result
    if behavior is not None and (not incremental or any(ref.namespace == "behavior" for ref in selected)):
        completeness.append(behavior.performed_grading_inventory_complete)
        if not incremental:
            completeness.append(behavior.runs.grading_inventory_complete)
    return aggregate_resources([activity.resources for activity in activities],
        inventory_complete=combine_inventory(completeness or (observed(True),)), cost_scope=tuple(cost_scope))


def explain_candidate_assessments(result, candidate_id):
    result.validate().raise_for_errors()
    if candidate_id not in result.plan.candidates:
        invalid(f"Unknown candidate: {candidate_id}", "candidate_id")
    rows = tuple(row for row in result.assessments if subject_candidate(row.subject) == candidate_id)
    config = tuple(row for row in rows if row.origin.kind == "configuration")
    behavior = tuple(row for row in rows if row.origin.kind == "behavior")
    refs = {ref for row in rows for ref in row.activity_refs}
    inventory = activity_inventory(result)
    capture = result.configuration.get(candidate_id)
    if capture:
        refs.update(a.ActivityRef(namespace="assessment", id=activity.id) for activity in capture.activities)
    return freeze_json({"candidate": result.plan.candidates[candidate_id], "capture": capture,
        "configuration_assessments": config, "behavioral_assessments": behavior,
        "coverage": tuple(cell for cell in result.check_coverage if cell.candidate_id == candidate_id),
        "snapshot_bindings": tuple(binding for binding in result.snapshot_bindings if binding.candidate_id == candidate_id),
        "activities": tuple({"ref": ref, "activity": activity} for ref, activity in inventory.items() if ref in refs),
        "limitations": ("Linked configuration and behavioral evidence does not establish causation.",)})


def summary_assessments(result):
    result.validate().raise_for_errors()
    return tuple(explain_candidate_assessments(result, candidate_id) for candidate_id in result.plan.candidates)


summary = summary_assessments
