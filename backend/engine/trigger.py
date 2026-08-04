"""
Entry trigger — the hot path.

PURE and allocation-light. Called from inside the websocket callback for every
armed instrument on every tick, so it must not log, must not do I/O, and must
not raise for control flow (BUILD_SPEC R1).

The rule: fire on the first tick whose price exceeds the reference by more
than `min_diff`. For options the reference is the previous close, because
options do not trade in the NSE pre-open (BUILD_SPEC R14).
"""

from __future__ import annotations

from typing import Any

from ..core.models import ArmedState, Signal
from ..core.timeutil import mono_ns


class TriggerConfig:
    """Flattened entry gates. Built once at arming; read on every tick.

    A plain slotted object rather than dict lookups — this runs thousands of
    times per second.
    """

    __slots__ = ("min_diff", "require_depth", "min_premium", "max_premium")

    def __init__(
        self,
        *,
        min_diff: float = 0.0,
        require_depth: bool = True,
        min_premium: float = 0.0,
        max_premium: float = 0.0,
    ) -> None:
        self.min_diff = float(min_diff)
        self.require_depth = bool(require_depth)
        self.min_premium = float(min_premium)
        self.max_premium = float(max_premium)


def best_bid_ask(tick: dict) -> tuple[float, float]:
    """First level of each side. (0.0, 0.0) when depth is absent.

    Depth is delivered in FULL mode only, so `tick["depth"]` may be missing
    entirely — never index it blindly.
    """
    depth = tick.get("depth")
    if not depth:
        return 0.0, 0.0
    buy = depth.get("buy") or ()
    sell = depth.get("sell") or ()
    bid = float(buy[0].get("price") or 0.0) if buy else 0.0
    ask = float(sell[0].get("price") or 0.0) if sell else 0.0
    return bid, ask


def evaluate(
    tick: dict,
    state: ArmedState,
    cfg: TriggerConfig,
    *,
    sig_id: str = "",
    t_tick_ns: int = 0,
) -> Signal | None:
    """Decide whether this tick fires an entry for this instrument.

    Returns a Signal and latches `state.fired`, or returns None. Never raises.

    The latch is set BEFORE returning so a repeated token inside the same tick
    batch cannot produce two entries (BUILD_SPEC R7).
    """
    if state.fired:
        return None

    price = tick.get("last_price") or 0.0
    if price <= 0.0 or state.ref_price <= 0.0:
        return None

    diff = price - state.ref_price
    if diff <= cfg.min_diff:
        return None

    if cfg.min_premium > 0.0 and price < cfg.min_premium:
        return None
    if cfg.max_premium > 0.0 and price > cfg.max_premium:
        return None

    bid, ask = best_bid_ask(tick)
    if cfg.require_depth and ask <= 0.0:
        # No offer to cross: we cannot price a marketable limit. Skip this
        # tick and stay armed for the next one.
        return None

    state.fired = True

    inst = state.instrument
    return Signal(
        sig_id=sig_id,
        token=inst.token,
        tradingsymbol=inst.tradingsymbol,
        underlying=inst.underlying,
        option_type=inst.instrument_type or "",
        strike=inst.strike,
        ref_price=state.ref_price,
        tick_price=float(price),
        diff=float(diff),
        best_bid=bid,
        best_ask=ask,
        lots=state.lots,
        quantity=state.quantity,
        tick_size=inst.tick_size,
        exchange=inst.exchange,
        is_index=inst.is_index,
        t_tick_ns=t_tick_ns or mono_ns(),
        t_signal_ns=mono_ns(),
    )


def build_config(entry_cfg: Any) -> TriggerConfig:
    """Build a TriggerConfig from the validated entry config section."""
    get = (lambda k, d: getattr(entry_cfg, k, d)) if not isinstance(entry_cfg, dict) \
        else (lambda k, d: entry_cfg.get(k, d))
    return TriggerConfig(
        min_diff=get("min_diff", 0.0),
        require_depth=get("require_depth", True),
        min_premium=get("min_premium", 0.0),
        max_premium=get("max_premium", 0.0),
    )


__all__ = ["TriggerConfig", "best_bid_ask", "evaluate", "build_config"]
