"""
Feed — the hot path.

Owns the arming table and the last-tick view. The websocket callback lands
here, and this is the most latency-sensitive code in the system:

    stamp -> enqueue for the recorder -> dict lookup -> float compare
    -> enqueue intent -> return

No HTTP, no disk, no logging, no locks that a slow thread can hold (R1).
Target: under 50 microseconds per batch.
"""

from __future__ import annotations

import queue
import threading
from typing import Callable

from ..core.enums import Phase
from ..core.models import ArmedState, Instrument, Signal, TickView
from ..core.timeutil import epoch_us, mono_ns, to_epoch_us
from .trigger import TriggerConfig, evaluate


class Feed:
    """Tick router and entry arming table.

    Threading:
      * `on_tick_batch` runs on the websocket IO thread (single writer for
        `_armed[*].fired` and `_last`).
      * Readers get immutable `TickView` objects, so a partially-updated view
        is impossible.
    """

    def __init__(
        self,
        *,
        recorder=None,
        trigger_cfg: TriggerConfig | None = None,
        on_signal: Callable[[Signal], None] | None = None,
    ) -> None:
        self.recorder = recorder
        self.trigger_cfg = trigger_cfg or TriggerConfig()
        self.on_signal = on_signal

        self.intent_q: queue.SimpleQueue[Signal] = queue.SimpleQueue()
        self._armed: dict[int, ArmedState] = {}
        self._last: dict[int, TickView] = {}
        self._symbols: dict[int, str] = {}
        self._lock = threading.RLock()          # guards ARMING, not the hot path

        self.phase: str = Phase.BOOT
        self.entries_enabled = False
        self._fire_after_ns = 0
        self._deadline_ns = 0
        self._sig_seq = 0
        self._session_prefix = ""

        self.signals_fired = 0
        self.ticks_seen = 0

    # -- arming (called before 09:15, off the hot path) --------------------

    def arm(
        self,
        instruments: list[Instrument],
        references: dict[int, float],
        *,
        lots: dict[int, int] | None = None,
        default_lots: int = 1,
    ) -> int:
        """Build the arming table. Only instruments with a positive reference
        price are armed — a missing reference cannot produce a valid diff."""
        lots = lots or {}
        with self._lock:
            for inst in instruments:
                ref = float(references.get(inst.token, 0.0))
                if ref <= 0.0:
                    continue
                self._armed[inst.token] = ArmedState(
                    instrument=inst,
                    ref_price=ref,
                    lots=int(lots.get(inst.token, default_lots)),
                    min_diff=self.trigger_cfg.min_diff,
                )
                self._symbols[inst.token] = inst.tradingsymbol
            return len(self._armed)

    def disarm(self) -> None:
        self.entries_enabled = False

    def reset(self) -> None:
        """Clear a finished session so tomorrow starts from nothing.

        Without this the arming table keeps yesterday's `fired` latches and stale
        reference prices, and every instrument would look already-traded.
        """
        with self._lock:
            self.entries_enabled = False
            self._armed.clear()
            self._last.clear()
            self._symbols.clear()
            self._sig_seq = 0
            self.signals_fired = 0
            self.ticks_seen = 0
            self.phase = Phase.IDLE

    def enable_entries(
        self, *, fire_after_ns: int, deadline_ns: int, session_prefix: str
    ) -> None:
        self._fire_after_ns = fire_after_ns
        self._deadline_ns = deadline_ns
        self._session_prefix = session_prefix
        self.entries_enabled = True

    def register_symbols(self, instruments: list[Instrument]) -> None:
        with self._lock:
            for inst in instruments:
                self._symbols.setdefault(inst.token, inst.tradingsymbol)

    # -- reads -------------------------------------------------------------

    def last(self, token: int) -> TickView | None:
        return self._last.get(token)

    def snapshot(self) -> dict[int, TickView]:
        return dict(self._last)

    def armed_view(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "token": st.token,
                    "symbol": st.instrument.tradingsymbol,
                    "underlying": st.instrument.underlying,
                    "ref_price": st.ref_price,
                    "lots": st.lots,
                    "fired": st.fired,
                    "ltp": (self._last.get(st.token).ltp
                            if st.token in self._last else 0.0),
                }
                for st in self._armed.values()
            ]

    def symbol_lookup(self) -> dict[int, str]:
        return dict(self._symbols)

    # -- HOT PATH ----------------------------------------------------------

    def on_tick_batch(self, ticks: list[dict], recv_ns: int) -> None:
        """Websocket callback. Must return in microseconds (R1)."""
        # 1. Persist first so a later failure cannot lose the raw data.
        rec = self.recorder
        if rec is not None:
            rec.put(ticks, recv_ns)

        # 2. Update the last-tick view (single writer: this thread).
        recv_us = epoch_us()
        last = self._last
        self.ticks_seen += len(ticks)
        for tick in ticks:
            token = tick.get("instrument_token")
            if token is None:
                continue
            bid = ask = 0.0
            depth = tick.get("depth")
            if depth:
                buy, sell = depth.get("buy"), depth.get("sell")
                if buy:
                    bid = buy[0].get("price") or 0.0
                if sell:
                    ask = sell[0].get("price") or 0.0
            last[token] = TickView(
                token=token,
                ltp=tick.get("last_price") or 0.0,
                bid=bid, ask=ask,
                volume=tick.get("volume_traded") or 0,
                oi=tick.get("oi") or 0,
                exchange_ts_us=to_epoch_us(tick.get("exchange_timestamp")),
                recv_us=recv_us,
                recv_ns=recv_ns,
            )

        # 3. Entry evaluation — cheapest gates first.
        if not self.entries_enabled or self.phase != Phase.TRADING:
            return
        if recv_ns < self._fire_after_ns or recv_ns > self._deadline_ns:
            return

        armed = self._armed
        if not armed:
            return

        cfg = self.trigger_cfg
        for tick in ticks:
            state = armed.get(tick.get("instrument_token"))
            if state is None or state.fired:
                continue
            self._sig_seq += 1
            signal = evaluate(
                tick, state, cfg,
                sig_id=f"{self._session_prefix}{self._sig_seq:03d}",
                t_tick_ns=recv_ns,
            )
            if signal is not None:
                self.signals_fired += 1
                self.intent_q.put_nowait(signal)
                cb = self.on_signal
                if cb is not None:
                    cb(signal)

    # -- stats -------------------------------------------------------------

    def stats(self) -> dict:
        with self._lock:
            armed = len(self._armed)
            fired = sum(1 for s in self._armed.values() if s.fired)
        return {
            "phase": str(self.phase),
            "entries_enabled": self.entries_enabled,
            "armed": armed,
            "fired": fired,
            "signals": self.signals_fired,
            "ticks_seen": self.ticks_seen,
            "tracked_instruments": len(self._last),
            "intent_queue": self.intent_q.qsize(),
        }


__all__ = ["Feed"]
