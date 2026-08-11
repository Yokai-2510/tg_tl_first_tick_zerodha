"""
Positions, holdings and margins.

Kite has NO position stream — the websocket delivers order updates only. Live
positions are therefore derived from fills and RECONCILED against this REST
view (BUILD_SPEC §14).
"""

from __future__ import annotations

from typing import Any


def positions(kite: Any, *, limiter=None) -> dict[str, list[dict]]:
    """`{"day": [...], "net": [...]}`; empty on failure rather than raising."""
    if limiter is not None:
        limiter.acquire("other", timeout=2.0)
    try:
        data = kite.positions()
    except Exception:
        return {"day": [], "net": []}
    return {"day": list(data.get("day") or []), "net": list(data.get("net") or [])}


def orders(kite: Any, *, limiter=None) -> list[dict]:
    if limiter is not None:
        limiter.acquire("other", timeout=2.0)
    try:
        return list(kite.orders() or [])
    except Exception:
        return []


def margins(kite: Any, *, limiter=None) -> dict:
    if limiter is not None:
        limiter.acquire("other", timeout=2.0)
    try:
        return kite.margins() or {}
    except Exception:
        return {}


def available_cash(margins_data: dict) -> float:
    """Live equity balance available to trade."""
    eq = (margins_data or {}).get("equity") or {}
    avail = eq.get("available") or {}
    for key in ("live_balance", "cash", "opening_balance"):
        value = avail.get(key)
        if value is not None:
            return float(value)
    return float(eq.get("net") or 0.0)


def capital(margins_data: dict) -> dict:
    """Flatten Kite's equity margin block into a capital view.

    Kite splits the account into `available` (what you can still deploy) and
    `utilised` (what is committed). `debits` is margin blocked by open positions
    and pending orders; `total` is reconstructed rather than read, because Kite
    reports no single "account value" field.
    """
    eq = (margins_data or {}).get("equity") or {}
    avail = eq.get("available") or {}
    used = eq.get("utilised") or {}

    def f(d: dict, key: str) -> float:
        try:
            return float(d.get(key) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    live = available_cash(margins_data)
    debits = f(used, "debits")
    exposure = f(used, "exposure")
    span = f(used, "span")
    option_premium = f(used, "option_premium")
    blocked = max(debits, exposure + span + option_premium)
    total = live + blocked

    return {
        "available": round(live, 2),
        "used": round(blocked, 2),
        "total": round(total, 2),
        "deployed_pct": round((blocked / total) * 100, 2) if total > 0 else 0.0,
        "opening_balance": round(f(avail, "opening_balance"), 2),
        "payin": round(f(avail, "intraday_payin"), 2),
        "net": round(f(eq, "net"), 2),
        "breakdown": {
            "debits": round(debits, 2),
            "span": round(span, 2),
            "exposure": round(exposure, 2),
            "option_premium": round(option_premium, 2),
        },
    }


def day_position_map(positions_data: dict) -> dict[str, dict]:
    """`{tradingsymbol: row}` for today's positions."""
    return {str(p.get("tradingsymbol")): p for p in positions_data.get("day", [])}


def net_quantity(row: dict) -> int:
    """Signed net quantity for a position row (0 means flat)."""
    return int(row.get("quantity") or 0)


__all__ = ["positions", "orders", "margins", "available_cash", "capital",
           "day_position_map", "net_quantity"]
