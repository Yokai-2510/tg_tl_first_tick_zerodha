"""Config schema, validation, merge-patch and the structural-change gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.config.loader import (
    ConfigError, ConfigStore, changed_paths, is_structural, load,
    load_credentials, merge_patch, parse,
)

EXAMPLE = Path("config/config.example.json")


@pytest.fixture
def raw() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8-sig"))


def test_shipped_example_is_valid():
    """The config we ship must actually load."""
    cfg = load(EXAMPLE)
    assert cfg.trading_mode.mode == "paper"
    assert cfg.universe.enabled_indices == ["NIFTY", "BANKNIFTY", "SENSEX"]
    assert cfg.broker.product.stock_options == "NRML"
    assert cfg.entry.entry_slippage_pct == 1.5


def test_defaults_fill_in():
    cfg = parse({})
    assert cfg.entry.min_diff == 0.0
    assert cfg.exits.monitor_interval_ms == 25
    assert cfg.instruments.strikes_per_side == 4


@pytest.mark.parametrize("patch,needle", [
    ({"exits": {"stop_loss": {"percentage": 5.0}}}, "must be negative"),
    ({"exits": {"target": {"percentage": -1.0}}}, "must be positive"),
    ({"schedule": {"trading_start": "25:00:00"}}, "out of range"),
    ({"schedule": {"manual_cutoff": "09:20:00"}}, "out of order"),
    ({"entry": {"entry_price_source": "bid"}}, "ask' or 'ltp"),
    ({"exits": {"exit_price_source": "ask"}}, "bid' or 'ltp"),
    ({"instruments": {"subscription_soft_cap": 9999}}, "less than or equal to 3000"),
    ({"entry": {"order_type": {"stock_options": "SL-M"}}}, "LIMIT or MARKET"),
    ({"broker": {"rate_limits": {"orders_per_sec": 50}}}, "less than or equal to 10"),
    ({"entry": {"limit_modification": {"max_modifications": 99}}}, "less than or equal to 25"),
    ({"exits": {"trailing_stop": {"trail_distance_pct": 0}}}, "greater than 0"),
])
def test_invalid_config_is_rejected(raw, patch, needle):
    with pytest.raises(ConfigError) as exc:
        parse(merge_patch(raw, patch))
    assert needle in str(exc.value)


def test_error_message_names_the_field(raw):
    with pytest.raises(ConfigError) as exc:
        parse(merge_patch(raw, {"instruments": {"subscription_soft_cap": 9999}}))
    assert "instruments.subscription_soft_cap" in str(exc.value)


def test_wildcard_cors_with_token_is_rejected(raw):
    with pytest.raises(ConfigError, match="cors_origins"):
        parse(merge_patch(raw, {"api": {"cors_origins": ["*"],
                                        "auth_token": "secret"}}))


def test_wildcard_cors_allowed_without_token(raw):
    cfg = parse(merge_patch(raw, {"api": {"cors_origins": ["*"], "auth_token": ""}}))
    assert cfg.api.cors_origins == ["*"]


def test_universe_must_select_something(raw):
    with pytest.raises(ConfigError, match="selects nothing"):
        parse(merge_patch(raw, {"universe": {
            "enabled": False, "top_n_gainers": 0, "top_n_losers": 0,
            "manual_instruments": [],
            "indices": {"NIFTY": {"enabled": False}, "BANKNIFTY": {"enabled": False},
                        "SENSEX": {"enabled": False}, "FINNIFTY": {"enabled": False}},
        }}))


def test_trailing_target_ceiling_must_exceed_activation(raw):
    with pytest.raises(ConfigError, match="max_extension_pct"):
        parse(merge_patch(raw, {"exits": {"trailing_target": {
            "enabled": True, "activation_pct": 30.0, "max_extension_pct": 20.0}}}))


def test_eod_before_trading_start_is_rejected(raw):
    with pytest.raises(ConfigError, match="before trading_start"):
        parse(merge_patch(raw, {"exits": {"eod_exit": {"square_off_time": "08:00:00"}}}))


# -- merge patch -----------------------------------------------------------

def test_merge_patch_is_recursive():
    base = {"a": {"b": 1, "c": 2}, "d": 3}
    assert merge_patch(base, {"a": {"b": 9}}) == {"a": {"b": 9, "c": 2}, "d": 3}


def test_merge_patch_none_deletes():
    assert merge_patch({"a": 1, "b": 2}, {"b": None}) == {"a": 1}


def test_merge_patch_does_not_mutate_base():
    base = {"a": {"b": 1}}
    merge_patch(base, {"a": {"b": 2}})
    assert base == {"a": {"b": 1}}


def test_changed_paths():
    before = {"a": {"b": 1, "c": 2}, "d": 3}
    after = {"a": {"b": 9, "c": 2}, "d": 3, "e": 4}
    assert changed_paths(before, after) == ["a.b", "e"]


def test_changed_paths_ignores_doc_keys():
    assert changed_paths({"_doc": "x", "a": 1}, {"_doc": "y", "a": 1}) == []


# -- structural gate -------------------------------------------------------

@pytest.mark.parametrize("path,expected", [
    ("universe.top_n_gainers", True),
    ("schedule.trading_start", True),
    ("instruments.strikes_per_side", True),
    ("broker.api_key", True),
    ("api.port", True),
    ("exits.stop_loss.percentage", False),
    ("entry.entry_slippage_pct", False),
    ("trading_mode.mode", False),
])
def test_is_structural(path, expected):
    assert is_structural(path) is expected


def test_store_rejects_structural_change_mid_session(tmp_path, raw):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    store = ConfigStore(path)
    store.load()

    with pytest.raises(ConfigError, match="structural"):
        store.apply_patch({"universe": {"top_n_gainers": 9}}, allow_structural=False)
    assert store.config.universe.top_n_gainers == 5      # unchanged

    cfg, changed = store.apply_patch(
        {"exits": {"stop_loss": {"percentage": -8.0}}}, allow_structural=False)
    assert cfg.exits.stop_loss.percentage == -8.0
    assert changed == ["exits.stop_loss.percentage"]


def test_store_rolls_back_on_invalid_patch(tmp_path, raw):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    store = ConfigStore(path)
    store.load()
    with pytest.raises(ConfigError):
        store.apply_patch({"exits": {"stop_loss": {"percentage": 99.0}}})
    assert store.config.exits.stop_loss.percentage == -5.0     # untouched


def test_store_notifies_listeners(tmp_path, raw):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    store = ConfigStore(path)
    store.load()
    seen = []
    store.on_change(lambda cfg, changed: seen.append(changed))
    store.apply_patch({"entry": {"entry_slippage_pct": 2.0}})
    assert seen == [["entry.entry_slippage_pct"]]


def test_store_save_round_trip(tmp_path, raw):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    store = ConfigStore(path)
    store.load()
    store.apply_patch({"entry": {"entry_slippage_pct": 2.5}})
    store.save()
    assert ConfigStore(path).load().entry.entry_slippage_pct == 2.5


# -- files -----------------------------------------------------------------

def test_missing_file_message(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load(tmp_path / "nope.json")


def test_malformed_json_names_the_line(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text('{"system": {,}}', encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load(p)


def test_credentials_require_all_fields(tmp_path):
    p = tmp_path / "creds.json"
    p.write_text(json.dumps({"api_key": "k", "api_secret": "s"}), encoding="utf-8")
    with pytest.raises(ConfigError, match="missing required"):
        load_credentials(p)

    p.write_text(json.dumps({"api_key": "k", "api_secret": "s", "user_id": "u",
                             "password": "p", "totp_key": "t"}), encoding="utf-8")
    assert load_credentials(p)["api_key"] == "k"
