import { useMemo, useState } from 'react'
import { api, type OrderRow, type Position } from '../lib/api'
import { DASH, int, money, pct, price, seconds, timeFromIso, timeFromUs } from '../lib/format'
import { MONO, V, ellip, statusTone } from '../lib/style'
import { useStore } from '../lib/store'
import { toast } from '../lib/toast'
import { Card, ChipRow, Dialog, Empty, Scroller, Segmented, Thead, Trow } from '../components/ui'

type Tab = 'open' | 'closed' | 'orders' | 'signals'
type OrdFilter = 'all' | 'entry' | 'exit' | 'rejected'

const COLS = 'minmax(200px,1.4fr) 56px 84px 84px 106px 80px 100px 124px 64px 116px 74px'

export default function Positions() {
  const positions = useStore((s) => s.positions)
  const closed = useStore((s) => s.closed)
  const orders = useStore((s) => s.orders)
  const signals = useStore((s) => s.signals)
  const [tab, setTab] = useState<Tab>('open')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <Segmented<Tab>
        value={tab}
        onChange={(t) => setTab(t)}
        options={[
          { key: 'open', label: 'Open', count: positions.length },
          { key: 'closed', label: 'Closed', count: closed.length },
          { key: 'orders', label: 'Orders', count: orders.length },
          { key: 'signals', label: 'Signals', count: signals.length },
        ]}
      />
      {tab === 'open' || tab === 'closed'
        ? <PositionTable rows={tab === 'closed' ? closed : positions} closed={tab === 'closed'} />
        : null}
      {tab === 'orders' ? <Orders rows={orders} /> : null}
      {tab === 'signals' ? <Signals /> : null}
    </div>
  )
}

/* ------------------------------------------------------------------ table */

function PositionTable({ rows, closed }: { rows: Position[]; closed: boolean }) {
  const phase = useStore((s) => s.status?.phase)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [ask, setAsk] = useState<Position | null>(null)
  const refresh = useStore((s) => s.refresh)

  const exit = async () => {
    const p = ask
    setAsk(null)
    if (!p) return
    try {
      await api.exitPosition(p.pos_id)
      await refresh('positions')
      toast('Exit sent', `Market exit sent for ${p.instrument.tradingsymbol}.`)
    } catch (e) {
      toast('Rejected', e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <Card pad={false} style={{ overflow: 'hidden' }}>
      <Scroller min={1240}>
        <Thead cols={COLS} pad="12px 20px">
          <div>Instrument</div>
          <div style={{ textAlign: 'right' }}>Qty</div>
          <div style={{ textAlign: 'right' }}>Entry</div>
          <div style={{ textAlign: 'right' }}>LTP</div>
          <div style={{ textAlign: 'right' }}>P&L ₹</div>
          <div style={{ textAlign: 'right' }}>P&L %</div>
          <div style={{ textAlign: 'right' }}>Max / Min</div>
          <div style={{ textAlign: 'right' }}>Trailing SL</div>
          <div style={{ textAlign: 'right' }}>Held</div>
          <div>Status</div>
          <div />
        </Thead>

        {rows.map((p) => {
          const isOpen = expanded === p.pos_id
          const failed = p.status === 'FAILED'
          const disabled = closed || p.flags.exiting || p.status === 'CLOSED' || failed
          const pnlColor = failed ? V.muted : p.live.pnl > 0 ? V.pos : p.live.pnl < 0 ? V.neg : V.muted
          const i = p.instrument

          return (
            <div key={p.pos_id}>
              <Trow cols={COLS} minHeight={46} pad="0 20px"
                background={isOpen ? V.sunken : 'transparent'}
                onClick={() => setExpanded(isOpen ? null : p.pos_id)}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
                  <span style={{ color: V.faint, fontSize: 9, flex: 'none', width: 8 }}>{isOpen ? '▾' : '▸'}</span>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontFamily: MONO, fontWeight: 500, ...ellip }}>{i.tradingsymbol}</div>
                    <div style={{ fontSize: 11, color: V.faint, whiteSpace: 'nowrap' }}>
                      {i.underlying} · {i.option_type ?? DASH} {i.strike || ''} · lot {int(i.lot_size)}
                    </div>
                  </div>
                </div>
                <div style={{ textAlign: 'right' }}>{int(p.quantity)}</div>
                <div style={{ textAlign: 'right' }}>{price(p.entry.price, { zeroIsDash: true })}</div>
                <div style={{ textAlign: 'right' }}>{price(p.live.ltp, { zeroIsDash: true })}</div>
                <div style={{ textAlign: 'right', color: pnlColor, fontWeight: 600 }}>
                  {failed ? DASH : money(p.live.pnl, { sign: true })}
                </div>
                <div style={{ textAlign: 'right', color: pnlColor }}>
                  {failed ? DASH : pct(p.live.pnl_pct)}
                </div>
                <div style={{ textAlign: 'right', fontSize: 11, color: V.faint }}>
                  {p.live.max_pnl_pct || p.live.min_pnl_pct
                    ? `${pct(p.live.max_pnl_pct)} / ${pct(p.live.min_pnl_pct)}`
                    : DASH}
                </div>
                <div style={{ textAlign: 'right', fontSize: 11 }}>
                  {p.trailing.sl_active ? price(p.trailing.sl_level) : DASH}
                  {p.trailing.sl_active && p.live.ltp ? (
                    <span style={{ color: V.faint }}> ({price(p.live.ltp - p.trailing.sl_level)})</span>
                  ) : null}
                </div>
                <div style={{ textAlign: 'right', color: V.muted }}>
                  {p.live.holding_seconds ? seconds(p.live.holding_seconds) : DASH}
                </div>
                <div><span style={statusTone(p.status)}>{p.status}</span></div>
                <div style={{ textAlign: 'right' }}>
                  <button
                    disabled={disabled}
                    onClick={(e) => {
                      e.stopPropagation()
                      if (p.flags.exiting) {
                        toast('Already exiting', `An exit order is in flight for ${p.pos_id}.`)
                        return
                      }
                      setAsk(p)
                    }}
                    style={{
                      padding: '5px 11px', borderRadius: 8, fontSize: 11, fontWeight: 500,
                      border: `1px solid ${disabled ? V.border : V.border2}`,
                      background: V.card, color: disabled ? V.faint : V.neg,
                      opacity: disabled ? 0.5 : 1,
                    }}>
                    Exit
                  </button>
                </div>
              </Trow>

              {isOpen ? <Expanded p={p} /> : null}
            </div>
          )
        })}
      </Scroller>

      {!rows.length ? (
        <Empty
          title={closed ? 'No closed positions.' : 'No open positions.'}
          why={phase === 'TRADING'
            ? 'Entries are armed and waiting for the first qualifying tick.'
            : `Nothing has been opened in this session. Current phase is ${phase ?? 'unknown'}.`}
        />
      ) : null}

      <Dialog
        open={!!ask}
        title={ask ? `Exit ${ask.instrument.tradingsymbol}?` : ''}
        body={ask
          ? `Sends a market exit for ${int(ask.quantity)} (${int(ask.lots)} lot${ask.lots === 1 ? '' : 's'}). Unrealised P&L is ${money(ask.live.pnl, { sign: true })}. This cannot be undone.`
          : ''}
        confirmLabel="Exit position"
        danger
        onCancel={() => setAsk(null)}
        onConfirm={() => void exit()}
      />
    </Card>
  )
}

function Expanded({ p }: { p: Position }) {
  const orders = useStore((s) => s.orders)
  const signals = useStore((s) => s.signals)
  const linked = orders.filter((o) => o.pos_id === p.pos_id)
  const sig = signals.find((s) => s.sig_id === p.sig_id)

  const blocks: { title: string; rows: { k: string; v: string; color?: string }[] }[] = [
    {
      title: 'Entry',
      rows: [
        { k: 'order_id', v: p.entry.order_id ?? DASH },
        { k: 'price', v: price(p.entry.price, { zeroIsDash: true }) },
        { k: 'filled', v: `${int(p.entry.filled_qty)} / ${int(p.quantity)}` },
        { k: 'at', v: timeFromUs(p.entry.at_us) },
        { k: 'ref_price', v: price(p.entry.ref_price, { zeroIsDash: true }), color: V.muted },
        { k: 'diff', v: p.entry.diff ? price(p.entry.diff) : DASH, color: p.entry.diff > 0 ? V.pos : V.muted },
      ],
    },
    {
      title: 'Exit',
      rows: [
        { k: 'order_id', v: p.exit.order_id ?? DASH },
        { k: 'price', v: price(p.exit.price, { zeroIsDash: true }) },
        { k: 'filled', v: p.exit.filled_qty ? `${int(p.exit.filled_qty)} / ${int(p.quantity)}` : DASH },
        { k: 'at', v: timeFromUs(p.exit.at_us) },
        { k: 'trigger', v: p.exit.trigger ?? DASH, color: p.exit.trigger ? V.warn : V.muted },
        { k: 'charges', v: p.charges ? money(p.charges) : DASH, color: V.muted },
      ],
    },
    {
      title: 'Trailing',
      rows: [
        { k: 'sl_active', v: String(p.trailing.sl_active), color: p.trailing.sl_active ? V.pos : V.muted },
        { k: 'sl_peak', v: price(p.trailing.sl_peak, { zeroIsDash: true }) },
        { k: 'sl_level', v: price(p.trailing.sl_level, { zeroIsDash: true }) },
        { k: 'tgt_active', v: String(p.trailing.tgt_active), color: p.trailing.tgt_active ? V.pos : V.muted },
        { k: 'tgt_peak', v: price(p.trailing.tgt_peak, { zeroIsDash: true }) },
        { k: 'tgt_level', v: price(p.trailing.tgt_level, { zeroIsDash: true }) },
      ],
    },
    {
      title: 'Live & flags',
      rows: [
        { k: 'ltp', v: price(p.live.ltp, { zeroIsDash: true }) },
        // 0 on both sides means an empty book, not a free option.
        { k: 'bid / ask', v: `${price(p.live.bid, { zeroIsDash: true })} / ${price(p.live.ask, { zeroIsDash: true })}` },
        { k: 'max_pnl_pct', v: pct(p.live.max_pnl_pct), color: V.pos },
        { k: 'min_pnl_pct', v: pct(p.live.min_pnl_pct), color: p.live.min_pnl_pct < 0 ? V.neg : V.muted },
        { k: 'exiting', v: String(p.flags.exiting), color: p.flags.exiting ? V.warn : V.muted },
        { k: 'reconciled', v: String(p.flags.reconciled), color: p.flags.reconciled ? V.pos : V.warn },
      ],
    },
  ]

  return (
    <div style={{
      padding: '18px 20px 20px 38px', borderBottom: `1px solid ${V.border}`, background: V.sunken,
    }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0,1fr))', gap: 16 }}>
        {blocks.map((b) => (
          <div key={b.title} style={{
            border: `1px solid ${V.border}`, borderRadius: 14, background: V.card, padding: '15px 16px',
          }}>
            <div style={{
              fontSize: 10, letterSpacing: '.08em', textTransform: 'uppercase',
              color: V.muted, marginBottom: 11,
            }}>{b.title}</div>
            {b.rows.map((r) => (
              <div key={r.k} style={{
                display: 'flex', justifyContent: 'space-between', gap: 10, padding: '3px 0', fontSize: 12,
              }}>
                <span style={{ color: V.muted }}>{r.k}</span>
                <span style={{ fontFamily: MONO, color: r.color ?? V.text, ...ellip }}>{r.v}</span>
              </div>
            ))}
          </div>
        ))}
      </div>

      <div style={{
        display: 'grid', gridTemplateColumns: 'minmax(0,1.6fr) minmax(0,1fr)', gap: 16, marginTop: 16,
      }}>
        <div style={{ border: `1px solid ${V.border}`, borderRadius: 14, background: V.card, padding: '15px 16px' }}>
          <div style={{
            fontSize: 10, letterSpacing: '.08em', textTransform: 'uppercase',
            color: V.muted, marginBottom: 10,
          }}>Linked orders</div>
          {linked.length ? linked.map((o, n) => (
            <div key={`${o.order_id ?? 'na'}-${n}`} style={{
              display: 'grid', gridTemplateColumns: '74px 58px 44px 72px 40px 96px minmax(0,1fr)',
              gap: 10, padding: '6px 0', fontSize: 11, fontFamily: MONO,
              borderTop: `1px solid ${V.border}`,
            }}>
              <div style={{ color: V.faint }}>{timeFromIso(o.at)}</div>
              <div>{o.role}</div>
              <div>{o.side}</div>
              <div style={{ textAlign: 'right' }}>{price(o.price, { zeroIsDash: true })}</div>
              <div style={{ textAlign: 'right' }}>#{o.attempt}</div>
              <div style={{ color: o.status === 'REJECTED' ? V.neg : o.status === 'COMPLETE' ? V.pos : V.warn }}>
                {o.status ?? DASH}
              </div>
              <div style={{ color: V.muted, ...ellip }}>{o.rejection ?? o.message ?? DASH}</div>
            </div>
          )) : (
            <div style={{ fontSize: 12, color: V.faint, padding: '6px 0' }}>No order rows for this position.</div>
          )}
        </div>

        <div style={{ border: `1px solid ${V.border}`, borderRadius: 14, background: V.card, padding: '15px 16px' }}>
          <div style={{
            fontSize: 10, letterSpacing: '.08em', textTransform: 'uppercase',
            color: V.muted, marginBottom: 10,
          }}>Originating signal</div>
          {sig ? (
            [
              { k: 'sig_id', v: sig.sig_id },
              { k: 'ref', v: price(sig.ref) },
              { k: 'price', v: price(sig.price) },
              { k: 'ask', v: price(sig.ask, { zeroIsDash: true }) },
              { k: 'diff', v: price(sig.diff), color: V.pos },
              { k: 'at', v: timeFromIso(sig.at) },
            ].map((r) => (
              <div key={r.k} style={{
                display: 'flex', justifyContent: 'space-between', gap: 10, padding: '3px 0', fontSize: 12,
              }}>
                <span style={{ color: V.muted }}>{r.k}</span>
                <span style={{ fontFamily: MONO, color: r.color ?? V.text }}>{r.v}</span>
              </div>
            ))
          ) : (
            <div style={{ fontSize: 12, color: V.muted, padding: '3px 0' }}>
              {p.sig_id
                ? `Signal ${p.sig_id} is no longer in the retained window.`
                : 'Not a strategy entry — adopted from the broker book.'}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ orders */

function Orders({ rows }: { rows: OrderRow[] }) {
  const [filter, setFilter] = useState<OrdFilter>('all')

  const groups = useMemo(() => {
    let list = rows
    if (filter === 'entry') list = list.filter((o) => o.role === 'ENTRY')
    if (filter === 'exit') list = list.filter((o) => o.role === 'EXIT')
    if (filter === 'rejected') list = list.filter((o) => o.status === 'REJECTED')

    const out: { posId: string; sym: string; rows: OrderRow[] }[] = []
    for (const o of list) {
      const key = o.pos_id ?? 'unlinked'
      let g = out.find((x) => x.posId === key)
      if (!g) { g = { posId: key, sym: o.sym, rows: [] }; out.push(g) }
      g.rows.push(o)
    }
    return out
  }, [rows, filter])

  const cols = '92px 70px 50px 58px 82px 52px 108px minmax(0,1fr)'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <ChipRow<OrdFilter>
        value={filter}
        onChange={setFilter}
        options={[
          { key: 'all', label: 'All' },
          { key: 'entry', label: 'Entry' },
          { key: 'exit', label: 'Exit' },
          { key: 'rejected', label: 'Rejected only' },
        ]}
      />

      {groups.map((g) => {
        const rejected = g.rows.filter((r) => r.status === 'REJECTED').length
        return (
          <Card key={g.posId} pad={false} style={{ overflow: 'hidden' }}>
            <div style={{
              display: 'flex', alignItems: 'center', gap: 12, padding: '13px 20px',
              borderBottom: `1px solid ${V.border}`, background: V.sunken,
            }}>
              <div style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600 }}>{g.posId}</div>
              <div style={{ fontSize: 12, color: V.muted, ...ellip }}>{g.sym}</div>
              <div style={{ flex: 1 }} />
              <div style={{ fontSize: 11, color: V.faint, whiteSpace: 'nowrap' }}>
                {g.rows.length} attempt{g.rows.length === 1 ? '' : 's'}
                {rejected ? ` · ${rejected} rejected` : ''}
              </div>
            </div>
            <Scroller min={880}>
              {g.rows.map((o, n) => (
                <Trow key={`${o.order_id ?? 'na'}-${n}`} cols={cols} minHeight={40}
                  background={o.status === 'REJECTED' ? V.negbg : 'transparent'}>
                  <div style={{ fontFamily: MONO, fontSize: 11, color: V.faint }}>{timeFromIso(o.at)}</div>
                  <div>{o.role}</div>
                  <div>{o.side}</div>
                  <div style={{ textAlign: 'right' }}>{int(o.qty)}</div>
                  <div style={{ textAlign: 'right' }}>{price(o.price, { zeroIsDash: true })}</div>
                  <div style={{ textAlign: 'right', color: o.attempt > 1 ? V.warn : V.muted }}>#{o.attempt}</div>
                  <div><span style={statusTone(o.status)}>{o.status ?? 'PENDING'}</span></div>
                  <div style={{
                    fontSize: 11, color: o.status === 'REJECTED' ? V.neg : V.muted, ...ellip,
                  }}>{o.rejection ?? o.message ?? DASH}</div>
                </Trow>
              ))}
            </Scroller>
          </Card>
        )
      })}

      {!groups.length ? (
        <Card pad={false}>
          <Empty title="No orders match this filter."
            why={rows.length ? 'Clear the filter to see every attempt.' : 'No attempts have been made this session.'} />
        </Card>
      ) : null}
    </div>
  )
}

/* ------------------------------------------------------------------ signals */

function Signals() {
  const signals = useStore((s) => s.signals)
  const positions = useStore((s) => s.positions)
  const closed = useStore((s) => s.closed)
  const all = [...positions, ...closed]
  const cols = '92px minmax(180px,1.4fr) 92px 92px 92px minmax(0,1fr)'

  return (
    <Card pad={false} style={{ overflow: 'hidden' }}>
      <Thead cols={cols} pad="12px 20px">
        <div>Time</div>
        <div>Signal</div>
        <div style={{ textAlign: 'right' }}>Ref</div>
        <div style={{ textAlign: 'right' }}>Price</div>
        <div style={{ textAlign: 'right' }}>Diff</div>
        <div>Outcome</div>
      </Thead>
      {signals.map((s) => {
        const pos = all.find((p) => p.sig_id === s.sig_id)
        return (
          <Trow key={s.sig_id} cols={cols} minHeight={46}>
            <div style={{ fontFamily: MONO, fontSize: 11, color: V.faint }}>{timeFromIso(s.at)}</div>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontFamily: MONO, ...ellip }}>{s.sym}</div>
              <div style={{ fontSize: 11, color: V.faint, fontFamily: MONO }}>{s.sig_id}</div>
            </div>
            <div style={{ textAlign: 'right', color: V.muted }}>{price(s.ref)}</div>
            <div style={{ textAlign: 'right' }}>{price(s.price)}</div>
            <div style={{ textAlign: 'right', color: s.diff > 0 ? V.pos : V.neg, fontWeight: 600 }}>
              {price(s.diff)}
            </div>
            <div style={{ fontSize: 11, color: pos ? V.muted : V.warn, ...ellip }}>
              {pos ? `${pos.status} · ${pos.pos_id}` : 'No position — refused or unfilled'}
            </div>
          </Trow>
        )
      })}
      {!signals.length ? (
        <Empty title="No signals yet." why="A signal is recorded the moment a diff clears the minimum." />
      ) : null}
    </Card>
  )
}
