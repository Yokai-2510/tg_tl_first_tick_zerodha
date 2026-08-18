import { useMemo, useState } from 'react'
import type { RankRow } from '../lib/api'
import { DASH, int, pct, price } from '../lib/format'
import { MONO, V, badge, ellip, pctWidth, tone } from '../lib/style'
import { useStore } from '../lib/store'
import { Card, CardHead, ChipRow, Empty, Scroller, Segmented, StatHead, Thead, Trow } from '../components/ui'

type Tab = 'summary' | 'movers'
type Side = 'all' | 'gainers' | 'losers'
type SortKey = 'change' | 'ltp' | 'volume' | 'rank'

export default function LiveData() {
  const [tab, setTab] = useState<Tab>('summary')
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <Segmented<Tab> value={tab} onChange={setTab} options={[
        { key: 'summary', label: 'Summary' },
        { key: 'movers', label: 'Gainers & losers' },
      ]} />
      {tab === 'summary' ? <Summary /> : <Movers />}
    </div>
  )
}

/* ------------------------------------------------------------------ summary */

function Summary() {
  const universe = useStore((s) => s.universe)
  const ranking = useStore((s) => s.ranking)
  const status = useStore((s) => s.status)

  const modes = status?.feed.modes ?? {}
  const modeSplit = Object.entries(modes)
    .map(([k, v]) => `${int(v)} ${k}`)
    .join(' · ')

  const stats = [
    {
      label: 'Subscribed',
      value: int(universe?.subscribed ?? status?.feed.subscribed ?? 0),
      sub: modeSplit || 'no mode breakdown',
    },
    {
      label: 'Tradeable',
      value: int(universe?.tradeable.length ?? 0),
      sub: 'may fire entries',
    },
    {
      label: 'Buffer',
      value: int(universe?.buffer.length ?? 0),
      sub: 'subscribed, never traded',
    },
    {
      label: 'Armed',
      value: int(universe?.armed.length ?? 0),
      sub: universe?.armed.length
        ? `${int(universe.armed.filter((a) => a.fired).length)} fired`
        : 'nothing armed yet',
    },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0,1fr))', gap: 18 }}>
        {stats.map((k) => (
          <Card key={k.label} pad="20px 22px">
            <StatHead title={k.label} />
            <div style={{ fontSize: 28, fontWeight: 600, letterSpacing: '-.032em', marginTop: 13 }}>{k.value}</div>
            <div style={{ fontSize: 12, color: V.muted, marginTop: 7, ...ellip }}>{k.sub}</div>
          </Card>
        ))}
      </div>

      <div style={{
        display: 'grid', gridTemplateColumns: 'minmax(0,1fr) minmax(0,1.15fr)',
        gap: 18, alignItems: 'start',
      }}>
        <Breadth rows={ranking} />
        <Extremes rows={ranking} />
      </div>
    </div>
  )
}

const BUCKETS: [string, (r: RankRow) => boolean, string][] = [
  ['above +2%', (r) => r.change_pct > 2, V.pos],
  ['+1 to +2%', (r) => r.change_pct > 1 && r.change_pct <= 2, V.pos],
  ['0 to +1%', (r) => r.change_pct > 0 && r.change_pct <= 1, V.pos],
  ['−1 to 0%', (r) => r.change_pct <= 0 && r.change_pct > -1, V.neg],
  ['−2 to −1%', (r) => r.change_pct <= -1 && r.change_pct > -2, V.neg],
  ['below −2%', (r) => r.change_pct <= -2, V.neg],
]

function Breadth({ rows }: { rows: RankRow[] }) {
  const counts = BUCKETS.map(([, test]) => rows.filter(test).length)
  const top = Math.max(...counts, 1)
  const up = rows.filter((r) => r.change_pct > 0).length

  return (
    <Card>
      <CardHead
        title="Breadth"
        sub="Ranked constituents by move against the previous close"
        right={<div style={{ fontSize: 12, fontFamily: MONO, color: V.muted, whiteSpace: 'nowrap' }}>
          {rows.length ? `${up} up · ${rows.length - up} down` : DASH}
        </div>}
      />
      <div style={{ marginTop: 18 }}>
        {BUCKETS.map(([label, , color], i) => (
          <div key={label} style={{
            display: 'grid', gridTemplateColumns: '78px minmax(0,1fr) 34px',
            gap: 12, alignItems: 'center', padding: '6px 0',
          }}>
            <div style={{ fontSize: 11, fontFamily: MONO, color: V.muted, textAlign: 'right' }}>{label}</div>
            <div style={{ height: 9, borderRadius: 5, background: V.chip, overflow: 'hidden' }}>
              <div style={{
                height: '100%', width: pctWidth(counts[i], top),
                background: counts[i] ? color : V.border2, borderRadius: 5,
              }} />
            </div>
            <div style={{
              fontSize: 12, fontFamily: MONO, textAlign: 'right',
              color: counts[i] ? V.text : V.faint,
            }}>{counts[i]}</div>
          </div>
        ))}
      </div>
      {!rows.length ? (
        <div style={{ fontSize: 12, color: V.faint, marginTop: 10, lineHeight: 1.6 }}>
          The ranking is computed at the settlement snapshot. Nothing to plot before then.
        </div>
      ) : null}
    </Card>
  )
}

function Extremes({ rows }: { rows: RankRow[] }) {
  const sorted = [...rows].sort((a, b) => b.change_pct - a.change_pct)
  const groups = [
    { title: 'TOP GAINERS', color: V.pos, rows: sorted.slice(0, 5) },
    { title: 'TOP LOSERS', color: V.neg, rows: sorted.slice(-5).reverse() },
  ]

  return (
    <Card>
      <CardHead title="Extremes" sub="The five names at each end of the ranking" />
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0,1fr))',
        gap: 22, marginTop: 16,
      }}>
        {groups.map((g) => (
          <div key={g.title} style={{ minWidth: 0 }}>
            <div style={{
              fontSize: 11, fontWeight: 700, letterSpacing: '.07em',
              color: g.color, marginBottom: 9,
            }}>{g.title}</div>
            {g.rows.length ? g.rows.map((r) => (
              <div key={r.symbol} style={{
                display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0',
                borderTop: `1px solid ${V.border}`, fontSize: 12, minWidth: 0,
              }}>
                <span style={{ fontWeight: 500, flex: 1, ...ellip }}>{r.symbol}</span>
                <span style={{ fontFamily: MONO, color: V.muted, whiteSpace: 'nowrap' }}>{price(r.ltp)}</span>
                <span style={{
                  fontFamily: MONO, color: tone(r.change_pct), whiteSpace: 'nowrap',
                  width: 62, textAlign: 'right',
                }}>{pct(r.change_pct)}</span>
              </div>
            )) : (
              <div style={{ fontSize: 12, color: V.faint, padding: '6px 0' }}>Not ranked yet.</div>
            )}
          </div>
        ))}
      </div>
    </Card>
  )
}

/* ------------------------------------------------------------------ movers */

const SORTS: { key: SortKey; label: string; get: (r: RankRow) => number }[] = [
  { key: 'rank', label: 'Rank', get: (r) => -r.rank },
  { key: 'change', label: 'Change %', get: (r) => r.change_pct },
  { key: 'ltp', label: 'LTP', get: (r) => r.ltp },
  { key: 'volume', label: 'Volume', get: (r) => r.volume ?? 0 },
]

const COLS = '44px minmax(120px,1fr) 88px 88px 84px 104px 128px 84px'

function Movers() {
  const ranking = useStore((s) => s.ranking)
  const universe = useStore((s) => s.universe)
  const [side, setSide] = useState<Side>('all')
  const [sort, setSort] = useState<SortKey>('rank')
  const [dir, setDir] = useState<'desc' | 'asc'>('desc')

  const tradeable = useMemo(() => new Set(universe?.tradeable ?? []), [universe])
  const buffer = useMemo(() => new Set(universe?.buffer ?? []), [universe])

  const rows = useMemo(() => {
    let list = ranking
    if (side === 'gainers') list = list.filter((r) => r.change_pct > 0)
    if (side === 'losers') list = list.filter((r) => r.change_pct < 0)
    const get = (SORTS.find((s) => s.key === sort) ?? SORTS[0]).get
    return [...list].sort((a, b) => (dir === 'desc' ? get(b) - get(a) : get(a) - get(b)))
  }, [ranking, side, sort, dir])

  const active = SORTS.find((s) => s.key === sort) ?? SORTS[0]

  return (
    <Card pad={false} style={{ overflow: 'hidden' }}>
      <div style={{
        padding: '18px 20px', borderBottom: `1px solid ${V.border}`,
        display: 'flex', flexDirection: 'column', gap: 14,
      }}>
        <CardHead
          title="Gainers and losers"
          sub={`Sorted by ${active.label.toLowerCase()}, ${dir === 'desc' ? 'high to low' : 'low to high'}. Tradeable names may fire entries; buffer names are subscribed only.`}
          right={<div style={{ fontSize: 11, color: V.faint, whiteSpace: 'nowrap' }}>
            {rows.length} of {ranking.length} ranked
          </div>}
        />
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
          <Segmented<Side> value={side} onChange={setSide} style={{ padding: 3 }} options={[
            { key: 'all', label: 'All' },
            { key: 'gainers', label: 'Gainers' },
            { key: 'losers', label: 'Losers' },
          ]} />
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 11, color: V.faint }}>Sort</span>
            <ChipRow<SortKey>
              value={sort}
              onChange={(k) => {
                if (k === sort) setDir(dir === 'desc' ? 'asc' : 'desc')
                else { setSort(k); setDir('desc') }
              }}
              options={SORTS.map((s) => ({
                key: s.key,
                label: s.label,
                count: s.key === sort ? (dir === 'desc' ? '↓' : '↑') : undefined,
              }))}
            />
          </div>
        </div>
      </div>

      <Scroller min={900} maxHeight={600}>
        <Thead cols={COLS}>
          <div>#</div>
          <div>Symbol</div>
          <div style={{ textAlign: 'right' }}>Prev close</div>
          <div style={{ textAlign: 'right' }}>LTP</div>
          <div style={{ textAlign: 'right' }}>Change</div>
          <div style={{ textAlign: 'right' }}>Volume</div>
          <div style={{ textAlign: 'right' }}>Day high / low</div>
          <div style={{ textAlign: 'right' }}>Set</div>
        </Thead>
        {rows.map((r) => {
          const isTradeable = tradeable.has(r.symbol)
          const isBuffer = r.buffer ?? buffer.has(r.symbol)
          const selected = r.selected || isTradeable || isBuffer
          return (
            <Trow key={r.symbol} cols={COLS} minHeight={38}
              background={selected ? V.sunken : 'transparent'}>
              <div style={{ color: V.faint, fontFamily: MONO }}>{r.rank}</div>
              <div style={{ fontWeight: selected ? 600 : 400, ...ellip }}>{r.symbol}</div>
              <div style={{ textAlign: 'right', color: V.muted }}>{price(r.prev_close)}</div>
              <div style={{ textAlign: 'right' }}>{price(r.ltp, { zeroIsDash: true })}</div>
              <div style={{ textAlign: 'right', color: tone(r.change_pct), fontWeight: 500 }}>
                {pct(r.change_pct)}
              </div>
              <div style={{ textAlign: 'right', color: V.muted, fontFamily: MONO, fontSize: 11 }}>
                {r.volume === undefined ? DASH : int(r.volume)}
              </div>
              <div style={{ textAlign: 'right', color: V.muted, fontFamily: MONO, fontSize: 11 }}>
                {r.high || r.low ? `${price(r.high)} / ${price(r.low)}` : DASH}
              </div>
              <div style={{ textAlign: 'right' }}>
                {isTradeable ? <span style={badge(V.posbg, V.pos)}>TRADE</span>
                  : isBuffer ? <span style={badge(V.chip, V.muted)}>BUFFER</span>
                    : <span style={{ color: V.faint, fontSize: 10 }}>{DASH}</span>}
              </div>
            </Trow>
          )
        })}
        {!rows.length ? (
          <Empty title="Nothing ranked yet."
            why="The ranking is computed from the pre-open snapshot at the settlement time." />
        ) : null}
      </Scroller>
    </Card>
  )
}
