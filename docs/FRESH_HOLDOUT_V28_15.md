# V28.15 — Research Freeze + Fresh Holdout Protocol

## Purpose

V28.14 tests whether a frozen strategy is stable across chronological folds and pairs inside the already observed research history. That is useful, but it is not a pristine future holdout because the strategy family was selected after seeing the 12-month research set.

V28.15 adds a stricter stage:

```text
V28.14 pass
-> freeze exact candidate
-> wait for market data that did not exist at freeze time
-> run one fixed holdout window
-> evaluate without retuning
```

## Freeze record

A freeze captures and hashes:

- complete strategy payload;
- execution/friction assumptions;
- symbols;
- entry and analysis timeframes;
- source run and V28.14 validation id;
- V28.13 evidence-policy version;
- V28.14 walk-forward-policy version;
- V28.15 holdout-policy version;
- UTC freeze cutoff;
- fixed future holdout start and end dates.

Two SHA-256 checks are stored:

```text
strategy_sha256
record_sha256
```

If the strategy payload or freeze record changes, the holdout is invalid rather than silently reinterpreted.

Freeze files are runtime research artifacts under:

```text
data/backtest_reviews/research_freezes/
```

They are intentionally not committed to Git.

## Future-data boundary

The freeze cutoff can occur at any UTC time. V28.15 does not use the remainder of that UTC day as unseen data.

Instead:

```text
freeze cutoff: 2026-09-13 09:30 UTC
first eligible holdout date: 2026-09-14 UTC
```

This sacrifices part of one day but removes ambiguity about candles that were partially observable before the freeze.

## Fixed window

Default policy:

```text
target_holdout_days = 60
minimum_observation_days = 30
allow_preliminary_queue = false
```

The target end date is calculated at freeze time and does not move later because a later end date happens to produce a better result.

The UI may show progress after 30 days, but by default it does not enable evaluation until the complete 60-day window exists in both calendar time and the local OHLCV store.

This avoids repeated peeking and cherry-picking the evaluation endpoint.

## Data readiness

Before the holdout job can be queued, V28.15 requires:

1. freeze-record integrity to pass;
2. the full future calendar window to have elapsed;
3. the local entry-timeframe OHLCV store for every frozen symbol to extend through the fixed target end date.

The existing incremental history updater should be used to keep that store current.

## Evaluation

The holdout job uses the exact frozen strategy payload and execution config. It runs separately for every frozen symbol over the same fixed future dates.

The result checks:

```text
freeze integrity
saved-result strategy hash
saved-result freeze-record hash
fixed-window match
sample size
net PnL after friction
expectancy R
profit factor
pair stability
drawdown vs net profit
friction drag
```

Default verdicts:

```text
fresh_holdout_pass
fresh_holdout_mixed
fresh_holdout_fail
insufficient_evidence
invalid_freeze_or_test
```

A failure should not be repaired by tuning on the holdout. Any redesign creates a new strategy hash, a new freeze, and a new future-data clock.

## Default evidence policy

`config/fresh_holdout_policy.json` currently requires approximately:

```text
>= 20 total fresh trades
positive net PnL
positive expectancy R
profit factor >= 1.05
>= 67% of frozen pairs profitable
max drawdown <= 1.5x net profit
friction drag <= 65% of positive pre-friction PnL
```

These are versioned research gates, not universal trading truths.

## UI

Streamlit page:

```text
Fresh Holdout Protocol
```

The page has three stages:

```text
1. Freeze a V28.14 survivor
2. Watch the future-data clock and per-symbol freshness
3. Queue/evaluate the fixed holdout once ready
```

## Interpretation

A `fresh_holdout_pass` is materially stronger evidence than a good historical backtest or same-history walk-forward result because the evaluated candles did not exist when the candidate was frozen.

It is still not permission for live execution.

Recommended next step after a pass:

```text
fresh holdout pass
-> paper validation with the same frozen strategy
-> operational monitoring / slippage comparison
-> only later consider constrained live execution
```
