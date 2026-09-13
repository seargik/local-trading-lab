# Local Trading Lab — Project Recap and Direction

_Last updated for V28.12._

## Source of truth

GitHub is the canonical project memory and code history. ChatGPT conversations are useful for reasoning and iteration, but project state should be stored in versioned repository files.

Repository:

```text
seargik/local-trading-lab
```

## Current status

- **V28.4**: repo-ready baseline, GitHub workflow, Codespaces/devcontainer, trend lifecycle scaffold.
- **V28.5**: demo mode, synthetic sample data, lifecycle-to-strategy fit labels, remote testing docs.
- **V28.6**: historical OHLCV backfill engine and incremental update CLI.
- **V28.7**: Data / History Manager, default 12-month history target set, recap and decision log.
- **V28.8**: Lifecycle Gate Backtest Lab for trade-level counterfactual validation before any execution gating.
- **V28.9**: Research Command Center with Market State and a narrow three-family batch research runner.
- **V28.10**: Runtime Cycle for history refresh, coverage/gap checks, safe analysis, Market State snapshots, and guarded research preparation.
- **V28.11**: explicit Strategy Family Registry and research-readiness audit.
- **V28.12**: OHLCV-only Compression Breakout Benchmark so all three core families can be replayed on the same historical data source.

## Current architecture

```text
GitHub
  -> code, docs, smoke tests, PR review

Runtime host: local PC / Codespaces / future VPS
  -> Streamlit UI
  -> runtime cycle orchestration
  -> collector/analyzer/backtest workers
  -> data/ohlcv_store monthly parquet partitions

Binance historical klines
  -> backfill/update-only
  -> local OHLCV store
  -> analysis and backtests

Strategy Family Registry
  -> explicit research family
  -> historical data requirements
  -> benchmark-only controls where needed

Saved backtests
  -> lifecycle counterfactual lab
  -> cross-validation / promotion review
```

## Data policy

Historical market data is runtime data, not source code. Generated parquet history stays under `data/ohlcv_store` and is ignored by Git.

Default requested history universe:

```text
BTCUSDT, ETHUSDT, SOLUSDT, LTCUSDT, BNBUSDT, UNIUSDT, AAVEUSDT, XRPUSDT, TRXUSDT
```

Default history target:

```text
12 months, 1h and 4h
```

## Product direction

This is not positioned as an AI trading bot yet.

Better positioning:

```text
A local crypto market-state and strategy-validation lab.
```

Core workflow:

```text
Data / History -> Market State -> Strategy Evidence -> Cross-validation -> Paper validation -> possible live execution later
```

## Simplified product shape

1. **Data / History** — coverage, freshness, gap audit, backfill, update-only refresh.
2. **Market State** — lifecycle, direction, confidence, allowed families, current fit.
3. **Strategy Evidence** — explicit family registry, narrow research batches, friction-aware backtests, lifecycle-gate study, cross-validation, promotion/rejection.
4. **Runtime Cycle** — one safe operation that keeps the first three modules fresh.

## Core research protocol

Research remains deliberately narrow:

```text
trend_pullback
compression_breakout
range_reversion
```

Default first-pass symbols:

```text
BTCUSDT, ETHUSDT, SOLUSDT
```

Default research timeframes:

```text
entry: 1h
analysis: 4h
lookback: 365 days
```

Default execution assumptions use the `binance_usdm_taker_light` friction preset rather than zero-cost research.

### V28.12 compression control

The richer compression strategies currently depend on historical open interest and/or order-book data, which the long-history store does not contain.

V28.12 therefore adds:

```text
OHLCV Compression Breakout Benchmark
```

It is `benchmark_only`, uses only OHLCV-derived breakout/compression/volume/HTF features, and is injected into research jobs only when the compression family otherwise has no historically replayable saved strategy. It is not automatically placed in live or paper strategy slots.

## V28.10 operating model

Default runtime sequence:

```text
incremental history refresh
-> coverage summary
-> gap audit
-> safe inline analysis
-> timestamped Market State snapshot
-> runtime report
```

Research preparation remains opt-in and is guarded by history readiness and duplicate-job checks.

Safe inline analysis explicitly keeps:

```text
auto_paper_mode = false
live_bundle_mode = false
```

## What is experimental

- Lifecycle fit remains evidence, not a hard paper/live rule.
- V28.8 is post-trade counterfactual filtering, not exact signal-path replay.
- The OHLCV compression benchmark is a research control, not evidence of production alpha.
- Historical OHLCV contains candles only; funding, open interest, liquidations and order-book history are separate datasets.
- One successful run is not enough to promote a strategy.
- Runtime scheduling is not yet persistent/24x7; V28.10 is an on-demand cycle.

## What should not be trusted yet

- Universal score thresholds.
- One-window backtest winners.
- Lifecycle gates that only improve one pair or period.
- Results before execution friction.
- Strategy selection from too few trades.
- Any live execution behavior that has not passed historical and paper validation.

## Validation ladder

```text
historical coverage
-> three-family baseline backtest
-> per-pair / per-period stability review
-> lifecycle counterfactual study
-> exact replay only if justified
-> out-of-sample / cross-validation
-> paper validation
-> live execution only later
```

## Next roadmap

### Immediate next step after real history is available

- Run the first 12-month V28.12 three-family evidence batch on BTC/ETH/SOL.
- Review trade counts, net expectancy, profit factor, drawdown and long/short asymmetry by family.
- Reject weak families early rather than tuning them indefinitely.
- Use V28.8 lifecycle filtering only on families that show baseline promise.

### Later

- Add historical OI/funding only if evidence suggests the richer derivatives strategies are worth the extra data complexity.
- Schedule the runtime cycle hourly/daily on the chosen runtime.
- Mobile-first dashboard refinement.
- Optional VPS deployment when 24/7 monitoring is useful.
