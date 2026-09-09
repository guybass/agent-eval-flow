"""Check that distributions contain current code and exact acceptance fixtures."""
import hashlib
import json
from pathlib import Path
import tarfile
import tomllib
import zipfile


def main():
    root = Path(__file__).resolve().parents[1]
    distribution = root / "dist"
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    stem = project["name"].replace("-", "_") + "-" + project["version"]
    wheel = distribution / (stem + "-py3-none-any.whl")
    source = distribution / (stem + ".tar.gz")
    with zipfile.ZipFile(wheel) as archive:
        for path in (root / "src/agent_eval_flow").rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            relative = path.relative_to(root / "src").as_posix()
            if archive.read(relative) != path.read_bytes():
                raise SystemExit(f"Wheel differs from current implementation: {relative}")
    checkpoint = json.loads((root / "tests/SHOWCASE_MANIFEST.json").read_text(encoding="utf-8"))
    with tarfile.open(source, "r:gz") as archive:
        prefix = archive.getmembers()[0].name.split("/")[0]
        members = set(archive.getnames())
        for name, digest in checkpoint["sha256"].items():
            member_name = prefix + "/" + name
            if member_name not in members:
                raise SystemExit(f"Source distribution is missing an acceptance fixture: {name}")
            member = archive.extractfile(member_name)
            if member is None or hashlib.sha256(member.read()).hexdigest() != digest:
                raise SystemExit(f"Source distribution changed an acceptance fixture: {name}")
    print("Wheel matches source; source distribution preserves all 90 acceptance/fixture hashes")


if __name__ == "__main__":
    main()
