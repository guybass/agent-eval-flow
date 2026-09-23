"""Portable tools reports from saved receipts, without importing Toolscore."""
from collections import Counter
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
import hashlib
from importlib.resources import files
import json
import math
import os
from pathlib import Path
import tempfile
from urllib.parse import quote, urlparse
from urllib.request import url2pathname

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import TypeAdapter

from .. import objects as o
from ..objects.identity import plain, semantic_fingerprint
from ..objects.tool_trace import ToolTrace
from ..results.query import index_result
from .html import _json


RECEIPT_TYPE = "application/vnd.agent-eval-flow.toolscore+json"


def _read(artifact):
    parsed = urlparse(artifact.uri)
    if parsed.scheme == "file" and parsed.netloc in ("", "localhost"):
        path = Path(url2pathname(parsed.path))
    elif not parsed.scheme or (os.name == "nt" and len(parsed.scheme) == 1):
        path = Path(artifact.uri)
    else:
        raise ValueError("Only retained local evidence can be bundled; no remote fetch was attempted")
    if not path.is_absolute():
        raise ValueError("Evidence location must be absolute before bundling")
    data = path.read_bytes()
    if not artifact.sha256 or hashlib.sha256(data).hexdigest() != artifact.sha256:
        raise ValueError("Evidence is missing a hash or differs from its recorded hash")
    return data


def _write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".tools-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _artifacts(value):
    """Walk typed references, including configuration evidence in assessments."""
    if isinstance(value, o.ArtifactRef):
        yield value
    elif is_dataclass(value):
        for field in fields(value):
            yield from _artifacts(getattr(value, field.name))
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _artifacts(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _artifacts(item)


def _receipt(artifact, run):
    receipt = json.loads(_read(artifact))
    if not isinstance(receipt, dict) or receipt.get("kind") != "agent-eval-flow.toolscore" or receipt.get("schema_version") != "1":
        raise ValueError("Unsupported Toolscore receipt")
    if (receipt.get("run_id") != run.id or receipt.get("assignment_id") != run.assignment_id
            or receipt.get("run_fingerprint") != run.fingerprint()):
        raise ValueError("Toolscore receipt belongs to a different capture")
    if receipt.get("status") not in ("ok", "missing", "error"):
        raise ValueError("Invalid Toolscore receipt status")
    for key in ("metrics", "settings", "evaluator", "resources"):
        if not isinstance(receipt.get(key), dict):
            raise ValueError(f"Invalid Toolscore receipt {key}")
    for metric in receipt["metrics"].values():
        if not isinstance(metric, dict) or metric.get("status") not in ("ok", "missing", "error", "not_applicable"):
            raise ValueError("Invalid saved tool metric status")
        value = metric.get("value")
        if metric["status"] == "ok":
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1 + 1e-9:
                raise ValueError("Invalid saved tool metric value")
        elif value is not None:
            raise ValueError("Unavailable saved tool metric must have a null value")
    if receipt.get("trace") is not None:
        TypeAdapter(ToolTrace).validate_json(json.dumps(receipt["trace"], allow_nan=False))
    if receipt["status"] == "ok":
        if not receipt.get("trace") or "score" not in receipt["metrics"]:
            raise ValueError("Scored Toolscore receipt lacks its trace or score status")
        alternatives, selected = receipt.get("alternatives"), receipt.get("selected_alternative")
        if not isinstance(alternatives, list) or type(selected) is not int or not 0 <= selected < len(alternatives):
            raise ValueError("Invalid selected tool contract alternative")
    elif receipt["metrics"]:
        raise ValueError("Unscored Toolscore receipt cannot claim saved metrics")
    return receipt


def _tool_rows(receipt):
    calls = (receipt.get("trace") or {}).get("calls", [])
    alternatives = receipt.get("alternatives", [])
    expected = alternatives[receipt["selected_alternative"]]["expected"] if alternatives else []
    needed = Counter(c["tool"] for c in expected)
    actual = Counter(c["tool"] for c in calls)
    return [{"tool": name, "expected": needed[name] if alternatives else None, "actual": actual[name],
        "completed": sum(c["tool"] == name and c["completion"] == "completed" for c in calls),
        "errors": sum(c["tool"] == name and c["completion"] == "error" for c in calls),
        "unknown": sum(c["tool"] == name and c["completion"] == "unknown" for c in calls)}
        for name in sorted(needed.keys() | actual.keys())]


def _summaries(runs):
    groups = {}
    for run in runs:
        fallback_error = any(row["status"] == "error" for row in run["measurements"])
        for item in run["receipts"] or [{"receipt": None, "error": fallback_error}]:
            receipt = item["receipt"]
            settings = receipt["settings"] if receipt else {"receipt": "unavailable"}
            config = semantic_fingerprint("tools-report-settings", settings)
            key = (run["candidate_id"], config)
            group = groups.setdefault(key, {"candidate": key[0], "settings": settings,
                "observed": 0, "missing": 0, "error": 0, "not_applicable": 0, "values": []})
            if receipt is None:
                group["error" if item["error"] else "missing"] += 1
                continue
            score = receipt["metrics"].get("score", {"status": receipt["status"], "value": None})
            if score["status"] == "ok":
                group["observed"] += 1
                group["values"].append(score["value"])
            else:
                group[score["status"]] += 1
    return [{**group, "mean": sum(group["values"]) / len(group["values"]) if group["values"] else None}
        for group in groups.values()]


def report_bundle(result, path, *, selection=None, policy=None):
    """Write general HTML, tools HTML/JSON and verified local evidence together.

    Original manifests and artifact references are not changed. The exported
    HTML and JSON evidence index are portable; this is not a relocated RunSet.
    Unavailable or unverified evidence is listed rather than fetched or hidden.
    """
    try:
        return _report_bundle(result, path, selection=selection, policy=policy)
    except OSError as exc:
        raise o.StorageError(f"Cannot write tools report bundle at {path}: {exc}") from exc


def _report_bundle(result, path, *, selection=None, policy=None):
    path = Path(path)
    behavior = result if isinstance(result, o.EvaluationResult) else result.behavior_result
    if behavior is None:
        raise o.ConfigurationError("A tools report requires a behavioral evaluation result")
    index = index_result(behavior)
    stem = "tools" if path.name == "report.html" else path.stem + "-tools"
    tools_path, json_path = path.with_name(stem + ".html"), path.with_name(stem + ".json")
    evidence_links, evidence_index, runs = {}, [], []
    artifacts = {(artifact.uri, artifact.sha256): artifact for artifact in _artifacts(result)}
    uri_hashes = {}
    for artifact in artifacts.values():
        uri_hashes.setdefault(artifact.uri, set()).add(artifact.sha256)
    for artifact in artifacts.values():
        entry = {**plain(artifact), "bundled_uri": None, "error": None}
        try:
            if len(uri_hashes[artifact.uri]) > 1:
                raise ValueError("Conflicting evidence hashes for the same source location")
            data = _read(artifact)
            extension = ".json" if "json" in artifact.media_type else ".txt" if artifact.media_type.startswith("text/") else ".bin"
            relative = "evidence/" + artifact.sha256 + extension
            _write(path.parent / relative, data)
            evidence_links[artifact.uri] = relative
            entry["bundled_uri"] = relative
        except (OSError, ValueError) as exc:
            entry["error"] = str(exc)
        evidence_index.append(entry)
    specs = {spec.id for spec in behavior.suite.metrics if isinstance(spec.source, o.EvaluatorSource)
             and spec.source.ref.name == "agent-eval-flow.toolscore"}
    for number, run in enumerate(behavior.runs.runs):
        assignment = index.assignments[run.assignment_id]
        row = {"run_id": run.id, "candidate_id": assignment.candidate_id, "unit": plain(assignment.unit),
            "anchor": f"tools-run-{number}", "general_anchor": f"run-{number}", "receipts": [],
            "measurements": plain(tuple(m for m in index.measurements[run.id] if m.metric in specs))}
        receipt_refs = {}
        for measurement in index.measurements[run.id]:
            if measurement.metric in specs:
                for ref in measurement.evidence:
                    if ref.artifact.media_type == RECEIPT_TYPE:
                        receipt_refs[(ref.artifact.uri, ref.artifact.sha256)] = ref.artifact
        for artifact in receipt_refs.values():
            item = {"artifact": plain(artifact), "receipt": None, "error": None, "tools": []}
            try:
                receipt = _receipt(artifact, run)
                item["tools"] = _tool_rows(receipt)
                item["receipt"] = receipt
            except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
                item["error"] = str(exc)
            row["receipts"].append(item)
        runs.append(row)
    view = {"title": behavior.runs.plan.study_id, "result_id": behavior.id, "general_href": quote(path.name),
        "json_href": quote(json_path.name), "runs": runs, "summaries": _summaries(runs), "evidence": evidence_index}
    root = files("agent_eval_flow.reporting").joinpath("templates")
    environment = Environment(loader=FileSystemLoader(str(root)), autoescape=select_autoescape(default=True),
        trim_blocks=True, lstrip_blocks=True)
    environment.filters["pretty_json"] = _json
    environment.globals["evidence_link"] = lambda uri: evidence_links.get(uri)
    html = environment.get_template("tools_report.html.j2").render(view=view,
        css=root.joinpath("report.css").read_text(encoding="utf-8"))
    _write(tools_path, html.encode("utf-8"))
    _write(json_path, json.dumps({"schema_version": "1", **view}, ensure_ascii=False, indent=2,
                                allow_nan=False).encode("utf-8"))
    companions = {"href": quote(tools_path.name), "runs": {row["run_id"]: quote(tools_path.name) + "#" + row["anchor"] for row in runs},
                  "evidence": evidence_links}
    if isinstance(result, o.EvaluationResult):
        from .html import report
        return report(result, path, selection=selection, _companions=companions)
    from .assessment_html import report_assessment
    return report_assessment(result, path, policy=policy, _companions=companions)
