"""Native session-goal completion sets must survive final-result capture."""
from dataclasses import dataclass
import json

import pytest

from agent_eval_flow.adapters.opensre import native_json


def test_native_goal_completion_set_survives_nested_goal_result():
    # The pinned SessionGoal.completed field is frozenset[int]. The live
    # investigation completed but this field originally prevented all output.
    @dataclass(slots=True)
    class Goal:
        condition: str
        completed: frozenset[int]

    @dataclass(slots=True)
    class GoalResult:
        goal: Goal
        last_result: dict
        turn_count: int

    result = GoalResult(Goal("Deliver the report", frozenset({2, 0})),
                        {"assistant_response_text": "Report saved", "action_result": {"accounting_status": "completed"}}, 1)
    value = native_json(result)
    assert json.loads(json.dumps(value))["goal"]["completed"] == [0, 2]
    assert value["last_result"] == result.last_result
    assert native_json(frozenset()) == []
    with pytest.raises(TypeError, match="Unsupported native event value"):
        native_json({object()})
