/**
 * Curated configuration sections.
 *
 * `GET /config` returns the whole config plus its JSON Schema, and the backend
 * will happily accept a patch for any path. A generated form built straight from
 * the schema is complete but unreadable: it puts the trading mode next to a
 * retention window, and it cannot say what order things happen in. So the paths
 * an operator actually touches are grouped here by decision, in the order the
 * engine makes them, with the sequence spelled out where the behaviour is not
 * obvious from a field name.
 *
 * Every `path` below is a real dotted path into the config object. Anything not
 * listed here is still editable — the Raw tab in Settings edits the object
 * directly — but it is not part of the daily loop.
 *
 * `structural` marks a section the engine reads once at boot. A patch is
 * accepted but does not take effect until a restart, and the UI says so before
 * the operator wonders why nothing changed.
 */

export type FieldType = 'bool' | 'enum' | 'int' | 'float' | 'string' | 'list'

export interface Field {
  path: string
  label: string
  type: FieldType
  doc: string
  options?: string[]
  min?: number
  max?: number
  unit?: string
}

export interface Section {
  id: string
  title: string
  doc?: string
  structural?: boolean
  steps?: string[]
  /** Sections whose body is a purpose-built screen rather than a field list. */
  custom?: 'instruments' | 'appearance' | 'credentials' | 'raw'
  fields?: Field[]
}

export const STRATEGY: Section[] = [
  { id: 'instruments', title: 'Instruments', custom: 'instruments' },

  {
    id: 'direction',
    title: 'Direction',
    doc: 'How CE or PE is decided. The side is never configured by hand — it falls out of the first tick after the open.',
    steps: [
      'Every armed instrument carries a reference price. For the underlying that is the 09:09 settlement snapshot it was ranked on; for the option contract it is the previous close, because options do not trade in the pre-open.',
      'From 09:15:00 each incoming tick is compared to that reference. The signed difference is the diff shown on Status and against each armed row.',
      'The first diff that clears the minimum resolves direction: above the reference buys CE, below buys PE.',
      'Direction is then fixed. Contracts picks the strike, one entry is sent, and that instrument never fires again for the session.',
      'If nothing clears the minimum before the deadline the instrument is dropped and its subscription released.',
    ],
    fields: [
      { path: 'universe.ranking_basis', label: 'ranking_basis', type: 'enum', options: ['settlement', 'futures_preopen', 'prev_close'], doc: 'Which price the pre-open ranking is computed from.' },
      { path: 'snapshots.option_reference.source', label: 'option_reference.source', type: 'enum', options: ['prev_close', 'settlement'], doc: 'The per-strike baseline the entry diff is measured against.' },
      { path: 'snapshots.option_reference.time', label: 'option_reference.time', type: 'string', doc: 'When that baseline is captured.' },
      { path: 'entry.min_diff', label: 'min_diff', type: 'float', min: 0, max: 100, doc: '0.0 means the first strictly positive tick qualifies.' },
      { path: 'entry.fire_after_seconds', label: 'fire_after_seconds', type: 'int', min: 0, max: 60, unit: 's', doc: 'Delay past 09:15:00 before firing, so the order is not treated as an AMO.' },
      { path: 'entry.deadline_seconds', label: 'deadline_seconds', type: 'int', min: 0, max: 900, unit: 's', doc: 'Stop looking for a qualifying tick after this. Also stops a mid-day restart firing stale entries.' },
      { path: 'entry.require_depth', label: 'require_depth', type: 'bool', doc: 'Refuse to fire into an empty book.' },
      { path: 'universe.rerank_on_open', label: 'rerank_on_open', type: 'bool', doc: 'Re-rank at 09:15 instead of holding the 09:09 ranking.' },
    ],
  },

  {
    id: 'contracts',
    title: 'Contracts',
    doc: 'Which option contract is bought once direction has resolved.',
    structural: true,
    steps: [
      'Direction resolves first. CE for an upward first tick, PE for a downward one — the side is not chosen here.',
      'ATM is the strike closest to the underlying at the ATM source price. ITM and OTM step away from it by the strike offset.',
      'strikes_per_side decides how much of the chain is subscribed either side of that strike, which is what keeps a ranking shuffle from leaving us without a live chain.',
      'Stock F&O is physically settled and fresh MIS buys are blocked in the last two trading days, so stocks roll to the next expiry. Indices are cash-settled and never roll.',
      'Subscriptions are budgeted against the soft cap; the count is on Status.',
    ],
    fields: [
      { path: 'instruments.strike_reference', label: 'strike_reference', type: 'enum', options: ['ATM', 'ITM', 'OTM'], doc: 'Which strike to buy once direction is known.' },
      { path: 'instruments.strike_offset', label: 'strike_offset', type: 'int', min: 0, max: 10, doc: 'Strikes away from ATM when ITM or OTM is chosen. Ignored for ATM.' },
      { path: 'instruments.strikes_per_side', label: 'strikes_per_side', type: 'int', min: 1, max: 20, doc: 'Chain depth subscribed either side of the chosen strike.' },
      { path: 'universe.atm_source', label: 'atm_source', type: 'enum', options: ['settlement', 'futures_preopen', 'prev_close'], doc: 'Price used to locate ATM.' },
      { path: 'instruments.subscription_soft_cap', label: 'subscription_soft_cap', type: 'int', min: 100, max: 5000, doc: 'Warn above this many live subscriptions.' },
      { path: 'instruments.subscribe_all_chains_early', label: 'subscribe_all_chains_early', type: 'bool', doc: 'Subscribe every candidate chain at arming rather than in two waves.' },
      { path: 'instruments.expiry_roll.enabled', label: 'expiry_roll.enabled', type: 'bool', doc: 'Roll to the next expiry near settlement.' },
      { path: 'instruments.expiry_roll.buffer_trading_days', label: 'expiry_roll.buffer_trading_days', type: 'int', min: 0, max: 5, doc: 'Trading days before expiry at which the roll happens.' },
      { path: 'instruments.expiry_roll.applies_to', label: 'expiry_roll.applies_to', type: 'enum', options: ['stocks_only', 'all', 'none'], doc: 'Indices are cash-settled, so stocks_only is correct for Zerodha.' },
    ],
  },

  {
    id: 'entry',
    title: 'Entry',
    doc: 'How the single allowed entry per instrument is priced and placed.',
    steps: [
      'The order is a LIMIT priced from the ask plus slippage. Zerodha blocks MARKET on stock options, so LIMIT is not optional there.',
      'A rejection that is recoverable — order type, LPP ceiling, no depth — falls back to a marketable limit rather than giving up.',
      'The limit is then modified up to max_modifications times, stepping by step_pct, before the attempt is abandoned.',
      'Every attempt is recorded and grouped under its position in Positions → Orders.',
    ],
    fields: [
      { path: 'entry.entry_price_source', label: 'entry_price_source', type: 'enum', options: ['ask', 'mid', 'last'], doc: 'Side of the book the limit is priced from.' },
      { path: 'entry.entry_slippage_pct', label: 'entry_slippage_pct', type: 'float', min: 0, max: 10, unit: '%', doc: 'Added to the price source to produce the limit.' },
      { path: 'entry.entry_validity', label: 'entry_validity', type: 'enum', options: ['IOC', 'DAY'], doc: 'IOC leaves nothing resting in the book.' },
      { path: 'entry.lots_default', label: 'lots_default', type: 'int', min: 1, max: 50, doc: 'Lots sent per entry unless a per-symbol override says otherwise.' },
      { path: 'entry.min_premium', label: 'min_premium', type: 'float', min: 0, max: 100000, doc: 'Skip contracts cheaper than this. 0 disables.' },
      { path: 'entry.max_premium', label: 'max_premium', type: 'float', min: 0, max: 100000, doc: 'Skip contracts richer than this. 0 disables.' },
      { path: 'entry.entry_retry.max_attempts', label: 'entry_retry.max_attempts', type: 'int', min: 1, max: 10, doc: 'Attempts after a recoverable rejection.' },
      { path: 'entry.entry_retry.interval_ms', label: 'entry_retry.interval_ms', type: 'int', min: 50, max: 5000, unit: 'ms', doc: 'Wait between attempts.' },
      { path: 'entry.limit_modification.enabled', label: 'limit_modification.enabled', type: 'bool', doc: 'Chase an unfilled limit rather than cancelling it.' },
      { path: 'entry.limit_modification.max_modifications', label: 'limit_modification.max_modifications', type: 'int', min: 0, max: 10, doc: 'How many times the limit may be moved.' },
      { path: 'entry.limit_modification.step_pct', label: 'limit_modification.step_pct', type: 'float', min: 0, max: 10, unit: '%', doc: 'How far each modification moves the price.' },
      { path: 'entry.lpp.retries', label: 'lpp.retries', type: 'int', min: 0, max: 10, doc: 'Retries after a last-price-protection rejection.' },
      { path: 'entry.lpp.safety_factor', label: 'lpp.safety_factor', type: 'float', min: 0.5, max: 1, doc: 'Fraction of the LPP ceiling the retry is priced at.' },
    ],
  },

  {
    id: 'exits',
    title: 'Exits',
    doc: 'Evaluated in priority order; the first trigger wins. Exits keep running even when entries are disarmed.',
    steps: [
      'Stop loss and target are fixed percentages of the entry price.',
      'The trailing stop arms once the position is up by activation_pct, then follows the peak at trail_distance_pct.',
      'When trailing_target is enabled the plain target is suppressed: the position runs to max_extension_pct or exits on a pullback instead.',
      'eod_exit squares off whatever is left at the square-off time, with its own slippage allowance.',
    ],
    fields: [
      { path: 'exits.stop_loss.enabled', label: 'stop_loss.enabled', type: 'bool', doc: 'Hard stop on entry price.' },
      { path: 'exits.stop_loss.percentage', label: 'stop_loss.percentage', type: 'float', min: -90, max: 0, unit: '%', doc: 'Negative. Percentage of entry price.' },
      { path: 'exits.target.enabled', label: 'target.enabled', type: 'bool', doc: 'Fixed take-profit.' },
      { path: 'exits.target.percentage', label: 'target.percentage', type: 'float', min: 0, max: 500, unit: '%', doc: 'Percentage of entry price.' },
      { path: 'exits.trailing_stop.enabled', label: 'trailing_stop.enabled', type: 'bool', doc: 'Ratchet the stop upward as the position gains.' },
      { path: 'exits.trailing_stop.activation_pct', label: 'trailing_stop.activation_pct', type: 'float', min: 0, max: 200, unit: '%', doc: 'Gain at which trailing begins.' },
      { path: 'exits.trailing_stop.trail_distance_pct', label: 'trailing_stop.trail_distance_pct', type: 'float', min: 0, max: 100, unit: '%', doc: 'Distance the stop trails behind the peak.' },
      { path: 'exits.trailing_target.enabled', label: 'trailing_target.enabled', type: 'bool', doc: 'Suppresses the plain target and extends instead.' },
      { path: 'exits.trailing_target.activation_pct', label: 'trailing_target.activation_pct', type: 'float', min: 0, max: 500, unit: '%', doc: 'Gain at which the target starts extending.' },
      { path: 'exits.trailing_target.max_extension_pct', label: 'trailing_target.max_extension_pct', type: 'float', min: 0, max: 500, unit: '%', doc: 'Ceiling the extension stops at.' },
      { path: 'exits.time_exit.enabled', label: 'time_exit.enabled', type: 'bool', doc: 'Force an exit after a holding period.' },
      { path: 'exits.time_exit.holding_seconds', label: 'time_exit.holding_seconds', type: 'int', min: 0, max: 22500, unit: 's', doc: 'Seconds held before the forced exit.' },
      { path: 'exits.eod_exit.square_off_time', label: 'eod_exit.square_off_time', type: 'string', doc: 'When the end-of-day square-off begins.' },
      { path: 'exits.monitor_interval_ms', label: 'monitor_interval_ms', type: 'int', min: 5, max: 1000, unit: 'ms', doc: 'How often exit conditions are evaluated.' },
      { path: 'exits.pnl_basis', label: 'pnl_basis', type: 'enum', options: ['ltp', 'bid', 'mid'], doc: 'Price P&L is marked against.' },
      { path: 'exits.exit_price_source', label: 'exit_price_source', type: 'enum', options: ['bid', 'mid', 'last'], doc: 'Side of the book an exit is priced from.' },
      { path: 'exits.exit_slippage_pct', label: 'exit_slippage_pct', type: 'float', min: 0, max: 10, unit: '%', doc: 'Allowance on a normal exit.' },
      { path: 'exits.eod_slippage_pct', label: 'eod_slippage_pct', type: 'float', min: 0, max: 20, unit: '%', doc: 'Wider allowance at square-off, where getting out matters more than the price.' },
    ],
  },

  {
    id: 'risk',
    title: 'Risk',
    doc: 'Session-level guards that cap exposure independently of individual position stops.',
    fields: [
      { path: 'positions.max_concurrent', label: 'max_concurrent', type: 'int', min: 1, max: 50, doc: 'Hard cap on concurrent open positions.' },
      { path: 'positions.max_per_symbol', label: 'max_per_symbol', type: 'int', min: 1, max: 10, doc: '1 prevents pyramiding into the same name.' },
      { path: 'entry.max_notional_per_trade', label: 'max_notional_per_trade', type: 'float', min: 0, max: 10000000, doc: 'Rupee ceiling for one entry. 0 disables.' },
      { path: 'entry.max_total_notional', label: 'max_total_notional', type: 'float', min: 0, max: 50000000, doc: 'Rupee ceiling across all open positions. 0 disables.' },
      { path: 'positions.broker_sync.enabled', label: 'broker_sync.enabled', type: 'bool', doc: 'Poll the broker book to catch positions closed by hand in the Kite app.' },
      { path: 'positions.broker_sync.poll_interval_seconds', label: 'broker_sync.poll_interval_seconds', type: 'int', min: 1, max: 60, unit: 's', doc: 'There is no position stream on the websocket, so this is a poll.' },
    ],
  },

  {
    id: 'mode',
    title: 'Mode & charges',
    doc: 'Whether orders reach the exchange, and how paper fills and costs are simulated. Read this twice before changing the mode.',
    steps: [
      'Paper places no broker order at all. Fills are simulated from the observed book using the fill model.',
      'Live sends real orders. The topbar turns red and the tab title is prefixed for the rest of the session.',
      'Charges are applied to paper P&L when simulate_charges is on, so the two modes stay comparable.',
      'Switching to live always asks for confirmation.',
    ],
    fields: [
      { path: 'trading_mode.mode', label: 'mode', type: 'enum', options: ['paper', 'live'], doc: 'paper simulates fills from the book; live sends real orders.' },
      { path: 'paper.fill_model', label: 'fill_model', type: 'enum', options: ['touch', 'mid', 'last'], doc: 'touch fills at the observed ask, capped at our limit — what a marketable limit really gets.' },
      { path: 'paper.starting_capital', label: 'starting_capital', type: 'float', min: 0, max: 100000000, doc: 'Opening balance for the simulated book.' },
      { path: 'paper.simulate_charges', label: 'simulate_charges', type: 'bool', doc: 'Apply brokerage, STT, exchange, SEBI, stamp duty and GST to paper P&L.' },
    ],
  },
]

export const SETTINGS: Section[] = [
  { id: 'appearance', title: 'Appearance', custom: 'appearance' },
  { id: 'credentials', title: 'Credentials', custom: 'credentials' },

  {
    id: 'connection',
    title: 'Connection',
    doc: 'Which broker supplies data, which one places orders, and how the feed behaves. The two are independent — data on one broker with orders on paper touches no live key at all.',
    structural: true,
    fields: [
      { path: 'broker.api_key', label: 'api_key', type: 'string', doc: 'Broker API key. The secret lives in credentials.json, never here.' },
      { path: 'broker.product.stock_options', label: 'product.stock_options', type: 'enum', options: ['NRML', 'MIS'], doc: 'NRML avoids the physical-settlement block on stock options.' },
      { path: 'broker.product.index_options', label: 'product.index_options', type: 'enum', options: ['MIS', 'NRML'], doc: 'Index options are cash-settled, so MIS is fine.' },
      { path: 'broker.rate_limits.orders_per_sec', label: 'rate_limits.orders_per_sec', type: 'int', min: 1, max: 50, doc: 'Enforced locally, mirroring the broker’s documented cap.' },
      { path: 'broker.rate_limits.per_minute', label: 'rate_limits.per_minute', type: 'int', min: 1, max: 2000, doc: 'Per-minute order cap.' },
      { path: 'broker.rate_limits.daily_cap', label: 'rate_limits.daily_cap', type: 'int', min: 1, max: 20000, doc: 'Per-day order cap.' },
      { path: 'broker.timeouts.order_ms', label: 'timeouts.order_ms', type: 'int', min: 200, max: 30000, unit: 'ms', doc: 'Order request timeout.' },
      { path: 'broker.timeouts.quote_ms', label: 'timeouts.quote_ms', type: 'int', min: 200, max: 30000, unit: 'ms', doc: 'Quote request timeout.' },
      { path: 'broker.ws.reconnect_max_tries', label: 'ws.reconnect_max_tries', type: 'int', min: 0, max: 200, doc: 'Feed reconnect attempts before giving up for the session.' },
      { path: 'broker.ws.reconnect_max_delay_s', label: 'ws.reconnect_max_delay_s', type: 'int', min: 1, max: 300, unit: 's', doc: 'Ceiling on the exponential backoff.' },
    ],
  },

  {
    id: 'schedule',
    title: 'Schedule',
    doc: 'The fixed daily clock. The state machine advances on these times; the service idles between sessions and needs nothing launched by hand.',
    structural: true,
    fields: [
      { path: 'schedule.phase1_time', label: 'phase1_time', type: 'string', doc: 'Authentication and contract download begins.' },
      { path: 'schedule.feed_connect_time', label: 'feed_connect_time', type: 'string', doc: 'Websocket connects and recording starts.' },
      { path: 'schedule.preopen_start', label: 'preopen_start', type: 'string', doc: 'Equity auction begins being tracked.' },
      { path: 'schedule.settlement_snapshot', label: 'settlement_snapshot', type: 'string', doc: 'Pre-open snapshot taken and the ranking computed.' },
      { path: 'schedule.wave2_subscribe_time', label: 'wave2_subscribe_time', type: 'string', doc: 'Second subscription wave for the selected chains.' },
      { path: 'schedule.option_reference_time', label: 'option_reference_time', type: 'string', doc: 'Per-strike entry baseline captured.' },
      { path: 'schedule.manual_cutoff', label: 'manual_cutoff', type: 'string', doc: 'Last moment a manual instrument may be added.' },
      { path: 'schedule.trading_start', label: 'trading_start', type: 'string', doc: 'Entries become permitted.' },
      { path: 'schedule.eod_time', label: 'eod_time', type: 'string', doc: 'Square-off begins.' },
      { path: 'schedule.auto_continue_daily', label: 'auto_continue_daily', type: 'bool', doc: 'Roll straight into the next session instead of stopping at IDLE.' },
    ],
  },

  {
    id: 'recorder',
    title: 'Recorder',
    doc: 'Full-depth tick and event capture, from feed connect until the last position closes plus the post-exit window.',
    fields: [
      { path: 'recorder.enabled', label: 'enabled', type: 'bool', doc: 'Write ticks and events to disk.' },
      { path: 'recorder.format', label: 'format', type: 'enum', options: ['ndjson', 'parquet'], doc: 'On-disk format for the tick stream.' },
      { path: 'recorder.compression', label: 'compression', type: 'enum', options: ['none', 'zstd', 'gzip'], doc: 'Codec for the tick stream.' },
      { path: 'recorder.record_depth_levels', label: 'record_depth_levels', type: 'int', min: 0, max: 20, doc: 'Book levels captured per tick.' },
      { path: 'recorder.flush_interval_ms', label: 'flush_interval_ms', type: 'int', min: 50, max: 10000, unit: 'ms', doc: 'Write cadence.' },
      { path: 'recorder.post_exit_record_seconds', label: 'post_exit_record_seconds', type: 'int', min: 0, max: 3600, unit: 's', doc: 'Keep recording after the last exit, so the aftermath is on disk.' },
      { path: 'recorder.max_disk_mb', label: 'max_disk_mb', type: 'int', min: 100, max: 1000000, unit: 'MB', doc: 'Recording budget on disk.' },
      { path: 'recorder.on_disk_full', label: 'on_disk_full', type: 'enum', options: ['stop_recording', 'halt_trading'], doc: 'stop_recording keeps trading; halt_trading does the opposite.' },
      { path: 'recorder.retention_days', label: 'retention_days', type: 'int', min: 1, max: 365, doc: 'How long recordings are kept.' },
    ],
  },

  {
    id: 'alerts',
    title: 'Alerts',
    doc: 'Where operational warnings are delivered. Leave disabled until a channel is configured.',
    fields: [
      { path: 'alerts.telegram.enabled', label: 'telegram.enabled', type: 'bool', doc: 'Send alerts to the operator chat.' },
      { path: 'alerts.telegram.chat_id', label: 'telegram.chat_id', type: 'string', doc: 'Chat that receives alerts. The bot token lives in credentials.' },
      { path: 'alerts.email.enabled', label: 'email.enabled', type: 'bool', doc: 'Send alerts by email.' },
    ],
  },

  {
    id: 'api',
    title: 'API',
    doc: 'The HTTP and websocket server this console talks to. Every change here needs a restart.',
    structural: true,
    fields: [
      { path: 'api.host', label: 'host', type: 'string', doc: 'Bind to 127.0.0.1 when running behind a reverse proxy, so the API is never directly exposed.' },
      { path: 'api.port', label: 'port', type: 'int', min: 1024, max: 65535, doc: 'Listen port.' },
      { path: 'api.ws_push_interval_ms', label: 'ws_push_interval_ms', type: 'int', min: 100, max: 5000, unit: 'ms', doc: 'How often diffs are pushed to this console.' },
      { path: 'api.cors_origins', label: 'cors_origins', type: 'list', doc: 'Every frontend origin, one per line. An unlisted origin is blocked by the browser and looks exactly like the backend being down.' },
    ],
  },

  {
    id: 'system',
    title: 'System',
    doc: 'Process-level settings. All schedule times are interpreted in this timezone.',
    structural: true,
    fields: [
      { path: 'system.timezone', label: 'timezone', type: 'string', doc: 'IANA zone name. Everything in Schedule is read in this zone.' },
      { path: 'system.data_dir', label: 'data_dir', type: 'string', doc: 'Root directory for recordings and state.' },
      { path: 'system.log_level', label: 'log_level', type: 'enum', options: ['DEBUG', 'INFO', 'WARN', 'ERROR'], doc: 'Minimum level written to the log stream.' },
      { path: 'system.retention_days', label: 'retention_days', type: 'int', min: 1, max: 365, doc: 'How long state and logs are kept.' },
    ],
  },

  { id: 'raw', title: 'Raw', custom: 'raw' },
]

/**
 * Credentials are deliberately not reachable over the API: credentials.json is
 * gitignored, chmod 600 on the host, and the backend never returns it. So the
 * Credentials tab documents the shape and offers a live round trip, rather than
 * pretending to edit values it cannot read.
 */
export const CREDENTIAL_GROUPS: { title: string; file: string; rows: [string, string][] }[] = [
  {
    title: 'Zerodha Kite',
    file: 'credentials.json → zerodha',
    rows: [
      ['api_key', 'Kite Connect API key from the developer console. Also mirrored in config.broker.api_key.'],
      ['api_secret', 'Paired secret, used once a day to exchange the request token.'],
      ['user_id', 'Kite login ID used for the daily session exchange.'],
      ['password', 'Account password, used only by the automated login.'],
      ['totp_key', 'Base32 seed for the two-factor code.'],
    ],
  },
  {
    title: 'Alerts',
    file: 'credentials.json → telegram',
    rows: [
      ['bot_token', 'Bot token for operator alerts. The chat id is in Settings → Alerts.'],
    ],
  },
]

/** Nav icons, drawn as a single stroked path each. */
export const ICONS: Record<string, string> = {
  dashboard: 'M2 2.8h4.6v4.6H2zM9.4 2.8H14v4.6H9.4zM2 9.4h4.6V14H2zM9.4 9.4H14V14H9.4z',
  positions: 'M2 4h12M2 8h12M2 12h7',
  live: 'M1.5 9h3l2-5 2.5 10L11.5 9h3',
  status: 'M8 2a6 6 0 1 0 6 6M8 8l3.2-2.2',
  strategy: 'M3 4h10M3 8h10M3 12h10',
  settings: 'M8 5.8A2.2 2.2 0 1 0 8 10.2 2.2 2.2 0 1 0 8 5.8M8 1.6v1.4M8 13v1.4M14.4 8H13M3 8H1.6M12.5 3.5l-1 1M4.5 11.5l-1 1M12.5 12.5l-1-1M4.5 4.5l-1-1',
  logs: 'M3.5 4.5L6 7l-2.5 2.5M8 11h4.5',
}

/** Human meaning for each phase, shown next to the phase pill in the topbar. */
export const PHASE_MEANING: Record<string, string> = {
  BOOT: 'idle until the next session',
  PHASE_1: 'authenticating, downloading contracts',
  PHASE_1_FAIL: 'pre-market checks failed — no trading today',
  FEED_LIVE: 'feed connected, recording',
  PREOPEN: 'equity auction being recorded',
  SETTLEMENT: 'ranking being computed',
  ARMING: 'option chains subscribing',
  FROZEN: 'instrument set locked',
  TRADING: 'entries live',
  MANAGING: 'holding, exits armed',
  EOD: 'squaring off',
  IDLE: 'waiting for tomorrow',
}

/** Which schedule key marks the start of each phase, for the session sequence. */
export const PHASE_SCHEDULE: Record<string, string> = {
  PHASE_1: 'phase1_time',
  FEED_LIVE: 'feed_connect_time',
  PREOPEN: 'preopen_start',
  SETTLEMENT: 'settlement_snapshot',
  ARMING: 'wave2_subscribe_time',
  FROZEN: 'manual_cutoff',
  TRADING: 'trading_start',
  EOD: 'eod_time',
}
