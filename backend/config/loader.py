"""
Config loading, validation, hot-reload, and credentials.

A malformed config fails HERE with the exact JSON path, not at 09:15 with a
traceback. `ConfigError` messages are safe to show in the API.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from .schema import Config

DEFAULT_CONFIG_PATH = Path("config/config.json")
DEFAULT_CREDENTIALS_PATH = Path("config/credentials.json")

#: Structural fields cannot change mid-session — they decide what is
#: subscribed and armed, which is fixed once the feed is live.
STRUCTURAL_PATHS: frozenset[str] = frozenset({
    "schedule", "broker.api_key", "universe", "instruments", "recorder.format",
    "recorder.compression", "api.host", "api.port",
})


class ConfigError(ValueError):
    """Config failed to load or validate. Message names the offending path."""


def _format_errors(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        path = ".".join(str(p) for p in err["loc"]) or "<root>"
        lines.append(f"  {path}: {err['msg']}")
    return "Config validation failed:\n" + "\n".join(lines)


def parse(raw: dict[str, Any]) -> Config:
    """Validate a config dict. Raises ConfigError with field paths."""
    try:
        return Config.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(_format_errors(exc)) from None


def read_json(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path}") from None
    except OSError as exc:
        raise ConfigError(f"Cannot read {path}: {exc}") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: line {exc.lineno}, {exc.msg}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a JSON object")
    return data


def load(path: Path | str = DEFAULT_CONFIG_PATH) -> Config:
    """Read and validate the config file."""
    return parse(read_json(Path(path)))


def load_credentials(path: Path | str = DEFAULT_CREDENTIALS_PATH) -> dict[str, str]:
    """Read broker credentials. Never logged, never returned by the API."""
    data = read_json(Path(path))
    required = ("api_key", "api_secret", "user_id", "password", "totp_key")
    missing = [k for k in required if not str(data.get(k, "")).strip()]
    if missing:
        raise ConfigError(
            f"{path} missing required field(s): {', '.join(missing)}"
        )
    return {k: str(v) for k, v in data.items() if not k.startswith("_")}


def merge_patch(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """RFC 7386 merge patch. `None` deletes a key; dicts merge recursively."""
    out = dict(base)
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge_patch(out[key], value)
        else:
            out[key] = value
    return out


def changed_paths(before: dict, after: dict, prefix: str = "") -> list[str]:
    """Dotted paths whose values differ between two config dicts."""
    paths: list[str] = []
    for key in set(before) | set(after):
        if key.startswith("_"):
            continue
        path = f"{prefix}{key}"
        b, a = before.get(key), after.get(key)
        if isinstance(b, dict) and isinstance(a, dict):
            paths.extend(changed_paths(b, a, prefix=f"{path}."))
        elif b != a:
            paths.append(path)
    return sorted(paths)


def is_structural(path: str) -> bool:
    """True if changing `path` requires a restart rather than a hot reload."""
    return any(path == s or path.startswith(s + ".") for s in STRUCTURAL_PATHS)


class ConfigStore:
    """Thread-safe holder for the live config.

    Readers get the current validated `Config` object. Writers validate a
    merge patch before anything is applied, so a bad patch never lands.
    """

    def __init__(self, path: Path | str = DEFAULT_CONFIG_PATH):
        self._path = Path(path)
        self._lock = threading.RLock()
        self._raw: dict[str, Any] = {}
        self._config: Config | None = None
        self._listeners: list[Callable[[Config, list[str]], None]] = []

    # -- read ---------------------------------------------------------------

    @property
    def config(self) -> Config:
        with self._lock:
            if self._config is None:
                raise ConfigError("config not loaded; call load() first")
            return self._config

    @property
    def raw(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._raw))       # deep copy

    def load(self) -> Config:
        with self._lock:
            raw = read_json(self._path)
            self._config = parse(raw)
            self._raw = raw
            return self._config

    # -- write --------------------------------------------------------------

    def apply_patch(
        self, patch: dict[str, Any], *, allow_structural: bool = True
    ) -> tuple[Config, list[str]]:
        """Validate and apply a merge patch.

        Returns (new config, changed paths). Raises ConfigError without
        mutating anything if the patch is invalid or, when
        `allow_structural=False`, if it touches a structural field.
        """
        with self._lock:
            merged = merge_patch(self._raw, patch)
            new_cfg = parse(merged)                        # validate BEFORE applying
            changes = changed_paths(self._raw, merged)

            if not allow_structural:
                blocked = [p for p in changes if is_structural(p)]
                if blocked:
                    raise ConfigError(
                        "structural changes are not allowed mid-session: "
                        + ", ".join(blocked)
                    )

            self._raw, self._config = merged, new_cfg

        for cb in list(self._listeners):
            try:
                cb(new_cfg, changes)
            except Exception:                              # a listener must not
                pass                                       # break config updates
        return new_cfg, changes

    def save(self) -> None:
        """Persist the current raw config atomically."""
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(self._raw, indent=2) + "\n", encoding="utf-8")
            tmp.replace(self._path)

    def on_change(self, callback: Callable[[Config, list[str]], None]) -> None:
        with self._lock:
            self._listeners.append(callback)


__all__ = [
    "Config", "ConfigError", "ConfigStore", "STRUCTURAL_PATHS",
    "load", "load_credentials", "parse", "read_json",
    "merge_patch", "changed_paths", "is_structural",
    "DEFAULT_CONFIG_PATH", "DEFAULT_CREDENTIALS_PATH",
]
