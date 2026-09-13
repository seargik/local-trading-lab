# V28.20 — Adaptive Evidence & Economic Viability

## Purpose

V28.20 moves TRAI from asking whether the Market State Identifier looks plausible to asking a harder question:

> Does the fixed Market State + Adaptive Router policy improve economic evidence relative to running the same strategy families statically?

This is still a research stage. It is designed to reject attractive-looking but economically weak adaptation before paper or live execution is considered.

## What V28.20 compares

The source is a completed controlled core-research batch containing the three families:

```text
trend_pullback
compression_breakout
range_reversion
```

For each family V28.20 loads a representative saved backtest. If more than one run exists for a family, the representative is chosen by explicit Strategy Family Registry priority and name, not by historical PnL. This avoids selecting the best historical winner after seeing the outcome.

The static baseline is the original friction-aware trade evidence produced by that family backtest.

The adaptive scenario then applies the fixed historical router state to each already-simulated trade:

```text
saved trade signal_time
        ↓
latest router decision_time <= signal_time
        ↓
state must be recent enough
        ↓
preferred family must match trade family
        ↓
router action must be TRADE_CANDIDATE
        ↓
router direction must match trade side
        ↓
include trade and apply router risk multiplier
```

The join is backward-only and therefore cannot use a future state for an earlier trade.

## Important limitation: this is counterfactual selection, not a full adaptive portfolio replay

V28.20 does **not** generate a new trade path from scratch. It filters and weights trades that were already generated independently by family backtests.

That is useful for testing whether the router selects better conditions, but it has important limitations:

- the strategy trade already exists before the router filter is applied;
- overlapping family trades may imply more capital than one account would have available;
- summed fixed-stake PnL is not a portfolio return;
- position sizing is a counterfactual router multiplier, not a complete account-level risk engine;
- missed fills, queueing, exchange outages and dynamic liquidity are not reproduced beyond the existing backtest friction model.

V28.20 therefore audits trade concurrency and reports when PnL aggregation may overstate deployable economics.

## Economic metrics

For static and adaptive evidence the lab reports:

```text
trades
win rate
total fixed-stake PnL
profit factor
expectancy bps per capital turn
expectancy R
max drawdown
max drawdown / net profit
capital turns
```

`expectancy_bps_per_capital_turn` is deliberately exposure-normalized:

```text
sum(PnL) / sum(stake deployed) × 10,000
```

This makes a router that simply takes fewer trades harder to praise merely because absolute drawdown or absolute PnL changed.

## Measuring WAIT

WAIT is treated as an economic decision, not a missing output.

For trades that had a valid historical state but were not selected by the router, V28.20 reports:

```text
avoided losing trades
avoided loss USD
missed winning trades
missed profit USD
excluded net PnL
WAIT value = -excluded net PnL
```

Interpretation:

- positive WAIT value: excluded trades lost money in aggregate;
- negative WAIT value: the router skipped profitable trades in aggregate.

A router that avoids losses but misses even more profit is not adding value.

## Friction stress

The adaptive trade set is stressed with additional round-trip execution friction:

```text
0 bps
5 bps
10 bps
20 bps
40 bps
```

This stress is added on top of the friction already present in the source backtests.

V28.20 also reports estimated break-even extra friction in basis points per capital turn. A strategy that becomes unattractive after only a few extra basis points is treated as fragile even if its historical PnL is positive.

## Stability checks

The adaptive result is checked by:

```text
pair
month
strategy family
```

Default policy requires at least:

```text
80 adaptive trades
2 active symbols
6 active months
67% profitable symbols
55% profitable months
<= 70% of adaptive trades from one family
profit factor >= 1.15
max drawdown / net profit <= 1.25
>= 8 bps extra-friction headroom
```

It must also show measurable uplift over the pooled static baseline through at least one of:

```text
profit-factor uplift >= 0.05
or
expectancy uplift >= 2 bps / capital turn
```

These are versioned research thresholds, not universal trading laws.

Policy:

```text
config/adaptive_evidence_policy.json
```

## Integrity gates

Historical economic evidence is blocked unless the underlying evidence is trustworthy.

### Timing integrity

Every representative family run must be marked:

```text
timing_integrity_version = 28.18
```

This ensures the historical backtest used closed-bar availability, causal pivots and no backward fill.

### Data integrity

The source OHLCV store is audited using the V28.19 continuity contract for each job symbol and analysis timeframe.

Promotion is blocked if the observed source window has:

```text
missing internal candles
unfinished candles
no usable rows
```

## Compression benchmark blocker

The current OHLCV-only compression strategy is intentionally marked `benchmark_only`.

If adaptive evidence uses that benchmark, even otherwise strong results are capped at:

```text
promising_research_only
```

This prevents a convenient OHLCV benchmark from being silently reinterpreted as a production-quality compression strategy.

## Verdicts

```text
no_adaptive_evidence
reject
insufficient_evidence
promising_research_only
adaptive_edge_candidate
```

### `reject`

Enough evidence exists and the adaptive result is economically negative, for example non-positive net PnL or profit factor below 1.0.

The correct next action is redesign or abandonment, not parameter polishing on the same sample.

### `insufficient_evidence`

The result does not provide enough robust evidence to justify advancement.

### `promising_research_only`

Positive evidence exists, but one or more robustness/promotion conditions fail, or a benchmark-only family participates.

### `adaptive_edge_candidate`

All current V28.20 historical gates pass and no benchmark-only family blocks promotion.

This means only:

> the fixed adaptive policy has earned the right to face stronger validation.

It does **not** mean the system will make money in the future.

## Reproducible evidence snapshots

Every UI analysis run writes a snapshot under:

```text
data/backtest_reviews/adaptive_evidence/
```

The snapshot includes:

```text
manifest.json
family_comparison.csv
pooled_comparison.csv
wait_analysis.csv
friction_stress.csv
stability_by_symbol.csv
stability_by_month.csv
annotated_trades.parquet
```

The manifest records the source research job and fingerprints the exact policies used:

```text
adaptive evidence policy
Market State Router policy
Market State Replay policy
```

Each policy receives a canonical SHA-256 fingerprint and the combined policy set receives its own hash. This is necessary so later validation can prove that the adaptive logic was not quietly changed after seeing the result.

## What V28.20 says about the goal of making money

A profitable system needs more than a clever state classifier. It needs an edge large enough to survive:

```text
fees
spread
slippage
model error
regime change
trade overlap / capital constraints
sample uncertainty
future unseen data
real execution
```

V28.20 therefore prefers rejecting a weak system to producing optimistic projected returns.

The strongest useful outcome at this stage is not “TRAI makes X%.” It is:

```text
the adaptive policy improves exposure-normalized expectancy
+ improves or preserves profit factor
+ survives extra friction
+ works across pairs and months
+ is not dominated by one family
+ uses causally valid data/timing
+ has enough observations
```

Only after that should the complete adaptive framework be frozen and tested chronologically and on genuinely future data.

## Recommended validation sequence after V28.20

```text
V28.19 integrity-safe historical data
→ V28.18 closed-bar backtests
→ V28.17 state replay diagnostics
→ V28.20 adaptive economic evidence
→ frozen adaptive walk-forward comparison
→ freeze exact strategies + router + evidence policy
→ V28.15-style genuinely future holdout
→ account-aware paper portfolio validation
→ constrained live execution only if all prior stages survive
```

## What remains before live-money credibility

The largest remaining methodological gap is an **exact capital-aware adaptive replay**. That future stage should simulate one shared account through time, resolve simultaneous candidates, enforce portfolio exposure/risk limits, and calculate a single causal equity curve rather than summing independent family results.

That is more valuable than adding more indicators or more strategies.
