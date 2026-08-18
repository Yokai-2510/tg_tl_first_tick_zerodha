import { useEffect, useRef, useState } from 'react'
import { PHASE_ORDER, type Phase } from '../lib/api'
import { DASH, duration, int, micros, millis, money, pct, timeFromUs } from '../lib/format'
import { PHASE_SCHEDULE } from '../lib/sections'
import { MONO, V, cut, ellip, pctWidth, tone } from '../lib/style'
import { useStore } from '../lib/store'
import { Bar, Card, CardHead, Kpi, Pill, Sparkline, StackedBar, StatHead } from '../components/ui'

/**
 * Samples a value each time it changes, capped to a fixed window.
 *
 * The backend keeps no P&L history — /status is a point-in-time snapshot — so the
 * curve below is what this console has watched since it was opened, not a
 * reconstruction of the day. The card says as much rather than implying a
 * server-side series that does not exist.
 */
function useSeries(value: number, cap = 120): { points: number[]; since: number } {
  const [points, setPoints] = useState<number[]>([value])
  const since = useRef(Date.now())
  const last = useRef(value)
  useEffect(() => {
    if (value === last.current) return
    last.current = value
    setPoints((p) => [...p, value].slice(-cap))
  }, [value, cap])
  return { points, since: since.current }
}

export default function Dashboard() {
  const status = useStore((s) => s.status)
  const positions = useStore((s) => s.positions)
  const orders = useStore((s) => s.orders)
  const signals = useStore((s) => s.signals)
  const latency = useStore((s) => s.latency)

  const realised = status?.positions.realised ?? 0
  const unrealised = status?.positions.unrealised ?? 0
  const charges = status?.positions.charges ?? 0
  const net = realised + unrealised

  const { points, since } = useSeries(Math.round(net * 100) / 100)
  const realisedSeries = useSeries(Math.round(realised * 100) / 100).points
  const unrealisedSeries = useSeries(Math.round(unrealised * 100) / 100).points

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1.62fr) minmax(0,1fr)', gap: 18 }}>
        <PnlCard net={net} realised={realised} unrealised={unrealised} charges={charges}
          points={points} since={since} />

        <div style={{ display: 'grid', gridTemplateRows: 'repeat(2, minmax(0,1fr))', gap: 18, minWidth: 0 }}>
          <SplitCard realised={realised} unrealised={unrealised}
            realisedSeries={realisedSeries} unrealisedSeries={unrealisedSeries}
            open={positions.length} exiting={positions.filter((p) => p.flags.exiting).length} />
          <CapitalCard />
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0,1fr))', gap: 18 }}>
        <Kpis />
      </div>

      <PhaseSequence />

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0,1fr))', gap: 18 }}>
        <Funnel orders={orders.length} signals={signals.length} />
        <FillQuality trades={latency.trades} median={latency.median_tick_to_fill_ms} />
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ P&L */

function PnlCard({ net, realised, unrealised, charges, points, since }: {
  net: number; realised: number; unrealised: number; charges: number
  points: number[]; since: number
}) {
  const [whole, frac] = cut(money(net, { sign: true }))
  const mn = Math.min(...points, 0)
  const mx = Math.max(...points, 0)
  const span = mx - mn || 1

  const W = 600
  const H = 184
  const pts = points.length > 1
    ? points.map((y, i) => [(i / (points.length - 1)) * W, H - 8 - ((y - mn) / span) * (H - 24)] as const)
    : [[0, H / 2] as const, [W, H / 2] as const]
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ')
  const area = `${line} L${W} ${H} L0 ${H} Z`
  const lastPt = pts[pts.length - 1]

  const grid = [mx, mx * 0.75 + mn * 0.25, (mx + mn) / 2, mx * 0.25 + mn * 0.75, mn]

  return (
    <Card pad="22px 24px 18px">
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16 }}>
        <div>
          <div style={{ fontSize: 12, letterSpacing: '.06em', textTransform: 'uppercase', color: V.muted }}>
            Session P&L
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 11, marginTop: 12, flexWrap: 'wrap' }}>
            <div style={{
              fontSize: 34, fontWeight: 600, letterSpacing: '-.035em',
              color: tone(net), whiteSpace: 'nowrap',
            }}>
              {whole}<span style={{ opacity: 0.5 }}>{frac}</span>
            </div>
            {charges ? (
              <Pill bg={V.chip} fg={V.muted}>charges {money(charges)}</Pill>
            ) : null}
          </div>
          <div style={{ fontSize: 12, color: V.muted, marginTop: 7 }}>
            Realised {money(realised, { sign: true })} · Unrealised {money(unrealised, { sign: true })}
          </div>
        </div>
        <div style={{ fontSize: 11, color: V.faint, textAlign: 'right', flex: 'none', lineHeight: 1.6 }}>
          sampled by this console<br />
          since {new Date(since).toLocaleTimeString('en-GB', { hour12: false })}
        </div>
      </div>

      <div style={{ position: 'relative', marginTop: 20, height: H }}>
        <div style={{
          position: 'absolute', inset: 0, display: 'flex',
          flexDirection: 'column', justifyContent: 'space-between',
        }}>
          {grid.map((y, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div style={{ width: 56, fontSize: 10, color: V.faint, textAlign: 'right', flex: 'none' }}>
                {money(y).replace('.00', '')}
              </div>
              <div style={{ flex: 1, borderTop: `1px dashed ${V.border}` }} />
            </div>
          ))}
        </div>
        <div style={{ position: 'absolute', left: 66, right: 0, top: 0, bottom: 0 }}>
          <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ width: '100%', height: '100%', display: 'block' }}>
            <defs>
              <linearGradient id="pnlgrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={V.accent} stopOpacity={0.2} />
                <stop offset="100%" stopColor={V.accent} stopOpacity={0} />
              </linearGradient>
            </defs>
            <path d={area} fill="url(#pnlgrad)" />
            <path d={line} fill="none" stroke={V.accent} strokeWidth={2.2} strokeLinejoin="round"
              strokeLinecap="round" vectorEffect="non-scaling-stroke" />
            <line x1={lastPt[0]} y1={4} x2={lastPt[0]} y2={H} stroke={V.border2} strokeWidth={1}
              strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
            <circle cx={lastPt[0]} cy={lastPt[1]} r={4.5} fill={V.accent} stroke={V.card}
              strokeWidth={2.5} vectorEffect="non-scaling-stroke" />
          </svg>
        </div>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', paddingLeft: 66, marginTop: 10 }}>
        <div style={{ fontSize: 10, color: V.faint }}>
          {new Date(since).toLocaleTimeString('en-GB', { hour12: false })}
        </div>
        <div style={{ fontSize: 10, color: V.faint }}>
          {points.length} sample{points.length === 1 ? '' : 's'}
        </div>
        <div style={{ fontSize: 10, color: V.text, fontWeight: 600 }}>now</div>
      </div>
    </Card>
  )
}

function SplitCard({ realised, unrealised, realisedSeries, unrealisedSeries, open, exiting }: {
  realised: number; unrealised: number
  realisedSeries: number[]; unrealisedSeries: number[]
  open: number; exiting: number
}) {
  const halves = [
    {
      label: 'Realised',
      value: realised,
      rail: realised ? V.pos : V.border2,
      sub: realised ? 'booked on closed positions' : 'nothing closed yet',
      series: realisedSeries,
      color: realised ? V.pos : V.border2,
    },
    {
      label: 'Unrealised',
      value: unrealised,
      rail: unrealised ? V.accent : V.border2,
      sub: open ? `${open} open${exiting ? `, ${exiting} exiting` : ''}` : 'nothing open',
      series: unrealisedSeries,
      color: unrealised ? V.accent : V.border2,
    },
  ]
  const total = realised + unrealised

  return (
    <Card pad="20px 22px" style={{ display: 'flex', flexDirection: 'column' }}>
      <StatHead title="Realised & unrealised" right={
        <Pill bg={total > 0 ? V.posbg : total < 0 ? V.negbg : V.chip}
          fg={total > 0 ? V.pos : total < 0 ? V.neg : V.muted}>
          {total ? money(total, { sign: true }) : 'flat'}
        </Pill>
      } />
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0,1fr))',
        gap: 20, marginTop: 15, flex: 1,
      }}>
        {halves.map((h) => {
          const [w, f] = cut(money(h.value))
          return (
            <div key={h.label} style={{
              minWidth: 0, display: 'flex', flexDirection: 'column',
              borderLeft: `2px solid ${h.rail}`, paddingLeft: 13,
            }}>
              <div style={{ fontSize: 11, letterSpacing: '.05em', textTransform: 'uppercase', color: V.muted }}>
                {h.label}
              </div>
              <div style={{
                fontSize: 24, fontWeight: 600, letterSpacing: '-.03em', marginTop: 8,
                color: tone(h.value), whiteSpace: 'nowrap',
              }}>
                {w}<span style={{ opacity: 0.5, fontSize: 17 }}>{f}</span>
              </div>
              <div style={{ fontSize: 11, color: V.muted, marginTop: 5, ...ellip }}>{h.sub}</div>
              <div style={{ flex: 1, minHeight: 10 }} />
              <Sparkline values={h.series} width={120} height={30} color={h.color} />
            </div>
          )
        })}
      </div>
    </Card>
  )
}

function CapitalCard() {
  const cap = useStore((s) => s.status?.capital)
  if (!cap) {
    return (
      <Card pad="20px 22px">
        <StatHead title="Capital" />
        <div style={{ fontSize: 12, color: V.faint, marginTop: 14 }}>Not reported by the backend.</div>
      </Card>
    )
  }

  const [w, f] = cut(money(cap.available))
  const total = cap.total || cap.opening_balance || 1
  const b = cap.breakdown

  return (
    <Card pad="20px 22px" style={{ display: 'flex', flexDirection: 'column' }}>
      <StatHead title="Capital" right={
        <Pill>{cap.simulated ? 'simulated' : `${pct(cap.deployed_pct, { sign: false })} deployed`}</Pill>
      } />
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 9, marginTop: 12 }}>
        <div style={{ fontSize: 28, fontWeight: 600, letterSpacing: '-.032em', whiteSpace: 'nowrap' }}>
          {w}<span style={{ opacity: 0.5, fontSize: 20 }}>{f}</span>
        </div>
        <div style={{ fontSize: 12, color: V.muted }}>available</div>
      </div>
      <div style={{ flex: 1, minHeight: 12 }} />
      <StackedBar segments={[
        { label: 'Option premium', value: money(b.option_premium), width: pctWidth(b.option_premium, total), color: V.accent },
        { label: 'SPAN', value: money(b.span), width: pctWidth(b.span, total), color: V.warn },
        { label: 'Exposure', value: money(b.exposure), width: pctWidth(b.exposure, total), color: V.muted },
        { label: 'Opening balance', value: money(cap.opening_balance), width: '0%', color: V.border2 },
      ]} />
    </Card>
  )
}

/* ------------------------------------------------------------------ KPIs */

function Kpis() {
  const status = useStore((s) => s.status)
  const positions = useStore((s) => s.positions)
  const signals = useStore((s) => s.signals)
  const universe = useStore((s) => s.universe)

  const armed = universe?.armed.length ?? status?.engine.armed ?? 0
  const fired = status?.engine.fired ?? 0
  const ticks = status?.feed.ticks ?? 0
  const age = status?.feed.last_tick_age_ms ?? null
  const connected = !!status?.feed.connected

  const openSeries = useSeries(positions.length).points
  const armedSeries = useSeries(armed).points
  const signalSeries = useSeries(signals.length).points
  const tickSeries = useSeries(ticks).points

  return (
    <>
      <Kpi title="Open positions" value={int(positions.length)}
        sub={positions.filter((p) => p.flags.exiting).length
          ? `${positions.filter((p) => p.flags.exiting).length} exiting`
          : 'none exiting'}
        dot={positions.length ? V.warn : V.faint}
        values={openSeries} sparkColor={positions.length ? V.accent : V.border2} />

      <Kpi title="Armed" value={int(armed)}
        sub={`${int(fired)} fired today`}
        dot={armed ? V.pos : V.faint}
        values={armedSeries} sparkColor={armed ? V.accent : V.border2} />

      <Kpi title="Signals" value={int(signals.length)}
        sub={signals.length ? `${int(status?.engine.intent_queue ?? 0)} queued` : 'none today'}
        dot={signals.length ? V.pos : V.faint}
        values={signalSeries} sparkColor={signals.length ? V.accent : V.border2} />

      <Kpi title="Feed" value={connected ? int(ticks) : 'Idle'}
        sub={connected
          ? `${int(status?.feed.subscribed ?? 0)} subscribed · ${age === null ? DASH : duration(age)}`
          : 'disconnected'}
        dot={connected ? V.pos : V.warn}
        values={tickSeries} sparkColor={connected ? V.pos : V.border2} />
    </>
  )
}

/* ------------------------------------------------------------------ sequence */

function PhaseSequence() {
  const status = useStore((s) => s.status)
  const phase = status?.phase ?? 'BOOT'
  const cur = PHASE_ORDER.indexOf(phase as Phase)

  // A phase that has already happened shows the time it actually ran; one that
  // has not shows its scheduled time. Both are useful, but never interchangeable.
  const actual = new Map<string, number>()
  for (const h of status?.history ?? []) actual.set(h.to, h.at_us)

  const trading = phase === 'TRADING' || phase === 'MANAGING'
  const curColor = phase === 'PHASE_1_FAIL' ? V.neg : trading ? V.pos : V.muted
  const curBg = phase === 'PHASE_1_FAIL' ? V.negbg : trading ? V.posbg : V.chip

  return (
    <Card pad="22px 24px 20px">
      <CardHead
        title="Session sequence"
        sub="Completed steps show the time they actually ran, upcoming ones their schedule"
        right={<div style={{ fontSize: 11, color: V.faint, whiteSpace: 'nowrap' }}>
          {cur >= 0 ? `${cur + 1} of ${PHASE_ORDER.length}` : phase}
        </div>}
        style={{ marginBottom: 22 }}
      />
      <div style={{ overflowX: 'auto', paddingBottom: 6 }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', minWidth: 1000 }}>
          {PHASE_ORDER.map((p, i) => {
            const done = cur >= 0 && i < cur
            const isCur = i === cur
            const at = actual.get(p)
            const sched = status?.schedule?.[PHASE_SCHEDULE[p] ?? '']
            return (
              <div key={p} style={{
                flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column',
                alignItems: 'center', gap: 10,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', width: '100%' }}>
                  <div style={{
                    flex: 1, height: 2,
                    background: i === 0 ? 'transparent' : i <= cur ? V.muted : V.border,
                  }} />
                  <div style={{
                    width: 13, height: 13, borderRadius: '50%', flex: 'none',
                    border: `2px solid ${done ? V.muted : isCur ? curColor : V.border2}`,
                    background: done ? V.muted : isCur ? curColor : V.card,
                    boxShadow: isCur ? `0 0 0 4px ${curBg}` : 'none',
                  }} />
                  <div style={{
                    flex: 1, height: 2,
                    background: i === PHASE_ORDER.length - 1 ? 'transparent' : i < cur ? V.muted : V.border,
                  }} />
                </div>
                <div style={{
                  fontSize: 10, fontWeight: isCur ? 700 : 500, letterSpacing: '.03em',
                  color: isCur ? curColor : done ? V.text : V.faint,
                  textAlign: 'center', lineHeight: 1.3,
                }}>{p}</div>
                <div style={{ fontSize: 10, fontFamily: MONO, color: isCur ? curColor : V.faint }}>
                  {at ? timeFromUs(at) : sched ?? DASH}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </Card>
  )
}

/* ------------------------------------------------------------------ funnel */

function Funnel({ orders, signals }: { orders: number; signals: number }) {
  const status = useStore((s) => s.status)
  const positions = useStore((s) => s.positions)
  const closed = useStore((s) => s.closed)

  const armed = status?.engine.armed ?? 0
  const filled = [...positions, ...closed].filter((p) => p.entry.filled_qty > 0).length
  const rows: [string, number][] = [
    ['Armed instruments', armed],
    ['Signals fired', signals],
    ['Orders sent', orders],
    ['Filled', filled],
  ]
  const top = Math.max(armed, signals, orders, filled, 1)

  return (
    <Card>
      <CardHead title="Entry funnel"
        right={<div style={{ fontSize: 11, color: V.faint }}>today</div>}
        style={{ marginBottom: 18 }} />
      {rows.map(([label, value]) => (
        <div key={label} style={{ marginBottom: 14 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 10 }}>
            <div style={{ fontSize: 12, color: V.muted }}>{label}</div>
            <div style={{ fontSize: 13, fontWeight: 600, fontFamily: MONO }}>{int(value)}</div>
          </div>
          <Bar fill={pctWidth(value, top)} color={V.accent} height={7} style={{ marginTop: 7 }} />
        </div>
      ))}
    </Card>
  )
}

function FillQuality({ trades, median }: {
  trades: { tick_to_signal_us: number; signal_to_req_ms: number; req_to_ack_ms: number; ack_to_fill_ms: number }[]
  median: number | null
}) {
  const n = trades.length
  const avg = (get: (t: typeof trades[number]) => number) =>
    n ? trades.reduce((a, t) => a + get(t), 0) / n : 0

  const parts = [
    { label: 'tick→signal (ours)', value: avg((t) => t.tick_to_signal_us) / 1000, color: V.accent, us: true },
    { label: 'signal→req (ours)', value: avg((t) => t.signal_to_req_ms), color: `${V.accent}88`, us: false },
    { label: 'req→ack (broker)', value: avg((t) => t.req_to_ack_ms), color: V.warn, us: false },
    { label: 'ack→fill (exchange)', value: avg((t) => t.ack_to_fill_ms), color: V.muted, us: false },
  ]
  const total = parts.reduce((a, p) => a + p.value, 0) || 1

  return (
    <Card>
      <CardHead
        title="Fill quality"
        sub="Mean of each leg across today's fills, split by owner"
        right={<div style={{ fontSize: 26, fontWeight: 600, letterSpacing: '-.03em', fontFamily: MONO }}>
          {median === null ? DASH : millis(median)}
        </div>}
        style={{ marginBottom: 16 }}
      />
      {n ? (
        <StackedBar height={10} segments={parts.map((p) => ({
          label: p.label,
          value: p.us ? micros(p.value * 1000) : millis(p.value),
          width: pctWidth(p.value, total),
          color: p.color,
        }))} />
      ) : (
        <div style={{ fontSize: 12, color: V.faint, lineHeight: 1.7, padding: '10px 0' }}>
          No fills measured yet. Latency is captured at fill, so this fills in with the first entry.
        </div>
      )}
    </Card>
  )
}
