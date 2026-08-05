"""
Executor — turns signals into filled positions.

Runs on worker threads, never on the websocket thread, so an HTTP call can
never stall the feed. Implements the retry ladder from BUILD_SPEC §8:

    1. marketable limit priced from the signal's live ask
    2. IOC no-fill / partial  -> re-price from the IN-MEMORY feed, re-place
    3. LPP rejection          -> re-price inside the band, re-place
    4. ORDER_TYPE rejection   -> apply the configured fallback
    5. MARGIN / RMS / AUTH    -> never retried

Paper mode is guarded at exactly one place: `_send`.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any, Callable

from ..core.enums import (
    ExitTrigger, OrderType, PositionStatus, RejectionKind, Side, TradingMode,
)
from ..core.models import OrderRecord, Position, Signal
from ..core.pricing import entry_limit_price, exit_limit_price
from ..core.timeutil import epoch_us, mono_ns
from ..brokers.kite import orders as korders


class Executor:
    """Worker pool that owns all order placement."""

    def __init__(
        self,
        *,
        kite: Any,
        cfg,
        book,
        feed,
        limiter=None,
        recorder=None,
        workers: int = 4,
        audit: Callable[[OrderRecord], None] | None = None,
        log=None,
    ) -> None:
        self.kite = kite
        self.cfg = cfg
        self.book = book
        self.feed = feed
        self.limiter = limiter
        self.recorder = recorder
        self.audit = audit
        self.log = log
        self.workers = max(1, workers)

        self._threads: list[threading.Thread] = []
        self._running = False
        self._exit_q: queue.SimpleQueue = queue.SimpleQueue()
        self.latencies: list[dict] = []
        self._lat_lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        for i in range(self.workers):
            t = threading.Thread(target=self._entry_loop, name=f"Executor-{i}",
                                 daemon=True)
            t.start()
            self._threads.append(t)
        t = threading.Thread(target=self._exit_loop, name="Executor-Exit", daemon=True)
        t.start()
        self._threads.append(t)

    def stop(self) -> None:
        self._running = False

    def request_exit(self, pos: Position, trigger: ExitTrigger) -> bool:
        """Queue an exit. Idempotent — a position can only be claimed once."""
        if not self.book.mark_exiting(pos, trigger):
            return False
        self._exit_q.put((pos, trigger))
        return True

    # -- worker loops ------------------------------------------------------

    def _entry_loop(self) -> None:
        while self._running:
            try:
                signal = self.feed.intent_q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self.execute_entry(signal)
            except Exception as exc:
                self._warn(f"entry failed for {signal.tradingsymbol}: {exc}")

    def _exit_loop(self) -> None:
        while self._running:
            try:
                pos, trigger = self._exit_q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self.execute_exit(pos, trigger)
            except Exception as exc:
                pos.flags.exiting = False           # allow a later retry
                self._warn(f"exit failed for {pos.tradingsymbol}: {exc}")

    # -- entry -------------------------------------------------------------

    def execute_entry(self, sig: Signal) -> Position | None:
        entry = self.cfg.entry
        allowed, reason = self.book.can_open(sig.tradingsymbol)
        if not allowed:
            self._info(f"entry skipped {sig.tradingsymbol}: {reason}")
            return None

        notional = sig.tick_price * sig.quantity
        if entry.max_notional_per_trade and notional > entry.max_notional_per_trade:
            self._info(
                f"entry skipped {sig.tradingsymbol}: notional {notional:.0f} "
                f"> max_notional_per_trade {entry.max_notional_per_trade:.0f}"
            )
            return None

        order_type = korders.resolve_order_type(
            is_index_symbol=sig.is_index,
            configured=(entry.order_type.index_options if sig.is_index
                        else entry.order_type.stock_options),
        )
        product = korders.resolve_product(
            is_index_symbol=sig.is_index,
            stock_product=str(self.cfg.broker.product.stock_options),
            index_product=str(self.cfg.broker.product.index_options),
        )

        pos_id = self.book.next_id(_prefix("pos"))
        price = entry_limit_price(
            best_ask=sig.best_ask, last_price=sig.tick_price, tick=sig.tick_size,
            price_source=str(entry.entry_price_source),
            slippage_pct=entry.entry_slippage_pct,
        )

        position = _new_position(sig, pos_id, self.cfg.trading_mode.mode)
        position.entry.ref_price = sig.ref_price
        position.entry.diff = sig.diff
        self.book.add(position)

        residual = sig.quantity
        attempts = entry.entry_retry.max_attempts if entry.entry_retry.enabled else 1
        lpp_left = entry.lpp.retries
        t_first_req = 0

        for attempt in range(1, attempts + 1):
            result, record = self._send(
                role="ENTRY", side=Side.BUY, sig=sig, pos_id=pos_id,
                quantity=residual, price=price, order_type=order_type,
                product=product, validity=str(entry.entry_validity),
                attempt=attempt,
            )
            t_first_req = t_first_req or record.t_req_ns

            # Paper mode fills synthetically at the touch, capped at our limit.
            if self.cfg.trading_mode.mode is TradingMode.PAPER:
                fill = sig.best_ask if sig.best_ask > 0 else sig.tick_price
                position.entry.price = min(fill, price)
                position.entry.filled_qty = residual
                position.entry.at_us = epoch_us()
                position.status = PositionStatus.ACTIVE
                self._record_latency(sig, t_first_req, record.t_ack_ns)
                self._info(
                    f"[PAPER] ENTRY {position.tradingsymbol} @ "
                    f"{position.entry.price} (limit {price}, diff {sig.diff:+.2f})"
                )
                return position

            if result.success and result.order_id:
                self.book.link_order(pos_id, result.order_id)
                position.entry.order_id = result.order_id
                final = self._await_terminal(result.order_id, timeout_s=5.0)

                if final and final.status == "COMPLETE":
                    self._fill(position, final, sig, t_first_req, record)
                    return position

                filled = final.filled_quantity if final else 0
                if filled:
                    residual -= filled
                    position.entry.filled_qty += filled
                if residual <= 0:
                    self._fill(position, final, sig, t_first_req, record)
                    return position

                if final and final.rejection_kind is RejectionKind.LPP and lpp_left > 0:
                    lpp_left -= 1
                    price = self._lpp_price(sig, final.lpp_limit)
                    continue
                price = self._reprice(sig, entry)          # IOC no-fill -> re-price
                time.sleep(entry.entry_retry.interval_ms / 1000.0)
                continue

            kind = result.rejection_kind
            if kind is RejectionKind.LPP and lpp_left > 0:
                lpp_left -= 1
                price = self._lpp_price(sig, result.lpp_limit)
                continue
            if kind is RejectionKind.ORDER_TYPE and entry.order_fallback.enabled:
                order_type = OrderType.LIMIT
                price = self._reprice(sig, entry)
                continue
            if kind in (RejectionKind.MARGIN, RejectionKind.RMS, RejectionKind.AUTH):
                self._warn(f"entry rejected ({kind}) {sig.tradingsymbol}: {result.error}")
                position.status = PositionStatus.FAILED
                return None
            if kind in (RejectionKind.NETWORK, RejectionKind.RATE_LIMIT):
                time.sleep(min(0.5 * attempt, 2.0))
                continue
            position.status = PositionStatus.FAILED
            return None

        position.status = PositionStatus.FAILED
        self._warn(f"entry exhausted retries for {sig.tradingsymbol}")
        return None

    # -- exit --------------------------------------------------------------

    def execute_exit(self, pos: Position, trigger: ExitTrigger) -> bool:
        exits = self.cfg.exits
        eod = trigger is ExitTrigger.EOD_SQUAREOFF
        view = self.feed.last(pos.token)
        bid = view.bid if view else pos.live.bid
        ltp = view.ltp if view else pos.live.ltp

        try:
            price = exit_limit_price(
                best_bid=bid, last_price=ltp, tick=pos.instrument.tick_size,
                price_source=str(exits.exit_price_source),
                slippage_pct=(exits.eod_slippage_pct if eod else exits.exit_slippage_pct),
            )
        except ValueError:
            pos.flags.exiting = False
            self._warn(f"exit skipped {pos.tradingsymbol}: no usable price")
            return False

        if self.cfg.trading_mode.mode is TradingMode.PAPER:
            self.book.close_locally(pos, trigger, price=price)
            return True

        attempts = max(1, exits.__dict__.get("exit_retry_attempts", 3))
        for attempt in range(1, attempts + 1):
            result, _ = self._send(
                role="EXIT", side=Side.SELL, sig=None, pos_id=pos.pos_id,
                quantity=pos.quantity, price=price,
                order_type=OrderType.LIMIT, product=_product_of(pos, self.cfg),
                validity="DAY", attempt=attempt, position=pos,
            )
            if result.success and result.order_id:
                self.book.link_order(pos.pos_id, result.order_id)
                pos.exit.order_id = result.order_id
                return True
            if result.rejection_kind is RejectionKind.LPP:
                price = korders.lpp_reprice(
                    lpp_limit=result.lpp_limit, live_ltp=ltp,
                    tick=pos.instrument.tick_size,
                    safety_factor=self.cfg.entry.lpp.safety_factor,
                )
                continue
            time.sleep(0.2 * attempt)

        pos.flags.exiting = False
        self._warn(f"exit failed after {attempts} attempts: {pos.tradingsymbol}")
        return False

    # -- helpers -----------------------------------------------------------

    def _send(
        self, *, role, side, sig, pos_id, quantity, price, order_type, product,
        validity, attempt, position: Position | None = None,
    ):
        symbol = sig.tradingsymbol if sig else position.tradingsymbol
        exchange = sig.exchange if sig else position.instrument.exchange

        record = OrderRecord(
            client_tag=pos_id, role=role, side=side,
            token=sig.token if sig else position.token,
            tradingsymbol=symbol, exchange=exchange, order_type=str(order_type),
            product=str(product), validity=str(validity), quantity=quantity,
            price=price, attempt=attempt, pos_id=pos_id,
            sig_id=sig.sig_id if sig else None,
            price_basis={"source": "ask" if role == "ENTRY" else "bid",
                         "raw": sig.best_ask if sig else (position.live.bid if position else 0.0)},
            t_req_ns=mono_ns(),
        )

        if self.cfg.trading_mode.mode is TradingMode.PAPER:
            result = _paper_result(price)
        else:
            if self.limiter is not None and not self.limiter.acquire("order", timeout=1.0):
                result = _fail("local rate limit exceeded", RejectionKind.RATE_LIMIT)
            else:
                result = korders.place(
                    self.kite, tradingsymbol=symbol, exchange=exchange, side=side,
                    quantity=quantity, price=price, order_type=order_type,
                    product=product, validity=validity, tag=pos_id,
                )

        record.t_ack_ns = mono_ns()
        record.order_id = result.order_id
        record.status = result.status
        record.rejection_kind = result.rejection_kind
        record.status_message = result.error
        self._audit(record)
        return result, record

    def _await_terminal(self, order_id: str, *, timeout_s: float):
        """Poll until terminal. Interim statuses are not terminal (R13)."""
        if self.cfg.trading_mode.mode is TradingMode.PAPER:
            return None
        deadline = time.monotonic() + timeout_s
        last = None
        while time.monotonic() < deadline:
            pos = self.book.by_order_id(order_id)
            if pos is not None and pos.status is PositionStatus.ACTIVE:
                return None                      # websocket beat us to it
            last = korders.read_history(self.kite, order_id)
            if korders.is_final(last):
                return last
            time.sleep(0.05)
        return last

    def _reprice(self, sig: Signal, entry) -> float:
        view = self.feed.last(sig.token)
        ask = view.ask if view and view.ask > 0 else sig.best_ask
        ltp = view.ltp if view and view.ltp > 0 else sig.tick_price
        return entry_limit_price(
            best_ask=ask, last_price=ltp, tick=sig.tick_size,
            price_source=str(entry.entry_price_source),
            slippage_pct=entry.entry_slippage_pct,
        )

    def _lpp_price(self, sig: Signal, lpp_limit: float | None) -> float:
        view = self.feed.last(sig.token)
        return korders.lpp_reprice(
            lpp_limit=lpp_limit,
            live_ltp=view.ltp if view else sig.tick_price,
            tick=sig.tick_size,
            safety_factor=self.cfg.entry.lpp.safety_factor,
        )

    def _fill(self, position: Position, final, sig: Signal, t_req_ns: int, record) -> None:
        price = final.average_price if final and final.average_price else record.price
        position.entry.price = price
        position.entry.filled_qty = position.quantity
        position.entry.at_us = epoch_us()
        position.status = PositionStatus.ACTIVE
        position.flags.broker_confirmed = final is not None
        self._record_latency(sig, t_req_ns, record.t_ack_ns)
        self._info(
            f"ENTRY FILLED {position.tradingsymbol} @ {price} "
            f"(limit {record.price}, diff {sig.diff:+.2f})"
        )

    def _record_latency(self, sig: Signal, t_req_ns: int, t_ack_ns: int) -> None:
        fill_ns = mono_ns()
        row = {
            "sig_id": sig.sig_id, "sym": sig.tradingsymbol,
            "tick_to_signal_us": round((sig.t_signal_ns - sig.t_tick_ns) / 1e3, 1),
            "signal_to_req_ms": round((t_req_ns - sig.t_signal_ns) / 1e6, 2),
            "req_to_ack_ms": round((t_ack_ns - t_req_ns) / 1e6, 2),
            "ack_to_fill_ms": round((fill_ns - t_ack_ns) / 1e6, 2),
            "total_tick_to_fill_ms": round((fill_ns - sig.t_tick_ns) / 1e6, 2),
        }
        with self._lat_lock:
            self.latencies.append(row)
        if self.recorder is not None:
            self.recorder.event("LATENCY", row)
        self._info(
            f"LATENCY {sig.tradingsymbol} | tick->signal {row['tick_to_signal_us']}us "
            f"| signal->req {row['signal_to_req_ms']}ms | req->ack {row['req_to_ack_ms']}ms "
            f"| total {row['total_tick_to_fill_ms']}ms"
        )

    def _audit(self, record: OrderRecord) -> None:
        if self.recorder is not None:
            self.recorder.event("ORDER", {
                "pos_id": record.pos_id, "sym": record.tradingsymbol,
                "role": str(record.role), "side": str(record.side),
                "qty": record.quantity, "price": record.price,
                "attempt": record.attempt, "order_id": record.order_id,
                "status": str(record.status) if record.status else None,
                "rejection": str(record.rejection_kind) if record.rejection_kind else None,
                "message": record.status_message,
            })
        if self.audit is not None:
            try:
                self.audit(record)
            except Exception:
                pass

    def _info(self, msg: str) -> None:
        if self.log:
            self.log.info(msg)

    def _warn(self, msg: str) -> None:
        if self.log:
            self.log.warning(msg)


# -- module helpers --------------------------------------------------------

def _prefix(kind: str) -> str:
    from ..core.timeutil import now_ist
    return f"{kind}_{now_ist().strftime('%Y%m%d')}_"


def _new_position(sig: Signal, pos_id: str, mode) -> Position:
    from ..core.models import Instrument
    from ..core.enums import InstrumentKind
    inst = Instrument(
        token=sig.token, tradingsymbol=sig.tradingsymbol, exchange=sig.exchange,
        underlying=sig.underlying, kind=InstrumentKind.OPTION,
        lot_size=max(1, sig.quantity // max(1, sig.lots)),
        tick_size=sig.tick_size, instrument_type=sig.option_type,
        strike=sig.strike, is_index=sig.is_index,
    )
    return Position(pos_id=pos_id, instrument=inst, lots=sig.lots,
                    quantity=sig.quantity, mode=mode, sig_id=sig.sig_id,
                    status=PositionStatus.PENDING)


def _product_of(pos: Position, cfg) -> str:
    return str(cfg.broker.product.index_options if pos.instrument.is_index
               else cfg.broker.product.stock_options)


def _paper_result(price: float):
    from ..core.models import OrderResult
    return OrderResult(success=True, order_id=None, status="COMPLETE",
                       average_price=price, t_req_ns=mono_ns(), t_ack_ns=mono_ns())


def _fail(message: str, kind: RejectionKind):
    from ..core.models import OrderResult
    return OrderResult(success=False, error=message, rejection_kind=kind,
                       t_req_ns=mono_ns(), t_ack_ns=mono_ns())


__all__ = ["Executor"]
