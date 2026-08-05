"""
Position book: fills in, positions out, reconciled against the broker.

Two sources, deliberately:
  * order-update websocket  -> SPEED (fills land in milliseconds)
  * positions REST poll     -> TRUTH (detects closes made outside the system)

Neither alone is enough: the order stream can miss events across a reconnect
gap, and the poll is far too slow to drive exits.

Single-writer: all mutation happens on the monitor thread via `apply_*`.
"""

from __future__ import annotations

import threading
from dataclasses import asdict
from typing import Any, Callable

from ..core.enums import (
    ExitTrigger, PositionStatus, Side, TradingMode, is_terminal,
)
from ..core.models import Instrument, Position
from ..core.pricing import pnl_basis_price, pnl_pct
from ..core.timeutil import epoch_us


class PositionBook:
    """In-memory book of today's positions."""

    def __init__(self, *, max_per_symbol: int = 1, max_concurrent: int = 10):
        self.max_per_symbol = max_per_symbol
        self.max_concurrent = max_concurrent
        self._by_id: dict[str, Position] = {}
        self._by_order: dict[str, str] = {}      # order_id -> pos_id
        self._lock = threading.RLock()
        self._seq = 0
        self.on_change: Callable[[Position, str], None] | None = None

    # -- ids ---------------------------------------------------------------

    def next_id(self, prefix: str) -> str:
        with self._lock:
            self._seq += 1
            return f"{prefix}{self._seq:03d}"

    # -- queries -----------------------------------------------------------

    def get(self, pos_id: str) -> Position | None:
        return self._by_id.get(pos_id)

    def by_order_id(self, order_id: str | None) -> Position | None:
        if not order_id:
            return None
        pos_id = self._by_order.get(str(order_id))
        return self._by_id.get(pos_id) if pos_id else None

    def all(self) -> list[Position]:
        with self._lock:
            return list(self._by_id.values())

    def open_positions(self) -> list[Position]:
        with self._lock:
            return [p for p in self._by_id.values() if p.is_open]

    def closed_positions(self) -> list[Position]:
        with self._lock:
            return [p for p in self._by_id.values()
                    if p.status is PositionStatus.CLOSED]

    def count_for_symbol(self, tradingsymbol: str) -> int:
        with self._lock:
            return sum(1 for p in self._by_id.values()
                       if p.tradingsymbol == tradingsymbol and p.is_open)

    def can_open(self, tradingsymbol: str) -> tuple[bool, str]:
        """Concurrency guards. Returns (allowed, reason_if_not)."""
        with self._lock:
            if len(self.open_positions()) >= self.max_concurrent:
                return False, f"max_concurrent ({self.max_concurrent}) reached"
            if self.count_for_symbol(tradingsymbol) >= self.max_per_symbol:
                return False, f"max_per_symbol ({self.max_per_symbol}) reached for {tradingsymbol}"
            return True, ""

    # -- mutation ----------------------------------------------------------

    def add(self, position: Position) -> Position:
        with self._lock:
            self._by_id[position.pos_id] = position
            for oid in (position.entry.order_id, position.exit.order_id):
                if oid:
                    self._by_order[str(oid)] = position.pos_id
            self._notify(position, "ADD")
            return position

    def link_order(self, pos_id: str, order_id: str) -> None:
        with self._lock:
            self._by_order[str(order_id)] = pos_id

    def apply_order_event(self, event: dict) -> Position | None:
        """Handle one order postback from the websocket.

        Interim statuses are recorded but do not change position state (R13).
        """
        order_id = str(event.get("order_id") or "")
        status = str(event.get("status") or "").upper()
        pos = self.by_order_id(order_id)
        if pos is None:
            tag = event.get("tag")
            pos = self._by_id.get(str(tag)) if tag else None
        if pos is None:
            return None

        with self._lock:
            if not is_terminal(status):
                return pos

            side = str(event.get("transaction_type") or "").upper()
            filled = int(event.get("filled_quantity") or 0)
            avg = float(event.get("average_price") or 0.0)

            if status == "COMPLETE" and side == Side.BUY:
                pos.entry.order_id = order_id
                pos.entry.price = avg or pos.entry.price
                pos.entry.filled_qty = filled or pos.quantity
                pos.entry.at_us = pos.entry.at_us or epoch_us()
                pos.quantity = pos.entry.filled_qty or pos.quantity
                pos.status = PositionStatus.ACTIVE
                pos.flags.broker_confirmed = True
                self._notify(pos, "ENTRY_FILLED")

            elif status == "COMPLETE" and side == Side.SELL:
                pos.exit.order_id = order_id
                pos.exit.price = avg or pos.exit.price
                pos.exit.filled_qty = filled or pos.quantity
                pos.exit.at_us = epoch_us()
                pos.status = PositionStatus.CLOSED
                pos.flags.exiting = False
                self._notify(pos, "EXIT_FILLED")

            elif status in ("REJECTED", "CANCELLED"):
                if side == Side.SELL:
                    pos.flags.exiting = False     # allow a retry
                    self._notify(pos, "EXIT_FAILED")
                elif pos.status is PositionStatus.PENDING:
                    pos.status = PositionStatus.FAILED
                    self._notify(pos, "ENTRY_FAILED")
            return pos

    def mark_exiting(self, pos: Position, trigger: ExitTrigger) -> bool:
        """Claim the exit. Returns False if another path already claimed it (R8)."""
        with self._lock:
            if pos.flags.exiting or pos.status is not PositionStatus.ACTIVE:
                return False
            pos.flags.exiting = True
            pos.exit.trigger = trigger
            pos.status = PositionStatus.EXITING
            self._notify(pos, "EXITING")
            return True

    def close_locally(self, pos: Position, trigger: ExitTrigger,
                      *, price: float = 0.0) -> None:
        """Close without an exit order of ours (manual close, or paper fill)."""
        with self._lock:
            pos.exit.trigger = trigger
            pos.exit.price = price or pos.live.ltp
            pos.exit.at_us = epoch_us()
            pos.exit.filled_qty = pos.quantity
            pos.status = PositionStatus.CLOSED
            pos.flags.exiting = False
            self._notify(pos, "CLOSED")

    def update_prices(self, snapshot: dict[int, Any], pnl_basis: str = "ltp") -> None:
        """Refresh live prices from the feed's tick view."""
        with self._lock:
            for pos in self._by_id.values():
                if not pos.is_open:
                    continue
                view = snapshot.get(pos.token)
                if view is None:
                    continue
                pos.live.ltp = view.ltp
                pos.live.bid = view.bid
                pos.live.ask = view.ask
                basis = pnl_basis_price(ltp=view.ltp, bid=view.bid, basis=pnl_basis)
                pos.live.pnl_pct = pnl_pct(pos.entry.price, basis)
                if pos.entry.price > 0 and basis > 0:
                    pos.live.pnl = round((basis - pos.entry.price) * pos.quantity, 2)

    # -- reconciliation ----------------------------------------------------

    def reconcile(
        self, broker_positions: dict[str, dict], *, adopt_unknown: bool = True,
        instrument_lookup: dict[str, Instrument] | None = None,
    ) -> dict[str, list[str]]:
        """Three-way match against the broker (BUILD_SPEC §14.3).

        Returns a report of what changed, for the audit trail.
        """
        report: dict[str, list[str]] = {
            "confirmed": [], "closed_externally": [], "qty_drift": [], "adopted": [],
        }
        with self._lock:
            for pos in list(self._by_id.values()):
                if not pos.is_open:
                    continue
                row = broker_positions.get(pos.tradingsymbol)
                broker_qty = abs(int((row or {}).get("quantity") or 0))
                if row is None or broker_qty == 0:
                    if pos.exit.order_id is None:
                        self.close_locally(pos, ExitTrigger.MANUAL_BROKER)
                        report["closed_externally"].append(pos.pos_id)
                    continue
                if broker_qty != pos.quantity:
                    report["qty_drift"].append(
                        f"{pos.tradingsymbol}: broker={broker_qty} local={pos.quantity}"
                    )
                pos.flags.reconciled = True
                report["confirmed"].append(pos.pos_id)

            if adopt_unknown:
                known = {p.tradingsymbol for p in self._by_id.values() if p.is_open}
                for symbol, row in broker_positions.items():
                    qty = abs(int(row.get("quantity") or 0))
                    if qty == 0 or symbol in known:
                        continue
                    inst = (instrument_lookup or {}).get(symbol)
                    if inst is None:
                        continue
                    adopted = Position(
                        pos_id=self.next_id("adopted_"),
                        instrument=inst,
                        lots=max(1, qty // max(1, inst.lot_size)),
                        quantity=qty,
                        mode=TradingMode.LIVE,
                        status=PositionStatus.ADOPTED_UNMANAGED,
                    )
                    adopted.entry.price = float(row.get("average_price") or 0.0)
                    adopted.entry.at_us = epoch_us()
                    adopted.flags.reconciled = True
                    self.add(adopted)
                    report["adopted"].append(symbol)
        return report

    # -- serialisation -----------------------------------------------------

    def to_dicts(self, positions: list[Position] | None = None) -> list[dict]:
        rows = positions if positions is not None else self.all()
        out = []
        for p in rows:
            d = asdict(p)
            d["instrument"] = {
                "token": p.instrument.token,
                "tradingsymbol": p.instrument.tradingsymbol,
                "underlying": p.instrument.underlying,
                "option_type": p.instrument.instrument_type,
                "strike": p.instrument.strike,
                "expiry": str(p.instrument.expiry) if p.instrument.expiry else None,
                "lot_size": p.instrument.lot_size,
                "exchange": p.instrument.exchange,
            }
            d["status"] = str(p.status)
            d["mode"] = str(p.mode)
            if p.exit.trigger:
                d["exit"]["trigger"] = str(p.exit.trigger)
            out.append(d)
        return out

    def summary(self) -> dict:
        with self._lock:
            open_p = [p for p in self._by_id.values() if p.is_open]
            closed = [p for p in self._by_id.values()
                      if p.status is PositionStatus.CLOSED]
            realised = sum(
                (p.exit.price - p.entry.price) * p.quantity
                for p in closed if p.exit.price and p.entry.price
            )
            return {
                "open": len(open_p),
                "closed": len(closed),
                "failed": sum(1 for p in self._by_id.values()
                              if p.status is PositionStatus.FAILED),
                "adopted": sum(1 for p in self._by_id.values()
                               if p.status is PositionStatus.ADOPTED_UNMANAGED),
                "unrealised": round(sum(p.live.pnl for p in open_p), 2),
                "realised": round(realised, 2),
                "charges": round(sum(p.charges for p in self._by_id.values()), 2),
            }

    def _notify(self, pos: Position, action: str) -> None:
        cb = self.on_change
        if cb is not None:
            try:
                cb(pos, action)
            except Exception:
                pass


__all__ = ["PositionBook"]
