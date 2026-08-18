"""
Entry trigger — the hot path.

THE RULE: fire on the first POSITIVE LTP TICK, `current LTP > previous LTP` for
that same strike. No best ask, no previous close, no min_diff, no confirmation.

A single tick can never fire: the first one only seeds the baseline.
"""

from __future__ import annotations

from backend.engine.trigger import TriggerConfig, best_bid_ask, build_config, evaluate

from .conftest import make_armed, make_tick

CFG = TriggerConfig()


def feed(armed, prices, cfg=CFG, **kw):
    """Push a sequence of LTPs; return the first signal produced, or None."""
    for px in prices:
        sig = evaluate(make_tick(ltp=px), armed, cfg, **kw)
        if sig is not None:
            return sig
    return None


# ------------------------------------------------------------------ the rule

def test_the_first_tick_only_seeds_the_baseline():
    """Nothing to compare against yet, so it cannot fire however high it is."""
    armed = make_armed(ref_price=117.85)
    assert evaluate(make_tick(ltp=9999.0), armed, CFG) is None
    assert armed.fired is False
    assert armed.prev_ltp == 9999.0, "but it must be remembered"


def test_fires_on_the_first_positive_tick():
    armed = make_armed(ref_price=117.85)
    assert evaluate(make_tick(ltp=250.0), armed, CFG) is None      # seed
    sig = evaluate(make_tick(ltp=251.0), armed, CFG, sig_id="sig_1")
    assert sig is not None
    assert sig.diff == 1.0, "diff is the tick itself, not distance from a close"
    assert sig.tick_price == 251.0
    assert armed.fired is True


def test_the_previous_close_is_irrelevant():
    """250 -> 251 fires even though both are far BELOW the previous close."""
    armed = make_armed(ref_price=5000.0)
    assert feed(armed, [250.0, 251.0]) is not None


def test_a_tick_far_above_the_close_does_not_fire_on_its_own():
    """The mirror image: high vs the close means nothing without an uptick."""
    armed = make_armed(ref_price=10.0)
    assert feed(armed, [500.0, 499.0, 498.0]) is None
    assert armed.fired is False


def test_flat_and_down_ticks_never_fire():
    armed = make_armed(ref_price=100.0)
    assert feed(armed, [110.0, 110.0, 109.0, 108.0, 108.0]) is None
    assert armed.fired is False


def test_a_down_move_then_an_uptick_still_fires():
    """prev_ltp tracks every tick, so the comparison is always the latest pair."""
    armed = make_armed(ref_price=100.0)
    sig = feed(armed, [110.0, 105.0, 100.0, 100.5])
    assert sig is not None
    assert sig.tick_price == 100.5 and sig.diff == 0.5


def test_the_smallest_possible_uptick_qualifies():
    """min_diff is gone: one tick of 0.05 is a positive tick."""
    armed = make_armed(ref_price=100.0)
    sig = feed(armed, [20.00, 20.05])
    assert sig is not None and round(sig.diff, 2) == 0.05


def test_latches_after_firing():
    """R7: one entry per instrument per session."""
    armed = make_armed(ref_price=100.0)
    assert feed(armed, [100.0, 110.0]) is not None
    assert evaluate(make_tick(ltp=120.0), armed, CFG) is None
    assert evaluate(make_tick(ltp=130.0), armed, CFG) is None


# ------------------------------------------------- depth no longer gates it

def test_a_tick_with_no_depth_still_fires():
    """Depth is for pricing only. Requiring an ask used to throw away exactly the
    tick that should have fired, on strikes whose book had not arrived."""
    armed = make_armed(ref_price=100.0)
    evaluate({"last_price": 50.0}, armed, CFG)
    sig = evaluate({"last_price": 51.0}, armed, CFG)
    assert sig is not None
    assert sig.best_ask == 0.0 and sig.best_bid == 0.0


def test_require_depth_is_accepted_but_ignored():
    """Old configs must still load, and must not change the decision."""
    cfg = TriggerConfig(require_depth=True)
    armed = make_armed(ref_price=100.0)
    evaluate({"last_price": 50.0}, armed, cfg)
    assert evaluate({"last_price": 51.0}, armed, cfg) is not None


def test_min_diff_is_accepted_but_ignored():
    cfg = TriggerConfig(min_diff=500.0)
    armed = make_armed(ref_price=100.0)
    assert feed(armed, [20.00, 20.05], cfg) is not None


def test_depth_is_still_reported_for_pricing():
    armed = make_armed(ref_price=100.0)
    evaluate(make_tick(ltp=157.0), armed, CFG)
    sig = evaluate(make_tick(ltp=158.0), armed, CFG)
    assert sig.best_ask == 158.0 and sig.best_bid == 157.5


# ------------------------------------------------------------------ guards

def test_premium_bounds_still_apply():
    armed = make_armed(ref_price=100.0)
    assert feed(armed, [1.0, 2.0], TriggerConfig(min_premium=50.0)) is None

    armed2 = make_armed(ref_price=100.0)
    assert feed(armed2, [900.0, 901.0], TriggerConfig(max_premium=500.0)) is None

    armed3 = make_armed(ref_price=100.0)
    assert feed(armed3, [100.0, 101.0],
                TriggerConfig(min_premium=50.0, max_premium=500.0)) is not None


def test_zero_or_missing_prices_are_safe():
    armed = make_armed(ref_price=100.0)
    assert evaluate({}, armed, CFG) is None
    assert evaluate({"last_price": 0.0}, armed, CFG) is None
    assert evaluate({"last_price": None}, armed, CFG) is None
    assert armed.prev_ltp == 0.0, "a junk tick must not poison the baseline"


def test_a_zero_tick_between_two_real_ones_does_not_fire_spuriously():
    armed = make_armed(ref_price=100.0)
    evaluate(make_tick(ltp=100.0), armed, CFG)
    evaluate({"last_price": 0.0}, armed, CFG)
    assert armed.prev_ltp == 100.0
    assert evaluate(make_tick(ltp=99.0), armed, CFG) is None


def test_best_bid_ask_variants():
    assert best_bid_ask({}) == (0.0, 0.0)
    assert best_bid_ask({"depth": None}) == (0.0, 0.0)
    assert best_bid_ask({"depth": {"buy": [], "sell": []}}) == (0.0, 0.0)
    assert best_bid_ask({"depth": {"buy": [{"price": 1.5}], "sell": [{"price": 2.0}]}}) \
        == (1.5, 2.0)


def test_signal_carries_instrument_metadata():
    armed = make_armed(ref_price=100.0)
    evaluate(make_tick(ltp=109.0), armed, CFG)
    sig = evaluate(make_tick(ltp=110.0), armed, CFG, sig_id="sig_x", t_tick_ns=42)
    assert sig.sig_id == "sig_x"
    assert sig.t_tick_ns == 42
    assert sig.tradingsymbol == "INDIGO26AUG5300PE"
    assert sig.underlying == "INDIGO" and sig.option_type == "PE"
    assert sig.strike == 5300.0 and sig.tick_size == 0.05
    assert sig.exchange == "NFO" and sig.is_index is False
    assert sig.ref_price == 100.0, "previous close still reported, just not used"
    assert sig.t_signal_ns >= sig.t_tick_ns


def test_build_config_from_dict_and_object():
    d = build_config({"min_diff": 1.0, "require_depth": False,
                      "min_premium": 5.0, "max_premium": 500.0})
    assert (d.min_premium, d.max_premium) == (5.0, 500.0)

    class Obj:
        min_diff, require_depth, min_premium, max_premium = 2.0, True, 7.0, 900.0
    o = build_config(Obj())
    assert (o.min_premium, o.max_premium) == (7.0, 900.0)


def test_hot_path_does_no_io(monkeypatch):
    """R1: evaluate() must not log, print, or open anything."""
    import builtins
    calls = []
    monkeypatch.setattr(builtins, "print", lambda *a, **k: calls.append("print"))
    real_open = builtins.open
    monkeypatch.setattr(builtins, "open",
                        lambda *a, **k: calls.append("open") or real_open(*a, **k))
    armed = make_armed(ref_price=117.85)
    evaluate(make_tick(ltp=157.0), armed, CFG)
    evaluate(make_tick(ltp=158.0), armed, CFG)
    assert calls == []
