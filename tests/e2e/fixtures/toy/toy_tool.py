"""Owned deterministic tool used by both scripted and live E2E missions."""
import json
import sys


def execute(request):
    return {"tool": "fixture.echo", "task_id": request["task_id"],
            "message": str(request["text"]), "items": [str(request["text"])]}


def main():
    json.dump(execute(json.load(sys.stdin)), sys.stdout)


if __name__ == "__main__":
    main()
