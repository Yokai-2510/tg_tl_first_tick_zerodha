"""Symbols, time handling, enums, models."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from backend.core.enums import (
    ENTRY_PHASES, RETRYABLE_REJECTIONS, Phase, PositionStatus, RejectionKind,
    is_terminal,
)
from backend.core.models import Instrument, TickView
from backend.core.symbols import (
    is_index, kind_of, normalise, option_exchange, spot_exchange, spot_quote_key,
)
from backend.core.timeutil import (
    IST, add_trading_days, epoch_us, has_passed, is_weekend, parse_hhmmss,
    seconds_until, to_epoch_us, today_at, trading_days_between,
)

from .conftest import make_instrument


# -- symbols (R12) ---------------------------------------------------------

@pytest.mark.parametrize("sym", ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX",
                                 "BANKEX", "MIDCPNIFTY", "nifty", " NIFTY "])
def test_index_symbols(sym):
    assert is_index(sym) is True


@pytest.mark.parametrize("sym", ["NIFTYBEES", "BANKNIFTY1", "INDIGO", "M&M",
                                 "BAJAJ-AUTO", "JUNIORBEES", ""])
def test_non_index_symbols(sym):
    assert is_index(sym) is False


def test_substring_trap():
    """'NIFTY' in 'NIFTYBEES' is True — exact matching must prevent this."""
    assert "NIFTY" in "NIFTYBEES"
    assert is_index("NIFTYBEES") is False


def test_option_exchange_routing():
    assert option_exchange("SENSEX") == "BFO"
    assert option_exchange("BANKEX") == "BFO"
    assert option_exchange("NIFTY") == "NFO"
    assert option_exchange("INDIGO") == "NFO"
    assert option_exchange("M&M") == "NFO"


def test_spot_exchange_and_quote_keys():
    assert spot_exchange("SENSEX") == "BSE"
    assert spot_exchange("INDIGO") == "NSE"
    assert spot_quote_key("NIFTY") == "NSE:NIFTY 50"
    assert spot_quote_key("BANKNIFTY") == "NSE:NIFTY BANK"
    assert spot_quote_key("SENSEX") == "BSE:SENSEX"
    assert spot_quote_key("INDIGO") == "NSE:INDIGO"


def test_punctuated_symbols_are_preserved():
    """M&M and BAJAJ-AUTO are real Nifty 50 members."""
    assert normalise(" m&m ") == "M&M"
    assert spot_quote_key("M&M") == "NSE:M&M"
    assert spot_quote_key("BAJAJ-AUTO") == "NSE:BAJAJ-AUTO"


def test_kind_of():
    assert kind_of("NIFTY").name == "INDEX"
    assert kind_of("INDIGO").name == "EQUITY"


# -- time (R10) ------------------------------------------------------------

def test_parse_hhmmss():
    assert parse_hhmmss("09:15:00").hour == 9
    assert parse_hhmmss("09:15").minute == 15
    assert parse_hhmmss("15:28:30").second == 30


@pytest.mark.parametrize("bad", ["", "9", "25:00:00", "09:60:00", "09:15:60",
                                 "aa:bb", "09:15:00:00"])
def test_parse_hhmmss_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        parse_hhmmss(bad)


def test_today_at_is_timezone_aware_ist():
    dt = today_at("09:15:00")
    assert dt.tzinfo is not None
    assert dt.utcoffset().total_seconds() == 5.5 * 3600
    assert (dt.hour, dt.minute) == (9, 15)


def test_has_passed_and_seconds_until():
    ref = datetime(2026, 8, 5, 9, 30, 0, tzinfo=IST)
    assert has_passed("09:15:00", ref) is True
    assert has_passed("09:45:00", ref) is False
    assert seconds_until("09:45:00", ref) == pytest.approx(900.0)
    assert seconds_until("09:15:00", ref) == pytest.approx(-900.0)


def test_trading_days_skips_weekends():
    assert trading_days_between(date(2026, 7, 27), date(2026, 7, 28)) == 1
    assert trading_days_between(date(2026, 7, 31), date(2026, 8, 3)) == 1   # Fri->Mon
    assert trading_days_between(date(2026, 7, 20), date(2026, 7, 28)) == 6
    assert trading_days_between(date(2026, 7, 28), date(2026, 7, 28)) == 0


def test_add_trading_days_and_weekend_check():
    assert add_trading_days(date(2026, 7, 31), 1) == date(2026, 8, 3)
    assert is_weekend(date(2026, 8, 1)) is True     # Saturday
    assert is_weekend(date(2026, 8, 3)) is False    # Monday


def test_to_epoch_us_from_naive_datetime():
    """pykiteconnect returns NAIVE IST datetimes, not epochs."""
    naive = datetime(2026, 8, 5, 9, 15, 0)
    aware = datetime(2026, 8, 5, 9, 15, 0, tzinfo=IST)
    assert to_epoch_us(naive) == to_epoch_us(aware)
    assert to_epoch_us(naive) == epoch_us(aware)


def test_to_epoch_us_unit_detection():
    secs = 1_785_900_900
    assert to_epoch_us(secs) == secs * 1_000_000
    assert to_epoch_us(secs * 1_000) == secs * 1_000_000        # ms
    assert to_epoch_us(secs * 1_000_000) == secs * 1_000_000    # us
    assert to_epoch_us(secs * 1_000_000_000) == secs * 1_000_000  # ns
    assert to_epoch_us(None) is None


def test_to_epoch_us_rejects_unsupported():
    with pytest.raises(TypeError):
        to_epoch_us("2026-08-05")


# -- enums -----------------------------------------------------------------

def test_terminal_statuses():
    assert is_terminal("COMPLETE") and is_terminal("REJECTED") and is_terminal("CANCELLED")
    assert not is_terminal("OPEN") and not is_terminal("OPEN PENDING")
    assert not is_terminal(None) and not is_terminal("")
    assert is_terminal("complete")           # case-insensitive


def test_enums_are_plain_strings_for_json():
    assert Phase.TRADING == "TRADING"
    assert PositionStatus.ACTIVE == "ACTIVE"
    assert f"{Phase.TRADING}" == "TRADING"


def test_entry_phase_set_is_only_trading():
    assert ENTRY_PHASES == frozenset({Phase.TRADING})
    for p in (Phase.FROZEN, Phase.MANAGING, Phase.ARMING, Phase.EOD):
        assert p not in ENTRY_PHASES


def test_retryable_rejections_exclude_margin_and_rms():
    assert RejectionKind.LPP in RETRYABLE_REJECTIONS
    assert RejectionKind.ORDER_TYPE in RETRYABLE_REJECTIONS
    assert RejectionKind.MARGIN not in RETRYABLE_REJECTIONS
    assert RejectionKind.RMS not in RETRYABLE_REJECTIONS
    assert RejectionKind.AUTH not in RETRYABLE_REJECTIONS


# -- models ----------------------------------------------------------------

def test_instrument_is_immutable():
    inst = make_instrument()
    with pytest.raises(Exception):
        inst.token = 999


def test_instrument_helpers():
    inst = make_instrument()
    assert inst.is_option is True
    assert inst.quote_key == "NFO:INDIGO26AUG5300PE"


def test_armed_state_quantity_uses_lot_size(armed):
    assert armed.quantity == armed.lots * armed.instrument.lot_size == 625


def test_tickview_feed_lag_and_depth():
    tv = TickView(token=1, ltp=100.0, bid=99.5, ask=100.5,
                  exchange_ts_us=1_000_000, recv_us=1_012_400)
    assert tv.has_depth is True
    assert tv.feed_lag_us == 12_400

    assert TickView(token=1).has_depth is False
    assert TickView(token=1, recv_us=5).feed_lag_us is None


def test_tickview_negative_lag_is_surfaced_not_clamped():
    """A negative lag means clock skew — surface it rather than hide it."""
    tv = TickView(token=1, exchange_ts_us=2_000_000, recv_us=1_000_000)
    assert tv.feed_lag_us == -1_000_000
