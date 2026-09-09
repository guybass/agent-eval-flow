"""Verify the test-first checkpoint without changing its historical manifest."""
import hashlib
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    checkpoint = json.loads((root / "tests/SHOWCASE_MANIFEST.json").read_text(encoding="utf-8"))
    changed = []
    for name, expected in checkpoint["sha256"].items():
        path = root / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            changed.append(name)
    if changed:
        raise SystemExit("Frozen acceptance files changed:\n" + "\n".join(changed))
    print(f"Verified {len(checkpoint['sha256'])} unchanged acceptance/fixture files")


if __name__ == "__main__":
    main()
