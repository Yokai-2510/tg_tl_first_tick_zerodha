import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import type { EventRow, LogRow } from '../lib/api'
import { DASH, timeFromIso } from '../lib/format'
import { MONO, V, badge, ellip } from '../lib/style'
import { useStore } from '../lib/store'
import { Card, CardHead, ChipRow, Empty, Segmented, Toggle } from '../components/ui'

type Tab = 'logs' | 'events'

export default function LogsEvents() {
  const [tab, setTab] = useState<Tab>('events')
  const logs = useStore((s) => s.logs)
  const events = useStore((s) => s.events)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <Segmented<Tab> value={tab} onChange={setTab} options={[
        { key: 'events', label: 'Events', count: events.length },
        { key: 'logs', label: 'Logs', count: logs.length },
      ]} />
      {tab === 'events' ? <Events /> : <Logs />}
    </div>
  )
}

/* ------------------------------------------------------------------ events */

/** Severity is derived from the kind: the feed carries no level of its own. */
function severity(kind: string): 'neg' | 'warn' | 'pos' | 'info' {
  const k = kind.toLowerCase()
  if (/(reject|fail|error|halt|kill)/.test(k)) return 'neg'
  if (/(warn|gap|drop|disconnect|retry|timeout|full)/.test(k)) return 'warn'
  if (/(fill|entry|exit|complete|arm)/.test(k)) return 'pos'
  return 'info'
}

const SEV_COLOR = { neg: V.neg, warn: V.warn, pos: V.pos, info: V.border2 } as const

/** Fields worth showing inline as chips, in the order an operator reads them. */
const CHIP_KEYS = ['sym', 'symbol', 'tradingsymbol', 'pos_id', 'sig_id', 'order_id',
  'side', 'role', 'status', 'trigger', 'price', 'qty', 'lots', 'reason', 'rejection']

function Events() {
  const events = useStore((s) => s.events)
  const [kind, setKind] = useState<string>('all')
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const [raw, setRaw] = useState<Record<string, boolean>>({})

  const kinds = useMemo(() => {
    const counts = new Map<string, number>()
    for (const e of events) counts.set(e.kind, (counts.get(e.kind) ?? 0) + 1)
    return [...counts.entries()].sort((a, b) => b[1] - a[1])
  }, [events])

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    const list = events.filter((e) => (kind === 'all' || e.kind === kind))
    const matched = q
      ? list.filter((e) => JSON.stringify(e).toLowerCase().includes(q))
      : list
    return [...matched].reverse()
  }, [events, kind, query])

  return (
    <Card pad={false} style={{ overflow: 'hidden' }}>
      <div style={{
        padding: '18px 22px', borderBottom: `1px solid ${V.border}`,
        display: 'flex', flexDirection: 'column', gap: 14,
      }}>
        <CardHead
          title="Events"
          sub="The typed event feed, newest first. Every row expands to its full payload; nothing is summarised away."
          right={<div style={{ fontSize: 11, color: V.faint, whiteSpace: 'nowrap' }}>
            {rows.length} of {events.length} retained
          </div>}
        />
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <ChipRow
            value={kind}
            onChange={setKind}
            options={[
              { key: 'all', label: 'All', count: events.length },
              ...kinds.map(([k, n]) => ({ key: k, label: k, count: n })),
            ]}
          />
          <div style={{ flex: 1 }} />
          <input value={query} onChange={(e) => setQuery(e.target.value)}
            placeholder="Search payload"
            style={{
              width: 220, padding: '8px 12px', borderRadius: 10, background: V.sunken,
              border: `1px solid ${V.border2}`, fontSize: 12, outline: 'none',
            }} />
        </div>
      </div>

      <div style={{ maxHeight: 640, overflowY: 'auto' }}>
        {rows.map((e, i) => {
          const id = `${e.kind}-${e.at}-${i}`
          const sev = severity(e.kind)
          const isOpen = open === id
          const payload = Object.entries(e).filter(([k]) => k !== 'kind' && k !== 'at')
          const chips = CHIP_KEYS
            .filter((k) => e[k] !== undefined && e[k] !== null && e[k] !== '')
            .slice(0, 5)
          const posId = typeof e.pos_id === 'string' ? e.pos_id : null

          return (
            <div key={id} style={{ borderBottom: `1px solid ${V.border}` }}>
              <div onClick={() => setOpen(isOpen ? null : id)}
                style={{
                  display: 'grid', gridTemplateColumns: '3px 84px minmax(150px,220px) minmax(0,1fr) 16px',
                  gap: 12, alignItems: 'center', padding: '10px 22px 10px 0',
                  background: isOpen ? V.sunken : 'transparent', cursor: 'pointer',
                }}>
                <div style={{ height: 26, background: SEV_COLOR[sev], borderRadius: '0 2px 2px 0' }} />
                <div style={{ fontSize: 11, fontFamily: MONO, color: V.faint }}>{timeFromIso(e.at)}</div>
                <div style={{ minWidth: 0 }}>
                  <span style={badge(
                    sev === 'neg' ? V.negbg : sev === 'warn' ? V.warnbg : sev === 'pos' ? V.posbg : V.chip,
                    sev === 'neg' ? V.neg : sev === 'warn' ? V.warn : sev === 'pos' ? V.pos : V.muted,
                  )}>{e.kind}</span>
                </div>
                <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', minWidth: 0 }}>
                  {chips.map((k) => (
                    <span key={k} style={{
                      fontSize: 11, fontFamily: MONO, padding: '2px 8px', borderRadius: 7,
                      background: V.chip, color: V.muted, whiteSpace: 'nowrap',
                      maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis',
                    }}>
                      <span style={{ color: V.faint }}>{k}</span> {String(e[k])}
                    </span>
                  ))}
                  {!chips.length ? (
                    <span style={{ fontSize: 11, color: V.faint }}>
                      {payload.length} field{payload.length === 1 ? '' : 's'}
                    </span>
                  ) : null}
                </div>
                <div style={{ fontSize: 9, color: V.faint }}>{isOpen ? '▾' : '▸'}</div>
              </div>

              {isOpen ? (
                <div style={{ padding: '4px 22px 18px 99px', background: V.sunken }}>
                  <div style={{
                    display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0,1fr))',
                    gap: '2px 22px',
                  }}>
                    {payload.map(([k, v]) => (
                      <div key={k} style={{
                        display: 'flex', justifyContent: 'space-between', gap: 12,
                        padding: '6px 0', borderTop: `1px solid ${V.border}`, fontSize: 12,
                      }}>
                        <span style={{ color: V.muted, fontFamily: MONO }}>{k}</span>
                        <span style={{ fontFamily: MONO, ...ellip, maxWidth: 320 }}>
                          {typeof v === 'object' ? JSON.stringify(v) : String(v)}
                        </span>
                      </div>
                    ))}
                  </div>

                  <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginTop: 14 }}>
                    {posId ? (
                      <Link to="/positions" style={{ fontSize: 11, color: V.accent }}>
                        Open {posId} in Positions →
                      </Link>
                    ) : null}
                    <div style={{ flex: 1 }} />
                    <span style={{ fontSize: 11, color: V.muted }}>Raw JSON</span>
                    <Toggle size="sm" on={!!raw[id]} onChange={(next) => setRaw((r) => ({ ...r, [id]: next }))} />
                  </div>

                  {raw[id] ? (
                    <pre style={{
                      margin: '12px 0 0', padding: '13px 15px', border: `1px solid ${V.border}`,
                      borderRadius: 12, background: V.card, fontFamily: MONO, fontSize: 11,
                      lineHeight: 1.65, color: V.muted, overflowX: 'auto',
                    }}>{JSON.stringify(e, null, 2)}</pre>
                  ) : null}
                </div>
              ) : null}
            </div>
          )
        })}

        {!rows.length ? (
          <Empty
            title={events.length ? 'No events match this filter.' : 'No events yet.'}
            why={events.length
              ? 'Clear the kind filter or the search to see the whole feed.'
              : 'Events are emitted from feed connect onwards and retained in a rolling window.'}
          />
        ) : null}
      </div>
    </Card>
  )
}

/* ------------------------------------------------------------------ logs */

const LEVELS = ['ERROR', 'WARN', 'INFO', 'DEBUG'] as const

function levelTone(level: string): [string, string] {
  const l = level.toUpperCase()
  if (l === 'ERROR' || l === 'CRITICAL') return [V.negbg, V.neg]
  if (l === 'WARN' || l === 'WARNING') return [V.warnbg, V.warn]
  if (l === 'INFO') return [V.chip, V.muted]
  return [V.chip, V.faint]
}

function Logs() {
  const logs = useStore((s) => s.logs)
  const [level, setLevel] = useState<string>('all')
  const [module, setModule] = useState<string>('all')
  const [query, setQuery] = useState('')
  const [follow, setFollow] = useState(true)
  const scroller = useRef<HTMLDivElement | null>(null)

  const counts = useMemo(() => {
    const c = new Map<string, number>()
    for (const l of logs) c.set(l.level.toUpperCase(), (c.get(l.level.toUpperCase()) ?? 0) + 1)
    return c
  }, [logs])

  const modules = useMemo(() => {
    const c = new Map<string, number>()
    for (const l of logs) c.set(l.module, (c.get(l.module) ?? 0) + 1)
    return [...c.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8)
  }, [logs])

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    return logs.filter((l) =>
      (level === 'all' || l.level.toUpperCase() === level)
      && (module === 'all' || l.module === module)
      && (!q || l.msg.toLowerCase().includes(q) || l.module.toLowerCase().includes(q)))
  }, [logs, level, module, query])

  // Pinned to the bottom while following, so a busy stream stays readable.
  useEffect(() => {
    if (!follow) return
    const el = scroller.current
    if (el) el.scrollTop = el.scrollHeight
  }, [rows.length, follow])

  return (
    <Card pad={false} style={{ overflow: 'hidden' }}>
      <div style={{
        padding: '18px 22px', borderBottom: `1px solid ${V.border}`,
        display: 'flex', flexDirection: 'column', gap: 14,
      }}>
        <CardHead
          title="Log stream"
          sub="Structured backend log lines, oldest first. Timestamps are IST and carry no date."
          right={<div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 11, color: V.muted }}>Follow</span>
            <Toggle size="sm" on={follow} onChange={setFollow} />
          </div>}
        />
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <ChipRow
            value={level}
            onChange={setLevel}
            options={[
              { key: 'all', label: 'All', count: logs.length },
              ...LEVELS.map((l) => ({ key: l, label: l, count: counts.get(l) ?? 0 })),
            ]}
          />
          <div style={{ flex: 1 }} />
          <input value={query} onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter message or module"
            style={{
              width: 240, padding: '8px 12px', borderRadius: 10, background: V.sunken,
              border: `1px solid ${V.border2}`, fontSize: 12, outline: 'none',
            }} />
        </div>
        {modules.length ? (
          <ChipRow
            value={module}
            onChange={setModule}
            options={[
              { key: 'all', label: 'All modules' },
              ...modules.map(([m, n]) => ({ key: m, label: m, count: n })),
            ]}
          />
        ) : null}
      </div>

      <div ref={scroller} style={{ maxHeight: 620, overflowY: 'auto' }}>
        {rows.map((l, i) => <LogLine key={`${l.ts}-${i}`} row={l} />)}
        {!rows.length ? (
          <Empty
            title={logs.length ? 'No lines match this filter.' : 'No log lines yet.'}
            why={logs.length
              ? 'Clear the level, module or text filter.'
              : 'The stream starts when the service does; the level floor is system.log_level.'}
          />
        ) : null}
      </div>
    </Card>
  )
}

function LogLine({ row }: { row: LogRow }) {
  const [bg, fg] = levelTone(row.level)
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '3px 78px 62px minmax(120px,180px) minmax(0,1fr)',
      gap: 12, alignItems: 'baseline', padding: '7px 22px 7px 0',
      borderBottom: `1px solid ${V.border}`,
      background: fg === V.neg ? V.negbg : 'transparent',
    }}>
      <div style={{ height: 15, background: fg === V.muted || fg === V.faint ? V.border2 : fg }} />
      <div style={{ fontSize: 11, fontFamily: MONO, color: V.faint }}>{row.ts || DASH}</div>
      <div><span style={badge(bg, fg)}>{row.level.toUpperCase()}</span></div>
      <div style={{ fontSize: 11, fontFamily: MONO, color: V.muted, ...ellip }}>{row.module}</div>
      <div style={{
        fontSize: 12, fontFamily: MONO, lineHeight: 1.55,
        color: fg === V.neg ? V.neg : V.text, wordBreak: 'break-word',
      }}>{row.msg}</div>
    </div>
  )
}

/** Exported for the event payload type, so the file documents what it consumes. */
export type { EventRow }
