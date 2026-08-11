# First-Tick Operator Console

Single-operator control console for the TG/TL first-tick trading engine.
React + TypeScript + Vite + Tailwind. No mock data — every screen reads the live
backend.

Spec: [`../docs/05_FRONTEND_BRIEF.md`](../docs/05_FRONTEND_BRIEF.md)

## Run locally

```bash
cd frontend
npm install
cp .env.example .env      # already points at the live backend
npm run dev               # http://localhost:5173
```

Paste the operator token when prompted. `http://localhost:5173` is already in the
backend's CORS allow-list.

```bash
npm run build       # tsc -b && vite build  ->  dist/
npm run typecheck
```

## Deploy to Vercel

1. Import the repo, set **Root Directory** to `frontend`.
2. Framework preset: **Vite** (build `npm run build`, output `dist`).
3. Environment variables:
   ```
   VITE_API_BASE = https://15-252-140-30.sslip.io/api/v1
   VITE_WS_URL   = wss://15-252-140-30.sslip.io/api/v1/ws
   ```
   These are inlined at build time — changing one needs a redeploy.
4. **Send the deployed URL to the backend owner** so it can be added to
   `api.cors_origins`. Until then the browser blocks every request with a CORS
   error, even though the backend is reachable. Vercel preview deployments get a
   different hostname each push and each needs allow-listing too.

## Pages

| Route | Contents |
|---|---|
| `/` Dashboard | Phase timeline, P&L and armed KPIs, open positions, feed + recorder health, recent events |
| `/positions` | Open / Closed / Orders / Signals. Row expands to entry, trailing state, book and flags. Per-position and exit-all |
| `/live` Live Data | Selection (tradeable vs buffer), armed instruments with live diff, settlement ranking, all ticks, manual instruments |
| `/status` | Feed, engine, recorder, broker rate limits, entry-latency breakdown, phase history, reconcile / restart |
| `/strategy` | Entry, exits, universe, instruments, positions config |
| `/settings` | Trading mode, broker, schedule, recorder, snapshots, alerts, system, API |
| `/logs` | Log stream with level/text filters, typed event feed, controls incl. kill switch |

## How it talks to the backend

- **Auth** — one bearer token, held in `sessionStorage` for the tab only. A `401`
  anywhere clears it and returns to the token screen.
- **WebSocket** — browsers cannot set headers on `WebSocket`, so the client first
  calls `POST /auth/ws-ticket` for a **single-use 60-second ticket** and connects
  with `?token=<ticket>`. A fresh ticket is fetched for every connect and reconnect.
- **Degradation** — the socket reconnects with exponential backoff (1s → 30s). After
  three consecutive failures it falls back to polling REST every second and the
  topbar reads *Polling*. Every screen works over REST alone; the socket is an
  efficiency layer, not a requirement.
- **Diffs** — `market` and `positions` arrive as partial diffs and are merged by
  token / `pos_id`. They are never wholesale-replaced.
- **Config** — the form is generated from the JSON Schema at `GET /config`, so new
  backend settings appear with no frontend release. `_doc` strings become inline
  help. A `422` is shown verbatim because it names the offending JSON path.

## Conventions worth preserving

- Every number uses tabular numerals so columns align while streaming.
- `bid`/`ask` of `0` mean an empty book (market closed) and render as `—`, not `0.00`.
- `feed_lag` is the exchange's dissemination delay, not ours, and is legitimately
  hours-large outside market hours — labelled *last trade age* then.
- An option's reference price is its **previous close**; options do not trade in the
  pre-open. `diff = LTP − reference` is the entry trigger.
- **Tradeable** names may fire entries. **Buffer** names are subscribed only and are
  never traded — the UI must keep them visually distinct.
- `live` mode tints the topbar and prefixes the tab title with `●`.
- Destructive actions confirm; the kill switch requires typing `KILL`.
