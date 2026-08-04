"""Ranking, shortlist, and subscription planning. BUILD_SPEC §4."""

from __future__ import annotations

import pytest

from backend.core.enums import InstrumentKind, SubscribeMode
from backend.core.models import Instrument
from backend.engine.universe import (
    SubscriptionCapExceeded, build_wave1, build_wave2, project_count, rank,
    shortlist,
)

SNAPSHOT = {
    "INDIGO":  {"ltp": 5312.0, "prev_close": 5180.0},   # +2.548
    "TCS":     {"ltp": 3100.0, "prev_close": 3050.0},   # +1.639
    "RELIANCE": {"ltp": 1450.0, "prev_close": 1440.0},  # +0.694
    "INFY":    {"ltp": 1500.0, "prev_close": 1510.0},   # -0.662
    "WIPRO":   {"ltp": 240.0,  "prev_close": 247.7},    # -3.108
    "TITAN":   {"ltp": 3400.0, "prev_close": 3560.0},   # -4.494
}


def test_rank_orders_by_change_pct():
    gainers, losers = rank(SNAPSHOT)
    assert [r.symbol for r in gainers[:3]] == ["INDIGO", "TCS", "RELIANCE"]
    assert [r.symbol for r in losers[:3]] == ["TITAN", "WIPRO", "INFY"]
    assert gainers[0].change_pct == pytest.approx(2.5483, abs=1e-3)
    assert losers[0].change_pct == pytest.approx(-4.4944, abs=1e-3)


def test_rank_assigns_ranks():
    gainers, losers = rank(SNAPSHOT)
    assert gainers[0].rank_gainer == 1
    assert losers[0].rank_loser == 1
    assert gainers[0].symbol != losers[0].symbol


def test_rank_excludes_bad_data_rather_than_defaulting_to_zero():
    """A missing price must not rank as 'unchanged' in the middle of the table."""
    snap = dict(SNAPSHOT)
    snap["BADPREV"] = {"ltp": 100.0, "prev_close": 0.0}
    snap["BADLTP"] = {"ltp": 0.0, "prev_close": 100.0}
    snap["MISSING"] = {}
    gainers, _ = rank(snap)
    names = {r.symbol for r in gainers}
    assert {"BADPREV", "BADLTP", "MISSING"}.isdisjoint(names)
    assert len(gainers) == len(SNAPSHOT)


def test_rank_is_deterministic_on_ties():
    tied = {"BBB": {"ltp": 110.0, "prev_close": 100.0},
            "AAA": {"ltp": 110.0, "prev_close": 100.0}}
    gainers, _ = rank(tied)
    assert [r.symbol for r in gainers] == ["AAA", "BBB"]     # alphabetical tiebreak


def test_shortlist_buffer_is_subscribed_not_traded():
    gainers, losers = rank(SNAPSHOT)
    sl = shortlist(gainers, losers, top_n_gainers=1, top_n_losers=1,
                   candidate_buffer=1)
    assert sl.tradeable == ("INDIGO", "TITAN")
    assert set(sl.buffer) == {"TCS", "WIPRO"}
    assert set(sl.tradeable).isdisjoint(sl.buffer)
    assert len(sl.all_symbols) == 4


def test_shortlist_without_buffer():
    gainers, losers = rank(SNAPSHOT)
    sl = shortlist(gainers, losers, top_n_gainers=2, top_n_losers=2,
                   candidate_buffer=0)
    assert sl.tradeable == ("INDIGO", "TCS", "TITAN", "WIPRO")
    assert sl.buffer == ()


def test_shortlist_can_disable_one_side():
    gainers, losers = rank(SNAPSHOT)
    sl = shortlist(gainers, losers, top_n_gainers=2, top_n_losers=0)
    assert sl.tradeable == ("INDIGO", "TCS")


def test_shortlist_dedupes_when_universe_is_small():
    small = {"A": {"ltp": 110.0, "prev_close": 100.0},
             "B": {"ltp": 90.0, "prev_close": 100.0}}
    gainers, losers = rank(small)
    sl = shortlist(gainers, losers, top_n_gainers=2, top_n_losers=2,
                   candidate_buffer=5)
    assert sorted(sl.tradeable) == ["A", "B"]
    assert sl.buffer == ()
    assert len(set(sl.all_symbols)) == len(sl.all_symbols)


def test_shortlist_buffer_larger_than_universe():
    gainers, losers = rank(SNAPSHOT)
    sl = shortlist(gainers, losers, top_n_gainers=1, top_n_losers=1,
                   candidate_buffer=99)
    assert len(set(sl.all_symbols)) == len(SNAPSHOT)


# -- subscription plans ----------------------------------------------------

def _eq(token, sym):
    return Instrument(token=token, tradingsymbol=sym, exchange="NSE",
                      underlying=sym, kind=InstrumentKind.EQUITY,
                      subscribe_mode=SubscribeMode.QUOTE, wave=1)


def _opt(token, sym, underlying):
    return Instrument(token=token, tradingsymbol=sym, exchange="NFO",
                      underlying=underlying, kind=InstrumentKind.OPTION,
                      lot_size=625, instrument_type="CE", strike=100.0,
                      subscribe_mode=SubscribeMode.FULL, wave=2)


def test_wave1_has_no_options():
    stocks = [_eq(i, f"S{i}") for i in range(50)]
    indices = [_eq(900 + i, n) for i, n in enumerate(["NIFTY", "BANKNIFTY", "SENSEX"])]
    plan = build_wave1(stocks, indices)
    assert plan.wave == 1
    assert plan.count == 53
    assert all(i.kind is not InstrumentKind.OPTION for i in plan.instruments)
    assert set(plan.by_mode()) == {"quote"}


def test_wave2_uses_full_mode_for_depth():
    chains = {"INDIGO": [_opt(1, "I1", "INDIGO"), _opt(2, "I2", "INDIGO")],
              "WIPRO": [_opt(3, "W1", "WIPRO")]}
    plan = build_wave2(chains, symbols=["INDIGO", "WIPRO"])
    assert plan.count == 3
    assert set(plan.by_mode()) == {"full"}
    assert sorted(plan.tokens()) == [1, 2, 3]


def test_wave2_ignores_unknown_symbols():
    chains = {"INDIGO": [_opt(1, "I1", "INDIGO")]}
    assert build_wave2(chains, symbols=["INDIGO", "NOTLISTED"]).count == 1


def test_cap_exceeded_fails_loudly_rather_than_truncating():
    chains = {"X": [_opt(i, f"X{i}", "X") for i in range(100)]}
    with pytest.raises(SubscriptionCapExceeded) as exc:
        build_wave2(chains, symbols=["X"], soft_cap=50)
    assert "soft_cap" in str(exc.value)


def test_cap_counts_the_session_total_not_just_this_wave():
    chains = {"X": [_opt(i, f"X{i}", "X") for i in range(30)]}
    build_wave2(chains, symbols=["X"], soft_cap=100, already_subscribed=50)
    with pytest.raises(SubscriptionCapExceeded):
        build_wave2(chains, symbols=["X"], soft_cap=100, already_subscribed=80)


def test_project_count_matches_documented_example():
    """docs example: n=5/5, buffer=5, 3 indices, strikes_per_side=4."""
    n = project_count(n_stocks=50, n_indices=3, top_n_gainers=5, top_n_losers=5,
                      candidate_buffer=5, strikes_per_side=4)
    # 53 spot + (20 shortlisted + 3 indices) * 18 option instruments
    assert n == 53 + 23 * 18
    assert n < 3000                      # well inside Kite's hard limit
