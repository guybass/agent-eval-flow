"""Keep the local schema correction explicit and verify it with native code."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from examples.openkritt_local_review import FIXTURE, LOCAL_WORKFLOW, build_study


@pytest.mark.parametrize("provenance_file", sorted(LOCAL_WORKFLOW.parent.glob("*.provenance.json")),
                         ids=lambda path: path.stem)
def test_all_local_workflow_provenance_hashes_match_original_bytes(provenance_file):
    root = Path(__file__).resolve().parents[2]
    provenance = json.loads(provenance_file.read_bytes())
    workflow = provenance_file.with_name(provenance_file.name.replace(".provenance.json", ".json"))
    assert hashlib.sha256(workflow.read_bytes()).hexdigest() == provenance["derived_sha256"]
    assert hashlib.sha256((root / provenance["source_fixture"]).read_bytes()).hexdigest() == provenance["source_sha256"]
    if "parent_variant" in provenance:
        parent = provenance_file.parent / provenance["parent_variant"]
        assert hashlib.sha256(parent.read_bytes()).hexdigest() == provenance["parent_sha256"]


@pytest.mark.parametrize("autocrlf", ["false", "true", "input"])
def test_git_checkout_preserves_byte_pinned_workflows(tmp_path, autocrlf):
    git = shutil.which("git")
    if git is None:
        pytest.skip("Git round-trip regression requires Git; direct provenance checks still run")
    root = Path(__file__).resolve().parents[2]
    repository = tmp_path / "repository"
    workflow_dir = repository / "examples/workflows"
    workflow_dir.mkdir(parents=True)
    (repository / ".gitattributes").write_bytes((root / ".gitattributes").read_bytes())
    for path in LOCAL_WORKFLOW.parent.glob("*.json"):
        (workflow_dir / path.name).write_bytes(path.read_bytes())
    # Exercise actual clean and checkout conversion in an isolated repository.
    # No commit, native runtime, or mutation of the project's Git index is needed.
    command = [git, "-c", f"core.autocrlf={autocrlf}", "-c", "core.safecrlf=false",
               "-c", f"core.attributesFile={tmp_path / 'no-global-attributes'}"]
    def run(*args):
        return subprocess.run([*command, *args], cwd=repository, check=True,
                              capture_output=True).stdout
    run("init", "--quiet")
    run("add", "--", ".gitattributes", "examples/workflows")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    run("checkout-index", "--all", f"--prefix={checkout.as_posix()}/")
    provenance_files = sorted(LOCAL_WORKFLOW.parent.glob("*.provenance.json"))
    assert provenance_files
    for provenance_file in provenance_files:
        expected = json.loads(provenance_file.read_bytes())["derived_sha256"]
        name = provenance_file.name.replace(".provenance.json", ".json")
        relative = f"examples/workflows/{name}"
        # Checking the index blob catches Windows checkout restoring CRLF and
        # masking incorrectly normalized stored bytes before Linux CI sees them.
        assert hashlib.sha256(run("show", f":{relative}")).hexdigest() == expected
        assert hashlib.sha256((checkout / relative).read_bytes()).hexdigest() == expected


def test_local_variant_preserves_prompts_topology_and_frozen_source():
    source = json.loads((FIXTURE / "workflow.json").read_bytes())
    derived = json.loads(LOCAL_WORKFLOW.read_bytes())
    provenance = json.loads(LOCAL_WORKFLOW.with_suffix(".provenance.json").read_bytes())
    assert hashlib.sha256((FIXTURE / "workflow.json").read_bytes()).hexdigest() == provenance["source_sha256"]
    assert hashlib.sha256(LOCAL_WORKFLOW.read_bytes()).hexdigest() == provenance["derived_sha256"]
    for old, new in zip(source["workflow"]["levels"], derived["workflow"]["levels"], strict=True):
        assert {key: value for key, value in old.items() if key != "outputFormat"} == {
            key: value for key, value in new.items() if key != "outputFormat"}
    keys = [key for level in derived["workflow"]["levels"] for key in level["outputFormat"]]
    assert len(keys) == len(set(keys))
    assert source["workflow"]["levels"][-1]["outputFormat"] == derived["workflow"]["levels"][-1]["outputFormat"]


def test_actual_pinned_native_validator_rejects_frozen_schema_accepts_local_variant():
    root = Path(__file__).resolve().parents[2]
    validator = root / "test-artifacts/openkritt-upstream/backend/src/lib/validation.js"
    node = shutil.which("node")
    if node is None or not validator.is_file():
        pytest.skip("Optional native schema regression needs Node and the pinned local OpenKritt checkout")
    source = """import {readFileSync} from 'node:fs';
const {validateWorkflow}=await import(process.argv[1]);
let rejected=false;
try { validateWorkflow(JSON.parse(readFileSync(process.argv[2])).workflow); }
catch(error) { rejected=error.status===422 && error.errors.some(row=>row.message.includes('used more than once')); }
if(!rejected) throw Error('Frozen schema no longer exposes the known duplicate-key incompatibility');
const valid=validateWorkflow(JSON.parse(readFileSync(process.argv[3])).workflow);
if(valid.levels.length!==3 || valid.levels.reduce((n,row)=>n+row.steps.length,0)!==4) throw Error('Topology changed');
console.log(JSON.stringify({frozenRejected:true,localAccepted:true,steps:4}));"""
    result = subprocess.run([node, "--input-type=module", "-e", source, validator.as_uri(),
        str(FIXTURE / "workflow.json"), str(LOCAL_WORKFLOW)], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == {"frozenRejected": True, "localAccepted": True, "steps": 4}


def test_actual_native_schema_builder_closes_objects_and_types_array_items():
    root = Path(__file__).resolve().parents[2]
    builder = root / "test-artifacts/openkritt-upstream/engine/open_kritt_engine/schema.py"
    if not builder.is_file():
        pytest.skip("Native schema regression needs the pinned local OpenKritt checkout")
    spec = importlib.util.spec_from_file_location("actual_openkritt_schema", builder)
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    def open_objects(schema):
        found = []
        if isinstance(schema, dict):
            if schema.get("type") == "object":
                if schema.get("additionalProperties") is not False:
                    found.append(schema)
                assert set(schema.get("required", [])) == set(schema.get("properties", {}))
            if schema.get("type") == "array":
                assert isinstance(schema.get("items"), dict) and schema["items"].get("type")
            for value in schema.values():
                found.extend(open_objects(value))
        elif isinstance(schema, list):
            for value in schema:
                found.extend(open_objects(value))
        return found
    previous = json.loads(LOCAL_WORKFLOW.with_name("openkritt_flaskr_local_v1.json").read_bytes())
    current = json.loads(LOCAL_WORKFLOW.read_bytes())
    assert sum(len(open_objects(native.output_schema(level["outputFormat"], level["multiOutput"])))
               for level in previous["workflow"]["levels"]) == 2
    for level in current["workflow"]["levels"]:
        schema = native.output_schema(level["outputFormat"], level["multiOutput"])
        assert not open_objects(schema)
        native.Draft202012Validator.check_schema(schema)
        # An empty explicit stub remains a valid native output for every stage.
        assert native.validate_payload({native.EXTRACTOR_HELPER_FIELD: True, "stub": True,
            "stub_explanation": "No supported finding in supplied source", "results": []}, schema, level["multiOutput"]) == []


def test_local_codex_preflight_rejects_known_unsupported_freeform_object_fields():
    config = {"backend_ref": {"name": "openkritt-local", "revision": "aef-0.4.0"},
              "settings": {"scan_options": {"harness": "codex"}}, "post_script_id": "1"}
    with pytest.raises(ValueError, match="free-form object"):
        build_study(config, workflow_file=LOCAL_WORKFLOW.with_name("openkritt_flaskr_local_v1.json"))
    study, evaluator = build_study(config)
    assert evaluator.ref.revision == "4" and study.suite.version == "4-complete-evidence-lineage"
