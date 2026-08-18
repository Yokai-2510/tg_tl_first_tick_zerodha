"""
Strike selection. The first positive tick decides the SIDE; this decides the
CONTRACT.
"""

from __future__ import annotations

from backend.core.enums import Moneyness, StrikeMode
from backend.engine.strike_select import (
    Candidate, choose, pick_automatic, pick_custom, spread_pct,
)


class Cfg:
    max_spread_pct = 2.0
    min_depth_lots = 0
    weight_spread = 1.0
    weight_depth = 1.0
    weight_oi = 0.5
    weight_volume = 0.5


def c(token, strike, *, bid=0.0, ask=0.0, oi=0, volume=0, ltp=0.0):
    return Candidate(token=token, tradingsymbol=f"X{int(strike)}CE", strike=strike,
                     ltp=ltp, bid=bid, ask=ask, oi=oi, volume=volume)


# ------------------------------------------------------------------- spread

def test_spread_pct_basic():
    assert spread_pct(99.0, 101.0) == 2.0


def test_a_one_sided_book_is_infinitely_wide_not_zero():
    """Returning 0.0 would make a strike with no bid look like the tightest."""
    assert spread_pct(0.0, 100.0) == float("inf")
    assert spread_pct(100.0, 0.0) == float("inf")
    assert spread_pct(0.0, 0.0) == float("inf")


def test_a_crossed_book_is_rejected():
    assert spread_pct(101.0, 99.0) == float("inf")


# ---------------------------------------------------------------- automatic

def test_the_tighter_spread_wins_all_else_equal():
    wide = c(1, 100, bid=95.0, ask=105.0, oi=1000, volume=1000)
    tight = c(2, 110, bid=99.5, ask=100.5, oi=1000, volume=1000)
    assert pick_automatic([wide, tight], Cfg()).token == 2


def test_more_open_interest_wins_when_spreads_match():
    thin = c(1, 100, bid=99.5, ask=100.5, oi=10, volume=10)
    deep = c(2, 110, bid=99.5, ask=100.5, oi=50_000, volume=50_000)
    assert pick_automatic([thin, deep], Cfg()).token == 2


def test_a_spread_wider_than_the_cap_is_disqualified():
    only = c(1, 100, bid=90.0, ask=110.0, oi=99_999, volume=99_999)
    assert pick_automatic([only], Cfg()) is None, "huge OI must not rescue it"


def test_a_strike_with_no_ask_cannot_be_bought():
    assert pick_automatic([c(1, 100, bid=99.0, ask=0.0, oi=9999)], Cfg()) is None


def test_min_depth_disqualifies_illiquid_strikes():
    class C(Cfg):
        min_depth_lots = 500
    assert pick_automatic([c(1, 100, bid=99.5, ask=100.5, volume=10)], C()) is None
    assert pick_automatic([c(1, 100, bid=99.5, ask=100.5, volume=900)], C()) is not None


def test_selection_is_deterministic_for_identical_candidates():
    a = c(1, 100, bid=99.5, ask=100.5, oi=1000, volume=1000)
    b = c(2, 110, bid=99.5, ask=100.5, oi=1000, volume=1000)
    assert pick_automatic([a, b], Cfg()).token == pick_automatic([b, a], Cfg()).token


def test_empty_candidates_is_none_not_a_crash():
    assert pick_automatic([], Cfg()) is None


# ------------------------------------------------------------------- custom

STRIKES = [c(i, s) for i, s in enumerate([90, 95, 100, 105, 110], start=1)]


def test_atm_picks_the_strike_nearest_spot():
    got = pick_custom(STRIKES, spot=101.0, option_type="CE",
                      reference=Moneyness.ATM, offset=0)
    assert got.strike == 100


def test_itm_ce_goes_below_spot():
    got = pick_custom(STRIKES, spot=100.0, option_type="CE",
                      reference=Moneyness.ITM, offset=2)
    assert got.strike == 90, "ITM for a CE is a LOWER strike"


def test_itm_pe_goes_above_spot():
    got = pick_custom(STRIKES, spot=100.0, option_type="PE",
                      reference=Moneyness.ITM, offset=2)
    assert got.strike == 110, "ITM for a PE is a HIGHER strike"


def test_otm_is_the_mirror_of_itm():
    ce = pick_custom(STRIKES, spot=100.0, option_type="CE",
                     reference=Moneyness.OTM, offset=1)
    pe = pick_custom(STRIKES, spot=100.0, option_type="PE",
                     reference=Moneyness.OTM, offset=1)
    assert ce.strike == 105 and pe.strike == 95


def test_an_offset_past_the_ladder_edge_clamps():
    got = pick_custom(STRIKES, spot=100.0, option_type="CE",
                      reference=Moneyness.ITM, offset=99)
    assert got.strike == 90, "clamped to the deepest listed strike"


def test_custom_without_a_spot_is_none():
    assert pick_custom(STRIKES, spot=0.0, option_type="CE",
                       reference=Moneyness.ITM, offset=1) is None


# -------------------------------------------------------------------- choose

def test_first_positive_returns_the_strike_that_ticked():
    assert choose(mode=StrikeMode.FIRST_POSITIVE, signal_token=77,
                  candidates=STRIKES) == 77


def test_every_mode_falls_back_to_the_ticked_strike_when_it_cannot_decide():
    """An unbuyable book or a missing spot must never cost the entry."""
    assert choose(mode=StrikeMode.AUTOMATIC, signal_token=77,
                  candidates=[], cfg=Cfg()) == 77
    assert choose(mode=StrikeMode.CUSTOM, signal_token=77,
                  candidates=STRIKES, spot=0.0) == 77


def test_automatic_returns_the_winner_not_the_signal():
    cands = [c(1, 100, bid=95.0, ask=105.0, oi=10, volume=10),
             c(2, 110, bid=99.5, ask=100.5, oi=9000, volume=9000)]
    assert choose(mode=StrikeMode.AUTOMATIC, signal_token=1,
                  candidates=cands, cfg=Cfg()) == 2
