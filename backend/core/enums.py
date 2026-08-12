"""
Canonical enums. Single source of truth — no string literals for these concepts
anywhere else in the codebase.

All are `str` enums so they serialise to JSON as plain strings and compare
equal to their string value (`Phase.TRADING == "TRADING"` is True).

Spec: docs/02_SYSTEM_DESIGN_AND_INTERFACES.md Appendix A.
"""

from __future__ import annotations

from enum import StrEnum


class Phase(StrEnum):
    """Daily lifecycle state. Transitions are validated (see engine/scheduler.py)."""

    BOOT = "BOOT"
    PHASE_1 = "PHASE_1"
    PHASE_1_FAIL = "PHASE_1_FAIL"
    FEED_LIVE = "FEED_LIVE"
    PREOPEN = "PREOPEN"
    SETTLEMENT = "SETTLEMENT"
    ARMING = "ARMING"
    FROZEN = "FROZEN"
    TRADING = "TRADING"
    MANAGING = "MANAGING"
    EOD = "EOD"
    IDLE = "IDLE"


#: The only phase in which new entry signals may fire.
ENTRY_PHASES: frozenset[Phase] = frozenset({Phase.TRADING})

#: Phases in which the exit engine must be running.
EXIT_PHASES: frozenset[Phase] = frozenset(
    {Phase.TRADING, Phase.MANAGING, Phase.EOD}
)


class PositionStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    EXITING = "EXITING"
    CLOSED = "CLOSED"
    FAILED = "FAILED"
    #: Present at the broker but not opened by us (found during reconciliation).
    #: Managed and exitable, never counted as a strategy entry.
    ADOPTED_UNMANAGED = "ADOPTED_UNMANAGED"


class OrderStatus(StrEnum):
    """Kite order statuses. Interim values are NOT terminal (BUILD_SPEC R13)."""

    OPEN = "OPEN"
    COMPLETE = "COMPLETE"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    # --- interim, seen before a terminal status ---
    PUT_ORDER_REQ_RECEIVED = "PUT ORDER REQ RECEIVED"
    VALIDATION_PENDING = "VALIDATION PENDING"
    OPEN_PENDING = "OPEN PENDING"
    TRIGGER_PENDING = "TRIGGER PENDING"
    CANCEL_PENDING = "CANCEL PENDING"
    MODIFY_VALIDATION_PENDING = "MODIFY VALIDATION PENDING"
    MODIFY_PENDING = "MODIFY PENDING"


#: Only these end an order's lifecycle. Everything else means "keep waiting".
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {OrderStatus.COMPLETE, OrderStatus.REJECTED, OrderStatus.CANCELLED}
)


def is_terminal(status: str | None) -> bool:
    """True if an order status ends the lifecycle. Unknown/None => not terminal."""
    return status is not None and status.upper() in TERMINAL_STATUSES


class ExitTrigger(StrEnum):
    """Evaluated in this declaration order; first hit wins."""

    MANUAL_BROKER = "MANUAL_BROKER"
    MANUAL_API = "MANUAL_API"
    STOP_LOSS = "STOP_LOSS"
    TARGET = "TARGET"
    TRAILING_TARGET = "TRAILING_TARGET"
    TRAILING_SL = "TRAILING_SL"
    TIME_EXIT = "TIME_EXIT"
    EOD_SQUAREOFF = "EOD_SQUAREOFF"


class RejectionKind(StrEnum):
    LPP = "LPP"
    MARGIN = "MARGIN"
    ORDER_TYPE = "ORDER_TYPE"
    RMS = "RMS"
    RATE_LIMIT = "RATE_LIMIT"
    NETWORK = "NETWORK"
    AUTH = "AUTH"
    OTHER = "OTHER"


#: Rejections worth retrying. Everything else is terminal for that signal.
RETRYABLE_REJECTIONS: frozenset[RejectionKind] = frozenset(
    {
        RejectionKind.LPP,
        RejectionKind.ORDER_TYPE,
        RejectionKind.RATE_LIMIT,
        RejectionKind.NETWORK,
    }
)


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderRole(StrEnum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"


class OrderType(StrEnum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class Validity(StrEnum):
    DAY = "DAY"
    IOC = "IOC"


class Product(StrEnum):
    MIS = "MIS"
    NRML = "NRML"
    CNC = "CNC"


class FallbackTo(StrEnum):
    MARKETABLE_LIMIT = "MARKETABLE_LIMIT"
    MARKET = "MARKET"
    NONE = "NONE"


class PriceSource(StrEnum):
    ASK = "ask"
    BID = "bid"
    LTP = "ltp"


class PnlBasis(StrEnum):
    LTP = "ltp"
    BID = "bid"


class AtmSource(StrEnum):
    SETTLEMENT = "settlement"
    PREV_CLOSE = "prev_close"
    FUTURES_PREOPEN = "futures_preopen"


class RankingBasis(StrEnum):
    SETTLEMENT = "settlement"
    PREV_CLOSE = "prev_close"
    PREOPEN = "preopen"


class Moneyness(StrEnum):
    ATM = "ATM"
    ITM = "ITM"
    OTM = "OTM"


class OptionType(StrEnum):
    CE = "CE"
    PE = "PE"


class InstrumentKind(StrEnum):
    OPTION = "OPTION"
    EQUITY = "EQUITY"
    FUTURE = "FUTURE"
    INDEX = "INDEX"


class SubscribeMode(StrEnum):
    """Kite websocket modes. Depth (bid/ask) is available in FULL only."""

    LTP = "ltp"
    QUOTE = "quote"
    FULL = "full"


class TradingMode(StrEnum):
    PAPER = "paper"
    LIVE = "live"


class RecordKind(StrEnum):
    """Discriminator for lines in the recorder's NDJSON stream."""

    TICK = "TICK"
    PHASE = "PHASE"
    SUBSCRIBED = "SUBSCRIBED"
    FEED_GAP = "FEED_GAP"
    SNAPSHOT = "SNAPSHOT"
    ARMED = "ARMED"
    SIGNAL = "SIGNAL"
    ORDER = "ORDER"
    POSITION = "POSITION"


class DiskFullPolicy(StrEnum):
    STOP_RECORDING = "stop_recording"
    HALT_TRADING = "halt_trading"


class FillModel(StrEnum):
    TOUCH = "touch"
    MIDPOINT = "midpoint"



class LogLevel(StrEnum):
    """Standard levels only, so the console can offer a closed list."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class DataBroker(StrEnum):
    """Who supplies the feed, instrument master and quotes."""

    ZERODHA = "zerodha"
    UPSTOX = "upstox"


class TradeBroker(StrEnum):
    """Who receives orders. `paper` touches no broker order API at all."""

    ZERODHA = "zerodha"
    UPSTOX = "upstox"
    PAPER = "paper"


class RollScope(StrEnum):
    """Which instruments roll to the next expiry near physical settlement."""

    STOCKS_ONLY = "stocks_only"
    ALL = "all"
    NONE = "none"


class SnapshotSource(StrEnum):
    """Which price a snapshot records as its reference."""

    PREV_CLOSE = "prev_close"
    LAST = "last"
    OPEN = "open"


class RecorderFormat(StrEnum):
    NDJSON = "ndjson"


class Compression(StrEnum):
    NONE = "none"
    ZSTD = "zstd"


class UploadAfter(StrEnum):
    EOD = "eod"
    SESSION = "session"
    NEVER = "never"

__all__ = [
    "Phase", "ENTRY_PHASES", "EXIT_PHASES",
    "PositionStatus", "OrderStatus", "TERMINAL_STATUSES", "is_terminal",
    "ExitTrigger", "RejectionKind", "RETRYABLE_REJECTIONS",
    "Side", "OrderRole", "OrderType", "Validity", "Product", "FallbackTo",
    "PriceSource", "PnlBasis", "AtmSource", "RankingBasis", "Moneyness",
    "OptionType", "InstrumentKind", "SubscribeMode", "TradingMode",
    "RecordKind", "DiskFullPolicy", "FillModel",
    "LogLevel", "DataBroker", "TradeBroker", "RollScope", "SnapshotSource",
    "RecorderFormat", "Compression", "UploadAfter",
]
