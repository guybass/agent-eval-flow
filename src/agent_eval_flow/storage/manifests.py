"""Atomic manifest publication and validation of persisted result joins."""
import os
from pathlib import Path
import tempfile

from .. import objects as o


def validate_result(result):
    from ..results.query import index_result, invalid, measurement_ids
    result.runs.validate().raise_for_errors()
    result.suite.validate().raise_for_errors()
    index = index_result(result)
    if result.schema_version != "0.4" or result.runs.schema_version != "0.4":
        invalid("Unsupported stored schema version", "schema_version")
    if result.suite_fingerprint != result.suite.fingerprint():
        invalid("Stored suite fingerprint does not match its definition", "suite_fingerprint")
    expected_cells = {(run_id, metric_id) for run_id in index.runs for metric_id in measurement_ids(result.suite)}
    actual_cells = {(row.run_id, row.metric) for row in result.measurements if row.key is None}
    if actual_cells != expected_cells:
        invalid("Measurements must include exactly one task cell per run and requested metric")
    expected_summaries = {(candidate_id, summary.id) for candidate_id in result.runs.plan.candidates
                          for summary in result.suite.summaries}
    if set(index.summaries) != expected_summaries:
        invalid("Stored candidate summaries do not cover the configured summaries")
    for bundle in result.runs.native_grades:
        for retained in bundle.activities:
            if index.activities.get(retained.id) != retained:
                invalid(f"Retained grading activity is missing or modified: {retained.id}")
    inventory = o.combine_inventory((result.runs.grading_inventory_complete,
                                     result.performed_grading_inventory_complete))
    expected = o.aggregate_resources((activity.resources for activity in result.activities),
        inventory_complete=inventory, cost_scope=result.suite.evaluation_cost_scope)
    if expected.cost_scope != result.evaluation_resources.cost_scope:
        invalid("Stored grading total has an inconsistent cost scope", "evaluation_resources.cost_scope")
    for field in ("cost_usd", "input_tokens", "output_tokens", "human_minutes"):
        declared, computed = getattr(result.evaluation_resources, field), getattr(expected, field)
        if declared.status != computed.status or declared.value != computed.value:
            invalid(f"Stored grading {field} total disagrees with the recorded activities/inventories",
                    f"evaluation_resources.{field}")


def validate_root(root):
    from ..results.query import invalid
    if isinstance(root, o.EvaluationResult):
        validate_result(root)
    elif isinstance(root, (o.Study, o.EvalDataset, o.RunSet)):
        root.validate().raise_for_errors()
    else:
        raise o.StorageError(f"Unsupported saved root: {type(root).__name__}")
    capture = root.runs if isinstance(root, o.EvaluationResult) else root if isinstance(root, o.RunSet) else None
    if capture is not None:
        if capture.schema_version != "0.4":
            invalid("Unsupported RunSet schema version", "schema_version")
        for assignment in capture.plan.assignments:
            candidate = capture.plan.candidates.get(assignment.candidate_id)
            if candidate is None or assignment.candidate_fingerprint != candidate.fingerprint():
                invalid(f"Assignment {assignment.id} has a stale candidate fingerprint")


def validate_result_report(result):
    try:
        validate_result(result)
    except o.ValidationError as exc:
        return exc.report
    return o.ValidationReport(issues=())


def save(root, path):
    from .codec import encode_root
    payload = encode_root(root)
    path = Path(path)
    temporary = None
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="wb", prefix=".manifest-", suffix=".tmp", dir=path, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path / "manifest.json")
    except OSError as exc:
        raise o.StorageError(f"Cannot write manifest at {path}: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def load(path, expected_type=None, *, expected_kind=None):
    from .codec import decode_root
    path = Path(path)
    try:
        payload = (path / "manifest.json").read_bytes()
    except OSError as exc:
        raise o.StorageError(f"Cannot read manifest at {path}: {exc}") from exc
    try:
        return decode_root(payload, expected_type if expected_type is not None else expected_kind)
    except o.StorageError as exc:
        raise o.StorageError(f"{path / 'manifest.json'}: {exc}") from exc
    except o.ValidationError as exc:
        issues = tuple(o.ValidationIssue(path=f"{path / 'manifest.json'}:{issue.path}",
            message=issue.message, severity=issue.severity) for issue in exc.report.issues)
        raise o.ValidationError(o.ValidationReport(issues=issues)) from exc


class ManifestStore:
    save = staticmethod(save)
    load = staticmethod(load)
