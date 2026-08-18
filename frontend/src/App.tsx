import { useEffect } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { setUnauthorizedHandler } from './lib/api'
import { useStore } from './lib/store'
import Shell from './components/Shell'
import { ErrorBoundary, Toasts } from './components/ui'
import SignIn, { Booting } from './pages/SignIn'
import Dashboard from './pages/Dashboard'
import Positions from './pages/Positions'
import LiveData from './pages/LiveData'
import StatusPage from './pages/StatusPage'
import Strategy from './pages/Strategy'
import Settings from './pages/Settings'
import LogsEvents from './pages/LogsEvents'

export default function App() {
  const token = useStore((s) => s.token)
  const ready = useStore((s) => s.ready)
  const bootstrap = useStore((s) => s.bootstrap)
  const signOut = useStore((s) => s.signOut)

  useEffect(() => { setUnauthorizedHandler(() => signOut()) }, [signOut])
  useEffect(() => { if (token && !ready) void bootstrap() }, [token, ready, bootstrap])

  if (!token) return <><SignIn /><Toasts /></>
  if (!ready) return <><Booting /><Toasts /></>

  return (
    <>
      <Shell>
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
      </Shell>
      <Toasts />
    </>
  )
}
