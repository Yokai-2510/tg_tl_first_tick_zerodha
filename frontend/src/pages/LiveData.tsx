import { useMemo, useState } from 'react'
import { api, type ArmedRow, type MarketRow, type RankRow } from '../lib/api'
import { useStore } from '../lib/store'
import { int, micros, pct, price, signClass, DASH } from '../lib/format'
import { Card, Field, Pill, Section, Stat, Table, Tabs } from '../components/ui'
import { Segmented, SortTh, useSort, type Col } from '../components/sortable'

type Tab = 'armed' | 'ranking' | 'ticks'

export default function LiveData() {
  const universe = useStore((s) => s.universe)
  const status = useStore((s) => s.status)
  const cfg = useStore((s) => s.cfg)
  const market = useStore((s) => s.market)
  const [tab, setTab] = useState<Tab>('armed')

  const cap = cfg?.config?.instruments?.subscription_soft_cap ?? 2400
  const armed = universe?.armed ?? []
  const fired = armed.filter((a) => a.fired).length

  return (
    <div className="space-y-4">
      <div className="grid gap-3 grid-cols-2 lg:grid-cols-5">
        <Stat label="Subscribed" value={int(universe?.subscribed ?? 0)} sub={`cap ${int(cap)}`} />
        <Stat label="Armed" value={int(armed.length)} sub={`${fired} fired`} />
        <Stat label="Tradeable" value={int(universe?.tradeable.length ?? 0)}
              sub="may fire entries" hint="Top-N ranked names" />
        <Stat label="Buffer" value={int(universe?.buffer.length ?? 0)}
              sub="subscribed, not traded"
              hint="Extra ranks kept live in case the ranking shifts" />
        <Stat label="Ticks tracked" value={int(Object.keys(market).length)}
              sub={`${int(status?.engine.ticks_seen ?? 0)} received`} />
      </div>

      {universe && (universe.tradeable.length > 0 || universe.buffer.length > 0) && (
        <Card label="Selection">
          <div className="flex flex-wrap gap-1.5 mb-2">
            {universe.tradeable.map((s) => <Pill key={s} tone="text-pos border-pos/40">{s}</Pill>)}
            {universe.buffer.map((s) => <Pill key={s} tone="text-muted border-line">{s}</Pill>)}
          </div>
          <div className="text-[11px] text-muted">
            Green = tradeable (may fire an entry). Grey = buffer (subscribed only, never traded).
          </div>
        </Card>
      )}

      <Section title="Instruments">
        <Tabs value={tab} onChange={setTab} tabs={[
          { id: 'armed', label: 'Armed', count: armed.length },
          { id: 'ranking', label: 'Ranking', count: useStore.getState().ranking.length },
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

// ---------------------------------------------------------------- armed

type ArmedLive = ArmedRow & { diff: number; bid: number; ask: number; volume: number; oi: number }

function ArmedTable({ phase }: { phase: string }) {
  const armed = useStore((s) => s.universe?.armed ?? [])
  const market = useStore((s) => s.market)
  const [q, setQ] = useState('')
  const [side, setSide] = useState<'all' | 'ce' | 'pe' | 'fired'>('all')

  const rows: ArmedLive[] = useMemo(() => {
    const needle = q.trim().toUpperCase()
    return armed
      .map((a) => {
        const m: MarketRow | undefined = market[String(a.token)]
        const ltp = m?.ltp ?? a.ltp
        return {
          ...a, ltp, diff: ltp - a.ref_price,
          bid: m?.bid ?? 0, ask: m?.ask ?? 0,
          volume: m?.volume ?? 0, oi: m?.oi ?? 0,
        }
      })
      .filter((a) => {
        if (needle && !a.symbol.includes(needle) && !a.underlying.includes(needle)) return false
        if (side === 'ce') return a.symbol.endsWith('CE')
        if (side === 'pe') return a.symbol.endsWith('PE')
        if (side === 'fired') return a.fired
        return true
      })
  }, [armed, market, q, side])

  const cols: Col<ArmedLive>[] = [
    { id: 'symbol', label: 'Symbol', get: (r) => r.symbol, num: false },
    { id: 'underlying', label: 'Under', get: (r) => r.underlying, num: false },
    { id: 'ref', label: 'Reference', get: (r) => r.ref_price, num: true,
      hint: 'Previous close — options do not trade in the pre-open' },
    { id: 'ltp', label: 'LTP', get: (r) => r.ltp, num: true },
    { id: 'diff', label: 'Diff', get: (r) => r.diff, num: true,
      hint: 'LTP minus reference. The first positive diff fires the entry.' },
    { id: 'bid', label: 'Bid', get: (r) => r.bid, num: true },
    { id: 'ask', label: 'Ask', get: (r) => r.ask, num: true },
    { id: 'volume', label: 'Volume', get: (r) => r.volume, num: true,
      hint: 'Contracts traded today on this strike' },
    { id: 'oi', label: 'OI', get: (r) => r.oi, num: true,
      hint: 'Open interest on this strike — favours liquid chains' },
    { id: 'lots', label: 'Lots', get: (r) => r.lots, num: true },
  ]
  const { sorted, by, dir, toggle } = useSort(rows, cols, 'diff', 'desc')

  const empty = ['BOOT', 'IDLE', 'PHASE_1', 'FEED_LIVE', 'PREOPEN', 'SETTLEMENT'].includes(phase)
    ? 'Nothing armed yet — option chains subscribe after the settlement snapshot.'
    : 'No armed instruments match this filter.'

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2 items-center">
        <input className="inp max-w-xs" placeholder="Filter symbol or underlying"
               value={q} onChange={(e) => setQ(e.target.value)} />
        <Segmented value={side} onChange={setSide} options={[
          { id: 'all', label: 'All' }, { id: 'ce', label: 'CE' },
          { id: 'pe', label: 'PE' }, { id: 'fired', label: 'Fired' },
        ]} />
        <span className="text-micro text-muted ml-auto">{sorted.length} of {armed.length}</span>
      </div>

      <Table colSpan={cols.length} empty={empty}
        head={cols.map((c) => (
          <SortTh key={c.id} col={c} by={by} dir={dir} onSort={toggle} />
        ))}>
        {sorted.map((a) => (
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
            <td className="td num mono text-[11px]">{a.volume ? int(a.volume) : DASH}</td>
            <td className="td num mono text-[11px]">{a.oi ? int(a.oi) : DASH}</td>
            <td className="td num">{a.lots}</td>
          </tr>
        ))}
      </Table>
      <div className="text-[11px] text-muted">
        Diff = LTP − reference. The reference for an option is its previous close, because
        options do not trade in the pre-open. Bid/ask and volume show — when the book is
        empty (market closed).
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- ranking

function RankingTable() {
  const ranking = useStore((s) => s.ranking)
  const universe = useStore((s) => s.universe)
  const buffer = new Set(universe?.buffer ?? [])
  const [side, setSide] = useState<'all' | 'gainers' | 'losers' | 'selected'>('all')

  const rows = useMemo(() => ranking.filter((r) => {
    if (side === 'gainers') return r.change_pct > 0
    if (side === 'losers') return r.change_pct < 0
    if (side === 'selected') return r.selected || buffer.has(r.symbol)
    return true
  }), [ranking, side, buffer])

  const cols: Col<RankRow>[] = [
    { id: 'rank', label: '#', get: (r) => r.rank, num: true },
    { id: 'symbol', label: 'Symbol', get: (r) => r.symbol, num: false },
    { id: 'prev', label: 'Prev close', get: (r) => r.prev_close, num: true },
    { id: 'ltp', label: 'Settlement', get: (r) => r.ltp, num: true,
      hint: 'Price at the 09:09 settlement snapshot' },
    { id: 'chg', label: 'Change %', get: (r) => r.change_pct, num: true },
    { id: 'volume', label: 'Volume', get: (r) => r.volume ?? 0, num: true,
      hint: 'Pre-open auction volume — filters out names that gapped on nothing' },
    { id: 'high', label: 'High', get: (r) => r.high ?? 0, num: true },
    { id: 'low', label: 'Low', get: (r) => r.low ?? 0, num: true },
    { id: 'sel', label: 'Selection' },
  ]
  const { sorted, by, dir, toggle } = useSort(rows, cols, 'chg', 'desc')

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2 items-center">
        <Segmented value={side} onChange={setSide} options={[
          { id: 'all', label: 'All 50' }, { id: 'gainers', label: 'Gainers' },
          { id: 'losers', label: 'Losers' }, { id: 'selected', label: 'Selected' },
        ]} />
        <span className="text-micro text-muted ml-auto">{sorted.length} symbols</span>
      </div>
      <Table colSpan={cols.length}
        empty="Ranking is computed at the settlement snapshot (09:09)."
        head={cols.map((c) => (
          <SortTh key={c.id} col={c} by={by} dir={dir} onSort={toggle} />
        ))}>
        {sorted.map((r) => (
          <tr key={r.symbol} className={r.selected ? 'bg-pos/5' : 'hover:bg-surface/60'}>
            <td className="td num text-muted">{r.rank}</td>
            <td className="td font-medium">{r.symbol}</td>
            <td className="td num">{price(r.prev_close)}</td>
            <td className="td num">{price(r.ltp)}</td>
            <td className={`td num font-medium ${signClass(r.change_pct)}`}>{pct(r.change_pct)}</td>
            <td className="td num mono text-[11px]">{r.volume ? int(r.volume) : DASH}</td>
            <td className="td num">{price(r.high, { zeroIsDash: true })}</td>
            <td className="td num">{price(r.low, { zeroIsDash: true })}</td>
            <td className="td">
              {r.selected ? <Pill tone="text-pos border-pos/40">Tradeable</Pill>
                : buffer.has(r.symbol) ? <Pill tone="text-muted border-line">Buffer</Pill>
                : <span className="text-muted">{DASH}</span>}
            </td>
          </tr>
        ))}
      </Table>
    </div>
  )
}

// ---------------------------------------------------------------- all ticks

type TickRow = MarketRow & { token: string }

function TicksTable() {
  const market = useStore((s) => s.market)
  const [q, setQ] = useState('')
  const [kind, setKind] = useState<'all' | 'options' | 'equity'>('all')

  const rows: TickRow[] = useMemo(() => {
    const needle = q.trim().toUpperCase()
    return Object.entries(market)
      .map(([token, r]) => ({ token, ...r }))
      .filter((r) => {
        const sym = r.sym ?? ''
        if (needle && !sym.includes(needle)) return false
        // A suffix test is WRONG here: BAJFINANCE ends in "CE". An option's
        // tradingsymbol always differs from its underlying; an equity's does not.
        const isOpt = !!r.underlying && sym !== r.underlying
        if (kind === 'options') return isOpt
        if (kind === 'equity') return !isOpt
        return true
      })
  }, [market, q, kind])

  const cols: Col<TickRow>[] = [
    { id: 'sym', label: 'Symbol', get: (r) => r.sym ?? '', num: false },
    { id: 'token', label: 'Token', get: (r) => Number(r.token), num: true },
    { id: 'ltp', label: 'LTP', get: (r) => r.ltp, num: true },
    { id: 'bid', label: 'Bid', get: (r) => r.bid, num: true },
    { id: 'ask', label: 'Ask', get: (r) => r.ask, num: true },
    { id: 'spread', label: 'Spread', get: (r) => (r.ask > 0 && r.bid > 0 ? r.ask - r.bid : -1), num: true,
      hint: 'Ask minus bid. Wide spreads fill worse.' },
    { id: 'volume', label: 'Volume', get: (r) => r.volume ?? 0, num: true },
    { id: 'oi', label: 'OI', get: (r) => r.oi ?? 0, num: true },
    { id: 'lag', label: 'Feed lag', get: (r) => r.feed_lag_us ?? 0, num: true,
      hint: "The exchange's dissemination delay, not ours" },
  ]
  const { sorted, by, dir, toggle } = useSort(rows, cols, 'volume', 'desc')
  const shown = sorted.slice(0, 600)

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2 items-center">
        <input className="inp max-w-xs" placeholder="Filter symbol"
               value={q} onChange={(e) => setQ(e.target.value)} />
        <Segmented value={kind} onChange={setKind} options={[
          { id: 'all', label: 'All' }, { id: 'options', label: 'Options' },
          { id: 'equity', label: 'Equity & index' },
        ]} />
        <span className="text-micro text-muted ml-auto">
          {shown.length < sorted.length ? `${shown.length} of ${sorted.length}` : `${sorted.length}`}
        </span>
      </div>
      <Table colSpan={cols.length} empty="No ticks received yet."
        head={cols.map((c) => (
          <SortTh key={c.id} col={c} by={by} dir={dir} onSort={toggle} />
        ))}>
        {shown.map((r) => {
          const spread = r.ask > 0 && r.bid > 0 ? r.ask - r.bid : null
          return (
            <tr key={r.token} className="hover:bg-surface/60">
              <td className="td font-medium">{r.sym ?? DASH}</td>
              <td className="td num mono text-[11px] text-muted">{r.token}</td>
              <td className="td num">{price(r.ltp)}</td>
              <td className="td num">{price(r.bid, { zeroIsDash: true })}</td>
              <td className="td num">{price(r.ask, { zeroIsDash: true })}</td>
              <td className="td num">{spread === null ? DASH : price(spread)}</td>
              <td className="td num mono text-[11px]">{r.volume ? int(r.volume) : DASH}</td>
              <td className="td num mono text-[11px]">{r.oi ? int(r.oi) : DASH}</td>
              <td className="td num text-muted mono text-[11px]">{micros(r.feed_lag_us)}</td>
            </tr>
          )
        })}
      </Table>
    </div>
  )
}

// ---------------------------------------------------------------- manual

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
