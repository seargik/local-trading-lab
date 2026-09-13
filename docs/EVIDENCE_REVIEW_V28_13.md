# V28.13 — Evidence Review / Research Scorecard

## Purpose

V28.13 turns completed core-research backtests into a conservative decision table. It does not rerun the backtest and it does not let an LLM decide whether a strategy works.

The scorecard reads the saved outputs already produced by the backtest worker:

- `summary`
- `performance_by_symbol`
- `performance_by_month`
- `performance_by_side`
- saved strategy metadata / research family

## Verdicts

The page uses four research-triage verdicts:

```text
reject
insufficient_evidence
promising
cross_validation_candidate
```

These verdicts are not trading recommendations and do not alter live or paper execution.

## Policy is explicit

Thresholds live in:

```text
config/research_evidence_policy.json
```

The first policy version checks:

- sample size;
- positive net PnL after execution friction;
- positive expectancy in R;
- profit factor;
- pair stability;
- month stability;
- drawdown relative to net profit;
- friction drag relative to pre-friction PnL.

Win rate is displayed but intentionally is not a promotion gate because different strategy families can have different payoff structures.

## Conservative benchmark handling

The V28.12 OHLCV Compression Breakout Benchmark is marked `benchmark_only`.

Even if it passes the strongest evidence thresholds, V28.13 will not label it a direct cross-validation candidate for production. Its strongest verdict is `promising`, with promotion blocked and the next step framed as deeper concept research.

## Default thresholds

The initial policy is intentionally conservative rather than optimized to the first result set.

General review readiness:

```text
minimum trades for normal review: 50
minimum trades for negative/reject decision: 30
minimum trades for cross-validation candidate: 100
minimum active symbols: 2
minimum active months: 4
```

Promising evidence additionally requires positive net PnL and expectancy, profit factor >= 1.10, at least half of active pairs and months profitable, drawdown no more than 1.5x net profit, and friction drag no more than 60% of pre-friction PnL.

Cross-validation candidate tightens those requirements to profit factor >= 1.20, at least 67% of pairs and 60% of months profitable, drawdown no more than net profit, and friction drag no more than 50% of pre-friction PnL.

These thresholds are versioned research policy, not universal market truths. They can be changed later, but any change should be recorded rather than silently tuned to a result.

## Evidence Review page

The Streamlit page is:

```text
Evidence Review
```

It allows selection of a completed V28.9/V28.12 core-research batch and shows:

- family and strategy;
- source and benchmark flag;
- verdict;
- trade count;
- win rate;
- net expectancy;
- profit factor;
- net PnL and execution cost;
- max drawdown and drawdown/net-profit ratio;
- profitable-pair share;
- profitable-month share;
- long/short trade split;
- checks passed;
- warnings and next step.

Each family can then be inspected by pair, month and side.

## Persistence

A scorecard can be saved as CSV and JSON under:

```text
data/backtest_reviews/evidence_scorecards/
```

That folder is runtime research output and is already covered by the ignored `data/backtest_reviews/` tree.

## Research flow after V28.13

```text
historical data
-> controlled three-family baseline
-> Evidence Review
-> reject weak families early
-> cross-validation only for survivors
-> lifecycle-gate study only where baseline edge exists
-> paper validation later
```

The key principle is to reduce false confidence: a complicated app should be willing to conclude that a strategy has insufficient evidence or should be rejected.
