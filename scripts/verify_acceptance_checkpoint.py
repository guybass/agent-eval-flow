"""Verify the integrity of preserved test sources and public fixtures."""
import hashlib
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    checkpoint = json.loads((root / "tests/fixtures_manifest.json").read_text(encoding="utf-8"))
    if checkpoint.get("schema_version") != 1:
        raise SystemExit("Unsupported fixture integrity manifest schema")
    changed = []
    for name, expected in checkpoint["sha256"].items():
        path = root / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            changed.append(name)
    if changed:
        raise SystemExit("Preserved test or fixture files are missing or changed:\n" + "\n".join(changed))
    print(f"Verified {len(checkpoint['sha256'])} unchanged test/fixture files")


if __name__ == "__main__":
    main()
