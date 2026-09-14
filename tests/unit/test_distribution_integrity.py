"""Exercise publication exclusions against real wheel and source archives."""
import hashlib
import io
import json
import tarfile
import zipfile

import pytest

from scripts.verify_distribution import verify_distributions


@pytest.fixture
def packages(tmp_path):
    files = {
        "pyproject.toml": b'[project]\nname = "agent-eval-flow"\nversion = "1.0.0"\n',
        "src/agent_eval_flow/__init__.py": b'VALUE = 1\n',
        "tests/e2e/fixtures/sample/HDFS_2k.log": b'Public pinned log fixture\n',
        "examples/profiles.local.example.json": b'{"settings": {}}\n',
        ".env.example": b'EXAMPLE_SETTING=\n',
        "tests/README.md": b'# Current public test instructions\n',
    }
    fixture = "tests/e2e/fixtures/sample/HDFS_2k.log"
    files["tests/fixtures_manifest.json"] = json.dumps({
        "schema_version": 1, "sha256": {fixture: hashlib.sha256(files[fixture]).hexdigest()},
    }).encode()
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    distribution = tmp_path / "dist"
    distribution.mkdir()
    stem = "agent_eval_flow-1.0.0"

    def write(*, extra_archive=None, extra_path=None, fixture_damage=None):
        wheel_files = {"agent_eval_flow/__init__.py": files["src/agent_eval_flow/__init__.py"]}
        source_files = dict(files)
        if fixture_damage == "missing":
            source_files.pop(fixture)
        elif fixture_damage == "changed":
            source_files[fixture] = b'Changed bytes\n'
        if extra_path:
            target = wheel_files if extra_archive == "wheel" else source_files
            target[extra_path] = b'Unpublishable test placeholder\n'
        with zipfile.ZipFile(distribution / f"{stem}-py3-none-any.whl", "w") as archive:
            for name, content in wheel_files.items():
                archive.writestr(name, content)
        with tarfile.open(distribution / f"{stem}.tar.gz", "w:gz") as archive:
            for name, content in source_files.items():
                member = tarfile.TarInfo(f"{stem}/{name}")
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
        return tmp_path

    return write


def test_public_log_fixtures_and_configuration_templates_remain_publishable(packages):
    assert verify_distributions(packages()) == 1


@pytest.mark.parametrize("archive", ["wheel", "source"])
@pytest.mark.parametrize("private_path", [
    "docs/private.md", "nested/doc/draft.md", "nested\\docs\\private.md",
    "nested/test-artifacts/run.json", "nested/demo-output/result.json", "nested/.report-site/index.html",
    "nested/.codex/config.toml",
    "config/.env", "config/.env.production", "config/.pypirc", "config/_netrc",
    "config/id_ed25519", "config/service.pem", "config/profiles.local.json",
    "tests/ACCEPTANCE_MANIFEST.json", "tests/SHOWCASE_MANIFEST.json", "tests/BASELINE.md",
    "tests/e2e/BASELINE.md", "tests/e2e/README.md", "tests/e2e/PROFILES.md",
    "tests/e2e/SHOWCASE.md", "tests/integrations/PROFILES.md",
])
def test_private_files_are_rejected_in_both_distribution_formats(packages, archive, private_path):
    root = packages(extra_archive=archive, extra_path=private_path)
    with pytest.raises(SystemExit, match="Forbidden packaged path"):
        verify_distributions(root)


@pytest.mark.parametrize("archive", ["wheel", "source"])
@pytest.mark.parametrize("unsafe_path", ["../outside.txt", "C:\\local\\output.json"])
def test_archive_paths_cannot_escape_the_package(packages, archive, unsafe_path):
    root = packages(extra_archive=archive, extra_path=unsafe_path)
    with pytest.raises(SystemExit, match="Unsafe path"):
        verify_distributions(root)


@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_migrated_manifest_still_rejects_missing_or_changed_fixture_bytes(packages, damage):
    with pytest.raises(SystemExit, match="Source distribution (?:is missing|changed)"):
        verify_distributions(packages(fixture_damage=damage))
