/** Shared primitives. One card pattern, one table pattern, reused everywhere. */

import { Component, useEffect, useRef, useState, type ReactNode } from 'react'
import { PHASE_ORDER, type Phase } from '../lib/api'
import { timeFromUs } from '../lib/format'

// ---------------------------------------------------------------- card

export function Card(
  { label, hint, children, right, className = '' }:
  { label?: string; hint?: string; children: ReactNode; right?: ReactNode; className?: string },
) {
  return (
    <div className={`card p-4 ${className}`}>
      {(label || right) && (
        <div className="flex items-start justify-between gap-2 mb-2">
          <div className="lbl" title={hint}>{label}</div>
          {right}
        </div>
      )}
      {children}
    </div>
  )
}

/** KPI: label, big value, muted sub-line. */
export function Stat(
  { label, value, sub, tone = '', hint }:
  { label: string; value: ReactNode; sub?: ReactNode; tone?: string; hint?: string },
) {
  return (
    <div className="card p-4">
      <div className="lbl mb-1.5" title={hint}>{label}</div>
      <div className={`text-kpi font-semibold leading-none ${tone}`}>{value}</div>
      {sub !== undefined && <div className="text-micro text-muted mt-1.5">{sub}</div>}
    </div>
  )
}

export function Section({ title, right, children }:
  { title: string; right?: ReactNode; children: ReactNode }) {
  return (
    <section className="space-y-3">
      <div className="flex items-end justify-between gap-3">
        <h2 className="text-[15px] font-semibold">{title}</h2>
        {right}
      </div>
      {children}
    </section>
  )
}

// ---------------------------------------------------------------- pills

const PHASE_TONE: Record<string, string> = {
  BOOT: 'text-muted border-line', IDLE: 'text-muted border-line',
  PHASE_1: 'text-accent border-accent/40', FEED_LIVE: 'text-accent border-accent/40',
  PREOPEN: 'text-accent border-accent/40', SETTLEMENT: 'text-accent border-accent/40',
  ARMING: 'text-warn border-warn/40', FROZEN: 'text-warn border-warn/40',
  EOD: 'text-warn border-warn/40',
  TRADING: 'text-pos border-pos/40', MANAGING: 'text-pos border-pos/40',
  PHASE_1_FAIL: 'text-neg border-neg/40',
}

export const PHASE_MEANING: Record<string, string> = {
  BOOT: 'waiting for the pre-market window',
  PHASE_1: 'authenticating, downloading contracts',
  PHASE_1_FAIL: 'pre-market checks failed — no trading today',
  FEED_LIVE: 'feed connected, recording',
  PREOPEN: 'pre-open auction being recorded',
  SETTLEMENT: 'ranking being computed',
  ARMING: 'option chains subscribing',
  FROZEN: 'instrument set locked, waiting for the bell',
  TRADING: 'entries live',
  MANAGING: 'holding, exits armed',
  EOD: 'squaring off',
  IDLE: 'day complete',
}

export function Pill({ children, tone = 'text-muted border-line', dot = false }:
  { children: ReactNode; tone?: string; dot?: boolean }) {
  return (
    <span className={`inline-flex items-center gap-1.5 h-6 px-2 rounded-card border
                      text-label uppercase tracking-wider font-medium ${tone}`}>
      {dot && <span className="w-1.5 h-1.5 rounded-full bg-current" />}
      {children}
    </span>
  )
}

export function PhasePill({ phase }: { phase: Phase }) {
  return <Pill tone={PHASE_TONE[phase] ?? 'text-muted border-line'} dot>{phase}</Pill>
}

export function StatusDot({ tone, label }: { tone: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-micro text-muted">
      <span className={`w-2 h-2 rounded-full ${tone}`} />{label}
    </span>
  )
}

// ---------------------------------------------------------------- table

export function Table({ head, children, empty, colSpan = 1 }:
  { head: ReactNode; children: ReactNode; empty?: string; colSpan?: number }) {
  const rows = Array.isArray(children) ? children.flat() : children
  const isEmpty = !rows || (Array.isArray(rows) && rows.length === 0)
  return (
    <div className="card overflow-hidden">
      <div className="overflow-auto max-h-[70vh]">
        <table className="w-full text-micro border-collapse">
          <thead><tr>{head}</tr></thead>
          <tbody>
            {isEmpty
              ? <tr><td className="td text-muted px-3 py-6 text-center" colSpan={colSpan}>
                  {empty ?? 'Nothing to show.'}
                </td></tr>
              : rows}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export function Tabs<T extends string>({ tabs, value, onChange }:
  { tabs: { id: T; label: string; count?: number }[]; value: T; onChange: (v: T) => void }) {
  return (
    <div className="flex gap-1 border-b border-line">
      {tabs.map((t) => (
        <button key={t.id} onClick={() => onChange(t.id)}
          className={`h-8 px-3 text-micro font-medium border-b-2 -mb-px transition-colors duration-100
            ${value === t.id ? 'border-accent text-ink' : 'border-transparent text-muted hover:text-ink'}`}>
          {t.label}
          {t.count !== undefined && <span className="ml-1.5 text-muted">{t.count}</span>}
        </button>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------- phase timeline

export function PhaseTimeline(
  { phase, history, schedule }:
  { phase: Phase; history: { to: string; at_us: number }[]; schedule: Record<string, string> },
) {
  const actual = new Map(history.map((h) => [h.to, h.at_us]))
  const planned: Record<string, string | undefined> = {
    PHASE_1: schedule.phase1_time,
    FEED_LIVE: schedule.feed_connect_time,
    SETTLEMENT: schedule.settlement_snapshot,
    FROZEN: schedule.manual_cutoff,
    TRADING: schedule.trading_start,
    EOD: schedule.eod_time,
  }
  const currentIdx = PHASE_ORDER.indexOf(phase)
  const failed = phase === 'PHASE_1_FAIL'

  return (
    <div className="card p-4">
      <div className="lbl mb-3">Session progress</div>
      <ol className="flex items-start gap-0 overflow-x-auto pb-1">
        {PHASE_ORDER.map((p, i) => {
          const done = !failed && i < currentIdx
          const now = p === phase
          const at = actual.get(p)
          return (
            <li key={p} className="flex items-start shrink-0">
              <div className="flex flex-col items-center gap-1.5 px-1 min-w-[74px]">
                <div className={`w-2.5 h-2.5 rounded-full border-2 ${
                  now ? 'bg-accent border-accent'
                  : done ? 'bg-muted border-muted'
                  : 'bg-transparent border-line'}`} />
                <div className={`text-[10px] leading-tight text-center font-medium ${
                  now ? 'text-ink' : done ? 'text-muted' : 'text-muted/60'}`}>
                  {p}
                </div>
                <div className="text-[10px] leading-tight text-muted/70 mono">
                  {at ? timeFromUs(at) : (planned[p] ?? '')}
                </div>
              </div>
              {i < PHASE_ORDER.length - 1 && (
                <div className={`h-px w-3 mt-[5px] ${done ? 'bg-muted' : 'bg-line'}`} />
              )}
            </li>
          )
        })}
      </ol>
      <div className="text-micro text-muted mt-2">
        {failed
          ? PHASE_MEANING.PHASE_1_FAIL
          : `${phase} — ${PHASE_MEANING[phase] ?? ''}`}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- dialogs

export function Confirm(
  { open, title, body, confirmWord, danger, onCancel, onConfirm }:
  {
    open: boolean; title: string; body: ReactNode
    confirmWord?: string; danger?: boolean
    onCancel: () => void; onConfirm: () => void
  },
) {
  const [typed, setTyped] = useState('')
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => { if (open) { setTyped(''); setTimeout(() => ref.current?.focus(), 30) } }, [open])
  if (!open) return null
  const ok = !confirmWord || typed === confirmWord

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
         onClick={onCancel}>
      <div className="card w-full max-w-md p-5 bg-raised" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-[15px] font-semibold mb-2">{title}</h3>
        <div className="text-micro text-muted mb-4">{body}</div>
        {confirmWord && (
          <div className="mb-4">
            <label className="lbl block mb-1.5">Type {confirmWord} to confirm</label>
            <input ref={ref} className="inp mono" value={typed}
                   onChange={(e) => setTyped(e.target.value)}
                   onKeyDown={(e) => { if (e.key === 'Enter' && ok) onConfirm() }} />
          </div>
        )}
        <div className="flex justify-end gap-2">
          <button className="btn" onClick={onCancel}>Cancel</button>
          <button className={`btn ${danger ? 'btn-danger' : 'btn-primary'}`}
                  disabled={!ok} onClick={onConfirm}>Confirm</button>
        </div>
      </div>
    </div>
  )
}

export function Banner({ tone = 'warn', children }:
  { tone?: 'warn' | 'neg' | 'accent'; children: ReactNode }) {
  const t = tone === 'neg' ? 'border-neg/40 text-neg' :
            tone === 'accent' ? 'border-accent/40 text-accent' : 'border-warn/40 text-warn'
  return (
    <div className={`card border ${t} px-4 py-2.5 text-micro font-medium`}>{children}</div>
  )
}

export function Field({ label, hint, children }:
  { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <div className="lbl mb-1">{label}</div>
      {children}
      {hint && <div className="text-[11px] text-muted mt-1 leading-snug">{hint}</div>}
    </label>
  )
}

export function KV({ k, v, tone = '' }: { k: string; v: ReactNode; tone?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1 border-b border-line/50 last:border-0">
      <span className="text-micro text-muted">{k}</span>
      <span className={`text-micro font-medium ${tone}`}>{v}</span>
    </div>
  )
}


/**
 * Catches render errors so a single bad field cannot blank the whole console.
 *
 * This exists because it already happened: /status omitted `feed.modes` before the
 * ticker connected, `Object.entries(undefined)` threw, React unmounted the tree and
 * the page went black with nothing in the UI to explain it. A crash should always
 * name itself.
 */
export class ErrorBoundary extends Component<
  { children: ReactNode }, { error: Error | null }
> {
  constructor(props: { children: ReactNode }) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error, info: unknown) {
    // Keep it in the console too -- the message on screen is deliberately short.
    console.error('render failed', error, info)
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <div className="p-4 space-y-3">
        <Banner tone="neg">
          This screen failed to render. The rest of the console still works — the
          error is below, and the browser console has the stack.
        </Banner>
        <div className="card p-4">
          <div className="lbl mb-2">Error</div>
          <pre className="text-[11px] mono whitespace-pre-wrap break-all text-neg">
            {error.message || String(error)}
          </pre>
          <button className="btn mt-3" onClick={() => this.setState({ error: null })}>
            Try again
          </button>
        </div>
      </div>
    )
  }
}
