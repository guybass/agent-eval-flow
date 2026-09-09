"""An actual read/reproduction tool for the live agent, not an agent simulator.

The future profile exposes three named tools backed by this command. The model
chooses calls; this program executes them and records actual inputs/results.
It has no dependency on Agent Eval Flow and generates no agent/native events.
"""
import argparse
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import sys
from uuid import uuid4

sys.dont_write_bytecode = True


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def within(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Tool path is outside the declared fixture")
    return path


def load_dependency(root):
    """Load the pinned Python fallback even if another version is installed."""
    path = root / "downloaded/src/markupsafe/__init__.py"
    name = "aef_fixture_markupsafe"
    spec = importlib.util.spec_from_file_location(
        name, path, submodule_search_locations=[str(path.parent)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package
    spec.loader.exec_module(package)
    assert Path(inspect.getfile(package)).resolve() == path.resolve()
    assert Path(inspect.getfile(package._escape_inner)).resolve() == path.with_name("_native.py").resolve()
    return package


def dispatch(root, action, arguments):
    manifest = json.loads((root / "SOURCE_MANIFEST.json").read_text(encoding="utf-8"))
    for row in manifest["files"]:
        assert digest(root / "downloaded" / row["path"]) == row["sha256"], row["path"]
    if action == "inventory":
        return {"upstream_revision": manifest["revision"], "files": manifest["files"],
                "incident": json.loads((root / "cases.json").read_text(encoding="utf-8"))}
    if action == "read_source":
        relative = arguments["path"]
        path = within(root / "downloaded", relative)
        if relative not in {row["path"] for row in manifest["files"]}:
            raise ValueError("Only declared downloaded files may be read")
        lines = path.read_text(encoding="utf-8").splitlines()
        start, end = arguments["start_line"], arguments["end_line"]
        if not 1 <= start <= end <= len(lines) or end - start >= 120:
            raise ValueError("Read a valid span of at most 120 lines")
        return {"path": relative, "sha256": digest(path), "start_line": start,
                "end_line": end, "lines": lines[start - 1:end]}
    if action != "run_cases":
        raise ValueError("Unknown investigation tool action")
    package = load_dependency(root)
    incident = json.loads((root / "cases.json").read_text(encoding="utf-8"))
    selected = arguments["case_ids"]
    known = {row["id"] for row in incident["cases"]}
    if not selected or len(selected) != len(set(selected)) or not set(selected) <= known:
        raise ValueError("Select distinct known case IDs")
    rows = []
    for row in incident["cases"]:
        if row["id"] not in selected:
            continue
        value, operation = row["value"], row["operation"]
        if operation == "escape":
            output = package.escape(value)
        elif operation == "escape_markup":
            output = package.escape(package.Markup(value))
        elif operation == "format":
            output = package.Markup(row["template"]).format(value=value)
        elif operation == "escape_twice":
            output = package.escape(package.escape(value))
        else:
            raise ValueError(operation)
        rows.append({"case_id": row["id"], "operation": operation, "input": row,
                     "observed": str(output), "output_type": type(output).__name__})
    hints = []
    for function in (package.escape, package.Markup.format, package._escape_inner):
        lines, start = inspect.getsourcelines(function)
        hints.append({"path": Path(inspect.getfile(function)).relative_to(root / "downloaded").as_posix(),
                      "start_line": start, "end_line": start + len(lines) - 1})
    return {"case_results": rows, "implementation": "downloaded-python-fallback",
            "source_hints": hints, "cases_sha256": digest(root / "cases.json")}


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--receipts", type=Path, required=True)
    parser.add_argument("--invocation", required=True)
    parser.add_argument("--nonce", required=True)
    options = parser.parse_args()
    payload = json.load(sys.stdin)
    root = options.fixture.resolve()
    result = dispatch(root, payload["action"], payload["arguments"])
    receipt = {"schema_version": "aef-workflow-tool/1", "receipt_id": uuid4().hex,
               "invocation_id": options.invocation, "nonce": options.nonce,
               "action": payload["action"], "arguments": payload["arguments"], "result": result,
               "source_manifest_sha256": digest(root / "SOURCE_MANIFEST.json"),
               "tool_sha256": digest(__file__)}
    options.receipts.mkdir(parents=True, exist_ok=True)
    destination = options.receipts / (receipt["receipt_id"] + ".json")
    destination.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    main()
