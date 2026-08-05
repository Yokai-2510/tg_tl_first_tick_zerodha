"""
Upstox instrument master.

Source: a public CDN gzip, no auth required.
    https://assets.upstox.com/market-quote/instruments/exchange/{NSE,BSE}.json.gz

Two field-level traps this module exists to neutralise:

  1. `tick_size` is quoted in PAISE (5.0 means 0.05 rupees). Passing it through
     unconverted makes every rounded price 100x wrong.
  2. `expiry` is epoch MILLISECONDS, not a date.

Row shape (abridged):
    {"instrument_key": "NSE_FO|49520", "exchange_token": "49520",
     "trading_symbol": "INDIGO 26 AUG 5300 PE", "name": "INDIGO",
     "expiry": 1787011200000, "strike_price": 5300.0, "tick_size": 5.0,
     "lot_size": 625, "instrument_type": "PE", "segment": "NSE_FO",
     "underlying_symbol": "INDIGO", "asset_symbol": "INDIGO"}
"""

from __future__ import annotations

import gzip
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

from ...core.enums import InstrumentKind, OptionType, SubscribeMode
from ...core.models import Instrument
from ...core.symbols import is_index, normalise
from ..base import surrogate_token

CDN = "https://assets.upstox.com/market-quote/instruments/exchange/{exchange}.json.gz"

#: Upstox segment -> our exchange label.
SEGMENT_TO_EXCHANGE = {
    "NSE_FO": "NFO", "BSE_FO": "BFO",
    "NSE_EQ": "NSE", "BSE_EQ": "BSE",
    "NSE_INDEX": "NSE", "BSE_INDEX": "BSE",
}

#: Our exchange label -> the CDN file that contains it.
EXCHANGE_TO_CDN = {"NFO": "NSE", "NSE": "NSE", "BFO": "BSE", "BSE": "BSE"}

_OPT_SEGMENTS = {"NSE_FO", "BSE_FO"}


def paise_to_rupees(tick_size: Any) -> float:
    """Upstox quotes tick_size in paise; the engine works in rupees.

    Values already below 1 are assumed to be rupees (defensive, in case
    Upstox ever changes the unit).
    """
    try:
        value = float(tick_size or 0.0)
    except (TypeError, ValueError):
        return 0.05
    if value <= 0:
        return 0.05
    return round(value / 100.0, 4) if value >= 1 else round(value, 4)


def epoch_ms_to_date(value: Any) -> date | None:
    """Upstox expiry is epoch milliseconds (UTC). Returns None when absent."""
    if value in (None, "", 0):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    # Contracts expire at 15:30 IST; the UTC date of the epoch is the expiry date.
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).date()


def download_master(exchange: str, cache_dir: Path, *, timeout: int = 60,
                    refresh: bool = True) -> list[dict]:
    """Download (or load from cache) one exchange master."""
    cdn_name = EXCHANGE_TO_CDN.get(exchange.upper(), "NSE")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"upstox_{cdn_name}.json"

    if refresh:
        try:
            resp = requests.get(CDN.format(exchange=cdn_name), timeout=timeout)
            resp.raise_for_status()
            rows = json.loads(gzip.decompress(resp.content).decode("utf-8"))
            cached.write_text(json.dumps(rows), encoding="utf-8")
            return rows
        except Exception:
            pass                                  # fall through to cache
    try:
        return json.loads(cached.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def to_instrument(row: dict, *, wave: int = 2,
                  mode: SubscribeMode = SubscribeMode.FULL) -> Instrument | None:
    """Convert one master row into a canonical Instrument."""
    key = str(row.get("instrument_key") or "")
    if not key:
        return None

    segment = str(row.get("segment") or "")
    exchange = SEGMENT_TO_EXCHANGE.get(segment, "NFO")
    itype = str(row.get("instrument_type") or "").upper()
    underlying = normalise(str(
        row.get("underlying_symbol") or row.get("asset_symbol")
        or row.get("name") or row.get("trading_symbol") or ""
    ))

    if itype in (OptionType.CE, OptionType.PE):
        kind = InstrumentKind.OPTION
    elif itype in ("FUT", "FUTIDX", "FUTSTK"):
        kind = InstrumentKind.FUTURE
    elif "INDEX" in segment:
        kind = InstrumentKind.INDEX
    else:
        kind = InstrumentKind.EQUITY

    return Instrument(
        token=surrogate_token(key),
        tradingsymbol=str(row.get("trading_symbol")
                          or row.get("tradingsymbol") or key),
        exchange=exchange,
        underlying=underlying,
        kind=kind,
        lot_size=int(row.get("lot_size") or 1) or 1,
        tick_size=paise_to_rupees(row.get("tick_size")),
        instrument_type=itype or None,
        strike=float(row.get("strike_price") or 0.0),
        expiry=epoch_ms_to_date(row.get("expiry")),
        is_index=is_index(underlying) or kind is InstrumentKind.INDEX,
        subscribe_mode=mode,
        wave=wave,
        data_key=key,
    )


def option_rows(master: Iterable[dict], underlying: str) -> list[dict]:
    name = normalise(underlying)
    out = []
    for row in master:
        if str(row.get("segment")) not in _OPT_SEGMENTS:
            continue
        sym = normalise(str(row.get("underlying_symbol")
                            or row.get("asset_symbol") or row.get("name") or ""))
        if sym == name:
            out.append(row)
    return out


def expiries_for(master: Iterable[dict], underlying: str) -> list[date]:
    out = {epoch_ms_to_date(r.get("expiry")) for r in option_rows(master, underlying)}
    return sorted(d for d in out if d is not None)


def strikes_for(master: Iterable[dict], underlying: str, expiry: date) -> list[float]:
    return sorted({
        float(r["strike_price"])
        for r in option_rows(master, underlying)
        if epoch_ms_to_date(r.get("expiry")) == expiry
        and float(r.get("strike_price") or 0) > 0
    })


def build_chain(master: Iterable[dict], underlying: str, expiry: date,
                spot: float, per_side: int, *,
                mode: SubscribeMode = SubscribeMode.FULL) -> list[Instrument]:
    """CE+PE instruments for the strike band around ATM."""
    from ..kite.instruments import strike_band          # pure, broker-agnostic

    rows = [r for r in option_rows(master, underlying)
            if epoch_ms_to_date(r.get("expiry")) == expiry]
    if not rows:
        raise ValueError(f"no Upstox option rows for {underlying} {expiry}")

    strikes = sorted({float(r["strike_price"]) for r in rows
                      if float(r.get("strike_price") or 0) > 0})
    if not strikes:
        raise ValueError(f"no strikes for {underlying} {expiry}")

    wanted = set(strike_band(strikes, spot, per_side))
    chain: list[Instrument] = []
    for row in rows:
        if float(row.get("strike_price") or 0) not in wanted:
            continue
        if str(row.get("instrument_type") or "").upper() not in (OptionType.CE,
                                                                 OptionType.PE):
            continue
        inst = to_instrument(row, wave=2, mode=mode)
        if inst is not None:
            chain.append(inst)
    chain.sort(key=lambda i: (i.strike, i.instrument_type or ""))
    return chain


def equity_instrument(master: Iterable[dict], symbol: str) -> Instrument | None:
    name = normalise(symbol)
    for row in master:
        if str(row.get("segment")) != "NSE_EQ":
            continue
        if normalise(str(row.get("trading_symbol") or "")) == name:
            return to_instrument(row, wave=1, mode=SubscribeMode.QUOTE)
    return None


#: Upstox index instrument keys (indices are not in the equity segment).
INDEX_KEYS = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
    "MIDCPNIFTY": "NSE_INDEX|NIFTY MID SELECT",
    "SENSEX": "BSE_INDEX|SENSEX",
    "BANKEX": "BSE_INDEX|BANKEX",
}


def index_instrument(symbol: str) -> Instrument | None:
    """Index spot instrument. Keys are well-known, so no master lookup needed."""
    name = normalise(symbol)
    key = INDEX_KEYS.get(name)
    if not key:
        return None
    return Instrument(
        token=surrogate_token(key),
        tradingsymbol=name,
        exchange="BSE" if key.startswith("BSE") else "NSE",
        underlying=name,
        kind=InstrumentKind.INDEX,
        is_index=True,
        subscribe_mode=SubscribeMode.QUOTE,
        wave=1,
        data_key=key,
    )


__all__ = [
    "CDN", "SEGMENT_TO_EXCHANGE", "INDEX_KEYS",
    "paise_to_rupees", "epoch_ms_to_date", "download_master", "to_instrument",
    "option_rows", "expiries_for", "strikes_for", "build_chain",
    "equity_instrument", "index_instrument",
]
