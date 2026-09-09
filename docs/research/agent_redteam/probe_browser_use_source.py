"""Inspect pinned upstream source with ast; never import or execute that source.

Uses only Python's standard library. Re-run to refresh browser_use_source_probe.json.
This checks source structure, not runtime behavior or an Agent Eval Flow adapter.
"""

import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen


COMMIT = "e25ab65e699af3031a1f2d348526de2844be0e89"
SOURCE_PATH = "browser_use/agent/views.py"
REPOSITORY = "https://github.com/browser-use/browser-use"
URL = f"https://raw.githubusercontent.com/browser-use/browser-use/{COMMIT}/{SOURCE_PATH}"
OUTPUT = Path(__file__).resolve().with_name("browser_use_source_probe.json")


def body_without_docstring(node):
    body = node.body
    if body and isinstance(body[0], ast.Expr):
        value = body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return body[1:]
    return body


def expression_is(node, expression):
    """Match a small expression, not a copied upstream method implementation."""
    return node is not None and ast.unparse(node) == ast.unparse(ast.parse(expression, mode="eval").body)


def nodes(node, kind):
    return [child for child in ast.walk(node) if isinstance(child, kind)]


def dumps_only_history(method):
    body = body_without_docstring(method)
    if len(body) != 1 or not isinstance(body[0], ast.Return):
        return False
    value = body[0].value
    return (
        isinstance(value, ast.Dict) and len(value.keys) == 1
        and expression_is(value.keys[0], "'history'")
        and isinstance(value.values[0], ast.ListComp)
        and expression_is(value.values[0].generators[0].iter, "self.history")
    )


def saves_model_dump(method):
    assignments = [
        node for node in nodes(method, ast.Assign)
        if any(expression_is(target, "data") for target in node.targets)
    ]
    dumps = [node for node in nodes(method, ast.Call) if expression_is(node.func, "json.dump")]
    return (
        len(assignments) == 1 and len(dumps) == 1
        and isinstance(assignments[0].value, ast.Call)
        and expression_is(assignments[0].value.func, "self.model_dump")
        and any(kw.arg == "sensitive_data" and expression_is(kw.value, "sensitive_data")
                for kw in assignments[0].value.keywords)
        and len(dumps[0].args) >= 1 and expression_is(dumps[0].args[0], "data")
        and assignments[0].lineno < dumps[0].lineno
    )


def reports_final_success(method):
    assignments = [
        node for node in nodes(method, ast.Assign)
        if any(expression_is(target, "last_result") for target in node.targets)
        and expression_is(node.value, "self.history[-1].result[-1]")
    ]
    guards = [node for node in nodes(method, ast.If) if expression_is(node.test, "last_result.is_done is True")]
    returns = nodes(method, ast.Return)
    return (
        len(assignments) == 1 and len(guards) == 1
        and assignments[0].lineno < guards[0].lineno
        and any(isinstance(node, ast.Return) and expression_is(node.value, "last_result.success")
                for node in guards[0].body)
        and len(returns) == 2
        and isinstance(body_without_docstring(method)[-1], ast.Return)
        and expression_is(body_without_docstring(method)[-1].value, "None")
    )


def sums_step_durations(method):
    loops = [node for node in nodes(method, ast.For) if expression_is(node.iter, "self.history")]
    if len(loops) != 1 or not expression_is(loops[0].target, "h"):
        return False
    guards = [node for node in loops[0].body if isinstance(node, ast.If) and expression_is(node.test, "h.metadata")]
    additions = nodes(method, ast.AugAssign)
    initializers = [node for node in nodes(method, ast.Assign)
                    if any(expression_is(target, "total") for target in node.targets)]
    return (
        len(guards) == 1 and len(additions) == 1 and additions[0] in guards[0].body
        and isinstance(additions[0].op, ast.Add)
        and expression_is(additions[0].target, "total")
        and expression_is(additions[0].value, "h.metadata.duration_seconds")
        and len(initializers) == 1 and expression_is(initializers[0].value, "0.0")
        and isinstance(body_without_docstring(method)[-1], ast.Return)
        and expression_is(body_without_docstring(method)[-1].value, "total")
    )


def location(node):
    return {
        "start_line": node.lineno,
        "end_line": node.end_lineno,
        "source_url": f"{REPOSITORY}/blob/{COMMIT}/{SOURCE_PATH}#L{node.lineno}-L{node.end_lineno}",
    }


def main():
    request = Request(URL, headers={"User-Agent": "Agent-Eval-Flow-source-inspection"})
    with urlopen(request, timeout=30) as response:
        raw = response.read()
        fetched_url = response.geturl()
    observed_at = datetime.now(timezone.utc).isoformat()
    tree = ast.parse(raw.decode("utf-8"), filename=SOURCE_PATH)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AgentHistoryList")
    fields = {
        node.target.id: node for node in cls.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    methods = {node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)}
    inspected_methods = ("model_dump", "save_to_file", "is_successful", "total_duration_seconds")
    checks = {
        "history_annotation_is_list_of_AgentHistory": ast.unparse(fields["history"].annotation) == "list[AgentHistory]",
        "usage_annotation_is_optional_UsageSummary": ast.unparse(fields["usage"].annotation) == "UsageSummary | None",
        "usage_defaults_to_None": isinstance(fields["usage"].value, ast.Constant) and fields["usage"].value.value is None,
        "model_dump_returns_only_history_key": dumps_only_history(methods["model_dump"]),
        "save_to_file_passes_model_dump_data_to_json_dump": saves_model_dump(methods["save_to_file"]),
        "is_successful_returns_last_result_success_under_done_guard": reports_final_success(methods["is_successful"]),
        "total_duration_sums_present_step_metadata": sums_step_durations(methods["total_duration_seconds"]),
    }
    if not all(checks.values()):
        raise ValueError(f"Pinned source did not match expected structures: {checks}")
    record = {
        "schema_version": "1",
        "probe_kind": "pinned_python_source_ast_inspection",
        "source": {
            "repository": REPOSITORY,
            "commit": COMMIT,
            "source_path": SOURCE_PATH,
            "url": URL,
            "fetched_url": fetched_url,
            "observed_at_utc": observed_at,
            "byte_count": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "inspection": {
            "class": "AgentHistoryList",
            "field_declarations": {
                name: {
                    **location(fields[name]),
                    "annotation": ast.unparse(fields[name].annotation),
                    "default": ast.unparse(fields[name].value) if fields[name].value is not None else "<required>",
                } for name in ("history", "usage")
            },
            "methods": {name: location(methods[name]) for name in inspected_methods},
            "checks": checks,
            "verified_source_facts": [
                "AgentHistoryList declares history: list[AgentHistory] and usage: UsageSummary | None = None.",
                "Its custom model_dump directly returns a dictionary with exactly one top-level key: history. It does not serialize the top-level usage field.",
                "save_to_file obtains data from self.model_dump(sensitive_data=sensitive_data), then passes that data to json.dump.",
                "is_successful returns the last history item's last result.success only if that result.is_done is True; otherwise it returns None.",
                "total_duration_seconds starts at 0.0 and adds h.metadata.duration_seconds for history items with metadata. Items without metadata contribute nothing.",
            ],
        },
        "integration_implications": [
            "An importer of this save_to_file format cannot recover the omitted top-level usage field from that field; absence is not evidence of zero cost.",
            "Preserve native is_successful separately from an independent task grade. This method reads a reported result flag; it does not itself perform independent evaluation.",
            "Label total_duration_seconds as a sum of recorded step durations. These statements alone do not establish assignment end-to-end wall time or completeness when metadata is missing.",
        ],
        "limits": [
            "Static AST inspection of one file at one commit. No upstream module was imported or executed.",
            "No browser, agent, model, runtime test, saved trajectory normalization, or Agent Eval Flow adapter was run.",
            "The probe does not evaluate inherited serializers, model_dump_json, other export paths, subclass overrides, monkeypatching, or separate usage/cost artifacts.",
            "No claims about the correctness of reported success, billing completeness, or actual task outcomes follow from these source checks.",
        ],
    }
    OUTPUT.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "sha256": record["source"]["sha256"], "checks_passed": len(checks)}))


if __name__ == "__main__":
    main()
