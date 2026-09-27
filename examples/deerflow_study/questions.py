"""Pre-registered, seeded question selection; the file must match its recorded hash.

Rows are filtered (optional answer-length cap) before seeded sampling, so the
filter is part of the pre-registered rule. Ids are zero-based row indexes of the
file, as in Guy's GPT Researcher case study.
"""
from __future__ import annotations

import csv
import hashlib
import random
from pathlib import Path


def select_questions(csv_path: Path, seed: int, n: int, expected_sha256: str, *,
                     question_col: str = "problem", answer_col: str = "answer",
                     delimiter: str = ",", max_answer_chars: int | None = None) -> list[dict]:
    data = Path(csv_path).read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        raise ValueError(f"CSV sha256 {actual} differs from pre-registered {expected_sha256}")
    with Path(csv_path).open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f, delimiter=delimiter))
    eligible = [i for i, row in enumerate(rows)
                if max_answer_chars is None or len(row[answer_col].strip()) <= max_answer_chars]
    ids = random.Random(seed).sample(eligible, n)
    return [{"id": i, "question": rows[i][question_col], "answer": rows[i][answer_col].strip()} for i in ids]
