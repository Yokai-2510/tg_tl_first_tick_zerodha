import { useState } from 'react'
import { api } from '../lib/api'
import { DASH, bytes, duration, int, micros, millis, price, seconds, timeFromIso } from '../lib/format'
import { getPath } from '../lib/patch'
import { MONO, V, ellip, pctWidth, tone } from '../lib/style'
import { useStore } from '../lib/store'
import { toast } from '../lib/toast'
import { Button, Card, CardHead, Empty, Gauge, KV, Pill, Scroller, Thead, Trow } from '../components/ui'

export default function StatusPage() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0,1fr))', gap: 18 }}>
        <FeedPanel />
        <RecorderPanel />
        <RateLimitPanel />
      </div>
      <SystemCard />
      <div style={{
        display: 'grid', gridTemplateColumns: 'minmax(0,1.55fr) minmax(0,1fr)',
        gap: 18, alignItems: 'start',
      }}>
        <ArmedTable />
        <Subscriptions />
      </div>
      <Latency />
      <Reconcile />
    </div>
  )
}

/* ------------------------------------------------------------------ panels */

function FeedPanel() {
  const feed = useStore((s) => s.status?.feed)
  if (!feed) return <Card><CardHead title="Feed" /></Card>

  const modes = Object.entries(feed.modes).map(([k, v]) => `${int(v)} ${k}`).join(' · ')
  return (
    <Card pad="20px 22px">
      <CardHead title="Feed" style={{ marginBottom: 16 }}
        right={<Pill bg={feed.connected ? V.posbg : V.chip} fg={feed.connected ? V.pos : V.muted}>
          {feed.connected ? 'Connected' : 'Idle'}
        </Pill>} />
      <KV k="Subscribed" v={int(feed.subscribed)} />
      <KV k="Mode split" v={modes || DASH} />
      <KV k="Ticks" v={int(feed.ticks)} />
      <KV k="Batches" v={int(feed.batches)} />
      {/* feed_lag is the exchange's dissemination delay, not ours, and is
          legitimately hours-large outside market hours. */}
      <KV k={feed.connected ? 'Last tick' : 'Last trade age'}
        v={feed.last_tick_age_ms === null ? DASH : duration(feed.last_tick_age_ms)}
        color={feed.connected ? V.text : V.muted} />
      <KV k="Reconnects · gaps" v={`${int(feed.reconnects)} · ${int(feed.gaps)}`}
        color={feed.gaps ? V.warn : V.text} />
      {feed.last_error ? <KV k="Last error" v={feed.last_error} color={V.neg} maxWidth={180} /> : null}
    </Card>
  )
}

function RecorderPanel() {
  const rec = useStore((s) => s.status?.recorder)
  if (!rec) return <Card><CardHead title="Recorder" /></Card>

  const state = !rec.enabled ? 'Disabled' : rec.disk_full ? 'Disk full' : rec.running ? 'Running' : 'Stopped'
  const tone2 = !rec.enabled ? [V.chip, V.muted] : rec.disk_full ? [V.negbg, V.neg]
    : rec.running ? [V.posbg, V.pos] : [V.warnbg, V.warn]

  return (
    <Card pad="20px 22px">
      <CardHead title="Recorder" style={{ marginBottom: 16 }}
        right={<Pill bg={tone2[0]} fg={tone2[1]}>{state}</Pill>} />
      <KV k="Queue depth" v={int(rec.queue_depth)} color={rec.queue_depth > 100 ? V.warn : V.text} />
      <KV k="Ticks · events" v={`${int(rec.ticks)} · ${int(rec.events)}`} />
      <KV k="Size" v={bytes(rec.bytes)} />
      <KV k="Batches" v={int(rec.batches)} />
      <KV k="Compression" v={rec.compression || DASH} />
      <KV k="Dropped" v={int(rec.dropped)} color={rec.dropped ? V.neg : V.text} />
      <KV k="Directory" v={rec.dir || DASH} color={V.muted} maxWidth={180} />
    </Card>
  )
}

function RateLimitPanel() {
  const limits = useStore((s) => s.status?.rate_limits)
  const entries = Object.entries(limits ?? {})
  const rejected = entries.reduce((a, [, b]) => a + (b.rejected ?? 0), 0)

  return (
    <Card pad="20px 22px">
      <CardHead title="Rate limits" style={{ marginBottom: 16 }}
        right={<Pill bg={rejected ? V.negbg : V.posbg} fg={rejected ? V.neg : V.pos}>
          {rejected ? `${int(rejected)} rejected` : 'Healthy'}
        </Pill>} />
      {entries.length ? entries.map(([name, bucket]) => {
        // Buckets carry a `rejected` counter plus whatever window keys the
        // backend tracks; show the windows, not the counter, as the value.
        const windows = Object.entries(bucket).filter(([k]) => k !== 'rejected')
        return (
          <KV key={name} k={name.replace(/_/g, ' ')}
            v={windows.map(([k, v]) => `${int(v)} ${k}`).join(' · ') || DASH}
            color={bucket.rejected ? V.neg : V.text} />
        )
      }) : (
        <div style={{ fontSize: 12, color: V.faint, padding: '8px 0' }}>Not reported.</div>
      )}
    </Card>
  )
}

/* ------------------------------------------------------------------ system */

function SystemCard() {
  const status = useStore((s) => s.status)
  const cfg = useStore((s) => s.cfg)
  if (!status) return null

  const softCap = Number(getPath(cfg?.config, 'instruments.subscription_soft_cap') ?? 0)
  const diskBudgetMb = Number(getPath(cfg?.config, 'recorder.max_disk_mb') ?? 0)
  const diskBudget = diskBudgetMb * 1024 * 1024
  const maxConcurrent = Number(getPath(cfg?.config, 'positions.max_concurrent') ?? 0)

  const subs = status.feed.subscribed
  const queue = status.recorder.queue_depth
  const used = status.recorder.bytes
  const open = status.positions.open

  const gaugeColor = (fraction: number) => fraction > 0.85 ? V.neg : fraction > 0.7 ? V.warn : V.pos

  const gauges = [
    {
      k: 'Subscriptions',
      v: softCap ? `${int(subs)} / ${int(softCap)}` : int(subs),
      fraction: softCap ? subs / softCap : 0,
      note: softCap ? 'soft cap from instruments.subscription_soft_cap' : 'no soft cap configured',
    },
    {
      k: 'Recording on disk',
      v: diskBudget ? `${bytes(used)} / ${bytes(diskBudget)}` : bytes(used),
      fraction: diskBudget ? used / diskBudget : 0,
      note: status.recorder.disk_full
        ? 'budget reached — recording stopped'
        : `on_disk_full: ${String(getPath(cfg?.config, 'recorder.on_disk_full') ?? DASH)}`,
    },
    {
      k: 'Open positions',
      v: maxConcurrent ? `${int(open)} / ${int(maxConcurrent)}` : int(open),
      fraction: maxConcurrent ? open / maxConcurrent : 0,
      note: 'positions.max_concurrent',
    },
    {
      k: 'Write queue',
      v: `${int(queue)} batch${queue === 1 ? '' : 'es'}`,
      fraction: Math.min(1, queue / 500),
      note: `flush every ${String(getPath(cfg?.config, 'recorder.flush_interval_ms') ?? DASH)}ms · ${int(status.recorder.dropped)} dropped`,
    },
  ]

  const healthy = gauges.every((g) => g.fraction <= 0.85) && !status.recorder.disk_full

  return (
    <Card>
      <CardHead
        title="System"
        sub="Live counters against the ceilings in configuration. Recording stops on its own at the disk budget."
        right={<Pill bg={healthy ? V.posbg : V.negbg} fg={healthy ? V.pos : V.neg}>
          {healthy ? 'Healthy' : 'At a limit'}
        </Pill>}
        style={{ marginBottom: 18 }}
      />
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1.1fr) minmax(0,1fr)', gap: 26 }}>
        <div>
          {gauges.map((g) => (
            <Gauge key={g.k} k={g.k} v={g.v} fill={pctWidth(g.fraction, 1)}
              color={gaugeColor(g.fraction)} note={g.note} />
          ))}
        </div>
        <div>
          <KV k="Phase" v={status.phase} />
          <KV k="Entries allowed" v={String(status.entries_allowed)}
            color={status.entries_allowed ? V.pos : V.muted} />
          <KV k="Uptime" v={seconds(status.uptime_s)} />
          <KV k="Server time" v={timeFromIso(status.server_time)} />
          <KV k="Tracked instruments" v={int(status.engine.tracked_instruments)} />
          <KV k="Ticks seen" v={int(status.engine.ticks_seen)} />
          <KV k="Intent queue" v={int(status.engine.intent_queue)}
            color={status.engine.intent_queue ? V.warn : V.text} />
          <KV k="Console clients" v={int(status.ws_clients)} />
          <KV k="Data directory" v={String(getPath(cfg?.config, 'system.data_dir') ?? DASH)}
            color={V.muted} maxWidth={220} />
          {status.last_error ? <KV k="Last error" v={status.last_error} color={V.neg} maxWidth={220} /> : null}
        </div>
      </div>
    </Card>
  )
}

/* ------------------------------------------------------------------ armed */

const ARMED_COLS = 'minmax(180px,1.3fr) 80px 80px 88px 44px 58px'

function ArmedTable() {
  const universe = useStore((s) => s.universe)
  const market = useStore((s) => s.market)
  const rows = universe?.armed ?? []

  return (
    <Card pad={false} style={{ overflow: 'hidden', minWidth: 0 }}>
      <div style={{ padding: '18px 20px', borderBottom: `1px solid ${V.border}` }}>
        <CardHead
          title="Armed instruments"
          sub={<>Diff is <span style={{ fontFamily: MONO }}>ltp − ref</span>; the first tick past the minimum resolves direction</>}
          right={<div style={{ fontSize: 11, color: V.faint, whiteSpace: 'nowrap' }}>
            {rows.length ? `${rows.filter((r) => r.fired).length} fired of ${rows.length}` : DASH}
          </div>}
        />
      </div>
      <Scroller min={620} maxHeight={400}>
        <Thead cols={ARMED_COLS}>
          <div>Symbol</div>
          <div style={{ textAlign: 'right' }}>Ref</div>
          <div style={{ textAlign: 'right' }}>LTP</div>
          <div style={{ textAlign: 'right' }}>Diff</div>
          <div style={{ textAlign: 'right' }}>Lots</div>
          <div style={{ textAlign: 'right' }}>Fired</div>
        </Thead>
        {rows.map((a) => {
          // The armed row carries its own ltp, but the market diff stream is
          // fresher between status polls.
          const live = market[String(a.token)]?.ltp ?? a.ltp
          const diff = live && a.ref_price ? live - a.ref_price : 0
          return (
            <Trow key={a.token} cols={ARMED_COLS} minHeight={38}>
              <div style={{ fontFamily: MONO, ...ellip }}>{a.symbol}</div>
              <div style={{ textAlign: 'right', color: V.muted }}>{price(a.ref_price, { zeroIsDash: true })}</div>
              <div style={{ textAlign: 'right' }}>{price(live, { zeroIsDash: true })}</div>
              <div style={{ textAlign: 'right', fontWeight: 600, color: tone(diff) }}>
                {diff ? price(diff) : DASH}
              </div>
              <div style={{ textAlign: 'right', color: V.muted }}>{int(a.lots)}</div>
              <div style={{ textAlign: 'right', fontSize: 11, color: a.fired ? V.accent : V.faint }}>
                {a.fired ? 'Yes' : DASH}
              </div>
            </Trow>
          )
        })}
        {!rows.length ? (
          <Empty title="Nothing armed."
            why="Instruments are armed after the settlement snapshot and frozen at the manual cutoff." />
        ) : null}
      </Scroller>
    </Card>
  )
}

function Subscriptions() {
  const status = useStore((s) => s.status)
  const universe = useStore((s) => s.universe)
  const cfg = useStore((s) => s.cfg)
  if (!status) return null

  const softCap = Number(getPath(cfg?.config, 'instruments.subscription_soft_cap') ?? 0)
  const subs = universe?.subscribed ?? status.feed.subscribed
  const modes = Object.entries(status.feed.modes)

  return (
    <Card pad="20px 22px">
      <CardHead title="Subscriptions" sub="Live websocket subscriptions held by the feed" />
      <div style={{ marginTop: 14 }}>
        <KV k="Total subscriptions" v={int(subs)} />
        {modes.map(([k, v]) => <KV key={k} k={`${k} mode`} v={int(v)} />)}
        <KV k="Soft cap" v={softCap ? int(softCap) : DASH} />
        <KV k="Indices" v={int(universe?.indices.length ?? 0)} />
        <KV k="Universe" v={int(universe?.nifty50.length ?? 0)} />
      </div>
      {softCap ? (
        <>
          <div style={{ marginTop: 14, height: 7, borderRadius: 4, background: V.chip, overflow: 'hidden' }}>
            <div style={{
              height: '100%', width: pctWidth(subs, softCap),
              background: subs > softCap ? V.neg : V.accent, borderRadius: 4,
            }} />
          </div>
          <div style={{ fontSize: 11, color: V.faint, marginTop: 9 }}>
            {int(subs)} of a {int(softCap)} soft cap
          </div>
        </>
      ) : null}
    </Card>
  )
}

/* ------------------------------------------------------------------ latency */

function Latency() {
  const latency = useStore((s) => s.latency)
  const trades = latency.trades
  const legend = [
    { label: 'tick→signal', owner: 'ours', color: V.accent },
    { label: 'signal→req', owner: 'ours', color: `${V.accent}88` },
    { label: 'req→ack', owner: 'broker', color: V.warn },
    { label: 'ack→fill', owner: 'exchange', color: V.muted },
  ]
  const max = Math.max(...trades.map((t) => t.total_tick_to_fill_ms), 1)

  return (
    <Card>
      <CardHead
        title="Latency by trade"
        sub="Tick→signal and signal→request are ours. Request→ack is the broker, ack→fill the exchange."
        right={<div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          {legend.map((l) => (
            <div key={l.label} style={{
              display: 'flex', alignItems: 'center', gap: 6, fontSize: 11,
              color: V.muted, whiteSpace: 'nowrap',
            }}>
              <span style={{ width: 9, height: 9, borderRadius: 3, background: l.color }} />
              {l.label} <span style={{ color: V.faint }}>{l.owner}</span>
            </div>
          ))}
        </div>}
        style={{ marginBottom: 16 }}
      />
      {trades.length ? trades.map((t) => {
        const segs = [
          { v: t.tick_to_signal_us / 1000, color: legend[0].color, title: `tick→signal ${micros(t.tick_to_signal_us)}` },
          { v: t.signal_to_req_ms, color: legend[1].color, title: `signal→req ${millis(t.signal_to_req_ms)}` },
          { v: t.req_to_ack_ms, color: legend[2].color, title: `req→ack ${millis(t.req_to_ack_ms)}` },
          { v: t.ack_to_fill_ms, color: legend[3].color, title: `ack→fill ${millis(t.ack_to_fill_ms)}` },
        ]
        return (
          <div key={t.sig_id} style={{
            display: 'grid', gridTemplateColumns: 'minmax(0,200px) minmax(0,1fr) 84px',
            gap: 16, alignItems: 'center', padding: '11px 0', borderTop: `1px solid ${V.border}`,
          }}>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontFamily: MONO, fontSize: 12, ...ellip }}>{t.sym}</div>
              <div style={{ fontSize: 11, color: V.faint, fontFamily: MONO }}>{t.sig_id}</div>
            </div>
            <div style={{ display: 'flex', height: 18, borderRadius: 7, overflow: 'hidden', background: V.chip }}>
              {segs.map((s, i) => (
                <div key={i} title={s.title}
                  style={{ width: pctWidth(s.v, max), background: s.color, minWidth: 2 }} />
              ))}
            </div>
            <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 12, fontWeight: 600 }}>
              {millis(t.total_tick_to_fill_ms)}
            </div>
          </div>
        )
      }) : (
        <Empty title="No measurements recorded."
          why="Latency is captured at fill, so the first entry populates this." />
      )}
    </Card>
  )
}

/* ------------------------------------------------------------------ reconcile */

function Reconcile() {
  const refresh = useStore((s) => s.refresh)
  const [report, setReport] = useState<Record<string, string[]> | null>(null)
  const [busy, setBusy] = useState(false)

  const run = async () => {
    setBusy(true)
    try {
      const r = await api.reconcile()
      setReport(r)
      await refresh('positions')
      toast('Reconciled', 'Broker book re-read against local state.')
    } catch (e) {
      toast('Reconcile failed', e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const entries = Object.entries(report ?? {})

  return (
    <Card>
      <CardHead
        title="Broker reconciliation"
        sub="Re-reads the broker book and adopts or closes anything that drifted. Safe at any time."
        right={<Button onClick={() => void run()} disabled={busy}>
          {busy ? 'Reconciling…' : 'Reconcile now'}
        </Button>}
      />
      {entries.length ? (
        <div style={{
          display: 'grid', gridTemplateColumns: `repeat(${Math.min(4, entries.length)}, minmax(0,1fr))`,
          gap: 16, marginTop: 18, paddingTop: 18, borderTop: `1px solid ${V.border}`,
        }}>
          {entries.map(([k, v]) => (
            <div key={k} style={{
              border: `1px solid ${V.border}`, borderRadius: 14, background: V.sunken, padding: '15px 16px',
            }}>
              <div style={{
                fontSize: 11, letterSpacing: '.05em', textTransform: 'uppercase', color: V.muted,
              }}>{k.replace(/_/g, ' ')}</div>
              <div style={{ fontSize: 22, fontWeight: 600, marginTop: 7, fontFamily: MONO }}>
                {Array.isArray(v) ? v.length : 0}
              </div>
              {Array.isArray(v) && v.length ? (
                <div style={{ fontSize: 11, color: V.faint, marginTop: 6, fontFamily: MONO, ...ellip }}>
                  {v.join(', ')}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
    </Card>
  )
}
