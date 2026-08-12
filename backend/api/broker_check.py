"""
Credential inspection and a real broker connection test.

Two jobs the console could not do before:

1. Show WHICH credentials the server is holding, without ever returning a secret.
   Values are masked to a shape you can recognise -- enough to tell "the right key
   with a typo" from "the wrong key entirely" -- and never reversible.

2. Actually exercise the broker. A green tick that only proves a file exists is
   worse than nothing, so the test authenticates for real, then calls profile and
   margins, and reports each step separately. That also means the console can show
   true account capital before 08:45, which is otherwise impossible: the broker
   session is only created in phase 1.

Every check returns a row rather than raising, so one failure still reports the
others -- knowing auth passed but margins failed is a different problem from
nothing working at all.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

#: Which credential keys matter, per broker, and whether each is required.
EXPECTED = {
    "zerodha": [
        ("api_key", True), ("api_secret", True), ("user_id", True),
        ("password", True), ("totp_key", True),
    ],
    "upstox": [
        ("api_key", True), ("api_secret", True), ("redirect_uri", True),
        ("mobile_no", False), ("totp_key", False), ("pin", False),
        ("access_token", False), ("auth_code", False),
    ],
}


def mask(value: str) -> str:
    """Show enough to identify a value, never enough to use it.

    Short secrets reveal nothing at all -- with a 6-digit PIN, even the first and
    last character would narrow it far too much.
    """
    v = str(value or "")
    if not v:
        return ""
    if len(v) <= 8:
        return "•" * len(v)
    return f"{v[:3]}{'•' * 6}{v[-2:]} ({len(v)} chars)"


def credential_view(creds: dict, brokers: list[str]) -> list[dict]:
    """A per-broker, per-field summary safe to send over the API."""
    out: list[dict] = []
    for broker in brokers:
        section = (creds or {}).get(broker) or {}
        fields = []
        for key, required in EXPECTED.get(broker, []):
            raw = str(section.get(key) or "")
            fields.append({
                "key": key,
                "required": required,
                "present": bool(raw),
                "masked": mask(raw),
                "length": len(raw),
            })
        missing = [f["key"] for f in fields if f["required"] and not f["present"]]
        out.append({
            "broker": broker,
            "fields": fields,
            "complete": not missing,
            "missing": missing,
        })
    return out


def token_cache_state(data_dir: str | Path) -> dict:
    """Whether a cached access token exists and whether it is still usable today.

    Kite tokens die at 06:00 IST, so a cache file being present says nothing on its
    own -- freshness is the part that matters.
    """
    from ..brokers.kite.auth import cache_is_fresh, read_cache

    path = Path(data_dir) / "access_token.json"
    if not path.is_file():
        return {"exists": False, "fresh": False, "issued_at": None,
                "detail": "No cached token -- the next connect will do a full login."}
    payload = read_cache(path) or {}
    fresh = cache_is_fresh(payload)
    return {
        "exists": True,
        "fresh": fresh,
        "issued_at": payload.get("issued_at"),
        "detail": ("Cached token is valid for today."
                   if fresh else
                   "Cached token has expired (Kite tokens reset at 06:00 IST); "
                   "the next connect will log in again."),
    }


def _row(name: str, ok: bool, detail: str, ms: float | None = None,
         data: Any = None) -> dict:
    row = {"name": name, "ok": ok, "detail": detail}
    if ms is not None:
        row["ms"] = round(ms, 1)
    if data is not None:
        row["data"] = data
    return row


def run_test(*, creds: dict, data_dir: str | Path, broker: str = "zerodha",
             limiter=None) -> dict:
    """Authenticate and exercise the broker. Never raises.

    Returns {ok, checks: [...], profile, capital}. `capital` is the real broker
    view when the calls succeed, which is what lets the dashboard stop showing
    simulated numbers outside a session.
    """
    checks: list[dict] = []
    profile: dict | None = None
    capital: dict | None = None

    if broker != "zerodha":
        return {"ok": False, "checks": [
            _row("broker supported", False,
                 f"Live testing is implemented for zerodha; {broker!r} is configured "
                 f"for data or trading but has no test path yet.")
        ], "profile": None, "capital": None}

    # 1. Credentials present -- cheap, and the cause of most failures.
    view = credential_view(creds, [broker])[0]
    checks.append(_row(
        "credentials present", view["complete"],
        "All required fields are set." if view["complete"]
        else f"Missing: {', '.join(view['missing'])}",
        data={"fields": view["fields"]}))
    if not view["complete"]:
        return {"ok": False, "checks": checks, "profile": None, "capital": None}

    # 2. Authenticate for real, reusing today's cached token when it is valid.
    from ..brokers.kite import auth as kauth
    from ..brokers.kite import portfolio as kportfolio

    kite = None
    t0 = time.perf_counter()
    try:
        cache = Path(data_dir) / "access_token.json"
        kite, session = kauth.login(creds, cache_path=cache)
        checks.append(_row(
            "authenticate", True,
            f"Signed in as {session.user_name or session.user_id}.",
            (time.perf_counter() - t0) * 1000))
    except Exception as exc:
        checks.append(_row("authenticate", False, f"{type(exc).__name__}: {exc}",
                           (time.perf_counter() - t0) * 1000))
        return {"ok": False, "checks": checks, "profile": None, "capital": None}

    # 3. Profile -- proves the token works against a real endpoint.
    t0 = time.perf_counter()
    try:
        profile = kite.profile() or {}
        checks.append(_row(
            "profile", True,
            f"{profile.get('user_name') or '?'} · {profile.get('email') or '?'}",
            (time.perf_counter() - t0) * 1000,
            data={"user_id": profile.get("user_id"),
                  "user_name": profile.get("user_name"),
                  "email": profile.get("email"),
                  "broker": profile.get("broker"),
                  "products": profile.get("products"),
                  "exchanges": profile.get("exchanges")}))
    except Exception as exc:
        checks.append(_row("profile", False, f"{type(exc).__name__}: {exc}",
                           (time.perf_counter() - t0) * 1000))

    # 4. Margins -- the real capital the dashboard should show.
    t0 = time.perf_counter()
    try:
        capital = kportfolio.capital(
            kportfolio.margins(kite, limiter=limiter, strict=True))
        checks.append(_row(
            "margins", True,
            f"Available Rs {capital.get('available', 0):,.2f} of "
            f"Rs {capital.get('total', 0):,.2f}.",
            (time.perf_counter() - t0) * 1000, data=capital))
    except Exception as exc:
        checks.append(_row("margins", False, f"{type(exc).__name__}: {exc}",
                           (time.perf_counter() - t0) * 1000))

    # 5. Instrument master -- the feed cannot resolve a strike without it.
    t0 = time.perf_counter()
    try:
        rows = kite.instruments("NFO") or []
        checks.append(_row("instrument master (NFO)", bool(rows),
                           f"{len(rows):,} contracts.",
                           (time.perf_counter() - t0) * 1000))
    except Exception as exc:
        checks.append(_row("instrument master (NFO)", False,
                           f"{type(exc).__name__}: {exc}",
                           (time.perf_counter() - t0) * 1000))

    return {"ok": all(c["ok"] for c in checks), "checks": checks,
            "profile": profile, "capital": capital}


__all__ = ["mask", "credential_view", "token_cache_state", "run_test", "EXPECTED"]
