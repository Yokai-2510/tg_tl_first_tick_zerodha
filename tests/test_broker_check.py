"""
Credential masking and the broker connection test.

The console had no way to see which credentials the server held, and no way to
prove they worked -- the first evidence either way arrived at 08:45 when phase 1
either authenticated or did not.
"""

from __future__ import annotations

import pytest

from backend.api.broker_check import (
    EXPECTED, credential_view, mask, run_test, token_cache_state,
)

FULL = {"zerodha": {"api_key": "kitefx9f2a1234", "api_secret": "s3cr3tvalue0987",
                    "user_id": "AB1234", "password": "hunter2hunter2",
                    "totp_key": "JBSWY3DPEHPK3PXP"}}


# ------------------------------------------------------------------- masking

def test_masking_never_returns_the_secret():
    assert "s3cr3tvalue0987" not in mask("s3cr3tvalue0987")


def test_masking_keeps_a_value_recognisable():
    """Enough to spot the wrong key; not enough to use it."""
    m = mask("kitefx9f2a1234")
    assert m.startswith("kit")
    assert "••34" in m, m          # last two kept; the suffix is the length note
    assert "fx9f2a12" not in m


def test_short_secrets_reveal_nothing_at_all():
    """A 6-digit PIN must not leak its first and last digit."""
    for pin in ("123456", "1234", "12345678"):
        m = mask(pin)
        assert set(m) == {"•"}, m
        assert len(m) == len(pin)


def test_masking_an_empty_value_is_empty_not_bullets():
    assert mask("") == ""
    assert mask(None) == ""


def test_length_is_reported_for_long_values():
    assert "(14 chars)" in mask("kitefx9f2a1234")


# ---------------------------------------------------------------- cred view

def test_a_complete_zerodha_section_is_complete():
    view = credential_view(FULL, ["zerodha"])[0]
    assert view["complete"] and view["missing"] == []
    assert {f["key"] for f in view["fields"]} == {k for k, _ in EXPECTED["zerodha"]}
    assert all(f["present"] for f in view["fields"])


def test_missing_required_fields_are_named():
    partial = {"zerodha": {"api_key": "kitefx9f2a1234"}}
    view = credential_view(partial, ["zerodha"])[0]
    assert not view["complete"]
    assert set(view["missing"]) == {"api_secret", "user_id", "password", "totp_key"}


def test_no_raw_secret_survives_anywhere_in_the_view():
    """The whole point of the endpoint: it must be safe to send over the wire."""
    import json
    blob = json.dumps(credential_view(FULL, ["zerodha"]))
    for secret in FULL["zerodha"].values():
        assert secret not in blob, f"{secret} leaked"


def test_an_absent_broker_section_reports_incomplete_not_crash():
    view = credential_view({}, ["zerodha"])[0]
    assert view["complete"] is False
    assert all(not f["present"] for f in view["fields"])


def test_optional_upstox_fields_do_not_block_completeness():
    ups = {"upstox": {"api_key": "k" * 12, "api_secret": "s" * 12,
                      "redirect_uri": "https://example.com/cb"}}
    view = credential_view(ups, ["upstox"])[0]
    assert view["complete"], view["missing"]


# -------------------------------------------------------------- token cache

def test_a_missing_token_cache_is_reported_not_raised(tmp_path):
    st = token_cache_state(tmp_path)
    assert st["exists"] is False and st["fresh"] is False
    assert "full login" in st["detail"]


def test_a_stale_token_cache_is_flagged_as_not_fresh(tmp_path):
    """A file existing proves nothing -- Kite tokens die at 06:00 IST."""
    import json
    (tmp_path / "access_token.json").write_text(json.dumps({
        "api_key": "k", "access_token": "t", "issued_at": "2020-01-01T09:00:00+05:30",
    }), encoding="utf-8")
    st = token_cache_state(tmp_path)
    assert st["exists"] is True and st["fresh"] is False
    assert "expired" in st["detail"].lower()


# ------------------------------------------------------------------ the test

def test_incomplete_credentials_fail_fast_without_touching_the_network(tmp_path):
    """No point attempting a login that cannot succeed."""
    res = run_test(creds={"zerodha": {"api_key": "x"}}, data_dir=tmp_path)
    assert res["ok"] is False
    assert [c["name"] for c in res["checks"]] == ["credentials present"]
    assert res["capital"] is None


def test_an_unsupported_broker_says_so_rather_than_pretending(tmp_path):
    res = run_test(creds=FULL, data_dir=tmp_path, broker="upstox")
    assert res["ok"] is False
    assert "no test path" in res["checks"][0]["detail"]


def test_a_login_failure_is_reported_as_a_row_not_an_exception(tmp_path, monkeypatch):
    """One broken step must still report the steps around it."""
    from backend.brokers.kite import auth as kauth

    def boom(*a, **k):
        raise RuntimeError("TOTP rejected")
    monkeypatch.setattr(kauth, "login", boom)

    res = run_test(creds=FULL, data_dir=tmp_path)
    assert res["ok"] is False
    names = [c["name"] for c in res["checks"]]
    assert names == ["credentials present", "authenticate"]
    assert "TOTP rejected" in res["checks"][1]["detail"]
    assert res["checks"][1]["ms"] >= 0, "timing must still be recorded on failure"


def test_a_successful_run_reports_every_step_and_real_capital(tmp_path, monkeypatch):
    from backend.brokers.kite import auth as kauth

    class FakeKite:
        def profile(self):
            return {"user_name": "Vijay", "email": "v@example.com",
                    "user_id": "AB1234", "broker": "ZERODHA"}
        def margins(self):
            return {"equity": {"available": {"live_balance": 250000.0,
                                             "opening_balance": 250000.0},
                               "utilised": {"debits": 0.0}}}
        def instruments(self, exch):
            return [{"tradingsymbol": "X"}] * 3
    monkeypatch.setattr(kauth, "login", lambda *a, **k: (
        FakeKite(), type("S", (), {"user_name": "Vijay", "user_id": "AB1234"})()))

    res = run_test(creds=FULL, data_dir=tmp_path)
    assert res["ok"] is True, res["checks"]
    assert [c["name"] for c in res["checks"]] == [
        "credentials present", "authenticate", "profile", "margins",
        "instrument master (NFO)"]
    assert res["profile"]["user_name"] == "Vijay"
    assert res["capital"]["available"] == 250000.0
    assert all("ms" in c for c in res["checks"][1:]), "each call must be timed"


def test_a_partial_failure_still_reports_the_checks_that_passed(tmp_path, monkeypatch):
    """Auth working but margins failing is a different problem from total failure."""
    from backend.brokers.kite import auth as kauth

    class HalfKite:
        def profile(self):
            return {"user_name": "Vijay"}
        def margins(self):
            raise RuntimeError("rate limited")
        def instruments(self, exch):
            return [{"tradingsymbol": "X"}]
    monkeypatch.setattr(kauth, "login", lambda *a, **k: (
        HalfKite(), type("S", (), {"user_name": "Vijay", "user_id": "AB1234"})()))

    res = run_test(creds=FULL, data_dir=tmp_path)
    assert res["ok"] is False
    by = {c["name"]: c for c in res["checks"]}
    assert by["authenticate"]["ok"] and by["profile"]["ok"]
    assert not by["margins"]["ok"] and "rate limited" in by["margins"]["detail"]
    assert by["instrument master (NFO)"]["ok"], "later checks must still run"
    assert res["capital"] is None
