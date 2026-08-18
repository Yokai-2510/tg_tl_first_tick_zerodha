"""
Which contract to buy, once the first positive tick has decided the side.

The side (CE or PE) and the contract are separate decisions. The tick decides the
side; this decides the strike, per `instruments.strike_mode`:

    FIRST_POSITIVE  the strike that ticked. Fastest -- zero extra work, and the
                    signal already carries everything needed.
    AUTOMATIC       score that side's armed strikes on spread, depth, OI and
                    volume; buy the best.
    CUSTOM          a fixed offset from ATM, ignoring which strike ticked.

PURE. No I/O, no broker calls, no clock. Runs off the hot path -- the signal has
already been queued by the time this is consulted, so a few microseconds here
cost nothing.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from ..core.enums import Moneyness, StrikeMode


class Candidate(NamedTuple):
    """One armed contract of the chosen side, with its live book."""

    token: int
    tradingsymbol: str
    strike: float
    ltp: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    oi: int = 0
    volume: int = 0


def spread_pct(bid: float, ask: float) -> float:
    """Bid-ask spread as a percentage of the mid. Infinite when there is no book.

    A one-sided book is not a tight book: returning 0.0 would make a strike with
    no bid look like the best one available.
    """
    if bid <= 0.0 or ask <= 0.0 or ask < bid:
        return float("inf")
    mid = (bid + ask) / 2.0
    return ((ask - bid) / mid) * 100.0 if mid > 0 else float("inf")


def score(c: Candidate, cfg: Any) -> float | None:
    """Rank one candidate. None means "disqualified".

    Normalised so no single term dominates: spread is inverted (tighter is
    better), and depth/OI/volume are compared against the field rather than used
    raw, by the caller.
    """
    sp = spread_pct(c.bid, c.ask)
    if sp > float(getattr(cfg, "max_spread_pct", 2.0)):
        return None
    min_depth = int(getattr(cfg, "min_depth_lots", 0))
    if min_depth > 0 and c.volume < min_depth:
        return None
    if c.ask <= 0.0:
        return None                      # nothing to buy from

    w_spread = float(getattr(cfg, "weight_spread", 1.0))
    w_depth = float(getattr(cfg, "weight_depth", 1.0))
    w_oi = float(getattr(cfg, "weight_oi", 0.5))
    w_vol = float(getattr(cfg, "weight_volume", 0.5))

    # Every term is squashed into (0, 1] so the configured weights mean what they
    # say and one huge OI cannot swamp a tight spread.
    tightness = 1.0 / (1.0 + sp)                        # smaller spread -> nearer 1
    depth = 1.0 - 1.0 / (1.0 + c.volume / 1000.0)       # traded size today
    oi = 1.0 - 1.0 / (1.0 + c.oi / 1000.0)              # open interest
    return (w_spread * tightness
            + w_depth * depth
            + w_oi * oi
            + w_vol * depth)


def pick_automatic(candidates: list[Candidate], cfg: Any) -> Candidate | None:
    """Best-scoring candidate, or None when every one is disqualified."""
    best, best_score = None, None
    for c in candidates:
        s = score(c, cfg)
        if s is None:
            continue
        # Ties break on the tighter spread, then the lower strike, so the choice
        # is deterministic and reproducible from a recording.
        key = (s, -spread_pct(c.bid, c.ask), -c.strike)
        if best_score is None or key > best_score:
            best, best_score = c, key
    return best


def pick_custom(candidates: list[Candidate], *, spot: float, option_type: str,
                reference: Moneyness, offset: int) -> Candidate | None:
    """A fixed offset from ATM, ignoring which strike ticked.

    ITM and OTM mean opposite directions for CE and PE, which is the part that is
    easy to get backwards: for a CE, in-the-money is BELOW spot; for a PE it is
    above.
    """
    if not candidates or spot <= 0.0:
        return None
    ordered = sorted(candidates, key=lambda c: c.strike)
    atm = min(range(len(ordered)), key=lambda i: abs(ordered[i].strike - spot))

    if reference is Moneyness.ATM or offset == 0:
        return ordered[atm]

    is_ce = (option_type or "").upper() == "CE"
    deeper = reference is Moneyness.ITM
    # ITM CE -> lower strikes; ITM PE -> higher strikes; OTM is the mirror.
    step = -offset if (deeper == is_ce) else offset
    return ordered[max(0, min(len(ordered) - 1, atm + step))]


def choose(
    *,
    mode: StrikeMode,
    signal_token: int,
    candidates: list[Candidate],
    cfg: Any = None,
    spot: float = 0.0,
    option_type: str = "",
    reference: Moneyness = Moneyness.ITM,
    offset: int = 0,
) -> int:
    """Resolve the token to trade. Always returns a token.

    Falls back to the strike that ticked whenever a mode cannot produce an
    answer -- an unbuyable book or a missing spot must never cost the entry.
    """
    if mode is StrikeMode.AUTOMATIC:
        best = pick_automatic(candidates, cfg)
        return best.token if best else signal_token
    if mode is StrikeMode.CUSTOM:
        best = pick_custom(candidates, spot=spot, option_type=option_type,
                           reference=reference, offset=offset)
        return best.token if best else signal_token
    return signal_token


__all__ = ["Candidate", "choose", "pick_automatic", "pick_custom", "score",
           "spread_pct"]
