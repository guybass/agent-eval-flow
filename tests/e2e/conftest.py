"""No production implementation or live service is imported during collection."""
import importlib
import json
from pathlib import Path

import pytest

from tests.e2e.support import ScriptedBackend


@pytest.fixture
def live_profile(request):
    marker = request.node.get_closest_marker("profile")
    assert marker, "A live test must identify its runtime profile"
    name = marker.args[0]
    path = request.config.getoption("--aef-profile-config")
    if not path:
        pytest.fail("An explicitly selected live profile requires --aef-profile-config; no fallback is permitted")
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    profile = dict(config.get(name, {}))
    required = {"factory", "backend_ref", "settings", "deployment", "model"}
    assert required <= profile.keys(), f"Incomplete profile {name}: {required - profile.keys()}"
    assert {"name", "revision"} <= profile["backend_ref"].keys()
    assert profile["backend_ref"]["revision"] and profile["model"].get("id")
    assert not any(token in json.dumps(profile) for token in ("REPLACE_", "<required>")), "Fill the example profile before selecting it"
    if name.endswith("_gcp"):
        assert profile["deployment"].get("kind") == "gcp"
        assert profile["deployment"].get("project") and profile["deployment"].get("location")
    profile["id"] = name
    return profile


@pytest.fixture
def live_backend(live_profile, tmp_path):
    module_name, separator, function_name = live_profile["factory"].partition(":")
    assert separator and module_name and function_name, "factory must be module:callable"
    factory = getattr(importlib.import_module(module_name), function_name)
    backend = factory(live_profile, workspace=tmp_path / "live-workspace")
    assert backend.ref.name == live_profile["backend_ref"]["name"]
    assert backend.ref.revision == live_profile["backend_ref"]["revision"]
    assert not isinstance(backend, ScriptedBackend), "A scripted backend cannot satisfy a live profile"
    return backend
