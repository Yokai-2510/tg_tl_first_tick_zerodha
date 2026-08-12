"""
End-to-end integration against a FAKE broker.

Covers the paths that unit tests cannot: a signal travelling from a tick all
the way to a filled position, exits firing from live prices, and every API
endpoint responding with the documented envelope.

No network, no real broker, no clock dependency.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.server import API_PREFIX, create_app
from backend.core.enums import ExitTrigger, Phase, PositionStatus, TradingMode
from backend.core.timeutil import IST, epoch_us, mono_ns
from backend.engine import exits as exits_mod
from backend.engine.executor import Executor
from backend.engine.feed import Feed
from backend.engine.positions import PositionBook
from backend.engine.recorder import Recorder
from backend.engine.scheduler import Scheduler
from backend.engine.trigger import build_config as build_trigger_cfg

from .conftest import make_instrument, make_position, make_tick


# ---------------------------------------------------------------- fake broker

class FakeKite:
    """Minimal Kite stand-in: records calls, returns plausible shapes."""

    def __init__(self, *, reject_first: str | None = None):
        self.placed: list[dict] = []
        self.reject_first = reject_first
        self._n = 0

    def place_order(self, **kw):
        self._n += 1
        self.placed.append(kw)
        if self.reject_first and self._n == 1:
            raise RuntimeError(self.reject_first)
        return f"ORD{self._n:04d}"

    def order_history(self, order_id):
        return [{"order_id": order_id, "status": "OPEN PENDING"},
                {"order_id": order_id, "status": "COMPLETE",
                 "filled_quantity": 625, "average_price": 158.0}]

    def positions(self):
        return {"day": [], "net": []}

    def orders(self):
        return []

    def margins(self):
        return {"equity": {"available": {"live_balance": 500000.0}, "net": 500000.0}}


# ---------------------------------------------------------------- harness

class Harness:
    """A cut-down Application good enough to drive the API and the engine."""

    def __init__(self, cfg, tmp_path, kite=None, config_path=None):
        self.cfg = cfg
        self.kite = kite or FakeKite()
        self._config_path = config_path
        self.started_at = time.monotonic()
        self.recorder = Recorder(tmp_path, compression="none", flush_interval_ms=50)
        self.recorder.start()
        self.book = PositionBook(max_per_symbol=cfg.positions.max_per_symbol,
                                 max_concurrent=cfg.positions.max_concurrent)
        self.feed = Feed(recorder=self.recorder,
                         trigger_cfg=build_trigger_cfg(cfg.entry))
        self.scheduler = Scheduler(cfg)
        self.exit_cfg = exits_mod.build_config(cfg.exits)
        self.executor = Executor(kite=self.kite, cfg=cfg, book=self.book,
                                 feed=self.feed, recorder=self.recorder)
        self.instruments: dict[int, object] = {}
        self.by_symbol: dict[str, object] = {}
        self.events: list[dict] = []
        self.logs: list[dict] = []
        self.signals: list[dict] = []
        self.order_records: list[dict] = []
        self.shortlist = None
        self.nifty50: list[str] = []
        self.subscribed_count = 0
        self.kfeed = None
        self._tickets: dict[str, float] = {}
        self._halted = False
        from backend.api.ws_push import WsHub
        from backend.config.loader import ConfigStore
        self.hub = WsHub()
        # Point the store at a COPY under tmp_path: a config PATCH calls
        # store.save(), which must never write into the repo, and the store's
        # auth_token must match the one the tests authenticate with.
        self.store = ConfigStore(config_path or (tmp_path / "config.json"))
        self.store.load()
        # Signals must reach the recorder, exactly as the real Application wires it.
        self.feed.on_signal = self._on_signal

    # the API expects these
    uptime_s = property(lambda self: round(time.monotonic() - self.started_at, 1))

    def status_payload(self):
        return {**self.scheduler.status(), "mode": str(self.cfg.trading_mode.mode),
                "halted": self._halted, "uptime_s": self.uptime_s,
                "feed": {"connected": False}, "engine": self.feed.stats(),
                "recorder": self.recorder.stats(),
                "positions": self.book.summary(), "ws_clients": 0}

    def universe_payload(self):
        return {"nifty50": self.nifty50, "indices": [], "tradeable": [],
                "buffer": [], "subscribed": self.subscribed_count,
                "armed": self.feed.armed_view()}

    def ranking_payload(self):
        return {"ranked": []}

    def market_payload(self):
        return {str(t): {"ltp": v.ltp, "bid": v.bid, "ask": v.ask}
                for t, v in self.feed.snapshot().items()}

    def orders_payload(self):
        return self.order_records

    def signals_payload(self):
        return self.signals

    def latency_payload(self):
        return {"trades": self.executor.latencies, "median_tick_to_fill_ms": None}

    def snapshot_for(self, topic):
        return {"status": self.status_payload, "market": self.market_payload,
                "positions": lambda: {"upsert": []}, "orders": self.orders_payload,
                "events": lambda: self.events, "logs": lambda: self.logs
                }.get(topic, dict)()

    def exit_all(self, trigger):
        return sum(1 for p in self.book.open_positions()
                   if self.executor.request_exit(p, trigger))

    def kill_switch(self):
        self._halted = True
        self.feed.disarm()
        return self.exit_all(ExitTrigger.MANUAL_API)

    def reconcile_now(self):
        return {"confirmed": [], "closed_externally": [], "qty_drift": [], "adopted": []}

    def edit_manual(self, action, symbol, body):
        return {"manual_instruments": [{"symbol": symbol}]}

    def on_config_changed(self, cfg, changed):
        self.cfg = cfg

    def _on_signal(self, sig):
        self.signals.append({"sig_id": sig.sig_id, "sym": sig.tradingsymbol,
                             "diff": sig.diff})
        self.recorder.event("SIGNAL", {"sig_id": sig.sig_id, "token": sig.token,
                                       "sym": sig.tradingsymbol, "diff": sig.diff})

    def issue_ticket(self):
        t = "ticket-abc"
        self._tickets[t] = time.monotonic() + 60
        return t

    def consume_ticket(self, ticket):
        return self._tickets.pop(ticket, 0) > time.monotonic()

    def close(self):
        self.executor.stop()
        self.recorder.stop()


def _tmp_config(tmp_path, **overrides):
    """Write a private copy of the example config so tests never touch the repo."""
    raw = json.loads(Path("config/config.example.json").read_text(encoding="utf-8-sig"))
    raw.setdefault("api", {})["auth_token"] = "test-token"
    for section, patch in overrides.items():
        raw.setdefault(section, {}).update(patch)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


@pytest.fixture
def harness(tmp_path):
    from backend.config.loader import load
    path = _tmp_config(tmp_path)
    cfg = load(path)
    h = Harness(cfg, tmp_path, config_path=path)
    yield h
    h.close()


@pytest.fixture
def client(harness):
    with TestClient(create_app(harness)) as c:
        c.headers.update({"Authorization": "Bearer test-token"})
        yield c


# ---------------------------------------------------------------- API

def test_health_is_unauthenticated(harness):
    with TestClient(create_app(harness)) as c:
        r = c.get(f"{API_PREFIX}/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["data"]["status"] == "ok"


def test_auth_is_required(harness):
    with TestClient(create_app(harness)) as c:
        assert c.get(f"{API_PREFIX}/status").status_code == 401
        r = c.get(f"{API_PREFIX}/status", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "AUTH_INVALID"


@pytest.mark.parametrize("path", [
    "/status", "/config", "/universe", "/universe/ranking", "/market/snapshot",
    "/positions", "/positions/closed", "/orders", "/signals", "/latency",
    "/recorder/stats", "/events", "/logs",
])
def test_all_read_endpoints_respond(client, path):
    r = client.get(f"{API_PREFIX}{path}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and "data" in body and "ts" in body


def test_status_shape(client):
    data = client.get(f"{API_PREFIX}/status").json()["data"]
    for key in ("phase", "mode", "engine", "recorder", "positions", "schedule"):
        assert key in data, f"missing {key}"


def test_config_endpoint_returns_schema(client):
    data = client.get(f"{API_PREFIX}/config").json()["data"]
    assert "config" in data and "schema" in data
    assert data["schema"]["type"] == "object"


def test_config_patch_applies_and_validates(client, harness):
    r = client.post(f"{API_PREFIX}/config",
                    json={"exits": {"stop_loss": {"percentage": -7.5}}})
    assert r.status_code == 200
    assert r.json()["data"]["changed"] == ["exits.stop_loss.percentage"]

    bad = client.post(f"{API_PREFIX}/config",
                      json={"exits": {"stop_loss": {"percentage": 7.5}}})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "CONFIG_INVALID"


def test_unknown_position_is_404(client):
    r = client.get(f"{API_PREFIX}/positions/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_kill_switch_requires_confirmation(client):
    assert client.post(f"{API_PREFIX}/control/kill_switch", json={}).status_code == 400
    r = client.post(f"{API_PREFIX}/control/kill_switch", json={"confirm": "KILL"})
    assert r.status_code == 200 and r.json()["data"]["halted"] is True


def test_arm_and_disarm(client, harness):
    client.post(f"{API_PREFIX}/control/arm")
    assert harness.feed.entries_enabled is True
    client.post(f"{API_PREFIX}/control/disarm")
    assert harness.feed.entries_enabled is False


def test_force_phase_refused_in_live_mode(client, harness):
    harness.cfg.trading_mode.mode = TradingMode.LIVE
    r = client.post(f"{API_PREFIX}/control/phase", json={"phase": "TRADING"})
    assert r.status_code == 409


def test_ws_ticket_then_connect(client, harness):
    ticket = client.post(f"{API_PREFIX}/auth/ws-ticket").json()["data"]["ticket"]
    with client.websocket_connect(f"{API_PREFIX}/ws?token={ticket}") as ws:
        ws.send_text(json.dumps({"op": "subscribe", "topics": ["status"]}))
        frame = json.loads(ws.receive_text())
        assert frame["topic"] == "status" and frame["type"] == "snapshot"
        assert "phase" in frame["data"]


def test_ws_rejects_bad_token(client):
    with pytest.raises(Exception):
        with client.websocket_connect(f"{API_PREFIX}/ws?token=nope"):
            pass


def test_ws_ping_pong(client):
    with client.websocket_connect(f"{API_PREFIX}/ws?token=test-token") as ws:
        ws.send_text(json.dumps({"op": "ping"}))
        assert json.loads(ws.receive_text())["op"] == "pong"


# ---------------------------------------------------------------- engine flow

def _arm_and_fire(harness, *, ltp=158.0, ask=158.0, ref=117.85):
    inst = make_instrument(token=555)
    harness.instruments[inst.token] = inst
    harness.by_symbol[inst.tradingsymbol] = inst
    harness.feed.arm([inst], {inst.token: ref})
    harness.feed.phase = Phase.TRADING
    harness.feed.enable_entries(fire_after_ns=0, deadline_ns=10 ** 18,
                                session_prefix="sig_test_")
    harness.feed.on_tick_batch(
        [make_tick(token=555, ltp=ltp, ask=ask, bid=ask - 0.5)], recv_ns=mono_ns())
    return harness.feed.intent_q.get_nowait()


def test_paper_entry_end_to_end(harness):
    """Tick -> signal -> marketable limit -> filled paper position."""
    sig = _arm_and_fire(harness)
    assert sig.diff == pytest.approx(158.0 - 117.85)

    pos = harness.executor.execute_entry(sig)
    assert pos is not None
    assert pos.status is PositionStatus.ACTIVE
    assert pos.mode is TradingMode.PAPER
    assert pos.quantity == 625
    # paper fills at the touch, capped by our limit (158 * 1.015 -> 160.40)
    assert pos.entry.price == pytest.approx(158.0)
    assert harness.kite.placed == [], "paper mode must not call the broker"
    assert harness.executor.latencies, "latency must be recorded for every entry"


def test_live_entry_places_a_marketable_limit(tmp_path):
    from backend.config.loader import load
    path = _tmp_config(tmp_path, trading_mode={"mode": "live"})
    h = Harness(load(path), tmp_path, kite=FakeKite(), config_path=path)
    try:
        sig = _arm_and_fire(h)
        pos = h.executor.execute_entry(sig)
        assert pos is not None and pos.status is PositionStatus.ACTIVE
        sent = h.kite.placed[0]
        assert sent["order_type"] == "LIMIT"          # never MARKET on a stock option
        assert sent["validity"] == "IOC"
        assert sent["product"] == "NRML"              # stock option -> NRML
        assert sent["price"] == pytest.approx(160.40)  # 158 * 1.015, ceil to tick
        assert sent["quantity"] == 625
        assert pos.entry.price == 158.0               # filled at the ask
    finally:
        h.close()


def test_live_entry_retries_after_lpp_rejection(tmp_path):
    from backend.config.loader import load
    path = _tmp_config(tmp_path, trading_mode={"mode": "live"})
    kite = FakeKite(reject_first="This order is outside the allowed LPP limit (150.00).")
    h = Harness(load(path), tmp_path, kite=kite, config_path=path)
    try:
        sig = _arm_and_fire(h)
        pos = h.executor.execute_entry(sig)
        assert len(kite.placed) == 2, "LPP rejection must trigger exactly one retry"
        assert kite.placed[1]["price"] <= 150.00, "retry must land inside the LPP band"
        assert pos is not None and pos.status is PositionStatus.ACTIVE
    finally:
        h.close()


def test_margin_rejection_is_not_retried(tmp_path):
    from backend.config.loader import load
    path = _tmp_config(tmp_path, trading_mode={"mode": "live"})

    class AlwaysReject(FakeKite):
        def place_order(self, **kw):
            self.placed.append(kw)
            raise RuntimeError("Insufficient funds")

    h = Harness(load(path), tmp_path, kite=AlwaysReject(), config_path=path)
    try:
        sig = _arm_and_fire(h)
        pos = h.executor.execute_entry(sig)
        assert pos is None
        assert len(h.kite.placed) == 1, "margin rejection must not be retried"
    finally:
        h.close()


def test_max_per_symbol_blocks_a_second_entry(harness):
    sig = _arm_and_fire(harness)
    assert harness.executor.execute_entry(sig) is not None
    # a second signal for the same symbol must be refused by the book
    sig2 = sig.__class__(**{**sig.__dict__, "sig_id": "sig_test_002"}) \
        if hasattr(sig, "__dict__") else sig
    assert harness.executor.execute_entry(sig2) is None


def test_exit_fires_from_stop_loss_and_closes_paper_position(harness):
    sig = _arm_and_fire(harness)
    pos = harness.executor.execute_entry(sig)

    pos.live.ltp = pos.entry.price * 0.90            # -10% vs SL of -5%
    pos.live.bid = pos.live.ltp - 0.5
    now = datetime(2026, 8, 5, 11, 0, tzinfo=IST)
    exits_mod.refresh_live(pos, harness.exit_cfg, now_us=epoch_us(now))
    hit, trigger = exits_mod.evaluate(pos, harness.exit_cfg, now)
    assert hit and trigger is ExitTrigger.STOP_LOSS

    assert harness.executor.request_exit(pos, trigger) is True
    assert harness.executor.request_exit(pos, trigger) is False   # idempotent
    harness.executor.execute_exit(pos, trigger)
    assert pos.status is PositionStatus.CLOSED
    assert pos.exit.trigger is ExitTrigger.STOP_LOSS


def test_recorder_captured_the_session(harness, tmp_path):
    _arm_and_fire(harness)
    time.sleep(0.3)
    harness.recorder.stop()
    files = list(Path(tmp_path).rglob("*.ndjson"))
    assert files
    kinds = {json.loads(l)["t"]
             for l in files[0].read_text(encoding="utf-8").splitlines() if l}
    assert "TICK" in kinds and "SIGNAL" in kinds


def test_positions_appear_in_the_api(client, harness):
    sig = _arm_and_fire(harness)
    harness.executor.execute_entry(sig)
    rows = client.get(f"{API_PREFIX}/positions").json()["data"]
    assert len(rows) == 1
    assert rows[0]["instrument"]["tradingsymbol"] == "INDIGO26AUG5300PE"
    assert rows[0]["status"] == "ACTIVE"

    r = client.post(f"{API_PREFIX}/positions/{rows[0]['pos_id']}/exit")
    assert r.status_code == 200 and r.json()["data"]["exiting"] is True


# ---------------------------------------------------------------- CORS

def _preflight(harness, origins: list[str], origin: str):
    """A real browser preflight, through the middleware create_app installed."""
    harness.cfg.api.cors_origins = origins
    with TestClient(create_app(harness)) as c:
        return c.options(
            f"{API_PREFIX}/status",
            headers={"Origin": origin,
                     "Access-Control-Request-Method": "GET",
                     "Access-Control-Request-Headers": "Authorization"},
        )


def test_preflight_allows_an_exact_origin(harness):
    r = _preflight(harness, ["https://console.example.com"],
                   "https://console.example.com")
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "https://console.example.com"


def test_preflight_allows_a_wildcard_preview_origin(harness):
    """create_app must actually forward allow_origin_regex, not just compute it."""
    r = _preflight(harness, ["https://*.first-tick.pages.dev"],
                   "https://feature-x.first-tick.pages.dev")
    assert r.status_code == 200
    assert (r.headers["access-control-allow-origin"]
            == "https://feature-x.first-tick.pages.dev")


def test_preflight_rejects_an_unlisted_origin(harness):
    r = _preflight(harness, ["https://*.first-tick.pages.dev"], "https://evil.com")
    assert "access-control-allow-origin" not in r.headers


def test_get_from_an_allowed_origin_carries_the_header(harness):
    """Without this header on the real response the browser discards the body."""
    harness.cfg.api.cors_origins = ["https://*.pages.dev"]
    with TestClient(create_app(harness)) as c:
        r = c.get(f"{API_PREFIX}/health", headers={"Origin": "https://a.pages.dev"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "https://a.pages.dev"
