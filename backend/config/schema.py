"""
Validated configuration schema.

Every field has a type, a default, and where it matters a constraint. Invalid
config fails at load with the offending JSON path — never at 09:15.

`_doc` keys in the JSON are documentation and are ignored here.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..core.enums import (
    AtmSource, DiskFullPolicy, FillModel, FallbackTo, Moneyness, PnlBasis,
    PriceSource, Product, RankingBasis, TradingMode, Validity,
)
from ..core.timeutil import parse_hhmmss


class _Base(BaseModel):
    """Ignores `_doc` and any future additive keys; forbids nothing silently."""

    model_config = ConfigDict(extra="ignore", validate_assignment=True)


def _time_field(name: str):
    @field_validator(name)
    @classmethod
    def _v(cls, v: str) -> str:
        parse_hhmmss(v)          # raises with a clear message if malformed
        return v
    return _v


# --------------------------------------------------------------------------

class SystemCfg(_Base):
    timezone: str = "Asia/Kolkata"
    data_dir: str = "./data"
    log_level: str = "INFO"
    retention_days: int = Field(7, ge=0)


class ScheduleCfg(_Base):
    phase1_time: str = "08:45:00"
    feed_connect_time: str = "08:55:00"
    preopen_start: str = "09:00:00"
    settlement_snapshot: str = "09:09:00"
    wave2_subscribe_time: str = "09:09:30"
    option_reference_time: str = "09:13:00"
    manual_cutoff: str = "09:14:00"
    trading_start: str = "09:15:00"
    eod_time: str = "15:28:00"
    auto_continue_daily: bool = True

    _v1 = _time_field("phase1_time")
    _v2 = _time_field("feed_connect_time")
    _v3 = _time_field("preopen_start")
    _v4 = _time_field("settlement_snapshot")
    _v5 = _time_field("wave2_subscribe_time")
    _v6 = _time_field("option_reference_time")
    _v7 = _time_field("manual_cutoff")
    _v8 = _time_field("trading_start")
    _v9 = _time_field("eod_time")

    @model_validator(mode="after")
    def _ordered(self):
        seq = [
            ("phase1_time", self.phase1_time),
            ("feed_connect_time", self.feed_connect_time),
            ("preopen_start", self.preopen_start),
            ("settlement_snapshot", self.settlement_snapshot),
            ("wave2_subscribe_time", self.wave2_subscribe_time),
            ("option_reference_time", self.option_reference_time),
            ("manual_cutoff", self.manual_cutoff),
            ("trading_start", self.trading_start),
            ("eod_time", self.eod_time),
        ]
        for (n1, t1), (n2, t2) in zip(seq, seq[1:]):
            if parse_hhmmss(t1) > parse_hhmmss(t2):
                raise ValueError(
                    f"schedule out of order: {n1} ({t1}) must not be after {n2} ({t2})"
                )
        return self


class RateLimitsCfg(_Base):
    orders_per_sec: int = Field(10, ge=1, le=10)
    quote_per_sec: int = Field(1, ge=1, le=3)
    per_minute: int = Field(400, ge=1, le=400)
    daily_cap: int = Field(5000, ge=1, le=5000)


class TimeoutsCfg(_Base):
    order_ms: int = Field(3000, ge=100)
    quote_ms: int = Field(2000, ge=100)


class WsCfg(_Base):
    reconnect_max_tries: int = Field(50, ge=1)
    reconnect_max_delay_s: int = Field(30, ge=1)


class ProductCfg(_Base):
    stock_options: Product = Product.NRML
    index_options: Product = Product.MIS


class BrokerCfg(_Base):
    """Data and trading brokers are chosen INDEPENDENTLY.

    `data_broker`  : zerodha | upstox   — feed, instrument master, quotes
    `trade_broker` : zerodha | upstox | paper — orders and the position book

    Splitting them is useful in practice: running data on Upstox with
    `trade_broker: paper` touches no Zerodha API key at all, so it cannot
    disturb another system already using that key.
    """

    data_broker: str = "zerodha"
    trade_broker: str = "zerodha"
    api_key: str = ""
    product: ProductCfg = Field(default_factory=ProductCfg)
    rate_limits: RateLimitsCfg = Field(default_factory=RateLimitsCfg)
    timeouts: TimeoutsCfg = Field(default_factory=TimeoutsCfg)
    ws: WsCfg = Field(default_factory=WsCfg)

    @field_validator("data_broker")
    @classmethod
    def _data_supported(cls, v: str) -> str:
        value = v.strip().lower()
        if value not in ("zerodha", "upstox"):
            raise ValueError(f"data_broker must be zerodha or upstox, got {v!r}")
        return value

    @field_validator("trade_broker")
    @classmethod
    def _trade_supported(cls, v: str) -> str:
        value = v.strip().lower()
        if value not in ("zerodha", "upstox", "paper"):
            raise ValueError(
                f"trade_broker must be zerodha, upstox or paper, got {v!r}")
        return value


class TradingModeCfg(_Base):
    mode: TradingMode = TradingMode.PAPER

    @property
    def is_live(self) -> bool:
        return self.mode is TradingMode.LIVE

    @property
    def place_actual_orders(self) -> bool:
        return self.is_live


class IndexCfg(_Base):
    enabled: bool = False
    lots: int = Field(1, ge=1)
    strike_offset: int = Field(2, ge=0)


class UniverseCfg(_Base):
    enabled: bool = True
    top_n_gainers: int = Field(5, ge=0, le=50)
    top_n_losers: int = Field(5, ge=0, le=50)
    candidate_buffer: int = Field(5, ge=0, le=50)
    ranking_basis: RankingBasis = RankingBasis.SETTLEMENT
    atm_source: AtmSource = AtmSource.SETTLEMENT
    atm_fallback_chain: list[AtmSource] = Field(
        default_factory=lambda: [AtmSource.SETTLEMENT, AtmSource.PREV_CLOSE]
    )
    rerank_on_open: bool = False
    subscribe_futures_preopen: bool = False
    indices: dict[str, IndexCfg] = Field(default_factory=dict)
    manual_instruments: list[dict] = Field(default_factory=list)
    per_symbol_overrides: dict[str, dict] = Field(default_factory=dict)
    nifty50_fallback_symbols: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _something_to_trade(self):
        any_index = any(c.enabled for c in self.indices.values())
        any_rank = self.enabled and (self.top_n_gainers + self.top_n_losers) > 0
        if not any_index and not any_rank and not self.manual_instruments:
            raise ValueError(
                "universe selects nothing: enable ranking, an index, or add "
                "manual_instruments"
            )
        return self

    @property
    def enabled_indices(self) -> list[str]:
        return [k.upper() for k, v in self.indices.items() if v.enabled]


class ExpiryRollCfg(_Base):
    enabled: bool = True
    buffer_trading_days: int = Field(1, ge=0, le=5)
    applies_to: str = "stocks_only"


class InstrumentsCfg(_Base):
    strike_reference: Moneyness = Moneyness.ITM
    strike_offset: int = Field(2, ge=0)
    strikes_per_side: int = Field(4, ge=1, le=20)
    subscription_soft_cap: int = Field(2400, ge=1, le=3000)
    subscribe_all_chains_early: bool = False
    expiry_roll: ExpiryRollCfg = Field(default_factory=ExpiryRollCfg)


class SnapshotCfg(_Base):
    enabled: bool = True
    time: str = "09:09:00"
    window_seconds: int = Field(60, ge=0)
    source: str = "prev_close"
    from_: str | None = Field(None, alias="from")
    to: str | None = None


class SnapshotsCfg(_Base):
    baseline: SnapshotCfg = Field(default_factory=lambda: SnapshotCfg(time="08:55:00"))
    preopen_track: SnapshotCfg = Field(default_factory=lambda: SnapshotCfg(time="09:00:00"))
    settlement: SnapshotCfg = Field(default_factory=lambda: SnapshotCfg(time="09:09:00"))
    option_reference: SnapshotCfg = Field(default_factory=lambda: SnapshotCfg(time="09:13:00"))
    market_open: SnapshotCfg = Field(default_factory=lambda: SnapshotCfg(time="09:15:00", window_seconds=2))
    eod: SnapshotCfg = Field(default_factory=lambda: SnapshotCfg(time="15:30:15"))


class RetryCfg(_Base):
    enabled: bool = True
    max_attempts: int = Field(3, ge=1, le=10)
    interval_ms: int = Field(300, ge=50)


class LimitModCfg(_Base):
    enabled: bool = True
    max_modifications: int = Field(3, ge=0, le=25)   # Kite hard-caps at 25
    step_pct: float = Field(1.0, ge=0.0)


class LppCfg(_Base):
    retries: int = Field(3, ge=0, le=10)
    safety_factor: float = Field(0.99, gt=0.5, le=1.0)


class OrderTypeCfg(_Base):
    stock_options: str = "LIMIT"
    index_options: str = "LIMIT"

    @field_validator("stock_options", "index_options")
    @classmethod
    def _valid(cls, v: str) -> str:
        if v.upper() not in ("LIMIT", "MARKET"):
            raise ValueError(f"order_type must be LIMIT or MARKET, got {v!r}")
        return v.upper()


class OrderFallbackCfg(_Base):
    enabled: bool = True
    on: list[str] = Field(default_factory=lambda:
                          ["ORDER_TYPE_REJECT", "LPP_REJECT", "NO_DEPTH"])
    to: FallbackTo = FallbackTo.MARKETABLE_LIMIT


class EntryCfg(_Base):
    min_diff: float = 0.0
    fire_after_seconds: float = Field(1.0, ge=0.0)
    deadline_seconds: int = Field(180, ge=1)
    require_depth: bool = True
    min_premium: float = Field(0.0, ge=0.0)
    max_premium: float = Field(0.0, ge=0.0)
    entry_price_source: PriceSource = PriceSource.ASK
    entry_slippage_pct: float = Field(1.5, ge=0.0, le=20.0)
    entry_validity: Validity = Validity.IOC
    order_type: OrderTypeCfg = Field(default_factory=OrderTypeCfg)
    order_fallback: OrderFallbackCfg = Field(default_factory=OrderFallbackCfg)
    lots_default: int = Field(1, ge=1)
    max_notional_per_trade: float = Field(0.0, ge=0.0)
    max_total_notional: float = Field(0.0, ge=0.0)
    entry_retry: RetryCfg = Field(default_factory=RetryCfg)
    limit_modification: LimitModCfg = Field(default_factory=LimitModCfg)
    lpp: LppCfg = Field(default_factory=LppCfg)

    @model_validator(mode="after")
    def _premium_window(self):
        if self.max_premium and self.min_premium > self.max_premium:
            raise ValueError(
                f"min_premium ({self.min_premium}) > max_premium ({self.max_premium})"
            )
        if self.entry_price_source is PriceSource.BID:
            raise ValueError("entry_price_source must be 'ask' or 'ltp', not 'bid'")
        return self


class ToggledPct(_Base):
    enabled: bool = True
    percentage: float = 0.0


class TrailingStopCfg(_Base):
    enabled: bool = True
    activation_pct: float = Field(7.0, ge=0.0)
    trail_distance_pct: float = Field(3.0, gt=0.0, lt=100.0)


class TrailingTargetCfg(_Base):
    enabled: bool = False
    activation_pct: float = Field(15.0, ge=0.0)
    extend_distance_pct: float = Field(5.0, gt=0.0, lt=100.0)
    max_extension_pct: float = Field(50.0, gt=0.0)


class TimeExitCfg(_Base):
    enabled: bool = False
    holding_seconds: int = Field(1200, ge=1)


class EodExitCfg(_Base):
    enabled: bool = True
    square_off_time: str = "15:28:00"
    _v = _time_field("square_off_time")


class ManualDetectionCfg(_Base):
    enabled: bool = True


class ExitsCfg(_Base):
    stop_loss: ToggledPct = Field(default_factory=lambda: ToggledPct(percentage=-5.0))
    target: ToggledPct = Field(default_factory=lambda: ToggledPct(percentage=30.0))
    trailing_stop: TrailingStopCfg = Field(default_factory=TrailingStopCfg)
    trailing_target: TrailingTargetCfg = Field(default_factory=TrailingTargetCfg)
    time_exit: TimeExitCfg = Field(default_factory=TimeExitCfg)
    eod_exit: EodExitCfg = Field(default_factory=EodExitCfg)
    manual_detection: ManualDetectionCfg = Field(default_factory=ManualDetectionCfg)
    monitor_interval_ms: int = Field(25, ge=5, le=1000)
    pnl_basis: PnlBasis = PnlBasis.LTP
    exit_price_source: PriceSource = PriceSource.BID
    exit_slippage_pct: float = Field(1.0, ge=0.0, le=20.0)
    eod_slippage_pct: float = Field(3.0, ge=0.0, le=30.0)

    @model_validator(mode="after")
    def _sane(self):
        if self.stop_loss.enabled and self.stop_loss.percentage >= 0:
            raise ValueError("stop_loss.percentage must be negative (e.g. -5.0)")
        if self.target.enabled and self.target.percentage <= 0:
            raise ValueError("target.percentage must be positive")
        if self.exit_price_source is PriceSource.ASK:
            raise ValueError("exit_price_source must be 'bid' or 'ltp', not 'ask'")
        tt = self.trailing_target
        if tt.enabled and tt.max_extension_pct <= tt.activation_pct:
            raise ValueError(
                "trailing_target.max_extension_pct must exceed activation_pct"
            )
        return self


class BrokerSyncCfg(_Base):
    enabled: bool = True
    poll_interval_seconds: float = Field(2.0, ge=0.5, le=60.0)


class PositionsCfg(_Base):
    max_concurrent: int = Field(10, ge=1, le=100)
    max_per_symbol: int = Field(1, ge=1, le=10)
    broker_sync: BrokerSyncCfg = Field(default_factory=BrokerSyncCfg)


class UploadCfg(_Base):
    enabled: bool = False
    target: str = ""
    after: str = "eod"


class RecorderCfg(_Base):
    enabled: bool = True
    format: str = "ndjson"
    compression: str = "zstd"
    record_depth_levels: int = Field(5, ge=0, le=5)
    flush_interval_ms: int = Field(500, ge=50)
    post_exit_record_seconds: int = Field(300, ge=0)
    retention_days: int = Field(7, ge=0)
    max_disk_mb: int = Field(20000, ge=100)
    on_disk_full: DiskFullPolicy = DiskFullPolicy.STOP_RECORDING
    upload: UploadCfg = Field(default_factory=UploadCfg)

    @field_validator("compression")
    @classmethod
    def _comp(cls, v: str) -> str:
        if v not in ("none", "zstd"):
            raise ValueError(f"compression must be 'none' or 'zstd', got {v!r}")
        return v


class TelegramCfg(_Base):
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""


class AlertsCfg(_Base):
    telegram: TelegramCfg = Field(default_factory=TelegramCfg)
    email: dict = Field(default_factory=dict)
    on: list[str] = Field(default_factory=list)


class ApiUser(_Base):
    """One console account. The password is never stored, only a PBKDF2 hash --
    generate one with `python -m backend.tools.passwd <password>`."""

    username: str = Field(min_length=1)
    password_hash: str = Field(default="", min_length=0)


class ApiCfg(_Base):
    host: str = "127.0.0.1"
    port: int = Field(8080, ge=1, le=65535)
    cors_origins: list[str] = Field(default_factory=list)
    auth_token: str = ""
    ws_push_interval_ms: int = Field(250, ge=50, le=5000)
    #: Console accounts for username+password sign-in. `auth_token` keeps working
    #: alongside these for scripts, curl and health checks.
    users: list[ApiUser] = Field(default_factory=list)
    #: How long a sign-in lasts. Sessions are signed with `auth_token`, so
    #: rotating that token revokes every outstanding session immediately.
    session_ttl_hours: int = Field(12, ge=1, le=720)

    @model_validator(mode="after")
    def _no_wildcard_with_token(self):
        if "*" in self.cors_origins and self.auth_token:
            raise ValueError(
                "cors_origins ['*'] with a real auth_token exposes the trading "
                "API to any origin; list explicit origins instead"
            )
        return self


class PaperCfg(_Base):
    starting_capital: float = Field(1_000_000.0, gt=0)
    simulate_charges: bool = True
    fill_model: FillModel = FillModel.TOUCH


class Config(_Base):
    """Root configuration."""

    system: SystemCfg = Field(default_factory=SystemCfg)
    schedule: ScheduleCfg = Field(default_factory=ScheduleCfg)
    broker: BrokerCfg = Field(default_factory=BrokerCfg)
    trading_mode: TradingModeCfg = Field(default_factory=TradingModeCfg)
    universe: UniverseCfg = Field(default_factory=UniverseCfg)
    instruments: InstrumentsCfg = Field(default_factory=InstrumentsCfg)
    snapshots: SnapshotsCfg = Field(default_factory=SnapshotsCfg)
    entry: EntryCfg = Field(default_factory=EntryCfg)
    exits: ExitsCfg = Field(default_factory=ExitsCfg)
    positions: PositionsCfg = Field(default_factory=PositionsCfg)
    recorder: RecorderCfg = Field(default_factory=RecorderCfg)
    alerts: AlertsCfg = Field(default_factory=AlertsCfg)
    api: ApiCfg = Field(default_factory=ApiCfg)
    paper: PaperCfg = Field(default_factory=PaperCfg)

    @model_validator(mode="after")
    def _cross_section(self):
        if self.exits.eod_exit.enabled:
            if parse_hhmmss(self.exits.eod_exit.square_off_time) < \
                    parse_hhmmss(self.schedule.trading_start):
                raise ValueError("exits.eod_exit.square_off_time is before trading_start")
        return self


__all__ = ["Config"]
