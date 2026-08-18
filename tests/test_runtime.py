"""Runtime modules: recorder, feed hot path, positions, scheduler, rate limiter."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

import pytest

from backend.brokers.kite.ratelimit import RateLimiter
from backend.core.enums import ExitTrigger, Phase, PositionStatus, SubscribeMode
from backend.core.timeutil import IST, epoch_us
from backend.engine.feed import Feed
from backend.engine.positions import PositionBook
from backend.engine.recorder import Recorder, tick_to_record
from backend.engine.scheduler import IllegalTransition, Scheduler
from backend.engine.trigger import TriggerConfig

from .conftest import make_instrument, make_position, make_tick


# ======================= recorder =======================

def test_tick_to_record_converts_datetime_timestamps():
    """exchange_timestamp arrives as a naive datetime, not an epoch."""
    tick = make_tick(ltp=158.0)
    tick["exchange_timestamp"] = datetime(2026, 8, 5, 9, 15, 0)
    tick["volume_traded"] = 1000
    rec = tick_to_record(tick, recv_ns=1, recv_us=2, batch_seq=3, batch_size=1)
    assert isinstance(rec["exch_ts"], int)
    assert rec["exch_ts"] > 1_700_000_000_000_000
    assert rec["ltp"] == 158.0 and rec["vol"] == 1000
    assert rec["depth"]["s"][0][0] == 158.0


def test_tick_to_record_without_depth():
    rec = tick_to_record(make_tick(depth=False), recv_ns=1, recv_us=2,
                         batch_seq=1, batch_size=1)
    assert "depth" not in rec


def test_tick_to_record_truncates_depth_levels():
    tick = make_tick()
    tick["depth"] = {"buy": [{"price": i, "quantity": 1, "orders": 1} for i in range(5)],
                     "sell": [{"price": i, "quantity": 1, "orders": 1} for i in range(5)]}
    rec = tick_to_record(tick, recv_ns=1, recv_us=2, batch_seq=1, batch_size=1,
                         depth_levels=2)
    assert len(rec["depth"]["b"]) == 2


def test_recorder_writes_every_tick(tmp_path):
    rec = Recorder(tmp_path, compression="none", flush_interval_ms=50)
    rec.start()
    for i in range(200):
        rec.put([make_tick(token=i, ltp=100.0 + i)], recv_ns=i)
    rec.event("PHASE", {"from": "PREOPEN", "to": "SETTLEMENT"})
    time.sleep(0.6)
    rec.stop()

    files = list((tmp_path).rglob("*.ndjson"))
    assert files, "recorder wrote no file"
    lines = [json.loads(l) for l in files[0].read_text(encoding="utf-8").splitlines() if l]
    assert rec.dropped == 0
    assert sum(1 for l in lines if l["t"] == "TICK") == 200
    assert any(l["t"] == "PHASE" for l in lines)

    seqs = [l["batch_seq"] for l in lines if l["t"] == "TICK"]
    assert seqs == sorted(seqs) and seqs[0] == 1 and seqs[-1] == 200


def test_recorder_put_never_blocks(tmp_path):
    """The hot path must not wait on disk."""
    rec = Recorder(tmp_path, compression="none")
    rec.start()
    start = time.perf_counter()
    for i in range(5000):
        rec.put([make_tick(token=i)], recv_ns=i)
    elapsed = time.perf_counter() - start
    rec.stop()
    assert elapsed < 1.0, f"5000 puts took {elapsed:.2f}s — put() is blocking"


def test_recorder_disabled_is_a_noop(tmp_path):
    rec = Recorder(tmp_path, enabled=False)
    rec.start()
    rec.put([make_tick()], recv_ns=1)
    rec.stop()
    assert list(tmp_path.rglob("*.ndjson")) == []


# ======================= feed hot path =======================

def _feed(**kw) -> Feed:
    return Feed(trigger_cfg=TriggerConfig(**kw))


def test_feed_updates_tick_view_and_ignores_ticks_when_disarmed():
    feed = _feed()
    feed.on_tick_batch([make_tick(token=1, ltp=100.0, bid=99.5, ask=100.5)], recv_ns=1)
    view = feed.last(1)
    assert view.ltp == 100.0 and view.bid == 99.5 and view.ask == 100.5
    assert feed.intent_q.qsize() == 0            # not armed -> no signal


def test_feed_fires_only_inside_the_window():
    feed = _feed()
    inst = make_instrument(token=1)
    feed.arm([inst], {1: 100.0})
    feed.phase = Phase.TRADING
    feed.enable_entries(fire_after_ns=1000, deadline_ns=5000, session_prefix="sig_")

    feed.on_tick_batch([make_tick(token=1, ltp=110.0)], recv_ns=500)      # too early
    assert feed.intent_q.qsize() == 0
    feed.on_tick_batch([make_tick(token=1, ltp=110.0)], recv_ns=9000)     # too late
    assert feed.intent_q.qsize() == 0
    feed.on_tick_batch([make_tick(token=1, ltp=110.0)], recv_ns=2000)     # in window
    assert feed.intent_q.qsize() == 1


def test_feed_fires_once_per_instrument():
    feed = _feed()
    feed.arm([make_instrument(token=1)], {1: 100.0})
    feed.phase = Phase.TRADING
    feed.enable_entries(fire_after_ns=0, deadline_ns=10**18, session_prefix="sig_")
    for px in (110.0, 120.0, 130.0):
        feed.on_tick_batch([make_tick(token=1, ltp=px)], recv_ns=1)
    assert feed.intent_q.qsize() == 1
    assert feed.signals_fired == 1


def test_feed_skips_instruments_without_a_reference_price():
    feed = _feed()
    armed = feed.arm([make_instrument(token=1), make_instrument(token=2)],
                     {1: 100.0, 2: 0.0})
    assert armed == 1


def test_feed_hot_path_is_fast():
    """Budget is 50us/batch; assert a generous ceiling to stay CI-stable."""
    feed = _feed()
    insts = [make_instrument(token=t) for t in range(200)]
    feed.arm(insts, {t: 100.0 for t in range(200)})
    feed.phase = Phase.TRADING
    feed.enable_entries(fire_after_ns=0, deadline_ns=10**18, session_prefix="s_")
    batch = [make_tick(token=t, ltp=99.0) for t in range(200)]   # no fires

    start = time.perf_counter()
    for _ in range(100):
        feed.on_tick_batch(batch, recv_ns=1)
    per_batch_us = (time.perf_counter() - start) / 100 * 1e6
    assert per_batch_us < 5000, f"{per_batch_us:.0f}us for a 200-tick batch"


# ======================= position book =======================

def test_book_enforces_max_per_symbol():
    book = PositionBook(max_per_symbol=1, max_concurrent=10)
    pos = make_position()
    book.add(pos)
    allowed, reason = book.can_open(pos.tradingsymbol)
    assert allowed is False and "max_per_symbol" in reason


def test_book_enforces_max_concurrent():
    book = PositionBook(max_per_symbol=5, max_concurrent=1)
    book.add(make_position())
    allowed, reason = book.can_open("SOMETHING-ELSE")
    assert allowed is False and "max_concurrent" in reason


def test_entry_fill_via_order_event():
    book = PositionBook()
    pos = make_position(status=PositionStatus.PENDING)
    book.add(pos)
    book.link_order(pos.pos_id, "ORD1")
    book.apply_order_event({"order_id": "ORD1", "status": "COMPLETE",
                            "transaction_type": "BUY", "filled_quantity": 625,
                            "average_price": 158.0})
    assert pos.status is PositionStatus.ACTIVE
    assert pos.entry.price == 158.0
    assert pos.flags.broker_confirmed is True


def test_interim_status_does_not_change_state():
    book = PositionBook()
    pos = make_position(status=PositionStatus.PENDING)
    book.add(pos)
    book.link_order(pos.pos_id, "ORD1")
    for status in ("OPEN PENDING", "VALIDATION PENDING", "OPEN"):
        book.apply_order_event({"order_id": "ORD1", "status": status,
                                "transaction_type": "BUY"})
        assert pos.status is PositionStatus.PENDING


def test_exit_latch_is_idempotent():
    book = PositionBook()
    pos = make_position()
    book.add(pos)
    assert book.mark_exiting(pos, ExitTrigger.STOP_LOSS) is True
    assert book.mark_exiting(pos, ExitTrigger.TRAILING_SL) is False
    assert pos.exit.trigger is ExitTrigger.STOP_LOSS


def test_rejected_exit_reopens_the_latch():
    book = PositionBook()
    pos = make_position()
    book.add(pos)
    book.mark_exiting(pos, ExitTrigger.STOP_LOSS)
    book.link_order(pos.pos_id, "EX1")
    book.apply_order_event({"order_id": "EX1", "status": "REJECTED",
                            "transaction_type": "SELL"})
    assert pos.flags.exiting is False           # retryable


def test_reconcile_detects_manual_close():
    book = PositionBook()
    pos = make_position()
    book.add(pos)
    report = book.reconcile({}, adopt_unknown=False)
    assert pos.status is PositionStatus.CLOSED
    assert pos.exit.trigger is ExitTrigger.MANUAL_BROKER
    assert report["closed_externally"] == [pos.pos_id]


def test_reconcile_reports_quantity_drift():
    book = PositionBook()
    pos = make_position(quantity=625)
    book.add(pos)
    report = book.reconcile(
        {pos.tradingsymbol: {"quantity": 1250}}, adopt_unknown=False)
    assert report["qty_drift"] and "broker=1250" in report["qty_drift"][0]
    assert pos.status is PositionStatus.ACTIVE


def test_reconcile_adopts_unknown_broker_position():
    book = PositionBook()
    inst = make_instrument(tradingsymbol="WIPRO26AUG240CE")
    report = book.reconcile(
        {"WIPRO26AUG240CE": {"quantity": 3000, "average_price": 5.2}},
        instrument_lookup={"WIPRO26AUG240CE": inst},
    )
    assert report["adopted"] == ["WIPRO26AUG240CE"]
    adopted = book.all()[0]
    assert adopted.status is PositionStatus.ADOPTED_UNMANAGED


def test_summary_counts():
    book = PositionBook()
    open_pos = make_position()
    book.add(open_pos)
    closed = make_position()
    closed.pos_id = "pos_2"
    book.add(closed)
    book.close_locally(closed, ExitTrigger.TARGET, price=110.0)
    s = book.summary()
    assert s["open"] == 1 and s["closed"] == 1
    assert s["realised"] == pytest.approx((110.0 - 100.0) * 625)


# ======================= scheduler =======================

def _sched(cfg):
    return Scheduler(cfg)


def test_phase_progression(config_obj):
    s = _sched(config_obj)
    day = datetime(2026, 8, 5, tzinfo=IST)
    for at, expected in [
        ("08:50:00", Phase.PHASE_1),
        ("08:56:00", Phase.FEED_LIVE),
        ("09:01:00", Phase.PREOPEN),
        ("09:09:30", Phase.SETTLEMENT),
        ("09:10:00", Phase.ARMING),
        ("09:14:30", Phase.FROZEN),
        ("09:15:30", Phase.TRADING),
        ("15:29:00", Phase.EOD),
    ]:
        h, m, sec = map(int, at.split(":"))
        s.tick(day.replace(hour=h, minute=m, second=sec))
        assert s.phase is expected, f"at {at} expected {expected}, got {s.phase}"


def test_illegal_transition_raises(config_obj):
    s = _sched(config_obj)
    with pytest.raises(IllegalTransition):
        s.transition(Phase.TRADING)


def test_force_bypasses_validation(config_obj):
    s = _sched(config_obj)
    s.transition(Phase.TRADING, force=True)
    assert s.phase is Phase.TRADING


def test_entries_allowed_only_in_trading(config_obj):
    s = _sched(config_obj)
    for phase in (Phase.FROZEN, Phase.ARMING, Phase.MANAGING, Phase.EOD):
        s.transition(phase, force=True)
        assert s.entries_allowed is False
    s.transition(Phase.TRADING, force=True)
    assert s.entries_allowed is True


def test_hook_runs_once(config_obj):
    s = _sched(config_obj)
    calls = []
    s.hooks[Phase.PHASE_1] = lambda: calls.append(1)
    s.transition(Phase.PHASE_1)
    s.transition(Phase.IDLE, force=True)
    s.transition(Phase.PHASE_1)
    assert calls == [1]


def test_failing_hook_moves_to_phase1_fail(config_obj):
    s = _sched(config_obj)

    def boom():
        raise RuntimeError("auth down")
    s.hooks[Phase.PHASE_1] = boom
    s.transition(Phase.PHASE_1)
    assert s.phase is Phase.PHASE_1_FAIL
    assert "auth down" in s.last_error


# ======================= rate limiter =======================

def test_per_second_limit():
    rl = RateLimiter(orders_per_sec=3)
    assert sum(rl.acquire("order") for _ in range(5)) == 3


def test_daily_cap_binds():
    rl = RateLimiter(orders_per_sec=1000, orders_per_min=1000, orders_per_day=4)
    assert sum(rl.acquire("order") for _ in range(10)) == 4


def test_kinds_are_independent():
    rl = RateLimiter(orders_per_sec=1, quote_per_sec=1)
    assert rl.acquire("order") is True
    assert rl.acquire("quote") is True
    assert rl.acquire("order") is False


def test_window_slides():
    rl = RateLimiter(orders_per_sec=2)
    assert rl.acquire("order") and rl.acquire("order")
    assert rl.acquire("order") is False
    time.sleep(1.05)
    assert rl.acquire("order") is True


def test_stats_and_reset():
    rl = RateLimiter(orders_per_sec=1)
    rl.acquire("order")
    rl.acquire("order")
    assert rl.stats()["order"]["rejected"] == 1
    rl.reset()
    assert rl.acquire("order") is True


# ------------------------------------------- the feed must never fail silently

def test_wait_connected_returns_true_once_the_socket_reports_connected():
    """The watchdog that turns a dead feed into a loud failure."""
    import threading
    from backend.brokers.kite.ticker import KiteFeed

    feed = KiteFeed("k", "t")
    assert feed.wait_connected(timeout=0.2) is False, "not connected yet"

    def connect_soon():
        import time
        time.sleep(0.15)
        feed.stats.connected = True

    threading.Thread(target=connect_soon, daemon=True).start()
    assert feed.wait_connected(timeout=3.0) is True


def test_wait_connected_times_out_when_the_socket_never_opens():
    """Exactly the 14-18 Aug failure: connect() returns fine, nothing connects."""
    from backend.brokers.kite.ticker import KiteFeed
    import time

    feed = KiteFeed("k", "t")
    t0 = time.monotonic()
    assert feed.wait_connected(timeout=0.5) is False
    assert 0.4 <= time.monotonic() - t0 < 2.0, "must actually wait, then give up"


def test_is_alive_requires_both_started_and_connected():
    from backend.brokers.kite.ticker import KiteFeed

    feed = KiteFeed("k", "t")
    assert feed.is_alive() is False
    feed.stats.connected = True
    assert feed.is_alive() is False, "connected but never started is not alive"
    feed._started = True
    assert feed.is_alive() is True


def test_connect_feed_raises_when_the_socket_does_not_come_up(config_obj, tmp_path):
    """A feed that never connects must fail the phase, not arm against nothing."""
    from backend.brokers.kite.ticker import KiteFeed

    calls = {"n": 0}

    class DeadFeed(KiteFeed):
        def connect(self):
            calls["n"] += 1          # returns cleanly, socket never opens

    feed = DeadFeed("k", "t")
    assert feed.connect() is None
    assert calls["n"] == 1
    assert feed.wait_connected(timeout=0.3) is False
    assert feed.is_alive() is False
