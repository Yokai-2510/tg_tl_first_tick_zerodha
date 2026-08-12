# First-Tick Open-Drive System — System Design & Interface Specification

**Companion to:** `FIRSTTICK_SYSTEM_IMPLEMENTATION_PLAN.md` (rationale, phasing, verified platform facts).
This document is the **contract**: architecture, module boundaries, data schemas, broker layer,
frontend APIs, and connection procedure.

**Deliberately excluded:** UI/UX, visual design, component layout, styling. The frontend is treated
purely as an **API client**.

| | |
|---|---|
| **Broker** | Zerodha Kite Connect v3 (`kiteconnect` Python SDK) |
| **Traded instruments** | **Options only** (index + stock). Equity/futures are subscribed for reference, never traded. |
| **Strategy** | Single — first tick with a positive premium difference vs reference |
| **Backend** | Python 3.11+, FastAPI + uvicorn, single process |
| **Frontend** | Separate SPA (Vercel/GitHub Pages), consumes REST + WebSocket |
| **Size budget** | ≈3,800 LOC backend |

---

## 0. Document map

| § | Section | Answers |
|---|---|---|
| 1 | Scope & deliverables | What gets built |
| 2 | Design principles | Rules every decision follows |
| 3 | System design | Processes, threads, data flow, state machine |
| 4 | **Modular design** | Every module: responsibility, interface, ownership, dependencies |
| 5 | **Data schemas** | Every persisted and in-flight object |
| 6 | **Broker layer** | The Kite facade |
| 7 | **Frontend REST API** | Every endpoint |
| 8 | **Frontend WebSocket API** | Push protocol |
| 9 | **Connecting the frontend** | Topology, TLS, auth, client model |
| 10 | Non-functional | Security, observability, limits |
| 11 | Out of scope | |

---

## 1. Scope & deliverables

### Backend deliverables
1. **Engine** — scheduler, universe/ranking, feed, trigger, executor, positions, exits.
2. **Tick recorder** — full-depth capture from 08:55 until last position + 300 s.
3. **Kite facade** — auth, ticker, orders, portfolio, instruments, quotes, rate limiting.
4. **REST API** — read + control (§7).
5. **WebSocket push API** — live state to the frontend (§8).
6. **Config system** — one JSON file, validated, hot-reloadable (§5.2).
7. **Audit artifacts** — orders, positions, latency, events, session manifest.

### Not built here
Frontend application, UI/UX, charting, backtester, multi-strategy framework, mobile app.

---

## 2. Design principles

| # | Principle | Consequence |
|---|---|---|
| P1 | **The WS callback thread never does I/O** | No HTTP, disk, or logging in `on_ticks`. Stamp → enqueue → evaluate in memory → return. |
| P2 | **One process, one WebSocket** | Market data *and* order updates share one Kite connection. |
| P3 | **Config over code branches** | Every behavioural question is a config field, not a subclass. |
| P4 | **Single-writer state** | Each mutable structure has exactly one owning thread; readers get atomic snapshots. |
| P5 | **Record everything, decide once** | The recorder is the source of truth for post-hoc analysis; no re-derivation from logs. |
| P6 | **Fail fast before the bell, fail safe after it** | Preflight blocks the session; runtime errors disarm entries but keep managing open positions. |
| P7 | **No abstraction without a second caller** | Interfaces appear when something actually needs swapping. |
| P8 | **The frontend is a client, not a component** | Backend ships JSON + WS. No templates, no SSR, no build coupling. |

---

## 3. System design

### 3.1 Process & thread model

Single OS process. Threads:

| Thread | Count | Owns | Blocking allowed? |
|---|---|---|---|
| `MainThread` | 1 | Scheduler / phase machine | Yes (sleeps) |
| `KiteTicker-IO` | 1 (SDK-managed) | WS socket; invokes `on_ticks` / `on_order_update` | **NEVER** |
| `Recorder` | 1 | Tick file handles | Yes (disk) |
| `Executor` | 4–8 | Order placement, pre-warmed HTTPS sessions | Yes (HTTP) |
| `PositionMonitor` | 1 | Position book, exit evaluation (25 ms loop) | Short only |
| `BrokerSync` | 1 | Positions REST poll (2 s) | Yes |
| `APIServer` | 1 + uvicorn workers | FastAPI | Yes |
| `WSPush` | 1 | Frontend fan-out (250 ms) | Yes |

### 3.2 Data flow — three paths

```
                        KiteTicker (ONE connection)
                                │
        ┌───────────────────────┼────────────────────────┐
        │ on_ticks              │                        │ on_order_update
        ▼                       ▼                        ▼
┌───────────────┐      ┌────────────────┐       ┌────────────────┐
│  HOT PATH     │      │   record_q     │       │    order_q     │
│  (in callback)│─────►│  SimpleQueue   │       │  SimpleQueue   │
│  <50 µs       │      └───────┬────────┘       └───────┬────────┘
│               │              ▼                        ▼
│ 1 stamp ns    │      ┌────────────────┐       ┌────────────────┐
│ 2 enqueue rec │      │ Recorder thread│       │ PositionBook   │
│ 3 armed? eval │      │  → NDJSON/zstd │       │  fills → posn  │
│ 4 fire→intent │      └────────────────┘       └───────┬────────┘
└──────┬────────┘                                       │
       ▼                                                ▼
┌────────────────┐                             ┌──────────────────┐
│   intent_q     │                             │ PositionMonitor  │
└──────┬─────────┘                             │  exits @ 25 ms   │
       ▼                                       └────────┬─────────┘
┌────────────────┐                                      │
│ Executor pool  │◄─────────────────────────────────────┘
│ pre-warmed TLS │            (exit orders)
└──────┬─────────┘
       ▼
┌────────────────┐        ┌──────────────┐        ┌──────────────┐
│  Kite REST     │        │  BrokerSync  │        │ API + WSPush │
│  (rate-limited)│        │  reconcile   │        │  frontend    │
└────────────────┘        └──────────────┘        └──────────────┘
```

| Path | Latency budget | Work |
|---|---|---|
| **Hot** (WS callback) | < 50 µs/batch | Stamp, enqueue, dict lookup, float compare, enqueue intent |
| **Warm** (executor, monitor) | 1–50 ms | HTTP order placement, exit evaluation |
| **Cold** (recorder, sync, API) | 100 ms–2 s | Disk, reconciliation, frontend serving |

### 3.3 Daily state machine

```
BOOT → PHASE_1(08:45) ─fail→ PHASE_1_FAIL → IDLE(next day)
             │pass
             ▼
       FEED_LIVE(08:55) → PREOPEN(09:00) → SETTLEMENT(09:09)
             → ARMING(09:09:30) → FROZEN(09:14) → TRADING(09:15)
             → MANAGING → EOD(15:28) → IDLE(next day)
```

Every transition is timestamped, persisted to `events.jsonl`, and pushed on WS topic `status`.
Phase is exposed at `GET /status`. Illegal transitions raise and halt entries (not exits).

| Phase | Entries armed | Exits active | Recorder |
|---|---|---|---|
| `PHASE_1` … `ARMING` | ✗ | ✗ | from `FEED_LIVE` |
| `FROZEN` | ✗ | ✗ | ✓ |
| `TRADING` | **✓** | ✓ | ✓ |
| `MANAGING` | ✗ | ✓ | ✓ |
| `EOD` | ✗ | ✓ (square-off) | until last exit + 300 s |

---

## 4. Modular design

### 4.1 Dependency rule

```
api  ──►  engine  ──►  brokers/kite  ──►  kiteconnect SDK
             │              │
             └──►  config ◄─┘          (config is a leaf — depends on nothing)
```
**Strict:** `brokers/` never imports `engine/`. `engine/` never imports `api/`. No cycles.
The broker layer is **state-free** — it takes parameters and returns results; it holds no session state
beyond the authenticated client handle.

### 4.2 Module reference

| Module | Responsibility | Public interface | Owns (state) | Thread | LOC |
|---|---|---|---|---|---|
| `config/loader.py` | Load, validate (pydantic), defaults, hot-reload | `load() -> Config`, `patch(dict) -> Config`, `on_change(cb)` | config object | any | 250 |
| `brokers/kite/auth.py` | TOTP login, token cache, validity probe | `login() -> (kite, access_token)`, `is_valid(token)` | — | main | 150 |
| `brokers/kite/instruments.py` | Master contract, expiry resolution, strike chains | `load_master(exch)`, `resolve_expiry(sym, today)`, `chain(sym, expiry, atm, n)` | cached master df | main | 200 |
| `brokers/kite/quotes.py` | Batched REST quote/ltp/ohlc | `quote(keys)`, `ltp(keys)`, `ohlc(keys)` | — | main/sync | 100 |
| `brokers/kite/ticker.py` | **Single WS**: connect, subscribe, modes, reconnect, order updates | `connect(plan)`, `subscribe(plan)`, `set_callbacks(...)`, `stats()` | socket, sub plan | WS-IO | 250 |
| `brokers/kite/orders.py` | place / modify / cancel / history, LPP parse, tick rounding | `place(...) -> OrderResult`, `modify(...)`, `cancel(...)`, `history(id)` | — | executor | 300 |
| `brokers/kite/portfolio.py` | positions, holdings, margins | `positions()`, `margins()` | — | sync | 100 |
| `brokers/kite/ratelimit.py` | Token buckets (10/s, 400/min, 5000/day, quote 1/s) | `acquire(kind) -> bool`, `stats()` | counters | shared | 100 |
| `engine/scheduler.py` | Phase machine, time triggers, preflight orchestration | `run()`, `phase`, `force(phase)` | phase | main | 200 |
| `engine/universe.py` | Rank TG/TL, shortlist ± buffer, ATM, wave plans | `build_wave1()`, `rank(snapshot)`, `build_wave2() -> SubscriptionPlan` | universe, ranks | main | 250 |
| `engine/feed.py` | WS wiring, **hot path**, arming table | `start()`, `arm(instruments)`, `disarm()`, `last(token)` | `armed{}`, `last_tick{}` | WS-IO | 250 |
| `engine/trigger.py` | Pure trigger evaluation | `evaluate(tick, armed) -> Signal\|None` | **stateless** | WS-IO | 120 |
| `engine/executor.py` | Intent → order lifecycle, retries, fallback ladder | `submit(intent)`, `exit(position, reason)` | in-flight orders | executor | 350 |
| `engine/positions.py` | Position book, fills, PnL, reconciliation | `on_order_event(e)`, `snapshot()`, `reconcile()` | **position book** | monitor | 300 |
| `engine/exits.py` | 8 priority-ordered conditions + trailing state | `evaluate(pos, cfg) -> (bool, trigger)`, `update_trailing(pos, cfg)` | **stateless** | monitor | 250 |
| `engine/recorder.py` | Queue → disk, rotation, gap markers | `start()`, `put(batch)`, `event(kind, payload)`, `stats()` | file handles | recorder | 200 |
| `api/server.py` + routes | REST + auth + CORS | FastAPI app | — | api | 450 |
| `api/ws_push.py` | Topic fan-out, diffing, client registry | `publish(topic, payload)` | client set | wspush | 150 |
| `main.py` | Crash-recovery loop, signals | — | — | main | 100 |

### 4.3 Testability contract

| Module | Test mode |
|---|---|
| `trigger`, `exits`, `instruments.resolve_expiry`, `orders` (rounding/LPP parse) | **Pure unit tests, no broker, no clock** |
| `universe.rank` | Fixture snapshot in → expected ranking out |
| `feed` + `executor` + `positions` | **Replay harness**: recorded NDJSON → mock broker |
| `api` | FastAPI `TestClient` against a seeded state |

---

## 5. Data schemas

### 5.1 Conventions

| Rule | Value |
|---|---|
| Timezone | All wall times IST (`Asia/Kolkata`); stored as ISO-8601 with offset |
| Monotonic timing | `*_ns` fields = `time.perf_counter_ns()` (latency math only, not wall time) |
| Wall timing | `*_us` fields = epoch microseconds UTC |
| Money | float, 2 dp; prices rounded to instrument tick size |
| IDs | `pos_<yyyymmdd>_<seq>`, `sig_<yyyymmdd>_<seq>`, broker `order_id` kept verbatim |
| Enums | UPPER_SNAKE (Appendix A) |
| Nulls | Absent field ≠ null; unknown values are `null`, not `0` |

### 5.2 Config schema

Single `config/config.json`. Every leaf has a `_doc` sibling. Validated on load; invalid → refuse to
start with the JSON-path of the offending field. See the implementation plan §15 for the annotated
version with rationale; the normative shape is:

```jsonc
{
  "system":   { "timezone":"Asia/Kolkata", "data_dir":"./data",
                "log_level":"INFO", "retention_days":7 },

  "schedule": { "phase1_time":"08:45:00", "feed_connect_time":"08:55:00",
                "preopen_start":"09:00:00", "settlement_snapshot":"09:09:00",
                "wave2_subscribe_time":"09:09:30", "option_reference_time":"09:13:00",
                "manual_cutoff":"09:14:00", "trading_start":"09:15:00",
                "eod_time":"15:28:00", "auto_continue_daily":true },

  "broker":   { "api_key":"…", "product":{ "stock_options":"NRML","index_options":"MIS" },
                "rate_limits":{ "orders_per_sec":10,"quote_per_sec":1,
                                "per_minute":400,"daily_cap":5000 },
                "timeouts":{ "order_ms":3000,"quote_ms":2000 },
                "ws":{ "reconnect_max_tries":50,"reconnect_max_delay_s":30 } },

  "trading_mode": { "mode":"paper" },

  "universe": { "enabled":true, "top_n_gainers":5, "top_n_losers":5, "candidate_buffer":5,
                "ranking_basis":"settlement", "atm_source":"settlement",
                "atm_fallback_chain":["settlement","futures_preopen","prev_close"],
                "rerank_on_open":false, "subscribe_futures_preopen":false,
                "indices":{ "NIFTY":{"enabled":true,"lots":1,"strike_offset":2},
                            "BANKNIFTY":{"enabled":true,"lots":1,"strike_offset":2},
                            "SENSEX":{"enabled":true,"lots":1,"strike_offset":2},
                            "FINNIFTY":{"enabled":false,"lots":1,"strike_offset":2} },
                "manual_instruments":[], "per_symbol_overrides":{} },

  "instruments": { "strike_reference":"ITM", "strike_offset":2, "strikes_per_side":4,
                   "subscription_soft_cap":2400, "subscribe_all_chains_early":false,
                   "expiry_roll":{ "enabled":true,"buffer_trading_days":1,
                                   "applies_to":"stocks_only" } },

  "entry":    { "min_diff":0.0, "fire_after_seconds":1, "deadline_seconds":180,
                "require_depth":true, "min_premium":0, "max_premium":0,
                "entry_price_source":"ask", "entry_slippage_pct":1.5,
                "entry_validity":"IOC",
                "order_type":{ "stock_options":"LIMIT","index_options":"LIMIT" },
                "order_fallback":{ "enabled":true,
                                   "on":["ORDER_TYPE_REJECT","LPP_REJECT","NO_DEPTH"],
                                   "to":"MARKETABLE_LIMIT" },
                "lots_default":1, "max_notional_per_trade":0, "max_total_notional":0,
                "entry_retry":{ "enabled":true,"max_attempts":3,"interval_ms":300 },
                "limit_modification":{ "enabled":true,"max_modifications":3,"step_pct":1.0 },
                "lpp":{ "retries":3,"safety_factor":0.99 } },

  "exits":    { "stop_loss":{"enabled":true,"percentage":-5.0},
                "target":{"enabled":true,"percentage":30.0},
                "trailing_stop":{"enabled":true,"activation_pct":7.0,"trail_distance_pct":3.0},
                "trailing_target":{"enabled":false,"activation_pct":15.0,
                                   "extend_distance_pct":5.0,"max_extension_pct":50.0},
                "time_exit":{"enabled":false,"holding_seconds":1200},
                "eod_exit":{"enabled":true,"square_off_time":"15:28:00"},
                "manual_detection":{"enabled":true},
                "monitor_interval_ms":25, "pnl_basis":"ltp",
                "exit_price_source":"bid", "exit_slippage_pct":1.0, "eod_slippage_pct":3.0 },

  "positions":{ "max_concurrent":10, "max_per_symbol":1,
                "broker_sync":{ "enabled":true,"poll_interval_seconds":2 } },

  "recorder": { "enabled":true, "format":"ndjson", "compression":"zstd",
                "record_depth_levels":5, "flush_interval_ms":500,
                "post_exit_record_seconds":300, "retention_days":7,
                "max_disk_mb":20000, "on_disk_full":"stop_recording",
                "upload":{ "enabled":false,"target":"","after":"eod" } },

  "alerts":   { "telegram":{"enabled":false,"bot_token":"","chat_id":""},
                "email":{"enabled":false},
                "on":["PHASE_1_FAIL","FEED_LOSS","KILL_SWITCH","ORDER_REJECT","DISK_FULL"] },

  "api":      { "host":"0.0.0.0","port":8080,
                "cors_origins":["https://your-project.pages.dev"],
                "auth_token":"…","ws_push_interval_ms":250 },

  "paper":    { "starting_capital":1000000,"simulate_charges":true,"fill_model":"touch" }
}
```

### 5.3 Instrument

```jsonc
{ "token": 12345678,               // Kite instrument_token (int) — primary key everywhere
  "tradingsymbol": "INDIGO26AUG5300PE",
  "exchange": "NFO",               // NFO | BFO | NSE
  "segment": "NFO-OPT",
  "underlying": "INDIGO",
  "kind": "OPTION",                // OPTION | EQUITY | FUTURE | INDEX
  "instrument_type": "PE",         // CE | PE | EQ | FUT | null
  "strike": 5300.0,
  "expiry": "2026-08-25",
  "lot_size": 625,
  "tick_size": 0.05,
  "is_index": false,
  "subscribe_mode": "full",        // ltp | quote | full
  "wave": 2 }                      // 1 | 2
```

### 5.4 Tick record *(recorder output — one line per tick)*

```jsonc
{ "t":"TICK",
  "recv_ns":  8123456789012,       // perf_counter_ns, FIRST statement in callback
  "recv_us":  1785900900123456,    // epoch µs
  "batch_seq": 148213,             // monotonic; gaps ⇒ loss
  "batch_size": 7,
  "token": 12345678,
  "sym": "INDIGO26AUG5300PE",
  "exch_ts": 1785900900000000,     // exchange_timestamp → feed_lag_us = recv_us − exch_ts
  "ltt": 1785900899000000,
  "ltp": 158.0,
  "ltq": 625,
  "atp": 151.2,
  "vol": 284375,
  "tbq": 12500, "tsq": 9375,
  "oi": 1875000, "oi_hi": 1900000, "oi_lo": 1810000,
  "ohlc": { "o":0.0, "h":0.0, "l":0.0, "c":117.85 },
  "depth": { "b":[[157.5,625,2],[157.0,1250,3]],   // [price, qty, orders] ×5
             "s":[[158.0,625,1],[158.5,1875,4]] } }
```

Event records share the stream (`"t"` discriminates), giving one chronological session file:

```jsonc
{ "t":"PHASE",     "recv_us":…, "from":"PREOPEN", "to":"SETTLEMENT" }
{ "t":"SUBSCRIBED","recv_us":…, "wave":2, "count":184, "modes":{"full":184} }
{ "t":"FEED_GAP",  "recv_us":…, "start_us":…, "end_us":…, "ms":842, "reconnects":1 }
{ "t":"SNAPSHOT",  "recv_us":…, "name":"settlement", "path":"snapshots/settlement.json" }
{ "t":"ARMED",     "recv_us":…, "count":24 }
{ "t":"SIGNAL",    "recv_us":…, "sig_id":"sig_20260805_003", "token":…, "diff":40.15 }
{ "t":"ORDER",     "recv_us":…, "order_id":"…", "stage":"SENT|ACK|POSTBACK", "status":"…" }
{ "t":"POSITION",  "recv_us":…, "pos_id":"…", "action":"OPEN|EXIT", "trigger":"STOP_LOSS" }
```

**File layout:** `data/<date>/ticks/<HH>.ndjson.zst`, rotated hourly; `manifest.json` indexes them.

### 5.5 Snapshot

```jsonc
{ "name":"settlement", "captured_at":"2026-08-05T09:09:00.142+05:30",
  "basis":"settlement",
  "instruments":{
    "INDIGO": { "token":408065, "ltp":5312.0, "prev_close":5180.0,
                "change_pct":2.55, "open":5312.0, "volume":184320 } },
  "ranking":{ "gainers":[{"rank":1,"symbol":"INDIGO","change_pct":2.55}],
              "losers":[{"rank":1,"symbol":"WIPRO","change_pct":-3.10}] } }
```

### 5.6 Signal (trigger output → intent)

```jsonc
{ "sig_id":"sig_20260805_003",
  "token":12345678, "sym":"INDIGO26AUG5300PE", "underlying":"INDIGO",
  "option_type":"PE", "strike":5300.0,
  "ref_price":117.85, "tick_price":158.0, "diff":40.15,
  "best_bid":157.5, "best_ask":158.0,
  "lots":1, "quantity":625,
  "t_tick_ns":…, "t_signal_ns":…,
  "reason":"FIRST_POSITIVE_DIFF" }
```

### 5.7 Order

```jsonc
{ "order_id":"260805000123456",       // broker id (null until ACK)
  "client_tag":"pos_20260805_003",    // our tag, echoed by broker
  "sig_id":"sig_20260805_003",
  "pos_id":"pos_20260805_003",
  "side":"BUY",                        // BUY | SELL
  "role":"ENTRY",                      // ENTRY | EXIT
  "token":12345678, "sym":"INDIGO26AUG5300PE",
  "order_type":"LIMIT", "product":"NRML", "validity":"IOC",
  "quantity":625, "price":162.75,
  "price_basis":{ "source":"ask","raw":158.0,"slippage_pct":1.5,"tick_rounded":"CEIL" },
  "attempt":1, "max_attempts":3,
  "status":"COMPLETE",                 // OPEN|COMPLETE|CANCELLED|REJECTED|<interim>
  "filled_quantity":625, "average_price":158.0,
  "status_message":null,
  "rejection":{ "kind":null,"lpp_limit":null },   // kind: LPP|MARGIN|ORDER_TYPE|RMS|OTHER
  "t_req_ns":…, "t_ack_ns":…, "t_first_postback_ns":…, "t_fill_ns":…,
  "postbacks":[ { "us":…, "status":"OPEN PENDING" }, { "us":…, "status":"COMPLETE" } ] }
```

### 5.8 Position

```jsonc
{ "pos_id":"pos_20260805_003",
  "status":"ACTIVE",                  // PENDING|ACTIVE|EXITING|CLOSED|FAILED|ADOPTED_UNMANAGED
  "mode":"live",                       // live | paper
  "token":12345678, "sym":"INDIGO26AUG5300PE",
  "underlying":"INDIGO", "option_type":"PE", "strike":5300.0, "expiry":"2026-08-25",
  "lots":1, "quantity":625, "lot_size":625,
  "entry":{ "order_id":"…","price":158.0,"filled_qty":625,
            "at":"2026-08-05T09:15:01.412+05:30","ref_price":117.85,"diff":40.15 },
  "exit": { "order_id":null,"price":null,"at":null,"trigger":null },
  "live": { "ltp":171.3,"bid":170.9,"ask":171.5,
            "pnl":8312.5,"pnl_pct":8.42,"max_pnl_pct":11.2,"min_pnl_pct":-1.4,
            "holding_seconds":184 },
  "trailing":{ "sl_active":true,"sl_peak":176.0,"sl_level":170.7,
               "tgt_active":false,"tgt_peak":0.0,"tgt_level":0.0 },
  "charges":{ "brokerage":40.0,"taxes":18.6,"total":58.6 },
  "flags":{ "exiting":false,"broker_confirmed":true,"reconciled":true } }
```

### 5.9 Latency record

```jsonc
{ "pos_id":"pos_20260805_003", "sym":"INDIGO26AUG5300PE",
  "exch_ts_us":…, "recv_us":…,
  "feed_lag_ms":12.4,          // recv_us − exch_ts  → BROKER/EXCHANGE side
  "tick_to_signal_us":38,      // ─┐
  "queue_wait_us":74,          //  │ OUR side
  "signal_to_req_ms":4.1,      //  │
  "req_to_ack_ms":41.7,        // ─┘
  "ack_to_first_postback_ms":88.2,
  "ack_to_fill_ms":902.5,
  "total_tick_to_fill_ms":1049.0 }
```

### 5.10 Session manifest

```jsonc
{ "date":"2026-08-05", "mode":"live",
  "phases":[{"phase":"FEED_LIVE","at":"…"}],
  "universe":{ "wave1":53,"wave2":184,"shortlist":["INDIGO","WIPRO"],
               "traded":["INDIGO26AUG5300PE"] },
  "feed":{ "ticks":4821334,"batches":688412,"gaps":0,"reconnects":0,
           "first_tick_after_open_ms":812,"median_feed_lag_ms":11.8 },
  "orders":{ "sent":8,"filled":7,"rejected":1,"lpp_retries":1 },
  "pnl":{ "realised":-5352.0,"charges":412.8 },
  "recorder":{ "files":7,"bytes":118293440,"dropped":0 },
  "clock":{ "ntp_offset_ms":3.2 },
  "integrity":{ "reconciled":true,"unmanaged_positions":0 } }
```

---

## 6. Broker layer — the Kite facade

### 6.1 Contract

- **State-free**: functions take explicit params, return explicit results. No hidden globals.
- **Never raises to the caller** for expected broker conditions — returns a result object.
  Unexpected conditions raise `BrokerError` with the taxonomy in §6.4.
- **Every call is logged** to `orders/api.jsonl` with request, response, and duration.

### 6.2 Signatures

```python
# auth.py
login() -> tuple[KiteConnect, str]         # TOTP flow, caches token to disk
is_valid(access_token: str) -> bool        # /user/profile probe

# instruments.py
load_master(exchange: str) -> DataFrame            # NFO | BFO, cached daily
resolve_expiry(symbol, today, is_index, cfg) -> date
chain(symbol, expiry, atm, strikes_per_side) -> list[Instrument]
lot_size(symbol, expiry) -> int
tick_size(token) -> float

# quotes.py
quote(keys: list[str]) -> dict          # batched, rate-limited (1/s)
ltp(keys)   -> dict
ohlc(keys)  -> dict

# ticker.py
class KiteFeed:
    connect(plan: SubscriptionPlan) -> None
    subscribe(plan: SubscriptionPlan) -> None      # incremental (wave 2)
    set_callbacks(on_tick_batch, on_order_event, on_state) -> None
    stats() -> FeedStats
    close() -> None
# on_tick_batch(ticks: list[dict], recv_ns: int)   ← MUST NOT BLOCK
# on_order_event(order_postback: dict)

# orders.py
place(kite, *, token, sym, exchange, side, quantity, price,
      order_type, product, validity, tag) -> OrderResult
modify(kite, *, order_id, price=None, order_type=None, validity=None) -> OrderResult
cancel(kite, *, order_id) -> OrderResult
history(kite, order_id) -> list[dict]
parse_lpp(status_message: str) -> float | None
round_price(price: float, tick: float, mode: "CEIL"|"FLOOR") -> float

# portfolio.py
positions(kite) -> dict          # {"day": [...], "net": [...]}
margins(kite)   -> dict

# ratelimit.py
acquire(kind: "order"|"quote"|"other", timeout_s: float = 0) -> bool
stats() -> dict
```

### 6.3 `OrderResult`

```jsonc
{ "success":true, "order_id":"260805000123456", "error":null,
  "rejection_kind":null, "lpp_limit":null,
  "t_req_ns":…, "t_ack_ns":…, "raw":{ } }
```

### 6.4 Error taxonomy & policy

| Kind | Detection | Retry? | Action |
|---|---|---|---|
| `LPP_REJECT` | `status_message` matches `allowed LPP limit (X)` | **Yes**, ≤3 | Re-price inside band × `safety_factor`, floor to tick |
| `ORDER_TYPE_REJECT` | MARKET refused on stock option | **Yes**, once | Fall back per `order_fallback.to` |
| `NO_DEPTH` | best_ask ≤ 0 at signal time | **Yes**, next tick | Skip this tick; re-evaluate |
| `MARGIN` / `RMS` | rejection text | **No** | Mark `FAILED`, alert, never re-fire |
| `RATE_LIMIT` | local bucket empty / HTTP 429 | **Yes**, backoff | Jittered retry within `deadline_seconds` |
| `NETWORK` / `5xx` | exception / status | **Yes**, ≤2 | Jittered backoff; IOC prevents duplicate resting orders |
| `AUTH` | 403 / token invalid | **No** | Halt entries, alert, attempt single re-login |

### 6.5 Feed contract (critical)

| Rule | Detail |
|---|---|
| One connection | Market data + order updates. Never open a second. |
| Callback discipline | `on_tick_batch` must return in < 50 µs. No I/O. (P1) |
| Reconnect | SDK auto-reconnect on. **On `on_reconnect` we must re-subscribe and re-apply modes** — Kite does not restore them. |
| Gap accounting | Outage duration emitted as `FEED_GAP` into the recorder; entries disarmed during the gap. |
| Mode budget | `full` only for tradeable options; `quote` for stocks/indices (§ plan 9). |
| Hard limits | ≤3,000 instruments, ≤3 connections per API key. |

---

## 6A. Multi-broker: data and trading are chosen independently

**`data_broker`** (zerodha | upstox) supplies the live feed, the instrument master
and REST quotes. **`trade_broker`** (zerodha | upstox | paper) places orders and owns
the position book. They are set separately in `config.broker`.

```jsonc
"broker": { "data_broker": "zerodha", "trade_broker": "zerodha" }   // default
"broker": { "data_broker": "upstox",  "trade_broker": "paper"    }   // isolated testing
"broker": { "data_broker": "upstox",  "trade_broker": "zerodha" }   // split
```

> **Why the split earns its keep:** running `data_broker: upstox` with
> `trade_broker: paper` touches **no Zerodha API key at all**, so it cannot disturb
> another system already using that key — no shared websocket count, no shared
> quote quota, no shared session.

### The contract every adapter satisfies

`backend/brokers/base.py` defines `DataBroker` and `TradeBroker` protocols plus the
**canonical shapes**. Adapters translate; the engine contains **zero** broker
conditionals.

| Concern | Kite (Zerodha) | Upstox | Canonical form |
|---|---|---|---|
| Instrument id | `instrument_token` int | `instrument_key` str `NSE_FO\|49520` | `Instrument.token` int + `data_key` / `trade_key` |
| **tick_size** | `0.05` rupees | **`5.0` paise** | **always rupees** |
| **expiry** | `date` | **epoch milliseconds** | `date` |
| Product | `MIS` / `NRML` | **`I` / `D`** | canonical names, mapped per adapter |
| Order status | `COMPLETE`, `OPEN PENDING` | lowercase `complete` | `normalise_status()` → canonical |
| Tick shape | flat + `depth.buy/sell` | nested `fullFeed.marketFF` | flat Kite-shaped dict |
| Order updates | **same websocket** | **separate portfolio stream** | `connect_order_stream()` → bool |

Two conversions are load-bearing and unit-tested with the failure they prevent:
`tick_size` 5.0 → 0.05 (unconverted, a 158.00 ask rounds to **165.00** instead of
160.40), and epoch-ms → `date`.

### Instrument identity across brokers

`Instrument.token` is the engine's primary key and comes from the **data** broker,
because that is what every tick carries. String-keyed brokers get a deterministic
56-bit surrogate (`surrogate_token`), stable across restarts so recorded tick files
stay readable.

When the two brokers differ, `trade_key` is resolved by matching the
**exchange-level contract identity** — `(underlying, expiry, strike, option_type)` —
never the tradingsymbol, because the formats differ:

```
Kite    INDIGO26AUG5300PE
Upstox  INDIGO 26 AUG 5300 PE
```

If a contract exists at the data broker but not at the trade broker,
`BrokerPair.resolve()` **raises**. Guessing an order identifier is not an
acceptable fallback.

### Upstox authentication

Ported from rank-momentum's production `brokers/upstox/auth.py`. Tokens die daily
at 03:30 IST, and Upstox exposes **no headless auth endpoint** — the authorization
code only appears on a browser redirect. Resolution order:

1. cached token issued after the last 03:30 reset
2. `credentials.upstox.access_token` you pasted in
3. `credentials.upstox.auth_code` exchanged for a token
4. **automated browser login**: mobile → TOTP → 6-digit PIN, 3 attempts with
   backoff, failure screenshot each time

Playwright is imported lazily, so it is required **only** for step 4:
`pip install playwright && playwright install chromium`.

### Known asymmetry

Kite delivers order updates on the **same** websocket, so fills land in
milliseconds. Upstox needs a **separate portfolio stream**. That stream does not
exist in the rank-momentum codebase either (verified: no `order_update` /
portfolio-feed handler anywhere in it), so there was nothing to port —
`connect_order_stream()` returns `False` and the engine polls `order_state()`
instead. Correct, just slower. Worth building before Upstox becomes the primary
trade broker.

### Where this differs from rank-momentum, deliberately

`tick_size`: rank-momentum stores the raw Upstox value (`instruments_manager.py`
keeps `float(row["tick_size"])`, i.e. `5.0`) and never rounds prices to the tick
grid — its limit prices are `round(price, 2)`. The bug is therefore latent there.
**This system rounds every price to the instrument tick**, which the exchange
requires, so the paise→rupee conversion is mandatory: with a raw `5.0`, a 158.00
ask rounds to **165.00** instead of 160.40.

### Files

```
brokers/base.py            protocols, canonical shapes, status + token normalisation
brokers/registry.py        factory, BrokerPair, cross-broker contract resolution
brokers/kite/adapter.py    Zerodha: both roles
brokers/upstox/            auth, instruments, feed, orders, adapter
```

---

## 7. Frontend REST API

### 7.1 Conventions

| Aspect | Rule |
|---|---|
| Base | `/api/v1` |
| Auth | `Authorization: Bearer <api.auth_token>` on **all** routes except `/health` |
| Content | `application/json`, UTF-8 |
| Envelope | Success `{ "ok":true, "data":…, "ts":"…" }` · Error `{ "ok":false, "error":{ "code":"…","message":"…","detail":… }, "ts":"…" }` |
| Codes | 200 ok · 400 validation · 401 auth · 409 illegal state · 422 config invalid · 429 rate limit · 500 internal |
| Latency | **All reads served from memory.** No route calls the broker synchronously. |
| Idempotency | Control routes accept `Idempotency-Key`; repeats within 60 s return the first result |

### 7.2 Read endpoints

| Method | Path | Returns |
|---|---|---|
| GET | `/health` | `{status, uptime_s, version}` — **unauthenticated**, for uptime checks |
| GET | `/status` | Phase, mode, market open?, feed stats, auth validity, counts (§7.4) |
| GET | `/config` | Full config + `_doc` strings + JSON-schema for form generation |
| GET | `/universe` | Wave 1/2 plans, ranking, shortlist, armed instruments |
| GET | `/universe/ranking` | Settlement ranking: all 50 with `change_pct`, rank, selected flag |
| GET | `/market/snapshot` | Per-instrument: ltp, bid, ask, ref, diff, armed, fired |
| GET | `/market/instrument/{token}` | Full latest tick incl. depth |
| GET | `/positions` | Open positions (§5.8) |
| GET | `/positions/closed` | Today's closed positions + exit trigger |
| GET | `/positions/{pos_id}` | One position, full detail incl. orders |
| GET | `/orders` | All orders today (§5.7); `?status=&role=` |
| GET | `/signals` | All signals incl. those not converted to orders |
| GET | `/latency` | Per-trade latency records (§5.9) + session aggregates |
| GET | `/snapshots` / `/snapshots/{name}` | Snapshot index / one snapshot (§5.5) |
| GET | `/recorder/stats` | Ticks, bytes, queue depth, drops, gaps, disk free |
| GET | `/events` | `events.jsonl` tail; `?since=&kind=` |
| GET | `/logs` | Log tail; `?level=&module=&limit=` |
| GET | `/reports/dates` | Dates with recorded sessions |
| GET | `/reports/{date}/manifest` | Session manifest (§5.10) |
| GET | `/reports/{date}/positions` \| `/orders` \| `/latency` | Historical records |
| GET | `/reports/{date}/ticks` | Tick-file index + signed download links |

### 7.3 Control endpoints

| Method | Path | Body | Effect | Guard |
|---|---|---|---|---|
| POST | `/config` | RFC-7386 merge patch | Validate → apply → persist → broadcast `config` | 422 on invalid; 409 for structural change mid-session |
| POST | `/config/validate` | config fragment | Dry-run validation only | — |
| POST | `/universe/manual` | `{action:"add"\|"remove", symbol, option_type?, strike?, lots?}` | Edit manual list | **409 after `manual_cutoff`** |
| POST | `/control/arm` \| `/disarm` | — | Enable/disable **new entries** (exits keep running) | — |
| POST | `/control/start` \| `/stop` \| `/restart` | — | Session lifecycle | — |
| POST | `/control/phase` | `{phase}` | Force a phase (**testing only**, refused in live mode) | 409 in live |
| POST | `/positions/{pos_id}/exit` | `{reason?}` | Manual exit one position | 409 if already exiting |
| POST | `/control/exit_all` | — | Exit every open position | — |
| POST | `/control/kill_switch` | `{confirm:"KILL"}` | **Exit all + halt for the day** (irreversible for session) | 400 without confirm |
| POST | `/control/panic_flatten` | `{confirm:"FLATTEN"}` | Exit all ignoring slippage caps | 400 without confirm |
| POST | `/control/reconcile` | — | Force broker reconciliation now | — |
| POST | `/auth/relogin` | — | Force fresh TOTP login | — |

### 7.4 `GET /status` — the frontend's primary poll/push payload

```jsonc
{ "ok":true, "ts":"2026-08-05T09:15:04.221+05:30",
  "data":{
    "phase":"TRADING", "mode":"live", "armed":true, "kill_switch":false,
    "market":{ "is_trading_day":true, "open":"09:15:00", "close":"15:30:00" },
    "clock":{ "server_time":"…", "ntp_offset_ms":3.2 },
    "auth":{ "valid":true, "expires_at":"2026-08-06T03:30:00+05:30" },
    "feed":{ "connected":true, "subscribed":237, "modes":{"quote":53,"full":184},
             "ticks":482133, "ticks_per_sec":1240, "last_tick_age_ms":38,
             "reconnects":0, "gaps":0, "median_feed_lag_ms":11.8 },
    "recorder":{ "running":true, "queue_depth":0, "bytes":118293440,
                 "dropped":0, "disk_free_mb":41230 },
    "counts":{ "armed":24, "signals":3, "orders":3, "open_positions":3, "closed":0 },
    "pnl":{ "realised":0.0, "unrealised":1842.5, "charges":124.2 },
    "capital":{ "available":95311.55, "used":42180.0 },
    "last_error":null }}
```

---

## 8. Frontend WebSocket API

### 8.1 Handshake

```
wss://<host>/api/v1/ws?token=<auth_token>
```
Token may be a query param (browsers cannot set headers on `WebSocket`) — it is therefore **short-lived
and separately issued** (§9.3). Server closes `4401` if invalid.

On connect the server sends one **snapshot per subscribed topic**, then diffs.

### 8.2 Client → server

```jsonc
{ "op":"subscribe",   "topics":["status","positions","market"] }
{ "op":"unsubscribe", "topics":["market"] }
{ "op":"ping" }
```

### 8.3 Server → client

```jsonc
{ "topic":"status",    "type":"snapshot"|"diff", "seq":18422, "ts":"…", "data":{…} }
{ "topic":"positions", "type":"diff", "seq":18423, "ts":"…",
  "data":{ "upsert":[ {…position…} ], "remove":["pos_20260805_001"] } }
{ "topic":"market",    "type":"diff", "seq":18424, "ts":"…",
  "data":{ "12345678":{ "ltp":171.3,"bid":170.9,"ask":171.5,"diff":53.45 } } }
{ "topic":"orders",    "type":"event", "seq":18425, "data":{ …order… } }
{ "topic":"events",    "type":"event", "seq":18426, "data":{ "kind":"PHASE","from":"…","to":"…" } }
{ "topic":"logs",      "type":"event", "seq":18427, "data":{ "level":"INFO","module":"EXEC","msg":"…" } }
{ "op":"pong", "ts":"…" }
```

| Topic | Cadence | Payload |
|---|---|---|
| `status` | `ws_push_interval_ms` (250 ms) or on change | §7.4 |
| `market` | 250 ms, **diff only**, armed instruments first | ltp/bid/ask/diff per token |
| `positions` | on change + 250 ms PnL refresh | upsert/remove |
| `orders` | on every order event | full order |
| `events` | immediate | phase, feed gap, snapshot, alert |
| `logs` | immediate, level-filtered | log line |

### 8.4 Rules

| Rule | Detail |
|---|---|
| **Never back-pressures the engine** | Push runs on its own thread; a slow client is dropped after `send_queue > 100`, close code `4408`. |
| Ordering | `seq` is monotonic per topic. A gap ⇒ client should re-request a snapshot. |
| Resync | `{"op":"resync","topics":[…]}` → server re-sends snapshots. |
| Heartbeat | Server pings every 20 s; client must pong within 10 s. |
| Auth expiry | Server closes `4401` when the token expires; client re-auths and reconnects. |
| **Fallback** | If WS is unavailable, polling `/status` + `/positions` at 1 s is functionally equivalent (only less efficient). The frontend must support both. |

---

## 9. Connecting the frontend

### 9.1 Topology

```
   Browser ── HTTPS ──► Vercel (static SPA)
      │
      ├── HTTPS ──────► api.yourdomain.com  ──► Caddy/nginx (TLS) ──► uvicorn :8080
      └── WSS   ──────► api.yourdomain.com/api/v1/ws ──────────────────────┘
                                    (EC2 35.154.x.x, ap-south-1)
```

### 9.2 ⚠️ The mixed-content problem — and why the current Vercel proxy is not enough

A page served from `https://*.vercel.app` **cannot** call `http://<ec2-ip>:8080`; browsers block it as
mixed content. The existing project works around this with a Vercel serverless proxy
(`Frontend/VercelApp/api/proxy.js` — `/api/proxy?target=…&path=…`).

**That works for REST, but it cannot carry a WebSocket.** Vercel serverless functions do not support
the WS upgrade, so a proxied frontend is forced back to polling — which is precisely what the `/ws`
push API exists to avoid.

| Option | REST | WS | Cost | Verdict |
|---|---|---|---|---|
| **A. Domain + TLS on the backend** (Caddy auto-Let's Encrypt in front of uvicorn) | ✓ | ✓ | a domain name | **Recommended.** ~10 lines of Caddyfile, auto-renewing certs. |
| **B. Cloudflare Tunnel** (`cloudflared`) | ✓ | ✓ | free, no public IP or open port needed | **Best if you don't want to expose the EC2 or manage DNS.** |
| C. Vercel serverless proxy (current) | ✓ | ✗ | free | REST-only fallback; keep as a backup path |
| D. Self-signed cert | ✓* | ✓* | free | Rejected — browsers require a manual exception |

**Recommendation: A or B.** Both give a stable `https://` + `wss://` origin, which also makes CORS,
cookies, and token handling straightforward. Keep C configured as a degraded fallback.

### 9.3 Auth flow

1. Operator enters the shared `auth_token` (from `config.api.auth_token`) in the frontend; stored in
   `sessionStorage`, never in `localStorage`.
2. REST: `Authorization: Bearer <token>`.
3. WS: the client first calls `POST /api/v1/auth/ws-ticket` → `{ticket, expires_in: 60}`, then connects
   `wss://…/ws?token=<ticket>`. This avoids putting the long-lived token in a URL (URLs leak via logs
   and Referer).
4. `401` ⇒ clear session and prompt again.

> Single shared token is acceptable for a single-operator system. If more than one person gets access,
> replace with per-user tokens before that happens — not after.

### 9.4 CORS

```
api.cors_origins = ["https://your-project.pages.dev", "https://*.your-project.pages.dev", "http://localhost:5173"]  # "*" in the host matches preview deploys
```
Allowed methods `GET, POST, OPTIONS`; allowed headers `Authorization, Content-Type, Idempotency-Key`;
credentials **not** used (bearer only). **Never ship `"*"` with a real auth token.**

### 9.5 Recommended client state model *(data only — no UI guidance)*

| Client store | Source | Update |
|---|---|---|
| `status` | `/status` → topic `status` | replace |
| `config` | `/config` | fetch on load + after `POST /config` |
| `universe` | `/universe` | fetch at `ARMING`, on `events` phase change |
| `market` | `/market/snapshot` → topic `market` | merge by token |
| `positions` | `/positions` → topic `positions` | upsert/remove by `pos_id` |
| `orders` | `/orders` → topic `orders` | upsert by `order_id` |
| `events`/`logs` | topics | append, ring buffer |

**Bootstrap sequence**
```
GET /health → GET /status → GET /config → GET /universe
→ POST /auth/ws-ticket → open WS → subscribe([status,market,positions,orders,events])
→ on WS snapshot: replace stores → on diff: merge
→ on WS close: exponential backoff reconnect (1s → 30s), fall back to 1s polling after 3 failures
```

### 9.6 Frontend environment

```
VITE_API_BASE   = https://api.yourdomain.com/api/v1
VITE_WS_URL     = wss://api.yourdomain.com/api/v1/ws
VITE_PROXY_BASE = /api/proxy            # optional degraded fallback (REST only)
```

### 9.7 Contract stability

- Path-versioned (`/api/v1`). Breaking changes ⇒ `/api/v2`.
- **Additive changes are always allowed**; clients must ignore unknown fields.
- `GET /config` returns a JSON-schema so the frontend can render config forms generically —
  **new config fields require no frontend release.**

---

## 10. Non-functional requirements

| Area | Requirement |
|---|---|
| **Security** | Credentials in `credentials.json`, `chmod 600`, gitignored. Bearer auth on all routes. CORS allow-list. Kill switch + panic flatten always reachable. No secrets in logs or API responses. |
| **Observability** | Structured logs (level, module, ts); `events.jsonl`; latency records per trade; `/status` health surface; alerts on `PHASE_1_FAIL`, `FEED_LOSS`, `ORDER_REJECT`, `DISK_FULL`, `KILL_SWITCH`. |
| **Durability** | Position book persisted on every state change; recorder flushed every 500 ms; three-way reconciliation on restart. |
| **Limits enforced** | 10 orders/s · 400/min · 5,000/day · 25 modifications/order · 3,000 instruments · 3 WS connections. |
| **Performance** | Hot path < 50 µs/batch · REST reads < 50 ms · WS push ≤ 250 ms · recorder sustains ≥ 5,000 ticks/s with zero drops. |
| **Recovery** | Unhandled exception → restart with backoff (≤5 crashes). Mid-session restart reconciles before arming. Feed loss → disarm entries, keep managing exits via REST. |
| **Time** | NTP-synced; offset recorded in the manifest; alert if \|offset\| > 50 ms. |

---

## 11. Out of scope

UI/UX and visual design · charting · backtesting · multi-strategy framework · multi-broker support ·
multi-user auth/RBAC · mobile app · automated capital allocation · tax/P&L reporting beyond the
session manifest.

---

## Appendix A — Enums

| Enum | Values |
|---|---|
| `Phase` | `BOOT` `PHASE_1` `PHASE_1_FAIL` `FEED_LIVE` `PREOPEN` `SETTLEMENT` `ARMING` `FROZEN` `TRADING` `MANAGING` `EOD` `IDLE` |
| `PositionStatus` | `PENDING` `ACTIVE` `EXITING` `CLOSED` `FAILED` `ADOPTED_UNMANAGED` |
| `OrderStatus` | `OPEN` `COMPLETE` `CANCELLED` `REJECTED` + Kite interim strings |
| `ExitTrigger` | `MANUAL_BROKER` `MANUAL_API` `STOP_LOSS` `TARGET` `TRAILING_TARGET` `TRAILING_SL` `TIME_EXIT` `EOD_SQUAREOFF` |
| `RejectionKind` | `LPP` `MARGIN` `ORDER_TYPE` `RMS` `RATE_LIMIT` `NETWORK` `AUTH` `OTHER` |
| `OrderTypeCfg` | `LIMIT` `MARKET` |
| `FallbackTo` | `MARKETABLE_LIMIT` `MARKET` `NONE` |
| `PriceSource` | `ask` `bid` `ltp` |
| `AtmSource` | `settlement` `prev_close` `futures_preopen` |
| `SubscribeMode` | `ltp` `quote` `full` |
| `RecordKind` | `TICK` `PHASE` `SUBSCRIBED` `FEED_GAP` `SNAPSHOT` `ARMED` `SIGNAL` `ORDER` `POSITION` |

## Appendix B — API error codes

| Code | HTTP | Meaning |
|---|---|---|
| `AUTH_REQUIRED` / `AUTH_INVALID` | 401 | Missing / bad token |
| `VALIDATION_FAILED` | 400 | Malformed request |
| `CONFIG_INVALID` | 422 | Config failed schema validation (includes JSON path) |
| `ILLEGAL_STATE` | 409 | Action not allowed in current phase (e.g. manual add after 09:14) |
| `NOT_FOUND` | 404 | Unknown `pos_id` / `order_id` / date |
| `CONFIRM_REQUIRED` | 400 | Destructive action without confirmation string |
| `RATE_LIMITED` | 429 | Client exceeded API rate limit |
| `BROKER_ERROR` | 502 | Upstream Kite failure (detail carries `RejectionKind`) |
| `INTERNAL` | 500 | Unhandled |

## Appendix C — Related documents

| Doc | Contents |
|---|---|
| `FIRSTTICK_SYSTEM_IMPLEMENTATION_PLAN.md` | Rationale, verified platform facts, build phases, testing, size budget |
| This document | Architecture, modules, schemas, broker layer, frontend APIs, connection |
