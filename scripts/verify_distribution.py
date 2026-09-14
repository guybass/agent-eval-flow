"""Check package integrity and exclude private documents and local artifacts.

Exclusions inspect archive paths, not file contents. This is not a secret scanner.
"""
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import tarfile
import tomllib
import zipfile


PRIVATE_DIRECTORIES = {
    "doc", "docs", "test-artifacts", "demo-output", ".report-site", "build", "dist",
    ".venv", ".tmp", ".pytest_cache", "__pycache__", ".git", ".codex", ".agents",
}
PRIVATE_FILENAMES = {
    ".env", ".pypirc", ".netrc", "_netrc", "profiles.local.json", ".e2e-local.json",
    "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
}
HISTORICAL_PATHS = {
    tuple(name.lower().split("/")) for name in (
        "tests/ACCEPTANCE_MANIFEST.json", "tests/SHOWCASE_MANIFEST.json",
        "tests/BASELINE.md", "tests/e2e/BASELINE.md", "tests/e2e/README.md",
        "tests/e2e/PROFILES.md", "tests/e2e/SHOWCASE.md", "tests/integrations/PROFILES.md",
    )
}


def verify_archive_paths(names, archive_name):
    """Apply the same publication exclusions to wheel and source archive names."""
    for name in names:
        path = PurePosixPath(name.replace("\\", "/"))
        if path.is_absolute() or any(PureWindowsPath(part).drive for part in path.parts) or ".." in path.parts:
            raise SystemExit(f"Unsafe path in {archive_name}: {name!r}")
        parts = tuple(part.lower() for part in path.parts)
        if not parts:
            continue
        filename = parts[-1]
        private = (
            any(part in PRIVATE_DIRECTORIES for part in parts)
            or filename in PRIVATE_FILENAMES
            or (filename.startswith(".env.") and filename != ".env.example")
            or filename.endswith((".pem", ".key", ".p12", ".pfx"))
            or any(parts[-len(old):] == old for old in HISTORICAL_PATHS)
        )
        if private:
            raise SystemExit(f"Forbidden packaged path in {archive_name}: {name!r}")


def verify_distributions(root):
    root = Path(root)
    distribution = root / "dist"
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    stem = project["name"].replace("-", "_") + "-" + project["version"]
    wheel = distribution / (stem + "-py3-none-any.whl")
    source = distribution / (stem + ".tar.gz")
    with zipfile.ZipFile(wheel) as archive:
        verify_archive_paths(archive.namelist(), wheel.name)
        for path in (root / "src/agent_eval_flow").rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            relative = path.relative_to(root / "src").as_posix()
            if archive.read(relative) != path.read_bytes():
                raise SystemExit(f"Wheel differs from current implementation: {relative}")
    checkpoint = json.loads((root / "tests/fixtures_manifest.json").read_text(encoding="utf-8"))
    if checkpoint.get("schema_version") != 1:
        raise SystemExit("Unsupported fixture integrity manifest schema")
    with tarfile.open(source, "r:gz") as archive:
        verify_archive_paths(archive.getnames(), source.name)
        prefix = stem
        members = set(archive.getnames())
        for name, digest in checkpoint["sha256"].items():
            member_name = prefix + "/" + name
            if member_name not in members:
                raise SystemExit(f"Source distribution is missing a preserved test/fixture: {name}")
            member = archive.extractfile(member_name)
            if member is None or hashlib.sha256(member.read()).hexdigest() != digest:
                raise SystemExit(f"Source distribution changed a preserved test/fixture: {name}")
    return len(checkpoint["sha256"])


def main():
    count = verify_distributions(Path(__file__).resolve().parents[1])
    print(f"Wheel matches source; source distribution preserves all {count} test/fixture hashes; "
          "both archives exclude private documentation and local artifacts")


if __name__ == "__main__":
    main()
