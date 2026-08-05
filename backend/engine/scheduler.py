"""
Phase state machine — the daily lifecycle.

    BOOT -> PHASE_1 -> FEED_LIVE -> PREOPEN -> SETTLEMENT -> ARMING
         -> FROZEN -> TRADING -> MANAGING -> EOD -> IDLE -> (next day)

Transitions are validated: an illegal transition raises rather than silently
putting the system in a state where entries could fire at the wrong time.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Callable

from ..core.enums import ENTRY_PHASES, Phase
from ..core.timeutil import epoch_us, has_passed, now_ist

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
        self.history: list[dict] = []
        self.last_error: str | None = None

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

        target = None
        if self._phase is Phase.BOOT and has_passed(s.phase1_time, now):
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
            self.transition(target)
        return self._phase

    def reset_for_new_day(self) -> None:
        with self._lock:
            self._phase = Phase.IDLE
            self._entered.clear()
            self.history.clear()
            self.last_error = None

    def status(self) -> dict:
        s = self.cfg.schedule
        return {
            "phase": str(self._phase),
            "entries_allowed": self.entries_allowed,
            "last_error": self.last_error,
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
