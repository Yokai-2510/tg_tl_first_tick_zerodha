# Documentation Index

Read in this order.

| # | Document | Answers | Read if you are… |
|---|---|---|---|
| 1 | [01_IMPLEMENTATION_PLAN.md](01_IMPLEMENTATION_PLAN.md) | **Why** — objective, verified broker/exchange facts, what we adopt & drop from the rank-momentum codebase, latency strategy, build phases, size budget | deciding scope or reviewing the approach |
| 2 | [02_SYSTEM_DESIGN_AND_INTERFACES.md](02_SYSTEM_DESIGN_AND_INTERFACES.md) | **What** — architecture, thread model, module boundaries, every data schema, broker facade, REST + WebSocket APIs, frontend connection contract | building the backend or the frontend |
| 3 | [03_BUILD_SPEC.md](03_BUILD_SPEC.md) | **How** — 15 absolute rules, exact algorithms with pseudocode, test vectors with expected values, anti-patterns, implementation order | writing the code |
| 4 | [04_DEVELOPER_SETUP_GUIDE.md](04_DEVELOPER_SETUP_GUIDE.md) | **Where** — EC2 provisioning, network/security groups, systemd, public HTTPS without buying a domain, Vercel wiring, ops & troubleshooting | deploying or operating it |

**Conflict rule:** for implementation details, `03_BUILD_SPEC.md` wins.

---

## If you only read one page

- **Hot path rule:** the WebSocket callback never does I/O — no HTTP, no disk, no logging. It stamps
  `perf_counter_ns()`, enqueues, evaluates in memory, and returns in under 50 µs.
- **One connection:** market data *and* order updates share a single KiteTicker. There is no position
  stream — positions are derived from fills and reconciled against the positions API.
- **Options only.** Equity and futures are reference data.
- **Reference price for an option is its previous close**, because options do not trade in the pre-open.
- **LIMIT orders only for stock options** — Zerodha blocks MARKET there.
- **Buy limits round UP to tick size, sell limits round DOWN.** Getting this backwards means not filling.
- **Reconcile with the broker before arming entries** after any restart.

The full list is [03_BUILD_SPEC.md §0 — The 15 absolute rules](03_BUILD_SPEC.md).
