# V28.9 — Research Command Center

## Purpose

V28.9 simplifies the working product around the research loop:

```text
Data / History -> Market State -> Strategy Evidence
```

It does not add live execution logic and does not create new trading strategies.

## Market State dashboard

The new `Research Command Center` page combines:

- pair;
- lifecycle state;
- lifecycle direction and confidence;
- best lifecycle-fit strategy;
- number of fit-ready and blocked/conflicting opinions;
- allowed strategy families;
- lifecycle entry/exit guidance;
- 12-month history readiness and freshness.

The dashboard can still render when history or analysis is missing. Missing state is shown explicitly instead of being inferred.

## Core Research Runner

The runner intentionally narrows research to three families:

```text
trend_pullback
compression_breakout
range_reversion
```

Default first-pass pairs:

```text
BTCUSDT, ETHUSDT, SOLUSDT
```

Default research timeframes:

```text
entry: 1h
analysis: 4h
```

The backtest engine reads the partitioned local OHLCV store directly. The 1h store is sufficient as the base because higher timeframes can be resampled by the backtest engine.

## Default execution assumptions

The runner uses `binance_usdm_taker_light` assumptions by default:

- fee: 4 bps per side;
- slippage: 1 bp per side;
- spread: 1 bp round-trip model input;
- funding: zero unless explicitly stress-tested.

This is intentionally more conservative than zero-cost research.

## Strategy selection

The runner reads the latest saved strategy versions and maps them into the three core research families using the lifecycle family mapper. It selects one strategy per family by default. The UI can increase this to three, but more is not automatically better.

If no existing strategy maps to a family, the runner shows that gap. V28.9 does not create a strategy merely to populate an experiment matrix.

## Queue behavior

`Queue core research batch` creates the normal backtest worker job under `data/backtest_jobs` with:

- selected pairs;
- selected strategy families;
- 1h entry timeframe;
- 4h analysis timeframe;
- selected lookback window;
- realistic execution configuration;
- a `v28_9_core_research` run marker.

The existing worker remains responsible for execution. The UI only prepares and queues research work.

## Evidence rule

One positive run is not promotion evidence. A strategy should only move forward after enough trades, positive net expectancy after friction, acceptable drawdown, stability across symbols/periods, and follow-up cross-validation.

V28.8 lifecycle gate results are likewise evidence for whether exact gated replay is worth building; they are not permission to alter live/paper execution.
