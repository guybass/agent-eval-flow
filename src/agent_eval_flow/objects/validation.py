"""Cross-record invariants, separate from Pydantic's strict field validation."""
from dataclasses import replace
from .identity import canonical_bytes, typed_key


def validate_study(study):
    from .records import ValidationIssue, ValidationReport
    issues = list(study.dataset.validate().issues) + list(study.suite.validate().issues)
    def error(path, message):
        issues.append(ValidationIssue(path=path, message=message, severity="error"))
    if not study.candidates:
        error("/candidates", "A study requires candidates")
    for name, candidate in study.candidates.items():
        if name != candidate.id:
            error(f"/candidates/{name}", "Candidate mapping key must equal its ID")
    owned, job_ids, channels = set(), set(), {}
    for index, job in enumerate(study.execution.native_jobs):
        path = f"/execution/native_jobs/{index}"
        if job.id in job_ids:
            error(path, "Duplicate native job ID")
        job_ids.add(job.id)
        if not job.candidate_ids or len(set(job.candidate_ids)) != len(job.candidate_ids):
            error(path, "Native jobs require nonempty, distinct candidates")
        for name in job.candidate_ids:
            if name not in study.candidates:
                error(path, f"Unknown candidate {name}")
            elif study.candidates[name].backend != job.backend:
                error(path, f"Native job backend name/revision does not match candidate {name}")
            if name in owned:
                error(path, f"Candidate {name} belongs to overlapping native jobs")
            owned.add(name)
        verifier_ids = set()
        for verifier in job.verifiers:
            if verifier.id in verifier_ids:
                error(path, "Duplicate verifier channel in one job")
            verifier_ids.add(verifier.id)
            identity = canonical_bytes(verifier)
            if verifier.id in channels and channels[verifier.id] != identity:
                error(path, f"Conflicting definition for verifier channel {verifier.id}")
            channels[verifier.id] = identity
            for table, columns in verifier.reference_columns.items():
                if table not in study.dataset.references:
                    error(path, f"Verifier {verifier.id} must use private reference tables")
                elif not set(columns) <= study.dataset.references[table].schema.keys():
                    error(path, f"Verifier {verifier.id} selects an unknown private column")
    contrast_ids = set()
    for contrast in study.contrasts:
        if contrast.id in contrast_ids or contrast.baseline not in study.candidates or contrast.challenger not in study.candidates:
            error("/contrasts", "Contrast identities must be distinct and resolve to candidates")
        contrast_ids.add(contrast.id)
    return ValidationReport(issues=tuple(issues))


def validate(record):
    from .records import ValidationReport
    name = type(record).__name__
    if name == "DataTable":
        from .dataset import validate_table
        return validate_table(record)
    if name == "EvalDataset":
        from .dataset import validate_dataset
        return validate_dataset(record)
    if name == "Study":
        return validate_study(record)
    if name == "RunPlan":
        return validate_plan(record)
    if name == "EvalSuite":
        from ..evaluation.compiler import validate_suite
        return validate_suite(record)
    if name == "EvaluationResult":
        from ..storage.manifests import validate_result_report
        return validate_result_report(record)
    if name in ("Run", "RunSet"):
        from .runset import validate_run, validate_runset
        return validate_run(record) if name == "Run" else validate_runset(record)
    return ValidationReport(issues=())


def validate_plan(plan):
    """An imported plan must establish the same complete assignment ownership."""
    from .records import ValidationIssue, ValidationReport
    issues = list(plan.dataset.units.validate().issues)
    def error(path, message):
        issues.append(ValidationIssue(path=path, message=message, severity="error"))
    if plan.dataset.unit_key != plan.dataset.units.key:
        error("/dataset/unit_key", "Plan dataset key disagrees with public unit table")
    if not set(plan.dataset.cluster_by) <= plan.dataset.units.schema.keys():
        error("/dataset/cluster_by", "Plan cluster columns must be present")
    if issues:
        return ValidationReport(issues=tuple(issues))
    roots = {typed_key(row, plan.dataset.unit_key) for row in plan.dataset.units.rows}
    expected = {(candidate, key, repetition) for candidate in plan.candidates for key in roots
                for repetition in range(plan.execution.repetitions)}
    actual, ids = set(), set()
    for index, assignment in enumerate(plan.assignments):
        path = f"/assignments/{index}"
        if assignment.id in ids:
            error(path, "Duplicate assignment ID")
        ids.add(assignment.id)
        if assignment.candidate_id not in plan.candidates:
            error(path, "Assignment refers to an unknown candidate")
            continue
        candidate = plan.candidates[assignment.candidate_id]
        if candidate.id != assignment.candidate_id or candidate.fingerprint() != assignment.candidate_fingerprint:
            error(path, "Assignment candidate identity/fingerprint disagrees with plan")
        try:
            if set(assignment.unit) != set(plan.dataset.unit_key):
                raise ValueError("Assignment unit fields disagree with dataset key")
            key = (assignment.candidate_id, typed_key(assignment.unit, plan.dataset.unit_key), assignment.repetition)
            if key in actual:
                error(path, "Duplicate candidate/task/repetition assignment")
            actual.add(key)
        except (KeyError, ValueError) as exc:
            error(path, str(exc))
    if actual != expected:
        error("/assignments", "Plan must contain each candidate/task/repetition exactly once")
    job_ids, ownership = set(), set()
    config_ids = [job.id for job in plan.execution.native_jobs]
    if len(set(config_ids)) != len(config_ids):
        error("/execution/native_jobs", "Duplicate native job configuration IDs")
    if len(plan.native_jobs) != len(plan.execution.native_jobs):
        error("/native_jobs", "Plan native groups do not match execution configuration")
    for index, job in enumerate(plan.native_jobs):
        path = f"/native_jobs/{index}"
        if job.id in job_ids or job.config not in plan.execution.native_jobs:
            error(path, "Duplicate or undeclared native job")
        job_ids.add(job.id)
        candidates = job.config.candidate_ids
        if not candidates or len(set(candidates)) != len(candidates):
            error(path, "Native group candidate IDs must be distinct and nonempty")
        if set(candidates) - plan.candidates.keys():
            error(path, "Unknown native group candidates")
        elif any(plan.candidates[name].backend != job.config.backend for name in candidates):
            error(path, "Native group backend differs from candidate backend")
        required = {a.id for a in plan.assignments if a.candidate_id in candidates}
        if set(job.assignment_ids) != required or len(job.assignment_ids) != len(required):
            error(path, "Native group must contain its exact configured assignments")
        if ownership & required:
            error(path, "Native groups overlap")
        ownership.update(required)
    return ValidationReport(issues=tuple(issues))
