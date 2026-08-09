# Frontend Brief — TG/TL First-Tick Operator Console

**Hand this whole document to the builder.** It is self-contained: design direction,
live connection details, every endpoint with **real captured payloads**, the
WebSocket protocol, screen-by-screen data mapping, and formatting rules.

The backend is **already built, deployed and running.** Do not stub it, mock it, or
change it. Build only the client.

---

## 1. What you are building

A **single-operator control console** for an intraday options trading bot that runs
unattended on a server. One person watches it, adjusts settings, and can force an
exit. It is not a consumer product and not multi-user.

| | |
|---|---|
| **Stack** | React + TypeScript + Vite |
| **Styling** | Tailwind CSS (no component library required; if you use one, prefer headless — Radix/Headless UI) |
| **Charts** | Only where listed. Prefer none. Numbers and tables carry this UI. |
| **Deploy** | Vercel, static SPA |
| **Auth** | Single bearer token entered by the operator |
| **Routing** | Client-side, 8 pages (§7) |
| **State** | Any (Zustand/Context). Structure per §8. |

### Non-goals
No sign-up, no user management, no theming UI, no i18n, no mobile-first layout
(desktop 1440px is the design target; must not *break* below 1024px), no charting
library unless §7 explicitly asks, no server-side rendering.

---

## 2. Design direction — read this carefully

The reference register is an **institutional financial terminal**: Bloomberg
terminal restraint, a Stripe/Linear dashboard's discipline, an audit report's
seriousness. **Formal, dense, precise, quiet.** The operator is watching real money
move at 09:15:00 and must read a number correctly in under a second.

### Absolute rules

| Do | Don't |
|---|---|
| System font stack, or Inter | ❌ Display/serif/rounded/handwritten fonts, no Poppins, no Comfortaa |
| **Tabular numerals on every number** (`font-variant-numeric: tabular-nums`) | ❌ Proportional digits in tables — columns must align |
| Flat surfaces, `1px` borders, `6–8px` radius | ❌ Gradients, glassmorphism, glows, heavy drop shadows |
| One neutral grey scale + **one** accent | ❌ Multi-colour palettes, rainbow charts, coloured card backgrounds |
| Green/red **only** for P&L sign and status | ❌ Green/red as decoration |
| Compact density: `13–14px` body, `12px` table text | ❌ Airy marketing spacing, oversized headings |
| Instant state changes | ❌ Bouncy/spring animation, confetti, skeleton shimmer. ≤150ms opacity/colour fades only |
| Uppercase 11px letterspaced labels for metric captions | ❌ Sentence-case decorative subtitles |
| Monospace **only** for IDs, tokens, latency, file paths | ❌ Monospace body text |
| Light **and** dark theme, following `prefers-color-scheme` | ❌ Forced dark-only |

### Palette

Neutral-first. Suggested (adjust, keep the discipline):

```
Light:  bg #FFFFFF   surface #FAFAFA   border #E4E4E7   text #18181B   muted #71717A
Dark:   bg #0A0A0B   surface #141416   border #26262A   text #FAFAFA   muted #A1A1AA
Accent: #2563EB (blue-600) — used sparingly: active nav, primary button, focus ring
Positive #059669   Negative #DC2626   Warning #D97706   Neutral/idle #71717A
```

Status colours are **semantic only**: a phase pill, a P&L number, a connection dot.
Never a coloured card background, never a coloured heading.

### Layout skeleton

```
┌──────────────────────────────────────────────────────────────────────┐
│ TOPBAR  logo · phase pill · mode pill · feed dot · clock · [Disarm]  │  56px, sticky
├──────────┬───────────────────────────────────────────────────────────┤
│ SIDEBAR  │  PAGE                                                     │
│ 220px    │  ┌ page title ─────────────────── last updated ─┐         │
│ 8 items  │  │ KPI row: 4–6 stat cards                      │         │
│          │  │ primary table / detail cards                  │        │
└──────────┴───────────────────────────────────────────────────────────┘
```

**Card anatomy** — one pattern, reused everywhere:
```
┌─────────────────────────────┐
│ LABEL                  ⓘ    │  11px uppercase, letterspacing .05em, muted
│ 1,24,530.50                 │  24–28px semibold, tabular-nums
│ ▲ 2.4%  vs prev close       │  12px, semantic colour, muted suffix
└─────────────────────────────┘
   1px border · 6px radius · surface bg · 16px padding · NO shadow
```

**Tables** carry most of the data. Sticky header, 32px rows, zebra off, 1px row
borders, right-align all numerics, left-align symbols, monospace for IDs. Sortable
where a column is comparable. Every table needs an explicit **empty state** with one
sentence explaining *why* it is empty (see §9).

---

## 3. Connection — live values

```
REST base   https://15-252-140-30.sslip.io/api/v1
WebSocket   wss://15-252-140-30.sslip.io/api/v1/ws
```

Real Let's Encrypt certificate, valid in every browser. **No proxy needed, no mixed
content, no certificate exception.** Call it directly from the Vercel page.

### Vercel environment variables
```
VITE_API_BASE = https://15-252-140-30.sslip.io/api/v1
VITE_WS_URL   = wss://15-252-140-30.sslip.io/api/v1/ws
```
Vite inlines these at build time — a change requires a redeploy.

### Auth

A single shared bearer token. The operator pastes it once.

- Store in **`sessionStorage`**, never `localStorage`.
- Send on every request except `/health`:
  `Authorization: Bearer <token>`
- On `401`: clear the token, return to the token-entry screen.

**Token-entry screen:** minimal, centred card, one password-type input, one button.
Validate by calling `GET /status`; on 200 store and enter the app, on 401 show
"Token rejected."

### WebSocket auth — two steps, do not skip

Browsers cannot set headers on `WebSocket`, so a **short-lived ticket** is used
instead of putting the long-lived token in a URL.

```ts
// 1. exchange the bearer token for a 60-second ticket
const { data } = await api.post('/auth/ws-ticket');   // { ticket, expires_in: 60 }
// 2. connect with the ticket
const ws = new WebSocket(`${WS_URL}?token=${data.ticket}`);
```
A ticket is **single-use**. Request a fresh one for every connect **and every
reconnect**. Close code `4401` = bad/expired ticket → get a new one and retry.

### ⚠️ CORS — you will be blocked until this is done

The backend allow-lists origins explicitly. It currently permits only
`http://localhost:5173`. **Send the deployed Vercel URL to the backend owner** to be
added, including preview URLs if you want those to work (Vercel gives each preview a
different hostname). Until then, production builds get a CORS error while local dev
works fine.

### Envelope

Every response, success or failure:
```jsonc
{ "ok": true,  "data": { … },                                   "ts": "2026-08-09T13:53:01.789822+05:30" }
{ "ok": false, "error": { "code": "…", "message": "…", "detail": null }, "ts": "…" }
```
Always read `data`. On `ok: false`, surface `error.message` verbatim — the backend
writes operator-readable messages (e.g. config validation names the exact JSON path).

| code | HTTP | Handle by |
|---|---|---|
| `AUTH_INVALID` | 401 | clear token → token screen |
| `VALIDATION_FAILED` | 400 | inline field error |
| `CONFIG_INVALID` | 422 | show `error.message` under the config form (it names the field path) |
| `ILLEGAL_STATE` | 409 | toast — action not allowed in this phase |
| `NOT_FOUND` | 404 | toast |
| `CONFIRM_REQUIRED` | 400 | you omitted the confirmation string |
| `RATE_LIMITED` | 429 | toast, back off |
| `BROKER_ERROR` | 502 | toast, show detail |
| `INTERNAL` | 500 | toast |

---

## 4. Domain concepts the UI must express correctly

Getting these wrong makes the UI misleading. They are the whole point of the product.

**Phase.** The bot walks a fixed daily sequence. It is the single most important
thing on screen — the operator's first question is always "where are we?"
```
BOOT → PHASE_1 → FEED_LIVE → PREOPEN → SETTLEMENT → ARMING → FROZEN
     → TRADING → MANAGING → EOD → IDLE            (PHASE_1_FAIL = aborted)
```
| Phase | Meaning for the operator | Suggested colour |
|---|---|---|
| `BOOT` / `IDLE` | waiting for tomorrow | neutral |
| `PHASE_1` | authenticating, downloading contracts | blue |
| `PHASE_1_FAIL` | **no trading today** | red |
| `FEED_LIVE` | feed connected, recording | blue |
| `PREOPEN` | 09:00–09:08 auction being recorded | blue |
| `SETTLEMENT` | ranking being computed | blue |
| `ARMING` | option chains subscribing | amber |
| `FROZEN` | instrument set locked, waiting for the bell | amber |
| `TRADING` | **entries live** | green |
| `MANAGING` | holding, exits armed | green |
| `EOD` | squaring off | amber |

**Mode.** `paper` or `live`. **This must be unmissable.** A persistent pill in the
topbar. In `live`, tint the topbar border and prefix the browser tab title with `●`.
Never let the operator wonder whether orders are real.

**Armed vs Fired.** An *armed* instrument is watched and may fire once. `fired: true`
means its single allowed entry has been used. One entry per instrument per day.

**Tradeable vs Buffer.** The top-N ranked names are **tradeable**. The extra
`candidate_buffer` names are **subscribed but never traded** — insurance if the
ranking shifts. The UI must visually distinguish these; conflating them is wrong.

**`feed_lag_us`.** Microseconds between the exchange's timestamp and our receipt —
i.e. *broker/exchange* delay, not ours. `null` when unknown. Can be **huge when the
market is shut** (a stale snapshot tick from Friday's close is legitimately ~35
hours old). Do not present a large value as an error; label it "trade age" when the
market is closed.

**Reference price.** For an option this is its **previous close**, because Indian
options do not trade in the pre-open. `diff = ltp − ref_price`; the first positive
diff fires the entry.

---

## 5. REST API — complete, with real captured payloads

All reads are served from memory; none call the broker. Poll freely (1 s is fine),
but prefer the WebSocket.

### `GET /health` — unauthenticated
```json
{ "status": "ok", "uptime_s": 26902.3, "version": "1.0.0", "phase": "TRADING" }
```

### `GET /status` — the primary payload. Real capture:
```jsonc
{
  "phase": "TRADING",
  "entries_allowed": true,
  "last_error": null,
  "schedule": { "phase1_time": "08:45:00", "feed_connect_time": "08:55:00",
                "settlement_snapshot": "09:09:00", "manual_cutoff": "09:14:00",
                "trading_start": "09:15:00", "eod_time": "15:28:00" },
  "history": [ { "from": "BOOT", "to": "PHASE_1", "at_us": 1786245300078676 } ],
  "mode": "paper",
  "halted": false,
  "uptime_s": 26901.4,
  "feed": { "connected": true, "subscribed": 463,
            "modes": { "quote": 52, "full": 411 },
            "ticks": 463, "batches": 2, "order_events": 0,
            "reconnects": 0, "gaps": 0,
            "last_tick_age_ms": 17039734.5, "last_error": null },
  "engine": { "phase": "TRADING", "entries_enabled": true, "armed": 233,
              "fired": 0, "signals": 0, "ticks_seen": 463,
              "tracked_instruments": 463, "intent_queue": 0 },
  "recorder": { "enabled": true, "running": true, "queue_depth": 0,
                "ticks": 463, "events": 11, "bytes": 195630, "dropped": 0,
                "batches": 2, "disk_full": false,
                "dir": "/opt/firsttick/data/2026-08-09/ticks",
                "compression": "zstd" },
  "positions": { "open": 0, "closed": 0, "failed": 0, "adopted": 0,
                 "unrealised": 0, "realised": 0, "charges": 0 },
  "rate_limits": { "order": { "rejected": 0, "used_1s": 0, "used_60s": 0,
                              "used_86400s": 0, "limit_1s": 10,
                              "limit_60s": 400, "limit_86400s": 5000 },
                   "quote": { "rejected": 0, "used_1s": 0, "limit_1s": 1 },
                   "other": { "rejected": 0, "used_1s": 0, "limit_1s": 10 } },
  "ws_clients": 0,
  "server_time": "2026-08-09T13:53:33.497460+05:30"
}
```

### `GET /universe`
Keys: `nifty50` (50 strings), `indices` (e.g. `["NIFTY","BANKNIFTY","SENSEX"]`),
`tradeable` (string[]), `buffer` (string[]), `subscribed` (int), `armed` (array):
```json
{ "token": 39396866, "symbol": "TCS26AUG2380CE", "underlying": "TCS",
  "ref_price": 62.65, "lots": 1, "fired": false, "ltp": 92.1 }
```

### `GET /universe/ranking` — all 50, `data.ranked`:
```json
{ "rank": 1, "symbol": "TCS", "ltp": 2452.7, "prev_close": 2373.0,
  "change_pct": 3.3586, "selected": true }
```
`rank` is the gainer rank (1 = biggest gainer, 50 = biggest faller).
`selected` = in the tradeable set.

### `GET /market/snapshot` — object keyed by token **string**:
```json
{ "6401": { "sym": "ADANIENT", "ltp": 3020.0, "bid": 0.0,
            "ask": 0.0, "feed_lag_us": null } }
```
~463 entries. `bid`/`ask` are `0.0` when the book is empty (market closed).

### `GET /market/instrument/{token}`
`{ token, ltp, bid, ask, volume, oi, exchange_ts_us, feed_lag_us }` · 404 if no tick yet.

### `GET /positions` · `GET /positions/closed` · `GET /positions/{pos_id}`
Currently `[]`. Shape when populated:
```jsonc
{ "pos_id": "pos_20260810_001", "status": "ACTIVE", "mode": "paper",
  "instrument": { "token": 12345678, "tradingsymbol": "INDIGO26AUG5300PE",
                  "underlying": "INDIGO", "option_type": "PE", "strike": 5300.0,
                  "expiry": "2026-08-25", "lot_size": 150, "exchange": "NFO" },
  "lots": 1, "quantity": 150, "sig_id": "sig_20260810_003",
  "entry": { "order_id": "260810000123456", "price": 158.0, "filled_qty": 150,
             "at_us": 1786245901412000, "ref_price": 117.85, "diff": 40.15 },
  "exit":  { "order_id": null, "price": 0.0, "filled_qty": 0,
             "at_us": 0, "trigger": null },
  "live":  { "ltp": 171.3, "bid": 170.9, "ask": 171.5, "pnl": 1995.0,
             "pnl_pct": 8.42, "max_pnl_pct": 11.2, "min_pnl_pct": -1.4,
             "holding_seconds": 184 },
  "trailing": { "sl_active": true, "sl_peak": 176.0, "sl_level": 170.7,
                "tgt_active": false, "tgt_peak": 0.0, "tgt_level": 0.0 },
  "flags": { "exiting": false, "broker_confirmed": true, "reconciled": true },
  "charges": 0.0 }
```
`status`: `PENDING` `ACTIVE` `EXITING` `CLOSED` `FAILED` `ADOPTED_UNMANAGED`.
`exit.trigger`: `STOP_LOSS` `TARGET` `TRAILING_SL` `TRAILING_TARGET` `TIME_EXIT`
`EOD_SQUAREOFF` `MANUAL_API` `MANUAL_BROKER`.

> `ADOPTED_UNMANAGED` = a position found at the broker that this bot did not open.
> Show it with a distinct badge; it is exitable but is not a strategy entry.

### `GET /orders` — every attempt, including rejections
```jsonc
{ "pos_id": "pos_20260810_001", "sym": "INDIGO26AUG5300PE", "role": "ENTRY",
  "side": "BUY", "qty": 150, "price": 165.90, "attempt": 1,
  "order_id": "260810000123456", "status": "COMPLETE",
  "rejection": null, "message": null, "at": "2026-08-10T09:15:01.4+05:30" }
```
`attempt` > 1 means a retry. `rejection` ∈ `LPP` `MARGIN` `ORDER_TYPE` `RMS`
`RATE_LIMIT` `NETWORK` `AUTH` `OTHER`. **Show retries grouped under their `pos_id`** —
a single entry can legitimately span 3 attempts.

### `GET /signals`
```jsonc
{ "sig_id": "sig_20260810_003", "sym": "INDIGO26AUG5300PE", "diff": 40.15,
  "ref": 117.85, "price": 158.0, "ask": 158.0, "at": "2026-08-10T09:15:01.2+05:30" }
```
A signal without a matching order = it was refused (position cap, notional cap).
Worth surfacing.

### `GET /latency`
```jsonc
{ "trades": [ { "sig_id": "…", "sym": "…", "tick_to_signal_us": 38.0,
                "signal_to_req_ms": 4.1, "req_to_ack_ms": 41.7,
                "ack_to_fill_ms": 902.5, "total_tick_to_fill_ms": 1049.0 } ],
  "median_tick_to_fill_ms": 1049.0 }
```
Render as a **horizontal stacked bar per trade** — the only chart this app needs.
Segments: tick→signal (ours), signal→req (ours), req→ack (broker), ack→fill
(exchange). Label which side owns each segment; that distinction is the point.

### `GET /recorder/stats`
```json
{ "enabled": true, "running": true, "queue_depth": 0, "ticks": 463, "events": 11,
  "bytes": 195630, "dropped": 0, "batches": 2, "disk_full": false,
  "dir": "/opt/firsttick/data/2026-08-09/ticks", "compression": "zstd" }
```
`dropped > 0` or `disk_full` = **prominent warning**.

### `GET /events?limit=200`
```json
{ "kind": "FEED_CONNECTED", "subscribed": 52, "modes": { "quote": 52 },
  "at": "2026-08-09T08:55:00.491885+05:30" }
```
Kinds include `FEED_CONNECTED` `FEED_CLOSED` `FEED_RECONNECT` `SIGNAL` `POSITION`.
Shape varies by kind — render `kind` + `at` prominently, remaining fields as
key/value pairs. Do not assume a fixed schema.

### `GET /logs?limit=200`
```json
{ "level": "INFO", "module": "engine", "msg": "API listening…", "ts": "06:24:39" }
```
`ts` is **time-only** (`HH:MM:SS`), not a full timestamp. Filter by level.

### `GET /config`
`{ "config": { …full config… }, "schema": { …JSON Schema… } }`

**Render the form generically from `schema`.** Do not hardcode fields — new backend
settings must appear with no frontend release. Use `config` for current values, the
JSON Schema for type/min/max/enum, and the `_doc` string present on most config
objects as inline help text. Group by top-level section (14 of them: `system`,
`schedule`, `broker`, `trading_mode`, `universe`, `instruments`, `snapshots`,
`entry`, `exits`, `positions`, `recorder`, `alerts`, `api`, `paper`).

### Control endpoints (all POST)

| Path | Body | Notes |
|---|---|---|
| `/config` | RFC-7386 merge patch, e.g. `{"exits":{"stop_loss":{"percentage":-7.5}}}` | Returns `{changed:[paths]}`. **409** if it touches a structural field mid-session (schedule/universe/instruments/api) — surface that clearly. |
| `/config/validate` | same | Dry run. **Call this on blur** for live validation. |
| `/universe/manual` | `{action:"add"\|"remove", symbol:"INDIGO", lots?:1}` | **409 after 09:14:00.** Disable the control and explain why once the cutoff passes. |
| `/control/arm` · `/control/disarm` | — | Enable/disable **new entries**. Exits keep running. Prominent topbar toggle. |
| `/control/start` · `/stop` · `/restart` | — | Lifecycle. Confirm first. |
| `/control/phase` | `{phase:"TRADING"}` | **Testing only; 409 in live mode.** Hide unless mode is `paper`. |
| `/positions/{pos_id}/exit` | — | Manual exit one. 409 if already exiting. |
| `/control/exit_all` | — | Confirm. Returns `{exiting:n}`. |
| `/control/kill_switch` | `{"confirm":"KILL"}` | **Exit everything + halt for the day.** Type-to-confirm dialog: operator must type `KILL`. Destructive styling. |
| `/control/reconcile` | — | Force broker reconciliation. Returns `{confirmed,closed_externally,qty_drift,adopted}`. |
| `/auth/ws-ticket` | — | 60s single-use WS ticket. |

---

## 6. WebSocket protocol

### Client → server
```jsonc
{ "op": "subscribe",   "topics": ["status","market","positions","orders","events","logs"] }
{ "op": "unsubscribe", "topics": ["market"] }
{ "op": "resync",      "topics": ["positions"] }
{ "op": "ping" }
```

### Server → client
```jsonc
{ "topic": "status",    "type": "snapshot", "seq": 1, "data": { …same as GET /status… } }
{ "topic": "market",    "type": "diff",     "seq": 42, "data": { "6401": { "ltp": …, "bid": …, "ask": … } } }
{ "topic": "positions", "type": "diff",     "seq": 7,  "data": { "upsert": [ …position… ], "remove": ["pos_…"] } }
{ "topic": "orders",    "type": "event",    "seq": 3,  "data": { …order… } }
{ "topic": "events",    "type": "event",    "seq": 9,  "data": { "kind": "SIGNAL", … } }
{ "op": "pong", "ts": "…" }
```

**Rules**
- On subscribe you get one `snapshot` per topic, then `diff`/`event` messages.
- `seq` is monotonic **per topic**. A gap ⇒ send `resync` for that topic.
- `market` diffs are **partial** — merge by token key, never replace the map.
- `positions` diffs use `upsert` / `remove` — apply both.
- Push cadence ~250 ms. Do not animate on every frame; it will strobe.
- Server pings every 20 s; reply to `ping` with the `pong` op.
- Close `4401` = auth → new ticket. Close `4408` = you were too slow and got dropped.
- **Reconnect:** exponential backoff 1s → 30s. After 3 consecutive failures, fall
  back to polling `/status` + `/positions` at 1 s and show a degraded indicator.
  Everything works over REST alone; WS is an efficiency layer, not a requirement.

---

## 7. Pages

### 7.1 Dashboard (`/`)
The at-a-glance answer to "is it working and am I making money?"

- **Phase timeline** — horizontal stepper of the 11 phases, current one highlighted,
  each completed step showing its actual time from `status.history`, upcoming ones
  showing their scheduled time from `status.schedule`. This is the signature
  component; make it excellent.
- **KPI cards:** Realised P&L · Unrealised P&L · Open positions · Armed instruments ·
  Signals fired · Feed status.
- **Open positions table** (compact, links to Positions).
- **Right column:** feed panel (connected, subscribed w/ mode split, ticks, ticks/s,
  last tick age, reconnects, gaps) · recorder panel (running, ticks, size, dropped,
  disk) · recent events (last 10).

### 7.2 Positions (`/positions`)
Tabs **Open** / **Closed**. Columns: symbol, underlying, type, strike, qty, entry,
LTP, P&L ₹, P&L %, max/min %, TSL level, held, status, actions.
- P&L sign-coloured; max/min as a tiny muted range.
- **TSL column:** when `trailing.sl_active`, show `sl_level` and how far LTP is above
  it. When inactive show `—`.
- Row expands to a detail card: entry/exit blocks, trailing state, flags, linked
  orders, and the originating signal (`sig_id`).
- Per-row **Exit** button → confirm dialog. Disable when `flags.exiting`.
- Badge `ADOPTED_UNMANAGED` distinctly.

### 7.3 Universe (`/universe`)
- **Ranking table**, all 50, from `/universe/ranking`: rank, symbol, prev close, LTP,
  change %, and a **Tradeable / Buffer / —** badge.
  Sort by change % by default. Colour only the change % cell.
- **Armed instruments table** from `universe.armed`: symbol, underlying, ref price,
  LTP, **diff** (computed `ltp − ref_price`, sign-coloured), lots, fired.
  Diff is the single most important derived number in the app — the entry trigger.
- **Manual instrument** add/remove panel. After `manual_cutoff` (09:14), disable it
  and show "Manual entry window closed at 09:14:00."
- Subscription summary: total, quote vs full split, vs `subscription_soft_cap`.

### 7.4 Orders (`/orders`)
All attempts, grouped by `pos_id`, newest first. Columns: time, symbol, role, side,
qty, price, attempt, status, rejection, message.
- Rejections visually distinct; show `message` in full (it carries the LPP ceiling).
- Filters: role, status, rejected-only.
- Second tab **Signals** — every signal, flagged if no order followed.

### 7.5 Latency (`/latency`)
Median tick→fill KPI, plus the stacked bar per trade (§5). Table of all five
measurements per trade. Above it, one line of plain text explaining which segments
are ours and which are the broker's/exchange's.

### 7.6 Config (`/config`)
Generic schema-driven form, grouped by the 14 sections, `_doc` as help text.
- Dirty-state tracking; **Save** / **Revert**.
- On blur, call `/config/validate` and show inline errors.
- On 422, display `error.message` — it names the exact JSON path.
- Structural fields (schedule/universe/instruments/api): mark clearly as
  "restart required"; on a 409 explain that plainly.
- `trading_mode.mode` gets an explicit confirm dialog when switching to `live`.

### 7.7 Controls (`/controls`)
Grouped, escalating destructiveness, each with a one-line consequence:
- **Arm / Disarm entries** (toggle)
- **Reconcile now** — show the returned report
- **Restart service** — confirm
- **Exit all positions** — confirm, shows count
- **Kill switch** — type `KILL`. Red, isolated, bordered section, explicitly labelled
  irreversible for the session.
- Phase-force control **only when `mode === "paper"`**.

### 7.8 Logs & Events (`/logs`)
Two panes or tabs. Logs: level filter, module filter, monospace, auto-scroll with a
pause-on-scroll-up. Events: `kind` + `at` prominent, remaining fields as key/value.

---

## 8. Client state & bootstrap

| Store | Source | Update |
|---|---|---|
| `auth` | operator input | sessionStorage |
| `status` | `/status` → topic `status` | replace |
| `config` + `schema` | `/config` | refetch after save |
| `universe` | `/universe`, `/universe/ranking` | refetch on phase change |
| `market` | `/market/snapshot` → topic `market` | **merge by token** |
| `positions` | `/positions` → topic `positions` | upsert/remove by `pos_id` |
| `orders` / `signals` | endpoints → topics | prepend, cap ~500 |
| `events` / `logs` | endpoints → topics | ring buffer ~500 |

```
GET /health                       → reachable?
GET /status (Bearer)              → 401 ⇒ token screen
GET /config, /universe            → forms + tables
POST /auth/ws-ticket → open WS    → subscribe all topics
on snapshot: replace · on diff: merge · on close: backoff, then poll
```

---

## 9. Formatting, states, edge cases

**Numbers.** Indian grouping for currency: `₹1,24,530.50` (2 dp, lakh/crore
grouping). Prices 2 dp. Percentages 2 dp with explicit sign (`+3.36%`). Latency:
`<1000µs` → `380µs`, else `1.05ms`; `ms` beyond 1000 → `1.05s`. Bytes → KB/MB.
`191µs`-style values are `feed_lag_us`. **Tabular numerals everywhere.**

**Times.** All IST. Show `HH:MM:SS` for today, `DD MMM HH:MM` otherwise. `at_us` is
epoch **microseconds** — divide by 1000 for JS `Date`. `logs[].ts` is already
`HH:MM:SS` with no date.

**Required states for every data surface**
- **Loading:** static skeleton or a quiet "Loading…" — no shimmer.
- **Empty:** explain the reason. `positions` empty during `BOOT` → "No positions.
  Trading starts at 09:15:00." Not "No data".
- **Error:** inline, with the backend's `error.message` and a Retry.
- **Disconnected:** topbar dot turns amber and reads "Polling" when WS is down but
  REST works; red "Disconnected" when neither works. Never silently show stale data.
- **Stale:** if `status.server_time` is more than 10 s behind the browser clock, mark
  the page "Data may be stale".

**Market-closed behaviour** (the state you will develop in, so handle it first)
- `bid`/`ask` are `0.00` — render `—`, not `0.00`.
- `feed_lag_us` can be tens of hours. Label it "trade age" and do not colour it red.
- `last_tick_age_ms` will be enormous. Format as duration, don't dump the raw number.
- Positions/orders/signals/latency all empty. This is normal, not an error.

**Safety in the UI**
- `mode: "live"` must be unmistakable at all times.
- Every destructive action confirms; kill switch requires typing `KILL`.
- Never display the auth token after entry, never log it.
- `status.halted: true` → persistent banner "Halted for the session — kill switch
  active." Disable arm/exit controls.
- `status.last_error` non-null → topbar warning that opens Logs.
- `PHASE_1_FAIL` → full-width red banner: "Pre-market checks failed — no trading
  today", with the reason from `last_error`.

---

## 10. Definition of done

- Token entry → dashboard works against the live backend.
- All 8 pages render real data, including `bid/ask = 0` and empty collections.
- WS connects via ticket, receives snapshots and diffs, reconnects with backoff,
  falls back to polling after 3 failures.
- Config form is generated from `schema` — not hardcoded — and shows a 422 field path.
- Kill switch requires typing `KILL`; every destructive action confirms.
- `live` mode is unmistakable.
- Light and dark both correct; no horizontal page scroll at 1024px.
- Every number uses tabular numerals; every table has a real empty state.
- No gradients, no glass, no display fonts, no bouncy animation.
