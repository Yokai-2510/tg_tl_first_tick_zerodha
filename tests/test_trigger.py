"""Entry trigger. BUILD_SPEC §6 — the hot path."""

from __future__ import annotations

from backend.engine.trigger import TriggerConfig, best_bid_ask, build_config, evaluate

from .conftest import make_armed, make_tick

CFG = TriggerConfig()


def test_fires_on_first_positive_diff():
    armed = make_armed(ref_price=117.85)
    sig = evaluate(make_tick(ltp=158.0), armed, CFG, sig_id="sig_1")
    assert sig is not None
    assert sig.diff == 158.0 - 117.85
    assert sig.tick_price == 158.0
    assert sig.best_ask == 158.0 and sig.best_bid == 157.5
    assert sig.quantity == 625
    assert armed.fired is True


def test_latches_after_firing():
    """R7: one entry per instrument per session."""
    armed = make_armed(ref_price=100.0)
    assert evaluate(make_tick(ltp=110.0), armed, CFG) is not None
    assert evaluate(make_tick(ltp=120.0), armed, CFG) is None
    assert evaluate(make_tick(ltp=130.0), armed, CFG) is None


def test_no_fire_on_negative_or_zero_diff():
    armed = make_armed(ref_price=117.85)
    assert evaluate(make_tick(ltp=100.0), armed, CFG) is None
    assert evaluate(make_tick(ltp=117.85), armed, CFG) is None
    assert armed.fired is False          # stays armed for a later tick


def test_min_diff_threshold_is_exclusive():
    armed = make_armed(ref_price=100.0)
    cfg = TriggerConfig(min_diff=5.0)
    assert evaluate(make_tick(ltp=105.0), armed, cfg) is None    # equal, not >
    assert evaluate(make_tick(ltp=105.05), armed, cfg) is not None


def test_require_depth_blocks_when_no_ask():
    armed = make_armed(ref_price=100.0)
    assert evaluate(make_tick(ltp=110.0, ask=0.0), armed, TriggerConfig()) is None
    assert armed.fired is False          # must remain armed

    armed2 = make_armed(ref_price=100.0)
    sig = evaluate(make_tick(ltp=110.0, ask=0.0), armed2,
                   TriggerConfig(require_depth=False))
    assert sig is not None and sig.best_ask == 0.0


def test_quote_mode_tick_without_depth_key():
    """QUOTE mode ticks have no 'depth' key at all — must not KeyError."""
    armed = make_armed(ref_price=100.0)
    tick = make_tick(ltp=110.0, depth=False)
    assert "depth" not in tick
    assert evaluate(tick, armed, TriggerConfig()) is None
    assert evaluate(tick, armed, TriggerConfig(require_depth=False)) is not None


def test_premium_bounds():
    armed = make_armed(ref_price=100.0)
    assert evaluate(make_tick(ltp=110.0), armed, TriggerConfig(min_premium=200.0)) is None
    assert evaluate(make_tick(ltp=110.0), armed, TriggerConfig(max_premium=50.0)) is None
    assert armed.fired is False
    assert evaluate(make_tick(ltp=110.0), armed,
                    TriggerConfig(min_premium=50.0, max_premium=200.0)) is not None


def test_zero_or_missing_prices_are_safe():
    armed = make_armed(ref_price=100.0)
    assert evaluate({"instrument_token": 111}, armed, CFG) is None
    assert evaluate(make_tick(ltp=0.0), armed, CFG) is None
    assert evaluate(make_tick(ltp=-5.0), armed, CFG) is None
    assert make_armed(ref_price=0.0).fired is False
    assert evaluate(make_tick(ltp=110.0), make_armed(ref_price=0.0), CFG) is None


def test_best_bid_ask_variants():
    assert best_bid_ask({}) == (0.0, 0.0)
    assert best_bid_ask({"depth": None}) == (0.0, 0.0)
    assert best_bid_ask({"depth": {"buy": [], "sell": []}}) == (0.0, 0.0)
    assert best_bid_ask({"depth": {"buy": [{"price": 1.5}], "sell": [{"price": 2.0}]}}) \
        == (1.5, 2.0)


def test_signal_carries_instrument_metadata():
    armed = make_armed(ref_price=100.0)
    sig = evaluate(make_tick(ltp=110.0), armed, CFG, sig_id="sig_x", t_tick_ns=42)
    assert sig.sig_id == "sig_x"
    assert sig.t_tick_ns == 42
    assert sig.tradingsymbol == "INDIGO26AUG5300PE"
    assert sig.underlying == "INDIGO" and sig.option_type == "PE"
    assert sig.strike == 5300.0 and sig.tick_size == 0.05
    assert sig.exchange == "NFO" and sig.is_index is False
    assert sig.t_signal_ns >= sig.t_tick_ns


def test_build_config_from_dict_and_object():
    d = build_config({"min_diff": 1.0, "require_depth": False,
                      "min_premium": 5.0, "max_premium": 500.0})
    assert (d.min_diff, d.require_depth, d.min_premium, d.max_premium) \
        == (1.0, False, 5.0, 500.0)

    class Obj:
        min_diff, require_depth, min_premium, max_premium = 2.0, True, 0.0, 0.0
    o = build_config(Obj())
    assert o.min_diff == 2.0 and o.require_depth is True


def test_hot_path_does_no_io(monkeypatch):
    """R1: evaluate() must not log, print, or open anything."""
    import builtins
    calls = []
    monkeypatch.setattr(builtins, "print", lambda *a, **k: calls.append("print"))
    real_open = builtins.open
    monkeypatch.setattr(builtins, "open", lambda *a, **k: calls.append("open") or real_open(*a, **k))
    evaluate(make_tick(ltp=158.0), make_armed(ref_price=117.85), CFG)
    assert calls == []
