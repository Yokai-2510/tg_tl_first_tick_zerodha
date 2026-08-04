"""
Time handling. Every schedule comparison in the system goes through here.

BUILD_SPEC R10: all schedule times are IST. `datetime.now()` (naive, server
local) must never be used for a schedule decision.

Two clocks, never mixed:
  * wall clock  — `now_ist()`, `epoch_us()`  : schedules, records, display
  * monotonic   — `mono_ns()`                : latency math only
"""

from __future__ import annotations

import time
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import numpy as np

IST = ZoneInfo("Asia/Kolkata")


# --------------------------------------------------------------------------
# Wall clock
# --------------------------------------------------------------------------

def now_ist() -> datetime:
    """Timezone-aware current time in IST."""
    return datetime.now(IST)


def today_ist() -> date:
    return now_ist().date()


def epoch_us(dt: datetime | None = None) -> int:
    """Epoch microseconds (UTC-based, timezone-correct)."""
    return int((dt or now_ist()).timestamp() * 1_000_000)


def to_epoch_us(value: datetime | int | float | None) -> int | None:
    """Normalise a broker timestamp to epoch microseconds.

    pykiteconnect returns `exchange_timestamp` / `last_trade_time` as **naive
    `datetime` objects in IST**, not epochs (BUILD_SPEC §11). Passing one
    straight through as an int is a real and easy bug; this is the only
    conversion point.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value.replace(tzinfo=IST) if value.tzinfo is None else value
        return int(dt.timestamp() * 1_000_000)
    if isinstance(value, (int, float)):
        v = float(value)
        # Heuristic on magnitude: s -> us, ms -> us, already us.
        if v > 1e17:            # nanoseconds
            return int(v / 1_000)
        if v > 1e14:            # microseconds
            return int(v)
        if v > 1e11:            # milliseconds
            return int(v * 1_000)
        return int(v * 1_000_000)   # seconds
    raise TypeError(f"unsupported timestamp type: {type(value)!r}")


# --------------------------------------------------------------------------
# Monotonic clock — latency only
# --------------------------------------------------------------------------

def mono_ns() -> int:
    """Monotonic nanoseconds. Use for durations; never for wall time."""
    return time.perf_counter_ns()


# --------------------------------------------------------------------------
# Schedule helpers
# --------------------------------------------------------------------------

def parse_hhmmss(value: str) -> dtime:
    """Parse 'HH:MM:SS' or 'HH:MM' into a `time`. Raises on anything else."""
    parts = value.strip().split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"invalid time {value!r}; expected HH:MM[:SS]")
    try:
        h, m = int(parts[0]), int(parts[1])
        s = int(parts[2]) if len(parts) == 3 else 0
    except ValueError as exc:
        raise ValueError(f"invalid time {value!r}: {exc}") from None
    if not (0 <= h < 24 and 0 <= m < 60 and 0 <= s < 60):
        raise ValueError(f"time out of range: {value!r}")
    return dtime(h, m, s)


def today_at(value: str | dtime, ref: datetime | None = None) -> datetime:
    """Today's date at the given IST time, timezone-aware."""
    t = parse_hhmmss(value) if isinstance(value, str) else value
    base = (ref or now_ist()).date()
    return datetime.combine(base, t, tzinfo=IST)


def has_passed(value: str | dtime, ref: datetime | None = None) -> bool:
    """True if the given IST time-of-day has already passed today."""
    return (ref or now_ist()) >= today_at(value, ref)


def seconds_until(value: str | dtime, ref: datetime | None = None) -> float:
    """Seconds from `ref` until the given IST time today (negative if passed)."""
    return (today_at(value, ref) - (ref or now_ist())).total_seconds()


# --------------------------------------------------------------------------
# Trading-day arithmetic
# --------------------------------------------------------------------------

def trading_days_between(start: date, end: date) -> int:
    """Weekdays from `start` (inclusive) to `end` (exclusive).

    Used by the expiry roll. Mon->Tue = 1, Fri->Mon = 1 (weekend skipped).

    LIMITATION: weekends only — exchange holidays are NOT accounted for.
    Phase 1 cross-checks against the market calendar and warns on divergence.
    """
    return int(np.busday_count(start, end))


def add_trading_days(start: date, n: int) -> date:
    """`start` shifted by n weekdays (holidays not considered)."""
    return np.busday_offset(start, n, roll="forward").astype("O")


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5


__all__ = [
    "IST", "now_ist", "today_ist", "epoch_us", "to_epoch_us", "mono_ns",
    "parse_hhmmss", "today_at", "has_passed", "seconds_until",
    "trading_days_between", "add_trading_days", "is_weekend",
]
