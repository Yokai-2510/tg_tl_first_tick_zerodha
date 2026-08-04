"""
Symbol classification and exchange routing.

BUILD_SPEC R12: index detection is an EXACT set membership test, never a
substring match — `"NIFTY" in "NIFTYBEES"` is True and would misroute.
"""

from __future__ import annotations

from .enums import InstrumentKind

#: Underlyings that are cash-settled indices. Everything else is a stock.
INDEX_SYMBOLS: frozenset[str] = frozenset(
    {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"}
)

#: Indices listed on BSE — their options trade in the BFO segment.
BSE_INDICES: frozenset[str] = frozenset({"SENSEX", "BANKEX"})

#: Kite REST quote keys for underlying spot values.
SPOT_KEY: dict[str, str] = {
    "NIFTY": "NSE:NIFTY 50",
    "BANKNIFTY": "NSE:NIFTY BANK",
    "FINNIFTY": "NSE:NIFTY FIN SERVICE",
    "MIDCPNIFTY": "NSE:NIFTY MID SELECT",
    "SENSEX": "BSE:SENSEX",
    "BANKEX": "BSE:BANKEX",
}

EXCHANGE_NFO = "NFO"
EXCHANGE_BFO = "BFO"
EXCHANGE_NSE = "NSE"
EXCHANGE_BSE = "BSE"


def normalise(symbol: str) -> str:
    """Canonical form: stripped and upper-cased.

    Punctuation is preserved — `M&M` and `BAJAJ-AUTO` are real Nifty 50
    symbols and must match the broker's tradingsymbol exactly.
    """
    return symbol.strip().upper()


def is_index(symbol: str) -> bool:
    """True for cash-settled index underlyings (R12: exact match only)."""
    return normalise(symbol) in INDEX_SYMBOLS


def option_exchange(symbol: str) -> str:
    """Exchange segment for this underlying's OPTION contracts."""
    return EXCHANGE_BFO if normalise(symbol) in BSE_INDICES else EXCHANGE_NFO


def spot_exchange(symbol: str) -> str:
    """Exchange segment for this underlying's SPOT/equity quote."""
    return EXCHANGE_BSE if normalise(symbol) in BSE_INDICES else EXCHANGE_NSE


def spot_quote_key(symbol: str) -> str:
    """Kite `quote()` key for the underlying.

    Indices use their published index name; stocks use `NSE:<SYMBOL>`.
    """
    sym = normalise(symbol)
    if sym in SPOT_KEY:
        return SPOT_KEY[sym]
    return f"{EXCHANGE_NSE}:{sym}"


def kind_of(symbol: str) -> InstrumentKind:
    """Underlying kind — INDEX or EQUITY. (Contract kind is a separate field.)"""
    return InstrumentKind.INDEX if is_index(symbol) else InstrumentKind.EQUITY


__all__ = [
    "INDEX_SYMBOLS", "BSE_INDICES", "SPOT_KEY",
    "EXCHANGE_NFO", "EXCHANGE_BFO", "EXCHANGE_NSE", "EXCHANGE_BSE",
    "normalise", "is_index", "option_exchange", "spot_exchange",
    "spot_quote_key", "kind_of",
]
