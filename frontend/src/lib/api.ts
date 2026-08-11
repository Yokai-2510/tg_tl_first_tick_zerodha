/**
 * REST client for the First-Tick backend.
 *
 * Every response is wrapped in { ok, data, ts } / { ok:false, error }. This
 * module unwraps `data` and throws a typed ApiError otherwise, so callers never
 * deal with the envelope.
 */

export const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string) ?? 'http://127.0.0.1:8080/api/v1'
export const WS_URL: string =
  (import.meta.env.VITE_WS_URL as string) ?? 'ws://127.0.0.1:8080/api/v1/ws'

const TOKEN_KEY = 'ft.token'

export const auth = {
  get: () => sessionStorage.getItem(TOKEN_KEY),
  set: (t: string) => sessionStorage.setItem(TOKEN_KEY, t),
  clear: () => sessionStorage.removeItem(TOKEN_KEY),
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly detail?: unknown,
  ) {
    super(message)
  }
  /** 409 — the action is not legal in the current phase. */
  get isIllegalState() { return this.status === 409 }
  /** 422 — config failed validation; `message` names the JSON path. */
  get isConfigInvalid() { return this.status === 422 }
}

/** Fires on 401 so the app can drop back to the token screen. */
type UnauthorizedHandler = () => void
let onUnauthorized: UnauthorizedHandler = () => {}
export const setUnauthorizedHandler = (fn: UnauthorizedHandler) => { onUnauthorized = fn }

async function request<T>(
  path: string,
  init: RequestInit = {},
  { withAuth = true }: { withAuth?: boolean } = {},
): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body) headers.set('Content-Type', 'application/json')
  const token = auth.get()
  if (withAuth && token) headers.set('Authorization', `Bearer ${token}`)

  let res: Response
  try {
    res = await fetch(`${API_BASE}${path}`, { ...init, headers })
  } catch (e) {
    // Network failure, DNS, or a CORS rejection — indistinguishable in the browser.
    throw new ApiError(0, 'NETWORK',
      'Cannot reach the backend. Check connectivity, or that this origin is in the ' +
      'backend api.cors_origins allow-list.', e)
  }

  const text = await res.text()
  let body: any = null
  try { body = text ? JSON.parse(text) : null } catch { /* non-JSON error page */ }

  if (res.status === 401) {
    auth.clear()
    onUnauthorized()
    throw new ApiError(401, 'AUTH_INVALID', 'Token rejected.')
  }
  if (!res.ok || body?.ok === false) {
    const err = body?.error
    throw new ApiError(res.status, err?.code ?? 'INTERNAL',
      err?.message ?? `Request failed (HTTP ${res.status})`, err?.detail)
  }
  return (body?.data ?? body) as T
}

const get = <T,>(p: string) => request<T>(p)
const post = <T,>(p: string, body?: unknown) =>
  request<T>(p, { method: 'POST', body: body === undefined ? '' : JSON.stringify(body) })

// ---------------------------------------------------------------- types

export type Phase =
  | 'BOOT' | 'PHASE_1' | 'PHASE_1_FAIL' | 'FEED_LIVE' | 'PREOPEN' | 'SETTLEMENT'
  | 'ARMING' | 'FROZEN' | 'TRADING' | 'MANAGING' | 'EOD' | 'IDLE'

export const PHASE_ORDER: Phase[] = [
  'BOOT', 'PHASE_1', 'FEED_LIVE', 'PREOPEN', 'SETTLEMENT',
  'ARMING', 'FROZEN', 'TRADING', 'MANAGING', 'EOD', 'IDLE',
]

export interface FeedStats {
  connected: boolean; subscribed: number; modes: Record<string, number>
  ticks: number; batches: number; order_events: number
  reconnects: number; gaps: number
  last_tick_age_ms: number | null; last_error: string | null
}
export interface EngineStats {
  phase: string; entries_enabled: boolean; armed: number; fired: number
  signals: number; ticks_seen: number; tracked_instruments: number; intent_queue: number
}
export interface RecorderStats {
  enabled: boolean; running: boolean; queue_depth: number; ticks: number
  events: number; bytes: number; dropped: number; batches: number
  disk_full: boolean; dir: string; compression: string
}
export interface PositionsSummary {
  open: number; closed: number; failed: number; adopted: number
  unrealised: number; realised: number; charges: number
}
export interface Bucket { rejected: number; [k: string]: number }
export interface Capital {
  available: number; used: number; total: number; deployed_pct: number
  opening_balance: number; payin: number; net: number
  breakdown: { debits: number; span: number; exposure: number; option_premium: number }
  simulated?: boolean
}
export interface Status {
  phase: Phase; entries_allowed: boolean; last_error: string | null
  schedule: Record<string, string>
  history: { from: string; to: string; at_us: number }[]
  mode: 'paper' | 'live'; halted: boolean; uptime_s: number
  feed: FeedStats; engine: EngineStats; recorder: RecorderStats
  positions: PositionsSummary
  capital: Capital
  rate_limits: Record<string, Bucket>
  ws_clients: number; server_time: string
}

export interface ArmedRow {
  token: number; symbol: string; underlying: string
  ref_price: number; lots: number; fired: boolean; ltp: number
}
export interface Universe {
  nifty50: string[]; indices: string[]; tradeable: string[]; buffer: string[]
  subscribed: number; armed: ArmedRow[]
}
export interface RankRow {
  rank: number; symbol: string; ltp: number; prev_close: number
  change_pct: number; selected: boolean
  volume?: number; open?: number; high?: number; low?: number; buffer?: boolean
}
export interface MarketRow {
  sym: string | null; underlying?: string | null
  ltp: number; bid: number; ask: number
  volume?: number; oi?: number
  feed_lag_us: number | null
}
export interface Position {
  pos_id: string; status: string; mode: string
  instrument: {
    token: number; tradingsymbol: string; underlying: string
    option_type: string | null; strike: number; expiry: string | null
    lot_size: number; exchange: string
  }
  lots: number; quantity: number; sig_id: string | null
  entry: { order_id: string | null; price: number; filled_qty: number; at_us: number; ref_price: number; diff: number }
  exit: { order_id: string | null; price: number; filled_qty: number; at_us: number; trigger: string | null }
  live: { ltp: number; bid: number; ask: number; pnl: number; pnl_pct: number; max_pnl_pct: number; min_pnl_pct: number; holding_seconds: number }
  trailing: { sl_active: boolean; sl_peak: number; sl_level: number; tgt_active: boolean; tgt_peak: number; tgt_level: number }
  flags: { exiting: boolean; broker_confirmed: boolean; reconciled: boolean }
  charges: number
}
export interface OrderRow {
  pos_id: string | null; sym: string; role: string; side: string
  qty: number; price: number; attempt: number; order_id: string | null
  status: string | null; rejection: string | null; message: string | null; at: string
}
export interface SignalRow {
  sig_id: string; sym: string; diff: number; ref: number
  price: number; ask: number; at: string
}
export interface LatencyRow {
  sig_id: string; sym: string; tick_to_signal_us: number
  signal_to_req_ms: number; req_to_ack_ms: number
  ack_to_fill_ms: number; total_tick_to_fill_ms: number
}
export interface LogRow { level: string; module: string; msg: string; ts: string }
export type EventRow = { kind: string; at: string } & Record<string, unknown>
export interface ConfigPayload { config: Record<string, any>; schema: Record<string, any> }

// ---------------------------------------------------------------- endpoints

export const api = {
  health: () => request<{ status: string; uptime_s: number; version: string; phase: Phase }>(
    '/health', {}, { withAuth: false }),

  status: () => get<Status>('/status'),
  universe: () => get<Universe>('/universe'),
  ranking: () => get<{ ranked: RankRow[] }>('/universe/ranking'),
  market: () => get<Record<string, MarketRow>>('/market/snapshot'),
  positions: () => get<Position[]>('/positions'),
  positionsClosed: () => get<Position[]>('/positions/closed'),
  orders: () => get<OrderRow[]>('/orders'),
  signals: () => get<SignalRow[]>('/signals'),
  latency: () => get<{ trades: LatencyRow[]; median_tick_to_fill_ms: number | null }>('/latency'),
  recorderStats: () => get<RecorderStats>('/recorder/stats'),
  events: (limit = 200) => get<EventRow[]>(`/events?limit=${limit}`),
  logs: (limit = 300) => get<LogRow[]>(`/logs?limit=${limit}`),
  config: () => get<ConfigPayload>('/config'),

  patchConfig: (patch: unknown) => post<{ changed: string[] }>('/config', patch),
  validateConfig: (patch: unknown) => post<{ valid: boolean }>('/config/validate', patch),

  manualAdd: (symbol: string, lots?: number) =>
    post<{ manual_instruments: unknown[] }>('/universe/manual',
      { action: 'add', symbol, ...(lots ? { lots } : {}) }),
  manualRemove: (symbol: string) =>
    post<{ manual_instruments: unknown[] }>('/universe/manual', { action: 'remove', symbol }),

  arm: () => post<{ armed: boolean }>('/control/arm'),
  disarm: () => post<{ armed: boolean }>('/control/disarm'),
  restart: () => post<unknown>('/control/restart'),
  forcePhase: (phase: Phase) => post<{ phase: string }>('/control/phase', { phase }),
  exitPosition: (posId: string) => post<{ pos_id: string; exiting: boolean }>(`/positions/${posId}/exit`),
  exitAll: () => post<{ exiting: number }>('/control/exit_all'),
  killSwitch: () => post<{ halted: boolean; exiting: number }>('/control/kill_switch', { confirm: 'KILL' }),
  reconcile: () => post<Record<string, string[]>>('/control/reconcile'),
  wsTicket: () => post<{ ticket: string; expires_in: number }>('/auth/ws-ticket'),
}
