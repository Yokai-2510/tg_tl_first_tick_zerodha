"""Upstox adapter — satisfies both DataBroker and TradeBroker."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Callable

import requests

from ...core.enums import SubscribeMode
from ...core.models import Instrument, OrderResult
from ...core.symbols import normalise, option_exchange
from ..base import BrokerError
from . import auth, instruments as uinst, orders as uorders
from .feed import UpstoxFeed

QUOTE_URL = "https://api.upstox.com/v2/market-quote/quotes"
OHLC_URL = "https://api.upstox.com/v2/market-quote/ohlc"

#: Upstox caps instrument_key params per quote call.
MAX_KEYS_PER_CALL = 500


class UpstoxBroker:
    name = "upstox"

    def __init__(self, credentials: dict, *, cache_dir: Path,
                 limiter=None, ws_cfg: dict | None = None):
        self.credentials = credentials
        self.cache_dir = Path(cache_dir)
        self.limiter = limiter
        self.ws_cfg = ws_cfg or {}
        self.session: auth.Session | None = None
        self.http = requests.Session()
        self.feed: UpstoxFeed | None = None
        self.master: dict[str, list[dict]] = {}

    # -- session -----------------------------------------------------------

    def login(self) -> auth.Session:
        if self.session is None:
            self.session = auth.login(
                self.credentials, cache_path=self.cache_dir / "upstox_token.json")
        return self.session

    @property
    def token(self) -> str:
        if self.session is None:
            raise BrokerError("login() before using the Upstox broker")
        return self.session.access_token

    # -- instruments -------------------------------------------------------

    def load_master(self, exchanges: list[str]) -> None:
        # Upstox ships one file per exchange (NSE covers NSE_EQ + NSE_FO).
        for cdn in {uinst.EXCHANGE_TO_CDN.get(e.upper(), "NSE") for e in exchanges}:
            rows = uinst.download_master(cdn, self.cache_dir)
            if not rows:
                raise BrokerError(f"Upstox master for {cdn} could not be loaded")
            self.master[cdn] = rows

    def _all_rows(self) -> list[dict]:
        return [r for rows in self.master.values() for r in rows]

    def equity_instrument(self, symbol: str) -> Instrument | None:
        return uinst.equity_instrument(self._all_rows(), symbol)

    def index_instrument(self, symbol: str) -> Instrument | None:
        return uinst.index_instrument(symbol)

    def expiries_for(self, underlying: str) -> list[date]:
        return uinst.expiries_for(self._all_rows(), underlying)

    def build_chain(self, underlying: str, expiry: date, spot: float,
                    per_side: int) -> list[Instrument]:
        cdn = uinst.EXCHANGE_TO_CDN.get(option_exchange(underlying), "NSE")
        rows = self.master.get(cdn) or self._all_rows()
        return uinst.build_chain(rows, underlying, expiry, spot, per_side)

    # -- market data -------------------------------------------------------

    def _quote(self, keys: list[str]) -> dict:
        out: dict = {}
        headers = uorders.headers_for(self.token)
        for i in range(0, len(keys), MAX_KEYS_PER_CALL):
            batch = keys[i:i + MAX_KEYS_PER_CALL]
            if self.limiter is not None:
                self.limiter.acquire("quote", timeout=5.0)
            try:
                data = self.http.get(QUOTE_URL, headers=headers,
                                     params={"instrument_key": ",".join(batch)},
                                     timeout=10).json()
            except Exception:
                continue
            if data.get("status") == "success":
                out.update(data.get("data") or {})
        return out

    def snapshot(self, symbols: list[str]) -> dict[str, dict]:
        insts = {s: self.equity_instrument(s) for s in symbols}
        keys = {i.data_key: s for s, i in insts.items() if i is not None}
        raw = self._quote(list(keys))
        out: dict[str, dict] = {}
        for row in raw.values():
            key = str(row.get("instrument_token") or "")
            symbol = keys.get(key)
            if symbol is None:
                # Upstox keys responses by "NSE_EQ:SYMBOL"; fall back to the symbol.
                symbol = normalise(str(row.get("symbol") or ""))
                if symbol not in insts:
                    continue
            ohlc = row.get("ohlc") or {}
            out[symbol] = {
                "ltp": float(row.get("last_price") or 0.0),
                "prev_close": float(ohlc.get("close") or 0.0),
                "open": float(ohlc.get("open") or 0.0),
                "high": float(ohlc.get("high") or 0.0),
                "low": float(ohlc.get("low") or 0.0),
                "volume": int(row.get("volume") or 0),
            }
        return out

    def prev_close(self, instruments: list[Instrument]) -> dict[int, float]:
        keys = {i.data_key: i.token for i in instruments if i.data_key}
        raw = self._quote(list(keys))
        out: dict[int, float] = {}
        for row in raw.values():
            token = keys.get(str(row.get("instrument_token") or ""))
            close = float((row.get("ohlc") or {}).get("close") or 0.0)
            if token is not None and close > 0:
                out[token] = close
        return out

    # -- feed --------------------------------------------------------------

    def connect_feed(self, on_tick_batch: Callable[[list[dict], int], None],
                     on_state: Callable[[str, dict], None] | None = None,
                     on_order_event: Callable[[dict], None] | None = None) -> None:
        self.feed = UpstoxFeed(self.token)
        self.feed.on_tick_batch = on_tick_batch
        self.feed.on_state = on_state
        self.feed.connect()

    def subscribe(self, instruments: list[Instrument],
                  mode: str = SubscribeMode.FULL) -> None:
        if self.feed is None:
            raise BrokerError("feed not connected")
        if self.feed._started:
            self.feed.subscribe_now(instruments, mode)
        else:
            self.feed.add(instruments, mode)

    def close_feed(self) -> None:
        if self.feed is not None:
            self.feed.close()

    def feed_stats(self) -> dict:
        return self.feed.stats() if self.feed else {"broker": self.name,
                                                    "connected": False}

    # -- trading -----------------------------------------------------------

    def place(self, *, instrument: Instrument, side: str, quantity: int,
              price: float, order_type: str, product: str, validity: str,
              tag: str | None = None) -> OrderResult:
        return uorders.place(self.http, self.token, instrument=instrument,
                             side=side, quantity=quantity, price=price,
                             order_type=order_type, product=product,
                             validity=validity, tag=tag)

    def modify(self, *, order_id: str, price: float | None = None,
               order_type: str | None = None) -> OrderResult:
        return uorders.modify(self.http, self.token, order_id=order_id,
                              price=price, order_type=order_type)

    def cancel(self, *, order_id: str) -> OrderResult:
        return uorders.cancel(self.http, self.token, order_id=order_id)

    def order_state(self, order_id: str) -> OrderResult:
        return uorders.order_state(self.http, self.token, order_id)

    def positions(self) -> dict[str, dict]:
        return uorders.positions(self.http, self.token)

    def margins(self) -> dict:
        return uorders.margins(self.http, self.token)

    def available_cash(self) -> float:
        return uorders.available_cash(self.margins())

    def resolve_order_type(self, *, is_index: bool, configured: str) -> str:
        return uorders.resolve_order_type(is_index=is_index, configured=configured)

    def resolve_product(self, *, is_index: bool, stock_product: str,
                        index_product: str) -> str:
        return uorders.resolve_product(is_index=is_index,
                                       stock_product=stock_product,
                                       index_product=index_product)

    def connect_order_stream(self, on_order_event: Callable[[dict], None]) -> bool:
        """Upstox order updates need a SEPARATE portfolio-stream websocket.

        Not wired here: the engine falls back to polling `order_state`, which is
        correct but slower than Kite's same-socket updates. Worth adding if
        Upstox becomes the primary trade broker.
        """
        return False


__all__ = ["UpstoxBroker", "QUOTE_URL", "OHLC_URL", "MAX_KEYS_PER_CALL"]
