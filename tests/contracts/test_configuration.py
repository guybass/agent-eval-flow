"""Public configuration contracts: identities, projections and immutable inputs.

These are specifications for the real package. A schema constructor may reject
malformed input before the public validate/plan boundary; neither outcome may
silently coerce the input or dispatch an agent.
"""
from dataclasses import replace
import importlib

import pytest

from tests.e2e.test_toy_pipeline import build


def rejected(api, operation):
    """Accept only the documented public or Pydantic validation failures."""
    pydantic_error = importlib.import_module("pydantic").ValidationError
    with pytest.raises((api.ValidationError, pydantic_error)):
        value = operation()
        if hasattr(value, "validate"):
            value.validate().raise_for_errors()


def dataset_parts(api):
    return {
        "id": "configuration-data",
        "units": api.DataTable(
            rows=({"task_id": "t1", "text": "first", "group": "g", "hidden": "ROOT_PRIVATE"},
                  {"task_id": "t2", "text": "second", "group": "g", "hidden": "OTHER_ROOT"}),
            key=("task_id",),
            schema={"task_id": "str", "text": "str", "group": "str", "hidden": "str"},
        ),
        "unit_key": ("task_id",),
        "input_columns": {"units": ("text",), "pages": ("text",)},
        "records": {"pages": api.DataTable(
            rows=({"task_id": "t1", "page": 1, "text": "one", "internal": "PAGE_PRIVATE"},
                  {"task_id": "t1", "page": 2, "text": "two", "internal": "PAGE_PRIVATE"},
                  {"task_id": "t2", "page": 1, "text": "other", "internal": "OTHER_PAGE"}),
            key=("task_id", "page"),
            schema={"task_id": "str", "page": "int", "text": "str", "internal": "str"},
        )},
        "references": {"oracle": api.DataTable(
            rows=({"task_id": "t1", "expected": "EVALUATOR_ONLY"},
                  {"task_id": "t2", "expected": "OTHER_EXPECTATION"}),
            key=("task_id",), schema={"task_id": "str", "expected": "str"},
        )},
        "cluster_by": ("group",),
    }


def test_projection_and_selection_preserve_joins_without_leaking_columns(api):
    dataset = api.EvalDataset(**dataset_parts(api))
    dataset.validate().raise_for_errors()
    public = dataset.agent_input({"task_id": "t1"})
    assert public.unit == {"task_id": "t1"}
    assert public.tables == {"units": ({"text": "first"},),
                             "pages": ({"text": "one"}, {"text": "two"})}
    assert "PRIVATE" not in repr(public) and "EVALUATOR_ONLY" not in repr(public)

    selected = dataset.select(({"task_id": "t1"},))
    selected.validate().raise_for_errors()
    assert selected.unit_key == dataset.unit_key and selected.cluster_by == dataset.cluster_by
    assert selected.input_columns == dataset.input_columns
    assert len(selected.units.to_records()) == 1
    assert len(selected.records["pages"].to_records()) == 2
    assert selected.references["oracle"].to_records() == (
        {"task_id": "t1", "expected": "EVALUATOR_ONLY"},)
    assert len(dataset.units.to_records()) == 2
    assert selected.fingerprint() != dataset.fingerprint()


@pytest.mark.parametrize("task_id, expected_schema", [("1", "str"), (1, "int")])
def test_from_records_preserves_typed_root_identity(api, task_id, expected_schema):
    dataset = api.EvalDataset.from_records(
        id="typed", units=[{"task_id": task_id, "text": "input"}],
        unit_key=("task_id",), input_columns={"units": ("text",)},
    )
    dataset.validate().raise_for_errors()
    assert dataset.units.schema["task_id"] == expected_schema
    assert type(dataset.agent_input({"task_id": task_id}).unit["task_id"]) is type(task_id)
    assert dataset.agent_input({"task_id": task_id}).tables == {"units": ({"text": "input"},)}


def test_dataset_copies_caller_containers_and_returns_immutable_views(api):
    rows = [{"task_id": "t1", "payload": {"items": ["original"]}}]
    columns = {"units": ("payload",)}
    dataset = api.EvalDataset.from_records(
        id="frozen", units=rows, unit_key=("task_id",), input_columns=columns,
    )
    before = dataset.fingerprint()
    rows[0]["payload"]["items"].append("late mutation")
    columns["units"] = ("task_id",)
    public = dataset.agent_input({"task_id": "t1"})
    assert tuple(public.tables["units"][0]["payload"]["items"]) == ("original",)
    with pytest.raises(TypeError):
        public.tables["units"][0]["payload"]["new"] = "forbidden"
    assert dataset.fingerprint() == before


@pytest.mark.parametrize("defect", [
    "duplicate_root", "boolean_key", "wrong_scalar", "child_without_root_key",
    "private_public_collision", "private_input_table", "unknown_input_column", "unknown_cluster",
])
def test_invalid_dataset_configuration_is_rejected(api, defect):
    def invalid():
        values = dataset_parts(api)
        if defect == "duplicate_root":
            values["units"] = replace(values["units"], rows=(values["units"].rows[0],) * 2)
        elif defect == "boolean_key":
            values["units"] = api.DataTable(
                rows=({"task_id": True},), key=("task_id",), schema={"task_id": "bool"})
            values.update(input_columns={"units": ("task_id",)}, records={}, references={}, cluster_by=None)
        elif defect == "wrong_scalar":
            page = values["records"]["pages"]
            values["records"] = {"pages": replace(page, rows=({**page.rows[0], "page": True},))}
        elif defect == "child_without_root_key":
            page = values["records"]["pages"]
            values["records"] = {"pages": replace(page, rows=(page.rows[0],), key=("page",))}
        elif defect == "private_public_collision":
            values["references"] = {"pages": values["references"]["oracle"]}
        elif defect == "private_input_table":
            values["input_columns"] = {"oracle": ("expected",)}
        elif defect == "unknown_input_column":
            values["input_columns"] = {"units": ("absent",)}
        else:
            values["cluster_by"] = ("absent",)
        return api.EvalDataset(**values)
    rejected(api, invalid)


def test_derive_replaces_components_and_top_level_settings_and_handles_native_omission(api):
    backend = api.VersionRef(name="adapter", revision="1")
    native = api.NativeConfig(schema_ref=api.VersionRef(name="dialect", revision="1"), values={"mode": "native"})
    old_tool = api.ComponentSpec(kind="tool", ref=api.VersionRef(name="tool", revision="1"), params={"old": 1})
    new_tool = api.ComponentSpec(kind="tool", ref=api.VersionRef(name="tool", revision="2"), params={"new": 2})
    base = api.Candidate(id="A", backend=backend, components={"tool": old_tool, "removed": old_tool},
                         settings={"nested": {"keep": 1, "old": 2}, "unchanged": True}, native=native)
    changed = base.derive(id="B", components={"tool": new_tool, "removed": None},
                          settings={"nested": {"new": 3}})
    assert changed.id == "B" and changed.backend == base.backend
    assert changed.components == {"tool": new_tool}
    assert changed.settings == {"nested": {"new": 3}, "unchanged": True}
    assert changed.native == native
    assert changed.derive(id="C", native=None).native is None
    replacement = api.NativeConfig(schema_ref=native.schema_ref, values={"mode": "replacement"})
    assert changed.derive(id="D", native=replacement).native == replacement
    assert base.components["tool"] == old_tool and "removed" in base.components
    assert base.settings["nested"] == {"keep": 1, "old": 2}


def test_diff_distinguishes_null_addition_from_removal_and_ignores_labels(api):
    base = api.Candidate(id="A", backend=api.VersionRef(name="adapter", revision="1"), components={})
    added = base.derive(id="B", settings={"nullable": None})
    additions = base.diff(added)
    removals = added.diff(base)
    assert len(additions) == len(removals) == 1
    assert additions[0].op == "add" and removals[0].op == "remove"
    assert additions[0].path == removals[0].path
    assert additions[0].after is None and removals[0].before is None
    assert base.diff(replace(base, id="label", description="New description")) == ()


def test_candidate_fingerprint_ignores_mapping_order_and_labels_but_tracks_behavior(api):
    backend = api.VersionRef(name="adapter", revision="1")
    base = api.Candidate(id="A", backend=backend, components={}, settings={"a": 1, "b": {"x": 2, "y": 3}})
    reordered = api.Candidate(id="renamed", backend=backend, components={}, description="documentation",
                              settings={"b": {"y": 3, "x": 2}, "a": 1})
    assert reordered.fingerprint() == base.fingerprint()
    assert base.derive(id="B", settings={"a": True}).fingerprint() != base.fingerprint()
    assert replace(base, backend=api.VersionRef(name="adapter", revision="2")).fingerprint() != base.fingerprint()


def test_candidate_defaults_and_defensive_copy(api):
    settings = {"nested": {"items": [1, 2]}}
    candidate = api.Candidate(id="A", backend=api.VersionRef(name="adapter"), components={}, settings=settings)
    other = api.Candidate(id="B", backend=candidate.backend, components={})
    settings["nested"]["items"].append(3)
    assert tuple(candidate.settings["nested"]["items"]) == (1, 2)
    assert candidate.backend.revision is None  # unknown versions remain explicit, not guessed
    assert other.settings == {} and other.description == "" and other.native is None
    with pytest.raises(TypeError):
        candidate.settings["nested"]["items"] = (9,)


@pytest.mark.parametrize("revision", [None, ""])
def test_native_dialect_requires_a_nonempty_resolved_revision(api, revision):
    rejected(api, lambda: api.Candidate(
        id="A", backend=api.VersionRef(name="adapter"), components={},
        native=api.NativeConfig(schema_ref=api.VersionRef(name="dialect", revision=revision))))


def test_plan_is_stable_complete_and_keeps_native_group_membership(api, toy_backend):
    study, _, _ = build(api, toy_backend, "plain", repetitions=2)
    job = api.NativeJobConfig(id="paired", backend=toy_backend.ref, candidate_ids=("A", "B"))
    study = replace(study, execution=replace(study.execution, native_jobs=(job,)))
    first, second = study.plan(), study.plan()
    assert toy_backend.calls == []
    assert first.assignments == second.assignments and first.native_jobs == second.native_jobs
    assert len(first.assignments) == len({item.id for item in first.assignments}) == 8
    assert {(item.candidate_id, item.unit["task_id"], item.repetition) for item in first.assignments} == {
        (candidate, task, repetition)
        for candidate in ("A", "B") for task in ("toy-1", "toy-2") for repetition in (0, 1)
    }
    assert all(item.candidate_fingerprint == study.candidates[item.candidate_id].fingerprint()
               for item in first.assignments)
    assert len(first.native_jobs) == 1 and first.native_jobs[0].config == job
    assert set(first.native_jobs[0].assignment_ids) == {item.id for item in first.assignments}
    assert "EVALUATOR_ONLY" not in repr(first.dataset)


def test_execution_and_native_configuration_defaults_are_explicit(api):
    budget = api.Budget(wall_time_s=3.0)
    policy = api.ExecutionPolicy(budget=budget)
    assert (policy.repetitions, policy.max_concurrency, policy.order_seed, policy.infrastructure_retries) == (1, 1, 0, 0)
    assert policy.cost_scope == ("model",) and policy.native_jobs == ()
    assert budget.max_tokens is None and budget.max_cost_usd is None
    native = api.NativeConfig(schema_ref=api.VersionRef(name="dialect", revision="1"))
    verifier = api.VerifierSpec(id="grade", implementation=api.VersionRef(name="grader", revision="1"))
    job = api.NativeJobConfig(id="job", backend=api.VersionRef(name="adapter", revision="1"), candidate_ids=("A",))
    assert native.values == {} and job.native is None and job.verifiers == ()
    assert verifier.reference_columns == {} and verifier.params == {} and verifier.native is None and verifier.budget is None


def test_identical_verifier_channel_can_be_reused_by_disjoint_jobs(api, toy_backend):
    study, _, _ = build(api, toy_backend, "plain")
    verifier = api.VerifierSpec(
        id="grade", implementation=api.VersionRef(name="grader", revision="1"),
        reference_columns={"private_oracle": ("private_marker",)},
        params={"strict": True}, budget=api.Budget(wall_time_s=2.0),
    )
    jobs = tuple(api.NativeJobConfig(id=candidate_id, backend=toy_backend.ref,
                                     candidate_ids=(candidate_id,), verifiers=(verifier,))
                 for candidate_id in ("A", "B"))
    study = replace(study, execution=replace(study.execution, native_jobs=jobs))
    study.validate().raise_for_errors()
    plan = study.plan()
    assignments = {assignment.id: assignment for assignment in plan.assignments}
    assert len(plan.native_jobs) == 2
    for job in plan.native_jobs:
        assert job.config.verifiers == (verifier,)
        assert len(job.assignment_ids) == 2
        assert {assignments[item].candidate_id for item in job.assignment_ids} == set(job.config.candidate_ids)
    assert toy_backend.calls == []


@pytest.mark.parametrize("defect", [
    "duplicate_jobs", "empty_candidates", "duplicate_candidate", "unknown_candidate",
    "overlapping_jobs", "wrong_backend_name", "wrong_backend_revision", "candidate_mapping_key",
])
def test_plan_rejects_inconsistent_native_job_ownership(api, toy_backend, defect):
    def invalid():
        study, _, _ = build(api, toy_backend, "plain")
        job = api.NativeJobConfig(id="job", backend=toy_backend.ref, candidate_ids=("A",))
        jobs = (job,)
        if defect == "duplicate_jobs":
            jobs = (job, replace(job, candidate_ids=("B",)))
        elif defect == "empty_candidates":
            jobs = (replace(job, candidate_ids=()),)
        elif defect == "duplicate_candidate":
            jobs = (replace(job, candidate_ids=("A", "A")),)
        elif defect == "unknown_candidate":
            jobs = (replace(job, candidate_ids=("unknown",)),)
        elif defect == "overlapping_jobs":
            jobs = (job, replace(job, id="other", candidate_ids=("A", "B")))
        elif defect == "wrong_backend_name":
            jobs = (replace(job, backend=api.VersionRef(name="other", revision=toy_backend.ref.revision)),)
        elif defect == "wrong_backend_revision":
            jobs = (replace(job, backend=api.VersionRef(name=toy_backend.ref.name, revision="different")),)
        else:
            study = replace(study, candidates={"wrong": study.candidates["A"], "B": study.candidates["B"]})
            jobs = ()
        return replace(study, execution=replace(study.execution, native_jobs=jobs)).plan()
    rejected(api, invalid)
    assert toy_backend.calls == []


@pytest.mark.parametrize("defect", ["public_table", "unknown_column", "conflicting_channel"])
def test_plan_rejects_invalid_private_verifier_configuration(api, toy_backend, defect):
    def invalid():
        study, _, _ = build(api, toy_backend, "plain")
        verifier = api.VerifierSpec(
            id="grade", implementation=api.VersionRef(name="grader", revision="1"),
            reference_columns={"private_oracle": ("private_marker",)},
        )
        if defect == "public_table":
            verifier = replace(verifier, reference_columns={"units": ("text",)})
        elif defect == "unknown_column":
            verifier = replace(verifier, reference_columns={"private_oracle": ("absent",)})
        first = api.NativeJobConfig(id="a", backend=toy_backend.ref, candidate_ids=("A",), verifiers=(verifier,))
        second = api.NativeJobConfig(id="b", backend=toy_backend.ref, candidate_ids=("B",), verifiers=(
            replace(verifier, params={"different": True}) if defect == "conflicting_channel" else verifier,))
        return replace(study, execution=replace(study.execution, native_jobs=(first, second))).plan()
    rejected(api, invalid)
    assert toy_backend.calls == []


@pytest.mark.parametrize("field, value", [
    ("repetitions", 0), ("max_concurrency", 0), ("infrastructure_retries", -1),
    ("wall_time_s", 0.0), ("wall_time_s", float("inf")), ("max_tokens", True),
])
def test_plan_rejects_invalid_limits_without_dispatch(api, toy_backend, field, value):
    def invalid():
        study, _, _ = build(api, toy_backend, "plain")
        if field in {"wall_time_s", "max_tokens"}:
            execution = replace(study.execution, budget=replace(study.execution.budget, **{field: value}))
        else:
            execution = replace(study.execution, **{field: value})
        return replace(study, execution=execution).plan()
    rejected(api, invalid)
    assert toy_backend.calls == []
