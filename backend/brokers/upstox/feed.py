"""
Upstox market-data feed (MarketDataStreamerV3) with tick normalisation.

The SDK decodes protobuf into a nested dict; the engine speaks a flat
Kite-shaped tick. `normalise_feed` is the whole translation layer and is a pure
function, so it is unit-tested against captured payloads without a connection.

Upstox message:
    {"feeds": {"<instrument_key>": {"fullFeed": {"marketFF": {
        "ltpc":        {"ltp", "ltt", "ltq", "cp"},
        "marketLevel": {"bidAskQuote": [{"bidP","bidQ","askP","askQ"}, ...]},
        "marketOHLC":  {"ohlc": [{"open","high","low","close","vol","ts"}, ...]},
        "atp", "vtt", "oi", "iv", "tbq", "tsq"
    }}}}}

Index instruments arrive under `indexFF` instead of `marketFF` and carry no
depth — handled explicitly rather than silently producing an empty book.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from ...core.enums import SubscribeMode
from ...core.models import Instrument
from ...core.timeutil import epoch_us, mono_ns
from ..base import BrokerError, surrogate_token

try:                                          # heavy optional dependency
    import upstox_client
except ImportError:                           # pragma: no cover
    upstox_client = None

#: Upstox subscription modes.
MODE_MAP = {
    SubscribeMode.LTP: "ltpc",
    SubscribeMode.QUOTE: "quote",
    SubscribeMode.FULL: "full",
}

#: Upstox caps instruments per connection (mode-dependent; full is the tightest).
MAX_INSTRUMENTS_FULL = 2000


def _depth_from(market_level: dict) -> dict:
    """`bidAskQuote` -> the canonical `depth` block."""
    quotes = (market_level or {}).get("bidAskQuote") or []
    buy, sell = [], []
    for level in quotes:
        bid_p = float(level.get("bidP") or 0.0)
        ask_p = float(level.get("askP") or 0.0)
        if bid_p > 0:
            buy.append({"price": bid_p, "quantity": int(level.get("bidQ") or 0),
                        "orders": 0})
        if ask_p > 0:
            sell.append({"price": ask_p, "quantity": int(level.get("askQ") or 0),
                         "orders": 0})
    return {"buy": buy, "sell": sell}


def normalise_tick(instrument_key: str, payload: dict,
                   token_lookup: dict[str, int] | None = None) -> dict | None:
    """Convert one Upstox feed entry into a canonical tick dict.

    Returns None when the payload carries no usable price, so callers never
    have to guard against half-formed entries.
    """
    full = (payload or {}).get("fullFeed") or {}
    body = full.get("marketFF") or full.get("indexFF")
    if not body:
        return None

    ltpc = body.get("ltpc") or {}
    ltp = float(ltpc.get("ltp") or 0.0)
    if ltp <= 0:
        return None

    token = (token_lookup or {}).get(instrument_key) or surrogate_token(instrument_key)

    ohlc_list = (body.get("marketOHLC") or {}).get("ohlc") or []
    daily = ohlc_list[0] if ohlc_list else {}

    tick: dict[str, Any] = {
        "instrument_token": token,
        "last_price": ltp,
        # ltt is epoch milliseconds; to_epoch_us() detects the unit.
        "last_trade_time": int(ltpc.get("ltt") or 0) or None,
        "exchange_timestamp": int(ltpc.get("ltt") or 0) or None,
        "last_traded_quantity": int(float(ltpc.get("ltq") or 0)),
        "average_traded_price": float(body.get("atp") or 0.0),
        "volume_traded": int(float(body.get("vtt") or 0)),
        "total_buy_quantity": int(body.get("tbq") or 0),
        "total_sell_quantity": int(body.get("tsq") or 0),
        "oi": int(body.get("oi") or 0),
        "ohlc": {
            "open": float(daily.get("open") or 0.0),
            "high": float(daily.get("high") or 0.0),
            "low": float(daily.get("low") or 0.0),
            # `cp` is the previous close — the option reference price (R14).
            "close": float(ltpc.get("cp") or daily.get("close") or 0.0),
        },
    }

    depth = _depth_from(body.get("marketLevel") or {})
    if depth["buy"] or depth["sell"]:
        tick["depth"] = depth
    return tick


def normalise_feed(message: dict,
                   token_lookup: dict[str, int] | None = None) -> list[dict]:
    """Convert a whole Upstox websocket message into canonical ticks."""
    feeds = (message or {}).get("feeds") or {}
    out = []
    for key, payload in feeds.items():
        tick = normalise_tick(key, payload, token_lookup)
        if tick is not None:
            out.append(tick)
    return out


class UpstoxFeed:
    """Market-data websocket wrapper mirroring `KiteFeed`'s surface."""

    def __init__(self, access_token: str):
        self.access_token = access_token
        self.on_tick_batch: Callable[[list[dict], int], None] | None = None
        self.on_state: Callable[[str, dict], None] | None = None

        self._streamer: Any = None
        self._plan: dict[str, str] = {}          # instrument_key -> upstox mode
        self._tokens: dict[str, int] = {}        # instrument_key -> engine token
        self._lock = threading.RLock()
        self._started = False

        self.ticks = 0
        self.batches = 0
        self.reconnects = 0
        self.gaps = 0
        self.connected = False
        self.last_tick_us = 0
        self.last_error: str | None = None

    # -- planning ----------------------------------------------------------

    def add(self, instruments: list[Instrument], mode: str = SubscribeMode.FULL) -> None:
        upstox_mode = MODE_MAP.get(mode, "full")
        with self._lock:
            for inst in instruments:
                key = inst.data_key
                if not key:
                    continue
                self._plan[key] = upstox_mode
                self._tokens[key] = inst.token
            if len(self._plan) > MAX_INSTRUMENTS_FULL:
                raise BrokerError(
                    f"{len(self._plan)} instruments exceeds the Upstox "
                    f"per-connection limit of {MAX_INSTRUMENTS_FULL}"
                )

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> None:
        if upstox_client is None:
            raise BrokerError(
                "upstox-python-sdk is not installed; Upstox cannot be used as "
                "the data broker (pip install upstox-python-sdk)"
            )
        if self._started:
            raise BrokerError("UpstoxFeed.connect() called twice")

        cfg = upstox_client.Configuration()
        cfg.access_token = self.access_token
        client = upstox_client.ApiClient(cfg)

        streamer = upstox_client.MarketDataStreamerV3(client, [], "full")
        streamer.on("open", self._on_open)
        streamer.on("message", self._on_message)
        streamer.on("error", self._on_error)
        streamer.on("close", self._on_close)
        streamer.on("reconnecting", self._on_reconnect)
        streamer.auto_reconnect(True, 10, 5)

        self._streamer = streamer
        self._started = True
        streamer.connect()

    def subscribe_now(self, instruments: list[Instrument],
                      mode: str = SubscribeMode.FULL) -> None:
        self.add(instruments, mode)
        keys = [i.data_key for i in instruments if i.data_key]
        if keys and self._streamer is not None:
            try:
                self._streamer.subscribe(keys, MODE_MAP.get(mode, "full"))
            except Exception as exc:
                self.last_error = f"subscribe failed: {exc}"

    def resubscribe_all(self) -> None:
        """Replay the plan. Upstox auto-reconnect does NOT restore subscriptions."""
        if self._streamer is None:
            return
        by_mode: dict[str, list[str]] = {}
        with self._lock:
            for key, mode in self._plan.items():
                by_mode.setdefault(mode, []).append(key)
        for mode, keys in by_mode.items():
            try:
                self._streamer.subscribe(keys, mode)
            except Exception as exc:
                self.last_error = f"resubscribe failed: {exc}"

    def close(self) -> None:
        self._started = False
        try:
            if self._streamer is not None:
                self._streamer.disconnect()
        except Exception:
            pass

    def stats(self) -> dict:
        age_ms = (epoch_us() - self.last_tick_us) / 1000.0 if self.last_tick_us else None
        return {
            "broker": "upstox",
            "connected": self.connected,
            "subscribed": len(self._plan),
            "ticks": self.ticks,
            "batches": self.batches,
            "reconnects": self.reconnects,
            "gaps": self.gaps,
            "last_tick_age_ms": round(age_ms, 1) if age_ms is not None else None,
            "last_error": self.last_error,
        }

    # -- callbacks (websocket thread — must not block) ---------------------

    def _on_message(self, message) -> None:
        recv_ns = mono_ns()                      # R2: first statement
        ticks = normalise_feed(message, self._tokens)
        if not ticks:
            return
        self.ticks += len(ticks)
        self.batches += 1
        self.last_tick_us = epoch_us()
        cb = self.on_tick_batch
        if cb is not None:
            cb(ticks, recv_ns)

    def _on_open(self) -> None:
        self.connected = True
        self.resubscribe_all()
        self._emit("CONNECTED", {"subscribed": len(self._plan)})

    def _on_close(self, code=None, reason=None) -> None:
        self.connected = False
        self._emit("CLOSED", {"code": code, "reason": str(reason)})

    def _on_error(self, error) -> None:
        self.last_error = str(error)
        self._emit("ERROR", {"reason": str(error)})

    def _on_reconnect(self, *_a) -> None:
        self.reconnects += 1
        self.gaps += 1
        self._emit("RECONNECT", {"attempt": self.reconnects})

    def _emit(self, state: str, payload: dict) -> None:
        cb = self.on_state
        if cb is not None:
            try:
                cb(state, payload)
            except Exception:
                pass


__all__ = ["UpstoxFeed", "normalise_feed", "normalise_tick",
           "MODE_MAP", "MAX_INSTRUMENTS_FULL"]
