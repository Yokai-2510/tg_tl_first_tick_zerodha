/**
 * The v3 surface in one place.
 *
 * Every screen is styled with inline style objects that read the CSS custom
 * properties from index.css. That is deliberate: a token change in one file
 * restyles the whole console, the operator's accent is a live variable rather
 * than a rebuilt class, and there is no cascade to reason about while reading a
 * screen. The helpers below are the shared vocabulary — card, head, pill,
 * segmented control, chip, sign colour, status tone.
 */
import type { CSSProperties } from 'react'

export const V = {
  page: 'var(--page)',
  card: 'var(--card)',
  sunken: 'var(--sunken)',
  border: 'var(--border)',
  border2: 'var(--border2)',
  text: 'var(--text)',
  muted: 'var(--muted)',
  faint: 'var(--faint)',
  pos: 'var(--pos)',
  neg: 'var(--neg)',
  warn: 'var(--warn)',
  posbg: 'var(--posbg)',
  negbg: 'var(--negbg)',
  warnbg: 'var(--warnbg)',
  chip: 'var(--chip)',
  shadow: 'var(--shadow)',
  accent: 'var(--accent)',
} as const

export const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace'

export const card: CSSProperties = {
  border: `1px solid ${V.border}`,
  borderRadius: 18,
  background: V.card,
  boxShadow: V.shadow,
  minWidth: 0,
}

/** Uppercase micro-label above a KPI. */
export const label: CSSProperties = {
  fontSize: 12,
  letterSpacing: '.06em',
  textTransform: 'uppercase',
  color: V.muted,
}

/** Smaller variant used inside expanded rows and table heads. */
export const label10: CSSProperties = {
  fontSize: 10,
  letterSpacing: '.08em',
  textTransform: 'uppercase',
  color: V.muted,
}

export const th: CSSProperties = {
  fontSize: 11,
  letterSpacing: '.05em',
  textTransform: 'uppercase',
  color: V.muted,
  background: V.sunken,
}

export const cardTitle: CSSProperties = { fontSize: 14, fontWeight: 600, letterSpacing: '-.015em' }
export const cardTitleLg: CSSProperties = { fontSize: 15, fontWeight: 600, letterSpacing: '-.015em' }
export const cardSub: CSSProperties = { fontSize: 12, color: V.muted, marginTop: 4, lineHeight: 1.55 }

export const ellip: CSSProperties = { whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }
export const mono: CSSProperties = { fontFamily: MONO }
export const num: CSSProperties = { textAlign: 'right' }

export const inputStyle: CSSProperties = {
  padding: '9px 12px',
  border: `1px solid ${V.border2}`,
  borderRadius: 10,
  background: V.sunken,
  fontSize: 12,
  outline: 'none',
  minWidth: 0,
}

export const btn: CSSProperties = {
  padding: '9px 15px',
  borderRadius: 10,
  border: `1px solid ${V.border2}`,
  background: V.card,
  color: V.text,
  fontSize: 12,
  fontWeight: 500,
  boxShadow: V.shadow,
}

export const btnPrimary: CSSProperties = {
  padding: '9px 17px',
  borderRadius: 10,
  border: 'none',
  background: V.accent,
  color: '#fff',
  fontSize: 12,
  fontWeight: 600,
}

export const btnDanger: CSSProperties = {
  padding: '9px 16px',
  borderRadius: 10,
  border: 'none',
  background: V.neg,
  color: '#fff',
  fontSize: 12,
  fontWeight: 600,
}

/** Segmented control: the selected pill lifts off a sunken track. */
export function seg(active: boolean): CSSProperties {
  return {
    padding: '7px 15px',
    borderRadius: 9,
    border: 'none',
    background: active ? V.card : 'transparent',
    color: active ? V.text : V.muted,
    fontSize: 12,
    fontWeight: active ? 600 : 500,
    boxShadow: active ? V.shadow : 'none',
    whiteSpace: 'nowrap',
  }
}

export const segTrack: CSSProperties = {
  display: 'flex',
  gap: 3,
  padding: 4,
  borderRadius: 12,
  background: V.chip,
  width: 'fit-content',
  flexWrap: 'wrap',
}

/** Outlined filter chip: accent border when on. */
export function chip(active: boolean): CSSProperties {
  return {
    padding: '6px 12px',
    borderRadius: 9,
    border: `1px solid ${active ? V.accent : V.border}`,
    background: active ? V.chip : V.card,
    color: active ? V.text : V.muted,
    fontSize: 11,
    fontWeight: 500,
    whiteSpace: 'nowrap',
  }
}

export function pill(bg: string, fg: string): CSSProperties {
  return {
    fontSize: 11,
    fontWeight: 600,
    padding: '3px 10px',
    borderRadius: 7,
    background: bg,
    color: fg,
    whiteSpace: 'nowrap',
  }
}

export function badge(bg: string, fg: string): CSSProperties {
  return {
    display: 'inline-block',
    padding: '3px 9px',
    borderRadius: 7,
    fontSize: 10,
    fontWeight: 600,
    letterSpacing: '.04em',
    background: bg,
    color: fg,
  }
}

/** Sign colour for P&L and change columns. Neutral at exactly zero. */
export function tone(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v) || v === 0) return V.muted
  return v > 0 ? V.pos : V.neg
}

const STATUS: Record<string, [string, string]> = {
  ACTIVE: [V.posbg, V.pos],
  OPEN: [V.warnbg, V.warn],
  EXITING: [V.warnbg, V.warn],
  CLOSED: [V.chip, V.muted],
  FAILED: [V.negbg, V.neg],
  REJECTED: [V.negbg, V.neg],
  COMPLETE: [V.posbg, V.pos],
  CANCELLED: [V.chip, V.muted],
  ADOPTED_UNMANAGED: [V.chip, V.text],
}

export function statusTone(s: string | null | undefined): CSSProperties {
  const [bg, fg] = STATUS[(s ?? '').toUpperCase()] ?? STATUS.CLOSED
  return badge(bg, fg)
}

/** An SVG path for a sparkline across w×h. Flat when there is nothing to plot. */
export function spark(vals: number[], w: number, h: number): string {
  if (vals.length < 2) return `M0 ${(h / 2).toFixed(1)} L${w} ${(h / 2).toFixed(1)}`
  const mn = Math.min(...vals)
  const mx = Math.max(...vals)
  const span = mx - mn || 1
  return vals
    .map((v, i) => {
      const x = (i / (vals.length - 1)) * w
      const y = h - 2 - ((v - mn) / span) * (h - 6)
      return `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`
    })
    .join(' ')
}

/** Split "₹1,24,530.50" into ["₹1,24,530", ".50"] so the paise can be dimmed. */
export function cut(s: string): [string, string] {
  const i = s.lastIndexOf('.')
  return i < 0 ? [s, ''] : [s.slice(0, i), s.slice(i)]
}

export function pctWidth(part: number, whole: number): string {
  if (!whole || !Number.isFinite(whole)) return '0%'
  return `${Math.max(0, Math.min(100, (part / whole) * 100)).toFixed(1)}%`
}
