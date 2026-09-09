"""Inspect one pinned upstream JSON fixture; never execute its contents.

Uses Python's standard library. Stores metadata only, not prompts or the patch.
Run directly to refresh swe_agent_fixture_probe.json beside this script.
"""

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen


COMMIT = "3ea751c087f32b16e039a2233dd6eefecef325d5"
FIXTURE_PATH = (
    "tests/test_data/trajectories/"
    "gpt4__swe-agent-test-repo__default_from_url__t-0.00__p-0.95__c-3.00__install-1/"
    "6e44b9__sweagenttestrepo-1c2844.traj"
)
URL = f"https://raw.githubusercontent.com/SWE-agent/SWE-agent/{COMMIT}/{FIXTURE_PATH}"
OUTPUT = Path(__file__).resolve().with_name("swe_agent_fixture_probe.json")


def main() -> None:
    request = Request(URL, headers={"User-Agent": "Agent-Eval-Flow-source-inspection"})
    with urlopen(request, timeout=30) as response:
        raw = response.read()
    fetched_at = datetime.now(timezone.utc).isoformat()
    fixture = json.loads(raw, parse_float=Decimal)
    info = fixture["info"]
    stats = info["model_stats"]
    steps = fixture["trajectory"]
    patch = info.get("submission")
    cost = stats["instance_cost"]
    assert isinstance(cost, (Decimal, int)) and not isinstance(cost, bool)
    for key in ("tokens_sent", "tokens_received", "api_calls"):
        assert isinstance(stats[key], int) and not isinstance(stats[key], bool)

    record = {
        "probe_kind": "saved_trajectory_metadata_inspection",
        "schema_version": "1",
        "source": {
            "repository": "https://github.com/SWE-agent/SWE-agent",
            "commit": COMMIT,
            "fixture_path": FIXTURE_PATH,
            "url": URL,
            "observed_at_utc": fetched_at,
            "byte_count": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "inspection": {
            "top_level_keys": list(fixture),
            "step_count": len(steps),
            "native_exit_status": info.get("exit_status"),
            "patch_present": isinstance(patch, str) and bool(patch.strip()),
            "native_model_stats": {
                "instance_cost_usd_decimal": str(cost),
                "decimal_representation": (
                    "Exact value of the upstream JSON number parsed with Decimal; "
                    "serialized as text to avoid another binary-float conversion. "
                    "This preserves the source value, not proof of billing precision."
                ),
                "tokens_sent": stats["tokens_sent"],
                "tokens_received": stats["tokens_received"],
                "api_calls": stats["api_calls"],
                "scope": "Native model_stats for this saved trajectory only.",
                "billing_verified": False,
            },
            "per_step_execution_time_present": all("execution_time" in step for step in steps),
            "root_wall_clock_duration_seconds": {
                "status": "unknown",
                "value": None,
                "reason": (
                    "The fixture has no root start/end timestamps. Per-step command "
                    "execution times do not establish end-to-end wall-clock duration."
                ),
            },
            "independent_task_grade": {
                "status": "unknown",
                "value": None,
                "reason": (
                    "No independent evaluator report was fetched or executed. "
                    "A submitted patch does not establish task resolution."
                ),
            },
            "full_system_cost_usd": {
                "status": "unknown",
                "value": None,
                "reason": (
                    "Model statistics alone do not establish complete accounting for "
                    "infrastructure, human work, evaluation, or uncaptured model calls."
                ),
            },
        },
        "limits": [
            "Historical upstream test fixture, not a current performance measurement.",
            "No agent, model, tool action, patch, or upstream Python code was executed.",
            "No task prompt, message history, trajectory content, or patch text is stored here.",
            "This probe is not an adapter or an evaluation result.",
        ],
    }
    OUTPUT.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    saved = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert saved == record
    assert saved["source"]["sha256"] == hashlib.sha256(raw).hexdigest()
    print(json.dumps({"output": str(OUTPUT), "sha256": saved["source"]["sha256"], "step_count": len(steps)}))


if __name__ == "__main__":
    main()
