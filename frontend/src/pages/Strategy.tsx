import { useMemo, useState } from 'react'
import { api } from '../lib/api'
import { DASH, int, pct, price } from '../lib/format'
import { buildPatch, getPath } from '../lib/patch'
import { STRATEGY } from '../lib/sections'
import { MONO, V, badge, cardSub, cardTitleLg, ellip, tone } from '../lib/style'
import { useStore } from '../lib/store'
import { toast } from '../lib/toast'
import ConfigForm from '../components/ConfigForm'
import {
  Button, Card, CardHead, Empty, Pill, Scroller, Segmented, Stepper, Thead, Toggle, Trow,
} from '../components/ui'

export default function Strategy() {
  const [tab, setTab] = useState(STRATEGY[0].id)
  const section = STRATEGY.find((s) => s.id === tab) ?? STRATEGY[0]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <Segmented value={tab} onChange={setTab}
        options={STRATEGY.map((s) => ({ key: s.id, label: s.title }))} />
      {section.custom === 'instruments' ? <Instruments /> : <ConfigForm section={section} />}
    </div>
  )
}

/* ------------------------------------------------------------------ instruments */

type View = 'selected' | 'auto' | 'manual' | 'index' | 'baselines'

function Instruments() {
  const universe = useStore((s) => s.universe)
  const cfg = useStore((s) => s.cfg)
  const [view, setView] = useState<View>('selected')

  const manual = (getPath(cfg?.config, 'universe.manual_instruments') as unknown[] | undefined) ?? []
  const indices = (getPath(cfg?.config, 'universe.indices') as Record<string, { enabled?: boolean }> | undefined) ?? {}
  const indexOn = Object.entries(indices).filter(([, v]) => v?.enabled).length

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <Segmented<View> value={view} onChange={setView} options={[
        { key: 'selected', label: 'Selected', count: (universe?.tradeable.length ?? 0) + (universe?.buffer.length ?? 0) },
        { key: 'auto', label: 'Automatic' },
        { key: 'manual', label: 'Manual', count: manual.length },
        { key: 'index', label: 'Indexes', count: indexOn },
        { key: 'baselines', label: 'Baselines', count: universe?.armed.length ?? 0 },
      ]} />
      {view === 'selected' ? <Selected /> : null}
      {view === 'auto' ? <Automatic /> : null}
      {view === 'manual' ? <Manual /> : null}
      {view === 'index' ? <Indexes /> : null}
      {view === 'baselines' ? <Baselines /> : null}
    </div>
  )
}

const SEL_COLS = 'minmax(130px,1fr) 96px 84px 62px 100px minmax(150px,1.1fr)'

function Selected() {
  const universe = useStore((s) => s.universe)
  const ranking = useStore((s) => s.ranking)
  const cfg = useStore((s) => s.cfg)

  const rows = useMemo(() => {
    const byRank = new Map(ranking.map((r) => [r.symbol, r]))
    const manual = new Set(
      ((getPath(cfg?.config, 'universe.manual_instruments') as unknown[] | undefined) ?? [])
        .map((m) => (typeof m === 'string' ? m : String((m as { symbol?: string }).symbol ?? ''))),
    )
    const indices = new Set(universe?.indices ?? [])
    const lots = Number(getPath(cfg?.config, 'entry.lots_default') ?? 1)

    const out: {
      sym: string; src: string; srcBg: string; srcFg: string
      lots: string; base: string; dir: string; dirColor: string; note: string
    }[] = []

    const add = (sym: string, src: string, srcBg: string, srcFg: string, note: string) => {
      const r = byRank.get(sym)
      out.push({
        sym, src, srcBg, srcFg,
        lots: String(lots),
        base: r ? price(r.prev_close) : DASH,
        dir: r ? (r.change_pct > 0 ? 'CE' : r.change_pct < 0 ? 'PE' : 'flat') : 'pending',
        dirColor: r ? tone(r.change_pct) : V.muted,
        note,
      })
    }

    for (const s of universe?.tradeable ?? []) {
      if (indices.has(s)) add(s, 'INDEX', V.warnbg, V.warn, 'armed directly, not ranked')
      else if (manual.has(s)) add(s, 'MANUAL', V.posbg, V.pos, 'added by hand before the cutoff')
      else add(s, 'RANKED', V.chip, V.muted, `rank ${byRank.get(s)?.rank ?? DASH} at the settlement snapshot`)
    }
    for (const s of universe?.buffer ?? []) {
      add(s, 'BUFFER', V.chip, V.faint, 'subscribed only — never traded')
    }
    return out
  }, [universe, ranking, cfg])

  const counts = [
    { k: 'Tradeable', v: universe?.tradeable.length ?? 0 },
    { k: 'Buffer', v: universe?.buffer.length ?? 0 },
    { k: 'Armed', v: universe?.armed.length ?? 0 },
    { k: 'Total', v: rows.length },
  ]

  return (
    <Card pad={false} style={{ overflow: 'hidden' }}>
      <div style={{
        display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between',
        gap: 16, padding: '20px 22px', borderBottom: `1px solid ${V.border}`, flexWrap: 'wrap',
      }}>
        <div>
          <div style={cardTitleLg}>Selected instruments</div>
          <div style={cardSub}>
            Everything the engine holds for this session. Tradeable names may fire entries;
            buffer names exist so a ranking shuffle near the open cannot leave us without a live chain.
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          {counts.map((c) => (
            <div key={c.k} style={{
              padding: '5px 11px', borderRadius: 8, background: V.chip,
              fontSize: 11, color: V.muted, whiteSpace: 'nowrap',
            }}>
              {c.k} <span style={{ color: V.text, fontWeight: 600 }}>{int(c.v)}</span>
            </div>
          ))}
        </div>
      </div>
      <Scroller min={760} maxHeight={560}>
        <Thead cols={SEL_COLS} pad="10px 22px">
          <div>Underlying</div>
          <div>Source</div>
          <div>Direction</div>
          <div style={{ textAlign: 'right' }}>Lots</div>
          <div style={{ textAlign: 'right' }}>Baseline</div>
          <div>Note</div>
        </Thead>
        {rows.map((r) => (
          <Trow key={`${r.src}-${r.sym}`} cols={SEL_COLS} pad="0 22px">
            <div style={{ fontWeight: 500, ...ellip }}>{r.sym}</div>
            <div><span style={badge(r.srcBg, r.srcFg)}>{r.src}</span></div>
            <div style={{ color: r.dirColor, fontFamily: MONO, fontSize: 11 }}>{r.dir}</div>
            <div style={{ textAlign: 'right', color: V.muted }}>{r.lots}</div>
            <div style={{ textAlign: 'right', fontFamily: MONO, color: V.muted }}>{r.base}</div>
            <div style={{ fontSize: 11, color: V.faint, ...ellip }}>{r.note}</div>
          </Trow>
        ))}
        {!rows.length ? (
          <Empty title="Nothing selected yet."
            why="The set is chosen at the settlement snapshot and frozen at the manual cutoff." />
        ) : null}
      </Scroller>
    </Card>
  )
}

function Automatic() {
  const cfg = useStore((s) => s.cfg)
  const refresh = useStore((s) => s.refresh)
  const [busy, setBusy] = useState(false)

  const enabled = getPath(cfg?.config, 'universe.enabled') === true
  const tg = Number(getPath(cfg?.config, 'universe.top_n_gainers') ?? 0)
  const tl = Number(getPath(cfg?.config, 'universe.top_n_losers') ?? 0)
  const buffer = Number(getPath(cfg?.config, 'universe.candidate_buffer') ?? 0)
  const basis = String(getPath(cfg?.config, 'universe.ranking_basis') ?? DASH)

  const patch = async (edits: Record<string, unknown>, what: string) => {
    if (busy) return
    setBusy(true)
    try {
      await api.patchConfig(buildPatch(edits))
      await refresh('config')
      toast('Saved', what)
    } catch (e) {
      toast('Rejected', e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const cards = [
    {
      title: 'TOP GAINERS', color: V.pos, hint: 'resolves CE on a positive first tick',
      value: tg, path: 'universe.top_n_gainers',
      note: tg === 0
        ? 'Gainers are not traded.'
        : `The ${tg} highest-ranked gainers are armed as tradeable.`,
    },
    {
      title: 'TOP LOSERS', color: V.neg, hint: 'resolves PE on a negative first tick',
      value: tl, path: 'universe.top_n_losers',
      note: tl === 0
        ? 'Losers are not traded. Only the gainer side is armed.'
        : `The ${tl} highest-ranked fallers are armed as tradeable.`,
    },
  ]

  return (
    <Card pad={false} style={{ overflow: 'hidden' }}>
      <div style={{
        display: 'flex', alignItems: 'flex-start', gap: 16,
        padding: '20px 24px', borderBottom: `1px solid ${V.border}`,
      }}>
        <div style={{ marginTop: 2 }}>
          <Toggle on={enabled} disabled={busy}
            onChange={(next) => void patch({ 'universe.enabled': next },
              next ? 'Automatic selection enabled.' : 'Automatic selection disabled.')} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={cardTitleLg}>Automatic — top gainers and losers</div>
          <div style={cardSub}>
            The engine ranks the universe at the settlement snapshot and arms the top names on each
            side. Ranking basis is <span style={{ fontFamily: MONO }}>{basis}</span>.
          </div>
        </div>
        <Pill bg={enabled ? V.posbg : V.chip} fg={enabled ? V.pos : V.muted}>
          {enabled ? `${tg + tl} armed` : 'Disabled'}
        </Pill>
      </div>

      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0,1fr))', gap: 18,
        padding: '20px 24px', borderBottom: `1px solid ${V.border}`, opacity: enabled ? 1 : 0.45,
      }}>
        {cards.map((c) => (
          <div key={c.title} style={{
            border: `1px solid ${V.border}`, borderRadius: 14, background: V.sunken, padding: '18px 19px',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
              <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '.07em', color: c.color }}>{c.title}</div>
              <div style={{ fontSize: 11, color: V.faint }}>{c.hint}</div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 15 }}>
              <Stepper value={c.value} min={0} max={25} disabled={!enabled || busy}
                onChange={(n) => void patch({ [c.path]: n }, `${c.path.split('.').pop()} set to ${n}.`)} />
              <div style={{ fontSize: 12, color: V.muted }}>instruments</div>
            </div>
            <div style={{ fontSize: 11, color: V.muted, marginTop: 14, lineHeight: 1.55 }}>{c.note}</div>
          </div>
        ))}
      </div>

      <div style={{ padding: '20px 24px', opacity: enabled ? 1 : 0.45 }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>Candidate buffer</div>
        <div style={{ fontSize: 12, color: V.muted, marginTop: 4, marginBottom: 14, lineHeight: 1.55 }}>
          Extra ranks either side of the cutoff. Those chains are subscribed but never traded, so a
          shuffle near the open cannot leave the engine without a live chain.
        </div>
        <Stepper value={buffer} min={0} max={25} disabled={!enabled || busy}
          onChange={(n) => void patch({ 'universe.candidate_buffer': n }, `candidate_buffer set to ${n}.`)} />
      </div>
    </Card>
  )
}

function Manual() {
  const universe = useStore((s) => s.universe)
  const ranking = useStore((s) => s.ranking)
  const cfg = useStore((s) => s.cfg)
  const status = useStore((s) => s.status)
  const refresh = useStore((s) => s.refresh)
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(false)

  const cutoff = String(getPath(cfg?.config, 'schedule.manual_cutoff') ?? '09:14:00')
  // The backend is the authority on the window; a 409 is the real answer. This
  // only decides whether the UI looks open, so an approximation is fine.
  const closed = !!status && !['BOOT', 'PHASE_1', 'FEED_LIVE', 'PREOPEN', 'SETTLEMENT', 'ARMING'].includes(status.phase)

  const list = useMemo(() => {
    const raw = (getPath(cfg?.config, 'universe.manual_instruments') as unknown[] | undefined) ?? []
    return raw.map((m) => typeof m === 'string'
      ? { symbol: m, lots: 1 }
      : { symbol: String((m as { symbol?: string }).symbol ?? ''), lots: Number((m as { lots?: number }).lots ?? 1) })
  }, [cfg])

  const suggestions = useMemo(() => {
    const q = query.trim().toUpperCase()
    if (!q) return []
    const pool = universe?.nifty50 ?? []
    const starts = pool.filter((s) => s.startsWith(q))
    const contains = pool.filter((s) => !s.startsWith(q) && s.includes(q))
    return [...starts, ...contains].slice(0, 7)
  }, [query, universe])

  const byRank = useMemo(() => new Map(ranking.map((r) => [r.symbol, r])), [ranking])

  const add = async (symbol: string) => {
    if (busy || !symbol) return
    setBusy(true)
    try {
      await api.manualAdd(symbol)
      await refresh('config')
      await refresh('universe')
      setQuery('')
      toast('Instrument added', `${symbol} will be armed for this session.`)
    } catch (e) {
      toast('Rejected', e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const remove = async (symbol: string) => {
    if (busy) return
    setBusy(true)
    try {
      await api.manualRemove(symbol)
      await refresh('config')
      await refresh('universe')
      toast('Instrument removed', `${symbol} removed from the manual set.`)
    } catch (e) {
      toast('Rejected', e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card pad={false} style={{ overflow: 'visible' }}>
      <div style={{ padding: '20px 22px', borderBottom: `1px solid ${V.border}` }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
          <div style={cardTitleLg}>Manual selection</div>
          <Pill bg={closed ? V.chip : V.posbg} fg={closed ? V.muted : V.pos}>
            {closed ? `Closed ${cutoff}` : `Open until ${cutoff}`}
          </Pill>
        </div>
        <div style={cardSub}>
          Names added here are armed alongside the automatic set. The window closes at {cutoff},
          after which the instrument set is locked for the session.
        </div>
      </div>

      <div style={{ padding: '18px 22px', borderBottom: `1px solid ${V.border}` }}>
        {closed ? (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 10, padding: '12px 14px',
            border: `1px solid ${V.border}`, borderRadius: 12, background: V.sunken,
            fontSize: 12, color: V.muted, lineHeight: 1.5, marginBottom: 14,
          }}>
            The manual entry window has closed for this session. The backend will refuse an add with a 409.
          </div>
        ) : null}
        <div style={{ position: 'relative' }}>
          <div style={{ display: 'flex', gap: 9 }}>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') void add(query.trim().toUpperCase()) }}
              placeholder={universe?.nifty50.length ? 'Search the universe, e.g. INDI…' : 'Universe not loaded yet'}
              style={{
                flex: 1, minWidth: 0, padding: '10px 13px', border: `1px solid ${V.border2}`,
                borderRadius: 10, background: V.sunken, fontSize: 12, outline: 'none',
              }} />
            <Button kind="primary" disabled={busy || !query.trim()}
              onClick={() => void add(query.trim().toUpperCase())}>Add</Button>
          </div>

          {suggestions.length ? (
            <div style={{
              position: 'absolute', top: 46, left: 0, right: 78, zIndex: 30,
              border: `1px solid ${V.border2}`, borderRadius: 12, background: V.card,
              boxShadow: '0 12px 32px rgba(0,0,0,.18)', overflow: 'hidden',
              maxHeight: 280, overflowY: 'auto',
            }}>
              {suggestions.map((s) => {
                const r = byRank.get(s)
                const already = list.some((m) => m.symbol === s)
                return (
                  <div key={s} onClick={() => { if (!already) void add(s); else setQuery('') }}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 12, padding: '9px 13px',
                      borderBottom: `1px solid ${V.border}`, cursor: 'pointer',
                      background: already ? V.sunken : V.card,
                    }}>
                    <div style={{ fontSize: 12, fontWeight: 600, fontFamily: MONO, flex: 1, minWidth: 0, ...ellip }}>{s}</div>
                    <div style={{ fontSize: 11, fontFamily: MONO, color: V.muted }}>
                      {r ? price(r.ltp, { zeroIsDash: true }) : DASH}
                    </div>
                    <div style={{
                      fontSize: 11, fontFamily: MONO, width: 60, textAlign: 'right',
                      color: r ? tone(r.change_pct) : V.faint,
                    }}>{r ? pct(r.change_pct) : DASH}</div>
                    <div style={{ fontSize: 10, color: V.faint, width: 56, textAlign: 'right' }}>
                      {already ? 'added' : r?.selected ? 'ranked' : ''}
                    </div>
                  </div>
                )
              })}
            </div>
          ) : null}
        </div>
      </div>

      <div style={{ padding: '16px 22px 20px', minHeight: 96 }}>
        {list.length ? (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {list.map((m) => (
              <div key={m.symbol} style={{
                display: 'flex', alignItems: 'center', gap: 9, padding: '7px 9px 7px 13px',
                border: `1px solid ${V.border2}`, borderRadius: 10, background: V.sunken,
              }}>
                <div>
                  <div style={{ fontSize: 12, fontWeight: 600, fontFamily: MONO }}>{m.symbol}</div>
                  <div style={{ fontSize: 10, color: V.faint, marginTop: 2 }}>
                    {m.lots} lot{m.lots === 1 ? '' : 's'} · direction on the first tick
                  </div>
                </div>
                <button onClick={() => void remove(m.symbol)} disabled={busy}
                  style={{
                    width: 20, height: 20, borderRadius: 6, border: 'none', background: V.chip,
                    color: V.muted, fontSize: 12, lineHeight: 1, display: 'grid', placeItems: 'center',
                  }}>×</button>
              </div>
            ))}
          </div>
        ) : (
          <div style={{ fontSize: 12, color: V.faint, lineHeight: 1.6, padding: '14px 0' }}>
            No manual instruments. The automatic set runs on its own.
          </div>
        )}
      </div>
    </Card>
  )
}

function Indexes() {
  const cfg = useStore((s) => s.cfg)
  const refresh = useStore((s) => s.refresh)
  const [busy, setBusy] = useState(false)

  const indices = (getPath(cfg?.config, 'universe.indices') as
    Record<string, { enabled?: boolean; lots?: number; strike_offset?: number }> | undefined) ?? {}
  const entries = Object.entries(indices)
  const on = entries.filter(([, v]) => v?.enabled).length

  const toggle = async (name: string, next: boolean) => {
    if (busy) return
    setBusy(true)
    try {
      await api.patchConfig(buildPatch({ [`universe.indices.${name}.enabled`]: next }))
      await refresh('config')
      toast(next ? 'Index enabled' : 'Index disabled', `${name} ${next ? 'will be armed' : 'will not be armed'}.`)
    } catch (e) {
      toast('Rejected', e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card pad={false} style={{ overflow: 'hidden' }}>
      <div style={{ padding: '20px 22px', borderBottom: `1px solid ${V.border}` }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
          <div style={cardTitleLg}>Indexes</div>
          <div style={{ fontSize: 11, color: V.faint }}>{on} of {entries.length} enabled</div>
        </div>
        <div style={cardSub}>
          Index options are armed directly rather than ranked. Each carries its own lot size and
          strike offset, and being cash-settled they never roll expiry.
        </div>
      </div>
      <div style={{
        padding: '16px 22px 22px', display: 'grid',
        gridTemplateColumns: 'repeat(2, minmax(0,1fr))', gap: 10,
      }}>
        {entries.map(([name, v]) => {
          const active = !!v?.enabled
          return (
            <div key={name} onClick={() => void toggle(name, !active)}
              style={{
                display: 'flex', alignItems: 'center', gap: 13, padding: '14px 15px',
                border: `1px solid ${active ? V.accent : V.border}`, borderRadius: 13,
                background: active ? V.sunken : V.card, cursor: 'pointer',
                transition: 'background .12s, border-color .12s',
              }}>
              <div style={{
                width: 18, height: 18, borderRadius: 6, flex: 'none',
                border: `1.5px solid ${active ? V.accent : V.border2}`,
                background: active ? V.accent : 'transparent',
                display: 'grid', placeItems: 'center',
              }}>
                <svg width="11" height="11" viewBox="0 0 12 12" fill="none"
                  stroke={active ? '#fff' : 'transparent'} strokeWidth={2.2}
                  strokeLinecap="round" strokeLinejoin="round">
                  <path d="M2.5 6.2l2.4 2.4L9.5 3.8" />
                </svg>
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 600, letterSpacing: '-.005em' }}>{name}</div>
                <div style={{ fontSize: 11, color: V.muted, marginTop: 2 }}>
                  {int(v?.lots ?? 1)} lot{(v?.lots ?? 1) === 1 ? '' : 's'} · strike offset {int(v?.strike_offset ?? 0)}
                </div>
              </div>
            </div>
          )
        })}
        {!entries.length ? (
          <div style={{ fontSize: 12, color: V.faint, padding: '14px 0' }}>
            No indices in configuration.
          </div>
        ) : null}
      </div>
    </Card>
  )
}

const BASE_COLS = 'minmax(130px,1fr) 92px 92px 92px 84px 96px minmax(120px,1fr)'

function Baselines() {
  const universe = useStore((s) => s.universe)
  const market = useStore((s) => s.market)
  const cfg = useStore((s) => s.cfg)
  const rows = universe?.armed ?? []
  const minDiff = Number(getPath(cfg?.config, 'entry.min_diff') ?? 0)

  return (
    <Card pad={false} style={{ overflow: 'hidden' }}>
      <div style={{ padding: '20px 22px', borderBottom: `1px solid ${V.border}` }}>
        <CardHead
          title="Baselines and drift"
          sub="The baseline is each contract's entry reference — its previous close, since options do not trade in the pre-open. Drift is how far the live price has moved since."
          right={<div style={{ fontSize: 11, color: V.faint, whiteSpace: 'nowrap' }}>
            min_diff {minDiff}
          </div>}
        />
      </div>
      <Scroller min={820} maxHeight={560}>
        <Thead cols={BASE_COLS} pad="10px 22px">
          <div>Instrument</div>
          <div style={{ textAlign: 'right' }}>Baseline</div>
          <div style={{ textAlign: 'right' }}>Current</div>
          <div style={{ textAlign: 'right' }}>Diff</div>
          <div style={{ textAlign: 'right' }}>Diff %</div>
          <div style={{ textAlign: 'right' }}>Threshold</div>
          <div>Resolved</div>
        </Thead>
        {rows.map((a) => {
          const live = market[String(a.token)]?.ltp ?? a.ltp
          const diff = live && a.ref_price ? live - a.ref_price : 0
          const pctMove = a.ref_price ? (diff / a.ref_price) * 100 : 0
          const cleared = diff > minDiff
          return (
            <Trow key={a.token} cols={BASE_COLS} pad="0 22px">
              <div style={{ fontWeight: 500, ...ellip }}>{a.symbol}</div>
              <div style={{ textAlign: 'right', fontFamily: MONO, color: V.muted }}>
                {price(a.ref_price, { zeroIsDash: true })}
              </div>
              <div style={{ textAlign: 'right', fontFamily: MONO }}>{price(live, { zeroIsDash: true })}</div>
              <div style={{ textAlign: 'right', fontFamily: MONO, color: tone(diff), fontWeight: 600 }}>
                {diff ? price(diff) : DASH}
              </div>
              <div style={{ textAlign: 'right', fontFamily: MONO, color: tone(diff) }}>
                {a.ref_price ? pct(pctMove) : DASH}
              </div>
              <div style={{ textAlign: 'right' }}>
                <span style={badge(cleared ? V.posbg : V.chip, cleared ? V.pos : V.muted)}>
                  {cleared ? 'CLEARED' : 'INSIDE'}
                </span>
              </div>
              <div style={{
                fontSize: 11, fontFamily: MONO, ...ellip,
                color: a.fired ? V.accent : V.muted,
              }}>
                {a.fired ? 'fired · locked' : cleared ? 'qualifying' : 'waiting for a tick'}
              </div>
            </Trow>
          )
        })}
        {!rows.length ? (
          <Empty title="Nothing armed yet."
            why="Baselines are captured at the option reference time, before trading starts." />
        ) : null}
      </Scroller>
    </Card>
  )
}
