"""
Shared data structures. One definition per concept — no parallel shapes.

Mutability is deliberate and documented per type:
  * frozen  — immutable facts (Instrument, Signal, OrderResult, TickView)
  * mutable — live state owned by exactly ONE thread (ArmedState, Position)

Ownership (BUILD_SPEC P4 / R1):
  ArmedState  -> websocket callback thread only
  Position    -> position-monitor thread only
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .enums import (
    ExitTrigger, InstrumentKind, OptionType, OrderRole, OrderStatus,
    PositionStatus, RejectionKind, Side, SubscribeMode, TradingMode,
)

# --------------------------------------------------------------------------
# Instruments
# --------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Instrument:
    """One tradeable or observable contract.

    `token` is the ENGINE's primary key and is derived from the DATA broker,
    because that is what arrives on every tick. `data_key` and `trade_key` hold
    each broker's native identifier, which differ in both format and value:

        Kite    instrument_token  1234567          -> "1234567"
        (other brokers use string keys such as "NSE_FO|49520")

    When data and trading use different brokers, `trade_key` is resolved by
    matching the exchange-level contract identity (underlying, expiry, strike,
    option type) rather than the tradingsymbol, whose format is broker-specific.
    """

    token: int
    tradingsymbol: str
    exchange: str
    underlying: str
    kind: InstrumentKind
    lot_size: int = 1
    tick_size: float = 0.05                  # ALWAYS rupees
    instrument_type: str | None = None       # CE | PE | EQ | FUT | None
    strike: float = 0.0
    expiry: date | None = None
    is_index: bool = False
    subscribe_mode: SubscribeMode = SubscribeMode.QUOTE
    wave: int = 1                            # 1 = pre-open, 2 = post-settlement
    data_key: str = ""                       # native id at the data broker
    trade_key: str = ""                      # native id at the trade broker

    @property
    def is_option(self) -> bool:
        return self.kind is InstrumentKind.OPTION

    @property
    def quote_key(self) -> str:
        """Kite REST quote key, e.g. 'NFO:INDIGO26AUG5300PE'."""
        return f"{self.exchange}:{self.tradingsymbol}"

    @property
    def contract_id(self) -> tuple:
        """Broker-independent identity, for cross-broker matching."""
        return (self.underlying.upper(), self.expiry, round(self.strike, 2),
                (self.instrument_type or "").upper())


# --------------------------------------------------------------------------
# Live market view
# --------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TickView:
    """Immutable snapshot of the latest tick for one instrument.

    Published by the feed thread; read by everyone else. Replaced wholesale
    rather than mutated, so readers never see a half-updated view.
    """

    token: int
    ltp: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    volume: int = 0
    oi: int = 0
    exchange_ts_us: int | None = None
    recv_us: int = 0
    recv_ns: int = 0

    @property
    def has_depth(self) -> bool:
        return self.bid > 0.0 and self.ask > 0.0

    @property
    def feed_lag_us(self) -> int | None:
        """Broker/exchange dissemination delay. None when unknown.

        May legitimately be negative if clocks are skewed — that is a clock
        problem to surface, not a value to clamp.
        """
        if self.exchange_ts_us is None or self.recv_us <= 0:
            return None
        return self.recv_us - self.exchange_ts_us


# --------------------------------------------------------------------------
# Entry arming + signal
# --------------------------------------------------------------------------

@dataclass(slots=True)
class ArmedState:
    """Per-instrument entry state. MUTABLE, owned by the websocket thread.

    `fired` is a one-way latch (BUILD_SPEC R7): set exactly once, before the
    signal leaves the callback, so a duplicate tick in the same batch cannot
    produce a second entry.
    """

    instrument: Instrument
    #: Previous close. NOT part of the entry decision any more -- the trigger is
    #: tick-over-tick. Kept because the strength engine normalises against it and
    #: the console displays it.
    ref_price: float
    lots: int
    #: Last LTP seen for this strike. The entry trigger is `ltp > prev_ltp`, so it
    #: lives here rather than in a side dict: ArmedState is already the one lookup
    #: the hot path does per tick, which makes the comparison free.
    #: 0.0 means "no tick yet" -- the first tick only seeds the baseline.
    prev_ltp: float = 0.0
    fired: bool = False

    @property
    def token(self) -> int:
        return self.instrument.token

    @property
    def quantity(self) -> int:
        return self.lots * self.instrument.lot_size


@dataclass(frozen=True, slots=True)
class Signal:
    """An entry decision. Immutable once created."""

    sig_id: str
    token: int
    tradingsymbol: str
    underlying: str
    option_type: str
    strike: float
    ref_price: float
    tick_price: float
    diff: float
    best_bid: float
    best_ask: float
    lots: int
    quantity: int
    tick_size: float
    exchange: str
    is_index: bool
    t_tick_ns: int
    t_signal_ns: int
    reason: str = "FIRST_POSITIVE_DIFF"


# --------------------------------------------------------------------------
# Orders
# --------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class OrderResult:
    """Outcome of one broker order call.

    Expected broker conditions are returned, not raised — only genuinely
    unexpected failures raise.
    """

    success: bool
    order_id: str | None = None
    error: str | None = None
    rejection_kind: RejectionKind | None = None
    lpp_limit: float | None = None
    status: OrderStatus | None = None
    filled_quantity: int = 0
    average_price: float = 0.0
    t_req_ns: int = 0
    t_ack_ns: int = 0
    raw: dict | None = None

    @property
    def ack_ms(self) -> float:
        if self.t_req_ns <= 0 or self.t_ack_ns <= 0:
            return -1.0
        return (self.t_ack_ns - self.t_req_ns) / 1e6


@dataclass(slots=True)
class OrderRecord:
    """Full audit trail for one order attempt. Appended to orders/*.jsonl."""

    client_tag: str
    role: OrderRole
    side: Side
    token: int
    tradingsymbol: str
    exchange: str
    order_type: str
    product: str
    validity: str
    quantity: int
    price: float
    attempt: int
    order_id: str | None = None
    sig_id: str | None = None
    pos_id: str | None = None
    status: OrderStatus | None = None
    filled_quantity: int = 0
    average_price: float = 0.0
    status_message: str | None = None
    rejection_kind: RejectionKind | None = None
    price_basis: dict = field(default_factory=dict)
    postbacks: list[dict] = field(default_factory=list)
    t_req_ns: int = 0
    t_ack_ns: int = 0
    t_fill_ns: int = 0


# --------------------------------------------------------------------------
# Positions
# --------------------------------------------------------------------------

@dataclass(slots=True)
class PositionEntry:
    order_id: str | None = None
    price: float = 0.0
    filled_qty: int = 0
    at_us: int = 0
    ref_price: float = 0.0
    diff: float = 0.0


@dataclass(slots=True)
class PositionExit:
    order_id: str | None = None
    price: float = 0.0
    filled_qty: int = 0
    at_us: int = 0
    trigger: ExitTrigger | None = None


@dataclass(slots=True)
class PositionLive:
    ltp: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    max_pnl_pct: float = 0.0
    min_pnl_pct: float = 0.0
    holding_seconds: int = 0


@dataclass(slots=True)
class PositionTrailing:
    """Trailing levels. Both ratchet upward only — never reset down."""

    sl_active: bool = False
    sl_peak: float = 0.0
    sl_level: float = 0.0
    tgt_active: bool = False
    tgt_peak: float = 0.0
    tgt_level: float = 0.0


@dataclass(slots=True)
class PositionFlags:
    #: One-way latch preventing duplicate exit orders (BUILD_SPEC R8).
    exiting: bool = False
    broker_confirmed: bool = False
    reconciled: bool = False


@dataclass(slots=True)
class Position:
    """A live or closed position. MUTABLE, owned by the monitor thread."""

    pos_id: str
    instrument: Instrument
    lots: int
    quantity: int
    mode: TradingMode
    status: PositionStatus = PositionStatus.PENDING
    sig_id: str | None = None
    entry: PositionEntry = field(default_factory=PositionEntry)
    exit: PositionExit = field(default_factory=PositionExit)
    live: PositionLive = field(default_factory=PositionLive)
    trailing: PositionTrailing = field(default_factory=PositionTrailing)
    flags: PositionFlags = field(default_factory=PositionFlags)
    charges: float = 0.0

    @property
    def token(self) -> int:
        return self.instrument.token

    @property
    def tradingsymbol(self) -> str:
        return self.instrument.tradingsymbol

    @property
    def is_open(self) -> bool:
        return self.status in (PositionStatus.ACTIVE, PositionStatus.EXITING)


__all__ = [
    "Instrument", "TickView", "ArmedState", "Signal",
    "OrderResult", "OrderRecord",
    "PositionEntry", "PositionExit", "PositionLive", "PositionTrailing",
    "PositionFlags", "Position",
]
