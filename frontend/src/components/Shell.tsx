import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { api } from '../lib/api'
import { clockIst, duration, int, seconds } from '../lib/format'
import { ICONS, PHASE_MEANING } from '../lib/sections'
import { MONO, V, ellip, pill } from '../lib/style'
import { useStore } from '../lib/store'
import { toast } from '../lib/toast'
import { Banner, Button, Dialog, Pill } from './ui'

const NAV = [
  { to: '/', icon: 'dashboard', label: 'Dashboard' },
  { to: '/positions', icon: 'positions', label: 'Positions' },
  { to: '/live', icon: 'live', label: 'Live Data' },
  { to: '/status', icon: 'status', label: 'Status' },
  { to: '/strategy', icon: 'strategy', label: 'Strategy' },
  { to: '/settings', icon: 'settings', label: 'Settings' },
  { to: '/logs', icon: 'logs', label: 'Logs & Events' },
]

const TITLES: Record<string, [string, string]> = {
  '/': ['Dashboard', 'Where the session stands right now'],
  '/positions': ['Positions', 'Open and closed positions, order attempts and signals'],
  '/live': ['Live Data', 'Session summary, breadth and the ranked constituent list'],
  '/status': ['Status', 'Feed, engine, recorder, armed instruments and subscriptions'],
  '/strategy': ['Strategy', 'Instruments, direction, contracts, entry, exits, risk and mode'],
  '/settings': ['Settings', 'Appearance, credentials, connection, schedule and system'],
  '/logs': ['Logs & Events', 'Structured log stream and the typed event feed'],
}

export default function Shell({ children }: { children: ReactNode }) {
  const { pathname } = useLocation()
  const [title, sub] = TITLES[pathname] ?? TITLES['/']
  const status = useStore((s) => s.status)
  const live = status?.mode === 'live'

  return (
    <div style={{ minHeight: '100vh', background: V.page, color: V.text, display: 'flex', alignItems: 'flex-start' }}>
      <Sidebar />
      <div style={{ flex: 1, minWidth: 0 }}>
        <Topbar />
        {live ? (
          <div style={{ height: 2, background: V.neg, position: 'sticky', top: 64, zIndex: 39 }} />
        ) : null}
        <HaltedBar />
        <div style={{ padding: '26px 30px 80px' }}>
          <div style={{
            display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between',
            gap: 20, marginBottom: 22,
          }}>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 23, fontWeight: 600, letterSpacing: '-.025em' }}>{title}</div>
              <div style={{ fontSize: 13, color: V.muted, marginTop: 5 }}>{sub}</div>
            </div>
            {pathname === '/' ? <SessionActions /> : null}
          </div>
          <Alerts />
          {children}
        </div>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ sidebar */

function Sidebar() {
  const positions = useStore((s) => s.positions)
  const universe = useStore((s) => s.universe)
  const status = useStore((s) => s.status)
  const link = useStore((s) => s.link)
  const lastUpdate = useStore((s) => s.lastUpdate)

  const counts: Record<string, string> = {
    '/positions': positions.length ? String(positions.length) : '',
    '/live': universe?.armed.length ? String(universe.armed.length) : '',
  }

  const linkColor = link === 'live' ? V.pos : link === 'polling' ? V.warn : link === 'connecting' ? V.muted : V.neg
  const linkLabel = link === 'live' ? 'Streaming' : link === 'polling' ? 'Polling'
    : link === 'connecting' ? 'Connecting' : 'Disconnected'

  return (
    <div style={{
      width: 240, flex: 'none', position: 'sticky', top: 0, height: '100vh',
      background: V.card, borderRight: `1px solid ${V.border}`,
      display: 'flex', flexDirection: 'column', padding: '20px 14px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 11, padding: '2px 8px 24px' }}>
        <div style={{
          width: 30, height: 30, borderRadius: 10, background: V.accent, color: '#fff',
          display: 'grid', placeItems: 'center', fontSize: 12, fontWeight: 700,
        }}>FT</div>
        <div>
          <div style={{ fontSize: 14, fontWeight: 600, letterSpacing: '-.015em' }}>First-Tick</div>
          <div style={{
            fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase',
            color: V.faint, marginTop: 2,
          }}>Operator</div>
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
        {NAV.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.to === '/'} style={({ isActive }) => ({
            display: 'flex', alignItems: 'center', gap: 11, padding: '10px 12px',
            borderRadius: 11, border: 'none', textDecoration: 'none',
            background: isActive ? V.chip : 'transparent',
            color: isActive ? V.text : V.muted,
            fontSize: 13, fontWeight: isActive ? 600 : 500,
            transition: 'background .12s, color .12s',
          })}>
            {({ isActive }) => (
              <>
                <span style={{
                  width: 16, height: 16, flex: 'none', display: 'grid', placeItems: 'center',
                  color: isActive ? V.accent : V.faint,
                }}>
                  <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor"
                    strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
                    <path d={ICONS[n.icon]} />
                  </svg>
                </span>
                <span style={{ flex: 1 }}>{n.label}</span>
                <span style={{ fontSize: 11, color: isActive ? V.muted : V.faint }}>{counts[n.to] ?? ''}</span>
              </>
            )}
          </NavLink>
        ))}
      </div>

      <div style={{ flex: 1 }} />

      <div style={{
        border: `1px solid ${V.border}`, borderRadius: 14, background: V.sunken, padding: 14,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 11 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{ width: 7, height: 7, borderRadius: '50%', background: linkColor }} />
            <div style={{ fontSize: 12, fontWeight: 600 }}>{linkLabel}</div>
          </div>
          <div style={{ fontSize: 10, color: V.faint, fontFamily: MONO }}>
            {lastUpdate ? `${Math.max(0, Math.round((Date.now() - lastUpdate) / 1000))}s` : '—'}
          </div>
        </div>
        <FootRow k="Uptime" v={status ? seconds(status.uptime_s) : '—'} />
        <FootRow k="Subscribed" v={status ? int(status.feed.subscribed) : '—'} />
      </div>
    </div>
  )
}

function FootRow({ k, v }: { k: string; v: string }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', fontSize: 11,
      color: V.muted, padding: '2px 0',
    }}>
      <span>{k}</span>
      <span style={{ fontFamily: MONO }}>{v}</span>
    </div>
  )
}

/* ------------------------------------------------------------------ topbar */

function Topbar() {
  const status = useStore((s) => s.status)
  const refresh = useStore((s) => s.refresh)
  const [now, setNow] = useState(clockIst())
  const [ask, setAsk] = useState<null | 'arm' | 'disarm'>(null)

  useEffect(() => {
    const t = window.setInterval(() => setNow(clockIst()), 1000)
    return () => window.clearInterval(t)
  }, [])

  const live = status?.mode === 'live'
  const phase = status?.phase ?? 'BOOT'
  const armed = !!(status?.entries_allowed && status?.engine.entries_enabled)

  useEffect(() => {
    document.title = `${live ? '● ' : ''}First-Tick — ${phase}`
  }, [live, phase])

  const trading = phase === 'TRADING' || phase === 'MANAGING'
  const failed = phase === 'PHASE_1_FAIL'
  const phaseColor = failed ? V.neg : trading ? V.pos : V.muted
  const phaseBg = failed ? V.negbg : trading ? V.posbg : V.chip

  const act = async () => {
    const want = ask
    setAsk(null)
    if (!want) return
    try {
      if (want === 'arm') await api.arm()
      else await api.disarm()
      await refresh('all')
      toast(want === 'arm' ? 'Entries armed' : 'Entries disarmed',
        want === 'arm'
          ? 'New entries are permitted again.'
          : 'New entries will not be placed. Exits keep running.')
    } catch (e) {
      toast('Not allowed', e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div style={{
      position: 'sticky', top: 0, zIndex: 40, height: 64, display: 'flex',
      alignItems: 'center', gap: 14, padding: '0 30px',
      background: V.card, borderBottom: `1px solid ${V.border}`,
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8, padding: '6px 12px',
        borderRadius: 9, background: phaseBg,
      }}>
        <div style={{ width: 6, height: 6, borderRadius: '50%', background: phaseColor }} />
        <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '.07em', color: phaseColor }}>{phase}</div>
      </div>
      <div style={{ fontSize: 12, color: V.muted, ...ellip }}>{PHASE_MEANING[phase] ?? ''}</div>

      <div style={{ flex: 1 }} />

      <div style={{
        display: 'flex', alignItems: 'center', gap: 7, padding: '6px 12px', borderRadius: 9,
        border: `1px solid ${live ? V.neg : V.border2}`,
        background: live ? V.negbg : V.card,
        color: live ? V.neg : V.muted,
      }}>
        <div style={{ fontSize: 10, letterSpacing: '.09em', textTransform: 'uppercase', opacity: 0.7 }}>Mode</div>
        <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '.08em' }}>
          {(status?.mode ?? 'paper').toUpperCase()}
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'baseline', gap: 5 }}>
        <div style={{ fontFamily: MONO, fontSize: 13 }}>{now}</div>
        <div style={{ fontSize: 10, color: V.faint, letterSpacing: '.06em' }}>IST</div>
      </div>

      <button
        onClick={() => {
          if (status?.halted) {
            toast('Not allowed', 'Halted for the session — arm controls are disabled until the service restarts.')
            return
          }
          setAsk(armed ? 'disarm' : 'arm')
        }}
        style={{
          padding: '8px 15px', borderRadius: 10, fontSize: 12, fontWeight: 600,
          border: `1px solid ${armed ? V.border2 : V.accent}`,
          background: armed ? V.card : V.accent,
          color: armed ? V.neg : '#fff',
          transition: 'background .12s',
        }}>
        {status?.halted ? 'Halted' : armed ? 'Disarm' : 'Arm'}
      </button>

      <Dialog
        open={ask !== null}
        title={ask === 'disarm' ? 'Disarm new entries?' : 'Arm new entries?'}
        body={ask === 'disarm'
          ? 'No new positions will be opened. Exits keep running on everything already held.'
          : 'The engine will fire entries on the next qualifying tick while the phase allows it.'}
        confirmLabel={ask === 'disarm' ? 'Disarm' : 'Arm'}
        danger={ask === 'disarm'}
        onCancel={() => setAsk(null)}
        onConfirm={() => void act()}
      />
    </div>
  )
}

function HaltedBar() {
  const halted = useStore((s) => s.status?.halted)
  if (!halted) return null
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10, padding: '11px 30px',
      background: V.negbg, borderBottom: `1px solid ${V.neg}`, color: V.neg, fontSize: 12,
    }}>
      <div style={{ fontWeight: 700, letterSpacing: '.04em' }}>HALTED FOR THE SESSION</div>
      <div style={{ opacity: 0.85 }}>
        Kill switch active. Arm and exit controls are disabled until the service restarts.
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ actions */

function SessionActions() {
  const status = useStore((s) => s.status)
  const positions = useStore((s) => s.positions)
  const refresh = useStore((s) => s.refresh)
  const [ask, setAsk] = useState<null | 'exitall' | 'kill'>(null)

  const open = positions.filter((p) => p.status !== 'CLOSED').length
  const halted = !!status?.halted

  const run = async () => {
    const want = ask
    setAsk(null)
    if (!want) return
    try {
      if (want === 'exitall') {
        const r = await api.exitAll()
        toast('Exiting all', `Market exits sent for ${r.exiting} position${r.exiting === 1 ? '' : 's'}.`)
      } else {
        const r = await api.killSwitch()
        toast('Kill switch executed', `Halted for the session. ${r.exiting} position${r.exiting === 1 ? '' : 's'} exiting.`)
      }
      await refresh('all')
    } catch (e) {
      toast('Rejected', e instanceof Error ? e.message : String(e))
    }
  }

  const reconcile = async () => {
    try {
      const report = await api.reconcile()
      const n = Object.values(report).reduce((a, v) => a + (Array.isArray(v) ? v.length : 0), 0)
      await refresh('positions')
      toast('Reconciled', n
        ? `Broker book re-read; ${n} position${n === 1 ? '' : 's'} needed attention.`
        : 'Broker book re-read against local state. Nothing had drifted.')
    } catch (e) {
      toast('Reconcile failed', e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 9, flex: 'none' }}>
      <Button onClick={() => void reconcile()}>Reconcile</Button>
      <Button disabled={!open || halted} onClick={() => setAsk('exitall')}>Exit all ({open})</Button>
      <Button kind="danger" disabled={halted} onClick={() => setAsk('kill')}>Kill switch</Button>

      <Dialog
        open={ask === 'exitall'}
        title="Exit all positions?"
        body={`Sends a market exit for ${open} open position${open === 1 ? '' : 's'}. Entries stay armed.`}
        confirmLabel={`Exit ${open}`}
        danger
        onCancel={() => setAsk(null)}
        onConfirm={() => void run()}
      />
      <Dialog
        open={ask === 'kill'}
        title="Kill switch"
        body="Exits every position at market and halts the bot for the rest of the session. Irreversible — the service must be restarted to resume trading."
        confirmLabel="Execute kill switch"
        danger
        typeToConfirm="KILL"
        onCancel={() => setAsk(null)}
        onConfirm={() => void run()}
      />
    </div>
  )
}

/* ------------------------------------------------------------------ alerts */

function Alerts() {
  const status = useStore((s) => s.status)
  const link = useStore((s) => s.link)
  const error = useStore((s) => s.error)

  const items = useMemo(() => {
    const out: ReactNode[] = []
    if (!status) return out
    if (status.phase === 'PHASE_1_FAIL') {
      out.push(<Banner key="p1" tone="neg">
        Pre-market checks failed — no trading today.{status.last_error ? ` ${status.last_error}` : ''}
      </Banner>)
    }
    if (status.recorder.disk_full) out.push(<Banner key="disk" tone="neg">Recorder stopped: disk full.</Banner>)
    if (status.recorder.dropped > 0) {
      out.push(<Banner key="drop" tone="warn">
        Recorder dropped {int(status.recorder.dropped)} record{status.recorder.dropped === 1 ? '' : 's'}.
      </Banner>)
    }
    if (!status.feed.connected) {
      out.push(<Banner key="feed" tone="warn">
        Market feed disconnected — {int(status.feed.reconnects)} reconnect attempt{status.feed.reconnects === 1 ? '' : 's'} so far.
      </Banner>)
    }
    if (link === 'down') out.push(<Banner key="ws" tone="warn">Live stream lost — retrying.</Banner>)
    const staleMs = Date.now() - new Date(status.server_time).getTime()
    if (staleMs > 10_000) {
      out.push(<Banner key="stale" tone="warn">
        Data may be stale — last server update {duration(staleMs)} ago.
      </Banner>)
    }
    if (error) out.push(<Banner key="err" tone="neg">{error}</Banner>)
    return out
  }, [status, link, error])

  if (!items.length) return null
  return <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 18 }}>{items}</div>
}

/** Small helper so pages can show a phase-coloured pill inline. */
export function PhasePill({ phase }: { phase: string }) {
  const trading = phase === 'TRADING' || phase === 'MANAGING'
  const failed = phase === 'PHASE_1_FAIL'
  return (
    <span style={pill(failed ? V.negbg : trading ? V.posbg : V.chip, failed ? V.neg : trading ? V.pos : V.muted)}>
      {phase}
    </span>
  )
}

export { Pill }
