"""Pre-registered, seeded SimpleQA selection; the CSV must match its recorded hash."""
from __future__ import annotations

import csv
import hashlib
import random
from pathlib import Path


def select_questions(csv_path: Path, seed: int, n: int, expected_sha256: str) -> list[dict]:
    data = Path(csv_path).read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        raise ValueError(f"CSV sha256 {actual} differs from pre-registered {expected_sha256}")
    with Path(csv_path).open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    ids = random.Random(seed).sample(range(len(rows)), n)
    return [{"id": i, "question": rows[i]["problem"], "answer": rows[i]["answer"]} for i in ids]
