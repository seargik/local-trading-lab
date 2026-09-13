# V28.23 — Genuinely Future Adaptive Portfolio Holdout

## Purpose

V28.23 is the first portfolio-level validation stage whose test window begins **after** the complete adaptive framework has been frozen.

It answers a stricter question than historical walk-forward:

> After TRAI has finished learning from historical research and the complete decision/risk framework is frozen, does the unchanged adaptive shared-account system still add economic value on market data that did not exist when the freeze was created?

This is research validation only. It does not enable paper orders, live orders, leverage, or autonomous execution.

## Eligibility

A future clock may start only from a V28.22 result with:

```text
pass_for_future_freeze
```

V28.23 does not provide a bypass around failed historical evidence. If V28.22 is `mixed`, `fail`, `static_baseline_better_oos`, `insufficient_walk_forward`, or invalid, the future clock must not start.

## What is frozen

The V28.23 record contains or fingerprints:

```text
representative strategy payloads
strategy payload hashes
per-family historical execution/backtest configs
Market State policy
Market State replay policy
adaptive-evidence policy
shared-account capital/risk policy
V28.22 walk-forward policy
V28.23 future-holdout policy
core behavioral implementation files
source V28.22 freeze
source V28.22 result signature
```

The behavioral implementation fingerprint is intentionally broader than earlier freezes and covers strategy interpretation, feature math, exits, Market State, routing, adaptive evidence, shared-account replay, the historical portfolio walk-forward, the future-holdout engine, and worker config merging.

This matters because a future test is not the same test if code semantics change even when a strategy still has the same display name.

## Future-data boundary

If the freeze is created at:

```text
2026-09-13 16:45 UTC
```

the first eligible date is:

```text
2026-09-14 00:00 UTC
```

The remainder of the freeze day is intentionally discarded from the holdout. This avoids partial-day contamination and makes the boundary unambiguous.

Default window:

```text
target_holdout_days = 60
```

The target endpoint is fixed when the freeze is created.

## No preliminary peeking

Default policy:

```text
allow_preliminary_evaluation = false
```

V28.23 refuses to run the final economic evaluation before the precommitted endpoint has fully elapsed.

The reason is methodological, not cosmetic. Repeatedly checking results and deciding to stop only when the account looks good turns a future holdout back into an optimization exercise.

## One clock per passed framework

By default:

```text
one_future_freeze_per_source_framework = true
```

The same passed V28.22 framework/result cannot simply create a new later future clock after the first period becomes disappointing.

A redesign is allowed, but it must be treated as a **new framework** with a new hash and a new future-data window. The failed future window may inform research, but it cannot then be reused as if it were unseen evidence for the redesigned system.

## Data readiness

The final backtest is not queued merely because 60 calendar days have elapsed.

For every frozen symbol, V28.23 checks both:

```text
entry timeframe
analysis timeframe
```

For the default research protocol that means 1h and 4h.

The local OHLCV store must:

```text
cover the first frozen day
cover the final required candle of the target day
contain no internal gaps
contain no unclosed rows in the fixed window
```

For example, if the final date is 2026-11-12:

```text
1h data must reach the 23:00 candle
4h data must reach the 20:00 candle
```

A single candle somewhere on the final date is not enough.

## Queue design

Only after the fixed endpoint and data-integrity checks pass does V28.23 queue the future research batch.

There is one task per frozen strategy family, and each task runs the complete frozen symbol set. This is important because the adaptive-evidence layer expects one representative multi-symbol run per family.

The queued batch preserves:

```text
exact start date
exact end date
exact symbols
exact entry/analysis timeframes
exact strategy payload hashes
exact per-family execution config hashes
exact future freeze hash
exact future framework hash
```

A completed job that does not match these values fails job integrity.

## Frozen Market State and Router policies

The future evaluator does not merely call the current default Market State configuration.

It explicitly passes the frozen:

```text
market_state_replay policy
market_state_router policy
adaptive_evidence policy
```

into the future evidence reconstruction.

This is essential. Otherwise the strategy payload could remain frozen while the rules defining `trend_running`, `range_chop`, confidence, direction, WAIT, or risk multiplier changed after the freeze.

## Shared-account comparison

After the future family backtests are complete, V28.23 reconstructs two accounts using the same frozen V28.21 capital policy:

```text
STATIC shared account
vs
ADAPTIVE shared account
```

The adaptive account must therefore justify the Market State / Router complexity on genuinely new data.

The account still enforces finite capital, stop-distance risk sizing, gross exposure, symbol exposure, same-direction exposure, concurrent-position limits, and friction.

## Future friction stress

The complete future account is replayed under additional round-trip friction of:

```text
0 bps
5 bps
10 bps
20 bps
40 bps
```

The default promotion gate requires the adaptive account to remain positive with PF >= 1.0 at at least +10 bps extra round-trip friction.

This does not prove real execution quality. It asks whether the historical edge has enough margin to survive a modest execution-model error.

## Default future-evidence gates

The default policy requires, among other things:

```text
future freeze integrity
current frozen framework match
exact future job integrity
V28.22 pass_for_future_freeze source
completed fixed future endpoint
source timing/data integrity
>= 30 accepted adaptive trades
>= 2 active symbols
>= 2 active months
positive adaptive return
adaptive PF >= 1.10
realized max drawdown <= 12%
positive-symbol share >= 67%
positive-month share >= 50%
adaptive return >= static return
adaptive PF not materially worse than static
survival at +10 bps extra friction
no accepted benchmark-only trade for promotion
```

The thresholds are policy, not laws of finance. They are intentionally explicit so future changes create a new version rather than silently moving the goalposts.

## Verdicts

Possible outcomes:

```text
invalid_freeze_or_test
insufficient_future_evidence
future_holdout_fail
static_baseline_better_future
future_holdout_mixed
pass_for_frozen_paper
```

### invalid_freeze_or_test

The freeze, current behavioral framework, future job, source evidence, or future-window process is not trustworthy enough to interpret economically.

### insufficient_future_evidence

The fixed future window completed, but too few usable adaptive trades / symbols exist for the configured minimum sample.

This is not converted into a pass by lowering the sample threshold after seeing the results.

### future_holdout_fail

The adaptive account is economically negative enough to reject this frozen version.

A redesign requires a new framework and new future clock.

### static_baseline_better_future

The adaptive account remains plausible on its own, but the simpler static shared account is better on both future return and PF.

This is a direct failure of the adaptation hypothesis. More sophisticated market-state logic is not valuable merely because it is sophisticated.

### future_holdout_mixed

Some future gates work and others do not. Keep the framework in research.

### pass_for_frozen_paper

The exact frozen adaptive portfolio survived a genuinely post-freeze market window strongly enough to justify a separate paper-validation stage.

It does **not** mean:

```text
live ready
profitable with certainty
safe to leverage
safe to increase position size
```

## What a pass still does not solve

Even a clean V28.23 pass still uses historical reconstruction after the future window closes. Its trade paths are based on OHLCV backtests, not actual exchange fills.

Remaining limitations include:

```text
realized-equity rather than synchronized mark-to-market portfolio DD
no historical queue position
no real latency
no partial-fill model
no exchange operational failures
no liquidation model
no complete funding/OI/order-book history in the current core protocol
one 60-day window can still be regime-specific
```

Therefore the next stage after a pass is **frozen paper validation**, where decisions are emitted prospectively before outcomes are known and compared with executable market prices in real time.

## Why this stage matters for the money question

Historical research can tell us whether an idea was coherent in known data. V28.22 can tell us whether the complete portfolio was reasonably stable across chronological slices of that known research period.

V28.23 asks the harder question:

```text
We stopped designing it.
We froze it.
New market data happened.
Did the same adaptive account still work?
```

That is much closer to evidence of a repeatable economic edge, although it is still not sufficient by itself for real-money deployment.
