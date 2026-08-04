# First-Tick Open-Drive System — Implementation Plan (Zerodha / Kite Connect)

> **Status:** Design document. No code written yet.
> **Scope:** Backend only — engine, tick recorder, Zerodha facade, REST + WebSocket APIs.
> Frontend is out of scope (it consumes these APIs; hosted separately on Vercel/GitHub Pages).
> **Strategy:** Single strategy — *trade the first tick showing a positive premium difference*.
> Direction logic is the existing one. No multi-strategy framework.

---

## 1. Objective and non-goals

### Objective
A **small, fast, auditable** service that:
1. Wakes itself at **09:00 IST**, prepares everything, and is fully armed before the open.
2. Watches **N top gainers + N top losers** (Nifty 50 constituents) plus **selected indices**
   (NIFTY / BANKNIFTY / FINNIFTY / SENSEX) — all selectable and configurable.
3. Fires on the **first positive-difference tick** per instrument, at the lowest achievable latency.
4. Records **every tick** for every relevant instrument, from WebSocket connect until the last
   position closes — so latency and fill problems can be proven from data instead of guessed at.
5. Manages exits (SL / Target / TSL / time / EOD / manual-detected) with per-condition toggles.
6. Exposes fast REST + WS endpoints for a remote frontend.

### Explicit non-goals
- No multi-strategy engine, no strategy registry, no per-strategy config trees.
- No frontend/GUI work.
- No backtester.
- No ML/prediction layer.

### The one-line problem statement
Today's algo is a single-symbol script, one OS process per symbol, no shared feed, no tick history,
and diagnosis happens by reading a shared text log after the fact. This plan replaces it with **one
process, one WebSocket, one recorder, one audit trail** — without inheriting the 13k-line weight of
the rank-momentum codebase.

---

## 2. Verified platform constraints (Kite Connect)

These were checked against Kite's live docs, not assumed. **They drive the design.**

| Constraint | Value | Design consequence |
|---|---|---|
| WS connections per API key | **3 max** | Use **exactly one**. No per-symbol connections. |
| Instruments per WS connection | **3,000 max** | Our worst case is ~1,000. Comfortable, but budget it (§9). |
| Mode packet sizes | `ltp` 8B / `quote` 44B / `full` 184B | Mixed-mode subscription (§9). Don't put everything on `full`. |
| Market depth (5 bid + 5 ask) | **`full` mode only** | Tradeable options **must** be `full` — bid/ask drives entry pricing. |
| Order updates | Same WS, `{"type":"order","data":{…}}` → `on_order_update` | **Order stream is free** — no second connection, no polling for fills. |
| Order placement rate | **10/sec**, 400/min, 5,000/day | Token-bucket limiter in the facade. Log rejects. |
| Quote REST | **1 req/sec** | Batch quotes; never call `quote()` inside the tick callback. |
| Modifications per order | **25 max**, then must cancel/replace | Cap modify attempts; fall back to cancel+replace. |
| Order statuses | `OPEN`, `COMPLETE`, `CANCELLED`, `REJECTED` + interim (`VALIDATION PENDING`, `OPEN PENDING`, …) | State machine must tolerate interim states, not treat them as terminal. |
| **MARKET orders on stock options** | **Blocked/restricted at Zerodha (illiquid contracts)** | **Entry and exit must be LIMIT-only for stock options.** No MARKET fallback. |
| Stock F&O physical settlement | Compulsory delivery for ITM at expiry | Expiry-roll rule (§11.4) is mandatory, not optional. |

> ⚠️ **There is no position stream.** Kite pushes *order* updates only. Real-time positions are
> **derived locally** from fills and **reconciled** against `GET /portfolio/positions`. Any claim of a
> "position websocket" would be inaccurate — §14 specifies how we get real-time behaviour honestly.

---

## 3. What we take from `rank_mementum_setup_vijay` — and what we drop

I read the repo (Backend ≈ 13k LOC). Verdict per subsystem:

### Adopt (proven, cheap to carry)
| From repo | Why |
|---|---|
| **Broker facade layout** (`brokers/upstox/{auth,orders,positions,instruments,websocket,rate_limiter}.py`) — state-free modules, one API area each, self-testable | Exactly the facade the user asked for. Mirror it as `brokers/kite/`. |
| **Two-phase daily scheduler** (Phase 1 premarket → Phase 2 trading, `SETUP/IDLE/PHASE_1/PHASE_1_DONE/PHASE_2` state machine) | Directly satisfies "start automatically at 09:00". Reuse the phase model verbatim. |
| **Exit-condition module shape** — priority-ordered checks, each independently toggled, `(triggered, trigger_name)` return | Clean, testable, matches the requested SL/TSL/Target/toggle design. Adopt nearly as-is. |
| **`entry_price_source` / `exit_price_source` + slippage buffer** config idiom (`ask`+buffer for entry, `bid`−buffer for exit) | Precisely the "bid/ask source configurable" requirement, already well-shaped. |
| **Config-with-`_doc`-strings** JSON convention | Self-documenting config; frontend can render help text from it. |
| **Crash-recovery outer loop** (restart on unhandled exception, max-crash cap) | Cheap resilience for an unattended morning process. |
| **`trading_mode` paper/live with derived flags** | Matches paper/live requirement. |

### Drop (weight without payoff here)
| Dropped | Reason |
|---|---|
| **1 ms polling data-processor** (`websocket_data.py`: `while… _update_stocks(); _update_options(); sleep(0.001)`) | This is the repo's biggest latency sin: it re-scans **every** instrument dict under a global lock every millisecond, then the strategy reads the result. For a first-tick strategy this adds latency and jitter for zero benefit. **Replaced by event-driven evaluation inside the tick callback** (§8). |
| Multi-strategy registry, per-strategy config blocks, strategy segregation | Explicitly out of scope. |
| Leaderboard overtake / volume-overtake / ND-threshold / composite direction modes | Not our strategy. Keep only the existing first-tick direction logic. |
| Playwright browser auth | Kite has clean TOTP login (already working in `login.py`). |
| Snapshot service (5 timed CSV snapshots), artifacts, reports-by-date, Tauri/Netlify frontends | Recorder + a thin reports endpoint covers the real need. |
| Global SSOT dict + 6 named locks | Replaced by narrow, purpose-built structures (§8.3). A single giant locked dict is the main contention source in the hot path. |

---

## 4. Architecture overview

**One process. Three thread classes. One WebSocket.**

```
                    ┌──────────────────────────────────────────────┐
                    │  KiteTicker  (ONE connection, threaded=True) │
                    │   on_ticks ──────► market data (all instr.)  │
                    │   on_order_update ► order/fill postbacks     │
                    └───────┬──────────────────────────┬───────────┘
                            │ (WS thread — never blocks)│
             ┌──────────────▼───────────┐   ┌───────────▼────────────┐
             │  HOT PATH (inline)       │   │ order event → OrderBus │
             │  • stamp recv_ns         │   └───────────┬────────────┘
             │  • recorder.put() (O(1)) │               │
             │  • armed? → eval trigger │   ┌───────────▼────────────┐
             │  • fire → intent queue   │   │ PositionBook (in-mem)  │
             └───┬──────────────┬───────┘   │ fills → avg price, qty │
                 │              │           └───────────┬────────────┘
    ┌────────────▼───┐   ┌──────▼────────┐              │
    │ RecorderThread │   │ ExecutorPool  │   ┌──────────▼───────────┐
    │ queue → disk   │   │ N threads     │   │ ExitEngine (25ms)    │
    │ (NDJSON/Parquet)│  │ pre-warmed    │   │ SL/TP/TSL/time/EOD   │
    └────────────────┘   │ HTTPS session │   └──────────┬───────────┘
                         └───────┬───────┘              │
                                 └──────────┬───────────┘
                                   ┌────────▼─────────┐
                                   │  Kite REST facade │
                                   │  (rate-limited)   │
                                   └───────────────────┘
        ┌───────────────────────────────────────────────────────┐
        │  FastAPI (uvicorn, separate thread) — REST + /ws push  │
        └───────────────────────────────────────────────────────┘
```

**The single rule that governs the design:**
> The WebSocket callback thread must **never** perform I/O — no HTTP, no disk write, no logging call,
> no lock that a slow thread can hold. It stamps, enqueues, evaluates in-memory, and returns.

Everything else follows from that.

---

## 5. Daily lifecycle — feed connects **before** the pre-open, not at 09:14

> **Corrected from the first draft.** The feed must be live before 09:00 so the entire pre-open
> auction is recorded, exactly like rank-momentum does. All times below are config fields (§15).

| Time (config) | Phase | Actions |
|---|---|---|
| **08:45:00** | `PHASE_1` | Fresh TOTP auth. Instrument master (NFO+BFO). Expiry resolution (**stock roll rule**, §11.4). Nifty 50 list. Preflight: funds, kill-switch, trading-day, disk. |
| **08:55:00** | `FEED_LIVE` | **Connect the single WebSocket. Subscribe wave 1** (50 stocks + index spots; futures optional, off by default). **Recorder starts here** — everything from this instant is on disk. Capture `baseline` snapshot (previous-close state). Pre-warm HTTPS pool to `api.kite.trade`. |
| **09:00–09:08** | `PREOPEN` | NSE **equity** pre-open call auction. Every tick recorded: indicative price, indicative qty, order imbalance evolution. This is the highest-information window of the morning and today it is thrown away entirely. |
| **09:08–09:12** | `SETTLEMENT` | Auction matches. **Settlement snapshot** at `settlement_snapshot_time` (default 09:09:00, ±window). This is the **ranking basis** and the **ATM source**. |
| **09:09–09:13** | `ARMING` | Rank TG/TL from settlement. Resolve ATM per selected symbol. **Subscribe wave 2** — option chains, `MODE_FULL`. Capture **option reference premiums** (§5.2). |
| **09:14:00** | `FROZEN` | Manual-entry cutoff. Instrument set frozen. Verify depth is arriving on every armed strike; anything without a book is flagged, not silently traded. |
| **09:15:00.000** | `TRADING` | Triggers armed. First qualifying tick per instrument fires an entry intent. |
| 09:15 → exit | `MANAGING` | Exit engine live. Recorder still running. |
| `eod_time` | `EOD` | Square-off. Recorder runs until **last position closed + `post_exit_record_seconds`** (default 300s). |
| — | `IDLE` | Flush recorder, write session manifest, reset for tomorrow. |

### 5.1 Why two subscription waves instead of one

Option strikes cannot be chosen correctly until the settlement price is known — ATM computed from
*previous close* is simply wrong on any gap day (23 Jul: BANKNIFTY prev close 57 700 → opened 56 800;
a prev-close ATM would have armed strikes ~900 points off). Wave 2 at ~09:09 picks strikes from the
**actual** settlement price.

The cost of waiting is zero, which brings us to the finding that shapes this whole section:

### 5.2 ⚠️ Options do **not** trade in the pre-open — verified

NSE's pre-open call auction covers the **equity cash** segment, and (since 8 Dec 2025) the F&O
pre-open covers **futures only — current-month stock and index futures**.
**Stock options and index options are explicitly excluded.**

This is confirmed by your own production logs: on 22 Jul, INDIGO 5300 PE was captured at 09:14:50
showing `PreOpen: 117.85`, which is the *previous close* — its first real print was `158.0` at
09:15:01. Several DRREDDY strikes showed `OPEN 0` (never traded at all).

**Three consequences:**
1. There is **no option tick data to miss** before 09:15 — so subscribing all 50 option chains at
   08:55 would record ~1,000 silent instruments for 20 minutes. Wave 2 is leaner *and* more accurate.
2. The option "reference price" for the first-tick diff **is the previous close**, by definition.
   This is what the current algo already does; the plan makes it explicit rather than incidental.
3. **The correct ATM reference is the equity pre-open settlement price.** For a stock, the 09:08
   auction price *is* where it opens — nothing is more direct. Futures also have a pre-open now
   (Dec 2025), but a futures price is `spot + basis`, so using it for ATM means backing out the
   carry, which **adds** error. Futures are therefore a **fallback/cross-check only**, not the primary
   source, and are **off by default**:
   `universe.atm_source: settlement ★ | prev_close | futures_preopen`.

   The one case futures genuinely earn their place: an illiquid stock whose pre-open auction fails to
   discover a price (no matching → no settlement print). Then the future is the only live forward
   reference available before 09:15, and `atm_fallback_chain` (default
   `settlement → futures_preopen → prev_close`) uses it rather than falling all the way back to a
   stale previous close.

> **To be unambiguous: this system trades options only.** Futures and equity spot are *subscribed for
> reference and recording*; the executor has no code path that can place an order in either. The
> instrument whitelist is built solely from option chains.

### 5.3 Snapshot ladder (all individually toggleable)

| Snapshot | Default time | Purpose |
|---|---|---|
| `baseline` | 08:55:00 | Previous-close state of every instrument, before anything moves |
| `preopen_track` | 09:00→09:08 continuous | Full auction evolution (recorded, not sampled) |
| `settlement` | 09:09:00 | **Ranking basis + ATM source** |
| `option_reference` | 09:13:00 | Per-strike reference premium for the diff trigger |
| `market_open` | 09:15:00 (+2 s) | First-print capture for post-hoc analysis |
| `eod` | 15:30:15 | Session close state |

**Restart safety:** a mid-session restart re-enters at the correct phase, reloads the position book
from disk, and **reconciles against the broker** before arming anything (§14.3). If it restarts after
09:08 the settlement snapshot is reloaded from disk rather than recomputed (it cannot be recaptured).

---

## 6. Module layout

```
backend/
├── main.py                       # entry: crash-recovery loop → scheduler
├── config/
│   ├── config.json               # THE config (sections per §15), _doc strings
│   ├── credentials.json          # api_key/secret/totp (gitignored, 0600)
│   └── loader.py                 # load + pydantic validate + hot-reload + defaults
├── brokers/kite/                 # ◄── THE FACADE (state-free, one API area each)
│   ├── __init__.py               # module map docstring
│   ├── auth.py                   # TOTP login → access_token, cache, validity probe
│   ├── ticker.py                 # KiteTicker wrapper: connect, subscribe, modes, reconnect
│   ├── orders.py                 # place/modify/cancel/history + LPP parsing + retry policy
│   ├── portfolio.py              # positions, holdings, margins
│   ├── instruments.py            # master contract, expiry resolution, strike chains
│   ├── quotes.py                 # batched REST quote/ltp/ohlc
│   └── ratelimit.py              # token buckets: 10/s orders, 1/s quote, 400/min, 5000/day
├── engine/
│   ├── scheduler.py              # phase state machine (§5)
│   ├── universe.py               # TG/TL ranking + indices + manual list → instrument set
│   ├── feed.py                   # WS wiring, subscription plan, hot-path callback
│   ├── trigger.py                # first-positive-diff evaluation (pure, testable)
│   ├── executor.py               # intent queue → order lifecycle (marketable limit, IOC, retry)
│   ├── positions.py              # PositionBook: fills → positions, reconciliation
│   ├── exits.py                  # priority-ordered exit conditions (adapted from repo)
│   └── recorder.py               # queue → disk writer thread
├── api/
│   ├── server.py                 # FastAPI app + CORS + auth
│   ├── routes_*.py               # §16
│   └── ws_push.py                # /ws fan-out to frontend
└── data/YYYY-MM-DD/
    ├── ticks/*.ndjson(.zst)      # the recorder output
    ├── orders/*.jsonl            # every request + response + postback
    ├── positions.json            # position book snapshots
    ├── latency.jsonl             # per-trade latency breakdown
    └── manifest.json             # session summary + integrity counts
```

**Target size: ~3,500–4,500 LOC.** If a module grows past ~400 lines, it is doing too much.

---

## 7. The Zerodha facade

State-free modules; the engine passes what it needs. Mirrors the repo's proven layout.

### `brokers/kite/ticker.py` — the critical one
```
class KiteFeed:
    connect(instrument_plan: dict[mode, list[token]])   # ONE connection
    on_tick_batch: Callable[[list[dict], int], None]    # (ticks, recv_ns)
    on_order_event: Callable[[dict], None]
    resubscribe()                                        # after reconnect
    stats() -> {ticks, batches, last_tick_ns, gaps, reconnects}
```
- Wraps `KiteTicker(api_key, access_token)`; `connect(threaded=True)`.
- Callbacks wired: `on_ticks`, `on_order_update`, `on_connect`, `on_close`, `on_error`,
  `on_reconnect`, `on_noreconnect`.
- **Auto-reconnect on**, with `reconnect_max_tries` / `reconnect_max_delay` from config.
  On `on_reconnect` → **re-subscribe and re-apply modes** (Kite does not restore them for you),
  then emit a `FEED_GAP` event carrying the outage duration — the recorder writes a gap marker so
  post-hoc analysis never mistakes a gap for market silence.
- `on_noreconnect` → escalate: alert + halt new entries (existing positions still managed via REST).

### `brokers/kite/orders.py` — encodes the hard-won lessons
Carries forward everything already proven in production on the current algo:
- **Marketable limit pricing** from live best ask (entry) / best bid (exit) + configurable slippage.
- **LPP rejection parsing** — `allowed LPP limit (X)` → re-price inside band → retry (bounded).
- **No MARKET fallback for stock options** (Zerodha blocks it) — instead: IOC + repricing retry loop.
- Tick-size rounding: `ceil` for buys, `floor` for sells — never round the wrong way across the touch.
- Every call returns `{success, order_id, response, error, t_req_ns, t_ack_ns}` for the latency log.

---

## 8. Tick pipeline and latency design *(the core of this plan)*

### 8.1 Hot path — what happens inside `on_ticks`
Ordered, and deliberately tiny:
1. `recv_ns = time.perf_counter_ns()` **first statement** — before any parsing.
2. `recorder.put((ticks, recv_ns))` — `queue.SimpleQueue.put`, O(1), never blocks, unbounded.
3. If phase != `TRADING` → return. (Pre-09:15 ticks are recorded but not evaluated.)
4. For each tick: `state = armed.get(token)` — a **plain dict lookup, no lock**.
   - Not armed / already fired → continue.
   - Else `trigger.evaluate(tick, state)` — pure arithmetic on floats already in the tick.
5. On fire: build the intent (token, side, best_ask, qty, t_signal_ns) →
   `intent_q.put_nowait(intent)` → mark `state.fired = True` (single-writer, no lock needed) → continue.
6. Return. **Total target: < 50 µs per batch.**

**No logging in the hot path.** Log lines are emitted by the recorder/executor threads from the
enqueued events. (`logger.info` does formatting + I/O and is a classic hidden stall.)

### 8.2 Why this beats the repo's approach
| | rank_momentum | This plan |
|---|---|---|
| Tick → decision | tick → `_raw_feeds` → 1 ms poll loop → SSOT dicts → strategy loop | tick → decision, **inline** |
| Added latency | ≥1 ms + lock waits + full-universe rescan per cycle | ~µs |
| Lock contention | global `market_data` lock held while scanning all instruments | none in hot path |
| Determinism | poll jitter | event-exact |

### 8.3 Data structures (chosen for the hot path)
- `armed: dict[int, ArmedState]` — pre-built **before 09:15**, never resized during trading.
  `ArmedState` is a `__slots__` class: `ref_price, fired, symbol, lot_size, side, min_diff`.
- `intent_q`, `record_q`, `order_q`: `queue.SimpleQueue` (C-implemented, no timeout machinery).
- Position book: owned by **one** thread; other threads read an immutable snapshot published
  atomically (a new dict swapped into a single reference — CPython reference assignment is atomic).

### 8.4 Execution path (off the WS thread)
- `ExecutorPool`: `max(4, n_instruments)` pre-started threads, each holding a **pre-warmed
  `requests.Session`** (or the `kiteconnect` client) with the TLS handshake to `api.kite.trade`
  already completed during WARMUP.
- Handoff cost: queue put/get ≈ µs. It removes head-of-line blocking — with 8 symbols firing at
  09:15:00.9, an inline HTTP call would serialise them; the pool sends them in parallel.
- Rate limiter is checked in the executor, not the WS thread.

### 8.5 Latency instrumentation (mandatory, every trade)
Written to `latency.jsonl`, one record per entry:
```
exchange_ts, recv_ns, signal_ns, intent_dequeued_ns, order_req_ns, order_ack_ns,
first_postback_ns, fill_ns
→ derived: feed_lag_ms, tick_to_signal_us, queue_wait_us, signal_to_req_ms,
           req_to_ack_ms, ack_to_fill_ms, total_tick_to_fill_ms
```
- `feed_lag_ms = recv_ns − exchange_timestamp` — **this is the one number that finally answers
  "why is the first tick ~800 ms after open?"** It separates *exchange/broker dissemination delay*
  (nothing we can fix in code) from *our processing* (which we can).
- Clock discipline: run `chrony`/`systemd-timesyncd` against a low-stratum NTP source; record clock
  offset in the manifest so cross-machine comparisons stay honest.

### 8.6 Latency measures worth taking (and their honest ceilings)
| Measure | Expected gain | Honest note |
|---|---|---|
| Single WS, subscribed 08:55 (20 min before the bell) | removes all connect/subscribe races at the open | Today's per-symbol processes each connect separately |
| Inline evaluation (no poll loop) | −1 to −3 ms vs repo design | Real, but small in absolute terms |
| Pre-warmed TLS session | −20 to −80 ms on the **first** order of the day | Genuinely significant at 09:15:00 |
| Executor pool (no head-of-line block) | −100 ms+ when many symbols fire together | Scales with instrument count |
| EC2 in `ap-south-1` (Mumbai) | already the case (your existing algo host) | No further gain available |
| Placement bandwidth | 10 orders/s cap | Hard broker limit; batch accordingly |

> **What this cannot fix:** the ~750–800 ms between 09:15:00.000 and the first tick. That is the
> exchange/broker publishing the first post-open trade. No client-side change moves it. The
> `feed_lag_ms` metric exists to *prove* that split rather than let it be re-litigated each week.

---

## 9. Subscription plan — two waves, mode-budgeted

### Wave 1 — 08:55, before the pre-open: **all 50 stocks, equity only**

| Class | Count | Mode | Rationale |
|---|---|---|---|
| Nifty 50 stocks | **50** | `quote` (44 B) | Records the whole pre-open auction; supplies the ranking |
| Index spots (NIFTY, BANKNIFTY, SENSEX) | 3 | `quote` | Spot reference for ATM |
| Current-month futures (**opt-in, off by default**) | ≤53 | `quote` | Reference/fallback only (§5.2). Never traded. |
| **Wave 1 total** | **~53** | | No options at all — they are silent until 09:15 |

### Wave 2 — ~09:09, after settlement: **rank, shortlist, then subscribe options**

1. Re-read all 50 stocks at the settlement price → **rank by % change**.
2. Take `top_n_gainers` + `top_n_losers`, **plus `candidate_buffer` extra ranks on each side**
   (default **5**) — cheap insurance against the ranking shuffling between 09:09 and the bell.
3. Subscribe **option chains for the shortlist only**, plus the enabled index chains.

| Class | Count | Mode |
|---|---|---|
| Shortlisted stock option chains | `(n_gainers + n_losers + 2×buffer) × 2 × strikes_per_side` | **`full`** (184 B — depth required) |
| Index option chains (NIFTY, BANKNIFTY, SENSEX) | `3 × 2 × strikes_per_side` | **`full`** |

**Worked example** — `n_gainers=5, n_losers=5, buffer=5, strikes_per_side=4`:
`(5+5+10) × 2 × 4 = 160` stock options + `3 × 2 × 4 = 24` index options = **184**.
Session total **≈ 237 instruments — about 8 % of Kite's 3,000 cap.**

> **The buffer is subscribed, not traded.** Only the final top-N fire entries. The extra ranks exist
> so that if the ranking shifts (or `rerank_on_open` is enabled) the chains are already live and
> armed — subscribing a chain at 09:15 would be far too late.

- `subscription_soft_cap` (default 2,400): if the projection exceeds it, **fail fast in Phase 1** with
  the arithmetic in the error — never silently truncate.
- `subscribe_all_chains_early` (default `false`): escape hatch to force all 50 chains into wave 1.

---

## 10. Universe selection

```
universe = manual_instruments                       (added before 09:14, always included)
         + top_n_gainers   (from Nifty 50, if enabled)
         + top_n_losers    (from Nifty 50, if enabled)
         + enabled_indices (NIFTY / BANKNIFTY / FINNIFTY / SENSEX)
```
- `top_n_gainers` / `top_n_losers`: integer N each, independently toggleable.
- Ranking basis is configurable: `prev_close` (default) or `preopen_price`, computed from the
  09:09 settlement snapshot, and optionally re-ranked at 09:15:00 from first ticks
  (`rerank_on_open`, default `false` — re-ranking costs time at the exact moment we want to fire).
- Per-symbol overrides: `lots`, `strike_offset`, `moneyness`, `enabled`.
- **Segment routing:** SENSEX → `BFO`; NIFTY/BANKNIFTY/FINNIFTY/stocks → `NFO`. Handled in the
  instruments module, not scattered through the engine.

---

## 11. Entry logic

### 11.1 The trigger (unchanged from the existing, proven logic)
For each armed instrument, on each tick:
```
ref   = reference price captured pre-open (per instrument)
price = tick.last_price   (or best_ask, per entry_price_source)
diff  = price − ref
fire when diff > min_diff   (default 0.0 → "first positive tick")
```
- One fire per instrument per session (`fired` latch).
- Gates, each toggleable: `min_diff`, `min_premium` / `max_premium`, `require_depth`
  (skip if best_ask is 0 — an empty book cannot be priced marketably),
  `fire_after_seconds` (default 1s past 09:15 — avoids brokers treating 09:15:00.000 as AMO,
  a behaviour the Obsidian system already guards against), and `deadline_seconds` (default 180 —
  prevents a mid-day restart from firing stale entries).

### 11.2 Direction
Existing logic retained: the **triggering strike itself** is bought (CE if a call fired, PE if a put
fired). Configurable `strike_reference` (ATM/ITM/OTM) + `strike_offset` decide which strikes are
*armed*; the one that fires first is the one traded.

### 11.3 Sizing
`quantity = lots × lot_size` (from instrument master, never hardcoded). Per-symbol `lots` override.
Pre-trade margin check via `portfolio.margins()` at Phase 1; per-order notional cap
(`max_notional_per_trade`) and session cap (`max_total_notional`) enforced in the executor.

### 11.4 Expiry selection — **mandatory rule**
Stock F&O is physically settled and Zerodha blocks fresh MIS buys in the **last two trading days**
before expiry.
```
if symbol is INDEX      → nearest expiry (cash-settled, always safe)
if symbol is STOCK      → if busday_count(today, nearest_expiry) <= 1 → use NEXT expiry
                          else nearest
```
Trading-day count (not calendar days) so weekends don't break it. **Known gap:** exchange holidays
inside the final window are not accounted for by `busday_count` alone — Phase 1 should cross-check
against the market-calendar API and log a warning if they disagree.

---

## 12. Order execution

### 12.1 Entry
1. **Price:** `entry_price_source` ∈ {`ask` (default), `ltp`} → `limit = round_up_to_tick(source × (1 + entry_slippage_pct/100))`.
   Marketable-by-construction: a buy limit fills **at the resting ask (≤ our price)**, so a wider
   buffer buys fill-certainty, not a worse price.
2. **Validity:** `IOC` (default) — fill what's available now, no resting order above the market.
   Pairs with the retry loop. `DAY` available for calmer instruments.
3. **Product:** configurable. Note MIS is blocked for stock options in the last two expiry days —
   §11.4 avoids that window entirely; also expose `NRML` for the stock-option path.
4. **Order type: fully configurable per class**, with a fallback ladder.

| Setting | Default | Behaviour |
|---|---|---|
| `order_type.stock_options` | `LIMIT` ★ | **MARKET is blocked by Zerodha on stock options** (§2). Setting `MARKET` here is allowed but will be rejected by the broker — the fallback then rescues it. |
| `order_type.index_options` | `LIMIT` ★ | Indices are cash-settled and accept MARKET; set `"MARKET"` if you want it. |
| `order_fallback.to` | `MARKETABLE_LIMIT` ★ | On `ORDER_TYPE_REJECT` / `LPP_REJECT` / `NO_DEPTH`, re-send as a marketable limit (ask + slippage, clamped inside the LPP band). `MARKET` and `NONE` also available. |

**Why `MARKETABLE_LIMIT` is the default fallback rather than `MARKET`:** a marketable limit fills at
the resting ask (≤ our price) *and* carries a price ceiling, so it behaves like a market order without
the unbounded-fill risk — and it works on stock options, where MARKET does not.

### 12.2 Unfilled handling — two independent, configurable mechanisms
| Mechanism | Config | Behaviour |
|---|---|---|
| **Re-price retry** (primary) | `entry_retry.enabled`, `max_attempts` (3), `interval_ms` (300) | On IOC partial/no-fill: read **fresh best ask from the in-memory feed** (not a REST quote — zero added latency), re-price, place again for the residual qty. |
| **Limit modification** (secondary) | `limit_modification.enabled`, `max_modifications` (3), `step_pct` | For resting DAY orders: modify price toward the touch. Hard-capped at Kite's 25-modify ceiling, then cancel+replace. |

> Design note from the user's own observation: *by the time you modify, the book has already moved.*
> Hence **priority order is (a) price it marketable at placement, (b) IOC re-place, (c) modify** —
> modification is the last resort, never the primary mechanism.

### 12.3 LPP rejections
Parse `allowed LPP limit (X)` from `status_message` → re-price to the highest tick **inside** the band
(from live LTP × `lpp_safety_factor`, default 0.99 of the band edge) → retry, bounded by
`lpp_retries` (default 3). Never exceed the band; floor-to-tick so rounding can't push back over.

### 12.4 Exit orders
`exit_price_source` ∈ {`bid` (default), `ltp`}; `limit = round_down_to_tick(bid × (1 − exit_slippage_pct/100))`.
Same retry ladder. EOD square-off uses a wider slippage (`eod_slippage_pct`) because certainty of exit
beats price at 15:28.

---

## 13. Exit engine

Priority-ordered, first trigger wins (structure adapted from the repo's `exit_condition.py`, which is
already well-shaped). **Every condition independently toggleable with its own values.**

| Priority | Condition | Config | Notes |
|---|---|---|---|
| 1 | `MANUAL_BROKER` | `manual_detection.enabled` | Position closed outside the system (Kite app/web) — §14.2 |
| 2 | `MANUAL_API` | always on | Operator hit `/positions/{id}/exit` or `/control/exit_all` |
| 3 | `STOP_LOSS` | `enabled`, `percentage` (negative) | Hard floor |
| 4 | `TARGET` | `enabled`, `percentage` | Skipped when trailing-target is armed |
| 5 | `TRAILING_TARGET` | `enabled`, `activation_pct`, `extend_distance_pct`, `max_extension_pct` | Level only ratchets up |
| 6 | `TRAILING_SL` | `enabled`, `activation_pct`, `trail_distance_pct` | Arms at activation; level only ratchets up |
| 7 | `TIME_EXIT` | `enabled`, `holding_seconds` | |
| 8 | `EOD_SQUAREOFF` | `enabled`, `square_off_time` | Default 15:28:00 |

- Evaluation cadence: `monitor_interval_ms` (default **25 ms**), driven off the **in-memory last tick**
  — no REST polling for PnL.
- PnL basis configurable: `ltp` (default) or `bid` (conservative — what you'd actually get out at).
- **Idempotent exits:** an `exiting` latch per position prevents double-sends when SL and TSL trip in
  the same tick. Exit order IDs are recorded before send.

---

## 14. Position tracking & reconciliation *(the "real-time portfolio" requirement, done honestly)*

### 14.1 Primary: order-update WebSocket
`on_order_update` delivers every order state change on the **same connection** — no second WS, no
polling. Fills update the `PositionBook` within milliseconds:
`COMPLETE` → set `filled_qty`, `average_price`, `fill_ns` → position becomes `ACTIVE`.
This is the fastest and most accurate fill signal available, and it replaces today's
`order_history()` polling loop entirely.

### 14.2 Secondary: broker positions poll (manual-close detection)
`GET /portfolio/positions` every `broker_sync.poll_interval_seconds` (default 2s, live mode only):
- Broker qty **0** while our book says ACTIVE **and** we hold no exit order → operator closed it
  manually → raise `MANUAL_BROKER`, close the position locally, stop managing it.
- Quantity/average-price drift beyond tolerance → log a **reconciliation warning** with both values.

> This two-source design is deliberate: the order stream gives *speed*, the positions poll gives
> *truth*. Neither alone is sufficient — the order stream can miss events across a reconnect gap,
> and the poll is too slow to drive exits.

### 14.3 Restart reconciliation
On startup mid-session: load the position book from disk → fetch broker positions **and** the day's
orders → three-way match. Any position present at the broker but not in our book is adopted as
`ADOPTED_UNMANAGED` (tracked and exitable, but never counted as a strategy entry). **No entries are
armed until reconciliation completes cleanly.**

---

## 15. Configuration schema

Single `config.json`, sectioned, every field with a `_doc` string, validated by pydantic on load
(fail fast with the offending path). Hot-reloadable for non-structural fields; structural changes
(instrument set, subscription plan) apply next session.

**Every previously "open decision" is now a config field with a chosen default — nothing blocks the
build; you change a value, not code.** Defaults marked ★ are the ones I'd ship.

```jsonc
system      : timezone "Asia/Kolkata", data_dir, log_level, retention_days 7

schedule    : phase1_time            "08:45:00"
              feed_connect_time      "08:55:00"   // ★ before pre-open (§5)
              preopen_start          "09:00:00"
              settlement_snapshot    "09:09:00"   // ranking basis + ATM source
              wave2_subscribe_time   "09:09:30"
              option_reference_time  "09:13:00"
              manual_cutoff          "09:14:00"   // manual entries rejected after this
              trading_start          "09:15:00"
              eod_time               "15:28:00"
              auto_continue_daily    true

broker      : api_key, exchange routing (NFO | BFO auto by symbol)
              product : { stock_options "NRML" ★,   // avoids the MIS physical-settlement block
                          index_options "MIS"  ★ }  // cash-settled, cheaper margin
              rate_limits { orders_per_sec 10, quote_per_sec 1,
                            per_minute 400, daily_cap 5000 }
              timeouts { order_ms 3000, quote_ms 2000 }

trading_mode: mode "paper" ★        // → place_actual_orders / require_confirmation derived

universe    : top_n_gainers 5, top_n_losers 5          // set 0 to disable either side
              candidate_buffer 5 ★                      // extra ranks each side: subscribed, not traded
              enabled true                              // master switch for TG/TL
              ranking_basis "settlement" ★              // settlement | prev_close | preopen
              atm_source    "settlement" ★              // settlement | prev_close | futures_preopen
              atm_fallback_chain ["settlement","futures_preopen","prev_close"] ★
              rerank_on_open false ★                    // true = more accurate, costs time at the bell
              subscribe_futures_preopen false ★         // reference/fallback ONLY — never traded
              indices : { NIFTY     {enabled true,  lots 1, strike_offset 2},
                          BANKNIFTY {enabled true,  lots 1, strike_offset 2},
                          SENSEX    {enabled true,  lots 1, strike_offset 2},
                          FINNIFTY  {enabled false, lots 1, strike_offset 2} }  // off ★
              manual_instruments []                     // added via API before manual_cutoff
              per_symbol_overrides {}                   // lots / offset / enabled per symbol

instruments : strike_reference "ITM" | "ATM" | "OTM" ★ITM, strike_offset 2,
              strikes_per_side 4, subscription_soft_cap 2400,
              subscribe_all_chains_early false,
              expiry_roll { enabled true, buffer_trading_days 1,
                            applies_to "stocks_only" ★ }   // indices always nearest

snapshots   : baseline{on,time}, preopen_track{on,from,to}, settlement{on,time,window_s},
              option_reference{on,time,source "prev_close" ★}, market_open{on,time,window_s},
              eod{on,time}

entry       : min_diff 0.0 ★                       // 0 = "first positive tick"
              fire_after_seconds 1 ★               // avoids 09:15:00.000 being treated as AMO
              deadline_seconds 180 ★               // no stale fires after a mid-day restart
              require_depth true ★                 // skip strikes with an empty book
              min_premium 0, max_premium 0         // 0 = disabled
              entry_price_source "ask" ★           // ask | ltp
              entry_slippage_pct 1.5 ★             // Obsidian's number; current algo uses 3.0
              entry_validity "IOC" ★               // IOC | DAY
              order_type : { stock_options "LIMIT" ★,   // MARKET is blocked on stock options (§2)
                             index_options "LIMIT" ★ }  // set "MARKET" if you want it
              order_fallback : { enabled true ★,
                                 on ["ORDER_TYPE_REJECT","LPP_REJECT","NO_DEPTH"],
                                 to "MARKETABLE_LIMIT" ★ }  // MARKETABLE_LIMIT | MARKET | NONE
              lots_default 1, max_notional_per_trade, max_total_notional
              entry_retry        { enabled true, max_attempts 3, interval_ms 300 }
              limit_modification { enabled true, max_modifications 3, step_pct 1.0 }
              lpp                { retries 3, safety_factor 0.99 }

exits       : stop_loss       { enabled true,  percentage -5.0 }
              target          { enabled true,  percentage 30.0 }
              trailing_stop   { enabled true,  activation_pct 7.0, trail_distance_pct 3.0 }
              trailing_target { enabled false, activation_pct 15.0,
                                extend_distance_pct 5.0, max_extension_pct 50.0 }
              time_exit       { enabled false, holding_seconds 1200 }
              eod_exit        { enabled true,  square_off_time "15:28:00" }
              manual_detection{ enabled true }     // broker-side close detection (§14.2)
              monitor_interval_ms 25, pnl_basis "ltp" ★ (ltp|bid),
              exit_price_source "bid" ★, exit_slippage_pct 1.0, eod_slippage_pct 3.0

positions   : max_concurrent 10, max_per_symbol 1 ★,
              broker_sync { enabled true, poll_interval_seconds 2 }

recorder    : enabled true, format "ndjson" ★ (ndjson|parquet), compression "zstd" ★,
              record_depth_levels 5, flush_interval_ms 500,
              post_exit_record_seconds 300, retention_days 7 ★,
              max_disk_mb 20000, on_disk_full "stop_recording" ★ (|halt_trading),
              upload { enabled false, target "s3://…", after "eod" }

alerts      : telegram { enabled false, bot_token "", chat_id "" },
              email    { enabled false, smtp… },
              on : ["PHASE_1_FAIL","FEED_LOSS","KILL_SWITCH","ORDER_REJECT","DISK_FULL"] ★

api         : host "0.0.0.0", port 8080, cors_origins ["https://<your>.vercel.app"],
              auth_token, ws_push_interval_ms 250

paper       : starting_capital 1000000, simulate_charges true, fill_model "touch" ★
```

---

## 16. API surface (FastAPI)

CORS locked to the Vercel origin(s) + localhost. Bearer-token auth on every mutating route.
All responses < 50 ms (served from memory, never from a broker call in the request path).

### Read
| Method | Route | Returns |
|---|---|---|
| GET | `/health` | liveness, uptime, phase, feed status |
| GET | `/status` | phase, market state, feed stats (ticks/s, last_tick_age, reconnects), auth validity |
| GET | `/universe` | resolved instrument set + subscription plan + modes |
| GET | `/market/snapshot` | last tick per instrument (LTP, bid/ask, diff-vs-ref, armed/fired) |
| GET | `/positions` | live book: entry, LTP, PnL ₹/%, TSL level, armed exits |
| GET | `/positions/closed` | today's closed trades + exit trigger |
| GET | `/orders` | every order today: request, ack, postbacks, final state |
| GET | `/latency` | per-trade latency breakdown (§8.5) |
| GET | `/config` | full config + `_doc` strings (frontend renders help from this) |
| GET | `/recorder/stats` | ticks recorded, bytes, queue depth, drops, gaps |
| GET | `/reports/{date}/...` | session manifest, trades, latency, tick-file index |
| GET | `/logs` | tail with level/tag filters |

### Write
| Method | Route | Action |
|---|---|---|
| POST | `/config` | patch config (validated; rejects structural changes mid-session) |
| POST | `/universe/manual` | add/remove manual instrument — **rejected after 09:14:00** |
| POST | `/control/start` \| `/stop` \| `/restart` | lifecycle |
| POST | `/control/arm` \| `/disarm` | enable/disable new entries without stopping the service |
| POST | `/positions/{id}/exit` | manual exit one position |
| POST | `/control/exit_all` | exit everything |
| POST | `/control/kill_switch` | **exit all + halt for the day** (irreversible for the session) |
| POST | `/control/panic_flatten` | exit-all ignoring slippage caps (emergency) |

### Push
`WS /ws` — topic-based fan-out (`status`, `market`, `positions`, `orders`, `logs`), throttled to
`ws_push_interval_ms` (default 250 ms) with **diff-only payloads**. The frontend subscribes to topics
and stops polling. Push runs on its own thread; a slow/stalled client is dropped, never back-pressures
the engine.

---

## 17. Safety and accountability

**Pre-trade (Phase 1, all must pass or `PHASE_1_FAIL` → no trading):**
auth valid · trading day confirmed · funds ≥ required margin · kill-switch off ·
instrument master fresh · expiry roll resolved · subscription count within cap · disk space free.

**In-flight guards:** rate limiter (10/s, 400/min, 5000/day) · `max_concurrent` positions ·
`max_per_symbol` (default 1, no pyramiding) · notional caps · `deadline_seconds` (no stale fires) ·
duplicate-fire latch · idempotent exits.

**Audit trail — every order records:** intent (tick that caused it, ref price, computed limit,
reason), request payload, ack, every postback, final state, and the full latency chain. A trade can be
reconstructed end-to-end from `orders/*.jsonl` + `ticks/*.ndjson` with no guesswork.

**Fail-safe defaults:** paper mode; entries disarmed until Phase 1 passes; on any unhandled exception
in the entry path → disarm entries but **keep managing open positions**; on feed loss → halt new
entries, manage existing via REST.

---

## 18. Failure modes and mitigations

| Failure | Detection | Mitigation |
|---|---|---|
| WS disconnect at 09:15 | `on_close` / heartbeat gap | Auto-reconnect + **re-subscribe + re-apply modes**; `FEED_GAP` marker in recorder; entries disarmed during gap |
| Tick callback slowed by a slow consumer | queue depth metric | Unbounded `SimpleQueue` + drop-to-disk-only mode; recorder never blocks the WS thread |
| Recorder disk full | free-space check each flush | `on_disk_full` policy: stop recording (default) or halt trading; alert either way |
| Order rejected (LPP) | `status_message` parse | Re-price inside band, bounded retries (§12.3) |
| Order rejected (margin/RMS) | rejection reason | No retry; log, alert, mark `FAILED`, do not re-fire |
| MARKET blocked on stock option | rejection | Never sent (LIMIT-only by design) |
| Broker API 5xx / timeout at open | facade error | Bounded retry with jitter; IOC prevents duplicate resting orders |
| Duplicate process launched | PID lockfile + broker `tag` check | Second instance refuses to start (**this has already happened twice in production — SENSEX was double-launched on 23 and 24 Jul, once resulting in a doubled position**) |
| Clock drift | NTP offset in manifest | Alert if \|offset\| > 50 ms |
| Restart mid-session | phase + book reload | Three-way reconciliation before arming (§14.3) |

---

## 19. Build phases

| Phase | Deliverable | Exit criteria |
|---|---|---|
| **P0** | Skeleton: config loader, logging, `main.py`, FastAPI `/health` `/status` | Service starts, config validates, endpoints respond |
| **P1** | **Kite facade** — auth, instruments, quotes, ratelimit | TOTP login works; master contract parses; expiry roll unit-tested |
| **P2** | **Feed + Recorder** (no trading) | Connect **08:55**, record the **entire pre-open auction** + open; verify zero drops, measure `feed_lag_ms`, confirm empirically that options are silent pre-09:15 |
| **P3** | Universe + trigger (paper) | TG/TL + indices resolve correctly; triggers fire in paper mode with full latency logs |
| **P4** | Executor + order lifecycle (paper → **1 lot live**) | Marketable limit, IOC retry, LPP retry all exercised; latency chain complete |
| **P5** | Position book + order-update WS + reconciliation | Fills land via `on_order_update`; manual close detected; restart reconciles |
| **P6** | Exit engine | All 8 conditions unit-tested; live 1-lot validation |
| **P7** | Full API + `/ws` push + reports | Frontend can drive the system end-to-end |
| **P8** | Hardening | Kill switch, caps, alerts, 5-day paper soak, then scale lots |

> **P2 is deliberately first and standalone.** The recorder answers the questions currently being
> guessed at — and it can run **safely alongside the existing algo** (read-only feed, separate API
> key session) to start gathering evidence before a single order routes through the new system.

---

## 20. Testing and verification

- **Unit (offline, no broker):** trigger arithmetic; tick rounding (ceil-buy/floor-sell); LPP parsing;
  expiry roll across weekends/month-ends; every exit condition + TSL ratchet; universe ranking.
- **Replay harness:** feed recorded `ticks/*.ndjson` back through `feed → trigger → executor`
  with a mock broker. **This is the payoff of the recorder** — every future strategy or latency change
  is validated against real market-open data instead of a live morning experiment.
- **Paper soak:** ≥5 sessions, full instrument set, verifying no drops, no double-fires, no leaks.
- **Live canary:** 1 lot, 1 symbol, several sessions; compare measured latency vs paper.
- **Chaos:** kill the WS mid-session; kill the process mid-position; simulate disk-full, LPP reject,
  broker 5xx, clock skew.

**Definition of done for the core claim:** a session where `latency.jsonl` shows
`tick_to_signal_us < 100` and `signal_to_req_ms < 20` for every entry, with `feed_lag_ms` reported
separately — so exchange delay and system delay are never conflated again.

---

## 21. Staying minimal — the size budget

Rank-momentum is ~13,000 LOC of backend because it carries a multi-strategy registry, 11 direction
modes, 3 frontends, a snapshot service, an artifacts/reports subsystem and a global SSOT with six
locks. **We need none of that.** This is one strategy with one trigger.

**Hard budget — if a module exceeds its line count, it is doing too much and gets split or cut:**

| Module | Budget | Notes |
|---|---:|---|
| `brokers/kite/*` (7 files) | 900 | The facade. Thin wrappers, no business logic. |
| `engine/feed.py` | 250 | WS wiring + hot path. The hot path itself is ~40 lines. |
| `engine/recorder.py` | 200 | Queue → disk. Deliberately dumb. |
| `engine/universe.py` | 250 | Ranking + strike resolution + wave planning |
| `engine/trigger.py` | 120 | Pure functions. Fully unit-testable offline. |
| `engine/executor.py` | 350 | Order lifecycle incl. IOC retry + LPP |
| `engine/positions.py` | 300 | Book + reconciliation |
| `engine/exits.py` | 250 | 8 conditions, adapted from the repo |
| `engine/scheduler.py` | 200 | Phase state machine |
| `config/loader.py` | 250 | Load + pydantic validate + hot-reload |
| `api/*` | 600 | REST + `/ws` push |
| `main.py` | 100 | Crash-recovery loop |
| **Total** | **≈ 3,800** | **~29 % of rank-momentum** |

**Rules that keep it there:**
1. **One strategy.** No registry, no per-strategy config trees, no plugin loading.
2. **No global SSOT.** Narrow structures owned by one thread each (§8.3). This alone removes a large
   class of lock/serialisation code.
3. **Config over branches.** Every behavioural question is a config field (§15) — not a new module,
   subclass or strategy variant.
4. **Recorder replaces the snapshot service.** Snapshots become *markers in the tick stream*, not five
   separate CSV writers with their own scheduling.
5. **The frontend is a client, not a component.** Backend ships REST + `/ws`; no templates, no build
   step, no server-side rendering. The GUI is developed and deployed independently against these APIs.
6. **No abstraction without a second caller.** Interfaces are introduced when something actually needs
   swapping, not in anticipation.

**Deployment note (not a config):** P2 (feed + recorder) is read-only and can safely run alongside the
current algo on your existing algo host to start banking tick data immediately. The live trading system should
get its own box, or at minimum its own API-key session, so a restart of one never disturbs the other.

---

## Appendix A — Recorded tick schema

One record per tick (NDJSON; Parquet optional for analysis):
```
recv_ns              int64   local receipt, perf_counter_ns (hot-path first statement)
recv_wall_us         int64   wall clock, for cross-system correlation
batch_seq            int64   monotonic batch counter (detects loss)
batch_size           int16   ticks in this callback invocation
instrument_token     int64
tradingsymbol        str     (denormalised once at subscribe time)
exchange_timestamp   int64   ← feed_lag_ms = recv_wall_us − exchange_timestamp
last_trade_time      int64
last_price           float
last_traded_quantity int
average_traded_price float
volume_traded        int
total_buy_quantity   int
total_sell_quantity  int
oi, oi_day_high, oi_day_low
ohlc                 {open, high, low, close}
depth.buy[0..4]      {price, quantity, orders}      ← configurable levels
depth.sell[0..4]     {price, quantity, orders}
```
Plus out-of-band event records in the same stream: `SUBSCRIBED`, `FEED_GAP{start,end,ms}`,
`ARMED`, `TRIGGER_FIRED{token,diff,ref}`, `ORDER_SENT`, `ORDER_POSTBACK`, `POSITION_EXIT` — so a
single chronological file tells the complete story of the session.

## Appendix B — Sources

- [Kite Connect — WebSocket streaming](https://kite.trade/docs/connect/v3/websocket/) — connection/instrument limits, modes, depth, order-update message shape
- [Kite Connect — Orders](https://kite.trade/docs/connect/v3/orders/) — variety/product/validity/market_protection, status values
- [Kite Connect — Exceptions & rate limits](https://kite.trade/docs/connect/v3/exceptions/) — 10/s orders, 1/s quote, 5000/day, 25 modifications
- [Zerodha — market orders on option contracts](https://support.zerodha.com/category/trading-and-markets/trading-faqs/f-otrading/articles/market-orders-monthly-options) — MARKET blocked on illiquid stock options
- [Zerodha — physical settlement policy](https://support.zerodha.com/category/trading-and-markets/trading-faqs/f-otrading/articles/policy-on-physical-settlement) — compulsory delivery, expiry-window restrictions
- [NSE — equity pre-open session](https://www.nseindia.com/static/products-services/equity-market-pre-open) — 09:00–09:08 order collection, matching, no new orders 09:08→09:15
- [NSE pre-open for equity derivatives (from 8 Dec 2025)](https://stocko.in/nse-introduces-pre-open-session-for-equity-derivatives-fo/) — **futures only (current-month stock + index); stock and index options excluded** — the basis for §5.2
- Local: `e:\Projects\rank_mementum_setup_vijay-main\Backend\` (architecture reference, ~13k LOC reviewed)
- Production: `~/Vijay915/algo_code.py` on your existing algo host (first-tick logic, LPP retry, expiry roll, marketable-ask pricing)
