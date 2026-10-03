"""Grading must survive a host clock stepping backwards (NTP or WSL resync).

Before the fix, an evaluation activity recorded ``ended_at=datetime.now()``; when
the clock stepped back during grading, ``ended_at`` fell before ``started_at``,
record validation rejected the activity, and the completed grading was lost.
"""
from datetime import datetime, timedelta, timezone

from agent_eval_flow.evaluation import engine


def test_activity_end_never_precedes_start_after_clock_step():
    started = datetime.now(timezone.utc) + timedelta(seconds=30)  # the clock later steps back
    assert engine.activity_end(started) == started


def test_activity_end_is_the_current_time_otherwise():
    earlier = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert engine.activity_end(earlier) > earlier
