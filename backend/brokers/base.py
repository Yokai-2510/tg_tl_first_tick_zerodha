"""
Broker abstraction — the contract every broker adapter must satisfy.

Two independent roles, selectable per broker in config:

    data_broker   provides the live feed, the instrument master and REST quotes
    trade_broker  places, modifies and cancels orders, and owns the position book

They can be the SAME broker or DIFFERENT ones. Running data on Upstox while
trading on Zerodha (or paper) is explicitly supported, and is the cleanest way
to test without touching a Zerodha API key that another system is using.

NORMALISATION IS THE WHOLE POINT OF THIS MODULE. Every adapter converts its
broker's wire format into the canonical shapes below, so the engine never
contains a single broker conditional.

Canonical tick dict (what `on_tick_batch` receives, from ANY data broker):

    {
      "instrument_token":   int,      # internal primary key (see Instrument.token)
      "last_price":         float,
      "exchange_timestamp": datetime | int | None,
      "last_trade_time":    datetime | int | None,
      "volume_traded":      int,
      "oi":                 int,
      "average_traded_price": float,
      "total_buy_quantity": int,
      "total_sell_quantity": int,
      "ohlc":  {"open": float, "high": float, "low": float, "close": float},
      "depth": {"buy":  [{"price": float, "quantity": int, "orders": int}, ...],
                "sell": [{"price": float, "quantity": int, "orders": int}, ...]},
    }

Canonical order-update dict (what `on_order_event` receives):

    {
      "order_id":         str,
      "status":           str,   # normalised to OrderStatus values (UPPER)
      "transaction_type": str,   # BUY | SELL
      "filled_quantity":  int,
      "average_price":    float,
      "tag":              str | None,
      "status_message":   str | None,
    }
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any, Callable, Protocol, runtime_checkable

from ..core.enums import OrderStatus
from ..core.models import Instrument, OrderResult


def surrogate_token(broker_key: str) -> int:
    """Stable 56-bit integer id derived from a broker's native string key.

    Kite identifies instruments with an int; Upstox uses a string like
    `NSE_FO|49520`. The engine keys everything by int, so string-keyed brokers
    get a deterministic surrogate.

    Deterministic across processes and restarts, so recorded tick files stay
    readable. 56 bits makes a collision negligible at our instrument counts
    (a 32-bit CRC would be a real risk across a 100k-row master).
    """
    digest = hashlib.blake2b(broker_key.encode("utf-8"), digest_size=7).digest()
    return int.from_bytes(digest, "big")


def contract_key(
    underlying: str, expiry: date | None, strike: float, option_type: str | None
) -> tuple:
    """Broker-independent identity for one contract.

    Used to match the SAME option across two brokers when data and trading are
    split. Tradingsymbol formats differ between brokers; the exchange-level
    facts (underlying, expiry, strike, CE/PE) do not.
    """
    return (underlying.upper(), expiry, round(float(strike), 2),
            (option_type or "").upper())


# --------------------------------------------------------------------------
# Status normalisation
# --------------------------------------------------------------------------

#: Broker-specific status strings -> canonical OrderStatus.
_STATUS_ALIASES: dict[str, str] = {
    # Kite already uses the canonical spellings.
    "COMPLETE": OrderStatus.COMPLETE,
    "REJECTED": OrderStatus.REJECTED,
    "CANCELLED": OrderStatus.CANCELLED,
    "CANCELED": OrderStatus.CANCELLED,
    "OPEN": OrderStatus.OPEN,
    # Upstox spellings.
    "COMPLETED": OrderStatus.COMPLETE,
    "FILLED": OrderStatus.COMPLETE,
    "OPEN PENDING": OrderStatus.OPEN_PENDING,
    "VALIDATION PENDING": OrderStatus.VALIDATION_PENDING,
    "PUT ORDER REQ RECEIVED": OrderStatus.PUT_ORDER_REQ_RECEIVED,
    "TRIGGER PENDING": OrderStatus.TRIGGER_PENDING,
    "MODIFY PENDING": OrderStatus.MODIFY_PENDING,
    "MODIFY VALIDATION PENDING": OrderStatus.MODIFY_VALIDATION_PENDING,
    "CANCEL PENDING": OrderStatus.CANCEL_PENDING,
    "AFTER MARKET ORDER REQ RECEIVED": OrderStatus.PUT_ORDER_REQ_RECEIVED,
}


def normalise_status(raw: str | None) -> str | None:
    """Map any broker's order status onto the canonical vocabulary."""
    if not raw:
        return None
    key = " ".join(str(raw).strip().upper().split())
    return _STATUS_ALIASES.get(key, key)


# --------------------------------------------------------------------------
# Protocols
# --------------------------------------------------------------------------

@runtime_checkable
class DataBroker(Protocol):
    """Live market data, instrument master and REST quotes."""

    name: str

    def login(self) -> Any: ...

    def load_master(self, exchanges: list[str]) -> None:
        """Fetch and cache the instrument master for the given segments."""

    def equity_instrument(self, symbol: str) -> Instrument | None: ...

    def index_instrument(self, symbol: str) -> Instrument | None: ...

    def expiries_for(self, underlying: str) -> list[date]: ...

    def build_chain(
        self, underlying: str, expiry: date, spot: float, per_side: int
    ) -> list[Instrument]: ...

    def snapshot(self, symbols: list[str]) -> dict[str, dict]:
        """`{symbol: {ltp, prev_close, open, high, low, volume}}` for ranking."""

    def prev_close(self, instruments: list[Instrument]) -> dict[int, float]:
        """`{token: previous_close}` — the option reference price (R14)."""

    # -- feed --
    def connect_feed(
        self,
        on_tick_batch: Callable[[list[dict], int], None],
        on_state: Callable[[str, dict], None] | None = None,
    ) -> None: ...

    def subscribe(self, instruments: list[Instrument], mode: str) -> None: ...

    def close_feed(self) -> None: ...

    def feed_stats(self) -> dict: ...


@runtime_checkable
class TradeBroker(Protocol):
    """Order placement and the broker-side position view."""

    name: str

    def login(self) -> Any: ...

    def place(
        self, *, instrument: Instrument, side: str, quantity: int, price: float,
        order_type: str, product: str, validity: str, tag: str | None = None,
    ) -> OrderResult: ...

    def modify(self, *, order_id: str, price: float | None = None,
               order_type: str | None = None) -> OrderResult: ...

    def cancel(self, *, order_id: str) -> OrderResult: ...

    def order_state(self, order_id: str) -> OrderResult:
        """Latest state of one order, with a normalised status."""

    def positions(self) -> dict[str, dict]:
        """`{tradingsymbol: {quantity, average_price, last_price}}`."""

    def margins(self) -> dict: ...

    def available_cash(self) -> float: ...

    def resolve_order_type(self, *, is_index: bool, configured: str) -> str:
        """Broker-specific legality (e.g. no MARKET on stock options)."""

    def resolve_product(self, *, is_index: bool, stock_product: str,
                        index_product: str) -> str:
        """Map canonical product names onto this broker's vocabulary."""

    # -- optional: order-update stream --
    def connect_order_stream(
        self, on_order_event: Callable[[dict], None]
    ) -> bool:
        """Subscribe to live order updates. Returns False if unsupported —
        the caller then falls back to polling `order_state`."""


class BrokerError(RuntimeError):
    """Adapter-level failure that the engine should surface, not swallow."""


__all__ = [
    "DataBroker", "TradeBroker", "BrokerError",
    "surrogate_token", "contract_key", "normalise_status",
]
