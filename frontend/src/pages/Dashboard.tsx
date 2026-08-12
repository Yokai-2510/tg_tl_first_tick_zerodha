import { Link } from 'react-router-dom'
import { useStore } from '../lib/store'
import {
  bytes, duration, int, money, pct, price, seconds, signClass, timeFromIso, DASH,
} from '../lib/format'
import { Card, KV, PhaseTimeline, Section, Stat, Table } from '../components/ui'
import CapitalCard from '../components/CapitalCard'

export default function Dashboard() {
  const status = useStore((s) => s.status)
  const positions = useStore((s) => s.positions)
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
        <div className="lg:col-span-2">
          <Section title="Open positions"
            right={<Link to="/positions" className="text-micro text-accent">All positions →</Link>}>
            <Table colSpan={7}
              empty={emptyPositions(status.phase, status.schedule.trading_start)}
              head={<>
                <th className="th">Symbol</th>
                <th className="th num">Qty</th>
                <th className="th num">Entry</th>
                <th className="th num">LTP</th>
                <th className="th num">P&L</th>
                <th className="th num">P&L %</th>
                <th className="th num">Held</th>
              </>}>
              {positions.map((x) => (
                <tr key={x.pos_id} className="hover:bg-surface/60">
                  <td className="td font-medium">{x.instrument.tradingsymbol}</td>
                  <td className="td num">{int(x.quantity)}</td>
                  <td className="td num">{price(x.entry.price)}</td>
                  <td className="td num">{price(x.live.ltp)}</td>
                  <td className={`td num font-medium ${signClass(x.live.pnl)}`}>{money(x.live.pnl)}</td>
                  <td className={`td num ${signClass(x.live.pnl_pct)}`}>{pct(x.live.pnl_pct)}</td>
                  <td className="td num text-muted">{seconds(x.live.holding_seconds)}</td>
                </tr>
              ))}
            </Table>
          </Section>
        </div>

        <div className="space-y-3">
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

function emptyPositions(phase: string, tradingStart: string): string {
  if (phase === 'PHASE_1_FAIL') return 'No positions — pre-market checks failed, no trading today.'
  if (['BOOT', 'IDLE'].includes(phase)) return `No positions. The session begins tomorrow; entries open at ${tradingStart}.`
  if (['PHASE_1', 'FEED_LIVE', 'PREOPEN', 'SETTLEMENT', 'ARMING', 'FROZEN'].includes(phase))
    return `No positions yet — entries open at ${tradingStart}.`
  return 'No open positions.'
}
