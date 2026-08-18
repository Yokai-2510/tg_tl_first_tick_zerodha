"""
Application wiring — the object that owns every subsystem.

Kept deliberately thin: it composes modules and runs the phase hooks. All
real logic lives in the modules it wires together.
"""

from __future__ import annotations

import secrets
import threading
import time
from pathlib import Path
from typing import Any

from .api.ws_push import WsHub
from .brokers.kite import instruments as kinst
from .brokers.kite import portfolio as kportfolio
from .brokers.kite import quotes as kquotes
from .brokers.kite import ratelimit as kratelimit
from .config.loader import ConfigStore, load_credentials
from .core.enums import ExitTrigger, Phase, SubscribeMode, TradingMode
from .core.symbols import is_index, option_exchange, spot_quote_key
from .core.timeutil import epoch_us, mono_ns, now_ist, today_at
from .data import nifty50
from .engine import exits as exits_mod
from .engine import universe as universe_mod
from .engine.executor import Executor
from .engine.feed import Feed
from .engine.positions import PositionBook
from .engine.recorder import Recorder, prune_old_sessions
from .engine.scheduler import Scheduler
from .engine.trigger import build_config as build_trigger_cfg


class Application:
    """Composition root."""

    def __init__(self, config_path: str = "config/config.json",
                 credentials_path: str = "config/credentials.json", log=None):
        self.store = ConfigStore(config_path)
        self.cfg = self.store.load()
        self.credentials_path = credentials_path
        self.log = log or _NullLog()
        self.started_at = time.monotonic()

        data_dir = Path(self.cfg.system.data_dir)
        self.recorder = Recorder(
            data_dir,
            enabled=self.cfg.recorder.enabled,
            compression=self.cfg.recorder.compression,
            depth_levels=self.cfg.recorder.record_depth_levels,
            flush_interval_ms=self.cfg.recorder.flush_interval_ms,
            max_disk_mb=self.cfg.recorder.max_disk_mb,
            on_disk_full=str(self.cfg.recorder.on_disk_full),
        )
        self.limiter = kratelimit.from_config(self.cfg.broker.rate_limits)
        self.hub = WsHub()
        self.scheduler = Scheduler(self.cfg, log=self.log, recorder=self.recorder)
        self.book = PositionBook(
            max_per_symbol=self.cfg.positions.max_per_symbol,
            max_concurrent=self.cfg.positions.max_concurrent,
        )
        self.feed = Feed(recorder=self.recorder,
                         trigger_cfg=build_trigger_cfg(self.cfg.entry))
        self.exit_cfg = exits_mod.build_config(self.cfg.exits)

        self.kite: Any = None
        self.session: Any = None
        self.kfeed: Any = None
        self.executor: Executor | None = None

        self.master: dict[str, list[dict]] = {}
        self.nifty50: list[str] = []
        self.settlement: dict[str, dict] = {}
        self.shortlist: Any = None
        self.instruments: dict[int, Any] = {}
        self.by_symbol: dict[str, Any] = {}
        self.subscribed_count = 0

        #: Cached broker capital. Refreshed on the sync thread — margins is a REST
        #: call and must never run inside an API request.
        self.capital: dict = {}
        self.events: list[dict] = []
        self.logs: list[dict] = []
        self.signals: list[dict] = []
        self.order_records: list[dict] = []
        self._tickets: dict[str, float] = {}
        self._halted = False
        self._running = False

        self.scheduler.hooks = {
            Phase.PHASE_1: self.phase1,
            Phase.FEED_LIVE: self.connect_feed,
            Phase.SETTLEMENT: self.capture_settlement,
            Phase.ARMING: self.arm_wave2,
            Phase.TRADING: self.start_trading,
            Phase.EOD: self.square_off,
        }
        self.scheduler.on_reset = self.reset_session
        self.book.on_change = self._on_position_change
        self.feed.on_signal = self._on_signal

    # ================= phases =================

    def phase1(self) -> None:
        """Auth, instrument master, universe list, preflight."""
        from .brokers.kite import auth as kauth

        creds = load_credentials(self.credentials_path)
        cache = Path(self.cfg.system.data_dir) / "access_token.json"
        self.kite, self.session = kauth.login(creds, cache_path=cache)
        self.log.info(f"AUTH ok: {self.session.user_name or self.session.user_id}")

        for exch in ("NFO", "BFO", "NSE"):
            self.master[exch] = kinst.load_master(self.kite, exch)
            self.log.info(f"master {exch}: {len(self.master[exch])} rows")

        result = nifty50.load(
            Path(self.cfg.system.data_dir) / "nifty50.txt",
            fallback_symbols=self.cfg.universe.nifty50_fallback_symbols,
            log=self.log,
        )
        self.nifty50 = list(result.symbols)

        projected = universe_mod.project_count(
            n_stocks=len(self.nifty50),
            n_indices=len(self.cfg.universe.enabled_indices),
            top_n_gainers=self.cfg.universe.top_n_gainers,
            top_n_losers=self.cfg.universe.top_n_losers,
            candidate_buffer=self.cfg.universe.candidate_buffer,
            strikes_per_side=self.cfg.instruments.strikes_per_side,
        )
        cap = self.cfg.instruments.subscription_soft_cap
        if projected > cap:
            raise RuntimeError(
                f"projected {projected} instruments exceeds subscription_soft_cap {cap}"
            )
        self.log.info(f"preflight ok — projected {projected} instruments (cap {cap})")

        self.refresh_capital()
        prune_old_sessions(self.cfg.system.data_dir, self.cfg.recorder.retention_days)
        self.executor = Executor(
            kite=self.kite, cfg=self.cfg, book=self.book, feed=self.feed,
            limiter=self.limiter, recorder=self.recorder, log=self.log,
            audit=self._on_order_record,
        )
        self.executor.start()

    def connect_feed(self) -> None:
        """08:55 — open the single websocket, subscribe wave 1, start recording."""
        from .brokers.kite.ticker import KiteFeed

        if self.kfeed is not None:          # never leak a previous day's socket
            try:
                self.kfeed.close()
            except Exception:
                pass
            self.kfeed = None
        self.recorder.start()
        stocks = [i for i in (kinst.equity_instrument(self.master["NSE"], s)
                              for s in self.nifty50) if i is not None]
        indices = self._index_spot_instruments()
        plan = universe_mod.build_wave1(
            stocks, indices, soft_cap=self.cfg.instruments.subscription_soft_cap)

        for inst in plan.instruments:
            self.instruments[inst.token] = inst
            self.by_symbol[inst.tradingsymbol] = inst
        self.feed.register_symbols(plan.instruments)
        self.recorder.symbol_lookup = self.feed.symbol_lookup()

        self.kfeed = KiteFeed(
            self.session.api_key, self.session.access_token,
            reconnect_max_tries=self.cfg.broker.ws.reconnect_max_tries,
            reconnect_max_delay=self.cfg.broker.ws.reconnect_max_delay_s,
        )
        self.kfeed.on_tick_batch = self.feed.on_tick_batch
        self.kfeed.on_order_event = self._on_order_event
        self.kfeed.on_state = self._on_feed_state
        for mode, tokens in plan.by_mode().items():
            self.kfeed.add(tokens, mode)
        self.kfeed.connect()

        # connect() is fire-and-forget and never raises, so a socket that fails to
        # open is indistinguishable from a healthy one. Blocking here turns five
        # silent dead sessions into one loud PHASE_1_FAIL. The scheduler treats a
        # FEED_LIVE hook exception as a failed pre-market, which is exactly right:
        # arming instruments against a feed that will never tick is worse than
        # not trading.
        if not self.kfeed.wait_connected(self.cfg.broker.ws.connect_timeout_s):
            self.recorder.event("FEED_CONNECT_TIMEOUT",
                                {"seconds": self.cfg.broker.ws.connect_timeout_s})
            raise RuntimeError(
                f"market feed did not connect within "
                f"{self.cfg.broker.ws.connect_timeout_s}s. Nothing can trade without "
                f"ticks. If this followed an EOD teardown, the Twisted reactor is "
                f"dead and the PROCESS must be restarted (see the "
                f"firsttick-restart.timer unit)."
            )
        self.subscribed_count = plan.count
        self.recorder.event("SUBSCRIBED", {"wave": 1, "count": plan.count,
                                           "modes": {k: len(v) for k, v in
                                                     plan.by_mode().items()}})
        self.log.info(f"WAVE 1 subscribed: {plan.count} instruments")

    def capture_settlement(self) -> None:
        """09:09 — snapshot the pre-open auction result; this is the ranking basis."""
        keys = [spot_quote_key(s) for s in self.nifty50]
        key_to_symbol = {spot_quote_key(s): s for s in self.nifty50}
        raw = kquotes.quote(self.kite, keys, limiter=self.limiter)
        self.settlement = kquotes.snapshot_from_quotes(raw, key_to_symbol)

        gainers, losers = universe_mod.rank(self.settlement)
        self.shortlist = universe_mod.shortlist(
            gainers, losers,
            top_n_gainers=self.cfg.universe.top_n_gainers,
            top_n_losers=self.cfg.universe.top_n_losers,
            candidate_buffer=self.cfg.universe.candidate_buffer,
        )
        self.recorder.event("SNAPSHOT", {"name": "settlement",
                                         "symbols": len(self.settlement),
                                         "tradeable": list(self.shortlist.tradeable)})
        self.log.info(
            f"SETTLEMENT ranked {len(self.settlement)} | "
            f"trade={list(self.shortlist.tradeable)} buffer={list(self.shortlist.buffer)}"
        )

    def arm_wave2(self) -> None:
        """09:09:30 — subscribe option chains for the shortlist, then arm."""
        if self.shortlist is None:
            raise RuntimeError("settlement snapshot missing; cannot arm")

        chains: dict[str, list] = {}
        symbols = list(self.shortlist.all_symbols) + self.cfg.universe.enabled_indices
        for symbol in symbols:
            try:
                chains[symbol] = self._chain_for(symbol)
            except Exception as exc:
                self.log.warning(f"chain build failed for {symbol}: {exc}")

        plan = universe_mod.build_wave2(
            chains, symbols=symbols,
            soft_cap=self.cfg.instruments.subscription_soft_cap,
            already_subscribed=self.subscribed_count,
        )
        for inst in plan.instruments:
            self.instruments[inst.token] = inst
            self.by_symbol[inst.tradingsymbol] = inst
        self.feed.register_symbols(plan.instruments)
        self.recorder.symbol_lookup = self.feed.symbol_lookup()
        self.kfeed.subscribe_now(plan.tokens(), SubscribeMode.FULL)
        self.subscribed_count += plan.count
        self.recorder.event("SUBSCRIBED", {"wave": 2, "count": plan.count})

        # Reference price for an option is its PREVIOUS CLOSE (R14).
        refs: dict[int, float] = {}
        lots: dict[int, int] = {}
        tradeable = set(self.shortlist.tradeable) | set(self.cfg.universe.enabled_indices)
        keys = [i.quote_key for i in plan.instruments if i.underlying in tradeable]
        raw = kquotes.quote(self.kite, keys, limiter=self.limiter)
        for inst in plan.instruments:
            if inst.underlying not in tradeable:
                continue
            row = raw.get(inst.quote_key) or {}
            prev_close = float((row.get("ohlc") or {}).get("close") or 0.0)
            if prev_close > 0:
                refs[inst.token] = prev_close
                lots[inst.token] = self._lots_for(inst.underlying)

        armed = self.feed.arm(list(plan.instruments), refs, lots=lots,
                              default_lots=self.cfg.entry.lots_default)
        self.recorder.event("ARMED", {"count": armed})
        self.log.info(f"WAVE 2 subscribed {plan.count} | ARMED {armed} instruments")

    def start_trading(self) -> None:
        """09:15 — arm entries within the configured window."""
        s = self.cfg.schedule
        now = now_ist()
        start = today_at(s.trading_start, now)
        fire_after = mono_ns() + int(
            max(0.0, (start - now).total_seconds() + self.cfg.entry.fire_after_seconds)
            * 1e9)
        deadline = fire_after + int(self.cfg.entry.deadline_seconds * 1e9)
        self.feed.phase = Phase.TRADING
        self.feed.enable_entries(
            fire_after_ns=fire_after, deadline_ns=deadline,
            session_prefix=f"sig_{now.strftime('%Y%m%d')}_",
        )
        self.log.info("TRADING armed")

    def square_off(self) -> None:
        self.feed.disarm()
        n = self.exit_all(ExitTrigger.EOD_SQUAREOFF)
        self.log.info(f"EOD square-off: {n} position(s)")

    # ================= runtime loops =================

    def run(self) -> None:
        self._running = True
        threading.Thread(target=self._monitor_loop, name="Monitor", daemon=True).start()
        threading.Thread(target=self._sync_loop, name="BrokerSync", daemon=True).start()
        threading.Thread(target=self._push_loop, name="WsPush", daemon=True).start()
        while self._running:
            try:
                self.scheduler.tick()
                self.feed.phase = self.scheduler.phase
            except Exception as exc:
                self.log.error(f"scheduler tick failed: {exc}")
            time.sleep(0.5)

    def stop(self) -> None:
        self._running = False
        if self.executor:
            self.executor.stop()
        if self.kfeed:
            self.kfeed.close()
        self.recorder.stop()

    def _monitor_loop(self) -> None:
        interval = self.cfg.exits.monitor_interval_ms / 1000.0
        while self._running:
            try:
                if self.scheduler.phase in (Phase.TRADING, Phase.MANAGING, Phase.EOD):
                    snapshot = self.feed.snapshot()
                    self.book.update_prices(snapshot, str(self.cfg.exits.pnl_basis))
                    now = now_ist()
                    now_us = epoch_us(now)
                    for pos in self.book.open_positions():
                        exits_mod.refresh_live(pos, self.exit_cfg, now_us=now_us)
                        exits_mod.update_trailing(pos, self.exit_cfg)
                        hit, trigger = exits_mod.evaluate(pos, self.exit_cfg, now)
                        if hit and self.executor:
                            self.executor.request_exit(pos, trigger)
            except Exception as exc:
                self.log.error(f"monitor loop: {exc}")
            time.sleep(interval)

    def _sync_loop(self) -> None:
        if not self.cfg.positions.broker_sync.enabled:
            return
        interval = self.cfg.positions.broker_sync.poll_interval_seconds
        while self._running:
            time.sleep(interval)
            if self.cfg.trading_mode.mode is TradingMode.PAPER or self.kite is None:
                continue
            try:
                self.reconcile_now()
                self.refresh_capital()
            except Exception as exc:
                self.log.error(f"broker sync: {exc}")

    def _push_loop(self) -> None:
        interval = self.cfg.api.ws_push_interval_ms / 1000.0
        while self._running:
            time.sleep(interval)
            try:
                if self.hub.client_count:
                    self.hub.publish("status", self.status_payload())
                    self.hub.publish("market", self.market_payload())
                    self.hub.publish("positions",
                                     {"upsert": self.book.to_dicts(
                                         self.book.open_positions())})
            except Exception:
                pass

    # ================= payloads =================

    @property
    def uptime_s(self) -> float:
        return round(time.monotonic() - self.started_at, 1)

    def status_payload(self) -> dict:
        # Imported here, like the other ticker imports, to keep kiteconnect out of
        # module import time.
        from .brokers.kite.ticker import FeedStats

        return {
            **self.scheduler.status(),
            "mode": str(self.cfg.trading_mode.mode),
            "halted": self._halted,
            "uptime_s": self.uptime_s,
            # A hand-written stub here shipped only {"connected": false}, so every
            # other field was missing until the ticker existed and the console
            # died on Object.entries(feed.modes). An empty FeedStats gives the
            # identical shape, so the payload never changes contract.
            "feed": (self.kfeed.stats if self.kfeed else FeedStats()).as_dict(),
            "engine": self.feed.stats(),
            "recorder": self.recorder.stats(),
            "positions": self.book.summary(),
            "capital": self.capital or self._paper_capital(),
            "rate_limits": self.limiter.stats(),
            "ws_clients": self.hub.client_count,
            "server_time": now_ist().isoformat(),
        }

    def reset_session(self) -> None:
        """Tear down a finished session so the next day starts clean.

        Called by the scheduler when it returns to IDLE. Everything rebuilt by
        phase1/connect_feed/arm_wave2 is dropped here; anything that survives a
        day (config, credentials, position history on disk) is left alone.
        """
        self.log.info("session reset — clearing feed, universe and arming state")
        if self.kfeed is not None:
            try:
                self.kfeed.close()
            except Exception:
                pass
            self.kfeed = None
        try:
            self.recorder.stop()
        except Exception:
            pass
        self.feed.reset()
        self.instruments.clear()
        self.by_symbol.clear()
        self.settlement = {}
        self.shortlist = None
        self.subscribed_count = 0
        self.master = {}
        self._halted = False
        # A new recorder so tomorrow writes into tomorrow's dated directory.
        self.recorder = Recorder(
            Path(self.cfg.system.data_dir),
            enabled=self.cfg.recorder.enabled,
            compression=self.cfg.recorder.compression,
            depth_levels=self.cfg.recorder.record_depth_levels,
            flush_interval_ms=self.cfg.recorder.flush_interval_ms,
            max_disk_mb=self.cfg.recorder.max_disk_mb,
            on_disk_full=str(self.cfg.recorder.on_disk_full),
        )
        self.feed.recorder = self.recorder
        self.scheduler.recorder = self.recorder
        if self.executor is not None:
            self.executor.recorder = self.recorder

    def refresh_capital(self) -> None:
        """Cache the broker capital view. Safe to call from any background thread."""
        if self.kite is None or self.cfg.trading_mode.mode is TradingMode.PAPER:
            self.capital = self._paper_capital()
            return
        try:
            # strict: a failed call must raise, not resolve to a zero-filled view
            # that silently replaces a good one on the dashboard.
            fresh = kportfolio.capital(
                kportfolio.margins(self.kite, limiter=self.limiter, strict=True))
        except Exception as exc:
            self.log.error(f"capital refresh failed, keeping the last known view: {exc}")
            return
        fresh["simulated"] = False
        self.capital = fresh

    def _paper_capital(self) -> dict:
        """Simulated capital so the console shows the same shape in paper mode."""
        start = float(self.cfg.paper.starting_capital)
        used = round(sum(p.entry.price * p.quantity for p in self.book.open_positions()), 2)
        realised = self.book.summary()["realised"]
        total = round(start + realised, 2)
        return {
            "available": round(total - used, 2), "used": used, "total": total,
            "deployed_pct": round((used / total) * 100, 2) if total > 0 else 0.0,
            "opening_balance": start, "payin": 0.0, "net": total,
            "breakdown": {"debits": used, "span": 0.0, "exposure": 0.0,
                          "option_premium": used},
            "simulated": True,
        }

    def universe_payload(self) -> dict:
        return {
            "nifty50": self.nifty50,
            "indices": self.cfg.universe.enabled_indices,
            "tradeable": list(self.shortlist.tradeable) if self.shortlist else [],
            "buffer": list(self.shortlist.buffer) if self.shortlist else [],
            "subscribed": self.subscribed_count,
            "armed": self.feed.armed_view(),
        }

    def ranking_payload(self) -> dict:
        if not self.shortlist:
            return {"ranked": []}
        tradeable = set(self.shortlist.tradeable)
        buffer = set(self.shortlist.buffer)
        return {"ranked": [
            {"rank": r.rank_gainer, "symbol": r.symbol, "ltp": r.ltp,
             "prev_close": r.prev_close, "change_pct": r.change_pct,
             "volume": int((self.settlement.get(r.symbol) or {}).get("volume") or 0),
             "open": float((self.settlement.get(r.symbol) or {}).get("open") or 0.0),
             "high": float((self.settlement.get(r.symbol) or {}).get("high") or 0.0),
             "low": float((self.settlement.get(r.symbol) or {}).get("low") or 0.0),
             "selected": r.symbol in tradeable,
             "buffer": r.symbol in buffer}
            for r in self.shortlist.gainers
        ]}

    def market_payload(self) -> dict:
        out = {}
        for token, view in self.feed.snapshot().items():
            inst = self.instruments.get(token)
            out[str(token)] = {
                "sym": inst.tradingsymbol if inst else None,
                "underlying": inst.underlying if inst else None,
                "ltp": view.ltp, "bid": view.bid, "ask": view.ask,
                "volume": view.volume, "oi": view.oi,
                "feed_lag_us": view.feed_lag_us,
            }
        return out

    def orders_payload(self) -> list[dict]:
        return self.order_records[-500:]

    def signals_payload(self) -> list[dict]:
        return self.signals[-200:]

    def latency_payload(self) -> dict:
        rows = self.executor.latencies if self.executor else []
        if not rows:
            return {"trades": [], "median_tick_to_fill_ms": None}
        vals = sorted(r["total_tick_to_fill_ms"] for r in rows)
        return {"trades": rows, "median_tick_to_fill_ms": vals[len(vals) // 2]}

    def snapshot_for(self, topic: str):
        return {
            "status": self.status_payload,
            "market": self.market_payload,
            "positions": lambda: {"upsert": self.book.to_dicts(
                self.book.open_positions())},
            "orders": self.orders_payload,
            "events": lambda: self.events[-100:],
            "logs": lambda: self.logs[-100:],
        }.get(topic, dict)()

    # ================= actions =================

    def exit_all(self, trigger: ExitTrigger) -> int:
        if not self.executor:
            return 0
        return sum(1 for p in self.book.open_positions()
                   if self.executor.request_exit(p, trigger))

    def kill_switch(self) -> int:
        self._halted = True
        self.feed.disarm()
        return self.exit_all(ExitTrigger.MANUAL_API)

    def reconcile_now(self) -> dict:
        if self.kite is None:
            return {}
        # strict, and abort on failure. A failed fetch must never be read as "the
        # broker has no positions" -- reconcile would close every open position
        # locally while they stay live at the broker, unmanaged.
        try:
            data = kportfolio.positions(self.kite, limiter=self.limiter, strict=True)
        except Exception as exc:
            self.log.error(
                f"reconcile skipped: could not read broker positions ({exc}). "
                f"Local positions left untouched."
            )
            return {"error": str(exc), "skipped": True}
        report = self.book.reconcile(
            kportfolio.day_position_map(data),
            instrument_lookup=self.by_symbol,
        )
        if any(report.values()):
            self.log.info(f"reconcile: {report}")
        return report

    def edit_manual(self, action: str, symbol: str, body: dict) -> dict:
        manual = list(self.cfg.universe.manual_instruments)
        if action == "add":
            manual.append({"symbol": symbol, **{k: v for k, v in body.items()
                                                if k not in ("action", "symbol")}})
        else:
            manual = [m for m in manual if m.get("symbol") != symbol]
        self.store.apply_patch({"universe": {"manual_instruments": manual}})
        self.cfg = self.store.config
        return {"manual_instruments": manual}

    def on_config_changed(self, new_cfg, changed: list[str]) -> None:
        self.cfg = new_cfg
        self.exit_cfg = exits_mod.build_config(new_cfg.exits)
        self.feed.trigger_cfg = build_trigger_cfg(new_cfg.entry)
        self.log.info(f"config updated: {changed}")

    def issue_ticket(self) -> str:
        ticket = secrets.token_urlsafe(24)
        self._tickets[ticket] = time.monotonic() + 60
        return ticket

    def consume_ticket(self, ticket: str) -> bool:
        expiry = self._tickets.pop(ticket, None)
        for key, exp in list(self._tickets.items()):
            if exp < time.monotonic():
                self._tickets.pop(key, None)
        return bool(expiry and expiry >= time.monotonic())

    # ================= callbacks =================

    def _on_signal(self, sig) -> None:
        self.signals.append({
            "sig_id": sig.sig_id, "sym": sig.tradingsymbol, "diff": sig.diff,
            "ref": sig.ref_price, "price": sig.tick_price, "ask": sig.best_ask,
            "at": now_ist().isoformat(),
        })
        self.recorder.event("SIGNAL", {"sig_id": sig.sig_id, "token": sig.token,
                                       "sym": sig.tradingsymbol, "diff": sig.diff})
        self.hub.publish("events", {"kind": "SIGNAL", "sym": sig.tradingsymbol,
                                    "diff": sig.diff}, kind="event")

    def _on_order_event(self, event: dict) -> None:
        self.book.apply_order_event(event)
        self.hub.publish("orders", event, kind="event")

    def _on_order_record(self, record) -> None:
        self.order_records.append({
            "pos_id": record.pos_id, "sym": record.tradingsymbol,
            "role": str(record.role), "side": str(record.side),
            "qty": record.quantity, "price": record.price,
            "attempt": record.attempt, "order_id": record.order_id,
            "status": str(record.status) if record.status else None,
            "rejection": str(record.rejection_kind) if record.rejection_kind else None,
            "message": record.status_message, "at": now_ist().isoformat(),
        })

    def _on_position_change(self, pos, action: str) -> None:
        self.events.append({"kind": "POSITION", "action": action,
                            "pos_id": pos.pos_id, "sym": pos.tradingsymbol,
                            "status": str(pos.status), "at": now_ist().isoformat()})
        self.recorder.event("POSITION", {"pos_id": pos.pos_id, "action": action,
                                         "sym": pos.tradingsymbol,
                                         "status": str(pos.status)})

    def _on_feed_state(self, state: str, payload: dict) -> None:
        self.events.append({"kind": f"FEED_{state}", **payload,
                            "at": now_ist().isoformat()})
        if state in ("RECONNECT", "CLOSED"):
            self.recorder.event("FEED_GAP", payload)
        if state == "NORECONNECT":
            # The socket exhausted its retry budget. Entries cannot fire without a
            # feed, so disarm rather than sit armed against stale prices. Exits keep
            # running on the REST path, and the daily reset builds a fresh socket.
            self.feed.disarm()
            self.log.error(
                "FEED gave up reconnecting — entries disarmed. Exits still managed "
                "via REST; a fresh socket is built at the next session."
            )
        self.log.info(f"FEED {state} {payload}")

    # ================= helpers =================

    def _index_spot_instruments(self) -> list:
        from .core.enums import InstrumentKind
        from .core.models import Instrument
        out = []
        for name in self.cfg.universe.enabled_indices:
            token = self._index_token(name)
            if token:
                out.append(Instrument(
                    token=token, tradingsymbol=name,
                    exchange="BSE" if name in ("SENSEX", "BANKEX") else "NSE",
                    underlying=name, kind=InstrumentKind.INDEX, is_index=True,
                    subscribe_mode=SubscribeMode.QUOTE, wave=1))
        return out

    def _index_token(self, name: str) -> int | None:
        target = spot_quote_key(name).split(":", 1)[1]
        for exch in ("NSE", "BFO", "NFO"):
            for row in self.master.get(exch, ()):
                if str(row.get("tradingsymbol")) == target:
                    return int(row["instrument_token"])
        return None

    def _chain_for(self, symbol: str) -> list:
        exch = option_exchange(symbol)
        master = self.master[exch]
        expiries = kinst.expiries_for(master, symbol)
        expiry = kinst.resolve_expiry(
            symbol, expiries, now_ist().date(),
            roll_enabled=self.cfg.instruments.expiry_roll.enabled,
            buffer_trading_days=self.cfg.instruments.expiry_roll.buffer_trading_days,
        )
        if expiry is None:
            raise ValueError(f"no expiry for {symbol}")
        spot = self._spot_for(symbol)
        return kinst.build_chain(master, symbol, expiry, spot,
                                 self.cfg.instruments.strikes_per_side)

    def _spot_for(self, symbol: str) -> float:
        row = self.settlement.get(symbol)
        if row and row.get("ltp"):
            return float(row["ltp"])
        key = spot_quote_key(symbol)
        raw = kquotes.ltp(self.kite, [key], limiter=self.limiter)
        return float((raw.get(key) or {}).get("last_price") or 0.0)

    def _lots_for(self, underlying: str) -> int:
        override = self.cfg.universe.per_symbol_overrides.get(underlying, {})
        if "lots" in override:
            return int(override["lots"])
        idx = self.cfg.universe.indices.get(underlying)
        if idx is not None:
            return int(idx.lots)
        return int(self.cfg.entry.lots_default)


class _NullLog:
    def info(self, *_a, **_k): pass
    def warning(self, *_a, **_k): pass
    def error(self, *_a, **_k): pass


__all__ = ["Application"]
