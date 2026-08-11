/**
 * Formatting. Every number the operator reads goes through here.
 *
 * Conventions the backend forces on us:
 *   - all times are IST
 *   - `at_us` fields are epoch MICROseconds
 *   - `logs[].ts` is already "HH:MM:SS" with no date
 *   - bid/ask are 0 when the book is empty (market closed) -> render as em dash
 *   - feed_lag can be legitimately hours-large when the market is shut
 */

const inr = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const plain = new Intl.NumberFormat('en-IN')

export const DASH = '—'

/** Indian-grouped rupees: 1,24,530.50 */
export function money(v: number | null | undefined, { sign = false } = {}): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH
  const s = inr.format(Math.abs(v))
  const pre = v < 0 ? '-' : sign && v > 0 ? '+' : ''
  return `${pre}₹${s}`
}

/** A price. 0 means "no book" for bid/ask, so callers pass zeroIsDash. */
export function price(v: number | null | undefined, { zeroIsDash = false } = {}): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH
  if (zeroIsDash && v === 0) return DASH
  return inr.format(v)
}

export function pct(v: number | null | undefined, { sign = true, dp = 2 } = {}): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH
  const s = Math.abs(v).toFixed(dp)
  const pre = v < 0 ? '-' : sign && v > 0 ? '+' : ''
  return `${pre}${s}%`
}

export function int(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH
  return plain.format(v)
}

/** Sign class for P&L and change columns. Neutral at exactly zero. */
export function signClass(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v) || v === 0) return 'text-ink'
  return v > 0 ? 'text-pos' : 'text-neg'
}

/** Microseconds -> "380µs" / "1.05ms" / "1.05s". */
export function micros(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH
  const a = Math.abs(v)
  const sgn = v < 0 ? '-' : ''
  if (a < 1000) return `${sgn}${Math.round(a)}µs`
  if (a < 1_000_000) return `${sgn}${(a / 1000).toFixed(2)}ms`
  return `${sgn}${(a / 1_000_000).toFixed(2)}s`
}

export function millis(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH
  return v < 1000 ? `${v.toFixed(2)}ms` : `${(v / 1000).toFixed(2)}s`
}

/** Human duration from milliseconds. Used for last_tick_age_ms, which can be huge. */
export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return DASH
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${s % 60}s`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ${m % 60}m`
  return `${Math.floor(h / 24)}d ${h % 24}h`
}

export function seconds(s: number | null | undefined): string {
  return duration(s === null || s === undefined ? null : s * 1000)
}

export function bytes(b: number | null | undefined): string {
  if (b === null || b === undefined || !Number.isFinite(b)) return DASH
  if (b < 1024) return `${b} B`
  if (b < 1024 ** 2) return `${(b / 1024).toFixed(1)} KB`
  if (b < 1024 ** 3) return `${(b / 1024 ** 2).toFixed(1)} MB`
  return `${(b / 1024 ** 3).toFixed(2)} GB`
}

const IST = 'Asia/Kolkata'

/** epoch microseconds -> HH:MM:SS IST */
export function timeFromUs(us: number | null | undefined): string {
  if (!us) return DASH
  return new Date(us / 1000).toLocaleTimeString('en-GB', { timeZone: IST, hour12: false })
}

/** ISO string -> HH:MM:SS (today) or "DD MMM HH:MM" */
export function timeFromIso(iso: string | null | undefined): string {
  if (!iso) return DASH
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return String(iso)
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  return sameDay
    ? d.toLocaleTimeString('en-GB', { timeZone: IST, hour12: false })
    : d.toLocaleString('en-GB', { timeZone: IST, day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false })
}

export function clockIst(d = new Date()): string {
  return d.toLocaleTimeString('en-GB', { timeZone: IST, hour12: false })
}

/** Human label for a config path segment: "top_n_gainers" -> "Top n gainers" */
export function humanise(key: string): string {
  const s = key.replace(/_/g, ' ').trim()
  return s.charAt(0).toUpperCase() + s.slice(1)
}
