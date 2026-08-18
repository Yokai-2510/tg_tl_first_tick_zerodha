"""
Entry trigger — the hot path.

PURE and allocation-light. Called from inside the websocket callback for every
armed instrument on every tick, so it must not log, must not do I/O, and must
not raise for control flow (BUILD_SPEC R1).

THE RULE: fire on the first POSITIVE LTP TICK -- `current LTP > previous LTP`
for that same strike. Nothing else participates.

Explicitly NOT part of the decision:
  * best ask / depth  -- read for pricing only; a missing book no longer
                         discards the tick that should have fired
  * previous close    -- kept on ArmedState for the strength engine and the
                         console, but never compared against here
  * min_diff          -- removed; any positive tick qualifies
  * confirmation      -- one tick, no second tick, no time window

The first strike of an underlying to post a positive tick decides both the side
(CE or PE) and, in `first_positive` mode, the contract.
"""

from __future__ import annotations

from typing import Any

from ..core.models import ArmedState, Signal
from ..core.timeutil import mono_ns


class TriggerConfig:
    """Flattened entry gates. Built once at arming; read on every tick.

    A plain slotted object rather than dict lookups — this runs thousands of
    times per second.

    `min_diff` and `require_depth` are accepted but IGNORED. They are kept in the
    signature so an existing config still loads; the trigger is now purely
    tick-over-tick and neither value can change its decision.
    """

    __slots__ = ("min_premium", "max_premium")

    def __init__(
        self,
        *,
        min_diff: float = 0.0,          # ignored: retained for config compatibility
        require_depth: bool = True,     # ignored: depth is for pricing only
        min_premium: float = 0.0,
        max_premium: float = 0.0,
    ) -> None:
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
    if price <= 0.0:
        return None

    # THE RULE: a positive LTP tick. `current LTP > previous LTP`, nothing else.
    #
    # prev_ltp is updated on EVERY tick, including the ones that do not fire, so
    # the comparison always uses the immediately preceding trade. A flat or down
    # tick just re-seeds and waits.
    prev = state.prev_ltp
    state.prev_ltp = price
    if prev <= 0.0:
        return None             # first tick for this strike: baseline only
    if price <= prev:
        return None             # flat or negative

    if cfg.min_premium > 0.0 and price < cfg.min_premium:
        return None
    if cfg.max_premium > 0.0 and price > cfg.max_premium:
        return None

    # Depth is read for PRICING only and never gates the signal. Requiring an ask
    # used to skip the tick entirely, which meant the first positive tick could be
    # thrown away on a strike whose book had not arrived yet. The executor prices
    # from the ask when there is one and falls back to the LTP when there is not.
    bid, ask = best_bid_ask(tick)

    diff = price - prev
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
