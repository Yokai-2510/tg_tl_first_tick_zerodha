"""
Nifty 50 constituent list.

There is no broker API for index membership — Kite's instrument master has
every NSE instrument but no "is in Nifty 50" flag. So the list comes from NSE.

Resolution order (first success wins):
  1. Fresh CSV from niftyindices.com  -> validated, then cached to disk
  2. Cached CSV from the last success -> used when today's fetch fails
  3. `fallback_symbols` from config   -> last resort, empty by default

If all three fail we raise. Trading a guessed universe is worse than not
trading: Phase 1 fails loudly instead.

CSV shape (verified 2026-08-05):
    Company Name,Industry,Symbol,Series,ISIN Code
    Adani Enterprises Ltd.,Metals & Mining,ADANIENT,EQ,INE423A01024

Symbols keep their punctuation exactly: `M&M`, `BAJAJ-AUTO` are real members
and must match the broker's tradingsymbol character-for-character.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

import requests

from ..core.symbols import normalise

CSV_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv"

#: niftyindices.com rejects non-browser agents.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,application/csv,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

EXPECTED_COUNT = 50
_SYMBOL_COL = "Symbol"
_SERIES_COL = "Series"


class Nifty50Error(RuntimeError):
    """Raised when no usable constituent list could be obtained."""


@dataclass(frozen=True, slots=True)
class Nifty50Result:
    symbols: tuple[str, ...]
    source: str                  # "fetch" | "cache" | "config"
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed)


def parse_csv(text: str) -> list[str]:
    """Parse the constituents CSV into a symbol list.

    Raises:
        ValueError: malformed CSV, missing column, or wrong symbol count.
    """
    # utf-8-sig equivalent: strip a BOM if the caller passed raw bytes decoded as utf-8.
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if reader.fieldnames is None or _SYMBOL_COL not in reader.fieldnames:
        raise ValueError(
            f"CSV missing {_SYMBOL_COL!r} column; got {reader.fieldnames!r}"
        )

    symbols: list[str] = []
    for row in reader:
        raw = (row.get(_SYMBOL_COL) or "").strip()
        if not raw:
            continue
        series = (row.get(_SERIES_COL) or "EQ").strip().upper()
        if series and series != "EQ":
            continue                      # ignore non-equity series defensively
        symbols.append(normalise(raw))

    if len(symbols) != EXPECTED_COUNT:
        raise ValueError(
            f"expected {EXPECTED_COUNT} symbols, parsed {len(symbols)}"
        )
    if len(set(symbols)) != len(symbols):
        dupes = sorted({s for s in symbols if symbols.count(s) > 1})
        raise ValueError(f"duplicate symbols in CSV: {dupes}")
    return symbols


def fetch(timeout: float = 15.0) -> list[str]:
    """Download and validate the live constituent list. Raises on any problem."""
    resp = requests.get(CSV_URL, headers=_HEADERS, timeout=timeout)
    resp.raise_for_status()
    return parse_csv(resp.content.decode("utf-8-sig", errors="replace"))


def load(
    cache_path: Path,
    *,
    fallback_symbols: list[str] | None = None,
    timeout: float = 15.0,
    log=None,
) -> Nifty50Result:
    """Resolve the constituent list: fetch -> cache -> config fallback.

    A successful fetch overwrites the cache and reports the diff against the
    previous list, so an index rebalance is visible rather than silent.
    """
    def _say(level: str, msg: str) -> None:
        if log is not None:
            getattr(log, level, log.info)(msg)

    previous = _read_cache(cache_path)

    try:
        symbols = fetch(timeout=timeout)
    except Exception as exc:                      # network, HTTP, or validation
        _say("warning", f"Nifty 50 fetch failed ({exc.__class__.__name__}: {exc})")
    else:
        _write_cache(cache_path, symbols)
        added = tuple(sorted(set(symbols) - set(previous))) if previous else ()
        removed = tuple(sorted(set(previous) - set(symbols))) if previous else ()
        if added or removed:
            _say("warning",
                 f"Nifty 50 membership changed: +{list(added)} -{list(removed)}")
        _say("info", f"Nifty 50 loaded from NSE ({len(symbols)} symbols)")
        return Nifty50Result(tuple(symbols), "fetch", added, removed)

    if previous:
        _say("warning",
             f"Using cached Nifty 50 list ({len(previous)} symbols) from {cache_path}")
        return Nifty50Result(tuple(previous), "cache")

    if fallback_symbols:
        cleaned = [normalise(s) for s in fallback_symbols if s.strip()]
        if len(cleaned) == EXPECTED_COUNT:
            _say("warning", "Using config fallback_symbols for Nifty 50")
            return Nifty50Result(tuple(cleaned), "config")
        _say("error",
             f"config fallback_symbols has {len(cleaned)}, expected {EXPECTED_COUNT}")

    raise Nifty50Error(
        "Could not obtain the Nifty 50 constituent list: fetch failed, no cache "
        f"at {cache_path}, and no usable config fallback."
    )


# --------------------------------------------------------------------------
# Cache I/O — plain newline-delimited symbols, easy to inspect and edit
# --------------------------------------------------------------------------

def _read_cache(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    symbols = [normalise(ln) for ln in lines if ln.strip() and not ln.startswith("#")]
    return symbols if len(symbols) == EXPECTED_COUNT else []


def _write_cache(path: Path, symbols: list[str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("\n".join(symbols) + "\n", encoding="utf-8")
        tmp.replace(path)                     # atomic: never a half-written cache
    except OSError:
        pass                                  # cache is an optimisation, not critical


__all__ = ["CSV_URL", "EXPECTED_COUNT", "Nifty50Error", "Nifty50Result",
           "parse_csv", "fetch", "load"]
