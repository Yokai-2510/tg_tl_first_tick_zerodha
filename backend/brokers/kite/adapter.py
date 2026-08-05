"""Zerodha/Kite adapter — satisfies both DataBroker and TradeBroker."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Callable

from ...core.enums import InstrumentKind, SubscribeMode
from ...core.models import Instrument, OrderResult
from ...core.symbols import spot_quote_key
from ..base import BrokerError
from . import auth, instruments as kinst, orders as korders, portfolio, quotes
from .ratelimit import RateLimiter
from .ticker import KiteFeed


class KiteBroker:
    """One object, both roles. Holds the authenticated client and the feed."""

    name = "zerodha"

    def __init__(self, credentials: dict, *, cache_dir: Path,
                 limiter: RateLimiter | None = None, ws_cfg: dict | None = None):
        self.credentials = credentials
        self.cache_dir = Path(cache_dir)
        self.limiter = limiter
        self.ws_cfg = ws_cfg or {}
        self.kite: Any = None
        self.session: auth.Session | None = None
        self.feed: KiteFeed | None = None
        self.master: dict[str, list[dict]] = {}

    # -- session -----------------------------------------------------------

    def login(self) -> auth.Session:
        if self.session is None:
            self.kite, self.session = auth.login(
                self.credentials, cache_path=self.cache_dir / "kite_token.json")
        return self.session

    # -- instruments -------------------------------------------------------

    def load_master(self, exchanges: list[str]) -> None:
        for exch in exchanges:
            self.master[exch] = kinst.load_master(self.kite, exch)

    def _all_rows(self) -> list[dict]:
        return [r for rows in self.master.values() for r in rows]

    def equity_instrument(self, symbol: str) -> Instrument | None:
        inst = kinst.equity_instrument(self.master.get("NSE", []), symbol)
        return _tag(inst) if inst else None

    def index_instrument(self, symbol: str) -> Instrument | None:
        target = spot_quote_key(symbol).split(":", 1)[1]
        for exch in ("NSE", "BFO", "NFO"):
            for row in self.master.get(exch, ()):
                if str(row.get("tradingsymbol")) == target:
                    token = int(row["instrument_token"])
                    return Instrument(
                        token=token, tradingsymbol=symbol.upper(),
                        exchange="BSE" if exch == "BFO" else "NSE",
                        underlying=symbol.upper(), kind=InstrumentKind.INDEX,
                        is_index=True, subscribe_mode=SubscribeMode.QUOTE,
                        wave=1, data_key=str(token), trade_key=str(token))
        return None

    def expiries_for(self, underlying: str) -> list[date]:
        return kinst.expiries_for(self._all_rows(), underlying)

    def build_chain(self, underlying: str, expiry: date, spot: float,
                    per_side: int) -> list[Instrument]:
        from ...core.symbols import option_exchange
        rows = self.master.get(option_exchange(underlying)) or self._all_rows()
        return [_tag(i) for i in
                kinst.build_chain(rows, underlying, expiry, spot, per_side)]

    # -- market data -------------------------------------------------------

    def snapshot(self, symbols: list[str]) -> dict[str, dict]:
        keys = {spot_quote_key(s): s for s in symbols}
        raw = quotes.quote(self.kite, list(keys), limiter=self.limiter)
        return quotes.snapshot_from_quotes(raw, keys)

    def prev_close(self, instruments: list[Instrument]) -> dict[int, float]:
        keys = {i.quote_key: i.token for i in instruments}
        raw = quotes.quote(self.kite, list(keys), limiter=self.limiter)
        out: dict[int, float] = {}
        for key, row in raw.items():
            token = keys.get(key)
            close = float((row.get("ohlc") or {}).get("close") or 0.0)
            if token is not None and close > 0:
                out[token] = close
        return out

    # -- feed --------------------------------------------------------------

    def connect_feed(self, on_tick_batch: Callable[[list[dict], int], None],
                     on_state: Callable[[str, dict], None] | None = None,
                     on_order_event: Callable[[dict], None] | None = None) -> None:
        if self.session is None:
            raise BrokerError("login() before connect_feed()")
        self.feed = KiteFeed(
            self.session.api_key, self.session.access_token,
            reconnect_max_tries=self.ws_cfg.get("reconnect_max_tries", 50),
            reconnect_max_delay=self.ws_cfg.get("reconnect_max_delay_s", 30))
        self.feed.on_tick_batch = on_tick_batch
        self.feed.on_state = on_state
        self.feed.on_order_event = on_order_event
        self.feed.connect()

    def subscribe(self, instruments: list[Instrument],
                  mode: str = SubscribeMode.FULL) -> None:
        if self.feed is None:
            raise BrokerError("feed not connected")
        tokens = [i.token for i in instruments]
        if self.feed._started:
            self.feed.subscribe_now(tokens, mode)
        else:
            self.feed.add(tokens, mode)

    def close_feed(self) -> None:
        if self.feed is not None:
            self.feed.close()

    def feed_stats(self) -> dict:
        return ({"broker": self.name, **self.feed.stats.as_dict()}
                if self.feed else {"broker": self.name, "connected": False})

    # -- trading -----------------------------------------------------------

    def place(self, *, instrument: Instrument, side: str, quantity: int,
              price: float, order_type: str, product: str, validity: str,
              tag: str | None = None) -> OrderResult:
        return korders.place(
            self.kite, tradingsymbol=instrument.tradingsymbol,
            exchange=instrument.exchange, side=side, quantity=quantity,
            price=price, order_type=order_type, product=product,
            validity=validity, tag=tag)

    def modify(self, *, order_id: str, price: float | None = None,
               order_type: str | None = None) -> OrderResult:
        return korders.modify(self.kite, order_id=order_id, price=price,
                              order_type=order_type)

    def cancel(self, *, order_id: str) -> OrderResult:
        return korders.cancel(self.kite, order_id=order_id)

    def order_state(self, order_id: str) -> OrderResult:
        return korders.read_history(self.kite, order_id)

    def positions(self) -> dict[str, dict]:
        data = portfolio.positions(self.kite, limiter=self.limiter)
        return {sym: {"quantity": int(r.get("quantity") or 0),
                      "average_price": float(r.get("average_price") or 0.0),
                      "last_price": float(r.get("last_price") or 0.0),
                      "pnl": float(r.get("pnl") or 0.0)}
                for sym, r in portfolio.day_position_map(data).items()}

    def margins(self) -> dict:
        return portfolio.margins(self.kite, limiter=self.limiter)

    def available_cash(self) -> float:
        return portfolio.available_cash(self.margins())

    def resolve_order_type(self, *, is_index: bool, configured: str) -> str:
        return korders.resolve_order_type(is_index_symbol=is_index,
                                          configured=configured)

    def resolve_product(self, *, is_index: bool, stock_product: str,
                        index_product: str) -> str:
        return korders.resolve_product(is_index_symbol=is_index,
                                       stock_product=stock_product,
                                       index_product=index_product)

    def connect_order_stream(self, on_order_event: Callable[[dict], None]) -> bool:
        """Kite delivers order updates on the SAME market-data websocket, so
        this is wired in `connect_feed`. Returns True to say it is covered."""
        if self.feed is not None:
            self.feed.on_order_event = on_order_event
            return True
        return False


def _tag(inst: Instrument) -> Instrument:
    """Kite's native id is the int token; record it in both key fields."""
    from dataclasses import replace
    key = str(inst.token)
    return replace(inst, data_key=key, trade_key=key)


__all__ = ["KiteBroker"]
