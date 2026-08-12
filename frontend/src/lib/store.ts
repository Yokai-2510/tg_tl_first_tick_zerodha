/**
 * Single application store.
 *
 * Data arrives two ways and must converge on the same shape:
 *   - REST poll (always works)
 *   - WebSocket snapshot/diff (efficient; falls back to polling after 3 failures)
 *
 * `market` and `positions` arrive as DIFFS and must be merged, never replaced.
 */

import { create } from 'zustand'
import {
  api, auth, ApiError, WS_URL, login,
  type ConfigPayload, type EventRow, type LatencyRow, type LogRow,
  type MarketRow, type OrderRow, type Position, type RankRow,
  type SignalRow, type Status, type Universe,
} from './api'

export type Link = 'connecting' | 'live' | 'polling' | 'down'

interface State {
  token: string | null
  ready: boolean

  status: Status | null
  universe: Universe | null
  ranking: RankRow[]
  market: Record<string, MarketRow>
  positions: Position[]
  closed: Position[]
  orders: OrderRow[]
  signals: SignalRow[]
  latency: { trades: LatencyRow[]; median_tick_to_fill_ms: number | null }
  events: EventRow[]
  logs: LogRow[]
  cfg: ConfigPayload | null

  link: Link
  lastUpdate: number
  error: string | null

  username: string | null
  signIn: (username: string, password: string) => Promise<void>
  signInWithToken: (token: string) => Promise<void>
  signOut: () => void
  bootstrap: () => Promise<void>
  refresh: (what?: 'all' | 'positions' | 'universe' | 'config' | 'audit') => Promise<void>
  connect: () => void
  disconnect: () => void
  setError: (e: string | null) => void
}

let ws: WebSocket | null = null
let wsFails = 0
let pollTimer: number | null = null
let retryTimer: number | null = null

export const useStore = create<State>((set, get) => ({
  token: auth.get(),
  username: (() => { try { return sessionStorage.getItem('ft.user') } catch { return null } })(),
  ready: false,
  status: null, universe: null, ranking: [], market: {},
  positions: [], closed: [], orders: [], signals: [],
  latency: { trades: [], median_tick_to_fill_ms: null },
  events: [], logs: [], cfg: null,
  link: 'connecting', lastUpdate: 0, error: null,

  setError: (error) => set({ error }),

  async signIn(username, password) {
    // Any half-finished session must go before we try a new one, or a stale
    // token could be sent with the login request.
    auth.clear()
    const res = await login(username, password)
    auth.set(res.token)
    set({ token: res.token, username: res.username })
    try { sessionStorage.setItem('ft.user', res.username) } catch { /* private mode */ }
    await get().bootstrap()
  },

  /** Kept for the api_token path: scripts, and a first run before accounts exist. */
  async signInWithToken(token) {
    auth.set(token)
    set({ token, username: null })
    await api.status()              // throws 401 if the token is wrong
    await get().bootstrap()
  },

  signOut() {
    get().disconnect()
    auth.clear()
    try { sessionStorage.removeItem('ft.user') } catch { /* private mode */ }
    set({ token: null, username: null, ready: false, status: null })
  },

  async bootstrap() {
    try {
      const [status, cfg] = await Promise.all([api.status(), api.config()])
      set({ status, cfg, ready: true, lastUpdate: Date.now(), error: null })
      await get().refresh('all')
      get().connect()
    } catch (e) {
      set({ error: e instanceof ApiError ? e.message : String(e), ready: false })
    }
  },

  async refresh(what = 'all') {
    const jobs: Promise<void>[] = []
    const grab = <T,>(p: Promise<T>, apply: (v: T) => void) =>
      jobs.push(p.then(apply).catch(() => {}))

    if (what === 'all') grab(api.status(), (status) => set({ status }))
    if (what === 'all' || what === 'positions') {
      grab(api.positions(), (positions) => set({ positions }))
      grab(api.positionsClosed(), (closed) => set({ closed }))
    }
    if (what === 'all' || what === 'universe') {
      grab(api.universe(), (universe) => set({ universe }))
      grab(api.ranking(), (r) => set({ ranking: r.ranked }))
      grab(api.market(), (market) => set({ market }))
    }
    if (what === 'all' || what === 'audit') {
      grab(api.orders(), (orders) => set({ orders }))
      grab(api.signals(), (signals) => set({ signals }))
      grab(api.latency(), (latency) => set({ latency }))
      grab(api.events(), (events) => set({ events }))
      grab(api.logs(), (logs) => set({ logs }))
    }
    if (what === 'config') grab(api.config(), (cfg) => set({ cfg }))
    await Promise.all(jobs)
    set({ lastUpdate: Date.now() })
  },

  connect() {
    if (!get().token) return
    get().disconnect()
    set({ link: 'connecting' })

    api.wsTicket()
      .then(({ ticket }) => {
        const sock = new WebSocket(`${WS_URL}?token=${encodeURIComponent(ticket)}`)
        ws = sock

        sock.onopen = () => {
          wsFails = 0
          stopPolling()
          set({ link: 'live' })
          sock.send(JSON.stringify({
            op: 'subscribe',
            topics: ['status', 'market', 'positions', 'orders', 'events', 'logs'],
          }))
        }

        sock.onmessage = (ev) => {
          let f: any
          try { f = JSON.parse(ev.data as string) } catch { return }
          if (f.op === 'pong') return
          applyFrame(f, set, get)
          set({ lastUpdate: Date.now() })
        }

        sock.onclose = () => { ws = null; scheduleRetry(set, get) }
        sock.onerror = () => { /* onclose always follows */ }
      })
      .catch(() => scheduleRetry(set, get))
  },

  disconnect() {
    if (retryTimer) { clearTimeout(retryTimer); retryTimer = null }
    stopPolling()
    if (ws) { ws.onclose = null; ws.close(); ws = null }
  },
}))

// ---------------------------------------------------------------- frames

export function applyFrame(f: any, set: any, get: () => State) {
  const { topic, type, data } = f
  if (!topic) return

  switch (topic) {
    case 'status':
      set({ status: data as Status })
      break

    case 'market': {
      // diffs are partial: merge by token, never replace the map
      const next = { ...get().market }
      for (const [token, row] of Object.entries(data as Record<string, Partial<MarketRow>>)) {
        next[token] = {
          ...(next[token] ?? { sym: null, ltp: 0, bid: 0, ask: 0, volume: 0, oi: 0, feed_lag_us: null }),
          ...row,
        }
      }
      set({ market: next })
      break
    }

    case 'positions': {
      if (type === 'snapshot' || (data as any).upsert || (data as any).remove) {
        const d = data as { upsert?: Position[]; remove?: string[] }
        const byId = new Map(get().positions.map((p) => [p.pos_id, p]))
        for (const p of d.upsert ?? []) byId.set(p.pos_id, p)
        for (const id of d.remove ?? []) byId.delete(id)
        set({ positions: [...byId.values()] })
      }
      break
    }

    // These three arrive BOTH ways: the initial snapshot is the whole list, then
    // each later frame is one row. Treating a snapshot as a single row pushed the
    // array itself in as one entry -- which is why the Orders tab showed a count
    // of 1 and rendered `function at() { [native code] }`: the "row" was an array,
    // and `row.at` resolved to Array.prototype.at. Array.isArray settles it
    // regardless of what `type` says.
    case 'orders':
      set({
        orders: Array.isArray(data)
          // The backend sends oldest-first; the table is newest-first, and later
          // frames are prepended, so a snapshot has to be reversed to match.
          ? ([...(data as OrderRow[])].reverse()).slice(0, 500)
          : [data as OrderRow, ...get().orders].slice(0, 500),
      })
      break

    case 'events':
      set({
        events: Array.isArray(data)
          ? (data as EventRow[]).slice(-500)
          : [...get().events, data as EventRow].slice(-500),
      })
      break

    case 'logs':
      set({
        logs: Array.isArray(data)
          ? (data as LogRow[]).slice(-500)
          : [...get().logs, data as LogRow].slice(-500),
      })
      break
  }
}

// ---------------------------------------------------------------- fallback

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

function startPolling(set: any, get: () => State) {
  if (pollTimer) return
  set({ link: 'polling' })
  pollTimer = window.setInterval(() => {
    get().refresh('all').catch(() => {})
  }, 1000)
}

/**
 * Exponential backoff 1s -> 30s. After 3 consecutive failures we start polling
 * so the console stays usable — the WebSocket is an efficiency layer, not a
 * requirement.
 */
function scheduleRetry(set: any, get: () => State) {
  wsFails += 1
  if (wsFails >= 3) startPolling(set, get)
  else set({ link: 'down' })

  const delay = Math.min(1000 * 2 ** (wsFails - 1), 30_000)
  if (retryTimer) clearTimeout(retryTimer)
  retryTimer = window.setTimeout(() => { if (get().token) get().connect() }, delay)
}
