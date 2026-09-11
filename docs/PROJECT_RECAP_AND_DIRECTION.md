# Local Trading Lab — Project Recap and Direction

_Last updated for V28.10._

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
- **V28.9**: Research Command Center that combines Market State with a narrow three-family batch research runner.
- **V28.10**: Runtime Cycle that combines history refresh, coverage/gap checks, safe analysis, Market State snapshots, and guarded research preparation.

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
3. **Strategy Evidence** — narrow research batches, friction-aware backtests, lifecycle-gate study, cross-validation, promotion/rejection.
4. **Runtime Cycle** — one safe operation that keeps the first three modules fresh.

## V28.9 research protocol

First-pass strategy research remains deliberately narrow:

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
```

Default execution assumptions use the `binance_usdm_taker_light` friction preset rather than zero-cost research.

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
- Strategy-family mapping is heuristic until families are stored explicitly in strategy metadata.
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
-> narrow baseline backtest
-> lifecycle counterfactual study
-> exact replay only if justified
-> out-of-sample / cross-validation
-> paper validation
-> live execution only later
```

## Next roadmap

### V28.10

- Runtime cycle orchestration.
- Safe inline analysis with auto-paper disabled.
- Market State history snapshots.
- Guarded research preparation.
- Runtime Cycle Streamlit status page.

### Next after real data/results exist

- Review the first 12-month core-family evidence batch.
- Use V28.8 on saved runs.
- If lifecycle evidence is promising, build exact signal-path replay.
- Store explicit `strategy_family` metadata instead of relying on heuristic name mapping.

### Later

- Schedule the runtime cycle hourly/daily on the chosen runtime.
- Mobile-first dashboard refinement.
- Optional VPS deployment when 24/7 monitoring is useful.
