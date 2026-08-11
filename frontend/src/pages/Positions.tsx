import { useState } from 'react'
import { api, type Position } from '../lib/api'
import { useStore } from '../lib/store'
import {
  int, money, pct, price, seconds, signClass, timeFromIso, timeFromUs, DASH,
} from '../lib/format'
import { Confirm, KV, Pill, Section, Table, Tabs } from '../components/ui'

type Tab = 'open' | 'closed' | 'orders' | 'signals'

export default function Positions() {
  const positions = useStore((s) => s.positions)
  const closed = useStore((s) => s.closed)
  const orders = useStore((s) => s.orders)
  const signals = useStore((s) => s.signals)
  const status = useStore((s) => s.status)
  const refresh = useStore((s) => s.refresh)
  const setError = useStore((s) => s.setError)
  const [tab, setTab] = useState<Tab>('open')
  const [exiting, setExiting] = useState<Position | null>(null)
  const [exitAll, setExitAll] = useState(false)

  const doExit = async () => {
    if (!exiting) return
    try { await api.exitPosition(exiting.pos_id); await refresh('positions') }
    catch (e: any) { setError(e?.message ?? 'Exit failed') }
    finally { setExiting(null) }
  }
  const doExitAll = async () => {
    try { const r = await api.exitAll(); await refresh('positions'); setError(null)
          if (!r.exiting) setError('No open positions to exit.') }
    catch (e: any) { setError(e?.message ?? 'Exit-all failed') }
    finally { setExitAll(false) }
  }

  return (
    <div className="space-y-4">
      <Section title="Positions" right={
        <button className="btn btn-danger" disabled={!positions.length}
                onClick={() => setExitAll(true)}>Exit all</button>
      }>
        <Tabs value={tab} onChange={setTab} tabs={[
          { id: 'open', label: 'Open', count: positions.length },
          { id: 'closed', label: 'Closed', count: closed.length },
          { id: 'orders', label: 'Orders', count: orders.length },
          { id: 'signals', label: 'Signals', count: signals.length },
        ]} />
      </Section>

      {tab === 'open' && (
        <OpenTable rows={positions} phase={status?.phase ?? ''}
                   tradingStart={status?.schedule.trading_start ?? '09:15:00'}
                   onExit={setExiting} />
      )}
      {tab === 'closed' && <ClosedTable rows={closed} />}
      {tab === 'orders' && <OrdersTable />}
      {tab === 'signals' && <SignalsTable />}

      <Confirm open={!!exiting} title="Exit this position?" danger
        body={exiting ? <>
          A market-crossing sell will be placed for{' '}
          <b>{exiting.instrument.tradingsymbol}</b> ({int(exiting.quantity)} qty).
          Current P&L {money(exiting.live.pnl)}.
        </> : null}
        onCancel={() => setExiting(null)} onConfirm={doExit} />

      <Confirm open={exitAll} title="Exit all open positions?" danger
        body={`${positions.length} position(s) will be closed at market. This cannot be undone.`}
        onCancel={() => setExitAll(false)} onConfirm={doExitAll} />
    </div>
  )
}

// ---------------------------------------------------------------- open

function OpenTable(
  { rows, phase, tradingStart, onExit }:
  { rows: Position[]; phase: string; tradingStart: string; onExit: (p: Position) => void },
) {
  const [open, setOpen] = useState<string | null>(null)
  const empty = ['BOOT', 'IDLE'].includes(phase)
    ? `No positions. Entries open at ${tradingStart}.`
    : phase === 'PHASE_1_FAIL'
      ? 'No positions — pre-market checks failed today.'
      : 'No open positions.'

  return (
    <Table colSpan={12} empty={empty} head={<>
      <th className="th">Symbol</th>
      <th className="th">Under</th>
      <th className="th">Type</th>
      <th className="th num">Strike</th>
      <th className="th num">Qty</th>
      <th className="th num">Entry</th>
      <th className="th num">LTP</th>
      <th className="th num">P&L</th>
      <th className="th num">P&L %</th>
      <th className="th num">TSL</th>
      <th className="th">Status</th>
      <th className="th" />
    </>}>
      {rows.flatMap((x) => {
        const expanded = open === x.pos_id
        const tsl = x.trailing.sl_active
          ? <span title={`peak ${price(x.trailing.sl_peak)}`}>{price(x.trailing.sl_level)}</span>
          : <span className="text-muted">{DASH}</span>
        return [
          <tr key={x.pos_id} className="hover:bg-surface/60 cursor-pointer"
              onClick={() => setOpen(expanded ? null : x.pos_id)}>
            <td className="td font-medium">{x.instrument.tradingsymbol}</td>
            <td className="td text-muted">{x.instrument.underlying}</td>
            <td className="td">{x.instrument.option_type ?? DASH}</td>
            <td className="td num">{price(x.instrument.strike)}</td>
            <td className="td num">{int(x.quantity)}</td>
            <td className="td num">{price(x.entry.price)}</td>
            <td className="td num">{price(x.live.ltp)}</td>
            <td className={`td num font-medium ${signClass(x.live.pnl)}`}>{money(x.live.pnl)}</td>
            <td className={`td num ${signClass(x.live.pnl_pct)}`}>
              {pct(x.live.pnl_pct)}
              <span className="text-muted ml-1 text-[11px]">
                {pct(x.live.max_pnl_pct, { sign: false })}/{pct(x.live.min_pnl_pct, { sign: false })}
              </span>
            </td>
            <td className="td num">{tsl}</td>
            <td className="td">
              {x.status === 'ADOPTED_UNMANAGED'
                ? <Pill tone="text-warn border-warn/40">Adopted</Pill>
                : <span className={x.flags.exiting ? 'text-warn' : ''}>{x.status}</span>}
            </td>
            <td className="td text-right">
              <button className="btn btn-danger h-6 px-2" disabled={x.flags.exiting}
                      onClick={(e) => { e.stopPropagation(); onExit(x) }}>Exit</button>
            </td>
          </tr>,
          expanded ? (
            <tr key={`${x.pos_id}-d`}>
              <td className="td bg-surface/40" colSpan={12}>
                <div className="grid gap-4 md:grid-cols-3 py-1">
                  <div>
                    <div className="lbl mb-1">Entry</div>
                    <KV k="Order" v={<span className="mono">{x.entry.order_id ?? DASH}</span>} />
                    <KV k="Filled" v={`${int(x.entry.filled_qty)} @ ${price(x.entry.price)}`} />
                    <KV k="At" v={timeFromUs(x.entry.at_us)} />
                    <KV k="Reference" v={price(x.entry.ref_price)} />
                    <KV k="Diff at entry" v={price(x.entry.diff)} tone={signClass(x.entry.diff)} />
                    <KV k="Signal" v={<span className="mono">{x.sig_id ?? DASH}</span>} />
                  </div>
                  <div>
                    <div className="lbl mb-1">Trailing</div>
                    <KV k="Stop armed" v={x.trailing.sl_active ? 'Yes' : 'No'} />
                    <KV k="Stop peak" v={price(x.trailing.sl_peak, { zeroIsDash: true })} />
                    <KV k="Stop level" v={price(x.trailing.sl_level, { zeroIsDash: true })} />
                    <KV k="Target armed" v={x.trailing.tgt_active ? 'Yes' : 'No'} />
                    <KV k="Target level" v={price(x.trailing.tgt_level, { zeroIsDash: true })} />
                  </div>
                  <div>
                    <div className="lbl mb-1">Book & flags</div>
                    <KV k="Bid / Ask" v={`${price(x.live.bid, { zeroIsDash: true })} / ${price(x.live.ask, { zeroIsDash: true })}`} />
                    <KV k="Held" v={seconds(x.live.holding_seconds)} />
                    <KV k="Lots / lot size" v={`${x.lots} × ${x.instrument.lot_size}`} />
                    <KV k="Expiry" v={x.instrument.expiry ?? DASH} />
                    <KV k="Broker confirmed" v={x.flags.broker_confirmed ? 'Yes' : 'No'} />
                    <KV k="Reconciled" v={x.flags.reconciled ? 'Yes' : 'No'} />
                  </div>
                </div>
              </td>
            </tr>
          ) : null,
        ].filter(Boolean)
      })}
    </Table>
  )
}

// ---------------------------------------------------------------- closed

function ClosedTable({ rows }: { rows: Position[] }) {
  return (
    <Table colSpan={9} empty="Nothing closed today." head={<>
      <th className="th">Symbol</th>
      <th className="th num">Qty</th>
      <th className="th num">Entry</th>
      <th className="th num">Exit</th>
      <th className="th num">P&L</th>
      <th className="th num">P&L %</th>
      <th className="th">Trigger</th>
      <th className="th num">Entry at</th>
      <th className="th num">Exit at</th>
    </>}>
      {rows.map((x) => {
        const pnl = (x.exit.price - x.entry.price) * x.quantity
        const pnlPct = x.entry.price > 0 ? ((x.exit.price - x.entry.price) / x.entry.price) * 100 : 0
        return (
          <tr key={x.pos_id} className="hover:bg-surface/60">
            <td className="td font-medium">{x.instrument.tradingsymbol}</td>
            <td className="td num">{int(x.quantity)}</td>
            <td className="td num">{price(x.entry.price)}</td>
            <td className="td num">{price(x.exit.price)}</td>
            <td className={`td num font-medium ${signClass(pnl)}`}>{money(pnl)}</td>
            <td className={`td num ${signClass(pnlPct)}`}>{pct(pnlPct)}</td>
            <td className="td">{x.exit.trigger ?? DASH}</td>
            <td className="td num text-muted">{timeFromUs(x.entry.at_us)}</td>
            <td className="td num text-muted">{timeFromUs(x.exit.at_us)}</td>
          </tr>
        )
      })}
    </Table>
  )
}

// ---------------------------------------------------------------- orders

function OrdersTable() {
  const orders = useStore((s) => s.orders)
  const [only, setOnly] = useState(false)
  const rows = only ? orders.filter((o) => o.rejection) : orders

  return (
    <div className="space-y-2">
      <label className="flex items-center gap-2 text-micro text-muted">
        <input type="checkbox" checked={only} onChange={(e) => setOnly(e.target.checked)} />
        Rejected only
      </label>
      <Table colSpan={9} empty="No orders today." head={<>
        <th className="th num">Time</th>
        <th className="th">Symbol</th>
        <th className="th">Role</th>
        <th className="th">Side</th>
        <th className="th num">Qty</th>
        <th className="th num">Price</th>
        <th className="th num">Try</th>
        <th className="th">Status</th>
        <th className="th">Detail</th>
      </>}>
        {rows.map((o, i) => (
          <tr key={`${o.order_id ?? 'x'}-${i}`}
              className={o.rejection ? 'bg-neg/5' : 'hover:bg-surface/60'}>
            <td className="td num text-muted mono">{timeFromIso(o.at)}</td>
            <td className="td font-medium">{o.sym}</td>
            <td className="td">{o.role}</td>
            <td className="td">{o.side}</td>
            <td className="td num">{int(o.qty)}</td>
            <td className="td num">{price(o.price)}</td>
            <td className="td num">{o.attempt > 1
              ? <span className="text-warn">{o.attempt}</span> : o.attempt}</td>
            <td className={`td ${o.rejection ? 'text-neg' : ''}`}>
              {o.rejection ?? o.status ?? DASH}
            </td>
            <td className="td text-muted max-w-[420px] truncate" title={o.message ?? ''}>
              {o.message ?? DASH}
            </td>
          </tr>
        ))}
      </Table>
    </div>
  )
}

// ---------------------------------------------------------------- signals

function SignalsTable() {
  const signals = useStore((s) => s.signals)
  const orders = useStore((s) => s.orders)
  const ordered = new Set(orders.map((o) => o.sym))
  return (
    <Table colSpan={7} empty="No signals fired today." head={<>
      <th className="th num">Time</th>
      <th className="th">Symbol</th>
      <th className="th num">Reference</th>
      <th className="th num">Tick</th>
      <th className="th num">Diff</th>
      <th className="th num">Ask</th>
      <th className="th">Outcome</th>
    </>}>
      {signals.map((s) => (
        <tr key={s.sig_id} className="hover:bg-surface/60">
          <td className="td num text-muted mono">{timeFromIso(s.at)}</td>
          <td className="td font-medium">{s.sym}</td>
          <td className="td num">{price(s.ref)}</td>
          <td className="td num">{price(s.price)}</td>
          <td className={`td num font-medium ${signClass(s.diff)}`}>{price(s.diff)}</td>
          <td className="td num">{price(s.ask, { zeroIsDash: true })}</td>
          <td className="td">{ordered.has(s.sym)
            ? <span className="text-muted">Order placed</span>
            : <span className="text-warn">No order — refused</span>}</td>
        </tr>
      ))}
    </Table>
  )
}
