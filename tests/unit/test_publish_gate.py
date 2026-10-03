"""Exercise the actual publishing gate with retained GitHub API response shapes."""
import io
import json
from pathlib import Path
import subprocess
import textwrap
from urllib.request import Request

import pytest


@pytest.fixture
def release_gate(tmp_path, monkeypatch):
    workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/publish.yml").read_text(encoding="utf-8")
    script = textwrap.dedent(workflow.split("python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0])
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "agent-eval-flow"\nversion = "0.6.0"\n')
    for key, value in {
        "GITHUB_REPOSITORY": "guybass/agent-eval-flow", "GITHUB_REF_NAME": "v0.6.0",
        "GITHUB_REF": "refs/tags/v0.6.0", "GITHUB_SHA": "a" * 40,
        "GITHUB_EVENT_NAME": "release", "GH_TOKEN": "test-token",
    }.items():
        monkeypatch.setenv(key, value)

    def checkout(command, *, text):
        assert command == ["git", "rev-parse", "HEAD"] and text
        return "a" * 40 + "\n"

    monkeypatch.setattr(subprocess, "check_output", checkout)
    jobs = [{"name": f"{job} ({system}, {version})", "status": "completed", "conclusion": "success"}
            for job in ("offline", "toolscore")
            for system in ("ubuntu-latest", "windows-latest")
            for version in ("3.11", "3.12", "3.13")]

    def run(rows, *, conclusion="success"):
        responses = [
            {"tag_name": "v0.6.0", "draft": False, "prerelease": False, "published_at": "2026-10-03"},
            {"sha": "a" * 40}, {"status": "identical"},
            {"workflow_runs": [{"id": 10, "head_sha": "a" * 40, "head_branch": "main", "event": "push",
                                "status": "completed", "conclusion": conclusion,
                                "html_url": "https://github.com/guybass/agent-eval-flow/actions/runs/10"}]},
            {"total_count": len(rows), "jobs": rows},
        ]

        def urlopen(request, *, timeout):
            assert isinstance(request, Request) and timeout == 30
            assert request.full_url.startswith("https://api.github.com/repos/guybass/agent-eval-flow/")
            return io.BytesIO(json.dumps(responses.pop(0)).encode())

        monkeypatch.setattr("urllib.request.urlopen", urlopen)
        exec(compile(script, "publish.yml", "exec"), {})
        assert not responses

    return run, jobs


def test_publish_accepts_complete_offline_and_toolscore_matrices(release_gate):
    run, jobs = release_gate
    run(jobs)


def test_publish_rejects_only_the_old_six_offline_jobs(release_gate):
    run, jobs = release_gate
    with pytest.raises(SystemExit, match="All twelve"):
        run(jobs[:6])


@pytest.mark.parametrize("outcome", ["failure", "skipped", "cancelled"])
def test_publish_requires_every_toolscore_job_to_pass(release_gate, outcome):
    run, jobs = release_gate
    jobs[-1]["conclusion"] = outcome
    with pytest.raises(SystemExit, match="All twelve"):
        run(jobs)


def test_publish_rejects_duplicate_jobs_hiding_missing_coverage(release_gate):
    run, jobs = release_gate
    jobs[-1] = dict(jobs[0])
    with pytest.raises(SystemExit, match="All twelve"):
        run(jobs)


def test_publish_rejects_an_unsuccessful_main_run(release_gate):
    run, jobs = release_gate
    with pytest.raises(SystemExit, match="must finish successfully"):
        run(jobs, conclusion="failure")
