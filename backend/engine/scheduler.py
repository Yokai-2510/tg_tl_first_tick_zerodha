"""
Phase state machine — the daily lifecycle.

    BOOT -> PHASE_1 -> FEED_LIVE -> PREOPEN -> SETTLEMENT -> ARMING
         -> FROZEN -> TRADING -> MANAGING -> EOD -> IDLE -> (next day)

Transitions are validated: an illegal transition raises rather than silently
putting the system in a state where entries could fire at the wrong time.
"""

from __future__ import annotations

import threading
from datetime import date, datetime
from typing import Callable

from ..core.enums import ENTRY_PHASES, Phase
from ..core.timeutil import epoch_us, has_passed, is_weekend, now_ist

#: Legal successors for each phase.
TRANSITIONS: dict[Phase, frozenset[Phase]] = {
    Phase.BOOT: frozenset({Phase.PHASE_1, Phase.IDLE}),
    Phase.PHASE_1: frozenset({Phase.FEED_LIVE, Phase.PHASE_1_FAIL, Phase.IDLE}),
    Phase.PHASE_1_FAIL: frozenset({Phase.IDLE, Phase.PHASE_1}),
    Phase.FEED_LIVE: frozenset({Phase.PREOPEN, Phase.IDLE}),
    Phase.PREOPEN: frozenset({Phase.SETTLEMENT, Phase.IDLE}),
    Phase.SETTLEMENT: frozenset({Phase.ARMING, Phase.IDLE}),
    Phase.ARMING: frozenset({Phase.FROZEN, Phase.IDLE}),
    Phase.FROZEN: frozenset({Phase.TRADING, Phase.IDLE}),
    Phase.TRADING: frozenset({Phase.MANAGING, Phase.EOD, Phase.IDLE}),
    Phase.MANAGING: frozenset({Phase.EOD, Phase.IDLE}),
    Phase.EOD: frozenset({Phase.IDLE}),
    Phase.IDLE: frozenset({Phase.PHASE_1, Phase.BOOT}),
}


class IllegalTransition(RuntimeError):
    pass


class Scheduler:
    """Owns the current phase and the times that drive it.

    Hooks are plain callables invoked once when their phase is entered. A hook
    that raises moves the system to PHASE_1_FAIL rather than leaving it in a
    half-started state.
    """

    def __init__(self, cfg, *, log=None, recorder=None):
        self.cfg = cfg
        self.log = log
        self.recorder = recorder
        self._phase = Phase.BOOT
        self._lock = threading.RLock()
        self._entered: set[Phase] = set()
        self.hooks: dict[Phase, Callable[[], None]] = {}
        #: Called once per day, before a new session starts, so the Application can
        #: tear down yesterday's feed and state. Without this the scheduler was a
        #: ONE-SHOT: it walked BOOT -> ... -> EOD and then stopped forever.
        self.on_reset: Callable[[], None] | None = None
        self.history: list[dict] = []
        self.last_error: str | None = None
        #: Date of the session that has already begun. Guards IDLE -> PHASE_1 so
        #: reaching IDLE in the evening cannot immediately start another session.
        self._session_date: date | None = None

    # -- phase -------------------------------------------------------------

    @property
    def phase(self) -> Phase:
        return self._phase

    @property
    def entries_allowed(self) -> bool:
        return self._phase in ENTRY_PHASES

    def can_transition(self, target: Phase) -> bool:
        return target in TRANSITIONS.get(self._phase, frozenset())

    def transition(self, target: Phase, *, force: bool = False) -> None:
        with self._lock:
            if target == self._phase:
                return
            if not force and not self.can_transition(target):
                raise IllegalTransition(f"{self._phase} -> {target} is not allowed")
            previous, self._phase = self._phase, target
            self.history.append({"from": str(previous), "to": str(target),
                                 "at_us": epoch_us()})
        self._say("info", f"PHASE {previous} -> {target}")
        if self.recorder is not None:
            self.recorder.event("PHASE", {"from": str(previous), "to": str(target)})

        hook = self.hooks.get(target)
        if hook is not None and target not in self._entered:
            self._entered.add(target)
            try:
                hook()
            except Exception as exc:
                self.last_error = f"{target} hook failed: {exc}"
                self._say("error", self.last_error)
                if target in (Phase.PHASE_1, Phase.FEED_LIVE, Phase.ARMING):
                    with self._lock:
                        self._phase = Phase.PHASE_1_FAIL

    # -- time-driven advance ----------------------------------------------

    def tick(self, now: datetime | None = None) -> Phase:
        """Advance the phase if the wall clock says it is time. Idempotent."""
        now = now or now_ist()
        s = self.cfg.schedule

        # A finished (or aborted) day returns to IDLE so tomorrow can start.
        if self._phase in (Phase.EOD, Phase.PHASE_1_FAIL):
            self.reset_for_new_day(keep_session_date=True)
            return self._phase

        # New calendar day: only clear the guard. The teardown already happened at
        # EOD, so running it again would fire on_reset twice per day and rebuild
        # the recorder for nothing.
        if self._session_date is not None and self._session_date != now.date():
            with self._lock:
                self._session_date = None

        target = None
        if self._phase in (Phase.BOOT, Phase.IDLE) and self._may_start(now):
            target = Phase.PHASE_1
        elif self._phase is Phase.PHASE_1 and has_passed(s.feed_connect_time, now):
            target = Phase.FEED_LIVE
        elif self._phase is Phase.FEED_LIVE and has_passed(s.preopen_start, now):
            target = Phase.PREOPEN
        elif self._phase is Phase.PREOPEN and has_passed(s.settlement_snapshot, now):
            target = Phase.SETTLEMENT
        elif self._phase is Phase.SETTLEMENT and has_passed(s.wave2_subscribe_time, now):
            target = Phase.ARMING
        elif self._phase is Phase.ARMING and has_passed(s.manual_cutoff, now):
            target = Phase.FROZEN
        elif self._phase is Phase.FROZEN and has_passed(s.trading_start, now):
            target = Phase.TRADING
        elif self._phase is Phase.TRADING and has_passed(s.eod_time, now):
            target = Phase.EOD
        elif self._phase is Phase.MANAGING and has_passed(s.eod_time, now):
            target = Phase.EOD

        if target is not None:
            if target is Phase.PHASE_1:
                self._session_date = now.date()
            self.transition(target)
        return self._phase

    def _may_start(self, now: datetime) -> bool:
        """True when a fresh session is due.

        Three conditions, all necessary:
          * the pre-market time has passed today
          * no session has already run today (otherwise reaching IDLE in the
            evening would immediately start a second one)
          * it is a weekday, unless auto_continue_daily is off

        NOTE: weekends only. Exchange holidays are not detected — on a holiday the
        system authenticates and subscribes but no ticks arrive, so nothing fires.
        """
        if not has_passed(self.cfg.schedule.phase1_time, now):
            return False
        if self._session_date == now.date():
            return False
        if is_weekend(now.date()):
            self._say_once("weekend", "info",
                           f"{now.date()} is a weekend — no session today")
            return False
        return True

    def reset_for_new_day(self, *, keep_session_date: bool = False) -> None:
        """Return to IDLE and let the Application tear down yesterday's state."""
        with self._lock:
            was = self._phase
            self._phase = Phase.IDLE
            self._entered.clear()
            self.history.clear()
            self.last_error = None
            if not keep_session_date:
                self._session_date = None

        if was is not Phase.IDLE:
            self._say("info", f"PHASE {was} -> IDLE (session reset)")
            if self.recorder is not None:
                self.recorder.event("PHASE", {"from": str(was), "to": "IDLE",
                                              "reason": "session_reset"})
        if self.on_reset is not None:
            try:
                self.on_reset()
            except Exception as exc:
                self._say("error", f"session reset hook failed: {exc}")

    def _say_once(self, key: str, level: str, msg: str) -> None:
        """Log a recurring condition once per day, not once per tick."""
        stamp = f"{key}:{now_ist().date()}"
        if getattr(self, "_said", None) is None:
            self._said: set[str] = set()
        if stamp not in self._said:
            self._said.add(stamp)
            self._say(level, msg)

    def status(self) -> dict:
        s = self.cfg.schedule
        return {
            "phase": str(self._phase),
            "entries_allowed": self.entries_allowed,
            "last_error": self.last_error,
            "session_date": str(self._session_date) if self._session_date else None,
            "schedule": {
                "phase1_time": s.phase1_time,
                "feed_connect_time": s.feed_connect_time,
                "settlement_snapshot": s.settlement_snapshot,
                "manual_cutoff": s.manual_cutoff,
                "trading_start": s.trading_start,
                "eod_time": s.eod_time,
            },
            "history": self.history[-12:],
        }

    def _say(self, level: str, msg: str) -> None:
        if self.log is not None:
            getattr(self.log, level, self.log.info)(msg)


__all__ = ["Scheduler", "TRANSITIONS", "IllegalTransition"]
