"""Price arithmetic. Vectors from BUILD_SPEC §2 and §7."""

from __future__ import annotations

import pytest

from backend.core.pricing import (
    CEIL, FLOOR, entry_limit_price, exit_limit_price, pct_change,
    pnl_basis_price, pnl_pct, pnl_rupees, round_price,
)


@pytest.mark.parametrize("price,mode,expected", [
    (9.8365, CEIL, 9.85),
    (103.206, CEIL, 103.25),
    (1.854, CEIL, 1.90),
    (10.00, CEIL, 10.00),     # exact value must NOT jump a tick
    (10.001, CEIL, 10.05),
    (646.87, FLOOR, 646.85),
    (646.85, FLOOR, 646.85),  # exact value must NOT drop a tick
    (9.84, FLOOR, 9.80),
])
def test_round_price_vectors(price, mode, expected):
    assert round_price(price, 0.05, mode) == pytest.approx(expected)


def test_round_price_float_dust():
    """10.00/0.05 == 199.99999999999997; naive ceil would overpay a tick."""
    assert round_price(10.00, 0.05, CEIL) == 10.00
    assert round_price(20.00, 0.05, CEIL) == 20.00
    assert round_price(0.05, 0.05, CEIL) == 0.05


def test_round_price_other_ticks():
    assert round_price(100.03, 0.10, CEIL) == 100.10
    assert round_price(100.03, 0.10, FLOOR) == 100.00
    assert round_price(1234.6, 1.0, CEIL) == 1235.0


@pytest.mark.parametrize("bad", [0, -0.05])
def test_round_price_rejects_bad_tick(bad):
    with pytest.raises(ValueError):
        round_price(10.0, bad, CEIL)


def test_round_price_rejects_bad_mode():
    with pytest.raises(ValueError):
        round_price(10.0, 0.05, "NEAREST")


def test_round_price_rejects_non_finite():
    with pytest.raises(ValueError):
        round_price(float("inf"), 0.05, CEIL)


# -- entry / exit limits (BUILD_SPEC §7) -----------------------------------

def test_entry_from_ask():
    assert entry_limit_price(best_ask=158.0, last_price=158.0, tick=0.05,
                             price_source="ask", slippage_pct=1.5) == 160.40


def test_entry_falls_back_to_ltp_when_no_ask():
    assert entry_limit_price(best_ask=0.0, last_price=158.0, tick=0.05,
                             price_source="ask", slippage_pct=1.5) == 160.40


def test_entry_cheap_option():
    assert entry_limit_price(best_ask=1.80, last_price=1.80, tick=0.05,
                             price_source="ask", slippage_pct=1.5) == 1.85


def test_entry_ltp_source_ignores_ask():
    assert entry_limit_price(best_ask=999.0, last_price=100.0, tick=0.05,
                             price_source="ltp", slippage_pct=0.0) == 100.00


def test_entry_raises_without_basis():
    with pytest.raises(ValueError):
        entry_limit_price(best_ask=0.0, last_price=0.0, tick=0.05,
                          price_source="ask", slippage_pct=1.5)


def test_exit_from_bid():
    assert exit_limit_price(best_bid=170.9, last_price=170.9, tick=0.05,
                            price_source="bid", slippage_pct=1.0) == 169.15


def test_exit_eod_wider_slippage():
    assert exit_limit_price(best_bid=170.9, last_price=170.9, tick=0.05,
                            price_source="bid", slippage_pct=3.0) == 165.75


def test_exit_raises_without_basis():
    with pytest.raises(ValueError):
        exit_limit_price(best_bid=0.0, last_price=0.0, tick=0.05,
                         price_source="bid", slippage_pct=1.0)


def test_buy_is_never_below_ask_and_sell_never_above_bid():
    """The property that actually matters: marketable in both directions."""
    for raw in (1.07, 9.99, 158.02, 646.83, 1234.56):
        assert entry_limit_price(best_ask=raw, last_price=raw, tick=0.05,
                                 price_source="ask", slippage_pct=0.0) >= raw
        assert exit_limit_price(best_bid=raw, last_price=raw, tick=0.05,
                                price_source="bid", slippage_pct=0.0) <= raw


# -- P&L -------------------------------------------------------------------

def test_pnl_rupees_and_pct():
    assert pnl_rupees(100.0, 110.0, 625) == 6250.0
    assert pnl_rupees(100.0, 90.0, 625) == -6250.0
    assert pnl_pct(100.0, 110.0) == 10.0
    assert pnl_pct(158.0, 171.3) == pytest.approx(8.4177, abs=1e-4)


@pytest.mark.parametrize("entry,current,qty", [
    (0.0, 100.0, 625), (100.0, 0.0, 625), (100.0, 110.0, 0), (-1.0, 100.0, 625),
])
def test_pnl_safe_on_bad_input(entry, current, qty):
    assert pnl_rupees(entry, current, qty) == 0.0


def test_pct_change_never_raises():
    assert pct_change(0.0, 100.0) == 0.0
    assert pct_change(100.0, 0.0) == 0.0
    assert pct_change(5180.0, 5312.0) == pytest.approx(2.5483, abs=1e-4)


def test_pnl_basis_selection():
    assert pnl_basis_price(ltp=171.3, bid=170.9, basis="bid") == 170.9
    assert pnl_basis_price(ltp=171.3, bid=170.9, basis="ltp") == 171.3
    # bid unavailable -> fall back to ltp rather than reporting zero P&L
    assert pnl_basis_price(ltp=171.3, bid=0.0, basis="bid") == 171.3
