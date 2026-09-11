# Decision Log

## V28.4 — Git baseline

Decision: move the project to GitHub as the source of truth.

Reason: zip patches became too risky and hard to verify. GitHub gives branch history, PRs, Actions checks, Codespaces preview, and rollback.

## V28.5 — Demo mode and lifecycle fit

Decision: add synthetic demo data and lifecycle-to-strategy fit labels as a soft evidence layer.

Constraint: lifecycle fit is not a hard live/paper gate.

## V28.6 — Historical OHLCV backfill

Decision: preload Binance USD-M futures OHLCV into the local parquet store and later update only the fresh missing part.

Constraint: candles only; funding/open interest/order book/liquidations are separate future datasets.

## V28.7 — History Manager and project memory

Decision: add Data / History Manager UI and stable project recap docs.

Default target set:

```text
BTCUSDT, ETHUSDT, SOLUSDT, LTCUSDT, BNBUSDT, UNIUSDT, AAVEUSDT, XRPUSDT, TRXUSDT
```

Default requested history:

```text
12mo, 1h and 4h
```

Decision: do not commit generated `data/ohlcv_store` parquet files to Git.

## V28.8 — Lifecycle Gate Backtest Lab

Decision: do not place lifecycle gating directly into the backtest/live signal path yet.

Promotion ladder:

```text
promising counterfactual result
-> exact signal-path replay
-> out-of-sample validation
-> paper-only gate
-> only later consider live execution
```

## V28.9 — Research Command Center

Decision: stop broadening the strategy surface and create a narrow evidence runner.

First-pass families:

```text
trend_pullback
compression_breakout
range_reversion
```

First-pass symbols:

```text
BTCUSDT, ETHUSDT, SOLUSDT
```

Default research timeframes:

```text
entry: 1h
analysis: 4h
```

Default execution assumptions: `binance_usdm_taker_light`, not zero friction.

## V28.10 — Runtime Cycle

Decision: combine routine maintenance and analysis into one orchestrated cycle.

Default sequence:

```text
incremental OHLCV update
-> coverage summary
-> gap audit
-> safe inline analysis
-> Market State snapshot
-> runtime report
```

Decision: inline analysis forces:

```text
auto_paper_mode = false
live_bundle_mode = false
```

Reason: routine refresh should update research state without silently changing paper/live execution behavior.

Decision: core research preparation remains opt-in and guarded.

Guardrails:

- require approximately 95% BTC/ETH/SOL 1h and 4h coverage when configured;
- do not queue a duplicate core research job;
- do not auto-promote any strategy;
- do not place exchange orders.

Decision: persist operational history under:

```text
data/runtime_cycles/
data/market_state_history/
```

and ignore those runtime artifacts in Git.

## Current strategic decision

Continue the project, but simplify aggressively.

Keep:

- Historical data store and incremental refresh.
- Backtest/replay foundation.
- Trend lifecycle router and fit labels.
- Friction-aware evaluation.
- Cross-validation/promotion workflow.
- Runtime-cycle orchestration.

Freeze for now:

- New strategy proliferation.
- Live execution changes.
- More complex bundle logic.
- LLM-generated trading decisions.

Focus:

```text
historical coverage -> narrow research batch -> lifecycle evidence -> cross-validation -> paper validation
```
