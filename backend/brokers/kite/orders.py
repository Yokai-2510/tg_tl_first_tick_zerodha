"""
Order placement, modification, and rejection handling.

State-free: the authenticated client is passed in. `kiteconnect` is never
imported here, so every pure function below is unit-testable without the SDK
or a network connection.

Expected broker conditions are RETURNED as OrderResult, not raised. Only
genuinely unexpected failures propagate.

Spec: BUILD_SPEC §8 (state machine) and §8.1 (LPP re-pricing).
"""

from __future__ import annotations

import re
from typing import Any

from ...core.enums import (
    OrderStatus, OrderType, Product, RejectionKind, Side, Validity, is_terminal,
)
from ...core.models import OrderResult
from ...core.pricing import FLOOR, round_price
from ...core.timeutil import mono_ns

#: Zerodha LPP rejection, e.g.
#: "This order is outside the allowed LPP limit (646.85)."
_LPP_RE = re.compile(r"allowed\s+LPP\s+limit\s*\(\s*([\d.]+)\s*\)", re.IGNORECASE)

#: Substring -> RejectionKind. Order matters: first match wins.
_REJECTION_PATTERNS: tuple[tuple[str, RejectionKind], ...] = (
    ("lpp limit", RejectionKind.LPP),
    ("insufficient", RejectionKind.MARGIN),
    ("margin", RejectionKind.MARGIN),
    ("funds", RejectionKind.MARGIN),
    ("market order", RejectionKind.ORDER_TYPE),
    ("order type", RejectionKind.ORDER_TYPE),
    ("not allowed", RejectionKind.RMS),
    ("blocked", RejectionKind.RMS),
    ("rms", RejectionKind.RMS),
    ("freeze", RejectionKind.RMS),
    ("too many requests", RejectionKind.RATE_LIMIT),
    ("rate limit", RejectionKind.RATE_LIMIT),
    ("timeout", RejectionKind.NETWORK),
    ("timed out", RejectionKind.NETWORK),
    ("connection", RejectionKind.NETWORK),
    ("gateway", RejectionKind.NETWORK),
    ("token", RejectionKind.AUTH),
    ("unauthor", RejectionKind.AUTH),
    ("forbidden", RejectionKind.AUTH),
)


# --------------------------------------------------------------------------
# Pure: rejection analysis
# --------------------------------------------------------------------------

def parse_lpp_limit(message: str | None) -> float | None:
    """Extract the allowed price ceiling from an LPP rejection message."""
    if not message:
        return None
    m = _LPP_RE.search(message)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def classify_rejection(message: str | None) -> RejectionKind:
    """Map a broker rejection message to a RejectionKind.

    Unrecognised messages return OTHER, which is NOT retryable — we would
    rather skip a trade than loop on an unknown error.
    """
    if not message:
        return RejectionKind.OTHER
    text = message.lower()
    for needle, kind in _REJECTION_PATTERNS:
        if needle in text:
            return kind
    return RejectionKind.OTHER


def lpp_reprice(
    *,
    lpp_limit: float | None,
    live_ltp: float,
    tick: float,
    safety_factor: float = 0.99,
    ltp_band: float = 1.09,
) -> float:
    """Highest price that should clear the LPP band.

    Takes the lower of:
      * live LTP x `ltp_band`  (Zerodha's band is ~10% of LTP; 1.09 keeps margin)
      * the exact ceiling from the message x `safety_factor`

    FLOOR-rounded so tick rounding can never push us back over the limit.

    Raises:
        ValueError: if neither input gives a usable cap.
    """
    caps: list[float] = []
    if live_ltp > 0:
        caps.append(live_ltp * ltp_band)
    if lpp_limit and lpp_limit > 0:
        caps.append(lpp_limit * safety_factor)
    if not caps:
        raise ValueError("no usable LPP cap (no live LTP and no parsed limit)")
    return round_price(min(caps), tick, FLOOR)


def resolve_order_type(*, is_index_symbol: bool, configured: str) -> str:
    """Order type actually sent to the broker.

    BUILD_SPEC R6: Zerodha blocks MARKET on stock options. A MARKET setting is
    downgraded to LIMIT for stock options rather than sent and rejected.
    """
    if configured == OrderType.MARKET and not is_index_symbol:
        return OrderType.LIMIT
    return configured


def resolve_product(*, is_index_symbol: bool, stock_product: str, index_product: str) -> str:
    return index_product if is_index_symbol else stock_product


# --------------------------------------------------------------------------
# Broker calls — `kite` is the authenticated client
# --------------------------------------------------------------------------

def place(
    kite: Any,
    *,
    tradingsymbol: str,
    exchange: str,
    side: str,
    quantity: int,
    price: float,
    order_type: str = OrderType.LIMIT,
    product: str = Product.NRML,
    validity: str = Validity.IOC,
    tag: str | None = None,
) -> OrderResult:
    """Place one order. Never raises for broker-level rejections."""
    t_req = mono_ns()
    params: dict[str, Any] = {
        "variety": "regular",
        "exchange": exchange,
        "tradingsymbol": tradingsymbol,
        "transaction_type": side,
        "quantity": int(quantity),
        "order_type": order_type,
        "product": product,
        "validity": validity,
    }
    if order_type == OrderType.LIMIT:
        params["price"] = float(price)
    if tag:
        params["tag"] = tag[:20]                 # Kite caps tag length

    try:
        order_id = kite.place_order(**params)
        return OrderResult(
            success=True, order_id=str(order_id),
            t_req_ns=t_req, t_ack_ns=mono_ns(), raw={"params": params},
        )
    except Exception as exc:
        msg = str(exc)
        return OrderResult(
            success=False, error=msg,
            rejection_kind=classify_rejection(msg),
            lpp_limit=parse_lpp_limit(msg),
            t_req_ns=t_req, t_ack_ns=mono_ns(), raw={"params": params},
        )


def modify(
    kite: Any, *, order_id: str, price: float | None = None,
    order_type: str | None = None, validity: str | None = None,
) -> OrderResult:
    """Modify a pending order. Kite caps modifications at 25 per order."""
    t_req = mono_ns()
    params: dict[str, Any] = {"variety": "regular", "order_id": order_id}
    if price is not None:
        params["price"] = float(price)
    if order_type is not None:
        params["order_type"] = order_type
    if validity is not None:
        params["validity"] = validity
    try:
        kite.modify_order(**params)
        return OrderResult(success=True, order_id=order_id,
                           t_req_ns=t_req, t_ack_ns=mono_ns())
    except Exception as exc:
        msg = str(exc)
        return OrderResult(success=False, order_id=order_id, error=msg,
                           rejection_kind=classify_rejection(msg),
                           lpp_limit=parse_lpp_limit(msg),
                           t_req_ns=t_req, t_ack_ns=mono_ns())


def cancel(kite: Any, *, order_id: str) -> OrderResult:
    t_req = mono_ns()
    try:
        kite.cancel_order(variety="regular", order_id=order_id)
        return OrderResult(success=True, order_id=order_id,
                           t_req_ns=t_req, t_ack_ns=mono_ns())
    except Exception as exc:
        msg = str(exc)
        return OrderResult(success=False, order_id=order_id, error=msg,
                           rejection_kind=classify_rejection(msg),
                           t_req_ns=t_req, t_ack_ns=mono_ns())


def read_history(kite: Any, order_id: str) -> OrderResult:
    """Latest state of one order from `order_history`.

    Interim statuses are reported as-is; the caller decides when to stop
    waiting (BUILD_SPEC R13 — only COMPLETE/REJECTED/CANCELLED are terminal).
    """
    t_req = mono_ns()
    try:
        history = kite.order_history(order_id=order_id)
    except Exception as exc:
        msg = str(exc)
        return OrderResult(success=False, order_id=order_id, error=msg,
                           rejection_kind=classify_rejection(msg),
                           t_req_ns=t_req, t_ack_ns=mono_ns())

    if not history:
        return OrderResult(success=False, order_id=order_id,
                           error="empty order history",
                           t_req_ns=t_req, t_ack_ns=mono_ns())

    last = history[-1]
    return summarise_history_row(last, order_id, t_req)


def summarise_history_row(row: dict, order_id: str, t_req_ns: int = 0) -> OrderResult:
    """Pure: turn one order-history / postback row into an OrderResult."""
    status = str(row.get("status", "") or "").upper()
    message = row.get("status_message") or row.get("status_message_raw")
    return OrderResult(
        success=status == OrderStatus.COMPLETE,
        order_id=str(row.get("order_id") or order_id),
        error=message if status == OrderStatus.REJECTED else None,
        rejection_kind=classify_rejection(message) if status == OrderStatus.REJECTED else None,
        lpp_limit=parse_lpp_limit(message),
        status=status or None,
        filled_quantity=int(row.get("filled_quantity") or 0),
        average_price=float(row.get("average_price") or 0.0),
        t_req_ns=t_req_ns,
        t_ack_ns=mono_ns(),
        raw=row,
    )


def is_final(result: OrderResult) -> bool:
    """True when the order lifecycle has ended."""
    return is_terminal(result.status)


__all__ = [
    "parse_lpp_limit", "classify_rejection", "lpp_reprice",
    "resolve_order_type", "resolve_product",
    "place", "modify", "cancel", "read_history",
    "summarise_history_row", "is_final",
    "Side", "OrderType", "Product", "Validity",
]
