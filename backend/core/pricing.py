"""
Price arithmetic. Every price the system sends to the broker is produced here.

BUILD_SPEC R5 — direction matters:
    BUY  limits round UP   (CEIL)  -> stay at or above the ask, so we cross
    SELL limits round DOWN (FLOOR) -> stay at or below the bid, so we cross

Rounding the wrong way leaves the order behind the touch and it does not fill.
"""

from __future__ import annotations

import math

CEIL = "CEIL"
FLOOR = "FLOOR"

#: Guards against float dust before ceil/floor. 10.00/0.05 evaluates to
#: 199.99999999999997; without this, CEIL would return 200 -> 10.05 and we
#: would silently overpay one tick on every exact-value order.
_QUANT_DP = 9


def round_price(price: float, tick: float, mode: str) -> float:
    """Round `price` to the instrument tick size in the given direction.

    Args:
        price: raw price, must be finite.
        tick:  instrument tick size, must be > 0.
        mode:  CEIL for buy limits, FLOOR for sell limits.

    Returns:
        Price snapped to the tick grid, rounded to 2 dp.
    """
    if tick <= 0:
        raise ValueError(f"tick must be > 0, got {tick!r}")
    if not math.isfinite(price):
        raise ValueError(f"price must be finite, got {price!r}")

    quotient = round(price / tick, _QUANT_DP)
    if mode == CEIL:
        steps = math.ceil(quotient)
    elif mode == FLOOR:
        steps = math.floor(quotient)
    else:
        raise ValueError(f"mode must be {CEIL!r} or {FLOOR!r}, got {mode!r}")
    return round(steps * tick, 2)


def entry_limit_price(
    *,
    best_ask: float,
    last_price: float,
    tick: float,
    price_source: str,
    slippage_pct: float,
) -> float:
    """BUY limit for an entry.

    Priced to be *marketable*: a buy limit executes against the resting ask
    (<= our price), so the slippage buffer buys fill-certainty, not a worse
    price. Falls back to last_price when the book is empty or 'ltp' is chosen.

    Raises:
        ValueError: if no usable price basis exists.
    """
    base = best_ask if (price_source == "ask" and best_ask > 0) else last_price
    if base <= 0:
        raise ValueError("no valid entry price basis (ask and ltp both <= 0)")
    return round_price(base * (1.0 + slippage_pct / 100.0), tick, CEIL)


def exit_limit_price(
    *,
    best_bid: float,
    last_price: float,
    tick: float,
    price_source: str,
    slippage_pct: float,
) -> float:
    """SELL limit for an exit. Mirror of `entry_limit_price`, crossing down."""
    base = best_bid if (price_source == "bid" and best_bid > 0) else last_price
    if base <= 0:
        raise ValueError("no valid exit price basis (bid and ltp both <= 0)")
    return round_price(base * (1.0 - slippage_pct / 100.0), tick, FLOOR)


# --------------------------------------------------------------------------
# P&L — all positions are LONG options (bought to open). No short handling.
# --------------------------------------------------------------------------

def pnl_rupees(entry_price: float, current_price: float, quantity: int) -> float:
    """Absolute P&L in rupees for a long option position."""
    if entry_price <= 0 or current_price <= 0 or quantity <= 0:
        return 0.0
    return round((current_price - entry_price) * quantity, 2)


def pnl_pct(entry_price: float, current_price: float) -> float:
    """P&L as a percentage of the entry price."""
    if entry_price <= 0 or current_price <= 0:
        return 0.0
    return round((current_price - entry_price) / entry_price * 100.0, 4)


def pct_change(from_price: float, to_price: float) -> float:
    """Percentage change; 0.0 when the basis is unusable (never raises)."""
    if from_price <= 0 or to_price <= 0:
        return 0.0
    return round((to_price - from_price) / from_price * 100.0, 4)


def pnl_basis_price(*, ltp: float, bid: float, basis: str) -> float:
    """Price used for P&L. 'bid' is conservative (what you'd actually get out at)."""
    if basis == "bid" and bid > 0:
        return bid
    return ltp


__all__ = [
    "CEIL", "FLOOR", "round_price",
    "entry_limit_price", "exit_limit_price",
    "pnl_rupees", "pnl_pct", "pct_change", "pnl_basis_price",
]
