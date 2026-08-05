# TG/TL First-Tick — Zerodha Open-Drive System

Intraday options system on **Zerodha Kite Connect**. At the open it watches the top-N gainers and
losers of the Nifty 50 (plus selected indices) and enters on the **first tick showing a positive
premium difference** against a pre-open reference.

> **Status: documentation complete, code not started.**
> Everything needed to build it is in [`docs/`](docs/). Start with [`docs/README.md`](docs/README.md).

---

## What it does

- **One process, one WebSocket, one tick recorder.**
- **Trades options only** (index + stock). Equity and futures are subscribed for reference and
  recording — never traded.
- **08:55** — feed connects, recorder starts, all 50 Nifty stocks + index spots subscribed.
- **09:00–09:08** — the NSE equity pre-open auction is recorded in full.
- **09:09** — settlement snapshot → rank top-N gainers/losers (plus a buffer that is *subscribed, not
  traded*) → subscribe those option chains + NIFTY / BANKNIFTY / SENSEX.
- **09:14** — manual-entry cutoff; instrument set frozen.
- **09:15** — first positive-difference tick fires a marketable limit entry.
- **Exits** — Stop Loss · Target · Trailing SL · Trailing Target · Time · EOD · manual (broker or API).
  Each independently toggleable with its own values.
- **Paper / live** is a single config switch. Everything else is configurable too.
- **Backend only.** The frontend is a separate client consuming REST + WebSocket.
- **Broker-agnostic.** `data_broker` (zerodha | upstox) and `trade_broker`
  (zerodha | upstox | paper) are chosen **independently** in config. Running data on
  Upstox with `trade_broker: paper` touches no Zerodha key at all — the clean way to
  test alongside a system already using that key.

## Three facts that shaped the design

1. **Order updates arrive on the same WebSocket as market data** (`on_order_update`) — real-time fills
   need no second connection and no polling. **But Kite has no position stream:** positions are derived
   from fills and reconciled against `GET /portfolio/positions`.
2. **NSE options do not trade in the pre-open.** The F&O pre-open (from Dec 2025) covers *futures only*.
   So an option's reference price is its **previous close**, and option chains are subscribed *after*
   the 09:09 settlement, when the correct ATM is finally known.
3. **Zerodha blocks MARKET orders on stock options.** Entries and exits are LIMIT-only, priced
   marketably from live depth, with an IOC re-price ladder and LPP-rejection retry.

---

## Documentation

| # | Doc | Answers |
|---|---|---|
| 1 | [Implementation Plan](docs/01_IMPLEMENTATION_PLAN.md) | **Why** — objective, verified broker/exchange facts, latency strategy, build phases, size budget |
| 2 | [System Design & Interfaces](docs/02_SYSTEM_DESIGN_AND_INTERFACES.md) | **What** — architecture, modules, data schemas, broker facade, REST + WebSocket APIs |
| 3 | [Build Spec](docs/03_BUILD_SPEC.md) | **How** — 18 absolute rules, algorithms, test vectors, anti-patterns |
| 4 | [Developer Setup Guide](docs/04_DEVELOPER_SETUP_GUIDE.md) | **Where** — EC2, network, systemd, HTTPS without a domain, Vercel wiring, ops |

**Conflict rule:** for implementation details, the Build Spec wins.

---

## Repository layout

```
.
├── docs/                  # the four specification documents
├── config/
│   ├── config.example.json        # full annotated config — copy to config.json
│   └── credentials.example.json   # copy to credentials.json (gitignored, chmod 600)
├── backend/
│   ├── brokers/kite/      # broker facade: auth, ticker, orders, portfolio, instruments, quotes
│   ├── engine/            # scheduler, universe, feed, trigger, executor, positions, exits, recorder
│   └── api/               # FastAPI REST + WebSocket push
├── deploy/
│   ├── firsttick.service  # systemd unit
│   └── Caddyfile.example  # TLS reverse proxy (sslip.io — no domain needed)
├── tests/
└── requirements.txt
```

## Quick start (once code exists)

```bash
git clone https://github.com/Yokai-2510/tg_tl_first_tick_zerodha.git
cd tg_tl_first_tick_zerodha

python3.12 -m venv .venv && ./.venv/bin/pip install -r requirements.txt

cp config/config.example.json      config/config.json
cp config/credentials.example.json config/credentials.json
chmod 600 config/credentials.json          # then fill in your Kite credentials

./.venv/bin/python backend/main.py
```

Deployment (EC2 + public HTTPS with **no domain purchase**) is in
[docs/04_DEVELOPER_SETUP_GUIDE.md](docs/04_DEVELOPER_SETUP_GUIDE.md).

## Build order

Phases are defined in [docs/01_IMPLEMENTATION_PLAN.md §19](docs/01_IMPLEMENTATION_PLAN.md).
**Start with P2 — feed + recorder only.** It is read-only, safe to run alongside any existing algo,
and immediately starts banking the tick data that every later phase is validated against.

## Safety

- `config/credentials.json` is gitignored. **Never commit credentials.**
- Default `trading_mode.mode` is `paper`. Switch to `live` only after a paper soak.
- Kill switch and panic-flatten endpoints are always reachable.
- Broker limits enforced in code: 10 orders/s · 400/min · 5,000/day · 3,000 instruments · 3 WS connections.
