"""
Kite authentication: automated TOTP login with a disk token cache.

Kite access tokens expire daily at ~06:00 IST, so a cached token is reused
within the day and a fresh login runs otherwise.

`kiteconnect` is imported lazily inside the functions that need it, so this
module can be imported (and the cache logic tested) without the SDK present.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pyotp
import requests

from ...core.timeutil import IST, now_ist

LOGIN_URL = "https://kite.zerodha.com/api/login"
TWOFA_URL = "https://kite.zerodha.com/api/twofa"
CONNECT_LOGIN = "https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"
CONNECT_FINISH = "https://kite.zerodha.com/connect/finish?sess_id={sess}&api_key={api_key}"

#: Kite invalidates tokens daily around this IST time.
TOKEN_RESET_HOUR = 6


class AuthError(RuntimeError):
    """Login failed. The message is safe to surface; it contains no secrets."""


@dataclass(frozen=True, slots=True)
class Session:
    api_key: str
    access_token: str
    user_id: str
    user_name: str = ""
    issued_at: str = ""


# --------------------------------------------------------------------------
# Token cache
# --------------------------------------------------------------------------

def last_reset_before(ref: datetime) -> datetime:
    """The most recent daily token-reset boundary at or before `ref`."""
    reset = ref.replace(hour=TOKEN_RESET_HOUR, minute=0, second=0, microsecond=0)
    return reset if ref >= reset else reset - timedelta(days=1)


def cache_is_fresh(payload: dict, *, now: datetime | None = None) -> bool:
    """True if a cached token is still valid.

    Kite invalidates tokens at the daily reset, so a token is fresh only if it
    was issued at or after the most recent reset boundary — a token from
    yesterday evening is stale this morning even though it is hours old.
    """
    issued = payload.get("issued_at")
    if not issued or not payload.get("access_token"):
        return False
    try:
        issued_dt = datetime.fromisoformat(issued)
    except (ValueError, TypeError):
        return False
    if issued_dt.tzinfo is None:
        issued_dt = issued_dt.replace(tzinfo=IST)

    ref = now or now_ist()
    return last_reset_before(ref) <= issued_dt <= ref


def read_cache(path: Path) -> dict | None:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def write_cache(path: Path, session: Session) -> None:
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps({
            "api_key": session.api_key,
            "access_token": session.access_token,
            "user_id": session.user_id,
            "user_name": session.user_name,
            "issued_at": session.issued_at,
        }, indent=2), encoding="utf-8")
        tmp.replace(p)
        try:
            p.chmod(0o600)
        except OSError:
            pass
    except OSError:
        pass


# --------------------------------------------------------------------------
# Login
# --------------------------------------------------------------------------

def request_token(credentials: dict[str, str], *, timeout: float = 20.0) -> str:
    """Run the browser-less TOTP flow and return a request_token."""
    api_key = credentials["api_key"]
    session = requests.Session()

    resp = session.get(CONNECT_LOGIN.format(api_key=api_key), timeout=timeout)
    if "sess_id=" not in resp.url:
        raise AuthError(f"could not obtain sess_id (landed on {resp.url.split('?')[0]})")
    sess_id = resp.url.split("sess_id=")[1].split("&")[0]

    login = session.post(LOGIN_URL, timeout=timeout, data={
        "user_id": credentials["user_id"],
        "password": credentials["password"],
    }).json()
    if login.get("status") != "success":
        raise AuthError(f"login rejected: {login.get('message', 'unknown error')}")

    twofa = session.post(TWOFA_URL, timeout=timeout, data={
        "user_id": credentials["user_id"],
        "request_id": login["data"]["request_id"],
        "twofa_value": pyotp.TOTP(credentials["totp_key"]).now(),
        "twofa_type": "totp",
        "skip_session": "true",
    }).json()
    if twofa.get("status") != "success":
        raise AuthError(f"2FA rejected: {twofa.get('message', 'unknown error')}")

    try:
        finish = session.get(
            CONNECT_FINISH.format(sess=sess_id, api_key=api_key),
            timeout=timeout, allow_redirects=True,
        )
        url = finish.url
    except requests.exceptions.ConnectionError as exc:
        url = str(exc)                     # the token often rides on the failed redirect
    if "request_token=" not in url:
        raise AuthError("login flow completed but no request_token was returned")
    return url.split("request_token=")[1].split("&")[0].split(" ")[0]


def login(
    credentials: dict[str, str],
    *,
    cache_path: Path | str | None = None,
    force: bool = False,
) -> tuple[Any, Session]:
    """Authenticate and return `(kite_client, Session)`.

    Reuses a same-day cached token unless `force=True`.
    """
    from kiteconnect import KiteConnect        # lazy: keeps this module importable

    api_key = credentials["api_key"]
    kite = KiteConnect(api_key=api_key)
    cache = Path(cache_path) if cache_path else None

    if cache and not force:
        payload = read_cache(cache)
        if payload and payload.get("api_key") == api_key and cache_is_fresh(payload):
            kite.set_access_token(payload["access_token"])
            try:
                profile = kite.profile()
            except Exception:
                pass                            # stale after all -> fresh login below
            else:
                return kite, Session(
                    api_key=api_key,
                    access_token=payload["access_token"],
                    user_id=profile.get("user_id", payload.get("user_id", "")),
                    user_name=profile.get("user_name", ""),
                    issued_at=payload.get("issued_at", ""),
                )

    token = request_token(credentials)
    try:
        data = kite.generate_session(token, api_secret=credentials["api_secret"])
    except Exception as exc:
        raise AuthError(f"generate_session failed: {exc}") from None

    kite.set_access_token(data["access_token"])
    session = Session(
        api_key=api_key,
        access_token=data["access_token"],
        user_id=data.get("user_id", credentials.get("user_id", "")),
        user_name=data.get("user_name", ""),
        issued_at=now_ist().isoformat(),
    )
    if cache:
        write_cache(cache, session)
    return kite, session


def is_valid(kite: Any) -> bool:
    """Cheap liveness probe against `/user/profile`."""
    try:
        return bool(kite.profile())
    except Exception:
        return False


__all__ = ["AuthError", "Session", "login", "is_valid", "request_token",
           "cache_is_fresh", "read_cache", "write_cache"]
