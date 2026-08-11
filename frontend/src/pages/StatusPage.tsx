import { useState } from 'react'
import { api } from '../lib/api'
import { useStore } from '../lib/store'
import {
  bytes, duration, int, micros, millis, seconds, timeFromIso, timeFromUs, DASH,
} from '../lib/format'
import { Card, Confirm, KV, Section, Stat, Table } from '../components/ui'
import CapitalCard from '../components/CapitalCard'

export default function StatusPage() {
  const status = useStore((s) => s.status)
  const latency = useStore((s) => s.latency)
  const refresh = useStore((s) => s.refresh)
  const setError = useStore((s) => s.setError)
  const [ask, setAsk] = useState<null | 'reconcile' | 'restart'>(null)
  const [report, setReport] = useState<Record<string, string[]> | null>(null)
  if (!status) return null

  const run = async () => {
    try {
      if (ask === 'reconcile') { setReport(await api.reconcile()); await refresh('positions') }
      if (ask === 'restart') { await api.restart() }
      setError(null)
    } catch (e: any) { setError(e?.message ?? 'Action failed') }
    finally { setAsk(null) }
  }

  const marketClosed = !status.feed.connected || (status.feed.last_tick_age_ms ?? 0) > 300_000

  return (
    <div className="space-y-5">
      <div className="grid gap-3 grid-cols-2 lg:grid-cols-4">
        <Stat label="Phase" value={status.phase}
              sub={status.entries_allowed ? 'entries allowed' : 'entries closed'} />
        <Stat label="Uptime" value={seconds(status.uptime_s)} sub="since last restart" />
        <Stat label="Ticks seen" value={int(status.engine.ticks_seen)}
              sub={`${int(status.engine.tracked_instruments)} instruments`} />
        <Stat label="Median tick→fill"
              value={latency.median_tick_to_fill_ms === null ? DASH : millis(latency.median_tick_to_fill_ms)}
              sub={`${latency.trades.length} trades`} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <CapitalCard cap={status.capital} detail />

        <Card label="Feed">
          <KV k="Connected" v={status.feed.connected ? 'Yes' : 'No'}
              tone={status.feed.connected ? 'text-pos' : 'text-neg'} />
          <KV k="Subscribed" v={int(status.feed.subscribed)} />
          {Object.entries(status.feed.modes).map(([m, n]) => (
            <KV key={m} k={`  mode ${m}`} v={int(n)} />
          ))}
          <KV k="Ticks" v={int(status.feed.ticks)} />
          <KV k="Batches" v={int(status.feed.batches)} />
          <KV k="Order events" v={int(status.feed.order_events)} />
          <KV k={marketClosed ? 'Last trade age' : 'Last tick'} v={duration(status.feed.last_tick_age_ms)} />
          <KV k="Reconnects" v={int(status.feed.reconnects)}
              tone={status.feed.reconnects ? 'text-warn' : ''} />
          <KV k="Gaps" v={int(status.feed.gaps)} tone={status.feed.gaps ? 'text-warn' : ''} />
          <KV k="Last error" v={status.feed.last_error ?? DASH} />
        </Card>

        <Card label="Engine">
          <KV k="Entries enabled" v={status.engine.entries_enabled ? 'Yes' : 'No'}
              tone={status.engine.entries_enabled ? 'text-pos' : 'text-muted'} />
          <KV k="Armed" v={int(status.engine.armed)} />
          <KV k="Fired" v={int(status.engine.fired)} />
          <KV k="Signals" v={int(status.engine.signals)} />
          <KV k="Intent queue" v={int(status.engine.intent_queue)} />
          <KV k="Open positions" v={int(status.positions.open)} />
          <KV k="Closed" v={int(status.positions.closed)} />
          <KV k="Failed" v={int(status.positions.failed)}
              tone={status.positions.failed ? 'text-neg' : ''} />
          <KV k="Adopted" v={int(status.positions.adopted)}
              tone={status.positions.adopted ? 'text-warn' : ''} />
          <KV k="WS clients" v={int(status.ws_clients)} />
        </Card>

        <Card label="Recorder">
          <KV k="Running" v={status.recorder.running ? 'Yes' : 'No'}
              tone={status.recorder.running ? 'text-pos' : 'text-muted'} />
          <KV k="Ticks" v={int(status.recorder.ticks)} />
          <KV k="Events" v={int(status.recorder.events)} />
          <KV k="Batches" v={int(status.recorder.batches)} />
          <KV k="On disk" v={bytes(status.recorder.bytes)} />
          <KV k="Queue depth" v={int(status.recorder.queue_depth)} />
          <KV k="Dropped" v={int(status.recorder.dropped)}
              tone={status.recorder.dropped ? 'text-neg' : ''} />
          <KV k="Disk full" v={status.recorder.disk_full ? 'YES' : 'No'}
              tone={status.recorder.disk_full ? 'text-neg' : ''} />
          <KV k="Compression" v={status.recorder.compression} />
          <KV k="Directory" v={<span className="mono text-[11px]">{status.recorder.dir}</span>} />
        </Card>
      </div>

      <Section title="Broker rate limits">
        <div className="grid gap-4 md:grid-cols-3">
          {Object.entries(status.rate_limits).map(([kind, b]) => (
            <Card key={kind} label={kind}>
              {Object.keys(b).filter((k) => k.startsWith('used_')).map((k) => {
                const win = k.replace('used_', '')
                const used = b[k]
                const limit = b[`limit_${win}`] ?? 0
                const ratio = limit ? used / limit : 0
                return (
                  <div key={k} className="py-1">
                    <div className="flex justify-between text-micro">
                      <span className="text-muted">per {win}</span>
                      <span>{int(used)} / {int(limit)}</span>
                    </div>
                    <div className="h-1 bg-line rounded mt-1 overflow-hidden">
                      <div className={`h-full ${ratio > 0.8 ? 'bg-neg' : ratio > 0.5 ? 'bg-warn' : 'bg-accent'}`}
                           style={{ width: `${Math.min(100, ratio * 100)}%` }} />
                    </div>
                  </div>
                )
              })}
              <KV k="Rejected" v={int(b.rejected)} tone={b.rejected ? 'text-neg' : ''} />
            </Card>
          ))}
        </div>
      </Section>

      <Section title="Entry latency">
        <div className="text-[11px] text-muted -mt-1">
          Tick→signal and signal→request are ours. Request→ack is the broker's; ack→fill is the
          exchange filling the order. Separating them is the point.
        </div>
        {latency.trades.length === 0
          ? <Card><div className="text-micro text-muted">
              No entries yet, so nothing measured. Populates on the first fill.
            </div></Card>
          : <>
              <Card>
                <div className="space-y-3">
                  {latency.trades.map((t) => {
                    const total = t.total_tick_to_fill_ms || 1
                    const segs = [
                      { w: t.tick_to_signal_us / 1000, c: 'bg-accent', n: 'tick→signal' },
                      { w: t.signal_to_req_ms, c: 'bg-accent/60', n: 'signal→req' },
                      { w: t.req_to_ack_ms, c: 'bg-warn', n: 'req→ack' },
                      { w: t.ack_to_fill_ms, c: 'bg-muted', n: 'ack→fill' },
                    ]
                    return (
                      <div key={t.sig_id}>
                        <div className="flex justify-between text-micro mb-1">
                          <span className="font-medium">{t.sym}</span>
                          <span className="text-muted">{millis(t.total_tick_to_fill_ms)}</span>
                        </div>
                        <div className="flex h-2 rounded overflow-hidden bg-line">
                          {segs.map((s) => (
                            <div key={s.n} className={s.c} title={`${s.n} ${millis(s.w)}`}
                                 style={{ width: `${Math.max(0, (s.w / total) * 100)}%` }} />
                          ))}
                        </div>
                      </div>
                    )
                  })}
                </div>
                <div className="flex gap-4 mt-3 text-[11px] text-muted">
                  <span><span className="inline-block w-2 h-2 bg-accent mr-1" />tick→signal (ours)</span>
                  <span><span className="inline-block w-2 h-2 bg-accent/60 mr-1" />signal→req (ours)</span>
                  <span><span className="inline-block w-2 h-2 bg-warn mr-1" />req→ack (broker)</span>
                  <span><span className="inline-block w-2 h-2 bg-muted mr-1" />ack→fill (exchange)</span>
                </div>
              </Card>
              <Table colSpan={6} head={<>
                <th className="th">Symbol</th>
                <th className="th num">Tick→signal</th>
                <th className="th num">Signal→req</th>
                <th className="th num">Req→ack</th>
                <th className="th num">Ack→fill</th>
                <th className="th num">Total</th>
              </>}>
                {latency.trades.map((t) => (
                  <tr key={t.sig_id} className="hover:bg-surface/60">
                    <td className="td font-medium">{t.sym}</td>
                    <td className="td num mono">{micros(t.tick_to_signal_us)}</td>
                    <td className="td num mono">{millis(t.signal_to_req_ms)}</td>
                    <td className="td num mono">{millis(t.req_to_ack_ms)}</td>
                    <td className="td num mono">{millis(t.ack_to_fill_ms)}</td>
                    <td className="td num mono font-medium">{millis(t.total_tick_to_fill_ms)}</td>
                  </tr>
                ))}
              </Table>
            </>}
      </Section>

      <Section title="System">
        <Card>
          <KV k="Server time" v={timeFromIso(status.server_time)} />
          <KV k="Mode" v={status.mode} tone={status.mode === 'live' ? 'text-warn' : ''} />
          <KV k="Halted" v={status.halted ? 'YES' : 'No'} tone={status.halted ? 'text-neg' : ''} />
          <KV k="Last error" v={status.last_error ?? DASH}
              tone={status.last_error ? 'text-neg' : ''} />
          <div className="flex gap-2 mt-3">
            <button className="btn" onClick={() => setAsk('reconcile')}>Reconcile with broker</button>
            <button className="btn btn-danger" onClick={() => setAsk('restart')}>Restart service</button>
          </div>
          {report && (
            <div className="mt-3 space-y-1">
              <div className="lbl">Reconciliation report</div>
              {Object.entries(report).map(([k, v]) => (
                <KV key={k} k={k.replace(/_/g, ' ')} v={v.length ? v.join(', ') : 'none'} />
              ))}
            </div>
          )}
        </Card>
      </Section>

      <Section title="Phase history">
        <Table colSpan={3} empty="No transitions recorded yet." head={<>
          <th className="th num">Time</th>
          <th className="th">From</th>
          <th className="th">To</th>
        </>}>
          {[...status.history].reverse().map((h, i) => (
            <tr key={i} className="hover:bg-surface/60">
              <td className="td num mono text-muted">{timeFromUs(h.at_us)}</td>
              <td className="td text-muted">{h.from}</td>
              <td className="td font-medium">{h.to}</td>
            </tr>
          ))}
        </Table>
      </Section>

      <Confirm open={ask !== null} danger
        title={ask === 'restart' ? 'Restart the service?' : 'Reconcile with the broker?'}
        body={ask === 'restart'
          ? 'The engine restarts and re-reconciles before arming. Open positions are preserved but entries are disabled until reconciliation completes. Do not do this during market hours unless necessary.'
          : 'Fetches the broker position book and three-way matches it against the local book. Detects positions closed outside this system.'}
        onCancel={() => setAsk(null)} onConfirm={run} />
    </div>
  )
}
