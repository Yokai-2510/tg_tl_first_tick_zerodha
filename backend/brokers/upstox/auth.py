"""
Upstox authentication.

Upstox access tokens are valid for one trading day and expire at 03:30 IST.

Three ways to obtain one, in the order this module tries them:

  1. cached token from today            — no interaction
  2. `access_token` supplied in config  — you pasted one in
  3. OAuth authorization-code exchange  — you have a fresh `code`

The fully automated TOTP flow requires driving Upstox's login pages, which the
rank-momentum project does with Playwright (`scripts/manual_login.py`). That is
deliberately NOT reimplemented here: it is brittle, heavyweight, and only
needed if you want Upstox as the TRADE broker. For Upstox as a DATA broker a
daily token paste (or a cron running the Playwright helper) is sufficient.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import requests

from ...core.timeutil import IST, now_ist
from ..base import BrokerError

TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"
PROFILE_URL = "https://api.upstox.com/v2/user/profile"

#: Upstox invalidates tokens daily at this IST hour.
TOKEN_RESET_HOUR = 3
TOKEN_RESET_MINUTE = 30


@dataclass(frozen=True, slots=True)
class Session:
    access_token: str
    user_id: str = ""
    user_name: str = ""
    issued_at: str = ""


def last_reset_before(ref: datetime) -> datetime:
    reset = ref.replace(hour=TOKEN_RESET_HOUR, minute=TOKEN_RESET_MINUTE,
                        second=0, microsecond=0)
    return reset if ref >= reset else reset - timedelta(days=1)


def cache_is_fresh(payload: dict, *, now: datetime | None = None) -> bool:
    """True when a cached token was issued after the most recent daily reset."""
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
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_cache(path: Path, session: Session) -> None:
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps({
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


def exchange_code(*, code: str, api_key: str, api_secret: str,
                  redirect_uri: str, timeout: float = 15.0) -> Session:
    """Exchange an OAuth authorization code for an access token."""
    try:
        resp = requests.post(
            TOKEN_URL,
            headers={"accept": "application/json",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data={"code": code, "client_id": api_key, "client_secret": api_secret,
                  "redirect_uri": redirect_uri, "grant_type": "authorization_code"},
            timeout=timeout,
        )
        data = resp.json()
    except Exception as exc:
        raise BrokerError(f"Upstox token exchange failed: {exc}") from None

    token = data.get("access_token")
    if not token:
        raise BrokerError(f"Upstox token exchange rejected: {data}")
    return Session(access_token=token, user_id=data.get("user_id", ""),
                   user_name=data.get("user_name", ""),
                   issued_at=now_ist().isoformat())


def verify(access_token: str, timeout: float = 10.0) -> dict | None:
    """Probe /user/profile. Returns the profile, or None if the token is dead."""
    try:
        resp = requests.get(PROFILE_URL, timeout=timeout, headers={
            "Authorization": f"Bearer {access_token}", "Accept": "application/json"})
        data = resp.json()
    except Exception:
        return None
    return data.get("data") if data.get("status") == "success" else None


def login(credentials: dict, *, cache_path: Path | str | None = None) -> Session:
    """Resolve a usable Upstox session, or raise with actionable instructions."""
    cache = Path(cache_path) if cache_path else None

    if cache:
        payload = read_cache(cache)
        if payload and cache_is_fresh(payload):
            profile = verify(payload["access_token"])
            if profile is not None:
                return Session(access_token=payload["access_token"],
                               user_id=profile.get("user_id", ""),
                               user_name=profile.get("user_name", ""),
                               issued_at=payload.get("issued_at", ""))

    token = str(credentials.get("access_token") or "").strip()
    if token:
        profile = verify(token)
        if profile is not None:
            session = Session(access_token=token,
                              user_id=profile.get("user_id", ""),
                              user_name=profile.get("user_name", ""),
                              issued_at=now_ist().isoformat())
            if cache:
                write_cache(cache, session)
            return session

    code = str(credentials.get("auth_code") or "").strip()
    if code:
        session = exchange_code(
            code=code, api_key=credentials["api_key"],
            api_secret=credentials["api_secret"],
            redirect_uri=credentials.get("redirect_uri", ""),
        )
        if cache:
            write_cache(cache, session)
        return session

    raise BrokerError(
        "No usable Upstox token. Provide one of:\n"
        "  * credentials.upstox.access_token  (valid until 03:30 IST)\n"
        "  * credentials.upstox.auth_code     (fresh OAuth code to exchange)\n"
        "Upstox tokens expire daily; automate with the Playwright login helper "
        "if you need Upstox unattended."
    )


__all__ = ["Session", "login", "verify", "exchange_code", "cache_is_fresh",
           "read_cache", "write_cache", "last_reset_before"]
