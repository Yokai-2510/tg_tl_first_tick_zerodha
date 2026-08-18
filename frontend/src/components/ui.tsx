import { Component, useEffect, useState, type CSSProperties, type ReactNode } from 'react'
import {
  V, MONO, card, cardTitle, cardTitleLg, cardSub, label, seg, segTrack, chip, pill,
  btn, btnPrimary, btnDanger, ellip, spark,
} from '../lib/style'
import { useToasts } from '../lib/toast'

/* ------------------------------------------------------------------ surfaces */

export function Card({ children, style, pad = '22px 24px' }:
  { children: ReactNode; style?: CSSProperties; pad?: CSSProperties['padding'] | false }) {
  return <div style={{ ...card, ...(pad === false ? {} : { padding: pad }), ...style }}>{children}</div>
}

export function CardHead({ title, sub, right, big = false, style }:
  { title: ReactNode; sub?: ReactNode; right?: ReactNode; big?: boolean; style?: CSSProperties }) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16, ...style }}>
      <div style={{ minWidth: 0 }}>
        <div style={big ? cardTitleLg : cardTitle}>{title}</div>
        {sub ? <div style={cardSub}>{sub}</div> : null}
      </div>
      {right ? <div style={{ flex: 'none' }}>{right}</div> : null}
    </div>
  )
}

/** Uppercase micro-label + value, the KPI head used across the dashboard. */
export function StatHead({ title, right }: { title: ReactNode; right?: ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
      <div style={{ ...label, ...ellip }}>{title}</div>
      {right}
    </div>
  )
}

export function Pill({ children, bg = V.chip, fg = V.muted }:
  { children: ReactNode; bg?: string; fg?: string }) {
  return <span style={pill(bg, fg)}>{children}</span>
}

/* ------------------------------------------------------------------ controls */

export function Segmented<T extends string>({ options, value, onChange, style }: {
  options: { key: T; label: string; count?: ReactNode }[]
  value: T
  onChange: (key: T) => void
  style?: CSSProperties
}) {
  return (
    <div style={{ ...segTrack, ...style }}>
      {options.map((o) => (
        <button key={o.key} onClick={() => onChange(o.key)} style={seg(o.key === value)}>
          {o.label}
          {o.count !== undefined && o.count !== null
            ? <span style={{ color: V.faint }}> {o.count}</span> : null}
        </button>
      ))}
    </div>
  )
}

export function ChipRow<T extends string>({ options, value, onChange }: {
  options: { key: T; label: string; count?: ReactNode }[]
  value: T
  onChange: (key: T) => void
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
      {options.map((o) => (
        <button key={o.key} onClick={() => onChange(o.key)} style={chip(o.key === value)}>
          {o.label}
          {o.count !== undefined && o.count !== null
            ? <span style={{ color: V.faint }}> {o.count}</span> : null}
        </button>
      ))}
    </div>
  )
}

export function Toggle({ on, onChange, disabled = false, size = 'md' }:
  { on: boolean; onChange: (next: boolean) => void; disabled?: boolean; size?: 'sm' | 'md' }) {
  const w = size === 'sm' ? 34 : 38
  const h = size === 'sm' ? 20 : 22
  const k = size === 'sm' ? 14 : 16
  return (
    <button
      onClick={() => !disabled && onChange(!on)}
      aria-pressed={on}
      style={{
        width: w, height: h, borderRadius: h / 2, flex: 'none', padding: 2,
        border: `1px solid ${on ? V.accent : V.border2}`,
        background: on ? V.accent : V.chip,
        display: 'flex', alignItems: 'center',
        justifyContent: on ? 'flex-end' : 'flex-start',
        opacity: disabled ? 0.45 : 1,
        transition: 'background .12s',
      }}>
      <span style={{ width: k, height: k, borderRadius: '50%', background: on ? '#fff' : V.muted }} />
    </button>
  )
}

export function Stepper({ value, onChange, min = 0, max = 99, disabled = false }:
  { value: number; onChange: (n: number) => void; min?: number; max?: number; disabled?: boolean }) {
  const clamp = (n: number) => Math.max(min, Math.min(max, n))
  const b: CSSProperties = {
    width: 30, height: 30, borderRadius: 9, border: `1px solid ${V.border2}`,
    background: V.card, color: V.text, fontSize: 15, lineHeight: 1,
    display: 'grid', placeItems: 'center',
  }
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
      <button style={b} disabled={disabled} onClick={() => onChange(clamp(value - 1))}>−</button>
      <div style={{
        fontSize: 30, fontWeight: 600, letterSpacing: '-.035em', fontFamily: MONO,
        minWidth: 34, textAlign: 'center',
      }}>{value}</div>
      <button style={b} disabled={disabled} onClick={() => onChange(clamp(value + 1))}>+</button>
    </div>
  )
}

export function Button({ children, onClick, kind = 'default', disabled = false, style }: {
  children: ReactNode
  onClick?: () => void
  kind?: 'default' | 'primary' | 'danger'
  disabled?: boolean
  style?: CSSProperties
}) {
  const base = kind === 'primary' ? btnPrimary : kind === 'danger' ? btnDanger : btn
  return (
    <button onClick={onClick} disabled={disabled}
      style={{ ...base, opacity: disabled ? 0.45 : 1, flex: 'none', ...style }}>
      {children}
    </button>
  )
}

/* ------------------------------------------------------------------ readouts */

/** Label / value row separated by a hairline. The console's most-used element. */
export function KV({ k, v, color = V.text, maxWidth }:
  { k: ReactNode; v: ReactNode; color?: string; maxWidth?: number }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10,
      padding: '7px 0', fontSize: 12, borderTop: `1px solid ${V.border}`,
    }}>
      <div style={{ color: V.muted }}>{k}</div>
      <div style={{ fontFamily: MONO, color, ...ellip, maxWidth: maxWidth ?? 200 }}>{v}</div>
    </div>
  )
}

export function Sparkline({ values, width, height, color }:
  { values: number[]; width: number; height: number; color: string }) {
  return (
    <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none"
      style={{ width, height, flex: 'none', display: 'block' }}>
      <path d={spark(values, width, height)} fill="none" stroke={color} strokeWidth={1.8}
        strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </svg>
  )
}

export function Kpi({ title, value, sub, dot, values, sparkColor }: {
  title: string
  value: ReactNode
  sub: ReactNode
  dot: string
  values: number[]
  sparkColor: string
}) {
  return (
    <Card pad="20px 22px">
      <StatHead title={title} right={
        <div style={{ width: 7, height: 7, borderRadius: '50%', background: dot, flex: 'none' }} />
      } />
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 12, marginTop: 14 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 28, fontWeight: 600, letterSpacing: '-.032em', whiteSpace: 'nowrap' }}>{value}</div>
          <div style={{ fontSize: 12, color: V.muted, marginTop: 6, ...ellip }}>{sub}</div>
        </div>
        <Sparkline values={values} width={90} height={34} color={sparkColor} />
      </div>
    </Card>
  )
}

export function Gauge({ k, v, fill, color, note }:
  { k: string; v: ReactNode; fill: string; color: string; note: ReactNode }) {
  return (
    <div style={{ padding: '9px 0' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 10 }}>
        <div style={{ fontSize: 12, color: V.muted }}>{k}</div>
        <div style={{ fontSize: 12, fontFamily: MONO, color }}>{v}</div>
      </div>
      <Bar fill={fill} color={color} style={{ marginTop: 7 }} />
      <div style={{ fontSize: 11, color: V.faint, marginTop: 5 }}>{note}</div>
    </div>
  )
}

export function Bar({ fill, color, height = 8, style }:
  { fill: string; color: string; height?: number; style?: CSSProperties }) {
  return (
    <div style={{ height, borderRadius: 5, background: V.chip, overflow: 'hidden', ...style }}>
      <div style={{ height: '100%', width: fill, background: color, borderRadius: 5 }} />
    </div>
  )
}

/** Stacked proportional bar with a legend underneath. */
export function StackedBar({ segments, height = 8 }:
  { segments: { label: string; value: ReactNode; width: string; color: string }[]; height?: number }) {
  return (
    <>
      <div style={{ display: 'flex', height, borderRadius: height / 2, overflow: 'hidden', background: V.chip }}>
        {segments.map((s) => (
          <div key={s.label} style={{ width: s.width, background: s.color, minWidth: 2 }} />
        ))}
      </div>
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0,1fr))',
        gap: '8px 18px', marginTop: 14,
      }}>
        {segments.map((s) => (
          <div key={s.label} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, minWidth: 0 }}>
            <span style={{ width: 9, height: 9, borderRadius: 3, background: s.color, flex: 'none' }} />
            <span style={{ color: V.muted, ...ellip }}>{s.label}</span>
            <span style={{ flex: 1 }} />
            <span style={{ fontFamily: MONO, whiteSpace: 'nowrap' }}>{s.value}</span>
          </div>
        ))}
      </div>
    </>
  )
}

export function Empty({ title, why }: { title: ReactNode; why?: ReactNode }) {
  return (
    <div style={{ padding: '60px 16px', textAlign: 'center', fontSize: 13, color: V.muted, lineHeight: 1.7 }}>
      {title}
      {why ? <><br /><span style={{ fontSize: 12, color: V.faint }}>{why}</span></> : null}
    </div>
  )
}

export function Banner({ tone: t, children }: { tone: 'neg' | 'warn' | 'info'; children: ReactNode }) {
  const map = {
    neg: [V.negbg, V.neg],
    warn: [V.warnbg, V.warn],
    info: [V.chip, V.muted],
  } as const
  const [bg, fg] = map[t]
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10, padding: '11px 16px',
      borderRadius: 12, background: bg, border: `1px solid ${fg}33`, color: fg,
      fontSize: 12, lineHeight: 1.5,
    }}>
      {children}
    </div>
  )
}

/* ------------------------------------------------------------------ tables */

/** Header row of a grid table. `cols` is a grid-template-columns string. */
export function Thead({ cols, children, pad = '10px 20px' }:
  { cols: string; children: ReactNode; pad?: string }) {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: cols, gap: 10, padding: pad,
      borderBottom: `1px solid ${V.border}`, background: V.sunken,
      fontSize: 11, letterSpacing: '.05em', textTransform: 'uppercase', color: V.muted,
    }}>
      {children}
    </div>
  )
}

export function Trow({ cols, children, onClick, background = 'transparent', minHeight = 42, pad = '0 20px' }: {
  cols: string
  children: ReactNode
  onClick?: () => void
  background?: string
  minHeight?: number
  pad?: string
}) {
  return (
    <div onClick={onClick} style={{
      display: 'grid', gridTemplateColumns: cols, gap: 10, padding: pad,
      minHeight, alignItems: 'center', borderBottom: `1px solid ${V.border}`,
      fontSize: 12, background, cursor: onClick ? 'pointer' : undefined,
      transition: 'background .12s',
    }}>
      {children}
    </div>
  )
}

/** Horizontal scroll container that keeps a table from collapsing its columns. */
export function Scroller({ min, children, maxHeight }:
  { min: number; children: ReactNode; maxHeight?: number }) {
  return (
    <div style={{ overflowX: 'auto' }}>
      <div style={{ minWidth: min }}>
        {maxHeight ? <div style={{ maxHeight, overflowY: 'auto' }}>{children}</div> : children}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ overlays */

export function Dialog({ open, title, body, confirmLabel, danger = false, typeToConfirm, onCancel, onConfirm }: {
  open: boolean
  title: string
  body: ReactNode
  confirmLabel: string
  danger?: boolean
  /** When set, the confirm button unlocks only once this exact word is typed. */
  typeToConfirm?: string
  onCancel: () => void
  onConfirm: () => void
}) {
  const [typed, setTyped] = useState('')
  useEffect(() => { if (open) setTyped('') }, [open])
  if (!open) return null
  const blocked = !!typeToConfirm && typed !== typeToConfirm

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 80, background: 'rgba(6,7,9,.62)',
      display: 'grid', placeItems: 'center', padding: 24,
    }}>
      <div style={{
        width: 460, maxWidth: '100%', border: `1px solid ${V.border}`, borderRadius: 18,
        background: V.card, padding: 26, boxShadow: '0 24px 60px rgba(0,0,0,.4)',
      }}>
        <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: '-.015em', color: danger ? V.neg : V.text }}>
          {title}
        </div>
        <div style={{ fontSize: 13, color: V.muted, lineHeight: 1.6, marginTop: 10 }}>{body}</div>

        {typeToConfirm ? (
          <div style={{ marginTop: 18 }}>
            <div style={{ fontSize: 11, color: V.muted, marginBottom: 8 }}>
              Type <span style={{ fontFamily: MONO, fontWeight: 700, color: V.neg }}>{typeToConfirm}</span> to confirm.
            </div>
            <input value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={typeToConfirm}
              autoFocus
              style={{
                width: '100%', padding: '10px 13px', border: `1px solid ${V.border2}`,
                borderRadius: 10, background: V.sunken, fontFamily: MONO, fontSize: 13,
                letterSpacing: '.16em', outline: 'none',
              }} />
          </div>
        ) : null}

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 9, marginTop: 22 }}>
          <Button onClick={onCancel}>Cancel</Button>
          <Button kind={danger ? 'danger' : 'primary'} disabled={blocked} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  )
}

export function Toasts() {
  const items = useToasts((s) => s.items)
  if (!items.length) return null
  return (
    <div style={{
      position: 'fixed', right: 22, bottom: 22, zIndex: 90,
      display: 'flex', flexDirection: 'column', gap: 10, alignItems: 'flex-end',
    }}>
      {items.map((t) => (
        <div key={t.id} style={{
          maxWidth: 400, border: `1px solid ${V.border}`, borderRadius: 14,
          background: V.card, padding: '13px 16px', boxShadow: '0 10px 28px rgba(0,0,0,.2)',
        }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: V.accent }}>{t.title}</div>
          <div style={{ fontSize: 12, color: V.muted, marginTop: 4, lineHeight: 1.5 }}>{t.body}</div>
        </div>
      ))}
    </div>
  )
}

/* ------------------------------------------------------------------ safety */

/**
 * Contained per screen: a crash on one page must not take out the nav and
 * topbar, so the operator can still reach the others — including the kill switch.
 */
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null }

  static getDerivedStateFromError(error: Error) { return { error } }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <Card>
        <CardHead title="This screen failed to render" sub="Every other screen still works. Reload to clear it." />
        <pre style={{
          margin: '16px 0 0', padding: '14px 15px', border: `1px solid ${V.border}`,
          borderRadius: 12, background: V.sunken, fontFamily: MONO, fontSize: 11,
          lineHeight: 1.65, color: V.neg, overflowX: 'auto', whiteSpace: 'pre-wrap',
        }}>{String(this.state.error?.message ?? this.state.error)}</pre>
        <div style={{ marginTop: 14 }}>
          <Button kind="primary" onClick={() => this.setState({ error: null })}>Try again</Button>
        </div>
      </Card>
    )
  }
}
