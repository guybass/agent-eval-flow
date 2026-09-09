"""Deterministic planned work, distinct from fresh invocation identities."""
from __future__ import annotations

import random

from agent_eval_flow.objects import Assignment, DataTable, DatasetInfo, PlannedJob, RunPlan
from agent_eval_flow.objects.identity import semantic_fingerprint, typed_key


def execution_identity(study):
    """Identity of execution meaning; evaluation and descriptive labels are excluded."""
    return semantic_fingerprint("execution", {
        "project_id": study.project_id,
        "dataset": study.dataset.fingerprint(),
        "unit_key": study.dataset.unit_key,
        "cluster_by": study.dataset.cluster_by or study.dataset.unit_key,
        "candidates": {key: value.fingerprint() for key, value in study.candidates.items()},
        "execution": study.execution,
        "environment": study.environment,
    })


def assignment_identity(candidate_id, unit, repetition, execution_identity):
    return semantic_fingerprint("assignment", (
        execution_identity, candidate_id, typed_key(unit, tuple(unit)), repetition,
    ))


def build_plan(study):
    study.validate().raise_for_errors()
    identity = execution_identity(study)
    columns = tuple(dict.fromkeys((*study.dataset.unit_key,
                                  *(study.dataset.cluster_by or study.dataset.unit_key))))
    dataset = DatasetInfo(
        id=study.dataset.id,
        fingerprint=study.dataset.fingerprint(),
        unit_key=study.dataset.unit_key,
        cluster_by=study.dataset.cluster_by or study.dataset.unit_key,
        units=DataTable(
            rows=tuple({column: row[column] for column in columns}
                       for row in study.dataset.units.rows),
            key=study.dataset.unit_key,
            schema={column: study.dataset.units.schema[column] for column in columns},
        ),
    )
    assignments = []
    for candidate_id in sorted(study.candidates):
        candidate = study.candidates[candidate_id]
        for row in study.dataset.units.rows:
            unit = {column: row[column] for column in study.dataset.unit_key}
            for repetition in range(study.execution.repetitions):
                assignments.append(Assignment(
                    id=assignment_identity(candidate_id, unit, repetition, identity),
                    candidate_id=candidate_id, candidate_fingerprint=candidate.fingerprint(),
                    unit=unit, repetition=repetition,
                ))
    if study.execution.order_seed is not None:
        random.Random(study.execution.order_seed).shuffle(assignments)
    jobs = tuple(PlannedJob(
        id=semantic_fingerprint("planned-job", (identity, config.id)), config=config,
        assignment_ids=tuple(item.id for item in assignments
                             if item.candidate_id in config.candidate_ids),
    ) for config in study.execution.native_jobs)
    return RunPlan(
        study_id=study.id, study_fingerprint=study.fingerprint(), project_id=study.project_id,
        dataset=dataset, candidates=study.candidates, assignments=tuple(assignments),
        execution=study.execution, contrasts=study.contrasts, environment=study.environment,
        native_jobs=jobs,
    )


plan = build_plan
