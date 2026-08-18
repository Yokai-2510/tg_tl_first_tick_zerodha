"""
Instrument master: expiry resolution, strike selection, option chains.

State-free. Every function takes the data it needs; nothing is cached in
module globals. `load_master` is the only function that touches the broker,
and it takes the client as a parameter.

Spec: BUILD_SPEC §3 (expiry) and §5 (strikes).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Sequence

from ...core.enums import InstrumentKind, Moneyness, OptionType, SubscribeMode
from ...core.models import Instrument
from ...core.symbols import is_index, normalise, option_exchange
from ...core.timeutil import trading_days_between

_OPT_SEGMENTS = {"NFO-OPT", "BFO-OPT"}


# --------------------------------------------------------------------------
# Expiry — BUILD_SPEC §3
# --------------------------------------------------------------------------

def _monthly_expiries(expiries: Sequence[date]) -> list[date]:
    """The last expiry in each calendar month -- the monthly contract.

    Derived rather than hardcoded to a weekday, because the monthly expiry moves
    for holidays and NSE has changed the day more than once.
    """
    last_of_month: dict[tuple[int, int], date] = {}
    for e in expiries:
        key = (e.year, e.month)
        if key not in last_of_month or e > last_of_month[key]:
            last_of_month[key] = e
    return sorted(last_of_month.values())


def resolve_expiry(
    symbol: str,
    expiries: Sequence[date],
    today: date,
    *,
    roll_enabled: bool = True,
    buffer_trading_days: int = 1,
    rule: str = "nearest",
) -> date | None:
    """Pick the expiry to trade for `symbol`.

    Stock options are physically settled and Zerodha blocks fresh MIS buys in
    the last two trading days before expiry, so stocks roll to the next
    expiry inside that window. Index options are cash-settled and never roll.

    Args:
        expiries: all known expiries for this underlying (any order).
        today:    reference date.
        buffer_trading_days: roll when this many trading days or fewer remain.

    Returns:
        The chosen expiry, or None if `expiries` is empty.
    """
    if not expiries:
        return None

    ordered = sorted(set(expiries))
    future = [e for e in ordered if e >= today]
    if not future:
        return ordered[-1]                    # everything expired; caller decides

    # `rule` chooses the CHAIN; the physical-settlement roll below then decides
    # whether that chain is too close to expiry to enter safely. They are
    # independent: asking for `monthly` still rolls on its last two days.
    rule = (rule or "nearest").lower()
    if rule == "next" and len(future) > 1:
        future = future[1:]
    elif rule == "monthly":
        monthly = _monthly_expiries(future)
        if monthly:
            future = monthly

    if is_index(symbol) or not roll_enabled:
        return future[0]
    if len(future) < 2:
        return future[0]                      # nothing to roll to

    if trading_days_between(today, future[0]) <= buffer_trading_days:
        return future[1]
    return future[0]


# --------------------------------------------------------------------------
# Strikes — BUILD_SPEC §5
# --------------------------------------------------------------------------

def find_atm_index(strikes: Sequence[float], spot: float) -> int:
    """Index of the strike nearest `spot`. Ties resolve to the LOWER strike."""
    if not strikes:
        raise ValueError("no strikes")
    return min(range(len(strikes)), key=lambda i: (abs(strikes[i] - spot), strikes[i]))


def pick_strike(
    strikes: Sequence[float],
    spot: float,
    option_type: str,
    moneyness: str,
    offset: int,
) -> float:
    """Select one strike by moneyness bucket and offset.

    Moneyness is direction-dependent:
        CE: ITM = lower strikes,  OTM = higher strikes
        PE: ITM = higher strikes, OTM = lower strikes

    `offset` 0 means the first strike in that bucket. Out-of-range indices are
    CLAMPED to the ends of the chain — never wrapped.
    """
    if not strikes:
        raise ValueError("no strikes")
    ordered = sorted(strikes)
    atm = find_atm_index(ordered, spot)

    if moneyness == Moneyness.ATM:
        idx = atm + offset
    else:
        outward = (
            (option_type == OptionType.CE and moneyness == Moneyness.OTM)
            or (option_type == OptionType.PE and moneyness == Moneyness.ITM)
        )
        step = 1 if outward else -1
        idx = atm + step * (1 + offset)

    return ordered[max(0, min(idx, len(ordered) - 1))]


def strike_band(strikes: Sequence[float], spot: float, per_side: int) -> list[float]:
    """`per_side` strikes each way around ATM, inclusive of ATM.

    Returns up to 2*per_side+1 strikes, truncated at the chain's edges.
    """
    if not strikes or per_side < 0:
        return []
    ordered = sorted(strikes)
    atm = find_atm_index(ordered, spot)
    return ordered[max(0, atm - per_side): atm + per_side + 1]


# --------------------------------------------------------------------------
# Master contract
# --------------------------------------------------------------------------

def load_master(kite: Any, exchange: str) -> list[dict]:
    """Download the instrument master for one exchange (`NFO`, `BFO`, `NSE`).

    The only broker call in this module. Returns raw dicts as Kite provides
    them; parsing happens in the functions below so they stay unit-testable.
    """
    return list(kite.instruments(exchange))


def option_rows(master: Iterable[dict], underlying: str) -> list[dict]:
    """Option rows of the master for one underlying."""
    name = normalise(underlying)
    return [
        r for r in master
        if r.get("segment") in _OPT_SEGMENTS
        and normalise(str(r.get("name", ""))) == name
    ]


def expiries_for(master: Iterable[dict], underlying: str) -> list[date]:
    """Sorted unique expiries available for one underlying."""
    out = {_as_date(r.get("expiry")) for r in option_rows(master, underlying)}
    return sorted(d for d in out if d is not None)


def strikes_for(master: Iterable[dict], underlying: str, expiry: date) -> list[float]:
    """Sorted unique strikes for one underlying and expiry."""
    out = {
        float(r["strike"])
        for r in option_rows(master, underlying)
        if _as_date(r.get("expiry")) == expiry and float(r.get("strike", 0)) > 0
    }
    return sorted(out)


def build_chain(
    master: Iterable[dict],
    underlying: str,
    expiry: date,
    spot: float,
    per_side: int,
    *,
    wave: int = 2,
    mode: SubscribeMode = SubscribeMode.FULL,
) -> list[Instrument]:
    """Instruments for the CE+PE strike band around ATM.

    Raises:
        ValueError: if the chain has no strikes for that expiry.
    """
    rows = [r for r in option_rows(master, underlying)
            if _as_date(r.get("expiry")) == expiry]
    if not rows:
        raise ValueError(f"no option rows for {underlying} {expiry}")

    all_strikes = sorted({float(r["strike"]) for r in rows if float(r.get("strike", 0)) > 0})
    if not all_strikes:
        raise ValueError(f"no strikes for {underlying} {expiry}")

    wanted = set(strike_band(all_strikes, spot, per_side))
    exch = option_exchange(underlying)
    idx = is_index(underlying)

    chain: list[Instrument] = []
    for r in rows:
        strike = float(r.get("strike", 0))
        itype = str(r.get("instrument_type", "")).upper()
        if strike not in wanted or itype not in (OptionType.CE, OptionType.PE):
            continue
        chain.append(
            Instrument(
                token=int(r["instrument_token"]),
                tradingsymbol=str(r["tradingsymbol"]),
                exchange=exch,
                underlying=normalise(underlying),
                kind=InstrumentKind.OPTION,
                lot_size=int(r.get("lot_size", 0)) or 1,
                tick_size=float(r.get("tick_size", 0)) or 0.05,
                instrument_type=itype,
                strike=strike,
                expiry=expiry,
                is_index=idx,
                subscribe_mode=mode,
                wave=wave,
            )
        )
    chain.sort(key=lambda i: (i.strike, i.instrument_type or ""))
    return chain


def equity_instrument(
    master: Iterable[dict], symbol: str, *, wave: int = 1,
    mode: SubscribeMode = SubscribeMode.QUOTE,
) -> Instrument | None:
    """The NSE equity row for one symbol, as an Instrument."""
    name = normalise(symbol)
    for r in master:
        if (r.get("segment") == "NSE"
                and normalise(str(r.get("tradingsymbol", ""))) == name):
            return Instrument(
                token=int(r["instrument_token"]),
                tradingsymbol=str(r["tradingsymbol"]),
                exchange="NSE",
                underlying=name,
                kind=InstrumentKind.EQUITY,
                lot_size=1,
                tick_size=float(r.get("tick_size", 0)) or 0.05,
                instrument_type="EQ",
                is_index=False,
                subscribe_mode=mode,
                wave=wave,
            )
    return None


def _as_date(value: Any) -> date | None:
    """Kite returns expiry as `date`, `datetime`, or 'YYYY-MM-DD'."""
    if value is None or value == "":
        return None
    if isinstance(value, date) and not hasattr(value, "hour"):
        return value
    if hasattr(value, "date"):
        return value.date()
    try:
        y, m, d = str(value)[:10].split("-")
        return date(int(y), int(m), int(d))
    except (ValueError, AttributeError):
        return None


__all__ = [
    "resolve_expiry", "find_atm_index", "pick_strike", "strike_band",
    "load_master", "option_rows", "expiries_for", "strikes_for",
    "build_chain", "equity_instrument",
]
