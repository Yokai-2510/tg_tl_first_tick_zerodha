"""Shared fixtures and builders for the test suite."""

from __future__ import annotations

from datetime import date

import pytest

from backend.core.enums import (
    InstrumentKind, OptionType, PositionStatus, SubscribeMode, TradingMode,
)
from backend.core.models import ArmedState, Instrument, Position


def make_instrument(
    *,
    token: int = 111,
    tradingsymbol: str = "INDIGO26AUG5300PE",
    underlying: str = "INDIGO",
    option_type: str = OptionType.PE,
    strike: float = 5300.0,
    lot_size: int = 625,
    tick_size: float = 0.05,
    exchange: str = "NFO",
    is_index: bool = False,
    expiry: date | None = None,
) -> Instrument:
    return Instrument(
        token=token,
        tradingsymbol=tradingsymbol,
        exchange=exchange,
        underlying=underlying,
        kind=InstrumentKind.OPTION,
        lot_size=lot_size,
        tick_size=tick_size,
        instrument_type=option_type,
        strike=strike,
        expiry=expiry or date(2026, 8, 25),
        is_index=is_index,
        subscribe_mode=SubscribeMode.FULL,
        wave=2,
    )


def make_armed(*, ref_price: float = 117.85, lots: int = 1, **kw) -> ArmedState:
    return ArmedState(instrument=make_instrument(**kw), ref_price=ref_price, lots=lots)


def make_tick(
    *,
    token: int = 111,
    ltp: float = 158.0,
    bid: float | None = 157.5,
    ask: float | None = 158.0,
    depth: bool = True,
) -> dict:
    """A Kite FULL-mode tick. `depth=False` simulates QUOTE mode (no book)."""
    tick: dict = {"instrument_token": token, "last_price": ltp}
    if depth:
        tick["depth"] = {
            "buy": [{"price": bid or 0.0, "quantity": 625, "orders": 2}],
            "sell": [{"price": ask or 0.0, "quantity": 625, "orders": 1}],
        }
    return tick


def make_position(
    *,
    entry_price: float = 100.0,
    ltp: float = 100.0,
    bid: float = 0.0,
    quantity: int = 625,
    status: PositionStatus = PositionStatus.ACTIVE,
    exiting: bool = False,
) -> Position:
    pos = Position(
        pos_id="pos_20260805_001",
        instrument=make_instrument(),
        lots=1,
        quantity=quantity,
        mode=TradingMode.PAPER,
        status=status,
    )
    pos.entry.price = entry_price
    pos.entry.filled_qty = quantity
    pos.entry.at_us = 1_785_900_900_000_000
    pos.live.ltp = ltp
    pos.live.bid = bid
    pos.flags.exiting = exiting
    return pos


@pytest.fixture
def instrument() -> Instrument:
    return make_instrument()


@pytest.fixture
def armed() -> ArmedState:
    return make_armed()
