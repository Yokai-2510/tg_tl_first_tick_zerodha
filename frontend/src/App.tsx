import { useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { API_BASE, api, apiBaseIsOverridden, configProblem, setApiBase,
         setUnauthorizedHandler } from './lib/api'
import { clockIst } from './lib/format'
import { useStore } from './lib/store'
import { Banner, Confirm, ErrorBoundary, PhasePill, Pill, StatusDot } from './components/ui'
import Dashboard from './pages/Dashboard'
import Positions from './pages/Positions'
import LiveData from './pages/LiveData'
import StatusPage from './pages/StatusPage'
import Strategy from './pages/Strategy'
import Settings from './pages/Settings'
import LogsEvents from './pages/LogsEvents'

const NAV = [
  { to: '/', label: 'Dashboard' },
  { to: '/positions', label: 'Positions' },
  { to: '/live', label: 'Live Data' },
  { to: '/status', label: 'Status' },
  { to: '/strategy', label: 'Strategy' },
  { to: '/settings', label: 'Settings' },
  { to: '/logs', label: 'Logs & Events' },
]

export default function App() {
  const token = useStore((s) => s.token)
  const ready = useStore((s) => s.ready)
  const bootstrap = useStore((s) => s.bootstrap)
  const signOut = useStore((s) => s.signOut)

  useEffect(() => { setUnauthorizedHandler(() => signOut()) }, [signOut])
  useEffect(() => { if (token && !ready) void bootstrap() }, [token, ready, bootstrap])

  if (!token) return <SignIn />
  if (!ready) return <Booting />

  return (
    <div className="min-h-full flex flex-col">
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <nav className="w-[200px] shrink-0 border-r border-line p-2 space-y-0.5 hidden md:block">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === '/'}
              className={({ isActive }) =>
                `block px-3 h-8 leading-8 rounded-card text-micro font-medium transition-colors duration-100 ${
                  isActive ? 'bg-surface text-ink border border-line' : 'text-muted hover:text-ink'}`}>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <main className="flex-1 min-w-0 p-4 md:p-5 overflow-x-hidden">
          <div className="space-y-2 empty:hidden mb-4 empty:mb-0"><MisconfigBanner /></div>
          <Alerts />
          {/* Contained per-page: a crash on one screen must not take out the
              nav and topbar, so the operator can still reach the others. */}
          <ErrorBoundary>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/positions" element={<Positions />} />
            <Route path="/live" element={<LiveData />} />
            <Route path="/status" element={<StatusPage />} />
            <Route path="/strategy" element={<Strategy />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/logs" element={<LogsEvents />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
          </ErrorBoundary>
        </main>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- topbar

function Topbar() {
  const status = useStore((s) => s.status)
  const link = useStore((s) => s.link)
  const refresh = useStore((s) => s.refresh)
  const [now, setNow] = useState(clockIst())
  const [ask, setAsk] = useState<null | 'arm' | 'disarm'>(null)

  useEffect(() => {
    const t = setInterval(() => setNow(clockIst()), 1000)
    return () => clearInterval(t)
  }, [])

  const live = status?.mode === 'live'
  const armed = status?.entries_allowed && status?.engine.entries_enabled

  useEffect(() => {
    document.title = `${live ? '● ' : ''}First-Tick — ${status?.phase ?? ''}`
  }, [live, status?.phase])

  const linkTone =
    link === 'live' ? 'bg-pos' : link === 'polling' ? 'bg-warn' :
    link === 'connecting' ? 'bg-muted' : 'bg-neg'
  const linkLabel =
    link === 'live' ? 'Streaming' : link === 'polling' ? 'Polling' :
    link === 'connecting' ? 'Connecting' : 'Disconnected'

  const act = async () => {
    try {
      if (ask === 'arm') await api.arm()
      if (ask === 'disarm') await api.disarm()
      await refresh('all')
    } finally { setAsk(null) }
  }

  return (
    <header className={`h-14 shrink-0 sticky top-0 z-30 bg-bg border-b flex items-center
                        gap-3 px-4 ${live ? 'border-warn/50' : 'border-line'}`}>
      <div className="font-semibold tracking-tight">First-Tick</div>
      <span className="text-label uppercase tracking-wider text-muted hidden sm:inline">Operator</span>

      <div className="flex items-center gap-2 ml-2 min-w-0">
        {status && <PhasePill phase={status.phase} />}
        {status && (
          <Pill tone={live ? 'text-warn border-warn/50' : 'text-muted border-line'}>
            Mode {status.mode}
          </Pill>
        )}
      </div>

      <div className="ml-auto flex items-center gap-4">
        <StatusDot tone={linkTone} label={linkLabel} />
        <span className="mono text-micro text-muted hidden sm:inline">{now} IST</span>
        <button className="btn" onClick={() => setAsk(armed ? 'disarm' : 'arm')}>
          {armed ? 'Disarm' : 'Arm'}
        </button>
      </div>

      <Confirm
        open={ask !== null}
        title={ask === 'disarm' ? 'Disarm new entries?' : 'Arm new entries?'}
        body={ask === 'disarm'
          ? 'No new positions will be opened. Exits keep running on open positions.'
          : 'The engine will fire entries on the next qualifying tick while in TRADING.'}
        danger={ask === 'disarm'}
        onCancel={() => setAsk(null)}
        onConfirm={act}
      />
    </header>
  )
}

// ---------------------------------------------------------------- alerts

/** Shown on every screen, including the token gate — a bad build never reaches status. */
function MisconfigBanner() {
  const problem = configProblem()
  if (!problem) return null
  return <Banner tone="neg">Deployment misconfigured: {problem}</Banner>
}

function Alerts() {
  const status = useStore((s) => s.status)
  const link = useStore((s) => s.link)
  const error = useStore((s) => s.error)
  if (!status) return null

  const staleMs = Date.now() - new Date(status.server_time).getTime()
  const items: React.ReactNode[] = []

  if (status.phase === 'PHASE_1_FAIL')
    items.push(<Banner key="p1" tone="neg">
      Pre-market checks failed — no trading today.{status.last_error ? ` ${status.last_error}` : ''}
    </Banner>)
  if (status.halted)
    items.push(<Banner key="halt" tone="neg">
      Halted for the session — kill switch active. Entries and exits are disabled.
    </Banner>)
  if (status.mode === 'live')
    items.push(<Banner key="live" tone="warn">
      LIVE mode — orders placed here are real.
    </Banner>)
  if (status.recorder.disk_full)
    items.push(<Banner key="disk" tone="neg">Recorder stopped: disk full.</Banner>)
  if (status.recorder.dropped > 0)
    items.push(<Banner key="drop" tone="warn">
      Recorder dropped {status.recorder.dropped} record(s).
    </Banner>)
  if (!status.feed.connected)
    items.push(<Banner key="feed" tone="warn">
      Market feed disconnected ({status.feed.reconnects} reconnect attempts).
    </Banner>)
  if (link === 'down')
    items.push(<Banner key="ws" tone="warn">Live stream lost — retrying.</Banner>)
  if (staleMs > 10_000)
    items.push(<Banner key="stale" tone="warn">
      Data may be stale — last server update {Math.round(staleMs / 1000)}s ago.
    </Banner>)
  if (error) items.push(<Banner key="err" tone="neg">{error}</Banner>)

  if (!items.length) return null
  return <div className="space-y-2 mb-4">{items}</div>
}

// ---------------------------------------------------------------- gates

function SignIn() {
  const signIn = useStore((s) => s.signIn)
  const signInWithToken = useStore((s) => s.signInWithToken)
  const [user, setUser] = useState('')
  const [pass, setPass] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // Escape hatches, hidden by default so the normal case is just user + password.
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [tokenMode, setTokenMode] = useState(false)
  const [tokenVal, setTokenVal] = useState('')
  const [baseVal, setBaseVal] = useState(API_BASE)

  const go = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true); setErr(null)
    try {
      if (tokenMode) await signInWithToken(tokenVal.trim())
      else await signIn(user.trim(), pass)
    } catch (e: any) {
      // The backend deliberately does not say which field was wrong.
      setErr(e?.message ?? 'Sign-in failed.')
    } finally { setBusy(false) }
  }

  const canSubmit = tokenMode ? !!tokenVal.trim() : (!!user.trim() && !!pass)

  return (
    <div className="min-h-full grid place-items-center p-4">
      <div className="w-full max-w-sm space-y-3">
        <MisconfigBanner />
        <form onSubmit={go} className="card p-6 w-full space-y-4">
          <div>
            <div className="font-semibold text-[15px]">First-Tick Console</div>
            <div className="text-micro text-muted mt-0.5">
              {tokenMode ? 'Sign in with an API token.' : 'Sign in to continue.'}
            </div>
          </div>

          {tokenMode ? (
            <label className="block">
              <div className="lbl mb-1">API token</div>
              <input className="inp mono" type="password" autoFocus
                     placeholder="Paste token" value={tokenVal}
                     onChange={(e) => setTokenVal(e.target.value)} />
            </label>
          ) : (
            <>
              <label className="block">
                <div className="lbl mb-1">Username</div>
                <input className="inp" autoFocus autoComplete="username"
                       value={user} onChange={(e) => setUser(e.target.value)} />
              </label>
              <label className="block">
                <div className="lbl mb-1">Password</div>
                <input className="inp" type="password" autoComplete="current-password"
                       value={pass} onChange={(e) => setPass(e.target.value)} />
              </label>
            </>
          )}

          {err && <div className="text-micro text-neg">{err}</div>}

          <button className="btn btn-primary w-full justify-center"
                  disabled={busy || !canSubmit}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>

          <div className="flex items-center justify-between text-[11px]">
            <button type="button" className="text-muted hover:text-ink"
                    onClick={() => { setTokenMode(!tokenMode); setErr(null) }}>
              {tokenMode ? 'Use username & password' : 'Use an API token'}
            </button>
            <button type="button" className="text-muted hover:text-ink"
                    onClick={() => setShowAdvanced(!showAdvanced)}>
              {showAdvanced ? 'Hide' : 'Backend URL'}
            </button>
          </div>

          {showAdvanced && (
            <div className="pt-3 border-t border-line space-y-2">
              <label className="block">
                <div className="lbl mb-1">Backend URL</div>
                <input className="inp mono text-[11px]" value={baseVal}
                       onChange={(e) => setBaseVal(e.target.value)}
                       placeholder="https://host/api/v1" />
              </label>
              <div className="text-[11px] text-muted leading-snug">
                Saved in this browser and used instead of the compiled-in default,
                so pointing at another server needs no rebuild. The live-stream URL
                is derived from it.
              </div>
              <div className="flex gap-2">
                {/* Changing the base reloads the page, so every module reads it. */}
                <button type="button" className="btn flex-1 justify-center"
                        disabled={!baseVal.trim() || baseVal.trim() === API_BASE}
                        onClick={() => setApiBase(baseVal)}>
                  Save & reload
                </button>
                {apiBaseIsOverridden && (
                  <button type="button" className="btn flex-1 justify-center"
                          onClick={() => setApiBase(null)}>
                    Reset to default
                  </button>
                )}
              </div>
            </div>
          )}
        </form>

        {/* Which backend this build talks to — the fastest way to spot a console
            deployed against the wrong server. */}
        <div className="text-[11px] text-muted text-center mono break-all">
          {API_BASE}{apiBaseIsOverridden && ' (overridden)'}
        </div>
      </div>
    </div>
  )
}

function Booting() {
  const error = useStore((s) => s.error)
  const bootstrap = useStore((s) => s.bootstrap)
  const signOut = useStore((s) => s.signOut)
  return (
    <div className="min-h-full grid place-items-center p-4">
      <div className="card p-6 w-full max-w-md space-y-3">
        <div className="font-semibold">{error ? 'Cannot reach the backend' : 'Loading…'}</div>
        {error && <div className="text-micro text-neg">{error}</div>}
        {error && (
          <div className="flex gap-2">
            <button className="btn btn-primary" onClick={() => void bootstrap()}>Retry</button>
            <button className="btn" onClick={signOut}>Change token</button>
          </div>
        )}
      </div>
    </div>
  )
}
