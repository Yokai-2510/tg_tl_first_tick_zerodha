# BUILD SPEC — Implementation Rules, Algorithms & Test Vectors

**Audience:** whoever (human or LLM) writes the code.
**Purpose:** remove all ambiguity. Every algorithm that can be got wrong is specified here with
pseudocode, edge cases, and test vectors with exact expected values.

**Read in this order:**
1. `FIRSTTICK_SYSTEM_IMPLEMENTATION_PLAN.md` — why the system exists, verified platform facts
2. `SYSTEM_DESIGN_AND_INTERFACES.md` — architecture, modules, schemas, APIs
3. **This document** — how to implement each part correctly
4. `DEVELOPER_SETUP_GUIDE.md` — how to deploy it

> **If this document and any other disagree, this document wins for implementation details.**

---

## 0. The 18 absolute rules

Violating any of these is a bug, even if tests pass.

| # | Rule | Why |
|---|---|---|
| **R1** | **No I/O inside `on_ticks` / `on_order_update`.** No HTTP, no file write, no `logger.*`, no `print`, no lock held by a slow thread. | Blocks the entire market feed for every instrument. |
| **R2** | **`recv_ns = time.perf_counter_ns()` is the FIRST statement** in the tick callback. | Anything before it is unmeasured latency. |
| **R3** | **One KiteTicker connection.** Market data and order updates share it. | Broker allows 3; more connections = more failure modes, no benefit. |
| **R4** | **After reconnect you MUST re-subscribe AND re-apply modes.** | Kite does not restore them. Silent data loss otherwise. |
| **R5** | **Buy prices round UP to tick; sell prices round DOWN.** | Rounding the wrong way puts you behind the touch and you don't fill. |
| **R6** | **Never send MARKET on a stock option.** | Zerodha rejects it. |
| **R7** | **One entry per instrument per session** (`fired` latch, set before the order is sent). | Prevents duplicate entries from the same tick batch. |
| **R8** | **Exits are idempotent** (`exiting` latch). | SL and TSL can trigger on the same tick; must send one exit. |
| **R9** | **Paper mode never calls a broker write endpoint.** | Guard at the single place orders are sent, not at call sites. |
| **R10** | **All schedule times are IST**, compared against `datetime.now(ZoneInfo("Asia/Kolkata"))`. | Server TZ must never be assumed. |
| **R11** | **`quantity = lots × lot_size`**, `lot_size` read from the instrument master. Never hardcode. | Lot sizes change. |
| **R12** | **Index detection is an explicit set**, never a substring match. | `"NIFTY" in "NIFTYBEES"` is `True`. |
| **R13** | **Interim order statuses are not terminal.** Only `COMPLETE`, `REJECTED`, `CANCELLED` end the lifecycle. | `OPEN PENDING`, `VALIDATION PENDING` etc. arrive first. |
| **R14** | **Reference price for options = previous close.** Options do not trade pre-open. | Section 5.2 of the plan. |
| **R15** | **Reconcile with the broker before arming** after any restart. | Prevents duplicate positions. |
| **R16** | **Upstox `tick_size` is PAISE.** Divide by 100 before any price maths. | 5.0 used raw rounds a 158.00 ask to 165.00 instead of 160.40. |
| **R17** | **Upstox `expiry` is epoch MILLISECONDS**, not a date. | Silently breaks the expiry roll. |
| **R18** | **Match contracts across brokers on `(underlying, expiry, strike, type)`**, never the tradingsymbol. | `INDIGO26AUG5300PE` vs `INDIGO 26 AUG 5300 PE`. |

---

## 1. Constants & lookup tables

```python
IST = ZoneInfo("Asia/Kolkata")

INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"}

BSE_INDICES   = {"SENSEX", "BANKEX"}          # → BFO exchange
# everything else that is an option → NFO

SPOT_KEY = {                                   # for REST quote() of the underlying
    "NIFTY":      "NSE:NIFTY 50",
    "BANKNIFTY":  "NSE:NIFTY BANK",
    "FINNIFTY":   "NSE:NIFTY FIN SERVICE",
    "MIDCPNIFTY": "NSE:NIFTY MID SELECT",
    "SENSEX":     "BSE:SENSEX",
    "BANKEX":     "BSE:BANKEX",
}

TERMINAL_STATUSES = {"COMPLETE", "REJECTED", "CANCELLED"}
```

```python
def is_index(symbol: str) -> bool:
    return symbol.upper() in INDEX_SYMBOLS          # R12 — exact match only

def exchange_for(symbol: str) -> str:
    return "BFO" if symbol.upper() in BSE_INDICES else "NFO"
```

---

## 2. Tick-size rounding *(R5)*

```python
def round_price(price: float, tick: float, mode: str) -> float:
    """mode: 'CEIL' for BUY limits, 'FLOOR' for SELL limits."""
    if tick <= 0:
        raise ValueError("tick must be > 0")
    q = price / tick
    if mode == "CEIL":
        n = math.ceil(round(q, 9))      # round() first kills float dust (2.0000000001)
    elif mode == "FLOOR":
        n = math.floor(round(q, 9))
    else:
        raise ValueError(mode)
    return round(n * tick, 2)
```

**Test vectors** (`tick = 0.05`):

| Input | Mode | Expected |
|---|---|---|
| 9.8365 | CEIL | **9.85** |
| 103.206 | CEIL | **103.25** |
| 1.854 | CEIL | **1.90** |
| 10.00 | CEIL | **10.00** ← exact value must not jump a tick |
| 10.001 | CEIL | **10.05** |
| 646.87 | FLOOR | **646.85** |
| 646.85 | FLOOR | **646.85** ← exact value must not drop a tick |
| 9.84 | FLOOR | **9.80** |

> The `round(q, 9)` is not decoration. Without it, `10.00 / 0.05 = 199.99999999999997` and `ceil`
> returns 200 → 10.05, silently paying an extra tick on every exact-value order.

---

## 3. Expiry resolution

```python
def resolve_expiry(symbol, expiries_sorted, today, cfg) -> date:
    """
    expiries_sorted: ascending list[date] for this underlying.
    Stock options are physically settled; Zerodha blocks fresh MIS buys in the
    last two trading days. Roll to the next expiry for STOCKS ONLY.
    """
    future = [e for e in expiries_sorted if e >= today]
    if not future:
        return expiries_sorted[-1] if expiries_sorted else None

    if is_index(symbol):                       # cash-settled → always nearest
        return future[0]
    if not cfg["expiry_roll"]["enabled"]:
        return future[0]

    buffer_days = cfg["expiry_roll"]["buffer_trading_days"]     # default 1
    trading_days_left = np.busday_count(today, future[0])       # weekdays, excl. end date
    if trading_days_left <= buffer_days and len(future) > 1:
        return future[1]
    return future[0]
```

**Test vectors** — July expiry Tue 28-Jul-2026, August expiry Tue 25-Aug-2026:

| Symbol | Today | `busday_count` | Expected | Reason |
|---|---|---|---|---|
| INDIGO | Mon 20-Jul | 6 | **28-Jul** | far from expiry |
| INDIGO | Fri 24-Jul | 2 | **28-Jul** | last two trading days are Mon 27 + Tue 28 |
| INDIGO | Mon 27-Jul | 1 | **25-Aug** | day before expiry → roll |
| INDIGO | Tue 28-Jul | 0 | **25-Aug** | expiry day → roll |
| NIFTY | Mon 27-Jul | 1 | **28-Jul** | index → never rolls |
| SENSEX | Tue 28-Jul | 0 | **28-Jul** | index → never rolls |
| INDIGO | Mon 27-Jul, only `[28-Jul]` exists | 1 | **28-Jul** | no next expiry → stay |
| INDIGO | Fri 31-Jul, expiries `[Mon 3-Aug, 31-Aug]` | 1 | **31-Aug** | weekend gap handled by trading-day count |

⚠️ **Known limitation:** `busday_count` handles weekends, not exchange holidays. Phase 1 must
cross-check against the market-calendar and log a warning on disagreement.

---

## 3A. Multi-broker normalisation

`data_broker` and `trade_broker` are independent (zerodha | upstox, plus `paper`
for trading). Every adapter converts to the canonical shapes in
`backend/brokers/base.py`; the engine has **no** broker conditionals.

```python
def paise_to_rupees(tick_size) -> float:
    v = float(tick_size or 0)
    if v <= 0:
        return 0.05
    return round(v / 100.0, 4) if v >= 1 else round(v, 4)   # >=1 means paise
```

**Test vectors:**

| Upstox `tick_size` | Canonical | Note |
|---|---|---|
| 5.0 | **0.05** | the trap |
| 10.0 | 0.10 | |
| 100.0 | 1.00 | |
| 0.05 | 0.05 | already rupees, unchanged |
| 0 / None | 0.05 | safe default |

| Upstox `expiry` | Canonical |
|---|---|
| 1787616000000 | `date(2026, 8, 25)` |
| 0 / None / "" | `None` |

**Status normalisation** — `normalise_status()` maps every broker spelling onto the
canonical vocabulary before `is_terminal()` is consulted:

| Raw | Canonical |
|---|---|
| `complete`, `completed`, `filled` | `COMPLETE` |
| `cancelled`, `canceled` | `CANCELLED` |
| `  Open   Pending  ` | `OPEN PENDING` (not terminal) |

**Product mapping:**

| Canonical | Kite | Upstox |
|---|---|---|
| stock options | `NRML` | `D` (intraday not offered — physically settled) |
| index options | `MIS` | `I` |

**Instrument identity.** `token` is the engine PK and comes from the DATA broker.
String-keyed brokers get `surrogate_token(key)` — a deterministic 56-bit blake2b
digest, stable across restarts so recorded tick files stay readable. A 32-bit CRC
would collide at ~77k instruments; verified collision-free across 50k keys.

**Cross-broker resolution.** `BrokerPair.resolve()` attaches `trade_key` by matching
`contract_id`. If the contract is absent at the trade broker it **raises** —
never guess an order identifier.

---

## 4. Ranking & shortlist

```python
def rank(snapshot) -> tuple[list, list]:
    """snapshot: {symbol: {"ltp": float, "prev_close": float}} at settlement."""
    rows = []
    for sym, d in snapshot.items():
        pc, ltp = d.get("prev_close", 0.0), d.get("ltp", 0.0)
        if pc <= 0 or ltp <= 0:
            continue                                    # never rank on missing data
        rows.append((sym, (ltp - pc) / pc * 100.0))
    rows.sort(key=lambda r: r[1], reverse=True)
    gainers = rows                                       # most positive first
    losers  = list(reversed(rows))                       # most negative first
    return gainers, losers


def shortlist(gainers, losers, cfg) -> tuple[set, set]:
    """Returns (tradeable, subscribe_only). Buffer names are SUBSCRIBED, not traded."""
    ng, nl = cfg["top_n_gainers"], cfg["top_n_losers"]
    buf    = cfg["candidate_buffer"]

    trade_g = {s for s, _ in gainers[:ng]}
    trade_l = {s for s, _ in losers[:nl]}
    sub_g   = {s for s, _ in gainers[:ng + buf]}
    sub_l   = {s for s, _ in losers[:nl + buf]}

    tradeable = trade_g | trade_l
    subscribe = (sub_g | sub_l) - tradeable
    return tradeable, subscribe
```

**Edge cases:** a symbol can appear in both lists only if fewer than `ng+nl+2*buf` symbols have data —
dedupe via sets. Symbols with `prev_close <= 0` are excluded, never defaulted to 0 %.

---

## 5. ATM & strike selection

```python
def find_atm(strikes_sorted: list[float], spot: float) -> int:
    """Index of the strike nearest spot. Ties → lower strike (deterministic)."""
    return min(range(len(strikes_sorted)),
               key=lambda i: (abs(strikes_sorted[i] - spot), strikes_sorted[i]))


def pick_strike(strikes_sorted, spot, option_type, reference, offset) -> float:
    """
    reference: 'ATM' | 'ITM' | 'OTM'.  offset: 0 = first strike in that bucket.
    Moneyness is direction-dependent:
        CE: ITM = LOWER strikes,  OTM = HIGHER strikes
        PE: ITM = HIGHER strikes, OTM = LOWER strikes
    """
    atm = find_atm(strikes_sorted, spot)
    if reference == "ATM":
        idx = atm
    else:
        step = 1 if (
            (option_type == "CE" and reference == "OTM") or
            (option_type == "PE" and reference == "ITM")
        ) else -1
        idx = atm + step * (1 + offset)
    return strikes_sorted[max(0, min(idx, len(strikes_sorted) - 1))]   # clamp, never wrap
```

**Test vectors** — `strikes = [100,105,110,115,120]`, `spot = 111` (ATM = 110, index 2):

| type | reference | offset | Expected |
|---|---|---|---|
| CE | ATM | 0 | 110 |
| CE | OTM | 0 | 115 |
| CE | OTM | 1 | 120 |
| CE | ITM | 0 | 105 |
| CE | ITM | 1 | 100 |
| PE | OTM | 0 | 105 |
| PE | ITM | 0 | 115 |
| CE | OTM | 5 | **120** (clamped, not wrapped) |

**Strike band to subscribe** — `strikes_per_side` each way around ATM, both CE and PE:
`strikes_sorted[max(0, atm-n) : atm+n+1]` → `2n+1` strikes × 2 types.

---

## 6. The trigger *(hot path — R1, R2)*

```python
class ArmedState:
    __slots__ = ("token","sym","underlying","option_type","strike","lot_size","tick_size",
                 "ref_price","min_diff","lots","fired","is_index")

def evaluate(tick: dict, st: ArmedState, cfg) -> Signal | None:
    """PURE. No logging, no I/O, no exceptions for control flow. Returns None or a Signal."""
    if st.fired:
        return None

    price = tick.get("last_price", 0.0)
    if price <= 0.0 or st.ref_price <= 0.0:
        return None

    diff = price - st.ref_price
    if diff <= cfg["min_diff"]:                  # default 0.0 → strictly positive
        return None

    bid = ask = 0.0
    depth = tick.get("depth")                    # present only in FULL mode
    if depth:
        s, b = depth.get("sell"), depth.get("buy")
        ask = s[0]["price"] if s else 0.0
        bid = b[0]["price"] if b else 0.0

    if cfg["require_depth"] and ask <= 0.0:
        return None                              # cannot price marketably → skip this tick

    if cfg["min_premium"] and price < cfg["min_premium"]:  return None
    if cfg["max_premium"] and price > cfg["max_premium"]:  return None

    st.fired = True                              # R7 — latch BEFORE returning
    return Signal(token=st.token, sym=st.sym, ref_price=st.ref_price,
                  tick_price=price, diff=diff, best_bid=bid, best_ask=ask,
                  lots=st.lots, quantity=st.lots * st.lot_size,
                  t_signal_ns=time.perf_counter_ns())
```

**Callback skeleton — implement exactly this shape:**

```python
def on_ticks(self, ws, ticks):
    recv_ns = time.perf_counter_ns()             # R2 — FIRST statement
    self.record_q.put((ticks, recv_ns))          # R1 — O(1), never blocks

    if self.phase != "TRADING" or not self.armed_enabled:
        return
    if recv_ns < self.fire_after_ns:             # fire_after_seconds past 09:15
        return
    if recv_ns > self.deadline_ns:               # deadline_seconds guard
        return

    armed = self.armed                            # local ref — one dict lookup
    for t in ticks:
        st = armed.get(t["instrument_token"])
        if st is None or st.fired:
            continue
        sig = evaluate(t, st, self.entry_cfg)
        if sig is not None:
            self.intent_q.put_nowait(sig)         # hand off; executor does the HTTP
    return                                        # NO logging anywhere above
```

**Gate order matters:** phase → armed → time window → per-instrument. Cheapest checks first.

---

## 7. Entry price computation

```python
def entry_limit_price(sig, cfg, tick_size, symbol) -> float:
    src      = cfg["entry_price_source"]          # "ask" | "ltp"
    slippage = cfg["entry_slippage_pct"] / 100.0

    base = sig.best_ask if (src == "ask" and sig.best_ask > 0) else sig.tick_price
    if base <= 0:
        raise ValueError("no valid price basis")
    return round_price(base * (1.0 + slippage), tick_size, "CEIL")   # R5


def exit_limit_price(position, cfg, tick_size, eod: bool = False) -> float:
    src      = cfg["exit_price_source"]           # "bid" | "ltp"
    pct      = cfg["eod_slippage_pct"] if eod else cfg["exit_slippage_pct"]
    slippage = pct / 100.0

    base = position.live.bid if (src == "bid" and position.live.bid > 0) else position.live.ltp
    if base <= 0:
        raise ValueError("no valid price basis")
    return round_price(base * (1.0 - slippage), tick_size, "FLOOR")  # R5
```

**Test vectors** (`tick = 0.05`, `entry_slippage_pct = 1.5`, `exit_slippage_pct = 1.0`):

| Case | Input | Expected |
|---|---|---|
| Entry, ask available | ask = 158.00 | `158 × 1.015 = 160.37` → CEIL → **160.40** |
| Entry, ask = 0, ltp = 158 | require_depth=false | `160.37` → **160.40** |
| Entry, cheap option | ask = 1.80 | `1.827` → **1.85** |
| Exit | bid = 170.90 | `169.191` → FLOOR → **169.15** |
| Exit EOD (3 %) | bid = 170.90 | `165.773` → **165.75** |

> **Why a marketable limit is not "overpaying":** a BUY limit executes at the *resting ask*, which is
> ≤ our price. The buffer buys fill-certainty, not a worse price. Proven live: 23 Jul ETERNAL limit
> 10.50, **filled 9.15**.

---

## 8. Order state machine

```
                ┌──────────► REJECTED ──► classify() ──► retry? ──► NEW attempt
                │                                    └─► FAILED (terminal)
NEW ─► SENT ─► ACK ─► [interim…] ─► COMPLETE ──► position ACTIVE
                │                └─► partial (IOC) ─► residual? ─► NEW attempt
                └──────────► CANCELLED ─► FAILED
```

```python
def submit_entry(sig, cfg, kite, mode) -> Position | None:
    attempt, residual = 0, sig.quantity
    price = entry_limit_price(sig, cfg, tick_size, sig.sym)

    while attempt < cfg["entry_retry"]["max_attempts"] and residual > 0:
        attempt += 1

        if mode == "paper":                                    # R9 — single guard point
            return simulate_fill(sig, price, residual)

        if not ratelimit.acquire("order"):
            sleep(0.05); continue

        res = orders.place(kite, token=sig.token, sym=sig.sym,
                           exchange=exchange_for(sig.underlying),
                           side="BUY", quantity=residual, price=price,
                           order_type=order_type_for(sig, cfg),
                           product=product_for(sig, cfg),
                           validity=cfg["entry_validity"], tag=pos_id)

        if res.success:
            final = await_terminal(kite, res.order_id, timeout_s=5)   # R13
            if final.status == "COMPLETE":
                return build_position(sig, final)
            if final.status in ("CANCELLED",) and final.filled_quantity > 0:
                residual -= final.filled_quantity                     # IOC partial
                price = reprice_from_live_feed(sig.token, cfg)        # NOT a REST quote
                continue
            if final.status == "REJECTED":
                res = final

        kind = classify_rejection(res)
        if kind == "LPP" and cfg["lpp"]["retries"] > 0:
            price = lpp_reprice(res.lpp_limit, sig.token, cfg, tick_size)
            continue
        if kind == "ORDER_TYPE" and cfg["order_fallback"]["enabled"]:
            cfg = with_fallback_order_type(cfg)                       # → MARKETABLE_LIMIT
            continue
        if kind in ("MARGIN", "RMS", "AUTH"):
            return None                                               # never retry
        if kind in ("NETWORK", "RATE_LIMIT"):
            sleep(backoff(attempt)); continue

    return None
```

**Rules:**
- `await_terminal` must **ignore interim statuses** (R13) and keep polling / consuming order-update
  events until `COMPLETE` / `REJECTED` / `CANCELLED` or timeout.
- Re-pricing on retry reads the **in-memory last tick** (zero latency), never a REST quote (1 req/s).
- Every attempt is recorded separately in `orders/*.jsonl` with its own `attempt` number.

### 8.1 LPP re-pricing

```python
def lpp_reprice(lpp_limit, token, cfg, tick_size) -> float:
    last = feed.last(token)                       # in-memory
    ltp  = last.ltp if last else 0.0
    cap_from_ltp  = ltp * 1.09 if ltp > 0 else float("inf")
    cap_from_msg  = lpp_limit * cfg["lpp"]["safety_factor"]   # 0.99
    return round_price(min(cap_from_ltp, cap_from_msg), tick_size, "FLOOR")  # FLOOR: stay inside
```
Parse with: `re.search(r"allowed LPP limit \(([\d.]+)\)", status_message)`.

**Test vector** — real rejection from 24 Jul:
`"This order is outside the allowed LPP limit (646.85)."` → parsed `646.85`
→ `646.85 × 0.99 = 640.38` → FLOOR(0.05) → **640.35** ✓ inside the band.

---

## 9. Exit engine

```python
PRIORITY = [MANUAL_BROKER, MANUAL_API, STOP_LOSS, TARGET,
            TRAILING_TARGET, TRAILING_SL, TIME_EXIT, EOD_SQUAREOFF]

def evaluate_exit(pos, cfg, now_ist) -> tuple[bool, str | None]:
    if pos.flags.exiting:                       # R8
        return False, None
    for check in PRIORITY:
        hit, name = check(pos, cfg, now_ist)
        if hit:
            return True, name
    return False, None
```

```python
def pnl_pct(pos, cfg) -> float:
    """All positions are LONG options (BUY to open). No short handling required."""
    basis = pos.live.bid if cfg["pnl_basis"] == "bid" and pos.live.bid > 0 else pos.live.ltp
    entry = pos.entry.price
    if entry <= 0 or basis <= 0:
        return 0.0
    return (basis - entry) / entry * 100.0

def pnl_rupees(pos, cfg) -> float:
    basis = pos.live.bid if cfg["pnl_basis"] == "bid" and pos.live.bid > 0 else pos.live.ltp
    return (basis - pos.entry.price) * pos.quantity
```

**Trailing stop — ratchet only, never loosens:**

```python
def update_trailing_sl(pos, c):
    if not c["enabled"]:
        return
    p = pnl_pct(pos, cfg)
    if p < c["activation_pct"]:
        return                                   # not armed yet
    ltp = pos.live.ltp
    if not pos.trailing.sl_active:
        pos.trailing.sl_active = True
        pos.trailing.sl_peak   = ltp
    if ltp > pos.trailing.sl_peak:
        pos.trailing.sl_peak = ltp
    new_level = round(pos.trailing.sl_peak * (1 - c["trail_distance_pct"]/100), 4)
    if new_level > pos.trailing.sl_level:        # ratchet: only ever moves UP
        pos.trailing.sl_level = new_level

def check_trailing_sl(pos, cfg, _now):
    c = cfg["trailing_stop"]
    if not c["enabled"] or not pos.trailing.sl_active:
        return False, None
    return (pos.live.ltp <= pos.trailing.sl_level), "TRAILING_SL"
```

**Test vector** — entry 100, `activation_pct = 7`, `trail_distance_pct = 3`:

| Step | LTP | pnl% | armed | peak | level | Exit? |
|---|---|---|---|---|---|---|
| 1 | 104 | 4.0 | no | – | 0 | no |
| 2 | 108 | 8.0 | **yes** | 108 | 104.76 | no |
| 3 | 115 | 15.0 | yes | 115 | 111.55 | no |
| 4 | 112 | 12.0 | yes | 115 | **111.55** (unchanged) | no |
| 5 | 111 | 11.0 | yes | 115 | 111.55 | **YES → TRAILING_SL** |

**Interaction rule:** when `trailing_target.enabled` is true, `TARGET` must **not** fire at
`target.percentage` — it fires only at `trailing_target.max_extension_pct`. Otherwise the target exit
would pre-empt the trail every time.

---

## 10. Position book & reconciliation

### 10.1 From order updates (fast path)

```python
def on_order_event(e):
    pos = book.by_tag(e.get("tag")) or book.by_order_id(e["order_id"])
    if pos is None:
        return                                   # not ours (manual order in Kite app)
    status = e["status"]
    if status not in TERMINAL_STATUSES:          # R13
        pos.record_postback(e); return
    if status == "COMPLETE":
        if e["transaction_type"] == "BUY":
            pos.mark_entry_filled(e["average_price"], e["filled_quantity"])
            pos.status = "ACTIVE"
        else:
            pos.mark_exit_filled(e["average_price"], e["filled_quantity"])
            pos.status = "CLOSED"
```

### 10.2 Manual-close detection (slow path, 2 s)

```python
def broker_sync():
    broker = {p["tradingsymbol"]: p for p in kite.positions()["day"]}
    for pos in book.active():
        bp = broker.get(pos.sym)
        if bp is None or bp["quantity"] == 0:
            if pos.exit.order_id is None:                # we didn't exit it
                close_locally(pos, trigger="MANUAL_BROKER")
        elif abs(bp["quantity"]) != pos.quantity:
            log.warning(f"QTY DRIFT {pos.sym}: broker={bp['quantity']} local={pos.quantity}")
```

### 10.3 Restart reconciliation *(R15)*

```
local book (disk)  +  kite.positions()  +  kite.orders()
   ├─ in both, matching qty            → resume managing
   ├─ local ACTIVE, broker qty 0       → mark CLOSED (MANUAL_BROKER)
   ├─ broker qty ≠ 0, not in local     → adopt as ADOPTED_UNMANAGED (exitable, not a strategy entry)
   └─ local PENDING, order COMPLETE    → promote to ACTIVE with broker's average_price
ONLY after this completes: arm entries.
```

---

## 11. Recorder

```python
def recorder_loop(q, cfg):
    fh, seq, last_flush = open_file(now_hour()), 0, time.time()
    while running:
        try:
            item = q.get(timeout=0.5)
        except Empty:
            maybe_flush(); continue

        ticks, recv_ns = item
        recv_us, seq = int(time.time()*1e6), seq + 1
        for t in ticks:
            fh.write(orjson.dumps(to_record(t, recv_ns, recv_us, seq, len(ticks))) + b"\n")

        if time.time() - last_flush > cfg["flush_interval_ms"]/1000:
            fh.flush(); last_flush = time.time()
        if hour_changed():
            fh = rotate(fh)
        if disk_low():
            handle_disk_full(cfg["on_disk_full"])
```

**Critical details:**
- `exchange_timestamp` from pykiteconnect is a **`datetime` object, not an epoch int** —
  convert with `int(dt.timestamp() * 1_000_000)` before storing. Same for `last_trade_time`.
- Depth is **absent** in non-`full` modes: `t.get("depth")` may be `None`. Never index blindly.
- `feed_lag_us = recv_us − exch_ts_us`. Can be negative if clocks are skewed → that is a **clock
  problem**, log it, don't clamp it to zero.
- Queue is **unbounded** (`SimpleQueue`); the recorder must never apply back-pressure to the WS thread.

---

## 12. Paper mode

```python
def simulate_fill(sig, limit_price, qty):
    """fill_model 'touch': fill at the ask we saw (what a marketable limit really gets)."""
    fill = sig.best_ask if sig.best_ask > 0 else sig.tick_price
    fill = min(fill, limit_price)                  # never fill worse than our limit
    return Position(entry_price=fill, quantity=qty, mode="paper", broker_confirmed=False)
```
Paper mode still runs the **full** trigger, pricing, exit and recorder path — only the broker write
call is skipped. Charges are simulated if `paper.simulate_charges`.

---

## 13. Implementation order & acceptance criteria

| Step | Build | Done when |
|---|---|---|
| 1 | `config/loader.py` | Invalid config fails with the JSON path; defaults applied; hot-reload fires callback |
| 2 | `brokers/kite/auth.py` | TOTP login returns a working client; token cached; `is_valid` correct |
| 3 | `brokers/kite/instruments.py` | All §3 expiry vectors pass; §5 strike vectors pass |
| 4 | `brokers/kite/orders.py` | All §2 rounding vectors pass; §8.1 LPP vector passes |
| 5 | `brokers/kite/ticker.py` | Connects; **re-subscribes after a forced disconnect (R4)**; order updates arrive |
| 6 | `engine/recorder.py` | 10k synthetic ticks → 10k lines, zero drops, `batch_seq` contiguous |
| 7 | `engine/feed.py` + `trigger.py` | Hot path < 50 µs/batch measured; §6 unit tests pass |
| 8 | `engine/universe.py` | §4 ranking + shortlist correct; wave plans within `subscription_soft_cap` |
| 9 | `engine/executor.py` | Paper fills; live 1-lot fills; retry ladder exercised against a mock |
| 10 | `engine/positions.py` | Fills via order events; all four reconciliation branches tested |
| 11 | `engine/exits.py` | §9 trailing vector passes; all 8 conditions unit-tested; idempotent |
| 12 | `engine/scheduler.py` | Phases fire at configured times; illegal transitions rejected |
| 13 | `api/*` | Every §7 endpoint responds; WS snapshot+diff; auth enforced |
| 14 | `main.py` | systemd-managed; survives crash + reboot; reconciles on restart |

---

## 14. Anti-patterns — things implementations get wrong

| ❌ Don't | ✅ Do |
|---|---|
| `logger.info()` inside `on_ticks` | Enqueue; log from the recorder/executor thread |
| `kite.quote()` to re-price a retry | Read `feed.last(token)` from memory |
| `requests.post()` inside the tick callback | `intent_q.put_nowait()` → executor thread |
| Treat `"OPEN PENDING"` as failure | Only `COMPLETE`/`REJECTED`/`CANCELLED` are terminal |
| `round(price/tick)*tick` | `round_price(..., "CEIL"/"FLOOR")` — direction matters |
| `if "NIFTY" in symbol` | `symbol.upper() in INDEX_SYMBOLS` |
| Assume `tick["depth"]` exists | `tick.get("depth")` and check mode |
| Treat `exchange_timestamp` as int | It's a `datetime`; convert explicitly |
| One lock around all state | One owner thread per structure; publish snapshots |
| Retry a MARGIN rejection | Only LPP / ORDER_TYPE / NETWORK / RATE_LIMIT are retryable |
| Re-subscribe forgotten after reconnect | Re-subscribe **and** re-apply modes (R4) |
| Send MARKET on a stock option | LIMIT only (R6) |
| `datetime.now()` for schedule checks | `datetime.now(IST)` (R10) |
| Hardcode `lot_size = 25` | Read from instrument master (R11) |
| Arm entries before reconciliation | Reconcile first (R15) |
| Bound the recorder queue | Unbounded; never back-pressure the feed |

---

## 15. Minimum test suite

**Pure unit (no broker, no clock, no network) — must all pass before any live run:**
```
test_round_price          # 8 vectors from §2
test_resolve_expiry       # 8 vectors from §3
test_pick_strike          # 8 vectors from §5
test_rank_and_shortlist   # incl. prev_close<=0 exclusion, dedupe
test_trigger              # fires once; latch; require_depth; min/max premium; ref<=0
test_entry_exit_price     # 5 vectors from §7
test_parse_lpp            # real 24-Jul message → 646.85
test_lpp_reprice          # → 640.35
test_trailing_sl          # 5-step ratchet from §9
test_exit_priority        # SL wins over TARGET; trailing_target suppresses TARGET
test_pnl                  # ltp basis vs bid basis
test_is_index             # "NIFTYBEES" is NOT an index
```

**Integration (mock broker + recorded ticks):**
```
test_replay_session       # recorded NDJSON → expected signals/orders
test_ioc_partial_fill     # residual re-placed
test_reconnect_resubscribe
test_restart_reconcile    # all four branches
test_paper_no_broker_calls  # assert zero write calls in paper mode
```

---

## 16. Glossary

| Term | Meaning |
|---|---|
| **Armed** | Instrument is subscribed, has a reference price, and may fire an entry |
| **Fired** | Its one allowed entry signal has been generated (latch) |
| **Reference price** | Baseline for the diff. For options = **previous close** (R14) |
| **Settlement snapshot** | 09:09 capture after the equity pre-open auction; ranking + ATM basis |
| **Marketable limit** | Buy limit ≥ best ask (or sell ≤ best bid) → fills on arrival |
| **LPP** | Zerodha's Last Price Protection band; orders outside it are rejected |
| **Wave 1 / Wave 2** | 08:55 stocks+indices / 09:09 option chains after ranking |
| **Buffer (candidate_buffer)** | Extra ranks subscribed but **not** traded |
| **Hot path** | Code inside the WS callback; < 50 µs, no I/O |
| **feed_lag** | `recv_us − exchange_timestamp` — broker/exchange delay, not ours |
| **Adopted position** | Broker position we didn't open; managed but not counted as a strategy entry |
