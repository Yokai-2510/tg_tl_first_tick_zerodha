import { useMemo, useState } from 'react'
import { api } from '../lib/api'
import { useStore } from '../lib/store'
import { int, micros, pct, price, signClass, DASH } from '../lib/format'
import { Card, Field, Pill, Section, Stat, Table, Tabs } from '../components/ui'

type Tab = 'armed' | 'ranking' | 'ticks'

export default function LiveData() {
  const universe = useStore((s) => s.universe)
  const ranking = useStore((s) => s.ranking)
  const market = useStore((s) => s.market)
  const status = useStore((s) => s.status)
  const cfg = useStore((s) => s.cfg)
  const [tab, setTab] = useState<Tab>('armed')

  const cap = cfg?.config?.instruments?.subscription_soft_cap ?? 2400
  const armed = universe?.armed ?? []
  const fired = armed.filter((a) => a.fired).length

  return (
    <div className="space-y-4">
      <div className="grid gap-3 grid-cols-2 lg:grid-cols-5">
        <Stat label="Subscribed" value={int(universe?.subscribed ?? 0)}
              sub={`cap ${int(cap)}`} />
        <Stat label="Armed" value={int(armed.length)} sub={`${fired} fired`} />
        <Stat label="Tradeable" value={int(universe?.tradeable.length ?? 0)}
              sub="fire entries" hint="Top-N ranked names" />
        <Stat label="Buffer" value={int(universe?.buffer.length ?? 0)}
              sub="subscribed, not traded"
              hint="Extra ranks kept live in case the ranking shifts" />
        <Stat label="Indices" value={int(universe?.indices.length ?? 0)}
              sub={universe?.indices.join(' · ') || DASH} />
      </div>

      {universe && (
        <Card label="Selection">
          <div className="flex flex-wrap gap-1.5 mb-2">
            {universe.tradeable.map((s) => (
              <Pill key={s} tone="text-pos border-pos/40">{s}</Pill>
            ))}
            {universe.buffer.map((s) => (
              <Pill key={s} tone="text-muted border-line">{s}</Pill>
            ))}
          </div>
          <div className="text-[11px] text-muted">
            Green = tradeable (may fire an entry). Grey = buffer (subscribed only, never traded).
          </div>
        </Card>
      )}

      <Section title="Instruments">
        <Tabs value={tab} onChange={setTab} tabs={[
          { id: 'armed', label: 'Armed', count: armed.length },
          { id: 'ranking', label: 'Ranking', count: ranking.length },
          { id: 'ticks', label: 'All ticks', count: Object.keys(market).length },
        ]} />
      </Section>

      {tab === 'armed' && <ArmedTable phase={status?.phase ?? ''} />}
      {tab === 'ranking' && <RankingTable />}
      {tab === 'ticks' && <TicksTable />}

      <ManualPanel />
    </div>
  )
}

function ArmedTable({ phase }: { phase: string }) {
  const armed = useStore((s) => s.universe?.armed ?? [])
  const market = useStore((s) => s.market)
  const [q, setQ] = useState('')

  const rows = useMemo(() => {
    const needle = q.trim().toUpperCase()
    return armed
      .map((a) => {
        const live = market[String(a.token)]
        const ltp = live?.ltp ?? a.ltp
        return { ...a, ltp, bid: live?.bid ?? 0, ask: live?.ask ?? 0, diff: ltp - a.ref_price }
      })
      .filter((a) => !needle || a.symbol.includes(needle) || a.underlying.includes(needle))
      .sort((a, b) => b.diff - a.diff)
  }, [armed, market, q])

  const empty = ['BOOT', 'IDLE', 'PHASE_1', 'FEED_LIVE', 'PREOPEN', 'SETTLEMENT'].includes(phase)
    ? 'Nothing armed yet — option chains subscribe after the settlement snapshot.'
    : 'No armed instruments.'

  return (
    <div className="space-y-2">
      <input className="inp max-w-xs" placeholder="Filter symbol or underlying"
             value={q} onChange={(e) => setQ(e.target.value)} />
      <Table colSpan={8} empty={empty} head={<>
        <th className="th">Symbol</th>
        <th className="th">Under</th>
        <th className="th num">Reference</th>
        <th className="th num">LTP</th>
        <th className="th num">Diff</th>
        <th className="th num">Bid</th>
        <th className="th num">Ask</th>
        <th className="th num">Lots</th>
      </>}>
        {rows.map((a) => (
          <tr key={a.token} className={a.fired ? 'bg-accent/5' : 'hover:bg-surface/60'}>
            <td className="td font-medium">
              {a.symbol}
              {a.fired && <span className="ml-2 text-[10px] text-accent uppercase">fired</span>}
            </td>
            <td className="td text-muted">{a.underlying}</td>
            <td className="td num">{price(a.ref_price)}</td>
            <td className="td num">{price(a.ltp)}</td>
            <td className={`td num font-medium ${signClass(a.diff)}`}>{price(a.diff)}</td>
            <td className="td num">{price(a.bid, { zeroIsDash: true })}</td>
            <td className="td num">{price(a.ask, { zeroIsDash: true })}</td>
            <td className="td num">{a.lots}</td>
          </tr>
        ))}
      </Table>
      <div className="text-[11px] text-muted">
        Diff = LTP − reference. The reference for an option is its previous close, because
        options do not trade in the pre-open. The first positive diff fires the entry.
      </div>
    </div>
  )
}

function RankingTable() {
  const ranking = useStore((s) => s.ranking)
  const universe = useStore((s) => s.universe)
  const buffer = new Set(universe?.buffer ?? [])

  return (
    <Table colSpan={6} empty="Ranking is computed at the settlement snapshot (09:09)." head={<>
      <th className="th num">#</th>
      <th className="th">Symbol</th>
      <th className="th num">Prev close</th>
      <th className="th num">Settlement</th>
      <th className="th num">Change</th>
      <th className="th">Selection</th>
    </>}>
      {ranking.map((r) => (
        <tr key={r.symbol} className={r.selected ? 'bg-pos/5' : 'hover:bg-surface/60'}>
          <td className="td num text-muted">{r.rank}</td>
          <td className="td font-medium">{r.symbol}</td>
          <td className="td num">{price(r.prev_close)}</td>
          <td className="td num">{price(r.ltp)}</td>
          <td className={`td num font-medium ${signClass(r.change_pct)}`}>{pct(r.change_pct)}</td>
          <td className="td">
            {r.selected
              ? <Pill tone="text-pos border-pos/40">Tradeable</Pill>
              : buffer.has(r.symbol)
                ? <Pill tone="text-muted border-line">Buffer</Pill>
                : <span className="text-muted">{DASH}</span>}
          </td>
        </tr>
      ))}
    </Table>
  )
}

function TicksTable() {
  const market = useStore((s) => s.market)
  const [q, setQ] = useState('')
  const rows = useMemo(() => {
    const needle = q.trim().toUpperCase()
    return Object.entries(market)
      .map(([token, r]) => ({ token, ...r }))
      .filter((r) => !needle || (r.sym ?? '').includes(needle))
      .sort((a, b) => (a.sym ?? '').localeCompare(b.sym ?? ''))
      .slice(0, 600)
  }, [market, q])

  return (
    <div className="space-y-2">
      <input className="inp max-w-xs" placeholder="Filter symbol"
             value={q} onChange={(e) => setQ(e.target.value)} />
      <Table colSpan={6} empty="No ticks received yet." head={<>
        <th className="th">Symbol</th>
        <th className="th mono">Token</th>
        <th className="th num">LTP</th>
        <th className="th num">Bid</th>
        <th className="th num">Ask</th>
        <th className="th num">Feed lag</th>
      </>}>
        {rows.map((r) => (
          <tr key={r.token} className="hover:bg-surface/60">
            <td className="td font-medium">{r.sym ?? DASH}</td>
            <td className="td mono text-muted">{r.token}</td>
            <td className="td num">{price(r.ltp)}</td>
            <td className="td num">{price(r.bid, { zeroIsDash: true })}</td>
            <td className="td num">{price(r.ask, { zeroIsDash: true })}</td>
            <td className="td num text-muted">{micros(r.feed_lag_us)}</td>
          </tr>
        ))}
      </Table>
      <div className="text-[11px] text-muted">
        Bid/ask show — when the book is empty (market closed). Feed lag is the exchange's
        dissemination delay, not ours; it is legitimately hours-large outside market hours.
      </div>
    </div>
  )
}

function ManualPanel() {
  const cfg = useStore((s) => s.cfg)
  const refresh = useStore((s) => s.refresh)
  const setError = useStore((s) => s.setError)
  const [sym, setSym] = useState('')
  const [busy, setBusy] = useState(false)
  const cutoff = cfg?.config?.schedule?.manual_cutoff ?? '09:14:00'
  const manual: any[] = cfg?.config?.universe?.manual_instruments ?? []

  const add = async () => {
    if (!sym.trim()) return
    setBusy(true)
    try { await api.manualAdd(sym.trim().toUpperCase()); setSym(''); await refresh('config'); setError(null) }
    catch (e: any) { setError(e?.message ?? 'Add failed') }
    finally { setBusy(false) }
  }
  const remove = async (s: string) => {
    setBusy(true)
    try { await api.manualRemove(s); await refresh('config'); setError(null) }
    catch (e: any) { setError(e?.message ?? 'Remove failed') }
    finally { setBusy(false) }
  }

  return (
    <Card label="Manual instruments"
          hint={`Accepted until ${cutoff}; the instrument set freezes after that.`}>
      <div className="flex gap-2 items-end mb-3">
        <div className="w-56">
          <Field label="Underlying symbol">
            <input className="inp" placeholder="e.g. INDIGO" value={sym}
                   onChange={(e) => setSym(e.target.value)}
                   onKeyDown={(e) => { if (e.key === 'Enter') void add() }} />
          </Field>
        </div>
        <button className="btn btn-primary" disabled={busy || !sym.trim()} onClick={add}>Add</button>
      </div>
      {manual.length === 0
        ? <div className="text-micro text-muted">
            None. Manual additions are accepted until {cutoff}, after which the backend
            refuses them and the instrument set is frozen.
          </div>
        : <div className="flex flex-wrap gap-1.5">
            {manual.map((m, i) => (
              <button key={i} className="btn h-6 px-2" disabled={busy}
                      onClick={() => remove(String(m.symbol ?? m))}>
                {String(m.symbol ?? m)} ✕
              </button>
            ))}
          </div>}
    </Card>
  )
}
