# V28.10 — Runtime Cycle

V28.10 turns the current research stack into one operational cycle without changing live execution behavior.

## Purpose

The runtime cycle combines:

```text
update missing 12-month OHLCV
-> summarize history coverage
-> audit candle gaps
-> run one safe analysis pass
-> save a timestamped Market State snapshot
-> save a runtime-cycle report
```

The default universe is BTCUSDT, ETHUSDT, SOLUSDT, LTCUSDT, BNBUSDT, UNIUSDT, AAVEUSDT, XRPUSDT and TRXUSDT on 1h and 4h history with a 12-month lookback.

Core strategy research remains narrower: BTCUSDT/ETHUSDT/SOLUSDT and the trend_pullback, compression_breakout and range_reversion families.

## Safe analysis mode

The inline analysis helper explicitly uses:

```text
auto_paper_mode = false
live_bundle_mode = false
```

It refreshes local scanner/analysis state but does not place exchange orders and does not enable live execution.

## Runtime outputs

Cycle reports are written under:

```text
data/runtime_cycles/
```

Market State history is written under:

```text
data/market_state_history/
```

Both locations are runtime artifacts and should remain outside Git.

## Research guardrails

Research queuing is opt-in. Before a core research batch is queued, the runtime cycle can require approximately 95% coverage for BTC/ETH/SOL on both 1h and 4h, and it refuses to create a duplicate batch when another core research job is already queued or running.

## What V28.10 does not do

It does not place trades, enable live execution, auto-promote strategies, turn lifecycle fit into a hard execution gate, or add another database.

The intended architecture remains:

```text
GitHub = code / docs / CI
local PC, Codespaces, or future VPS = runtime
Parquet under data/ohlcv_store = persistent market history
```

The same runtime cycle can later be scheduled on a local machine or VPS once 24/7 monitoring is actually useful.
