"""
Batched REST market data: quote / LTP / OHLC.

Kite allows 1 quote request per second, so these are BATCHED and never called
from the hot path — the websocket is the live source. REST quotes are used
for pre-open snapshots and reconciliation only.
"""

from __future__ import annotations

from typing import Any, Iterable

#: Kite caps instruments per quote() call.
MAX_KEYS_PER_CALL = 500


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def quote(kite: Any, keys: list[str], *, limiter=None) -> dict[str, dict]:
    """Full quotes (depth + OHLC) for `keys`, batched and rate-limited."""
    out: dict[str, dict] = {}
    for batch in _chunks(list(dict.fromkeys(keys)), MAX_KEYS_PER_CALL):
        if limiter is not None:
            limiter.acquire("quote", timeout=5.0)
        try:
            out.update(kite.quote(batch))
        except Exception:
            continue                     # partial data beats no data for snapshots
    return out


def ltp(kite: Any, keys: list[str], *, limiter=None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for batch in _chunks(list(dict.fromkeys(keys)), MAX_KEYS_PER_CALL):
        if limiter is not None:
            limiter.acquire("quote", timeout=5.0)
        try:
            out.update(kite.ltp(batch))
        except Exception:
            continue
    return out


def ohlc(kite: Any, keys: list[str], *, limiter=None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for batch in _chunks(list(dict.fromkeys(keys)), MAX_KEYS_PER_CALL):
        if limiter is not None:
            limiter.acquire("quote", timeout=5.0)
        try:
            out.update(kite.ohlc(batch))
        except Exception:
            continue
    return out


def snapshot_from_quotes(
    quotes: dict[str, dict], key_to_symbol: dict[str, str]
) -> dict[str, dict]:
    """Reduce raw quotes to the ranking shape: {symbol: {ltp, prev_close, ...}}.

    `prev_close` comes from `ohlc.close`, which is the PREVIOUS day's close —
    the correct ranking basis before the open.
    """
    out: dict[str, dict] = {}
    for key, data in quotes.items():
        symbol = key_to_symbol.get(key)
        if not symbol:
            continue
        o = data.get("ohlc") or {}
        out[symbol] = {
            "ltp": float(data.get("last_price") or 0.0),
            "prev_close": float(o.get("close") or 0.0),
            "open": float(o.get("open") or 0.0),
            "high": float(o.get("high") or 0.0),
            "low": float(o.get("low") or 0.0),
            "volume": int(data.get("volume") or data.get("volume_traded") or 0),
            "token": data.get("instrument_token"),
        }
    return out


def best_bid_ask(quote_row: dict) -> tuple[float, float]:
    """First depth level from a REST quote row. (0.0, 0.0) when absent."""
    depth = (quote_row or {}).get("depth") or {}
    buy, sell = depth.get("buy") or (), depth.get("sell") or ()
    return (float(buy[0].get("price") or 0.0) if buy else 0.0,
            float(sell[0].get("price") or 0.0) if sell else 0.0)


__all__ = ["quote", "ltp", "ohlc", "snapshot_from_quotes", "best_bid_ask",
           "MAX_KEYS_PER_CALL"]
