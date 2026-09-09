"""Regression from actual local scan 2: native repeats wrap, rather than flatten, results."""
from copy import deepcopy
import json
from pathlib import Path

from examples.openkritt_local_review import lineage_preserved, _repeat_json


SOURCE = Path(__file__).with_name("fixtures") / "openkritt_scan2_lineage.json"


def capture():
    return json.loads(SOURCE.read_text(encoding="utf-8"))


def replace_repeat(row, value):
    marker = "Results from earlier repeats:\n```json\n"
    before, remaining = row["prompt_filled"].split(marker)
    _, end = json.JSONDecoder().raw_decode(remaining)
    row["prompt_filled"] = before + marker + json.dumps(value, separators=(",", ":")) + remaining[end:]


def test_actual_cumulative_repeat_envelopes_preserve_complete_native_lineage():
    saved = capture()
    assert len(saved["metadata"]) == 8
    second_map = next(row for row in saved["metadata"] if row["step_id"] == 13 and row["repeat_run"] == 2)
    first_map = next(row for row in saved["metadata"] if row["step_id"] == 13 and row["repeat_run"] == 1)
    # This assertion reproduces why revision 2 incorrectly rejected real scan 2.
    assert json.dumps(first_map["output_json"]["results"], sort_keys=True, indent=2) not in second_map["prompt_filled"]
    assert _repeat_json(second_map["prompt_filled"]) == [
        {"repeat_run": 1, "result": first_map["output_json"]["results"][0]}]
    assert lineage_preserved(saved["metadata"], saved["results"], saved["workflow"])


def test_native_json_whitespace_and_key_order_do_not_change_lineage():
    saved = capture()
    for row in saved["metadata"]:
        if row["repeat_run"] > 1:
            replace_repeat(row, _repeat_json(row["prompt_filled"]))
    assert lineage_preserved(saved["metadata"], saved["results"], saved["workflow"])


def test_dropped_repeat_answer_or_wrong_repeat_identity_fails():
    for replacement in ([], [{"repeat_run": 99, "result": {"wrong": "answer"}}]):
        saved = capture()
        row = next(item for item in saved["metadata"] if item["step_id"] == 13 and item["repeat_run"] == 2)
        replace_repeat(row, replacement)
        assert not lineage_preserved(saved["metadata"], saved["results"], saved["workflow"])


def test_actual_parent_result_must_reach_both_branches_and_match_producer():
    saved = capture()
    parent = saved["results"][0]["json_answer"]
    branch = next(row for row in saved["metadata"] if row["step_id"] == 14 and row["repeat_run"] == 1)
    serialized = json.dumps([parent], sort_keys=True)
    assert serialized in branch["prompt_filled"]
    branch["prompt_filled"] = branch["prompt_filled"].replace(serialized, "[]", 1)
    assert not lineage_preserved(saved["metadata"], saved["results"], saved["workflow"])
    saved = capture()
    saved["results"][0]["prev_id"] = 987
    assert not lineage_preserved(saved["metadata"], saved["results"], saved["workflow"])
