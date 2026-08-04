# Backend

Module layout and responsibilities are specified in
[docs/02_SYSTEM_DESIGN_AND_INTERFACES.md](../docs/02_SYSTEM_DESIGN_AND_INTERFACES.md) section 4.2,
with per-module line budgets. Implementation rules and algorithms are in
[docs/03_BUILD_SPEC.md](../docs/03_BUILD_SPEC.md).

```
brokers/kite/  auth, ticker, orders, portfolio, instruments, quotes, ratelimit
engine/        scheduler, universe, feed, trigger, executor, positions, exits, recorder
api/           server, routes, ws_push
```

Dependency rule (strict, no cycles):

```
api  ->  engine  ->  brokers/kite  ->  kiteconnect SDK
            |            |
            +--> config <-+
```
