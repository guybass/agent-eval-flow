"""Owned observability tools: data and call audit, never an agent/model loop.

The prepared profile exposes these through the actual OpenSRE ToolProvider. A
native execution hook supplies tool_call_id; it must not pre-call the tools.
"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from threading import RLock
import uuid

TOOL_NAMES = ("fixture_incident_open", "fixture_logs_search", "fixture_metrics_query",
              "fixture_topology_describe", "fixture_changes_list", "fixture_report_write")


def verify_inputs(root=None):
    """Offline integrity/preflight check; no API package, model or network."""
    root = Path(root) if root is not None else Path(__file__).parent
    manifest = json.loads((root / "SOURCES.json").read_text())
    assert manifest["schema_version"] == "aef-fixture-sources/1"
    paths = set()
    for entry in manifest["sources"]:
        assert entry["path"] not in paths
        paths.add(entry["path"])
        target = (root / entry["path"]).resolve()
        assert target.is_relative_to(root.resolve())
        content = target.read_bytes()
        assert len(content) == entry["bytes"], entry["path"]
        assert hashlib.sha256(content).hexdigest() == entry["sha256"], entry["path"]
        if "url" in entry:
            assert entry["revision"] in entry["url"]
    assert {"context.json", "incidents.json", "upstream/HDFS_2k.log", "upstream/LOGHUB_LICENSE"} <= paths
    rows = read_log_rows(root)
    assert len(rows) == 2000 and len({row["component"] for row in rows}) > 1
    context = json.loads((root / "context.json").read_text())
    window = context["window"]
    warnings = [row for row in rows if row["date"] == window["date"] and row["level"] == "WARN"
                and window["start_time"] <= row["time"] <= window["end_time"]]
    assert len(warnings) >= 8
    assert len({host for row in rows for host in row["hosts"]}) > 10
    assert len(context["changes"]) > 1
    assert all(item["provenance"] == "synthetic exercise context" for item in context["changes"])
    assert json.loads((root / "incidents.json").read_text())[0]["incident_id"] == context["incident_id"]
    return rows


def read_log_rows(root):
    rows = []
    for line_id, line in enumerate((Path(root) / "upstream/HDFS_2k.log").read_text().splitlines(), 1):
        date, clock, thread, level, component, message = line.split(" ", 5)
        rows.append({"line_id": line_id, "date": date, "time": clock, "thread": thread,
                     "level": level, "component": component.rstrip(":"), "message": message,
                     "raw": line, "sha256": hashlib.sha256(line.encode()).hexdigest(),
                     "block_ids": re.findall(r"blk_-?\d+", message),
                     "hosts": sorted(set(re.findall(r"\b\d+\.\d+\.\d+\.\d+\b", message)))})
    return rows


class IncidentStore:
    """Fresh per-assignment store; independent call receipts survive download."""

    def __init__(self, root, work, *, invocation_id):
        self.root, self.work, self.invocation_id = Path(root), Path(work), invocation_id
        self.work.mkdir(parents=True, exist_ok=False)
        self.context = json.loads((self.root / "context.json").read_text())
        self.rows = read_log_rows(self.root)
        self.snapshot = uuid.uuid4().hex
        self.cursors, self.receipts = {}, {}
        self.opened = False
        self._lock = RLock()

    def call(self, tool_name, arguments, *, tool_call_id, loop_id):
        with self._lock:
            assert tool_name in TOOL_NAMES
            native_key = (loop_id, tool_call_id)
            assert loop_id and tool_call_id and native_key not in self.receipts
            started = datetime.now(timezone.utc).isoformat()
            receipt_id = uuid.uuid4().hex
            if tool_name == "fixture_incident_open":
                assert arguments["incident_id"] == self.context["incident_id"]
                self.opened = True
                body = {**self.context, "source_line_count": len(self.rows)}
            else:
                assert self.opened and arguments["snapshot_id"] == self.snapshot
                if tool_name == "fixture_logs_search":
                    body = self._logs(arguments, receipt_id)
                elif tool_name == "fixture_metrics_query":
                    body = self._metrics()
                elif tool_name == "fixture_topology_describe":
                    peers = Counter(host for row in self.rows for host in row["hosts"])
                    body = {"nodes": [{"address": host, "sample_mentions": count}
                                      for host, count in peers.most_common()],
                            "components": sorted({row["component"] for row in self.rows}),
                            "provenance": "extracted from sample; ownership/health unknown"}
                elif tool_name == "fixture_changes_list":
                    body = {"changes": self.context["changes"], "provenance": "synthetic exercise context"}
                else:
                    body = self._report(arguments["report"])
            result = {"receipt_id": receipt_id, "invocation_id": self.invocation_id,
                      "snapshot_id": self.snapshot, **body}
            row = {"schema_version": "aef-incident-tool-receipt/1", "sequence": len(self.receipts),
                   "invocation_id": self.invocation_id, "loop_id": loop_id, "tool_call_id": tool_call_id,
                   "tool_name": tool_name, "arguments": arguments, "result": result,
                   "started_at": started, "ended_at": datetime.now(timezone.utc).isoformat()}
            self.receipts[native_key] = row
            with (self.work / "tool-audit.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, sort_keys=True) + "\n")
            return result

    def _logs(self, arguments, receipt_id):
        cursor, previous = arguments.get("cursor"), None
        if cursor:
            query, offset, previous = self.cursors[cursor]
            assert not arguments.get("query"), "Continuation uses the server-held query"
        else:
            query, offset = arguments.get("query", {}), 0
        window = self.context["window"]
        matches = [row for row in self.rows if row["date"] == window["date"]
                   and window["start_time"] <= row["time"] <= window["end_time"]
                   and (not query.get("level") or row["level"] == query["level"])
                   and (not query.get("contains") or query["contains"] in row["raw"])]
        page, next_cursor = matches[offset:offset + 4], None
        if offset + 4 < len(matches):
            next_cursor = uuid.uuid4().hex
            self.cursors[next_cursor] = (query, offset + 4, receipt_id)
        return {"query": query, "offset": offset, "rows": page, "total_matches": len(matches),
                "next_cursor": next_cursor, "previous_receipt_id": previous,
                "provenance": "unmodified Loghub sample lines", "source": "upstream/HDFS_2k.log"}

    def _metrics(self):
        date = self.context["window"]["date"]
        selected = [row for row in self.rows if row["date"] == date]
        buckets = Counter((row["time"][:2], row["level"]) for row in selected)
        series = [{"source_date": date, "hour": hour, "level": level, "count": count}
                  for (hour, level), count in sorted(buckets.items())]
        return {"metric": "sample_log_count", "series": series, "sample_size": len(selected),
                "source_line_ids": [row["line_id"] for row in selected],
                "provenance": "computed from downloaded sample; not a production rate"}

    def _report(self, report):
        assert report["incident_id"] == self.context["incident_id"]
        assert isinstance(report["summary"], str) and report["summary"].strip()
        for key in ("hypotheses", "timeline", "evidence", "limitations", "next_actions"):
            assert isinstance(report[key], list) and report[key], key
        observed = {row["result"]["receipt_id"] for row in self.receipts.values()}
        assert all(item["receipt_id"] in observed for item in report["evidence"])
        content = json.dumps(report, indent=2).encode()
        (self.work / "investigation.json").write_bytes(content)
        return {"report_file": "investigation.json", "sha256": hashlib.sha256(content).hexdigest(),
                "evidence_count": len(report["evidence"])}


def tool_schemas():
    """JSON schemas for real native registration; no fabricated native fields."""
    snapshot = {"type": "string", "description": "snapshot_id from fixture_incident_open"}
    report = {"type": "object", "properties": {
        "incident_id": {"type": "string"}, "summary": {"type": "string"},
        "hypotheses": {"type": "array", "items": {"type": "object"}},
        "timeline": {"type": "array", "items": {"type": "object"}},
        "evidence": {"type": "array", "items": {"type": "object", "properties": {
            "receipt_id": {"type": "string"}, "note": {"type": "string"}},
            "required": ["receipt_id", "note"]}},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "next_actions": {"type": "array", "items": {"type": "string"}}},
        "required": ["incident_id", "summary", "hypotheses", "timeline", "evidence", "limitations", "next_actions"]}
    properties = {name: {"snapshot_id": snapshot} for name in TOOL_NAMES}
    properties[TOOL_NAMES[0]] = {"incident_id": {"type": "string"}}
    properties[TOOL_NAMES[1]].update({"query": {"type": "object", "properties": {
        "level": {"type": "string"}, "contains": {"type": "string"}}, "additionalProperties": False},
        "cursor": {"type": "string"}})
    properties[TOOL_NAMES[-1]]["report"] = report
    descriptions = ("Open incident and obtain a fresh snapshot token.",
        "Search incident WARN logs; follow next_cursor for another page, or search a discovered block/host.",
        "Read hourly log-level counts computed from the downloaded sample.",
        "Read components and host addresses extracted from the sample.",
        "Read explicitly synthetic exercise deployment/configuration history.",
        "Save the incident report, citing returned receipt IDs as evidence.")
    return [{"name": name, "description": description, "input_schema": {
        "type": "object", "properties": properties[name], "required": ["incident_id"] if index == 0
        else (["snapshot_id", "report"] if index == 5 else ["snapshot_id"]),
        "additionalProperties": False}} for index, (name, description) in enumerate(zip(TOOL_NAMES, descriptions))]
