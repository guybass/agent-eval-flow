"""Escaped standalone assessment reports over retained data only."""
from importlib.resources import files
import os
from pathlib import Path
import tempfile

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import objects as o
from ..results.assessment_query import summary_assessments, activity_inventory
from ..results.assessment_selection import decide_assessments
from .html import _json, _safe_link, _view


def report_assessment(result, path, *, policy=None):
    result.validate().raise_for_errors()
    template_root = files("agent_eval_flow.reporting").joinpath("templates")
    environment = Environment(loader=FileSystemLoader(str(template_root)),
        autoescape=select_autoescape(default=True), trim_blocks=True, lstrip_blocks=True)
    environment.filters["pretty_json"] = _json
    environment.globals["safe_link"] = _safe_link
    activities = tuple({"ref": ref, "activity": activity,
        "performed": ref in result.performed_activity_refs}
        for ref, activity in activity_inventory(result).items())
    view = {"result": result, "candidates": summary_assessments(result),
        "activities": activities,
        "decision": decide_assessments(result, policy) if policy is not None else None,
        "behavior": _view(result.behavior_result, None) if result.behavior_result is not None else None}
    css = template_root.joinpath("report.css").read_text(encoding="utf-8")
    rendered = environment.get_template("assessment_report.html.j2").render(view=view, css=css)
    path, temporary = Path(path), None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=".assessment-report-",
                                         suffix=".tmp", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return path
    except OSError as exc:
        raise o.StorageError(f"Cannot write assessment report at {path}: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


class AssessmentHtmlReportRenderer:
    write = staticmethod(report_assessment)
