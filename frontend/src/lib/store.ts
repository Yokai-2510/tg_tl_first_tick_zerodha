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
  api, auth, ApiError, WS_URL,
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

  signIn: (token: string) => Promise<void>
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
  ready: false,
  status: null, universe: null, ranking: [], market: {},
  positions: [], closed: [], orders: [], signals: [],
  latency: { trades: [], median_tick_to_fill_ms: null },
  events: [], logs: [], cfg: null,
  link: 'connecting', lastUpdate: 0, error: null,

  setError: (error) => set({ error }),

  async signIn(token) {
    auth.set(token)
    set({ token })
    await api.status()              // throws 401 if the token is wrong
    await get().bootstrap()
  },

  signOut() {
    get().disconnect()
    auth.clear()
    set({ token: null, ready: false, status: null })
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

function applyFrame(f: any, set: any, get: () => State) {
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

    case 'orders':
      set({ orders: [data as OrderRow, ...get().orders].slice(0, 500) })
      break

    case 'events':
      set({ events: [...get().events, data as EventRow].slice(-500) })
      break

    case 'logs':
      set({ logs: [...get().logs, data as LogRow].slice(-500) })
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
