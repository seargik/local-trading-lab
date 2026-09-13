# V28.18 — Closed-Bar Timing Integrity

## Purpose

V28.18 hardens the historical backtest clock before adaptive-router profitability is evaluated.

The rule is simple:

> A historical decision may use a candle only after that candle has closed.

This applies to the entry timeframe, analysis timeframe, and every higher-timeframe context row.

## Why this was needed

The pre-V28.18 backtest engine aligned enriched rows by `open_time`. That creates two important historical risks:

1. a completed 4h/1d feature row could be selected at the candle's opening timestamp even though its high/low/close did not exist yet;
2. the shared feature builder contained two additional non-causal operations for historical work: backward filling and centered five-bar pivot detection.

Those behaviors can make a historical result look cleaner than a decision that could actually have been made at the time.

## V28.18 contract

For historical backtests:

```text
source candle opens
        ↓
OHLCV evolves
        ↓
candle closes
        ↓
feature row becomes available
        ↓
strategy may evaluate it
        ↓
next-bar entry logic may proceed
```

A 4h candle opening at 08:00 is therefore available at 12:00, not at 08:00.

A 1d candle is available only when that daily candle has closed.

## Feature causality changes

### 1. Closed-bar availability clock

Feature calculations still use the original candle timestamps so session-based calculations keep their intended clock semantics.

After calculation, the historical feature row receives an availability timestamp equal to:

```text
source_open_time + inferred candle duration
```

The original open timestamp is preserved as `source_open_time` for audit/debugging.

### 2. No backward fill

The normal feature builder previously finished with backward fill followed by forward fill. Backward fill is useful for some display/runtime contexts, but it is not valid for historical research because an early missing indicator can inherit a value that is only known later.

V28.18 disables backward fill while a backtest is constructing historical features. Forward fill remains allowed because it carries only already-known values forward.

Example:

```text
MA200 at bar 100 -> remains unavailable
MA200 at bar 205 -> available from historical bars already seen
```

### 3. Causally confirmed pivots

The legacy market-structure helper used a centered five-bar window. A pivot located at bar `t` therefore needed bars `t+1` and `t+2` to confirm it, but the historical row at `t` could already receive that pivot label.

V28.18 keeps the five-bar idea but delays the pivot until the two confirmation bars exist.

```text
candidate pivot: t
confirmation:    t+2
first usable:    after t+2 closes
```

Market-structure and downstream CHoCH-style features built during the historical backtest therefore use only confirmed pivots.

## Compatibility design

The V28.17 engine is preserved verbatim as:

```text
app_src/backtest_core_legacy.py
```

`app_src/backtest_core.py` is now a compatibility facade. It exports the existing public API but runs historical backtests under the V28.18 timing contract.

This keeps existing imports such as:

```python
from app_src.backtest_core import run_backtest
```

working without rewriting the rest of the app.

`run_backtest_matrix` is also routed through the same V28.18 contract so what-if matrices cannot silently fall back to the old clock.

## Result metadata

New V28.18 backtests include:

```text
timing_integrity_version = 28.18
closed_bar_only = true
causal_pivots = true
backward_fill_disabled = true
```

and a `timing_integrity_audit` in the saved run config.

Trade rows also receive the V28.18 timing marker.

The audit checks at least:

- signal time is not after entry time;
- higher-timeframe feature availability is not after signal time;
- the causal-pivot and no-backward-fill contract was active.

## Research consequence

Backtest evidence created before V28.18 should be treated as **legacy evidence** and rerun before being used for new promotion decisions.

Do not compare a pre-V28.18 score directly with a V28.18 score and interpret the difference as strategy improvement or deterioration. The information set changed.

The intended sequence is now:

```text
populate/refresh historical OHLCV
        ↓
V28.18 timing-safe backtest
        ↓
V28.13 Evidence Review
        ↓
V28.14 frozen walk-forward
        ↓
V28.15 fresh holdout
```

## Scope boundary

V28.18 does not:

- change live or paper execution;
- change strategy thresholds;
- add a new trading strategy;
- claim profitability;
- turn V28.17 state-direction accuracy into trading PnL;
- solve missing funding/OI/order-book history.

It makes the historical evidence pipeline stricter so later adaptive-router comparisons are less likely to benefit from accidental lookahead.

## Validation

`smoke_test_v28_18_timing_integrity.py` checks:

- one-hour features become available one hour after source open;
- source open time is preserved;
- MA200 is not backward-filled into early history;
- a five-bar pivot is unavailable until its two right-side confirmation bars exist;
- valid timing audits pass;
- deliberately future HTF context or signal-after-entry timing fails the audit.
