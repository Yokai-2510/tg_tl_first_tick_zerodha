"""
Broker factory and cross-broker instrument resolution.

`data_broker` and `trade_broker` are chosen independently in config. When they
differ, every tradeable instrument needs BOTH brokers' native ids: the feed
arrives keyed by the data broker, but orders must be addressed to the trade
broker.

Tradingsymbols are NOT reliable for this match — Kite writes
`INDIGO26AUG5300PE`, Upstox writes `INDIGO 26 AUG 5300 PE`. The match is done
on the exchange-level contract identity instead: (underlying, expiry, strike,
option type).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from ..core.models import Instrument
from .base import BrokerError, contract_key

ZERODHA = "zerodha"
UPSTOX = "upstox"
PAPER = "paper"

SUPPORTED_DATA = (ZERODHA, UPSTOX)
SUPPORTED_TRADE = (ZERODHA, UPSTOX, PAPER)


def make_broker(name: str, credentials: dict, *, cache_dir: Path,
                limiter=None, ws_cfg: dict | None = None) -> Any:
    """Instantiate a broker adapter by name."""
    key = str(name).strip().lower()
    if key == ZERODHA:
        from .kite.adapter import KiteBroker
        return KiteBroker(credentials, cache_dir=cache_dir, limiter=limiter,
                          ws_cfg=ws_cfg)
    if key == UPSTOX:
        from .upstox.adapter import UpstoxBroker
        return UpstoxBroker(credentials, cache_dir=cache_dir, limiter=limiter,
                            ws_cfg=ws_cfg)
    raise BrokerError(
        f"unknown broker {name!r}; supported: {', '.join(SUPPORTED_DATA)}"
    )


def credentials_for(broker: str, all_credentials: dict) -> dict:
    """Pull one broker's credential block.

    Accepts either a nested layout (`{"zerodha": {...}, "upstox": {...}}`) or a
    flat one, so an existing single-broker credentials file keeps working.
    """
    key = str(broker).strip().lower()
    block = all_credentials.get(key)
    if isinstance(block, dict) and block:
        return block
    return {k: v for k, v in all_credentials.items() if not isinstance(v, dict)}


class BrokerPair:
    """The data broker and the trade broker, plus contract mapping between them."""

    def __init__(self, data: Any, trade: Any, *, paper: bool = False):
        self.data = data
        self.trade = trade
        self.paper = paper
        self._trade_index: dict[tuple, Instrument] = {}

    @property
    def same_broker(self) -> bool:
        return self.data is self.trade

    @property
    def names(self) -> dict[str, str]:
        return {"data": getattr(self.data, "name", "?"),
                "trade": PAPER if self.paper else getattr(self.trade, "name", "?")}

    def login(self) -> None:
        self.data.login()
        if not self.same_broker and not self.paper:
            self.trade.login()

    def load_master(self, exchanges: list[str]) -> None:
        self.data.load_master(exchanges)
        if not self.same_broker and not self.paper:
            self.trade.load_master(exchanges)

    # -- cross-broker mapping ---------------------------------------------

    def index_trade_chain(self, underlying: str, expiry, spot: float,
                          per_side: int) -> None:
        """Cache the trade broker's chain for one underlying, keyed by contract."""
        if self.same_broker or self.paper:
            return
        try:
            for inst in self.trade.build_chain(underlying, expiry, spot, per_side):
                self._trade_index[inst.contract_id] = inst
        except Exception as exc:
            raise BrokerError(
                f"trade broker has no chain for {underlying} {expiry}: {exc}"
            ) from None

    def resolve(self, instrument: Instrument) -> Instrument:
        """Attach the trade broker's identifiers to a data-broker instrument.

        Raises when the contract is missing at the trade broker — placing an
        order against a guessed identifier is not an acceptable fallback.
        """
        if self.same_broker or self.paper:
            return replace(instrument, trade_key=instrument.data_key)

        match = self._trade_index.get(instrument.contract_id)
        if match is None:
            raise BrokerError(
                f"{instrument.tradingsymbol} ({instrument.contract_id}) exists at "
                f"{self.data.name} but not at {self.trade.name}; refusing to trade it"
            )
        return replace(
            instrument,
            trade_key=match.data_key,
            # The trade broker's own symbol/lot are authoritative for orders.
            tradingsymbol=match.tradingsymbol or instrument.tradingsymbol,
            lot_size=match.lot_size or instrument.lot_size,
            exchange=match.exchange or instrument.exchange,
        )

    def resolve_all(self, instruments: list[Instrument]) -> tuple[list[Instrument],
                                                                  list[str]]:
        """Resolve a batch. Returns (resolved, unresolved_symbols)."""
        resolved, missing = [], []
        for inst in instruments:
            try:
                resolved.append(self.resolve(inst))
            except BrokerError:
                missing.append(inst.tradingsymbol)
        return resolved, missing


def build_pair(cfg, credentials: dict, *, cache_dir: Path, limiter=None) -> BrokerPair:
    """Construct the broker pair described by config."""
    data_name = str(cfg.broker.data_broker).lower()
    trade_name = str(cfg.broker.trade_broker).lower()
    ws_cfg = cfg.broker.ws.model_dump() if hasattr(cfg.broker.ws, "model_dump") else {}

    data = make_broker(data_name, credentials_for(data_name, credentials),
                       cache_dir=cache_dir, limiter=limiter, ws_cfg=ws_cfg)

    if trade_name == PAPER:
        return BrokerPair(data, data, paper=True)
    if trade_name == data_name:
        return BrokerPair(data, data)

    trade = make_broker(trade_name, credentials_for(trade_name, credentials),
                        cache_dir=cache_dir, limiter=limiter, ws_cfg=ws_cfg)
    return BrokerPair(data, trade)


__all__ = ["make_broker", "build_pair", "credentials_for", "BrokerPair",
           "ZERODHA", "UPSTOX", "PAPER", "SUPPORTED_DATA", "SUPPORTED_TRADE"]
