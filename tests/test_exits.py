"""Exit engine. BUILD_SPEC §9 — priority, ratchet, idempotency."""

from __future__ import annotations

from datetime import datetime

import pytest

from backend.core.enums import ExitTrigger
from backend.core.timeutil import IST
from backend.engine.exits import (
    ExitConfig, build_config, evaluate, refresh_live, update_trailing,
)

from .conftest import make_position

MIDDAY = datetime(2026, 8, 5, 11, 0, 0, tzinfo=IST)
AFTER_EOD = datetime(2026, 8, 5, 15, 29, 0, tzinfo=IST)
NOW_US = 1_785_900_900_000_000


def _tick(pos, ltp, cfg, now_us=NOW_US):
    pos.live.ltp = ltp
    refresh_live(pos, cfg, now_us=now_us)
    update_trailing(pos, cfg)
    return evaluate(pos, cfg, MIDDAY)


# -- stop loss / target ----------------------------------------------------

def test_stop_loss():
    cfg = ExitConfig(trailing_stop_enabled=False)
    pos = make_position(entry_price=100.0)
    assert _tick(pos, 96.0, cfg) == (False, None)
    assert _tick(pos, 95.0, cfg) == (True, ExitTrigger.STOP_LOSS)


def test_target():
    cfg = ExitConfig(trailing_stop_enabled=False, target_pct=30.0)
    pos = make_position(entry_price=100.0)
    assert _tick(pos, 129.0, cfg) == (False, None)
    assert _tick(pos, 130.0, cfg) == (True, ExitTrigger.TARGET)


def test_disabled_conditions_never_fire():
    cfg = ExitConfig(stop_loss_enabled=False, target_enabled=False,
                     trailing_stop_enabled=False, eod_exit_enabled=False)
    pos = make_position(entry_price=100.0)
    assert _tick(pos, 10.0, cfg) == (False, None)
    assert _tick(pos, 1000.0, cfg) == (False, None)


def test_stop_loss_wins_over_target():
    """Priority order, not evaluation order of the config."""
    cfg = ExitConfig(stop_loss_pct=-5.0, target_pct=1.0, trailing_stop_enabled=False)
    pos = make_position(entry_price=100.0)
    assert _tick(pos, 90.0, cfg)[1] == ExitTrigger.STOP_LOSS


# -- trailing stop ratchet (BUILD_SPEC §9 vector) --------------------------

def test_trailing_sl_ratchet_vector():
    cfg = ExitConfig(stop_loss_enabled=False, target_enabled=False,
                     trailing_stop_enabled=True,
                     trailing_stop_activation_pct=7.0,
                     trailing_stop_distance_pct=3.0)
    pos = make_position(entry_price=100.0)

    assert _tick(pos, 104.0, cfg) == (False, None)
    assert pos.trailing.sl_active is False

    assert _tick(pos, 108.0, cfg) == (False, None)
    assert pos.trailing.sl_active is True
    assert pos.trailing.sl_peak == 108.0
    assert pos.trailing.sl_level == pytest.approx(104.76)

    assert _tick(pos, 115.0, cfg) == (False, None)
    assert pos.trailing.sl_level == pytest.approx(111.55)

    # pullback: level must NOT move down
    assert _tick(pos, 112.0, cfg) == (False, None)
    assert pos.trailing.sl_peak == 115.0
    assert pos.trailing.sl_level == pytest.approx(111.55)

    assert _tick(pos, 111.0, cfg) == (True, ExitTrigger.TRAILING_SL)


def test_trailing_sl_not_armed_below_activation():
    cfg = ExitConfig(stop_loss_enabled=False, target_enabled=False,
                     trailing_stop_activation_pct=7.0)
    pos = make_position(entry_price=100.0)
    _tick(pos, 106.9, cfg)
    assert pos.trailing.sl_active is False
    assert _tick(pos, 101.0, cfg) == (False, None)     # no trail to breach


# -- trailing target -------------------------------------------------------

def test_trailing_target_suppresses_plain_target():
    """With trailing target on, TARGET fires only at max_extension."""
    cfg = ExitConfig(stop_loss_enabled=False, trailing_stop_enabled=False,
                     target_enabled=True, target_pct=10.0,
                     trailing_target_enabled=True,
                     trailing_target_activation_pct=15.0,
                     trailing_target_extend_pct=5.0,
                     trailing_target_max_extension_pct=50.0)
    pos = make_position(entry_price=100.0)
    assert _tick(pos, 112.0, cfg) == (False, None)       # past target_pct, not out
    assert _tick(pos, 120.0, cfg) == (False, None)       # armed
    assert pos.trailing.tgt_active is True
    assert pos.trailing.tgt_level == pytest.approx(114.0)
    assert _tick(pos, 114.0, cfg) == (True, ExitTrigger.TRAILING_TARGET)


def test_trailing_target_ceiling():
    cfg = ExitConfig(stop_loss_enabled=False, trailing_stop_enabled=False,
                     target_enabled=True, target_pct=10.0,
                     trailing_target_enabled=True,
                     trailing_target_activation_pct=15.0,
                     trailing_target_max_extension_pct=50.0)
    pos = make_position(entry_price=100.0)
    assert _tick(pos, 150.0, cfg) == (True, ExitTrigger.TARGET)


# -- time / EOD ------------------------------------------------------------

def test_time_exit():
    cfg = ExitConfig(stop_loss_enabled=False, target_enabled=False,
                     trailing_stop_enabled=False,
                     time_exit_enabled=True, time_exit_holding_seconds=600)
    pos = make_position(entry_price=100.0)
    assert _tick(pos, 101.0, cfg, now_us=NOW_US + 500 * 1_000_000) == (False, None)
    assert _tick(pos, 101.0, cfg, now_us=NOW_US + 601 * 1_000_000) \
        == (True, ExitTrigger.TIME_EXIT)


def test_eod_squareoff():
    cfg = ExitConfig(stop_loss_enabled=False, target_enabled=False,
                     trailing_stop_enabled=False, eod_square_off_time="15:28:00")
    pos = make_position(entry_price=100.0, ltp=101.0)
    refresh_live(pos, cfg, now_us=NOW_US)
    assert evaluate(pos, cfg, MIDDAY) == (False, None)
    assert evaluate(pos, cfg, AFTER_EOD) == (True, ExitTrigger.EOD_SQUAREOFF)


# -- idempotency & guards (R8) ---------------------------------------------

def test_exiting_latch_blocks_second_exit():
    cfg = ExitConfig()
    pos = make_position(entry_price=100.0, ltp=50.0, exiting=True)
    refresh_live(pos, cfg, now_us=NOW_US)
    assert evaluate(pos, cfg, MIDDAY) == (False, None)


def test_no_exit_without_usable_prices():
    cfg = ExitConfig()
    assert evaluate(make_position(entry_price=0.0, ltp=100.0), cfg, MIDDAY) == (False, None)
    assert evaluate(make_position(entry_price=100.0, ltp=0.0), cfg, MIDDAY) == (False, None)


# -- P&L basis -------------------------------------------------------------

def test_pnl_basis_bid_is_conservative():
    cfg_ltp = ExitConfig(pnl_basis="ltp")
    cfg_bid = ExitConfig(pnl_basis="bid")
    pos = make_position(entry_price=100.0, ltp=110.0, bid=108.0)
    refresh_live(pos, cfg_ltp, now_us=NOW_US)
    assert pos.live.pnl_pct == pytest.approx(10.0)
    refresh_live(pos, cfg_bid, now_us=NOW_US)
    assert pos.live.pnl_pct == pytest.approx(8.0)


def test_max_min_pnl_tracked():
    cfg = ExitConfig(stop_loss_enabled=False, target_enabled=False,
                     trailing_stop_enabled=False)
    pos = make_position(entry_price=100.0)
    for px in (105.0, 120.0, 95.0, 110.0):
        _tick(pos, px, cfg)
    assert pos.live.max_pnl_pct == pytest.approx(20.0)
    assert pos.live.min_pnl_pct == pytest.approx(-5.0)


def test_build_config_from_dict():
    cfg = build_config({
        "stop_loss": {"enabled": True, "percentage": -8.0},
        "target": {"enabled": False, "percentage": 25.0},
        "trailing_stop": {"enabled": True, "activation_pct": 6.0,
                          "trail_distance_pct": 2.5},
        "eod_exit": {"enabled": True, "square_off_time": "15:20:00"},
        "pnl_basis": "bid",
    })
    assert cfg.stop_loss_pct == -8.0
    assert cfg.target_enabled is False
    assert cfg.trailing_stop_activation_pct == 6.0
    assert cfg.eod_square_off_time == "15:20:00"
    assert cfg.pnl_basis == "bid"
