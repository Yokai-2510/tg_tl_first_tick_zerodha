"""Expiry resolution and strike selection. Vectors from BUILD_SPEC §3 and §5."""

from __future__ import annotations

from datetime import date

import pytest

from backend.brokers.kite.instruments import (
    build_chain, expiries_for, find_atm_index, pick_strike, resolve_expiry,
    strike_band, strikes_for,
)
from backend.core.enums import Moneyness, OptionType

JUL = date(2026, 7, 28)      # Tuesday
AUG = date(2026, 8, 25)      # Tuesday
SEP = date(2026, 9, 29)
EXPIRIES = [JUL, AUG, SEP]


# -- expiry roll (BUILD_SPEC §3) -------------------------------------------

@pytest.mark.parametrize("symbol,today,expected,why", [
    ("INDIGO", date(2026, 7, 20), JUL, "far from expiry"),
    ("INDIGO", date(2026, 7, 24), JUL, "Friday: last two trading days are Mon+Tue"),
    ("INDIGO", date(2026, 7, 27), AUG, "day before expiry -> roll"),
    ("INDIGO", date(2026, 7, 28), AUG, "expiry day -> roll"),
    ("WIPRO",  date(2026, 7, 27), AUG, "any stock rolls"),
    ("NIFTY",  date(2026, 7, 27), JUL, "index never rolls"),
    ("SENSEX", date(2026, 7, 28), JUL, "index never rolls on expiry day"),
    ("BANKNIFTY", date(2026, 7, 28), JUL, "index never rolls"),
])
def test_resolve_expiry_vectors(symbol, today, expected, why):
    assert resolve_expiry(symbol, EXPIRIES, today) == expected, why


def test_no_next_expiry_stays_put():
    assert resolve_expiry("INDIGO", [JUL], date(2026, 7, 27)) == JUL


def test_weekend_gap_counts_trading_days():
    """Fri 31-Jul -> Mon 3-Aug is ONE trading day, so a stock must roll."""
    mon, month_end = date(2026, 8, 3), date(2026, 8, 31)
    assert resolve_expiry("INDIGO", [mon, month_end], date(2026, 7, 31)) == month_end
    # Thursday is two trading days out -> stay
    assert resolve_expiry("INDIGO", [mon, month_end], date(2026, 7, 30)) == mon


def test_roll_disabled():
    assert resolve_expiry("INDIGO", EXPIRIES, date(2026, 7, 28),
                          roll_enabled=False) == JUL


def test_buffer_of_two_rolls_earlier():
    assert resolve_expiry("INDIGO", EXPIRIES, date(2026, 7, 24),
                          buffer_trading_days=2) == AUG


def test_empty_and_all_expired():
    assert resolve_expiry("INDIGO", [], date(2026, 7, 27)) is None
    assert resolve_expiry("INDIGO", [JUL], date(2026, 12, 1)) == JUL


def test_unsorted_and_duplicated_input():
    assert resolve_expiry("INDIGO", [SEP, JUL, AUG, JUL], date(2026, 7, 20)) == JUL


# -- strike selection (BUILD_SPEC §5) --------------------------------------

STRIKES = [100.0, 105.0, 110.0, 115.0, 120.0]
SPOT = 111.0                                  # ATM = 110 (index 2)


def test_find_atm():
    assert find_atm_index(STRIKES, SPOT) == 2
    assert find_atm_index(STRIKES, 100.0) == 0
    assert find_atm_index(STRIKES, 999.0) == 4


def test_find_atm_tie_resolves_lower():
    assert find_atm_index([100.0, 110.0], 105.0) == 0


@pytest.mark.parametrize("otype,money,offset,expected", [
    (OptionType.CE, Moneyness.ATM, 0, 110.0),
    (OptionType.CE, Moneyness.OTM, 0, 115.0),
    (OptionType.CE, Moneyness.OTM, 1, 120.0),
    (OptionType.CE, Moneyness.ITM, 0, 105.0),
    (OptionType.CE, Moneyness.ITM, 1, 100.0),
    (OptionType.PE, Moneyness.OTM, 0, 105.0),
    (OptionType.PE, Moneyness.OTM, 1, 100.0),
    (OptionType.PE, Moneyness.ITM, 0, 115.0),
    (OptionType.PE, Moneyness.ITM, 1, 120.0),
    (OptionType.CE, Moneyness.OTM, 5, 120.0),    # clamped, NOT wrapped
    (OptionType.PE, Moneyness.OTM, 9, 100.0),    # clamped at the low end
])
def test_pick_strike_vectors(otype, money, offset, expected):
    assert pick_strike(STRIKES, SPOT, otype, money, offset) == expected


def test_pick_strike_unsorted_input():
    assert pick_strike([120.0, 100.0, 110.0, 105.0, 115.0], SPOT,
                       OptionType.CE, Moneyness.OTM, 0) == 115.0


def test_strike_band():
    assert strike_band(STRIKES, SPOT, 1) == [105.0, 110.0, 115.0]
    assert strike_band(STRIKES, SPOT, 2) == STRIKES
    assert strike_band(STRIKES, SPOT, 0) == [110.0]
    assert strike_band(STRIKES, SPOT, 99) == STRIKES      # truncates at edges
    assert strike_band([], SPOT, 2) == []


def test_strike_band_at_chain_edge():
    assert strike_band(STRIKES, 100.0, 2) == [100.0, 105.0, 110.0]


# -- master parsing --------------------------------------------------------

def _master_row(sym, strike, itype, expiry=JUL, token=1, seg="NFO-OPT"):
    return {
        "instrument_token": token, "tradingsymbol": sym, "name": "INDIGO",
        "segment": seg, "instrument_type": itype, "strike": strike,
        "expiry": expiry, "lot_size": 625, "tick_size": 0.05,
    }


MASTER = [
    _master_row("INDIGO26JUL5300PE", 5300.0, "PE", JUL, 1),
    _master_row("INDIGO26JUL5300CE", 5300.0, "CE", JUL, 2),
    _master_row("INDIGO26JUL5350PE", 5350.0, "PE", JUL, 3),
    _master_row("INDIGO26JUL5350CE", 5350.0, "CE", JUL, 4),
    _master_row("INDIGO26AUG5300PE", 5300.0, "PE", AUG, 5),
    {"instrument_token": 9, "tradingsymbol": "INDIGO", "name": "INDIGO",
     "segment": "NSE", "instrument_type": "EQ", "strike": 0, "expiry": "",
     "lot_size": 1, "tick_size": 0.05},
]


def test_expiries_and_strikes_from_master():
    assert expiries_for(MASTER, "INDIGO") == [JUL, AUG]
    assert strikes_for(MASTER, "INDIGO", JUL) == [5300.0, 5350.0]


def test_expiry_accepts_string_dates():
    rows = [_master_row("X", 100.0, "CE", "2026-07-28", 1)]
    assert expiries_for(rows, "INDIGO") == [JUL]


def test_build_chain_selects_band_and_both_types():
    chain = build_chain(MASTER, "INDIGO", JUL, spot=5300.0, per_side=1)
    assert {i.tradingsymbol for i in chain} == {
        "INDIGO26JUL5300PE", "INDIGO26JUL5300CE",
        "INDIGO26JUL5350PE", "INDIGO26JUL5350CE",
    }
    assert all(i.lot_size == 625 and i.exchange == "NFO" for i in chain)
    assert all(i.expiry == JUL for i in chain)      # never leaks the AUG row


def test_build_chain_raises_on_unknown_expiry():
    with pytest.raises(ValueError):
        build_chain(MASTER, "INDIGO", date(2027, 1, 1), spot=5300.0, per_side=1)
