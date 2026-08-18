"""
Multi-broker abstraction: normalisation, registry, and cross-broker mapping.

The engine must never see a broker difference. These tests assert that both
adapters produce byte-identical canonical shapes.
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.brokers.base import (
    contract_key, normalise_status, surrogate_token,
)
from backend.brokers.registry import (
    BrokerPair, credentials_for, make_broker, PAPER, UPSTOX, ZERODHA,
)
from backend.brokers.upstox import instruments as uinst
from backend.brokers.upstox import orders as uorders
from backend.brokers.upstox.feed import normalise_feed, normalise_tick
from backend.core.enums import InstrumentKind, OrderStatus, RejectionKind
from backend.core.models import Instrument

from .conftest import make_instrument


# ======================= identity =======================

def test_surrogate_token_is_stable_and_distinct():
    a = surrogate_token("NSE_FO|49520")
    assert a == surrogate_token("NSE_FO|49520")        # deterministic
    assert a != surrogate_token("NSE_FO|49521")
    assert 0 < a < 2 ** 56


def test_surrogate_tokens_do_not_collide_across_a_realistic_master():
    keys = [f"NSE_FO|{i}" for i in range(50_000)]
    tokens = {surrogate_token(k) for k in keys}
    assert len(tokens) == len(keys), "surrogate token collision"


def test_contract_key_matches_across_symbol_formats():
    """Kite and Upstox spell the symbol differently; the contract is identical."""
    kite = contract_key("INDIGO", date(2026, 8, 25), 5300.0, "PE")
    upstox = contract_key("indigo", date(2026, 8, 25), 5300.00, "pe")
    assert kite == upstox


# ======================= status normalisation =======================

@pytest.mark.parametrize("raw,expected", [
    ("COMPLETE", "COMPLETE"),
    ("complete", "COMPLETE"),
    ("completed", "COMPLETE"),          # Upstox spelling
    ("filled", "COMPLETE"),
    ("rejected", "REJECTED"),
    ("cancelled", "CANCELLED"),
    ("canceled", "CANCELLED"),          # US spelling
    ("open pending", "OPEN PENDING"),
    ("  Open   Pending  ", "OPEN PENDING"),
    ("trigger pending", "TRIGGER PENDING"),
    (None, None),
    ("", None),
])
def test_normalise_status(raw, expected):
    assert normalise_status(raw) == expected


def test_normalised_status_feeds_the_terminal_check():
    from backend.core.enums import is_terminal
    assert is_terminal(normalise_status("completed")) is True
    assert is_terminal(normalise_status("open pending")) is False


# ======================= Upstox field traps =======================

@pytest.mark.parametrize("raw,expected", [
    (5.0, 0.05),          # paise -> rupees: THE trap
    (10.0, 0.10),
    (1.0, 0.01),
    (100.0, 1.00),
    (0.05, 0.05),         # already rupees -> unchanged
    (0, 0.05),            # missing -> safe default
    (None, 0.05),
])
def test_tick_size_paise_conversion(raw, expected):
    assert uinst.paise_to_rupees(raw) == pytest.approx(expected)


def test_tick_size_trap_would_break_pricing_if_unconverted():
    """5.0 passed through raw would round 158.00 to 160.00, not 160.40."""
    from backend.core.pricing import CEIL, round_price
    assert round_price(160.37, uinst.paise_to_rupees(5.0), CEIL) == 160.40
    assert round_price(160.37, 5.0, CEIL) == 165.00        # the bug we avoid


def test_expiry_epoch_ms_conversion():
    # 2026-08-25 00:00:00 UTC
    assert uinst.epoch_ms_to_date(1787616000000) == date(2026, 8, 25)
    assert uinst.epoch_ms_to_date(None) is None
    assert uinst.epoch_ms_to_date(0) is None
    assert uinst.epoch_ms_to_date("") is None
    assert uinst.epoch_ms_to_date(date(2026, 8, 25)) == date(2026, 8, 25)


def test_upstox_row_to_instrument():
    row = {
        "instrument_key": "NSE_FO|49520", "exchange_token": "49520",
        "trading_symbol": "INDIGO 26 AUG 5300 PE", "name": "INDIGO",
        "expiry": 1787616000000, "strike_price": 5300.0, "tick_size": 5.0,
        "lot_size": 625, "instrument_type": "PE", "segment": "NSE_FO",
        "underlying_symbol": "INDIGO",
    }
    inst = uinst.to_instrument(row)
    assert inst.token == surrogate_token("NSE_FO|49520")
    assert inst.data_key == "NSE_FO|49520"
    assert inst.exchange == "NFO"
    assert inst.underlying == "INDIGO"
    assert inst.kind is InstrumentKind.OPTION
    assert inst.instrument_type == "PE"
    assert inst.strike == 5300.0
    assert inst.expiry == date(2026, 8, 25)
    assert inst.lot_size == 625
    assert inst.tick_size == 0.05          # converted from paise
    assert inst.is_index is False


def test_upstox_index_instruments():
    nifty = uinst.index_instrument("NIFTY")
    assert nifty.data_key == "NSE_INDEX|Nifty 50" and nifty.is_index
    sensex = uinst.index_instrument("SENSEX")
    assert sensex.data_key == "BSE_INDEX|SENSEX" and sensex.exchange == "BSE"
    assert uinst.index_instrument("INDIGO") is None


def test_sensex_routes_to_bse_file():
    assert uinst.EXCHANGE_TO_CDN["BFO"] == "BSE"
    assert uinst.EXCHANGE_TO_CDN["NFO"] == "NSE"


# ======================= Upstox feed normalisation =======================

MARKET_MSG = {
    "feeds": {
        "NSE_FO|49520": {
            "fullFeed": {
                "marketFF": {
                    "ltpc": {"ltp": 158.0, "ltt": 1787616000000, "ltq": "625",
                             "cp": 117.85},
                    "marketLevel": {"bidAskQuote": [
                        {"bidP": 157.5, "bidQ": 625, "askP": 158.0, "askQ": 1250},
                        {"bidP": 157.0, "bidQ": 1250, "askP": 158.5, "askQ": 625},
                    ]},
                    "marketOHLC": {"ohlc": [
                        {"open": 120.0, "high": 160.0, "low": 118.0,
                         "close": 117.85, "vol": 284375}]},
                    "atp": 151.2, "vtt": 284375, "oi": 1875000,
                    "tbq": 12500, "tsq": 9375,
                }
            }
        }
    }
}

INDEX_MSG = {
    "feeds": {
        "NSE_INDEX|Nifty 50": {
            "fullFeed": {"indexFF": {
                "ltpc": {"ltp": 24150.0, "ltt": 1787616000000, "cp": 24000.0},
                "marketOHLC": {"ohlc": [{"open": 24100.0, "high": 24200.0,
                                         "low": 24050.0, "close": 24000.0}]},
            }}
        }
    }
}


def test_upstox_tick_normalises_to_canonical_shape():
    ticks = normalise_feed(MARKET_MSG)
    assert len(ticks) == 1
    t = ticks[0]
    assert t["instrument_token"] == surrogate_token("NSE_FO|49520")
    assert t["last_price"] == 158.0
    assert t["volume_traded"] == 284375
    assert t["oi"] == 1875000
    # prev close lands in ohlc.close -- the option reference price (R14)
    assert t["ohlc"]["close"] == 117.85
    # depth in the SAME shape the engine's trigger expects
    assert t["depth"]["buy"][0]["price"] == 157.5
    assert t["depth"]["sell"][0]["price"] == 158.0
    assert t["depth"]["sell"][0]["quantity"] == 1250


def test_normalised_upstox_tick_drives_the_trigger_unchanged():
    """The whole point: engine code is broker-agnostic."""
    from backend.engine.trigger import TriggerConfig, best_bid_ask, evaluate
    from .conftest import make_armed

    tick = normalise_feed(MARKET_MSG)[0]
    assert best_bid_ask(tick) == (157.5, 158.0)

    armed = make_armed(ref_price=117.85, token=tick["instrument_token"])
    evaluate(tick, armed, TriggerConfig())  # seed prev_ltp
    tick = dict(tick, last_price=(tick.get('last_price') or 0) + 1.0)
    sig = evaluate(tick, armed, TriggerConfig())
    assert sig is not None
    # diff is the positive tick itself now, not distance from the close.
    assert sig.diff == pytest.approx(1.0)
    assert sig.best_ask == 158.0


def test_index_feed_has_no_depth_and_is_still_valid():
    ticks = normalise_feed(INDEX_MSG)
    assert len(ticks) == 1
    assert ticks[0]["last_price"] == 24150.0
    assert "depth" not in ticks[0]          # indices carry no book


def test_feed_skips_unusable_payloads():
    assert normalise_feed({}) == []
    assert normalise_feed({"feeds": {}}) == []
    assert normalise_feed({"feeds": {"X": {}}}) == []
    assert normalise_feed({"feeds": {"X": {"fullFeed": {"marketFF": {
        "ltpc": {"ltp": 0.0}}}}}}) == []
    assert normalise_tick("X", {"fullFeed": {"marketFF": {}}}) is None


def test_token_lookup_overrides_surrogate():
    ticks = normalise_feed(MARKET_MSG, {"NSE_FO|49520": 999})
    assert ticks[0]["instrument_token"] == 999


# ======================= Upstox orders =======================

class StubHTTP:
    def __init__(self, response: dict, status: int = 200):
        self.response, self.calls = response, []

    def _r(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return type("R", (), {"json": lambda _s: self.response})()

    def post(self, url, **kw): return self._r("POST", url, **kw)
    def put(self, url, **kw): return self._r("PUT", url, **kw)
    def get(self, url, **kw): return self._r("GET", url, **kw)
    def delete(self, url, **kw): return self._r("DELETE", url, **kw)


def _upstox_instrument() -> Instrument:
    from dataclasses import replace
    return replace(make_instrument(), data_key="NSE_FO|49520",
                   trade_key="NSE_FO|49520")


def test_upstox_place_success():
    http = StubHTTP({"status": "success", "data": {"order_ids": ["241010000123"]}})
    res = uorders.place(http, "tok", instrument=_upstox_instrument(), side="BUY",
                        quantity=625, price=160.40, product="NRML", validity="DAY")
    assert res.success and res.order_id == "241010000123"
    _, url, kw = http.calls[0]
    assert url == uorders.PLACE_URL
    body = kw["json"]
    assert body["instrument_token"] == "NSE_FO|49520"
    assert body["product"] == "D"           # NRML -> D
    assert body["price"] == 160.40
    assert body["order_type"] == "LIMIT"


def test_upstox_rejection_is_classified():
    http = StubHTTP({"status": "error", "errors": [
        {"errorCode": "UDAPI1010", "message": "Insufficient funds for this order"}]})
    res = uorders.place(http, "tok", instrument=_upstox_instrument(), side="BUY",
                        quantity=625, price=1.0)
    assert res.success is False
    assert res.rejection_kind is RejectionKind.MARGIN
    assert "Insufficient funds" in res.error


def test_upstox_price_band_rejection_maps_to_lpp():
    assert uorders.classify("Order price is outside the daily price range") \
        is RejectionKind.LPP


def test_upstox_product_and_order_type_rules():
    # stock options are physically settled -> always delivery
    assert uorders.resolve_product(is_index=False, stock_product="MIS",
                                   index_product="MIS") == "D"
    assert uorders.resolve_product(is_index=True, stock_product="NRML",
                                   index_product="MIS") == "I"
    # MARKET is not accepted on stock options
    assert uorders.resolve_order_type(is_index=False, configured="MARKET") == "LIMIT"
    assert uorders.resolve_order_type(is_index=True, configured="MARKET") == "MARKET"


def test_upstox_order_state_normalises_status():
    http = StubHTTP({"status": "success", "data": {
        "order_id": "241010000123", "status": "complete",
        "filled_quantity": 625, "average_price": 158.0}})
    res = uorders.order_state(http, "tok", "241010000123")
    assert res.status == OrderStatus.COMPLETE
    assert res.success and res.average_price == 158.0


def test_upstox_interim_status_is_not_terminal():
    from backend.core.enums import is_terminal
    res = uorders.summarise({"order_id": "1", "status": "open pending"}, "1")
    assert not is_terminal(res.status)


def test_upstox_positions_normalise():
    http = StubHTTP({"status": "success", "data": [
        {"trading_symbol": "INDIGO 26 AUG 5300 PE", "quantity": 625,
         "average_price": 158.0, "last_price": 171.3, "pnl": 8312.5}]})
    pos = uorders.positions(http, "tok")
    assert pos["INDIGO 26 AUG 5300 PE"]["quantity"] == 625
    assert pos["INDIGO 26 AUG 5300 PE"]["average_price"] == 158.0


# ======================= registry =======================

def test_make_broker_rejects_unknown():
    from backend.brokers.base import BrokerError
    with pytest.raises(BrokerError, match="unknown broker"):
        make_broker("finvasia", {}, cache_dir=".")


def test_credentials_nested_and_flat():
    nested = {"zerodha": {"api_key": "z"}, "upstox": {"api_key": "u"}}
    assert credentials_for(ZERODHA, nested)["api_key"] == "z"
    assert credentials_for(UPSTOX, nested)["api_key"] == "u"
    flat = {"api_key": "legacy", "api_secret": "s"}
    assert credentials_for(ZERODHA, flat)["api_key"] == "legacy"


class FakeBroker:
    def __init__(self, name, chain=None):
        self.name, self._chain = name, chain or []

    def build_chain(self, underlying, expiry, spot, per_side):
        return self._chain


def test_same_broker_pair_copies_the_key():
    b = FakeBroker("zerodha")
    pair = BrokerPair(b, b)
    inst = make_instrument()
    from dataclasses import replace
    inst = replace(inst, data_key="123")
    assert pair.same_broker is True
    assert pair.resolve(inst).trade_key == "123"


def test_paper_pair_needs_no_trade_broker():
    b = FakeBroker("upstox")
    pair = BrokerPair(b, b, paper=True)
    assert pair.names == {"data": "upstox", "trade": PAPER}


def test_cross_broker_resolution_matches_on_contract_not_symbol():
    from dataclasses import replace
    data_inst = replace(make_instrument(tradingsymbol="INDIGO26AUG5300PE"),
                        data_key="NSE_FO|49520")
    trade_inst = replace(make_instrument(tradingsymbol="INDIGO 26 AUG 5300 PE"),
                         data_key="9876543")

    pair = BrokerPair(FakeBroker("upstox"), FakeBroker("zerodha", [trade_inst]))
    pair.index_trade_chain("INDIGO", date(2026, 8, 25), 5300.0, 4)

    resolved = pair.resolve(data_inst)
    assert resolved.token == data_inst.token          # feed key unchanged
    assert resolved.trade_key == "9876543"            # order goes to Kite's id
    assert resolved.tradingsymbol == "INDIGO 26 AUG 5300 PE"


def test_cross_broker_refuses_to_guess_a_missing_contract():
    from backend.brokers.base import BrokerError
    from dataclasses import replace
    data_inst = replace(make_instrument(), data_key="NSE_FO|1")
    pair = BrokerPair(FakeBroker("upstox"), FakeBroker("zerodha", []))
    pair.index_trade_chain("INDIGO", date(2026, 8, 25), 5300.0, 4)
    with pytest.raises(BrokerError, match="refusing to trade"):
        pair.resolve(data_inst)


def test_resolve_all_reports_missing_without_raising():
    from dataclasses import replace
    ok = replace(make_instrument(strike=5300.0), data_key="A")
    missing = replace(make_instrument(strike=9999.0,
                                      tradingsymbol="INDIGO26AUG9999PE"),
                      data_key="B")
    trade_inst = replace(make_instrument(strike=5300.0), data_key="T1")
    pair = BrokerPair(FakeBroker("upstox"), FakeBroker("zerodha", [trade_inst]))
    pair.index_trade_chain("INDIGO", date(2026, 8, 25), 5300.0, 4)

    resolved, unresolved = pair.resolve_all([ok, missing])
    assert len(resolved) == 1 and resolved[0].trade_key == "T1"
    assert unresolved == ["INDIGO26AUG9999PE"]


# ======================= config =======================

def test_broker_selection_validates(tmp_path):
    import json
    from pathlib import Path
    from backend.config.loader import ConfigError, merge_patch, parse
    raw = json.loads(Path("config/config.example.json").read_text(encoding="utf-8-sig"))

    for data_b, trade_b in [("zerodha", "zerodha"), ("upstox", "paper"),
                            ("upstox", "zerodha"), ("zerodha", "upstox")]:
        cfg = parse(merge_patch(raw, {"broker": {"data_broker": data_b,
                                                 "trade_broker": trade_b}}))
        assert cfg.broker.data_broker == data_b
        assert cfg.broker.trade_broker == trade_b

    with pytest.raises(ConfigError, match="data_broker"):
        parse(merge_patch(raw, {"broker": {"data_broker": "paper"}}))
    with pytest.raises(ConfigError, match="trade_broker"):
        parse(merge_patch(raw, {"broker": {"trade_broker": "finvasia"}}))


# ======================= Upstox auth =======================

def test_token_cache_freshness_spans_the_daily_reset():
    from datetime import datetime, timedelta
    from backend.brokers.upstox.auth import cache_is_fresh, last_reset_before
    from backend.core.timeutil import IST

    morning = datetime(2026, 8, 5, 9, 0, tzinfo=IST)      # after 03:30 reset
    assert last_reset_before(morning) == datetime(2026, 8, 5, 3, 30, tzinfo=IST)
    predawn = datetime(2026, 8, 5, 2, 0, tzinfo=IST)      # before it
    assert last_reset_before(predawn) == datetime(2026, 8, 4, 3, 30, tzinfo=IST)

    issued_today = {"access_token": "t",
                    "issued_at": datetime(2026, 8, 5, 8, 0, tzinfo=IST).isoformat()}
    assert cache_is_fresh(issued_today, now=morning) is True

    # yesterday evening is STALE this morning even though it is only hours old
    issued_yday = {"access_token": "t",
                   "issued_at": datetime(2026, 8, 4, 20, 0, tzinfo=IST).isoformat()}
    assert cache_is_fresh(issued_yday, now=morning) is False


@pytest.mark.parametrize("payload", [
    {}, {"access_token": ""}, {"access_token": "t"},
    {"access_token": "t", "issued_at": "not-a-date"},
    {"access_token": "t", "issued_at": None},
])
def test_cache_freshness_rejects_bad_payloads(payload):
    from backend.brokers.upstox.auth import cache_is_fresh
    assert cache_is_fresh(payload) is False


def test_cache_round_trip(tmp_path):
    from backend.brokers.upstox.auth import Session, read_cache, write_cache
    path = tmp_path / "upstox_token.json"
    write_cache(path, Session(access_token="abc", user_id="U1",
                              issued_at="2026-08-05T08:00:00+05:30"))
    assert read_cache(path)["access_token"] == "abc"
    assert list(tmp_path.glob("*.tmp")) == []          # atomic write


def test_auto_login_reports_missing_fields_precisely(monkeypatch):
    """A partially filled credentials block must say WHICH fields are missing."""
    import backend.brokers.upstox.auth as uauth
    from backend.brokers.base import BrokerError

    # pretend playwright is importable so we reach the credential check
    import sys, types
    stub = types.ModuleType("playwright.sync_api")
    stub.sync_playwright = lambda: None
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", stub)

    with pytest.raises(BrokerError) as exc:
        uauth.fetch_auth_code({"api_key": "k", "api_secret": "s",
                               "redirect_uri": "https://x"})
    msg = str(exc.value)
    assert "mobile_no" in msg and "totp_key" in msg and "pin" in msg


def test_login_falls_back_through_the_chain(tmp_path, monkeypatch):
    """cache -> access_token -> auth_code -> browser, in that order."""
    import backend.brokers.upstox.auth as uauth
    from backend.brokers.upstox.auth import Session, login

    monkeypatch.setattr(uauth, "verify",
                        lambda tok, timeout=10.0: {"user_id": "U1", "user_name": "V"})

    # 2. configured access_token wins when there is no cache
    s = login({"access_token": "pasted"}, cache_path=tmp_path / "tok.json")
    assert s.access_token == "pasted"

    # 1. now the cache short-circuits it
    s2 = login({"access_token": "different"}, cache_path=tmp_path / "tok.json")
    assert s2.access_token == "pasted"


def test_login_uses_auth_code_before_opening_a_browser(tmp_path, monkeypatch):
    import backend.brokers.upstox.auth as uauth
    from backend.brokers.upstox.auth import Session, login

    called = []
    monkeypatch.setattr(uauth, "verify", lambda *a, **k: None)
    monkeypatch.setattr(uauth, "exchange_code",
                        lambda **kw: Session(access_token="from-code",
                                             issued_at="2026-08-05T09:00:00+05:30"))
    monkeypatch.setattr(uauth, "fetch_auth_code",
                        lambda *a, **k: called.append("browser") or "CODE")

    s = login({"api_key": "k", "api_secret": "s", "redirect_uri": "https://x",
               "auth_code": "SUPPLIED"}, cache_path=tmp_path / "tok.json")
    assert s.access_token == "from-code"
    assert called == [], "auth_code was supplied; the browser must not open"


def test_login_error_lists_every_option(tmp_path, monkeypatch):
    import backend.brokers.upstox.auth as uauth
    from backend.brokers.base import BrokerError
    from backend.brokers.upstox.auth import login

    monkeypatch.setattr(uauth, "verify", lambda *a, **k: None)
    with pytest.raises(BrokerError) as exc:
        login({}, cache_path=tmp_path / "tok.json", auto=False)
    msg = str(exc.value)
    assert "access_token" in msg and "auth_code" in msg and "totp_key" in msg
