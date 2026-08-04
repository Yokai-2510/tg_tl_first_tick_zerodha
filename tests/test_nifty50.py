"""Nifty 50 constituent loading: parse, cache, fallback chain."""

from __future__ import annotations

import pytest

from backend.data import nifty50
from backend.data.nifty50 import Nifty50Error, load, parse_csv

HEADER = "Company Name,Industry,Symbol,Series,ISIN Code\n"


def _csv(symbols: list[str]) -> str:
    rows = [f"{s} Ltd.,Industry,{s},EQ,INE{i:09d}" for i, s in enumerate(symbols)]
    return HEADER + "\n".join(rows) + "\n"


def _fifty(prefix: str = "SYM") -> list[str]:
    return [f"{prefix}{i:02d}" for i in range(50)]


# -- parsing ---------------------------------------------------------------

def test_parses_exactly_fifty():
    symbols = _fifty()
    assert parse_csv(_csv(symbols)) == symbols


def test_preserves_punctuated_symbols():
    """M&M and BAJAJ-AUTO must survive parsing byte-for-byte."""
    symbols = _fifty()[:48] + ["M&M", "BAJAJ-AUTO"]
    parsed = parse_csv(_csv(symbols))
    assert "M&M" in parsed and "BAJAJ-AUTO" in parsed


def test_strips_bom_and_whitespace():
    body = "﻿" + _csv(_fifty()).replace("SYM00", " SYM00 ")
    assert parse_csv(body)[0] == "SYM00"


def test_uppercases_symbols():
    symbols = _fifty()
    assert parse_csv(_csv(symbols).lower().replace("symbol", "Symbol")
                     .replace("series", "Series"))[0] == "SYM00"


@pytest.mark.parametrize("count", [0, 49, 51])
def test_rejects_wrong_count(count):
    with pytest.raises(ValueError, match="expected 50"):
        parse_csv(_csv(_fifty()[:count] if count <= 50 else _fifty() + ["EXTRA"]))


def test_rejects_missing_symbol_column():
    with pytest.raises(ValueError, match="Symbol"):
        parse_csv("Company Name,Industry,Series\nA,B,EQ\n")


def test_rejects_duplicates():
    symbols = _fifty()[:49] + ["SYM00"]
    with pytest.raises(ValueError, match="duplicate"):
        parse_csv(_csv(symbols))


def test_ignores_non_eq_series():
    rows = [f"{s} Ltd.,Ind,{s},EQ,INE{i:09d}" for i, s in enumerate(_fifty())]
    rows.append("Junk Ltd.,Ind,JUNK,BE,INE999999999")
    assert len(parse_csv(HEADER + "\n".join(rows) + "\n")) == 50


# -- fallback chain --------------------------------------------------------

def test_fetch_success_writes_cache(tmp_path, monkeypatch):
    symbols = _fifty()
    monkeypatch.setattr(nifty50, "fetch", lambda timeout=15.0: symbols)
    cache = tmp_path / "nifty50.txt"

    result = load(cache, fallback_symbols=None)
    assert result.source == "fetch"
    assert list(result.symbols) == symbols
    assert cache.exists()
    assert cache.read_text(encoding="utf-8").splitlines() == symbols


def test_cache_used_when_fetch_fails(tmp_path, monkeypatch):
    symbols = _fifty()
    cache = tmp_path / "nifty50.txt"
    cache.write_text("\n".join(symbols) + "\n", encoding="utf-8")

    def boom(timeout=15.0):
        raise ConnectionError("niftyindices unreachable")
    monkeypatch.setattr(nifty50, "fetch", boom)

    result = load(cache, fallback_symbols=None)
    assert result.source == "cache"
    assert list(result.symbols) == symbols


def test_config_fallback_when_no_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(nifty50, "fetch",
                        lambda timeout=15.0: (_ for _ in ()).throw(OSError("down")))
    result = load(tmp_path / "missing.txt", fallback_symbols=_fifty())
    assert result.source == "config"
    assert len(result.symbols) == 50


def test_raises_when_everything_fails(tmp_path, monkeypatch):
    """Trading a guessed universe is worse than not trading."""
    monkeypatch.setattr(nifty50, "fetch",
                        lambda timeout=15.0: (_ for _ in ()).throw(OSError("down")))
    with pytest.raises(Nifty50Error):
        load(tmp_path / "missing.txt", fallback_symbols=None)


def test_short_config_fallback_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(nifty50, "fetch",
                        lambda timeout=15.0: (_ for _ in ()).throw(OSError("down")))
    with pytest.raises(Nifty50Error):
        load(tmp_path / "missing.txt", fallback_symbols=["ONLY", "THREE", "HERE"])


def test_corrupt_cache_is_ignored(tmp_path, monkeypatch):
    cache = tmp_path / "nifty50.txt"
    cache.write_text("ONLY\nTWO\n", encoding="utf-8")     # wrong count
    monkeypatch.setattr(nifty50, "fetch",
                        lambda timeout=15.0: (_ for _ in ()).throw(OSError("down")))
    with pytest.raises(Nifty50Error):
        load(cache, fallback_symbols=None)


def test_membership_change_is_reported(tmp_path, monkeypatch):
    """An index rebalance must be visible, not silent."""
    old = _fifty()
    new = old[:49] + ["NEWCO"]
    cache = tmp_path / "nifty50.txt"
    cache.write_text("\n".join(old) + "\n", encoding="utf-8")
    monkeypatch.setattr(nifty50, "fetch", lambda timeout=15.0: new)

    result = load(cache, fallback_symbols=None)
    assert result.changed is True
    assert result.added == ("NEWCO",)
    assert result.removed == (old[49],)


def test_no_change_reported_when_stable(tmp_path, monkeypatch):
    symbols = _fifty()
    cache = tmp_path / "nifty50.txt"
    cache.write_text("\n".join(symbols) + "\n", encoding="utf-8")
    monkeypatch.setattr(nifty50, "fetch", lambda timeout=15.0: symbols)
    assert load(cache, fallback_symbols=None).changed is False


def test_cache_write_is_atomic(tmp_path, monkeypatch):
    """No .tmp file should survive a successful write."""
    monkeypatch.setattr(nifty50, "fetch", lambda timeout=15.0: _fifty())
    cache = tmp_path / "nested" / "nifty50.txt"
    load(cache, fallback_symbols=None)
    assert cache.exists()
    assert list(cache.parent.glob("*.tmp")) == []
