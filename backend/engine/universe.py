"""
Universe: ranking, shortlisting, and the two subscription waves.

Wave 1 (08:55, before the pre-open): all 50 Nifty stocks + index spots.
    Options are deliberately excluded — they do not trade in the pre-open,
    so there would be nothing to record (BUILD_SPEC R14).

Wave 2 (~09:09, after the settlement snapshot): rank, shortlist, then
    subscribe option chains for the shortlist + enabled indices. Strikes come
    from the SETTLEMENT price, never the previous close — on a gap day a
    prev-close ATM is simply wrong.

The `candidate_buffer` names are SUBSCRIBED but NOT TRADED: insurance against
the ranking shuffling between 09:09 and the bell, when it is far too late to
subscribe a new chain.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.enums import SubscribeMode
from ..core.models import Instrument
from ..core.pricing import pct_change
from ..core.symbols import normalise


@dataclass(frozen=True, slots=True)
class RankRow:
    symbol: str
    ltp: float
    prev_close: float
    change_pct: float
    rank_gainer: int = 0        # 1 = biggest gainer
    rank_loser: int = 0         # 1 = biggest loser


@dataclass(frozen=True, slots=True)
class Shortlist:
    """Result of ranking. `tradeable` fires entries; `buffer` only subscribes."""

    tradeable: tuple[str, ...]
    buffer: tuple[str, ...]
    gainers: tuple[RankRow, ...]
    losers: tuple[RankRow, ...]

    @property
    def all_symbols(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.tradeable + self.buffer))


@dataclass(slots=True)
class SubscriptionPlan:
    """Instruments to subscribe, grouped by websocket mode."""

    wave: int
    instruments: list[Instrument] = field(default_factory=list)

    def by_mode(self) -> dict[str, list[int]]:
        out: dict[str, list[int]] = {}
        for inst in self.instruments:
            out.setdefault(str(inst.subscribe_mode), []).append(inst.token)
        return out

    @property
    def count(self) -> int:
        return len(self.instruments)

    def tokens(self) -> list[int]:
        return [i.token for i in self.instruments]


class SubscriptionCapExceeded(RuntimeError):
    """Projected subscription count exceeds the configured soft cap."""


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------

def rank(snapshot: dict[str, dict]) -> tuple[list[RankRow], list[RankRow]]:
    """Rank symbols by % change.

    `snapshot` maps symbol -> {"ltp": float, "prev_close": float}.

    Symbols with a non-positive price or previous close are EXCLUDED, never
    defaulted to 0% — a missing price must not rank as "unchanged" and land
    in the middle of the table.

    Returns (gainers, losers); gainers[0] is the biggest riser, losers[0] the
    biggest faller.
    """
    rows: list[RankRow] = []
    for symbol, data in snapshot.items():
        ltp = float(data.get("ltp") or 0.0)
        prev = float(data.get("prev_close") or 0.0)
        if ltp <= 0.0 or prev <= 0.0:
            continue
        rows.append(RankRow(normalise(symbol), ltp, prev, pct_change(prev, ltp)))

    ordered = sorted(rows, key=lambda r: (-r.change_pct, r.symbol))
    n = len(ordered)
    gainers = [
        RankRow(r.symbol, r.ltp, r.prev_close, r.change_pct,
                rank_gainer=i + 1, rank_loser=n - i)
        for i, r in enumerate(ordered)
    ]
    losers = list(reversed(gainers))
    return gainers, losers


def shortlist(
    gainers: list[RankRow],
    losers: list[RankRow],
    *,
    top_n_gainers: int,
    top_n_losers: int,
    candidate_buffer: int = 0,
) -> Shortlist:
    """Split the ranking into tradeable names and buffer names.

    A symbol that qualifies on both sides (possible only when the universe is
    tiny) counts as tradeable once — sets deduplicate.
    """
    ng = max(0, int(top_n_gainers))
    nl = max(0, int(top_n_losers))
    buf = max(0, int(candidate_buffer))

    trade = [r.symbol for r in gainers[:ng]] + [r.symbol for r in losers[:nl]]
    wide = [r.symbol for r in gainers[:ng + buf]] + [r.symbol for r in losers[:nl + buf]]

    tradeable = tuple(dict.fromkeys(trade))
    buffer = tuple(s for s in dict.fromkeys(wide) if s not in set(tradeable))
    return Shortlist(tradeable, buffer, tuple(gainers), tuple(losers))


# --------------------------------------------------------------------------
# Subscription planning
# --------------------------------------------------------------------------

def build_wave1(
    stocks: list[Instrument],
    index_spots: list[Instrument],
    *,
    mode: SubscribeMode = SubscribeMode.QUOTE,
    soft_cap: int = 2400,
) -> SubscriptionPlan:
    """Wave 1: equities + index spots only. No options."""
    plan = SubscriptionPlan(wave=1)
    for inst in [*stocks, *index_spots]:
        plan.instruments.append(
            inst if inst.subscribe_mode == mode
            else _with_mode(inst, mode, wave=1)
        )
    _check_cap(plan.count, soft_cap, "wave 1")
    return plan


def build_wave2(
    chains: dict[str, list[Instrument]],
    *,
    symbols: list[str],
    mode: SubscribeMode = SubscribeMode.FULL,
    soft_cap: int = 2400,
    already_subscribed: int = 0,
) -> SubscriptionPlan:
    """Wave 2: option chains for the shortlist + enabled indices.

    `chains` maps underlying -> its strike-band instruments. Depth is required
    for ask-based entry pricing, so the mode is FULL.

    Raises:
        SubscriptionCapExceeded: if the session total would exceed the cap.
            Fails loudly rather than silently truncating the universe.
    """
    plan = SubscriptionPlan(wave=2)
    for symbol in symbols:
        for inst in chains.get(normalise(symbol), ()):
            plan.instruments.append(
                inst if inst.subscribe_mode == mode
                else _with_mode(inst, mode, wave=2)
            )
    _check_cap(already_subscribed + plan.count, soft_cap, "session total")
    return plan


def project_count(
    *,
    n_stocks: int,
    n_indices: int,
    top_n_gainers: int,
    top_n_losers: int,
    candidate_buffer: int,
    strikes_per_side: int,
) -> int:
    """Projected session instrument count, for the Phase 1 preflight check."""
    shortlisted = top_n_gainers + top_n_losers + 2 * candidate_buffer
    strikes = (2 * strikes_per_side + 1) * 2          # both sides of ATM, CE+PE
    return n_stocks + n_indices + (shortlisted + n_indices) * strikes


def _with_mode(inst: Instrument, mode: SubscribeMode, *, wave: int) -> Instrument:
    """Instrument is frozen; return a copy with a different mode/wave."""
    from dataclasses import replace
    return replace(inst, subscribe_mode=mode, wave=wave)


def _check_cap(count: int, soft_cap: int, what: str) -> None:
    if soft_cap and count > soft_cap:
        raise SubscriptionCapExceeded(
            f"{what}: {count} instruments exceeds subscription_soft_cap "
            f"{soft_cap}. Reduce top_n/candidate_buffer/strikes_per_side."
        )


__all__ = [
    "RankRow", "Shortlist", "SubscriptionPlan", "SubscriptionCapExceeded",
    "rank", "shortlist", "build_wave1", "build_wave2", "project_count",
]
