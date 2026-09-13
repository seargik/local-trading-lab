# V28.12 — OHLCV Compression Breakout Benchmark

## Why this exists

V28.11 exposed a research mismatch: the existing compression/breakout strategies depend on historical open interest and/or order-book data, while the current long-history store contains OHLCV only.

Running those strategies across 12 months of OHLCV would therefore be misleading. V28.12 adds a deliberately simple benchmark whose full input can be reconstructed from stored candles.

## Benchmark role

`OHLCV Compression Breakout Benchmark` is a research control, not a promoted trading strategy.

It is marked `benchmark_only=true` in the strategy-family registry and is loaded directly into research jobs when the `compression_breakout` family otherwise has no historically replayable saved strategy. It is not automatically added to live or paper strategy slots.

## Inputs

The benchmark uses only features derived from OHLCV and higher-timeframe OHLCV:

- `breakout_above_n_bar_high`
- `breakout_below_n_bar_low`
- `compression_before_breakout`
- `volume_ratio`
- `breakout_close_strength`
- `htf_alignment`

`compression_before_breakout` already exists in the feature engine. It checks whether the prior bar's Bollinger-band width and ATR were below their recent 20-bar medians. Breakout flags compare the current close with the prior 20-bar high/low.

## Rule structure

Long and short sides each have a 100-point rule set. The action threshold is 70.

The strongest evidence is the actual 20-bar breakout. Prior compression and volume expansion add confirmation, while close strength and non-opposing HTF direction provide secondary confirmation.

Default research exit assumptions:

- Expected RR: `1:2`
- Exit family: `breakout_balanced`
- TP mode: `structure_atr`
- Stop multiplier: `1.0`

## Three-family protocol

With V28.12 the core research protocol can be structurally complete using the same historical data source:

1. `trend_pullback` — saved OHLCV-ready strategy from the explicit registry.
2. `compression_breakout` — OHLCV benchmark control when no replayable saved compression strategy exists.
3. `range_reversion` — saved OHLCV-ready strategy from the explicit registry.

Default research universe remains BTCUSDT, ETHUSDT and SOLUSDT, with 1h entry, 4h analysis, 365-day lookback and realistic Binance USD-M execution friction.

## What this does not prove

A positive benchmark result does not prove the compression strategy is production-ready. It only answers whether this simple compression-to-breakout concept deserves deeper work.

Promotion still requires sample-size review, stability by pair/period/side, realistic costs, out-of-sample validation, cross-validation and paper testing.

The richer compression strategies that use OI/order-book information remain blocked for long-history replay until those historical datasets exist.
