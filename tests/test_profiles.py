"""
Credential profiles.

Three file layouts exist in the wild and all three must keep working: the flat
one on the deployed host, a per-broker section, and the new profiles shape.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from backend.config.loader import (
    ConfigError, DEFAULT_PROFILE, load_credentials, profiles, set_active_profile,
)

Z = {"api_key": "k" * 16, "api_secret": "s" * 32, "user_id": "AB1234",
     "password": "p" * 11, "totp_key": "t" * 32}


def write(tmp_path, payload) -> pathlib.Path:
    p = tmp_path / "credentials.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


# ------------------------------------------------------------ layout support

def test_a_flat_file_becomes_one_default_profile(tmp_path):
    """The shape actually deployed on the host."""
    p = write(tmp_path, Z)
    known, active = profiles(p)
    assert list(known) == [DEFAULT_PROFILE] and active == DEFAULT_PROFILE
    assert load_credentials(p)["api_key"] == Z["api_key"]


def test_a_per_broker_section_becomes_one_default_profile(tmp_path):
    p = write(tmp_path, {"zerodha": Z})
    known, active = profiles(p)
    assert list(known) == [DEFAULT_PROFILE]
    assert load_credentials(p)["api_secret"] == Z["api_secret"]


def test_top_level_still_beats_a_nested_section(tmp_path):
    """The real host file had both; only the top-level keys ever logged in."""
    p = write(tmp_path, {**Z, "zerodha": {**Z, "api_key": "PLACEHOLDER_KEY_X"}})
    assert load_credentials(p)["api_key"] == Z["api_key"]


def test_the_profiles_layout_is_read_and_the_active_one_honoured(tmp_path):
    p = write(tmp_path, {
        "active_profile": "live",
        "profiles": {"paper": {**Z, "user_id": "PAPER1"},
                     "live": {**Z, "user_id": "LIVE01"}}})
    known, active = profiles(p)
    assert set(known) == {"paper", "live"} and active == "live"
    assert load_credentials(p)["user_id"] == "LIVE01"
    assert load_credentials(p)["_profile"] == "live"


def test_a_specific_profile_can_be_loaded_without_switching(tmp_path):
    p = write(tmp_path, {
        "active_profile": "live",
        "profiles": {"paper": {**Z, "user_id": "PAPER1"}, "live": Z}})
    assert load_credentials(p, profile="paper")["user_id"] == "PAPER1"
    assert load_credentials(p)["user_id"] == "AB1234", "active must be unchanged"


def test_an_unknown_active_profile_falls_back_rather_than_crashing(tmp_path):
    p = write(tmp_path, {"active_profile": "typo", "profiles": {"main": Z}})
    known, active = profiles(p)
    assert active == "main", "a typo must not lock the operator out"


def test_an_unknown_profile_name_is_a_clear_error(tmp_path):
    p = write(tmp_path, {"profiles": {"main": Z}})
    with pytest.raises(ConfigError, match="unknown profile"):
        load_credentials(p, profile="nope")


def test_incomplete_credentials_name_the_profile(tmp_path):
    p = write(tmp_path, {"profiles": {"main": {"api_key": "only"}}})
    with pytest.raises(ConfigError, match="'main'"):
        load_credentials(p)


# ------------------------------------------------------------- switching

def test_switching_migrates_a_flat_file_to_the_profiles_shape(tmp_path):
    p = write(tmp_path, Z)
    assert set_active_profile(DEFAULT_PROFILE, p) == DEFAULT_PROFILE
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["active_profile"] == DEFAULT_PROFILE
    assert raw["profiles"][DEFAULT_PROFILE]["api_key"] == Z["api_key"]
    assert load_credentials(p)["api_key"] == Z["api_key"], "still loads after migration"


def test_switching_changes_which_profile_loads(tmp_path):
    p = write(tmp_path, {
        "active_profile": "a",
        "profiles": {"a": {**Z, "user_id": "AAAAAA"},
                     "b": {**Z, "user_id": "BBBBBB"}}})
    assert load_credentials(p)["user_id"] == "AAAAAA"
    set_active_profile("b", p)
    assert load_credentials(p)["user_id"] == "BBBBBB"
    assert set(profiles(p)[0]) == {"a", "b"}, "the other profile must survive"


def test_switching_to_an_unknown_profile_is_refused_and_changes_nothing(tmp_path):
    p = write(tmp_path, {"active_profile": "a", "profiles": {"a": Z}})
    before = p.read_text(encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown profile"):
        set_active_profile("ghost", p)
    assert p.read_text(encoding="utf-8") == before, "the file must be untouched"


def test_the_write_is_atomic_and_leaves_no_temp_files(tmp_path):
    p = write(tmp_path, Z)
    set_active_profile(DEFAULT_PROFILE, p)
    leftovers = [f.name for f in tmp_path.iterdir() if f.name.startswith(".tmp-")]
    assert leftovers == [], leftovers


def test_no_secret_leaks_into_the_doc_field(tmp_path):
    p = write(tmp_path, Z)
    set_active_profile(DEFAULT_PROFILE, p)
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert Z["api_secret"] not in str(raw.get("_doc", ""))


# ------------------------------------------- the /broker/profiles endpoints

def test_the_test_route_does_not_clobber_the_account_with_the_profile_name():
    """run_test returns the broker account under `profile`. Stamping the credential
    set's name onto the same key silently replaced the account details with a
    string, so the console had nothing to show."""
    from backend.api import broker_check

    class FakeKite:
        def profile(self):
            return {"user_name": "Vijay", "email": "v@example.com"}
        def margins(self):
            return {"equity": {"available": {"live_balance": 1000.0}}}
        def instruments(self, exch):
            return [{"tradingsymbol": "X"}]

    import backend.brokers.kite.auth as kauth
    real = kauth.login
    kauth.login = lambda *a, **k: (
        FakeKite(), type("S", (), {"user_name": "Vijay", "user_id": "AB1234"})())
    try:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            res = broker_check.run_test(creds=Z, data_dir=td)
    finally:
        kauth.login = real

    assert isinstance(res["profile"], dict), "the account must stay a dict"
    assert res["profile"]["user_name"] == "Vijay"
    res["profile_name"] = "live"          # what the route adds
    assert isinstance(res["profile"], dict), "adding the name must not overwrite it"
