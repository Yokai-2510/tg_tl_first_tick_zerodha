import { Link } from 'react-router-dom'
import { useStore } from '../lib/store'
import {
  bytes, duration, int, money, pct, signClass, timeFromIso, DASH,
} from '../lib/format'
import { Card, KV, PhaseTimeline, Stat } from '../components/ui'
import CapitalCard from '../components/CapitalCard'

export default function Dashboard() {
  const status = useStore((s) => s.status)
  const events = useStore((s) => s.events)
  if (!status) return null

  const p = status.positions
  const marketClosed = !status.feed.connected || (status.feed.last_tick_age_ms ?? 0) > 300_000

  return (
    <div className="space-y-5">
      <PhaseTimeline phase={status.phase} history={status.history} schedule={status.schedule} />

      <div className="grid gap-3 grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <Stat label="Realised P&L" value={money(p.realised)} tone={signClass(p.realised)}
              sub={`${p.closed} closed`} />
        <Stat label="Unrealised P&L" value={money(p.unrealised)} tone={signClass(p.unrealised)}
              sub={`${p.open} open`} />
        <Stat label="Open positions" value={int(p.open)}
              sub={p.adopted ? `${p.adopted} adopted` : 'none adopted'} />
        <Stat label="Armed" value={int(status.engine.armed)}
              sub={`${status.engine.fired} fired`}
              hint="Instruments watched for a first positive tick" />
        <Stat label="Signals" value={int(status.engine.signals)}
              sub={`${status.engine.intent_queue} queued`} />
        <Stat label="Capital free" value={money(status.capital?.available)}
              sub={status.capital
                ? `${pct(status.capital.deployed_pct, { sign: false })} deployed`
                : 'unknown'}
              hint="Available margin at the broker" />
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="space-y-3 lg:col-span-3 lg:grid lg:grid-cols-3 lg:gap-5 lg:space-y-0">
          <CapitalCard cap={status.capital} />

          <Card label="Market feed">
            <KV k="Connection" v={status.feed.connected ? 'Connected' : 'Disconnected'}
                tone={status.feed.connected ? 'text-pos' : 'text-neg'} />
            <KV k="Subscribed" v={`${int(status.feed.subscribed)} instruments`} />
            <KV k="Modes" v={Object.entries(status.feed.modes ?? {})
                  .map(([m, n]) => `${m} ${n}`).join(' · ') || DASH} />
            <KV k="Ticks received" v={int(status.feed.ticks)} />
            <KV k={marketClosed ? 'Last trade age' : 'Last tick'}
                v={duration(status.feed.last_tick_age_ms)} />
            <KV k="Reconnects" v={int(status.feed.reconnects)}
                tone={status.feed.reconnects > 0 ? 'text-warn' : ''} />
            <KV k="Feed gaps" v={int(status.feed.gaps)}
                tone={status.feed.gaps > 0 ? 'text-warn' : ''} />
          </Card>

          <Card label="Tick recorder">
            <KV k="Running" v={status.recorder.running ? 'Yes' : 'No'}
                tone={status.recorder.running ? 'text-pos' : 'text-muted'} />
            <KV k="Ticks written" v={int(status.recorder.ticks)} />
            <KV k="Events" v={int(status.recorder.events)} />
            <KV k="On disk" v={bytes(status.recorder.bytes)} />
            <KV k="Dropped" v={int(status.recorder.dropped)}
                tone={status.recorder.dropped > 0 ? 'text-neg' : ''} />
            <KV k="Queue depth" v={int(status.recorder.queue_depth)} />
            <KV k="Compression" v={status.recorder.compression} />
          </Card>

          <Card label="Recent events"
                right={<Link to="/logs" className="text-micro text-accent">All →</Link>}>
            {events.length === 0
              ? <div className="text-micro text-muted py-2">No events yet today.</div>
              : <div className="space-y-1.5">
                  {events.slice(-8).reverse().map((e, i) => (
                    <div key={i} className="flex items-baseline gap-2">
                      <span className="mono text-[11px] text-muted shrink-0">
                        {timeFromIso(e.at)}
                      </span>
                      <span className="text-micro">{e.kind}</span>
                    </div>
                  ))}
                </div>}
          </Card>
        </div>
      </div>
    </div>
  )
}

