import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/api'
import { useStore } from '../lib/store'
import { timeFromIso } from '../lib/format'
import { Card, Confirm, Section, Tabs } from '../components/ui'

type Tab = 'logs' | 'events' | 'controls'

export default function LogsEvents() {
  const [tab, setTab] = useState<Tab>('logs')
  const logs = useStore((s) => s.logs)
  const events = useStore((s) => s.events)

  return (
    <div className="space-y-4">
      <Section title="Logs & Events">
        <Tabs value={tab} onChange={setTab} tabs={[
          { id: 'logs', label: 'Logs', count: logs.length },
          { id: 'events', label: 'Events', count: events.length },
          { id: 'controls', label: 'Controls' },
        ]} />
      </Section>
      {tab === 'logs' && <Logs />}
      {tab === 'events' && <Events />}
      {tab === 'controls' && <Controls />}
    </div>
  )
}

const LEVEL_TONE: Record<string, string> = {
  ERROR: 'text-neg', CRITICAL: 'text-neg',
  WARNING: 'text-warn', INFO: 'text-ink', DEBUG: 'text-muted',
}

function Logs() {
  const logs = useStore((s) => s.logs)
  const [level, setLevel] = useState('ALL')
  const [q, setQ] = useState('')
  const [follow, setFollow] = useState(true)
  const box = useRef<HTMLDivElement>(null)

  const rows = useMemo(() => logs.filter((l) =>
    (level === 'ALL' || l.level === level) &&
    (!q.trim() || l.msg.toLowerCase().includes(q.toLowerCase()) ||
      l.module.toLowerCase().includes(q.toLowerCase()))
  ), [logs, level, q])

  useEffect(() => {
    if (follow && box.current) box.current.scrollTop = box.current.scrollHeight
  }, [rows.length, follow])

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2 items-center">
        <select className="inp w-32" value={level} onChange={(e) => setLevel(e.target.value)}>
          {['ALL', 'DEBUG', 'INFO', 'WARNING', 'ERROR'].map((l) =>
            <option key={l} value={l}>{l}</option>)}
        </select>
        <input className="inp max-w-xs" placeholder="Filter message or module"
               value={q} onChange={(e) => setQ(e.target.value)} />
        <label className="flex items-center gap-2 text-micro text-muted">
          <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
          Follow
        </label>
        <span className="text-micro text-muted ml-auto">{rows.length} lines</span>
      </div>
      <div ref={box} className="card overflow-auto max-h-[70vh] p-3"
           onWheel={() => setFollow(false)}>
        {rows.length === 0
          ? <div className="text-micro text-muted py-2">No log lines match.</div>
          : rows.map((l, i) => (
              <div key={i} className="flex gap-3 items-baseline py-[1px]">
                <span className="mono text-[11px] text-muted shrink-0 w-16">{l.ts}</span>
                <span className={`text-[11px] uppercase shrink-0 w-16 ${LEVEL_TONE[l.level] ?? ''}`}>
                  {l.level}
                </span>
                <span className="mono text-[11px] text-muted shrink-0 w-20 truncate">{l.module}</span>
                <span className="mono text-[11px] whitespace-pre-wrap break-all">{l.msg}</span>
              </div>
            ))}
      </div>
    </div>
  )
}

function Events() {
  const events = useStore((s) => s.events)
  const [kind, setKind] = useState('ALL')
  const kinds = useMemo(
    () => ['ALL', ...Array.from(new Set(events.map((e) => e.kind)))], [events])
  const rows = useMemo(
    () => (kind === 'ALL' ? events : events.filter((e) => e.kind === kind)).slice().reverse(),
    [events, kind])

  return (
    <div className="space-y-2">
      <div className="flex gap-2 items-center">
        <select className="inp w-48" value={kind} onChange={(e) => setKind(e.target.value)}>
          {kinds.map((k) => <option key={k} value={k}>{k}</option>)}
        </select>
        <span className="text-micro text-muted ml-auto">{rows.length} events</span>
      </div>
      {rows.length === 0
        ? <Card><div className="text-micro text-muted">
            No events yet. Phase transitions, feed changes, signals and position
            changes appear here as the session runs.
          </div></Card>
        : <div className="space-y-1.5">
            {rows.map((e, i) => {
              const rest = Object.entries(e).filter(([k]) => k !== 'kind' && k !== 'at')
              return (
                <div key={i} className="card px-3 py-2">
                  <div className="flex items-baseline gap-3">
                    <span className="mono text-[11px] text-muted shrink-0">{timeFromIso(e.at)}</span>
                    <span className="text-micro font-medium">{e.kind}</span>
                  </div>
                  {rest.length > 0 && (
                    <div className="flex flex-wrap gap-x-4 gap-y-0.5 mt-1">
                      {rest.map(([k, v]) => (
                        <span key={k} className="text-[11px] text-muted">
                          {k}={' '}
                          <span className="mono text-ink">
                            {typeof v === 'object' ? JSON.stringify(v) : String(v)}
                          </span>
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>}
    </div>
  )
}

function Controls() {
  const status = useStore((s) => s.status)
  const refresh = useStore((s) => s.refresh)
  const setError = useStore((s) => s.setError)
  const [ask, setAsk] = useState<null | 'kill' | 'exitAll'>(null)
  const paper = status?.mode === 'paper'

  const run = async () => {
    try {
      if (ask === 'kill') await api.killSwitch()
      if (ask === 'exitAll') await api.exitAll()
      await refresh('all'); setError(null)
    } catch (e: any) { setError(e?.message ?? 'Action failed') }
    finally { setAsk(null) }
  }

  const forcePhase = async (p: string) => {
    try { await api.forcePhase(p as any); await refresh('all'); setError(null) }
    catch (e: any) { setError(e?.message ?? 'Phase change refused') }
  }

  return (
    <div className="space-y-4 max-w-2xl">
      <Card label="Session">
        <div className="text-micro text-muted mb-3">
          Exit every open position at market. Entries stay armed.
        </div>
        <button className="btn btn-danger" onClick={() => setAsk('exitAll')}
                disabled={!status?.positions.open}>
          Exit all positions ({status?.positions.open ?? 0})
        </button>
      </Card>

      {paper && (
        <Card label="Phase override (paper only)">
          <div className="text-micro text-muted mb-3">
            Force a phase for testing. The backend refuses this in live mode.
          </div>
          <div className="flex flex-wrap gap-2">
            {['PHASE_1', 'FEED_LIVE', 'PREOPEN', 'SETTLEMENT', 'ARMING', 'FROZEN',
              'TRADING', 'MANAGING', 'EOD', 'IDLE'].map((p) => (
              <button key={p} className="btn h-7 px-2 text-[11px]"
                      onClick={() => forcePhase(p)}>{p}</button>
            ))}
          </div>
        </Card>
      )}

      <div className="card border-neg/40 p-4">
        <div className="lbl text-neg mb-1">Kill switch</div>
        <div className="text-micro text-muted mb-3">
          Exits every open position and halts trading for the rest of the day.
          This cannot be undone for the session — the service must be restarted.
        </div>
        <button className="btn btn-danger" onClick={() => setAsk('kill')}
                disabled={status?.halted}>
          {status?.halted ? 'Already halted' : 'Activate kill switch'}
        </button>
      </div>

      <Confirm open={ask !== null} danger
        confirmWord={ask === 'kill' ? 'KILL' : undefined}
        title={ask === 'kill' ? 'Activate the kill switch?' : 'Exit all positions?'}
        body={ask === 'kill'
          ? 'All open positions will be exited and trading halts for the rest of the day. Irreversible for this session.'
          : `${status?.positions.open ?? 0} position(s) will be closed at market.`}
        onCancel={() => setAsk(null)} onConfirm={run} />
    </div>
  )
}
