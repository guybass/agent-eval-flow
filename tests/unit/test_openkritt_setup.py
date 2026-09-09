"""Native configuration shape checks; no service or agent is contacted."""
from types import SimpleNamespace

import pytest

from examples.integrations import openkritt_setup as setup


def catalog():
    return {"providers": [{"provider": "codex", "status": "ready", "models": [
        {"id": "returned-default", "isDefault": True, "thinkingEfforts": ["medium"]},
        {"id": "returned-luna", "thinkingEfforts": ["low", "medium"]}]}]}


def test_bootstrap_selects_only_a_returned_model_and_checks_explicit_choice():
    model, effort, reason = setup.choose_model(catalog())
    assert model["id"] == "returned-luna" and effort == "low"
    assert "not a cost claim" in reason
    assert setup.choose_model(catalog(), "returned-default")[0]["id"] == "returned-default"
    with pytest.raises(ValueError, match="absent"):
        setup.choose_model(catalog(), "invented-model")
    with pytest.raises(ValueError, match="configured Codex"):
        setup.choose_model({"providers": []})


@pytest.mark.parametrize("existing", [False, True])
def test_bootstrap_creates_only_owned_postscript_and_retains_exact_policy(tmp_path, monkeypatch, existing):
    monkeypatch.setattr(setup.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(stdout=setup.UPSTREAM_REVISION.encode()))
    class API:
        base_url = "http://127.0.0.1:13002"
        receipts = tmp_path / "receipts"
        calls = []
        def request(self, method, path, payload=None):
            self.calls.append((method, path, payload))
            if path == "/api/model-catalog":
                return catalog()
            if path == "/api/severity-rankers":
                return [{"id": "1", "name": "Unrelated default", "content": "Native rules"}]
            if path == "/api/post-scripts" and method == "GET":
                return [{**setup.POST_SCRIPT, "id": "42"}] if existing else []
            assert path == "/api/post-scripts" and method == "POST"
            assert payload == setup.POST_SCRIPT
            return {**payload, "id": "42"}
    api = API()
    config = setup.prepare(api, upstream_dir=tmp_path, engine_data_dir=tmp_path,
                           local_repos_dir=tmp_path, wait_models_s=0)
    assert config["settings"]["scan_options"]["severity_ranker"] == setup.SOURCE_ONLY_SEVERITY_POLICY
    assert config["settings"]["scan_options"]["model"] == "returned-luna"
    assert config["settings"]["scan_options"]["thinking_effort"] == "low"
    assert config["post_script_id"] == "42"
    assert sum(method == "POST" for method, *_ in api.calls) == (0 if existing else 1)
    assert not any("scans" in path for _, path, _ in api.calls)
