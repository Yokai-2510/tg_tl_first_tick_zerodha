"""
The single websocket. Market data AND order updates ride this one connection.

Two rules that this module exists to enforce:
  R3  Exactly one connection per process (Kite allows 3 per API key).
  R4  After a reconnect we MUST re-subscribe AND re-apply modes — Kite does
      not restore them, and silently losing depth would be invisible until
      an order was priced from a stale book.

`on_tick_batch` is invoked on the websocket IO thread and MUST NOT BLOCK.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ...core.enums import SubscribeMode
from ...core.timeutil import epoch_us, mono_ns

#: Kite hard limits.
MAX_INSTRUMENTS = 3000
MAX_CONNECTIONS = 3


@dataclass
class FeedStats:
    connected: bool = False
    subscribed: int = 0
    modes: dict[str, int] = field(default_factory=dict)
    ticks: int = 0
    batches: int = 0
    order_events: int = 0
    reconnects: int = 0
    gaps: int = 0
    last_tick_us: int = 0
    connected_at_us: int = 0
    disconnected_at_us: int = 0
    last_error: str | None = None

    def as_dict(self) -> dict:
        age_ms = (epoch_us() - self.last_tick_us) / 1000.0 if self.last_tick_us else None
        return {
            "connected": self.connected,
            "subscribed": self.subscribed,
            "modes": dict(self.modes),
            "ticks": self.ticks,
            "batches": self.batches,
            "order_events": self.order_events,
            "reconnects": self.reconnects,
            "gaps": self.gaps,
            "last_tick_age_ms": round(age_ms, 1) if age_ms is not None else None,
            "last_error": self.last_error,
        }


class SubscriptionTooLarge(RuntimeError):
    """More instruments than one Kite connection can carry."""


class KiteFeed:
    """Wrapper around KiteTicker.

    The subscription plan is remembered so it can be replayed verbatim after a
    reconnect. Callbacks are plain functions, set once before `connect()`.
    """

    def __init__(
        self,
        api_key: str,
        access_token: str,
        *,
        reconnect_max_tries: int = 50,
        reconnect_max_delay: int = 30,
    ) -> None:
        self.api_key = api_key
        self.access_token = access_token
        self.reconnect_max_tries = reconnect_max_tries
        self.reconnect_max_delay = reconnect_max_delay

        self.stats = FeedStats()
        self.on_tick_batch: Callable[[list[dict], int], None] | None = None
        self.on_order_event: Callable[[dict], None] | None = None
        self.on_state: Callable[[str, dict], None] | None = None

        #: token -> mode. The authoritative plan, replayed on reconnect (R4).
        self._plan: dict[int, str] = {}
        self._lock = threading.RLock()
        self._kws: Any = None
        self._started = False

    # -- planning ----------------------------------------------------------

    def add(self, tokens: list[int], mode: str = SubscribeMode.QUOTE) -> None:
        """Record instruments in the plan (does not talk to the socket)."""
        with self._lock:
            for t in tokens:
                self._plan[int(t)] = str(mode)
            if len(self._plan) > MAX_INSTRUMENTS:
                raise SubscriptionTooLarge(
                    f"{len(self._plan)} instruments exceeds Kite's per-connection "
                    f"limit of {MAX_INSTRUMENTS}"
                )
            self._refresh_counts()

    def _refresh_counts(self) -> None:
        self.stats.subscribed = len(self._plan)
        modes: dict[str, int] = {}
        for m in self._plan.values():
            modes[m] = modes.get(m, 0) + 1
        self.stats.modes = modes

    def plan_snapshot(self) -> dict[int, str]:
        with self._lock:
            return dict(self._plan)

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> None:
        """Open the single connection and subscribe the current plan."""
        from kiteconnect import KiteTicker      # lazy import keeps tests SDK-free

        if self._started:
            raise RuntimeError("KiteFeed.connect() called twice (R3: one connection)")

        kws = KiteTicker(self.api_key, self.access_token)
        kws.on_ticks = self._on_ticks
        kws.on_order_update = self._on_order_update
        kws.on_connect = self._on_connect
        kws.on_close = self._on_close
        kws.on_error = self._on_error
        kws.on_reconnect = self._on_reconnect
        kws.on_noreconnect = self._on_noreconnect

        self._kws = kws
        self._started = True
        kws.connect(threaded=True, disable_ssl_verification=False)

    def wait_connected(self, timeout: float = 15.0) -> bool:
        """Block until the socket is actually up. Returns False on timeout.

        `connect(threaded=True)` returns immediately and NEVER raises when the
        connection cannot be established, so without this a dead feed looks exactly
        like a healthy one. That is not hypothetical: KiteTicker runs on a Twisted
        reactor, a stopped reactor cannot be restarted in-process, and after the
        first EOD teardown every later connect() silently did nothing. Five
        sessions (14-18 Aug) armed 230+ instruments against a socket that was never
        open and took zero ticks, with no error anywhere.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.stats.connected:
                return True
            time.sleep(0.1)
        return False

    def is_alive(self) -> bool:
        """Connected AND actually delivering, as far as we can tell."""
        return bool(self._started and self.stats.connected)

    def subscribe_now(self, tokens: list[int], mode: str) -> None:
        """Add to the plan and push to a live socket (used for wave 2)."""
        self.add(tokens, mode)
        self._apply(tokens, mode)

    def resubscribe_all(self) -> None:
        """Replay the whole plan. Called on connect and after every reconnect (R4)."""
        plan = self.plan_snapshot()
        by_mode: dict[str, list[int]] = {}
        for token, mode in plan.items():
            by_mode.setdefault(mode, []).append(token)
        for mode, tokens in by_mode.items():
            self._apply(tokens, mode)

    def _apply(self, tokens: list[int], mode: str) -> None:
        if not tokens or self._kws is None:
            return
        try:
            self._kws.subscribe(tokens)
            self._kws.set_mode(self._kite_mode(mode), tokens)
        except Exception as exc:
            self.stats.last_error = f"subscribe failed: {exc}"

    def _kite_mode(self, mode: str) -> str:
        m = str(mode).lower()
        if self._kws is None:
            return m
        return {
            "ltp": self._kws.MODE_LTP,
            "quote": self._kws.MODE_QUOTE,
            "full": self._kws.MODE_FULL,
        }.get(m, self._kws.MODE_QUOTE)

    def close(self) -> None:
        self._started = False
        try:
            if self._kws is not None:
                self._kws.close()
        except Exception:
            pass

    # -- callbacks (websocket IO thread) -----------------------------------

    def _on_ticks(self, ws, ticks) -> None:
        recv_ns = mono_ns()                       # R2: first statement
        self.stats.ticks += len(ticks)
        self.stats.batches += 1
        self.stats.last_tick_us = epoch_us()
        cb = self.on_tick_batch
        if cb is not None:
            cb(ticks, recv_ns)

    def _on_order_update(self, ws, data) -> None:
        self.stats.order_events += 1
        cb = self.on_order_event
        if cb is not None:
            cb(data)

    def _on_connect(self, ws, response) -> None:
        self.stats.connected = True
        self.stats.connected_at_us = epoch_us()
        self.resubscribe_all()                    # R4
        self._emit("CONNECTED", {"subscribed": self.stats.subscribed,
                                 "modes": dict(self.stats.modes)})

    def _on_close(self, ws, code, reason) -> None:
        self.stats.connected = False
        self.stats.disconnected_at_us = epoch_us()
        self._emit("CLOSED", {"code": code, "reason": str(reason)})

    def _on_error(self, ws, code, reason) -> None:
        self.stats.last_error = f"{code}: {reason}"
        self._emit("ERROR", {"code": code, "reason": str(reason)})

    def _on_reconnect(self, ws, attempts) -> None:
        self.stats.reconnects += 1
        self.stats.gaps += 1
        gap_ms = None
        if self.stats.disconnected_at_us:
            gap_ms = round((epoch_us() - self.stats.disconnected_at_us) / 1000.0, 1)
        self._emit("RECONNECT", {"attempt": attempts, "gap_ms": gap_ms})

    def _on_noreconnect(self, ws) -> None:
        self.stats.connected = False
        self._emit("NORECONNECT", {"reconnects": self.stats.reconnects})

    def _emit(self, state: str, payload: dict) -> None:
        cb = self.on_state
        if cb is not None:
            try:
                cb(state, payload)
            except Exception:
                pass                              # never break the IO thread


__all__ = ["KiteFeed", "FeedStats", "SubscriptionTooLarge",
           "MAX_INSTRUMENTS", "MAX_CONNECTIONS"]
