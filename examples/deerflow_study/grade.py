"""Deterministic grading: a normalized gold-alias match; empty answers go to review."""
from .detector import aliases, normalize


def grade(answer: str, gold: str) -> str:
    if not answer or not answer.strip():
        return "review"
    text = f" {normalize(answer)} "
    return "correct" if any(f" {a} " in text for a in aliases(gold)) else "incorrect"
