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

## V28.11 — Explicit Strategy Family Registry

Decision: research-family assignment must use explicit registry metadata rather than strategy-name heuristics when deciding research readiness.

Registry records:

```text
strategy family
research inclusion
historical-data requirements
historical readiness
priority
```

Reason: a strategy should not enter a controlled family comparison merely because its name happens to resemble that family.

Finding: the saved trend-pullback and range-reversion candidates can be replayed from OHLCV, while the existing compression candidates require historical OI and/or order-book inputs.

## V28.12 — OHLCV Compression Breakout Benchmark

Decision: do not fake missing derivatives/order-book history and do not drop the compression family from the comparison. Add one explicit OHLCV-only benchmark control instead.

Benchmark:

```text
OHLCV Compression Breakout Benchmark
```

Inputs:

```text
20-bar breakout
prior-bar compression vs recent BB/ATR medians
volume ratio
breakout close strength
HTF OHLCV alignment
```

Decision: mark the benchmark `benchmark_only=true` and load it directly into research jobs when compression otherwise has no replayable saved strategy.

Constraint: the benchmark is not automatically saved into live/paper strategy slots and is not a production recommendation.

Decision: the three-family protocol should only queue when every selected family has an explicitly classified, historically replayable candidate.

Default controlled protocol remains:

```text
BTCUSDT / ETHUSDT / SOLUSDT
1h entry / 4h analysis
365 days
realistic Binance USD-M friction
trend_pullback vs compression_breakout vs range_reversion
```

## Current strategic decision

Continue the project, but simplify aggressively.

Keep:

- Historical data store and incremental refresh.
- Backtest/replay foundation.
- Trend lifecycle router and fit labels.
- Explicit strategy-family registry.
- Friction-aware evaluation.
- Cross-validation/promotion workflow.
- Runtime-cycle orchestration.

Freeze for now:

- New strategy proliferation outside controlled research needs.
- Live execution changes.
- More complex bundle logic.
- LLM-generated trading decisions.

Focus:

```text
historical coverage -> controlled three-family baseline -> reject/retain -> lifecycle evidence -> cross-validation -> paper validation
```
