"""
Exit engine — priority-ordered conditions, each independently toggleable.

PURE: no I/O, no broker calls, no clock reads except the `now` passed in.
The caller places the actual exit order.

Priority (first hit wins) — BUILD_SPEC §9:
    MANUAL_BROKER > MANUAL_API > STOP_LOSS > TARGET
    > TRAILING_TARGET > TRAILING_SL > TIME_EXIT > EOD_SQUAREOFF

All positions are LONG options, so "up" is always profit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dtime

from ..core.enums import ExitTrigger
from ..core.models import Position
from ..core.pricing import pnl_basis_price, pnl_pct as calc_pnl_pct
from ..core.timeutil import parse_hhmmss


@dataclass(frozen=True, slots=True)
class ExitConfig:
    """Flattened exit configuration. Built once from the config section."""

    stop_loss_enabled: bool = True
    stop_loss_pct: float = -5.0                # negative

    target_enabled: bool = True
    target_pct: float = 30.0

    trailing_stop_enabled: bool = True
    trailing_stop_activation_pct: float = 7.0
    trailing_stop_distance_pct: float = 3.0

    trailing_target_enabled: bool = False
    trailing_target_activation_pct: float = 15.0
    trailing_target_extend_pct: float = 5.0
    trailing_target_max_extension_pct: float = 50.0

    time_exit_enabled: bool = False
    time_exit_holding_seconds: int = 1200

    eod_exit_enabled: bool = True
    eod_square_off_time: str = "15:28:00"

    pnl_basis: str = "ltp"

    @property
    def eod_time(self) -> dtime:
        return parse_hhmmss(self.eod_square_off_time)


# --------------------------------------------------------------------------
# Live stats refresh — call once per monitor tick, before evaluate()
# --------------------------------------------------------------------------

def refresh_live(pos: Position, cfg: ExitConfig, *, now_us: int) -> None:
    """Recompute P&L and holding time from the position's current prices."""
    basis = pnl_basis_price(ltp=pos.live.ltp, bid=pos.live.bid, basis=cfg.pnl_basis)
    entry = pos.entry.price
    pos.live.pnl_pct = calc_pnl_pct(entry, basis)
    pos.live.pnl = round((basis - entry) * pos.quantity, 2) if entry > 0 and basis > 0 else 0.0
    if pos.live.pnl_pct > pos.live.max_pnl_pct:
        pos.live.max_pnl_pct = pos.live.pnl_pct
    if pos.live.pnl_pct < pos.live.min_pnl_pct:
        pos.live.min_pnl_pct = pos.live.pnl_pct
    if pos.entry.at_us > 0 and now_us > pos.entry.at_us:
        pos.live.holding_seconds = int((now_us - pos.entry.at_us) / 1_000_000)


# --------------------------------------------------------------------------
# Trailing state — both levels ratchet UPWARD ONLY, never reset down
# --------------------------------------------------------------------------

def update_trailing(pos: Position, cfg: ExitConfig) -> None:
    """Arm and advance trailing stop / trailing target. Call before evaluate()."""
    ltp = pos.live.ltp
    if ltp <= 0:
        return
    pnl = pos.live.pnl_pct

    if cfg.trailing_stop_enabled and pnl >= cfg.trailing_stop_activation_pct:
        t = pos.trailing
        if not t.sl_active:
            t.sl_active = True
            t.sl_peak = ltp
        if ltp > t.sl_peak:
            t.sl_peak = ltp
        level = round(t.sl_peak * (1 - cfg.trailing_stop_distance_pct / 100.0), 4)
        if level > t.sl_level:
            t.sl_level = level

    if cfg.trailing_target_enabled and pnl >= cfg.trailing_target_activation_pct:
        t = pos.trailing
        if not t.tgt_active:
            t.tgt_active = True
            t.tgt_peak = ltp
        if ltp > t.tgt_peak:
            t.tgt_peak = ltp
        level = round(t.tgt_peak * (1 - cfg.trailing_target_extend_pct / 100.0), 4)
        if level > t.tgt_level:
            t.tgt_level = level


# --------------------------------------------------------------------------
# Individual conditions
# --------------------------------------------------------------------------

def _stop_loss(pos: Position, cfg: ExitConfig, _now: datetime) -> bool:
    return cfg.stop_loss_enabled and pos.live.pnl_pct <= cfg.stop_loss_pct


def _target(pos: Position, cfg: ExitConfig, _now: datetime) -> bool:
    if not cfg.target_enabled:
        return False
    # With trailing target on, the plain target must NOT fire — otherwise it
    # would pre-empt the trail every time. Exit only at the ceiling.
    if cfg.trailing_target_enabled:
        return pos.live.pnl_pct >= cfg.trailing_target_max_extension_pct
    return pos.live.pnl_pct >= cfg.target_pct


def _trailing_target(pos: Position, cfg: ExitConfig, _now: datetime) -> bool:
    if not cfg.trailing_target_enabled or not pos.trailing.tgt_active:
        return False
    return pos.live.ltp <= pos.trailing.tgt_level


def _trailing_sl(pos: Position, cfg: ExitConfig, _now: datetime) -> bool:
    if not cfg.trailing_stop_enabled or not pos.trailing.sl_active:
        return False
    return pos.live.ltp <= pos.trailing.sl_level


def _time_exit(pos: Position, cfg: ExitConfig, _now: datetime) -> bool:
    return (cfg.time_exit_enabled
            and pos.live.holding_seconds > cfg.time_exit_holding_seconds)


def _eod(pos: Position, cfg: ExitConfig, now: datetime) -> bool:
    return cfg.eod_exit_enabled and now.time() >= cfg.eod_time


#: Evaluation order. MANUAL_* are raised by other subsystems, not checked here.
_CHECKS: tuple[tuple[ExitTrigger, object], ...] = (
    (ExitTrigger.STOP_LOSS, _stop_loss),
    (ExitTrigger.TARGET, _target),
    (ExitTrigger.TRAILING_TARGET, _trailing_target),
    (ExitTrigger.TRAILING_SL, _trailing_sl),
    (ExitTrigger.TIME_EXIT, _time_exit),
    (ExitTrigger.EOD_SQUAREOFF, _eod),
)


def evaluate(
    pos: Position, cfg: ExitConfig, now: datetime
) -> tuple[bool, ExitTrigger | None]:
    """First triggered condition wins.

    Returns (False, None) if the position is already exiting — the latch makes
    exits idempotent when several conditions trip on the same tick
    (BUILD_SPEC R8).
    """
    if pos.flags.exiting:
        return False, None
    if pos.entry.price <= 0 or pos.live.ltp <= 0:
        return False, None                      # no usable prices yet

    for trigger, check in _CHECKS:
        if check(pos, cfg, now):
            return True, trigger
    return False, None


def build_config(exits_cfg) -> ExitConfig:
    """Build an ExitConfig from the validated `exits` config section."""
    def sec(name: str):
        v = getattr(exits_cfg, name, None) if not isinstance(exits_cfg, dict) \
            else exits_cfg.get(name)
        return v or {}

    def val(section, key, default):
        s = sec(section)
        return (s.get(key, default) if isinstance(s, dict)
                else getattr(s, key, default))

    top = (lambda k, d: exits_cfg.get(k, d)) if isinstance(exits_cfg, dict) \
        else (lambda k, d: getattr(exits_cfg, k, d))

    return ExitConfig(
        stop_loss_enabled=val("stop_loss", "enabled", True),
        stop_loss_pct=val("stop_loss", "percentage", -5.0),
        target_enabled=val("target", "enabled", True),
        target_pct=val("target", "percentage", 30.0),
        trailing_stop_enabled=val("trailing_stop", "enabled", True),
        trailing_stop_activation_pct=val("trailing_stop", "activation_pct", 7.0),
        trailing_stop_distance_pct=val("trailing_stop", "trail_distance_pct", 3.0),
        trailing_target_enabled=val("trailing_target", "enabled", False),
        trailing_target_activation_pct=val("trailing_target", "activation_pct", 15.0),
        trailing_target_extend_pct=val("trailing_target", "extend_distance_pct", 5.0),
        trailing_target_max_extension_pct=val("trailing_target", "max_extension_pct", 50.0),
        time_exit_enabled=val("time_exit", "enabled", False),
        time_exit_holding_seconds=val("time_exit", "holding_seconds", 1200),
        eod_exit_enabled=val("eod_exit", "enabled", True),
        eod_square_off_time=val("eod_exit", "square_off_time", "15:28:00"),
        pnl_basis=top("pnl_basis", "ltp"),
    )


__all__ = ["ExitConfig", "refresh_live", "update_trailing", "evaluate", "build_config"]
