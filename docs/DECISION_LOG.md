# Decision Log

## V28.4 — Git baseline

Decision: GitHub is the source of truth for code, docs, tests and version history.

Reason: zip patches were too risky and difficult to verify. Runtime market data remains outside Git.

## V28.5 — Demo mode and lifecycle fit

Decision: add synthetic demo data and lifecycle-to-strategy fit labels as a soft evidence layer.

Constraint: lifecycle fit is not a hard paper/live gate.

## V28.6 — Historical OHLCV backfill

Decision: preload Binance USD-M futures OHLCV into the local parquet store and refresh only recent history after the initial load.

Constraint: candles only. Funding, open interest, order book and liquidations are separate datasets.

## V28.7 — History Manager and project memory

Decision: add the Data / History Manager and stable project documentation.

Default universe:

```text
BTCUSDT, ETHUSDT, SOLUSDT, LTCUSDT, BNBUSDT, UNIUSDT, AAVEUSDT, XRPUSDT, TRXUSDT
```

Default history target:

```text
12mo, 1h and 4h
```

Decision: generated `data/ohlcv_store` parquet files are runtime data and must not be committed to Git.

## V28.8 — Lifecycle Gate Backtest Lab

Decision: do not place lifecycle gating directly into the live/paper signal path before evidence.

Validation ladder:

```text
counterfactual evidence
-> exact replay
-> out-of-sample validation
-> paper validation
-> only later consider live execution
```

## V28.9 — Research Command Center

Decision: stop broad strategy proliferation and create a narrow controlled research protocol.

Core families:

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

Decision: use realistic Binance USD-M friction assumptions rather than zero-cost research.

## V28.10 — Runtime Cycle

Decision: combine routine data refresh and safe analysis into one operation.

Default sequence:

```text
incremental OHLCV refresh
-> coverage/gap checks
-> safe inline analysis
-> Market State snapshot
-> runtime report
```

Decision: routine analysis must not silently enable execution.

```text
auto_paper_mode = false
live_bundle_mode = false
```

Research preparation remains opt-in and guarded.

## V28.11 — Explicit Strategy Family Registry

Decision: research-family assignment must use explicit registry metadata rather than name heuristics when research readiness matters.

Registry records:

```text
family
research inclusion
historical data requirements
historical readiness
priority
benchmark-only status where applicable
```

Finding: OHLCV is sufficient for the selected trend-pullback and range-reversion candidates, while existing compression/order-flow candidates require historical OI and/or order-book data.

## V28.12 — OHLCV Compression Breakout Benchmark

Decision: do not fake missing OI/order-book history and do not silently drop the compression family. Add one deliberately simple OHLCV-only benchmark instead.

Benchmark:

```text
OHLCV Compression Breakout Benchmark
```

Inputs are restricted to reproducible OHLCV-derived features such as prior compression, breakout, volume expansion, close strength and HTF alignment.

Decision: mark the benchmark `benchmark_only=true`.

Constraint: benchmark evidence is concept evidence, not an automatic production recommendation.

## V28.13 — Evidence Review / Research Scorecard

Decision: completed core-research runs must pass transparent evidence gates before more expensive validation.

Verdicts:

```text
reject
insufficient_evidence
promising
cross_validation_candidate
```

Policy is versioned in:

```text
config/research_evidence_policy.json
```

Main checks: sample size, net PnL after friction, expectancy R, profit factor, pair stability, month stability, drawdown and friction drag.

Decision: win rate remains descriptive rather than a universal promotion gate because different families can have different payoff structures.

Decision: benchmark-only strategies cannot receive direct production-oriented promotion status.

## V28.14 — Frozen Walk-Forward Validation

Decision: a non-benchmark `cross_validation_candidate` must use the exact same strategy payload across chronological holdouts.

Default approximate one-year pattern:

```text
train months 1-6  -> test months 7-8
train months 1-8  -> test months 9-10
train months 1-10 -> test months 11-12
```

Decision: hash the complete strategy payload and reject a validation run if it changes between folds.

Decision: include pair-transfer and LONG/SHORT diagnostics.

Important methodology constraint: V28.14 is a frozen temporal-stability test, not a pristine untouched future test when candidate selection used the same historical year.

Possible verdicts:

```text
pass_for_next_validation
mixed
insufficient_evidence
fail
invalid_test
```

A pass authorizes only stronger validation.

## V28.15 — Research Freeze + Fresh Holdout

Decision: only a V28.14 `pass_for_next_validation` may start the fresh-holdout clock.

Decision: freeze the exact candidate before future holdout data exists.

The freeze records strategy payload, execution/friction assumptions, symbols, timeframes, source run, policy versions, UTC cutoff and fixed future dates.

Integrity checks:

```text
strategy_sha256
record_sha256
```

Decision: the fresh holdout starts on the next UTC date after the freeze cutoff, not during the partially observed freeze day.

Default policy:

```text
target_holdout_days = 60
minimum_observation_days = 30
allow_preliminary_queue = false
```

Reason: fix the endpoint before outcomes are known rather than repeatedly peeking and stopping when results look favorable.

Decision: a failed holdout cannot be repaired by tuning against the same failed future window. Any redesign requires a new hash and new future-data clock.

A `fresh_holdout_pass` authorizes only a separate paper-validation stage.

## V28.16 — Market State Identifier + Adaptive Router

Decision: TRAI should adapt to the market rather than freeze itself into one always-on strategy.

The system now separates:

```text
Market State Identifier
-> what is the market doing now?

Adaptive Router
-> how should the research system behave in that state?
```

Market State exposes:

```text
lifecycle state
direction
trend strength
volatility
structure
HTF alignment
confidence
```

Adaptive Router exposes:

```text
preferred strategy family
route direction
TRADE / PREPARE / WAIT / PROTECT action
entry mode
exit family
research risk multiplier
reasons
```

Decision: the market response may adapt every bar, but the rules that define state and routing must remain versioned and fixed during a validation experiment.

Constraint: V28.16 remains research/display behavior and does not directly gate paper/live execution.

## V28.17 — Historical Market State Replay

Decision: validate the Market State Identifier historically before using routing profitability as evidence.

Replay must use only information that was closed and available at each historical decision time.

Diagnostics include:

```text
state distribution
state churn and dwell time
state transitions
confidence calibration
preferred family/action distribution
later directional agreement
no-lookahead audit
```

Decision: directional accuracy is a diagnostic for the state model, not a claim of trading profitability.

Constraint: V28.17 does not place orders or simulate an adaptive execution portfolio.

## V28.18 — Closed-Bar Timing Integrity

Decision: historical backtests must obey a strict information clock.

A candle becomes available only after it closes. Higher-timeframe context must also be closed before a lower-timeframe decision can use it.

Decision: remove two additional lookahead mechanisms from historical research:

```text
no backward fill of feature values
no centered swing pivots that require unobserved future bars
```

Centered pivots are replaced with causally confirmed pivots.

New backtests carry:

```text
timing_integrity_version = 28.18
closed_bar_only = true
```

Decision: pre-V28.18 backtests are legacy evidence and should be rerun before new promotion decisions.

No paper/live execution behavior changed.

## V28.19 — Historical Data Integrity / Backfill Hardening

Decision: harden the OHLCV store before comparing adaptive-router profitability.

Only fully closed Binance candles may be written by the historical backfill. Older unfinished tail rows are pruned. Update-only refresh overlaps recent candles so the tail can be replaced safely. Transient failures use retry/backoff. Consecutive `open_time` values are audited and internal gaps are repaired when possible.

Defaults include:

```text
overlap_bars = 2
max_retries = 4
retry_backoff_seconds = 0.5
repair_gaps = true
max_gap_repairs = 50
```

Decision: Data / History Manager readiness requires both coverage/freshness and continuity.

Decision: Runtime Cycle automatically benefits from hardened backfill defaults without changing paper/live execution.

## V28.20 — Adaptive Evidence & Economic Viability

Decision: stop treating a plausible Market State label as sufficient evidence. The adaptive system must demonstrate measurable economic improvement over static family baselines before stronger validation.

The V28.20 test is a causal counterfactual layer over already-simulated family trades:

```text
saved strategy signal
-> latest historical router state already available
-> require family/action/direction match
-> apply fixed router risk multiplier
-> compare selected evidence with static baseline
```

Decision: use backward-only state joins. A router decision after a strategy signal must never influence that earlier signal.

Decision: choose one representative saved run per family by explicit registry priority, not by historical PnL.

Decision: measure adaptation in exposure-normalized terms, not only absolute PnL. Report PF, expectancy per capital turn, drawdown, pair/month stability and family concentration.

Decision: treat `WAIT` as an economic decision and report avoided losses and missed profits separately.

Decision: stress the selected adaptive set with additional round-trip friction of 0/5/10/20/40 bps.

Decision: audit capital overlap. Summed fixed-stake PnL from overlapping independent strategies is not treated as deployable single-account equity.

Decision: promotion is blocked unless source runs carry V28.18 timing integrity and V28.19 source continuity passes.

Decision: benchmark-only compression participation caps the result at `promising_research_only`.

Decision: persist reproducible evidence snapshots with SHA-256 policy fingerprints.

## V28.21 — Shared-Account Adaptive Portfolio Replay

Decision: resolve V28.20's largest capital-realism gap before adding more strategy complexity.

Static and adaptive historical candidates must now compete for **one finite account** in chronological order.

Account chronology:

```text
close exits already due
-> realize PnL into current equity
-> rank candidates using only pre-entry information
-> apply shared capital/risk/exposure limits
-> accept/reject candidates
-> size accepted positions from current equity
```

Decision: process exits before entries at the same timestamp so released capital can be reused without artificial overlap.

Decision: future outcome must never influence same-time arbitration.

Static priority:

```text
strategy score
-> deterministic symbol/family/id tie-breakers
```

Adaptive priority:

```text
router confidence
-> router risk multiplier
-> strategy score
-> deterministic symbol/family/id tie-breakers
```

Decision: use stop-distance-aware risk sizing rather than fixed notional sizing.

Default account policy:

```text
starting_equity_usd = 10000
base_risk_per_trade_pct = 0.5
max_total_open_risk_pct = 1.5
max_position_notional_pct = 35
max_symbol_notional_pct = 35
max_gross_exposure_pct = 100
max_same_direction_exposure_pct = 70
max_concurrent_positions = 3
max_positions_per_family = 2
one_position_per_symbol = true
```

Decision: keep leverage/liquidation out of the model for now. The default account cannot exceed 100% gross notional. Leverage should not be added merely to magnify a weak edge.

Decision: approximate BTC/ETH/SOL correlation risk with a same-direction gross-exposure cap. This is explicitly weaker than a future covariance/factor model but materially better than treating concurrent crypto longs as independent.

Decision: maintain an explicit candidate rejection ledger. Capital/exposure-limited opportunities must be visible rather than silently omitted.

Decision: compare **static and adaptive under the same account constraints**. The adaptive layer must add value after finite capital is enforced, not only in a pooled independent-trade table.

Decision: rerun the complete account for 0/5/10/20/40 bps extra round-trip friction because higher costs change equity and therefore later compounded position sizes.

Decision: report one account equity curve, capital turnover, accepted/rejected trades, PF, realized drawdown, max gross exposure, max open risk, max same-direction exposure and pair/family/month contributions.

Important limitation: V28.21 drawdown is realized-equity drawdown at exits. It does not yet synchronize all open positions mark-to-market candle by candle. Intratrade account drawdown can therefore be worse.

Decision: preserve trade-level MAE as a diagnostic, but do not sum independent MAEs as if their worst moments occurred simultaneously.

Verdicts:

```text
invalid_evidence
no_portfolio_evidence
reject
static_baseline_better
insufficient_evidence
promising_research_only
shared_account_edge_candidate
```

Decision: `static_baseline_better` is a first-class failure of the adaptive architecture. More sophistication is not rewarded when it reduces economic value.

Decision: `shared_account_edge_candidate` authorizes only stronger frozen validation. It does not enable paper/live trading.

Decision: benchmark-only accepted trades continue to block production-oriented promotion.

Decision: save reproducible V28.21 snapshots under:

```text
data/backtest_reviews/shared_account_replays/
```

Snapshots fingerprint:

```text
shared-account policy
adaptive-evidence policy
market-state router policy
market-state replay policy
```

The exact adaptive + capital policy must be frozen before the next validation stage.

## Current strategic decision

Continue TRAI as a research/validation system. Keep execution changes frozen until the **complete adaptive portfolio** survives stronger chronological and genuinely future validation.

Keep:

- integrity-safe historical OHLCV;
- closed-bar historical timing;
- Market State + Adaptive Router research;
- explicit strategy-family registry;
- friction-aware evidence testing;
- shared-account capital/risk replay;
- conservative reject/retain gates;
- reproducible policy fingerprints;
- frozen walk-forward and future-holdout protocols;
- Runtime Cycle orchestration.

Freeze for now:

- uncontrolled strategy proliferation;
- leverage optimization;
- live execution changes;
- opaque LLM trading decisions;
- complexity that is not justified by evidence.

Current research ladder:

```text
V28.19 trustworthy OHLCV
-> V28.18 closed-bar timing
-> V28.16 Market State Identifier
-> V28.17 historical state replay
-> controlled static family baselines
-> V28.20 adaptive economic evidence
-> V28.21 shared-account portfolio replay
-> freeze complete strategy/router/capital policy
-> portfolio-level frozen walk-forward
-> genuinely future holdout
-> frozen shared-account paper validation
-> constrained live execution only later
```

Next major development should validate the whole frozen adaptive portfolio across time rather than add another collection of strategies.
