"""Shared public-package fixtures; collection never imports the future library."""
import importlib

import pytest

from tests.e2e.support import ScriptedBackend


PROFILE_NAMES = {"codex_local", "claude_local", "vertex_gcp", "opensre_gcp", "openkritt_gcp",
                 "harbor_native", "skillevaluator_import", "nat_batch"}


def pytest_addoption(parser):
    group = parser.getgroup("agent-eval-flow")
    group.addoption("--aef-live", action="append", default=[], choices=sorted(PROFILE_NAMES))
    group.addoption("--aef-profile-config", default=None,
                    help="Local JSON configuration; never commit credentials")


def pytest_collection_modifyitems(config, items):
    selected = set(config.getoption("--aef-live"))
    for item in items:
        marker = item.get_closest_marker("profile")
        if marker and marker.args[0] not in selected:
            item.add_marker(pytest.mark.skip(reason=f"Live profile {marker.args[0]} was not selected"))


@pytest.fixture
def api():
    try:
        return importlib.import_module("agent_eval_flow")
    except ModuleNotFoundError as exc:
        if exc.name != "agent_eval_flow":
            raise
        pytest.fail(
            "RED: agent_eval_flow is not implemented/installed yet. "
            "These tests require the real public library; no stub or importorskip fallback.",
            pytrace=False,
        )


@pytest.fixture
def toy_backend(api, tmp_path):
    return ScriptedBackend(api, tmp_path / "toy-processes")
