"""Offline checks for the DeerFlow evidence-loss study tooling (no model calls)."""
import csv
import hashlib

import pytest

from examples.deerflow_study.questions import select_questions


def _csv(tmp_path, rows):
    path = tmp_path / "q.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["metadata", "problem", "answer"])
        writer.writeheader()
        for question, answer in rows:
            writer.writerow({"metadata": "{}", "problem": question, "answer": answer})
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_selection_is_seeded_and_hash_checked(tmp_path):
    path, digest = _csv(tmp_path, [(f"Q{i}?", f"A{i}") for i in range(50)])
    first = select_questions(path, seed=20260928, n=5, expected_sha256=digest)
    again = select_questions(path, seed=20260928, n=5, expected_sha256=digest)
    assert first == again and len(first) == 5
    assert all(set(q) == {"id", "question", "answer"} for q in first)
    with pytest.raises(ValueError, match="sha256"):
        select_questions(path, seed=20260928, n=5, expected_sha256="0" * 64)
