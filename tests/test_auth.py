"""
Username + password sign-in.

The console previously required pasting a 64-char secret out of a config file on
the server. These cover the accounts that replaced it, and the properties that
make them worth having rather than theatre.
"""

from __future__ import annotations

import time

import pytest

from backend.api.auth import (
    find_user, hash_password, issue_session, read_session, verify_password,
)

# Real PBKDF2 at 600k iterations costs ~200ms; tests use a low count where the
# iteration number is not what is under test.
FAST = 1000
SECRET = "0f9bf722e3f40af877631d59eb68ecbde799b575cd877bebf389c6430481f713"


# ------------------------------------------------------------------ passwords

def test_a_password_verifies_against_its_own_hash():
    h = hash_password("correct horse", iterations=FAST)
    assert verify_password("correct horse", h)


def test_a_wrong_password_is_rejected():
    h = hash_password("correct horse", iterations=FAST)
    assert not verify_password("Correct horse", h)
    assert not verify_password("correct hors", h)
    assert not verify_password("", h)


def test_the_same_password_hashes_differently_every_time():
    """A per-hash salt: two users with one password must not share a digest, or
    the config file leaks which accounts to attack together."""
    a = hash_password("same", iterations=FAST)
    b = hash_password("same", iterations=FAST)
    assert a != b
    assert verify_password("same", a) and verify_password("same", b)


def test_the_plaintext_never_appears_in_the_hash():
    assert "hunter2" not in hash_password("hunter2", iterations=FAST)


def test_an_empty_password_cannot_be_hashed():
    with pytest.raises(ValueError):
        hash_password("")


@pytest.mark.parametrize("junk", [
    "", "not-a-hash", "pbkdf2_sha256$abc$x$y", "md5$1$a$b",
    "pbkdf2_sha256$1000$!!!$!!!", "a$b$c",
])
def test_a_malformed_stored_hash_returns_false_and_does_not_raise(junk):
    """A corrupt config line must be a failed login, never a 500."""
    assert verify_password("anything", junk) is False


def test_the_hash_records_its_iteration_count():
    h = hash_password("x", iterations=12345)
    assert h.startswith("pbkdf2_sha256$12345$")
    assert verify_password("x", h), "must verify using the count it recorded"


# ------------------------------------------------------------------- sessions

def test_a_session_round_trips():
    tok = issue_session("vijay", SECRET, ttl_seconds=3600)
    claims = read_session(tok, SECRET)
    assert claims and claims["u"] == "vijay"


def test_an_expired_session_is_refused():
    tok = issue_session("vijay", SECRET, ttl_seconds=1, now=time.time() - 60)
    assert read_session(tok, SECRET) is None


def test_a_session_signed_with_another_secret_is_refused():
    """Rotating auth_token must invalidate every outstanding session."""
    tok = issue_session("vijay", SECRET, ttl_seconds=3600)
    assert read_session(tok, SECRET + "x") is None


def test_claims_cannot_be_edited_without_the_secret():
    """The forgery that matters: extend your own expiry, or become someone else."""
    import base64, json
    tok = issue_session("vijay", SECRET, ttl_seconds=3600)
    _, body, sig = tok.split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=="))
    claims["u"] = "admin"
    claims["exp"] = claims["exp"] + 10**6
    forged = base64.urlsafe_b64encode(
        json.dumps(claims).encode()).decode().rstrip("=")
    assert read_session(f"v1.{forged}.{sig}", SECRET) is None


def test_the_signature_is_checked_before_the_expiry_is_trusted():
    """An unsigned token claiming a far-future expiry must not be accepted."""
    import base64, json
    claims = {"u": "admin", "iat": 0, "exp": 99999999999}
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    assert read_session(f"v1.{body}.", SECRET) is None
    assert read_session(f"v1.{body}.AAAA", SECRET) is None


@pytest.mark.parametrize("junk", [
    "", "abc", "v1.only-two", "v2.a.b", "v1..", "....",
])
def test_a_malformed_token_is_refused(junk):
    assert read_session(junk, SECRET) is None


def test_issuing_without_a_secret_is_an_error():
    """Sessions signed with an empty key would be trivially forgeable."""
    with pytest.raises(ValueError):
        issue_session("vijay", "", ttl_seconds=60)


def test_reading_without_a_secret_refuses_rather_than_allows():
    tok = issue_session("vijay", SECRET, ttl_seconds=60)
    assert read_session(tok, "") is None


# -------------------------------------------------------------------- lookup

def test_usernames_match_case_insensitively():
    users = [{"username": "Vijay", "password_hash": "x"}]
    assert find_user(users, "vijay") is users[0]
    assert find_user(users, "  VIJAY ") is users[0]
    assert find_user(users, "vijayy") is None
    assert find_user([], "vijay") is None
    assert find_user(None, "vijay") is None
