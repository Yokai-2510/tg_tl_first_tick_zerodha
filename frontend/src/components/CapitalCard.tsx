/** Capital: available vs deployed, with the margin breakdown behind it. */

import type { Capital } from '../lib/api'
import { money, pct } from '../lib/format'
import { KV } from './ui'

export default function CapitalCard({ cap, detail = false }:
  { cap: Capital | undefined; detail?: boolean }) {
  if (!cap) return null
  const total = cap.total || 0
  const usedPct = total > 0 ? (cap.used / total) * 100 : 0
  const b = cap.breakdown ?? { debits: 0, span: 0, exposure: 0, option_premium: 0 }

  return (
    <div className="card p-4">
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="lbl">Capital</div>
        <span className={`text-label uppercase tracking-wider ${
          usedPct > 0 ? 'text-warn' : 'text-muted'}`}>
          {usedPct > 0 ? `${pct(usedPct, { sign: false })} deployed` : 'idle'}
        </span>
      </div>

      <div className="text-kpi font-semibold leading-none">{money(cap.available)}</div>
      <div className="text-micro text-muted mt-1.5">
        available of {money(total)}
        {cap.simulated && <span className="ml-1.5 text-warn">simulated</span>}
      </div>

      {/* deployed vs free — one bar, two segments, no decoration */}
      <div className="flex h-1.5 rounded overflow-hidden bg-line mt-3" role="img"
           aria-label={`${pct(usedPct, { sign: false })} deployed`}>
        <div className="bg-warn" style={{ width: `${Math.min(100, usedPct)}%` }} />
        <div className="bg-pos/50" style={{ width: `${Math.max(0, 100 - usedPct)}%` }} />
      </div>
      <div className="flex justify-between text-[11px] text-muted mt-1">
        <span>Deployed {money(cap.used)}</span>
        <span>Free {money(cap.available)}</span>
      </div>

      {detail && (
        <div className="mt-3 pt-3 border-t border-line">
          <div className="lbl mb-1">Margin breakdown</div>
          <KV k="Debits" v={money(b.debits)} />
          <KV k="SPAN" v={money(b.span)} />
          <KV k="Exposure" v={money(b.exposure)} />
          <KV k="Option premium" v={money(b.option_premium)} />
          <KV k="Opening balance" v={money(cap.opening_balance)} />
          <KV k="Intraday payin" v={money(cap.payin)} />
          <KV k="Net" v={money(cap.net)} />
          {cap.simulated && (
            <div className="text-[11px] text-muted mt-2 leading-snug">
              Paper mode — derived from starting_capital plus realised P&L, less the
              notional of open positions. Not a broker balance.
            </div>
          )}
        </div>
      )}
    </div>
  )
}
