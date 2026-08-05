"""
Upstox authentication.

Upstox access tokens are valid for one trading day and expire at 03:30 IST.

Four ways to obtain one, tried in order:

  1. cached token from today              — no interaction
  2. `access_token` supplied in config    — you pasted one in
  3. OAuth authorization-code exchange    — you have a fresh `code`
  4. **automated browser login**          — mobile → TOTP → PIN, unattended

Step 4 is ported from rank-momentum's proven `brokers/upstox/auth.py`: Upstox
has no headless auth API, so the authorization code must be captured from a
real browser redirect. Playwright is an OPTIONAL dependency — imported lazily,
so everything else works without it and only step 4 is unavailable.

    pip install playwright && playwright install chromium
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


# --------------------------------------------------------------------------
# Automated browser login (ported from rank-momentum, proven in production)
# --------------------------------------------------------------------------

LOGIN_URL = "https://api.upstox.com/v2/login/authorization/dialog"

#: Selectors on Upstox's login pages. Kept together so a UI change is a
#: one-place fix rather than a hunt through the flow.
SELECTORS = {
    "mobile": "#mobileNum",
    "get_otp": "Get OTP",
    "otp": "#otpNum",
    "continue": "Continue",
    "pin_visible": "input[type='password']",
    "pin_label": "Enter 6-digit PIN",
}

REQUIRED_LOGIN_FIELDS = ("api_key", "api_secret", "redirect_uri",
                         "mobile_no", "totp_key", "pin")


def fetch_auth_code(credentials: dict, *, headless: bool = True,
                    max_retries: int = 3, browser_args: list[str] | None = None,
                    screenshot_dir: Path | None = None, log=None) -> str:
    """Drive the Upstox login pages and capture the authorization code.

    Upstox exposes no headless auth endpoint — the code only appears on the
    redirect after mobile + TOTP + PIN. Retries with backoff and saves a
    screenshot of each failure, so a UI change is diagnosable rather than a
    silent "login failed".

    Raises:
        BrokerError: playwright missing, credentials incomplete, or all
            attempts exhausted.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise BrokerError(
            "playwright is not installed, so automated Upstox login is "
            "unavailable. Either `pip install playwright && playwright install "
            "chromium`, or supply credentials.upstox.access_token instead."
        ) from None

    missing = [f for f in REQUIRED_LOGIN_FIELDS if not str(credentials.get(f, "")).strip()]
    if missing:
        raise BrokerError(
            f"automated Upstox login needs {', '.join(missing)} in credentials.upstox"
        )

    import time as _time
    from urllib.parse import parse_qs, quote, urlparse

    api_key = credentials["api_key"]
    redirect_uri = credentials["redirect_uri"]
    auth_url = (f"{LOGIN_URL}?response_type=code&client_id={api_key}"
                f"&redirect_uri={quote(redirect_uri, safe='')}")
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        code: str | None = None

        def capture(request) -> None:
            nonlocal code
            if code is None and redirect_uri in request.url and "code=" in request.url:
                code = parse_qs(urlparse(request.url).query).get("code", [None])[0]

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless,
                                            args=browser_args or [])
                context = browser.new_context(ignore_https_errors=True)
                page = context.new_page()
                page.on("request", capture)
                try:
                    page.goto(auth_url, wait_until="domcontentloaded", timeout=60_000)

                    page.wait_for_selector(SELECTORS["mobile"], state="visible",
                                           timeout=30_000)
                    page.locator(SELECTORS["mobile"]).fill(str(credentials["mobile_no"]))
                    page.get_by_role("button", name=SELECTORS["get_otp"]).click()

                    page.wait_for_selector(SELECTORS["otp"], timeout=30_000)
                    page.locator(SELECTORS["otp"]).fill(
                        pyotp.TOTP(credentials["totp_key"]).now())
                    page.get_by_role("button", name=SELECTORS["continue"]).click()

                    page.wait_for_selector(SELECTORS["pin_visible"], timeout=30_000)
                    page.get_by_label(SELECTORS["pin_label"]).fill(str(credentials["pin"]))
                    page.get_by_role("button", name=SELECTORS["continue"]).click()
                    page.wait_for_timeout(5_000)

                    if code is None and redirect_uri in page.url and "code=" in page.url:
                        code = parse_qs(urlparse(page.url).query).get("code", [None])[0]
                except Exception:
                    _screenshot(page, screenshot_dir, attempt, log)
                    raise
                finally:
                    context.close()
                    browser.close()

            if code:
                if log:
                    log.info(f"Upstox authorization code captured (attempt {attempt})")
                return code
            raise BrokerError("login completed but no authorization code appeared")

        except Exception as exc:
            last_error = exc
            if log:
                log.warning(f"Upstox login attempt {attempt}/{max_retries}: {exc}")
            if attempt < max_retries:
                _time.sleep(5 * attempt)

    raise BrokerError(f"Upstox automated login failed after {max_retries} "
                      f"attempts: {last_error}")


def _screenshot(page, directory: Path | None, attempt: int, log) -> None:
    """Best-effort failure screenshot; never masks the original error."""
    if directory is None:
        return
    try:
        Path(directory).mkdir(parents=True, exist_ok=True)
        path = Path(directory) / f"upstox_auth_fail_{attempt}.png"
        page.screenshot(path=str(path), full_page=True)
        if log:
            log.info(f"login failure screenshot: {path}")
    except Exception:
        pass


def verify(access_token: str, timeout: float = 10.0) -> dict | None:
    """Probe /user/profile. Returns the profile, or None if the token is dead."""
    try:
        resp = requests.get(PROFILE_URL, timeout=timeout, headers={
            "Authorization": f"Bearer {access_token}", "Accept": "application/json"})
        data = resp.json()
    except Exception:
        return None
    return data.get("data") if data.get("status") == "success" else None


def login(credentials: dict, *, cache_path: Path | str | None = None,
          auto: bool = True, headless: bool = True, log=None) -> Session:
    """Resolve a usable Upstox session, or raise with actionable instructions.

    Order: cached token -> configured access_token -> auth_code exchange ->
    automated browser login (when `auto` and playwright are available).
    """
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
    if not code and auto:
        code = fetch_auth_code(
            credentials, headless=headless,
            screenshot_dir=(cache.parent if cache else None), log=log)

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
        "  * mobile_no + totp_key + pin       (automated login; needs playwright)"
    )


__all__ = ["Session", "login", "verify", "exchange_code", "fetch_auth_code",
           "cache_is_fresh", "LOGIN_URL", "SELECTORS", "REQUIRED_LOGIN_FIELDS",
           "read_cache", "write_cache", "last_reset_before"]
