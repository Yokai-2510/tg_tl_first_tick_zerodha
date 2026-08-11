"""
The daily cycle — the bug that stopped the bot trading a second day.

The scheduler used to be a ONE-SHOT: it walked BOOT -> ... -> EOD and then stopped
forever, because nothing transitioned EOD -> IDLE and nothing reset `_entered`. In
production it sat in EOD through an entire trading day and never ran.

These tests drive several consecutive days through `tick()`.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from backend.core.enums import Phase
from backend.core.timeutil import IST
from backend.engine.scheduler import Scheduler

# 2026-08-12 is a Wednesday, 08-13 Thursday, 08-14 Friday,
# 08-15 Saturday, 08-16 Sunday, 08-17 Monday.
WED, THU, FRI, SAT, SUN, MON = 12, 13, 14, 15, 16, 17


def at(day: int, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, 8, day, h, m, tzinfo=IST)


def run_day(s: Scheduler, day: int, hooks: list[str] | None = None) -> list[Phase]:
    """Drive one full day minute-by-minute at the times that matter."""
    seen: list[Phase] = []
    for t in ("08:00", "08:46", "08:56", "09:01", "09:10", "09:10",
              "09:14", "09:16", "15:29", "16:00", "20:00"):
        s.tick(at(day, t))
        if not seen or seen[-1] is not s.phase:
            seen.append(s.phase)
    return seen


@pytest.fixture
def sched(config_obj):
    s = Scheduler(config_obj)
    s.hooks = {p: (lambda: None) for p in Phase}
    return s


def test_a_single_day_reaches_trading_then_returns_to_idle(sched):
    seen = run_day(sched, WED)
    assert Phase.PHASE_1 in seen
    assert Phase.TRADING in seen
    assert Phase.EOD in seen
    assert sched.phase is Phase.IDLE, "a finished day must return to IDLE"


def test_three_consecutive_days_each_run(sched):
    """The regression: day 2 and day 3 must also trade."""
    for day in (WED, THU, FRI):
        seen = run_day(sched, day)
        assert Phase.PHASE_1 in seen, f"day {day} never ran Phase 1"
        assert Phase.TRADING in seen, f"day {day} never reached TRADING"
        assert sched.phase is Phase.IDLE


def test_hooks_rerun_every_day(sched):
    """`_entered` must be cleared, or phase1/connect_feed run once and never again."""
    calls: dict[str, int] = {}
    for p in (Phase.PHASE_1, Phase.FEED_LIVE, Phase.ARMING, Phase.TRADING):
        sched.hooks[p] = (lambda name=str(p): calls.__setitem__(name, calls.get(name, 0) + 1))
    for day in (WED, THU, FRI):
        run_day(sched, day)
    for p in ("PHASE_1", "FEED_LIVE", "ARMING", "TRADING"):
        assert calls.get(p) == 3, f"{p} hook ran {calls.get(p)} times, expected 3"


def test_reset_hook_fires_once_per_day(sched):
    n = {"count": 0}
    sched.on_reset = lambda: n.__setitem__("count", n["count"] + 1)
    for day in (WED, THU, FRI):
        run_day(sched, day)
    # once at each day's EOD; the same-day repeats must not re-fire it
    assert n["count"] == 3, f"reset fired {n['count']} times"


def test_idle_in_the_evening_does_not_start_a_second_session(sched):
    """The trap: after EOD -> IDLE, phase1_time has 'passed' — it must not restart."""
    run_day(sched, WED)
    assert sched.phase is Phase.IDLE
    for t in ("16:00", "18:00", "21:00", "23:59"):
        sched.tick(at(WED, t))
        assert sched.phase is Phase.IDLE, f"restarted the same evening at {t}"


def test_weekend_is_skipped(sched):
    run_day(sched, FRI)
    assert sched.phase is Phase.IDLE

    for day in (SAT, SUN):
        seen = run_day(sched, day)
        assert Phase.PHASE_1 not in seen, f"ran a session on day {day} (weekend)"
        assert sched.phase is Phase.IDLE

    seen = run_day(sched, MON)
    assert Phase.TRADING in seen, "Monday must run after a weekend"


def test_a_failed_premarket_still_resets_for_tomorrow(sched):
    def boom():
        raise RuntimeError("auth down")
    sched.hooks[Phase.PHASE_1] = boom

    sched.tick(at(WED, "08:46"))
    assert sched.phase is Phase.PHASE_1_FAIL

    # the next tick clears the failure back to IDLE so tomorrow is not blocked
    sched.tick(at(WED, "09:00"))
    assert sched.phase is Phase.IDLE

    sched.hooks[Phase.PHASE_1] = lambda: None
    seen = run_day(sched, THU)
    assert Phase.TRADING in seen, "a failed day must not block the next one"


def test_starting_mid_session_does_not_skip_to_trading(sched):
    """A restart at 09:20 must still walk the phases, not jump straight in."""
    sched.tick(at(WED, "09:20"))
    assert sched.phase is Phase.PHASE_1
    sched.tick(at(WED, "09:20"))
    assert sched.phase is Phase.FEED_LIVE


def test_session_date_is_reported(sched):
    assert sched.status()["session_date"] is None
    sched.tick(at(WED, "08:46"))
    assert sched.status()["session_date"] == "2026-08-12"


def test_feed_and_arming_state_is_cleared_between_days():
    """Yesterday's `fired` latches must not survive, or nothing can trade."""
    from backend.engine.feed import Feed
    from backend.engine.trigger import TriggerConfig
    from .conftest import make_instrument, make_tick

    feed = Feed(trigger_cfg=TriggerConfig())
    inst = make_instrument(token=1)
    feed.arm([inst], {1: 100.0})
    feed.phase = Phase.TRADING
    feed.enable_entries(fire_after_ns=0, deadline_ns=10**18, session_prefix="d1_")
    feed.on_tick_batch([make_tick(token=1, ltp=110.0)], recv_ns=1)
    assert feed.intent_q.qsize() == 1
    assert feed.armed_view()[0]["fired"] is True

    feed.reset()
    assert feed.stats()["armed"] == 0
    assert feed.entries_enabled is False

    # re-arm for a new day: the instrument must be able to fire again
    feed.arm([inst], {1: 200.0})
    feed.phase = Phase.TRADING
    feed.enable_entries(fire_after_ns=0, deadline_ns=10**18, session_prefix="d2_")
    assert feed.armed_view()[0]["fired"] is False
    assert feed.armed_view()[0]["ref_price"] == 200.0, "reference must be the new day's"
    feed.on_tick_batch([make_tick(token=1, ltp=210.0)], recv_ns=1)
    assert feed.intent_q.qsize() == 2, "day 2 must be able to fire"
