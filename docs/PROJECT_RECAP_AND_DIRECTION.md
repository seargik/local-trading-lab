# Local Trading Lab — Project Recap and Direction

_Last updated for V28.15._

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
- **V28.13**: Evidence Review / Research Scorecard that converts completed research batches into conservative reject/retain/cross-validation decisions.
- **V28.14**: frozen-payload walk-forward validation with anchored chronological holdouts, symbol stability, LONG/SHORT diagnostics and pair-transfer checks.
- **V28.15**: tamper-evident research freeze plus a fixed future-data holdout that starts only after the freeze cutoff.

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

Completed core backtests
  -> V28.13 Evidence Review
  -> V28.14 frozen walk-forward stability test
  -> V28.15 freeze exact candidate
  -> wait for genuinely future candles
  -> fixed fresh holdout
  -> paper validation only after a pass
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
Data / History -> Market State -> Strategy Evidence -> Evidence Review -> Walk-forward -> Fresh Holdout -> Paper validation -> possible live execution later
```

## Simplified product shape

1. **Data / History** — coverage, freshness, gap audit, backfill, update-only refresh.
2. **Market State** — lifecycle, direction, confidence, allowed families, current fit.
3. **Strategy Evidence** — explicit family registry, narrow research batches, friction-aware backtests and lifecycle studies.
4. **Evidence Review** — conservative scorecard that rejects weak evidence early instead of encouraging endless tuning.
5. **Walk-Forward Validation** — frozen-payload chronological stability and pair-transfer tests for V28.13 survivors.
6. **Fresh Holdout** — tamper-evident freeze and a fixed future-data validation window that did not exist at candidate-selection time.
7. **Runtime Cycle** — one safe operation that keeps data and market-state research fresh.

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

V28.12 therefore adds `OHLCV Compression Breakout Benchmark` as a `benchmark_only` research control. It is not automatically placed in live or paper strategy slots.

### V28.13 evidence policy

Evidence Review does not use a hidden model score. Its thresholds are stored in `config/research_evidence_policy.json` and focus on sample size, positive net expectancy after friction, profit factor, drawdown, pair stability, month stability and friction drag.

Benchmark-only strategies can provide promising concept evidence, but V28.13 blocks them from direct production-oriented cross-validation candidate status.

### V28.14 walk-forward policy

Walk-forward thresholds are versioned in `config/walk_forward_policy.json`.

For an approximately 12-month source run the default fold design is:

```text
train months 1-6  -> test months 7-8
train months 1-8  -> test months 9-10
train months 1-10 -> test months 11-12
```

The complete strategy payload is hashed and must remain unchanged across validation folds. V28.14 changes only chronological test windows and metadata; it does not retune parameters between folds.

Important caveat: V28.14 is a strong temporal-stability test but not a pristine untouched future holdout because V28.13 selected the candidate after seeing the same 12-month research set.

### V28.15 fresh holdout policy

V28.15 closes that methodology gap. Only a V28.14 `pass_for_next_validation` can be frozen.

The freeze records:

```text
strategy payload + SHA-256
execution/friction config
symbols and timeframes
source run and V28.14 id
policy versions
UTC cutoff
fixed future start/end dates
whole-record SHA-256
```

The first eligible holdout date is the next UTC date after the freeze cutoff, so no partially observed freeze-day candle can leak into the future sample.

Default policy in `config/fresh_holdout_policy.json` uses a fixed 60-day holdout. A 30-day observation milestone is reported, but preliminary evaluation is disabled by default. The endpoint is fixed at freeze time to avoid repeated peeking or cherry-picking.

Fresh-holdout runtime artifacts stay under:

```text
data/backtest_reviews/research_freezes/
data/backtest_reviews/fresh_holdout_scorecards/
```

and remain ignored by Git.

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

Research preparation remains opt-in and is guarded by history readiness and duplicate-job checks. Safe inline analysis keeps `auto_paper_mode = false` and `live_bundle_mode = false`.

## What is experimental

- Lifecycle fit remains evidence, not a hard paper/live rule.
- V28.8 is post-trade counterfactual filtering, not exact signal-path replay.
- The OHLCV compression benchmark is a research control, not evidence of production alpha.
- V28.13–V28.15 thresholds are versioned research policy, not universal market truths.
- V28.14 same-history walk-forward is not equivalent to a never-seen future holdout.
- A V28.15 fresh-holdout pass is stronger evidence but is still not live-trading permission.
- Historical OHLCV contains candles only; funding, open interest, liquidations and order-book history are separate datasets.
- Runtime scheduling is not yet persistent/24x7; V28.10 is an on-demand cycle.

## What should not be trusted yet

- Universal score thresholds.
- One-window backtest winners.
- Lifecycle gates that only improve one pair or period.
- Results before execution friction.
- Strategy selection from too few trades.
- Walk-forward results where the strategy payload changed between folds.
- A holdout whose strategy/config/window changed after its freeze.
- Any live execution behavior that has not passed historical, fresh-holdout and paper validation.

## Validation ladder

```text
historical coverage
-> three-family baseline backtest
-> V28.13 Evidence Review
-> reject weak families early
-> V28.14 frozen walk-forward stability
-> V28.15 freeze exact survivor
-> genuinely future fixed holdout
-> paper validation without retuning
-> live execution only later
```

## Immediate next step after real history is available

- Run the first 12-month V28.12 three-family evidence batch on BTC/ETH/SOL.
- Let V28.13 reject weak evidence.
- Queue V28.14 only for genuine non-benchmark `cross_validation_candidate` results.
- Freeze only V28.14 `pass_for_next_validation` survivors in V28.15.
- Keep the incremental history updater running so future holdout data accumulates naturally.
- Do not edit a frozen strategy while its future-data clock is running; a redesign creates a new freeze and a new clock.

## Later

- Add a paper-validation protocol only after a V28.15 pass exists.
- Add historical OI/funding only if evidence suggests the richer derivatives strategies are worth the extra data complexity.
- Schedule the runtime cycle hourly/daily on the chosen runtime.
- Mobile-first dashboard refinement.
- Optional VPS deployment when 24/7 monitoring is useful.
