"""Render an escaped, offline HTML view without callbacks or network access."""
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from importlib.resources import files
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import objects as o
from ..results.query import index_result, collect_run_evidence, invalid


def _plain(value):
    if is_dataclass(value):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, (Decimal, datetime)):
        return str(value)
    return value


def _json(value):
    return json.dumps(_plain(value), ensure_ascii=False, indent=2, allow_nan=False)


def _safe_link(uri):
    if any(ord(char) < 32 for char in uri):
        return None
    parsed = urlparse(uri)
    if parsed.scheme.lower() in ("http", "https") and parsed.netloc:
        return uri
    if parsed.scheme.lower() == "file":
        return uri
    try:
        path = Path(uri)
        if path.is_absolute():
            return path.as_uri()
    except ValueError:
        pass
    return None


def _runtime_checks(result, run, measurements):
    """Join saved check details to observations; never select or grade evidence."""
    specs = tuple(spec for spec in result.suite.metrics
                  if isinstance(spec.source, o.EvaluatorSource)
                  and spec.source.ref.name == "agent-eval-flow.runtime-evidence")
    if not specs:
        return ()
    from ..objects.runtime_evidence import decode_runtime_observation

    events = {event.id: event for event in run.events}
    checks = []
    for spec in specs:
        rows = tuple(row for row in measurements if row.metric == spec.id)
        task = next((row for row in rows if row.key is None), None)
        details = []
        for row in rows:
            if row.key is None or "event" not in row.key:
                continue
            event_id = row.key["event"]
            event = events.get(event_id)
            record, error = None, None
            if event is None:
                error = "The linked runtime observation is absent from the saved run."
            else:
                try:
                    record = decode_runtime_observation(event)
                    if record is None:
                        error = "The linked event is not a versioned runtime observation."
                except ValueError as exc:
                    error = "Cannot decode the linked runtime observation: " + str(exc)
            evidence = list(row.evidence)
            if event is not None:
                evidence.extend((*event.inputs, *event.outputs))
                if event.source is not None:
                    evidence.append(event.source)
            if record is not None:
                evidence.extend((*record.declared.evidence, *record.observed.evidence))
            details.append({"measurement": row, "event_id": event_id, "event": event,
                "observation": record, "error": error,
                "evidence": tuple(dict.fromkeys(evidence))})
        checks.append({"spec": spec, "measurement": task, "details": details,
            "has_expected_override": "expected" in spec.params,
            "expected_override": spec.params.get("expected")})
    return tuple(checks)


def _view(result, selection):
    index = index_result(result)
    if selection is not None and selection.result_id != result.id:
        invalid("Selection belongs to a different result", "selection.result_id")
    task_rows = []
    for ordinal, run in enumerate(result.runs.runs):
        task_rows.append({"anchor": f"run-{ordinal}", "run": run,
            "assignment": index.assignments[run.assignment_id],
            "score": index.scores[run.id], "measurements": index.measurements[run.id],
            "evidence": collect_run_evidence(index, run.id), "resources": run.resources(),
            "duration": run.duration_s(),
            "runtime_checks": _runtime_checks(result, run, index.measurements[run.id])})
    return {"result": result, "plan": result.runs.plan, "coverage": result.runs.coverage(),
        "tasks": task_rows, "selection": selection,
        "incremental_resources": result.incremental_evaluation_resources()}


def report(result, path, *, selection=None):
    view = _view(result, selection)
    template_root = files("agent_eval_flow.reporting").joinpath("templates")
    environment = Environment(loader=FileSystemLoader(str(template_root)),
        autoescape=select_autoescape(default=True), trim_blocks=True, lstrip_blocks=True)
    environment.filters["pretty_json"] = _json
    environment.globals["safe_link"] = _safe_link
    template = environment.get_template("report.html.j2")
    # This string comes exclusively from our packaged stylesheet, never from
    # agent text, configuration or a user-supplied template.
    css = template_root.joinpath("report.css").read_text(encoding="utf-8")
    rendered = template.render(view=view, css=css)
    path = Path(path)
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=".report-", suffix=".tmp",
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return path
    except OSError as exc:
        raise o.StorageError(f"Cannot write report at {path}: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


class HtmlReportRenderer:
    write = staticmethod(report)
