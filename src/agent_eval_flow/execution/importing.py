"""Import envelopes without execution and reconcile absent planned work."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError as SchemaValidationError

from agent_eval_flow.objects import ArtifactRef, ImportedCapture, RunSet, StorageError, VersionRef
from .capture import capture_error, missing_run, validate_capture


def reconcile_import(capture, *, plan, run_set_id, importer, import_source):
    if not isinstance(capture, ImportedCapture):
        raise capture_error("import", "Importer must return an ImportedCapture envelope")
    assignments = {item.id: item for item in plan.assignments}
    runs, run_ids = {}, set()
    for run in capture.runs:
        if run.assignment_id not in assignments:
            raise capture_error("import.runs.assignment_id", "Imported run has an unknown assignment")
        if run.assignment_id in runs or run.id in run_ids:
            raise capture_error("import.runs", "Duplicate imported assignment or run ID")
        runs[run.assignment_id] = run
        run_ids.add(run.id)
    jobs, ownership, retained_links = {}, {}, {}
    planned = {job.id: job for job in plan.native_jobs}
    for job in capture.native_jobs:
        if job.id in jobs:
            raise capture_error("import.native_jobs", "Duplicate native job ID")
        jobs[job.id] = job
        if job.planned_job_id not in planned:
            raise capture_error("import.native_jobs", "Unknown planned native job")
        expected = planned[job.planned_job_id]
        if (set(job.assignment_ids) != set(expected.assignment_ids)
                or len(job.assignment_ids) != len(expected.assignment_ids)
                or job.backend != expected.config.backend):
            raise capture_error("import.native_jobs", "Native job ownership differs from plan")
        for assignment_id in job.assignment_ids:
            if assignment_id in ownership:
                raise capture_error("import.native_jobs", "Ambiguous native job ownership")
            ownership[assignment_id] = job.id
        for link in job.links:
            if link.assignment_id in retained_links:
                raise capture_error("import.native_jobs.links", "Duplicate imported native link")
            if link.assignment_id not in job.assignment_ids:
                raise capture_error("import.native_jobs.links", "Native link outside job ownership")
            retained_links[link.assignment_id] = link
    for assignment in plan.assignments:
        if assignment.id in runs:
            continue
        link = retained_links.get(assignment.id)
        run_id = link.run_id if link is not None else str(uuid4())
        if run_id in run_ids:
            raise capture_error("import.native_jobs.links", "Missing run link reuses a captured run ID")
        run_ids.add(run_id)
        run = missing_run(assignment, run_id=run_id, cost_scope=plan.execution.cost_scope,
                          job_id=ownership.get(assignment.id), reason="Import omitted this planned assignment")
        if link is not None:
            from dataclasses import replace
            run = replace(run, native_refs=link.native_refs)
        runs[assignment.id] = run
    return validate_capture(RunSet(
        id=run_set_id, plan=plan, runs=tuple(runs[item.id] for item in plan.assignments),
        importer=importer, import_source=import_source, native_jobs=capture.native_jobs,
        native_grades=capture.native_grades, projections=capture.projections,
        grading_inventory_complete=capture.grading_inventory_complete,
    ))


def import_runs(source, *, plan, importer):
    # A malformed plan must not reach importer-owned I/O or native parsing.
    plan.validate().raise_for_errors()
    source = Path(source).resolve()
    try:
        if source.is_file():
            before = sha256(source.read_bytes()).hexdigest()
            reference = ArtifactRef(uri=source.as_uri(), media_type="application/octet-stream", sha256=before)
        elif source.is_dir():
            before = None
            reference = ArtifactRef(uri=source.as_uri(), media_type="inode/directory", sha256=None)
        else:
            raise StorageError(f"Import source does not exist: {source}")
    except OSError as exc:
        raise StorageError(f"Cannot read import source {source}: {exc}") from exc
    ref = importer.ref
    if not isinstance(ref, VersionRef) or not ref.name:
        raise capture_error("importer.ref", "Importer must expose a named VersionRef")
    try:
        capture = importer.read(source, plan=plan)
    except OSError as exc:
        raise StorageError(f"Cannot import {source}: {exc}") from exc
    except SchemaValidationError as exc:
        raise capture_error("import", f"Invalid data from {ref.name}: {exc}") from exc
    if before is not None:
        try:
            after = sha256(source.read_bytes()).hexdigest()
        except OSError as exc:
            raise StorageError(f"Cannot verify import source {source}: {exc}") from exc
        if after != before:
            raise capture_error("import_source", "Import source changed while it was being read")
    return reconcile_import(capture, plan=plan, run_set_id=str(uuid4()),
                            importer=ref, import_source=reference)


import_from = import_runs
