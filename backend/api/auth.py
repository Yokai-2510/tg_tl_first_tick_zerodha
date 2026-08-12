"""
Username + password sign-in, and the session tokens it issues.

Why not just the static `api.auth_token`: one shared secret cannot be revoked for
one person, has no expiry, and has to be copied by hand from a config file on the
server. This adds real accounts while keeping that token working for scripts and
health checks.

Sessions are **stateless**: the token carries its own claims and an HMAC signature,
so a service restart does not sign everybody out and there is no session table to
grow. The signing key is derived from `api.auth_token`, so rotating that token
invalidates every outstanding session -- which is the behaviour you want from it.

Passwords are stored as PBKDF2-HMAC-SHA256, never plaintext. Format:

    pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>

Nothing here needs a third-party dependency; `hashlib` and `hmac` are stdlib.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

#: OWASP's floor for PBKDF2-HMAC-SHA256 is 600k; this is a login path hit rarely,
#: so the ~200ms cost is invisible to a person and expensive to a cracker.
ITERATIONS = 600_000
ALGO = "pbkdf2_sha256"


def _b64e(raw: bytes) -> str:
    """URL-safe base64 with padding stripped, so a token is one clean word."""
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(txt: str) -> bytes:
    return base64.urlsafe_b64decode(txt + "=" * (-len(txt) % 4))


# --------------------------------------------------------------- passwords

def hash_password(password: str, *, iterations: int = ITERATIONS,
                  salt: bytes | None = None) -> str:
    if not password:
        raise ValueError("password must not be empty")
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{ALGO}${iterations}${_b64e(salt)}${_b64e(dk)}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check. Returns False on any malformed record rather than
    raising, so a corrupt config line cannot become a 500 on the login route."""
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$")
        if algo != ALGO:
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 _b64d(salt_b64), int(iters))
    except Exception:
        return False
    return hmac.compare_digest(dk, _b64d(hash_b64))


# --------------------------------------------------------------- sessions

def _sign(payload_b64: str, secret: str) -> str:
    return _b64e(hmac.new(secret.encode("utf-8"), payload_b64.encode("utf-8"),
                          hashlib.sha256).digest())


def issue_session(username: str, secret: str, *, ttl_seconds: int,
                  now: float | None = None) -> str:
    """Build a signed, self-describing token: v1.<claims>.<signature>."""
    if not secret:
        raise ValueError("a signing secret is required to issue sessions")
    now = time.time() if now is None else now
    claims = {"u": username, "iat": int(now), "exp": int(now + ttl_seconds)}
    body = _b64e(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    return f"v1.{body}.{_sign(body, secret)}"


def read_session(token: str, secret: str, *, now: float | None = None) -> dict | None:
    """Return the claims if the token is genuine and unexpired, else None.

    The signature is checked BEFORE the claims are trusted, so an attacker cannot
    hand us a token that merely *claims* not to have expired.
    """
    if not token or not secret:
        return None
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "v1":
        return None
    _, body, sig = parts
    if not hmac.compare_digest(sig, _sign(body, secret)):
        return None
    try:
        claims = json.loads(_b64d(body))
    except Exception:
        return None
    now = time.time() if now is None else now
    if not isinstance(claims.get("exp"), int) or claims["exp"] <= now:
        return None
    return claims


def find_user(users: list, username: str):
    """Match a username case-insensitively; people do not remember capitalisation."""
    target = (username or "").strip().lower()
    for u in users or []:
        name = getattr(u, "username", None) or (u.get("username") if isinstance(u, dict) else None)
        if name and name.strip().lower() == target:
            return u
    return None


__all__ = ["hash_password", "verify_password", "issue_session", "read_session",
           "find_user", "ITERATIONS", "ALGO"]
