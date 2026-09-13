# Local Trading Lab — Project Recap and Direction

_Last updated for V28.16._

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
- **V28.16**: first-class Market State Identifier plus an explainable Adaptive Router that maps changing market state to preferred strategy family, direction, entry/exit behavior and research risk multiplier.

## Current architecture

```text
GitHub
  -> code, docs, smoke tests, PR review

Runtime host: local PC / Codespaces / future VPS
  -> Streamlit UI
  -> runtime cycle orchestration
  -> collector/analyzer/backtest workers
  -> data/ohlcv_store monthly parquet partitions

Binance historical / fresh klines
  -> backfill/update-only
  -> local OHLCV store
  -> analysis and backtests

Market State Identifier
  -> lifecycle state
  -> direction
  -> trend strength
  -> volatility
  -> structure
  -> HTF alignment
  -> explainable confidence

Adaptive Router
  -> preferred strategy family
  -> route direction
  -> TRADE / PREPARE / WAIT / PROTECT behavior
  -> entry / exit family
  -> research risk multiplier

Strategy Family Registry
  -> explicit research family
  -> historical data requirements
  -> benchmark-only controls where needed

Validation
  -> V28.13 Evidence Review
  -> V28.14 frozen walk-forward stability
  -> V28.15 freeze exact adaptive candidate/policy
  -> genuinely future fixed holdout
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

TRAI is currently best described as:

```text
A local crypto market-state and strategy-validation lab.
```

The intended behavior is adaptive rather than one-strategy-fits-all:

```text
market data
-> Market State Identifier
-> Adaptive Router
-> strategy family / direction / entry / exit / risk / WAIT
-> evidence and validation
```

The system should be allowed to change behavior when the market changes, while the **rules that define market state and adaptation remain fixed and auditable during validation**.

## Simplified product shape

1. **Data / History** — coverage, freshness, gap audit, backfill, update-only refresh.
2. **Market State** — current trend/lifecycle, direction, trend strength, volatility, structure, HTF alignment and confidence.
3. **Adaptive Router** — map current state to preferred strategy family, route direction, entry/exit behavior and research risk.
4. **Strategy Evidence** — explicit family registry, narrow research batches, friction-aware backtests and lifecycle studies.
5. **Evidence Review** — conservative scorecard that rejects weak evidence early instead of encouraging endless tuning.
6. **Walk-Forward Validation** — frozen chronological stability and pair-transfer tests for survivors.
7. **Fresh Holdout** — tamper-evident freeze and a fixed future-data validation window that did not exist at candidate-selection time.
8. **Runtime Cycle** — one safe operation that keeps data and market-state research fresh.

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

## V28.16 Market State + Adaptive Router

V28.16 formalizes current market state as a first-class object. Per analyzed pair it exposes:

```text
lifecycle state
direction
trend strength
volatility state
market structure
HTF alignment
confidence
preferred strategy family
router action
route direction
risk multiplier
entry mode
exit family
reasons
```

The default adaptive research policy is versioned in:

```text
config/market_state_router_policy.json
```

Examples:

```text
compression_building -> prepare compression_breakout, no trade yet
breakout_attempt      -> compression_breakout candidate if direction/confidence are clear
trend_entering        -> prepare trend_pullback
trend_pullback_entry  -> trend_pullback candidate
trend_running         -> wait for next pullback instead of chasing
range_chop            -> range_reversion only at the range edge
trend_extended_late   -> protect / wait
trend_exhaustion      -> wait for reversal confirmation
panic_volatility      -> wait
```

The confidence/risk mapping is transparent and versioned. It is not a learned black-box probability. Risk remains zero unless the router has an actual trade candidate.

V28.16 is still a **research/display layer**. It does not yet gate paper/live execution.

## V28.12 compression control

The richer compression strategies currently depend on historical open interest and/or order-book data, which the long-history store does not contain.

V28.12 therefore adds `OHLCV Compression Breakout Benchmark` as a `benchmark_only` research control. It is not automatically placed in live or paper strategy slots.

## V28.13 evidence policy

Evidence Review thresholds are stored in `config/research_evidence_policy.json` and focus on sample size, positive net expectancy after friction, profit factor, drawdown, pair stability, month stability and friction drag.

Benchmark-only strategies can provide promising concept evidence, but V28.13 blocks them from direct production-oriented cross-validation candidate status.

## V28.14 walk-forward policy

Walk-forward thresholds are versioned in `config/walk_forward_policy.json`.

For an approximately 12-month source run the default fold design is:

```text
train months 1-6  -> test months 7-8
train months 1-8  -> test months 9-10
train months 1-10 -> test months 11-12
```

The complete strategy payload is hashed and must remain unchanged across validation folds. V28.14 changes only chronological test windows and metadata; it does not retune parameters between folds.

V28.14 is a strong temporal-stability test but not a pristine untouched future holdout because V28.13 selected the candidate after seeing the same 12-month research set.

## V28.15 fresh holdout policy

V28.15 freezes exact research state and waits for future data that did not exist at freeze time. The first eligible holdout date is the next UTC date after the freeze cutoff.

Default policy in `config/fresh_holdout_policy.json` uses a fixed 60-day holdout. A 30-day observation milestone is reported, but preliminary evaluation is disabled by default. The endpoint is fixed at freeze time to avoid repeated peeking or cherry-picking.

For the adaptive system, a future freeze must eventually include not only individual strategy payloads but also the exact **market-state and routing policy versions** used to select behavior.

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

Research preparation remains opt-in and guarded. Safe inline analysis keeps `auto_paper_mode = false` and `live_bundle_mode = false`.

## What is experimental

- V28.16 market-state confidence and risk multipliers are explicit hypotheses to validate, not universal truths.
- Lifecycle/market-state routing is not yet a hard paper/live execution gate.
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
- Market-state routes that only work on one pair or period.
- Results before execution friction.
- Strategy selection from too few trades.
- Walk-forward results where the frozen payload/policy changed between folds.
- A holdout whose strategy/config/router/window changed after its freeze.
- Any live execution behavior that has not passed historical, adaptive-router, fresh-holdout and paper validation.

## Validation ladder

```text
historical coverage
-> current Market State Identifier
-> adaptive-router historical replay
-> three-family baseline evidence
-> V28.13 Evidence Review
-> reject weak behavior early
-> V28.14 frozen walk-forward
-> freeze strategy + market-state/router policy
-> genuinely future fixed holdout
-> paper validation without retuning
-> live execution only later
```

## Immediate next step after real history is available

- Keep the Runtime Cycle refreshing history and current market-state analysis.
- Use the new Market State page as the quick answer to what each pair is doing now.
- Replay the V28.16 router historically before allowing it to become a paper/live gate.
- Run the controlled three-family evidence batch on BTC/ETH/SOL.
- Let V28.13 reject weak evidence.
- Queue V28.14 only for genuine non-benchmark survivors.
- Freeze the complete adaptive decision framework for future V28.15 holdout validation.

## Later

- Add frozen paper-validation only after the adaptive router survives historical, walk-forward and fresh-holdout evidence.
- Add historical OI/funding only if evidence suggests the richer derivatives strategies are worth the extra data complexity.
- Schedule the runtime cycle hourly/daily on the chosen runtime.
- Mobile-first dashboard refinement.
- Optional VPS deployment when 24/7 monitoring is useful.
