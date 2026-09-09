"""Atomic publication of the explicitly separate assessment format."""
import os
from pathlib import Path
import tempfile

from .. import objects as o
from .assessment_codec import encode_assessment_root, decode_assessment_root


def save_assessment(root, path):
    payload = encode_assessment_root(root)
    path, temporary = Path(path), None
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="wb", prefix=".assessment-", suffix=".tmp",
                                         dir=path, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path / "manifest.json")
    except OSError as exc:
        raise o.StorageError(f"Cannot write assessment at {path}: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def load_assessment(path, expected_kind=None, *, expected_type=None):
    path = Path(path) / "manifest.json"
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise o.StorageError(f"Cannot read assessment at {path}: {exc}") from exc
    return decode_assessment_root(payload, expected_type or expected_kind)
