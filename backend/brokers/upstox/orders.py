"""
Upstox order placement, status and portfolio.

Plain `requests` against the documented REST endpoints — no SDK needed for the
order path, so this stays testable with a stub session.

Vocabulary differences handled here so the engine never branches on broker:

    product     MIS -> "I" (intraday)   NRML/CNC -> "D" (delivery)
    status      lowercase, e.g. "complete" -> normalised to COMPLETE
    instrument  `instrument_token` in the payload is the instrument_key string

Note: NSE stock options are physically settled and Upstox does not offer
intraday (I) on them — the same constraint Zerodha expresses as "no MIS in the
last two expiry days". `resolve_product` therefore returns D for stock options.
"""

from __future__ import annotations

from typing import Any

import requests

from ...core.enums import OrderType, RejectionKind, Side
from ...core.models import Instrument, OrderResult
from ...core.timeutil import mono_ns
from ..base import normalise_status

PLACE_URL = "https://api-hft.upstox.com/v3/order/place"
MODIFY_URL = "https://api-hft.upstox.com/v3/order/modify"
CANCEL_URL = "https://api-hft.upstox.com/v3/order/cancel"
DETAILS_URL = "https://api.upstox.com/v2/order/details"
POSITIONS_URL = "https://api.upstox.com/v2/portfolio/short-term-positions"
FUNDS_URL = "https://api.upstox.com/v2/user/get-funds-and-margin"
EXIT_ALL_URL = "https://api.upstox.com/v2/order/positions/exit"

#: Canonical product -> Upstox product code.
PRODUCT_MAP = {"MIS": "I", "NRML": "D", "CNC": "D", "I": "I", "D": "D"}


def headers_for(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def classify(message: str | None) -> RejectionKind:
    """Map an Upstox error message onto the canonical rejection taxonomy."""
    if not message:
        return RejectionKind.OTHER
    text = str(message).lower()
    if "margin" in text or "insufficient" in text or "funds" in text:
        return RejectionKind.MARGIN
    if "price" in text and ("range" in text or "band" in text or "circuit" in text):
        return RejectionKind.LPP
    if "order type" in text or "market order" in text or "not offered" in text:
        return RejectionKind.ORDER_TYPE
    if "blocked" in text or "not allowed" in text or "freeze" in text:
        return RejectionKind.RMS
    if "too many" in text or "rate" in text:
        return RejectionKind.RATE_LIMIT
    if "timeout" in text or "timed out" in text or "connection" in text:
        return RejectionKind.NETWORK
    if "token" in text or "unauthor" in text or "expired" in text:
        return RejectionKind.AUTH
    return RejectionKind.OTHER


def _error_text(payload: dict) -> str:
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict):
            return str(first.get("message") or first.get("errorCode") or first)
        return str(first)
    return str(errors or payload.get("message") or "unknown error")


def resolve_product(*, is_index: bool, stock_product: str, index_product: str) -> str:
    """Canonical product -> Upstox code.

    Stock options are physically settled and Upstox does not offer intraday on
    them, so they are always delivery regardless of what was configured.
    """
    if not is_index:
        return "D"
    return PRODUCT_MAP.get(str(index_product).upper(), "I")


def resolve_order_type(*, is_index: bool, configured: str) -> str:
    """MARKET is not accepted on NSE stock options; downgrade to LIMIT."""
    if str(configured).upper() == OrderType.MARKET and not is_index:
        return OrderType.LIMIT
    return str(configured).upper()


# --------------------------------------------------------------------------

def place(
    session: Any, access_token: str, *, instrument: Instrument, side: str,
    quantity: int, price: float, order_type: str = OrderType.LIMIT,
    product: str = "D", validity: str = "DAY", tag: str | None = None,
    timeout: float = 5.0,
) -> OrderResult:
    """Place one order. Broker rejections are returned, not raised."""
    t_req = mono_ns()
    payload = {
        "instrument_token": instrument.trade_key or instrument.data_key,
        "quantity": int(quantity),
        "product": PRODUCT_MAP.get(str(product).upper(), "D"),
        "validity": str(validity).upper(),
        "price": float(price) if str(order_type).upper() == OrderType.LIMIT else 0.0,
        "order_type": str(order_type).upper(),
        "transaction_type": str(side).upper(),
        "tag": (tag or "")[:20],
        "disclosed_quantity": 0,
        "trigger_price": 0.0,
        "is_amo": False,
        "slice": True,
    }
    try:
        resp = session.post(PLACE_URL, json=payload,
                            headers=headers_for(access_token), timeout=timeout)
        data = resp.json()
    except Exception as exc:
        return OrderResult(success=False, error=str(exc),
                           rejection_kind=RejectionKind.NETWORK,
                           t_req_ns=t_req, t_ack_ns=mono_ns())

    t_ack = mono_ns()
    if data.get("status") == "success":
        ids = (data.get("data") or {}).get("order_ids") or []
        return OrderResult(success=True, order_id=str(ids[0]) if ids else None,
                           t_req_ns=t_req, t_ack_ns=t_ack, raw=data)

    message = _error_text(data)
    return OrderResult(success=False, error=message, rejection_kind=classify(message),
                       t_req_ns=t_req, t_ack_ns=t_ack, raw=data)


def modify(session: Any, access_token: str, *, order_id: str,
           price: float | None = None, order_type: str | None = None,
           quantity: int | None = None, timeout: float = 5.0) -> OrderResult:
    t_req = mono_ns()
    payload: dict[str, Any] = {"order_id": order_id}
    if price is not None:
        payload["price"] = float(price)
    if order_type is not None:
        payload["order_type"] = str(order_type).upper()
    if quantity is not None:
        payload["quantity"] = int(quantity)
    try:
        data = session.put(MODIFY_URL, json=payload,
                           headers=headers_for(access_token), timeout=timeout).json()
    except Exception as exc:
        return OrderResult(success=False, order_id=order_id, error=str(exc),
                           rejection_kind=RejectionKind.NETWORK,
                           t_req_ns=t_req, t_ack_ns=mono_ns())
    ok = data.get("status") == "success"
    message = None if ok else _error_text(data)
    return OrderResult(success=ok, order_id=order_id, error=message,
                       rejection_kind=None if ok else classify(message),
                       t_req_ns=t_req, t_ack_ns=mono_ns(), raw=data)


def cancel(session: Any, access_token: str, *, order_id: str,
           timeout: float = 5.0) -> OrderResult:
    t_req = mono_ns()
    try:
        data = session.delete(CANCEL_URL, params={"order_id": order_id},
                              headers=headers_for(access_token),
                              timeout=timeout).json()
    except Exception as exc:
        return OrderResult(success=False, order_id=order_id, error=str(exc),
                           rejection_kind=RejectionKind.NETWORK,
                           t_req_ns=t_req, t_ack_ns=mono_ns())
    ok = data.get("status") == "success"
    return OrderResult(success=ok, order_id=order_id,
                       error=None if ok else _error_text(data),
                       t_req_ns=t_req, t_ack_ns=mono_ns(), raw=data)


def order_state(session: Any, access_token: str, order_id: str,
                timeout: float = 5.0) -> OrderResult:
    """Latest state of one order, with the status normalised."""
    t_req = mono_ns()
    try:
        data = session.get(DETAILS_URL, params={"order_id": order_id},
                           headers=headers_for(access_token), timeout=timeout).json()
    except Exception as exc:
        return OrderResult(success=False, order_id=order_id, error=str(exc),
                           rejection_kind=RejectionKind.NETWORK,
                           t_req_ns=t_req, t_ack_ns=mono_ns())
    return summarise(data.get("data") or {}, order_id, t_req)


def summarise(row: dict, order_id: str, t_req_ns: int = 0) -> OrderResult:
    """Pure: one Upstox order row -> canonical OrderResult."""
    status = normalise_status(row.get("status"))
    message = row.get("status_message") or row.get("status_message_raw")
    return OrderResult(
        success=status == "COMPLETE",
        order_id=str(row.get("order_id") or order_id),
        error=message if status == "REJECTED" else None,
        rejection_kind=classify(message) if status == "REJECTED" else None,
        status=status,
        filled_quantity=int(row.get("filled_quantity") or 0),
        average_price=float(row.get("average_price") or 0.0),
        t_req_ns=t_req_ns, t_ack_ns=mono_ns(), raw=row,
    )


def normalise_order_event(row: dict) -> dict:
    """Upstox portfolio-stream update -> canonical order-event dict."""
    return {
        "order_id": str(row.get("order_id") or ""),
        "status": normalise_status(row.get("status")),
        "transaction_type": str(row.get("transaction_type") or "").upper(),
        "filled_quantity": int(row.get("filled_quantity") or 0),
        "average_price": float(row.get("average_price") or 0.0),
        "tag": row.get("tag"),
        "status_message": row.get("status_message"),
    }


# --------------------------------------------------------------------------

def positions(session: Any, access_token: str, timeout: float = 5.0) -> dict[str, dict]:
    """`{tradingsymbol: {quantity, average_price, last_price}}`."""
    try:
        data = session.get(POSITIONS_URL, headers=headers_for(access_token),
                           timeout=timeout).json()
    except Exception:
        return {}
    if data.get("status") != "success":
        return {}
    out = {}
    for row in data.get("data") or []:
        symbol = str(row.get("trading_symbol") or row.get("tradingsymbol") or "")
        if not symbol:
            continue
        out[symbol] = {
            "quantity": int(row.get("quantity") or 0),
            "average_price": float(row.get("average_price") or 0.0),
            "last_price": float(row.get("last_price") or 0.0),
            "pnl": float(row.get("pnl") or 0.0),
        }
    return out


def margins(session: Any, access_token: str, timeout: float = 5.0) -> dict:
    try:
        data = session.get(FUNDS_URL, headers=headers_for(access_token),
                           timeout=timeout).json()
    except Exception:
        return {}
    return data.get("data") or {}


def available_cash(margins_data: dict) -> float:
    equity = (margins_data or {}).get("equity") or {}
    for key in ("available_margin", "payin_amount", "net"):
        if equity.get(key) is not None:
            return float(equity[key])
    return 0.0


__all__ = [
    "PLACE_URL", "MODIFY_URL", "CANCEL_URL", "DETAILS_URL", "POSITIONS_URL",
    "PRODUCT_MAP", "headers_for", "classify", "resolve_product",
    "resolve_order_type", "place", "modify", "cancel", "order_state",
    "summarise", "normalise_order_event", "positions", "margins",
    "available_cash",
]
