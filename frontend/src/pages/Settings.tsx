import ConfigForm from '../components/ConfigForm'
import { Card, KV } from '../components/ui'
import { API_BASE, WS_URL } from '../lib/api'
import { useStore } from '../lib/store'

/** Infrastructure: broker, schedule, recorder, API, alerts. */
export default function Settings() {
  const signOut = useStore((s) => s.signOut)
  const status = useStore((s) => s.status)

  return (
    <div className="space-y-5">
      <ConfigForm
        title="Settings"
        note="System, broker and recorder configuration. Broker credentials live in
              config/credentials.json on the server and are never exposed over the API."
        sections={['trading_mode', 'broker', 'schedule', 'recorder', 'snapshots',
                   'alerts', 'system', 'paper', 'api']}
      />

      <Card label="This console">
        <KV k="REST base" v={<span className="mono text-[11px]">{API_BASE}</span>} />
        <KV k="WebSocket" v={<span className="mono text-[11px]">{WS_URL}</span>} />
        <KV k="Backend mode" v={status?.mode ?? '—'}
            tone={status?.mode === 'live' ? 'text-warn' : ''} />
        <div className="mt-3">
          <button className="btn" onClick={signOut}>Sign out / change token</button>
        </div>
        <div className="text-[11px] text-muted mt-2 leading-snug">
          The access token is held in this browser tab only. Signing out clears it.
        </div>
      </Card>
    </div>
  )
}
