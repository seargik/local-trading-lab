# Decision Log

## V28.4 — Git baseline

Decision: move the project to GitHub as the source of truth.

Reason: zip patches became too risky and hard to verify. GitHub gives branch history, PRs, Actions checks, Codespaces preview, and rollback.

## V28.5 — Demo mode and lifecycle fit

Decision: add synthetic demo data and lifecycle-to-strategy fit labels as a soft evidence layer.

Constraint: lifecycle fit is not a hard live/paper gate.

## V28.6 — Historical OHLCV backfill

Decision: preload Binance USD-M futures OHLCV into the local parquet store and later update only the fresh missing part.

Constraint: candles only; funding/open interest/order book/liquidations are separate future datasets.

## V28.7 — History Manager and project memory

Decision: add Data / History Manager UI and stable project recap docs.

Default target set:

```text
BTCUSDT, ETHUSDT, SOLUSDT, LTCUSDT, BNBUSDT, UNIUSDT, AAVEUSDT, XRPUSDT, TRXUSDT
```

Default requested history:

```text
12mo, 1h and 4h
```

Decision: do not commit generated `data/ohlcv_store` parquet files to Git.

## V28.8 — Lifecycle Gate Backtest Lab

Decision: do not place lifecycle gating directly into the backtest/live signal path yet.

Promotion ladder:

```text
promising counterfactual result
-> exact signal-path replay
-> out-of-sample validation
-> paper-only gate
-> only later consider live execution
```

## V28.9 — Research Command Center

Decision: stop broadening the strategy surface and create a narrow evidence runner.

First-pass families:

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

Default execution assumptions: `binance_usdm_taker_light`, not zero friction.

## V28.10 — Runtime Cycle

Decision: combine routine maintenance and analysis into one orchestrated cycle.

Default sequence:

```text
incremental OHLCV update
-> coverage summary
-> gap audit
-> safe inline analysis
-> Market State snapshot
-> runtime report
```

Decision: inline analysis forces:

```text
auto_paper_mode = false
live_bundle_mode = false
```

Reason: routine refresh should update research state without silently changing paper/live execution behavior.

Decision: core research preparation remains opt-in and guarded.

Guardrails:

- require approximately 95% BTC/ETH/SOL 1h and 4h coverage when configured;
- do not queue a duplicate core research job;
- do not auto-promote any strategy;
- do not place exchange orders.

Decision: persist operational history under:

```text
data/runtime_cycles/
data/market_state_history/
```

and ignore those runtime artifacts in Git.

## V28.11 — Explicit Strategy Family Registry

Decision: research-family assignment must use explicit registry metadata rather than strategy-name heuristics when deciding research readiness.

Registry records:

```text
strategy family
research inclusion
historical-data requirements
historical readiness
priority
```

Reason: a strategy should not enter a controlled family comparison merely because its name happens to resemble that family.

Finding: the saved trend-pullback and range-reversion candidates can be replayed from OHLCV, while the existing compression candidates require historical OI and/or order-book inputs.

## V28.12 — OHLCV Compression Breakout Benchmark

Decision: do not fake missing derivatives/order-book history and do not drop the compression family from the comparison. Add one explicit OHLCV-only benchmark control instead.

Benchmark:

```text
OHLCV Compression Breakout Benchmark
```

Inputs:

```text
20-bar breakout
prior-bar compression vs recent BB/ATR medians
volume ratio
breakout close strength
HTF OHLCV alignment
```

Decision: mark the benchmark `benchmark_only=true` and load it directly into research jobs when compression otherwise has no replayable saved strategy.

Constraint: the benchmark is not automatically saved into live/paper strategy slots and is not a production recommendation.

Decision: the three-family protocol should only queue when every selected family has an explicitly classified, historically replayable candidate.

Default controlled protocol remains:

```text
BTCUSDT / ETHUSDT / SOLUSDT
1h entry / 4h analysis
365 days
realistic Binance USD-M friction
trend_pullback vs compression_breakout vs range_reversion
```

## V28.13 — Evidence Review / Research Scorecard

Decision: completed core-research batches must pass a transparent evidence review before they are treated as candidates for more expensive validation.

Verdicts:

```text
reject
insufficient_evidence
promising
cross_validation_candidate
```

Decision: store triage thresholds in versioned configuration:

```text
config/research_evidence_policy.json
```

The review checks sample size, net PnL after friction, expectancy R, profit factor, pair stability, month stability, drawdown relative to net profit, and execution-friction drag.

Decision: win rate is descriptive only and is not a promotion gate because different strategy families can have materially different payoff ratios.

Decision: benchmark-only strategies can demonstrate concept promise but cannot receive direct production-oriented cross-validation-candidate status. The V28.12 compression benchmark therefore remains promotion-blocked even when its metrics are strong.

Decision: scorecard snapshots can be saved under:

```text
data/backtest_reviews/evidence_scorecards/
```

and remain runtime research artifacts rather than Git source files.

## V28.14 — Frozen Walk-Forward Validation

Decision: a non-benchmark `cross_validation_candidate` from V28.13 should be tested with the exact same strategy payload across expanding chronological holdouts before any paper-promotion discussion.

Default fold pattern for a roughly 12-month run:

```text
train months 1-6  -> test months 7-8
train months 1-8  -> test months 9-10
train months 1-10 -> test months 11-12
```

Decision: hash the complete strategy payload before queueing and verify the same SHA-256 hash in every saved validation result.

Reason: a walk-forward test is meaningless if thresholds, exits, stops or rules drift between folds.

Decision: store validation thresholds in:

```text
config/walk_forward_policy.json
```

and treat them as versioned research policy rather than hidden AI judgment.

Decision: include explicit pair-transfer diagnostics using the same frozen strategy.

Decision: LONG/SHORT stability is reported but is not a hard V28.14 promotion gate because legitimate strategies may be directionally asymmetric.

Important methodology decision: V28.14 must not be described as a pristine untouched out-of-sample test when V28.13 selected the candidate using the same 12-month dataset. It is a frozen-parameter temporal-stability test. A genuinely fresh period remains required later.

Possible V28.14 verdicts:

```text
pass_for_next_validation
mixed
insufficient_evidence
fail
invalid_test
```

A pass authorizes only stronger validation, never live execution.

## V28.15 — Research Freeze + Fresh Holdout

Decision: only a V28.14 `pass_for_next_validation` may start the fresh-holdout clock.

Decision: freeze the exact candidate before any future holdout candle exists. The freeze contains the complete strategy payload, frozen execution/friction configuration, symbols, timeframes, source run, V28.14 id, policy versions, UTC cutoff and fixed holdout dates.

Decision: store two integrity checks:

```text
strategy_sha256
record_sha256
```

If either the strategy payload or freeze record changes, the holdout becomes invalid rather than silently accepting the changed candidate.

Decision: begin the holdout on the next UTC date after the freeze cutoff. Do not use the partially observed freeze day as unseen data.

Default policy:

```text
target_holdout_days = 60
minimum_observation_days = 30
allow_preliminary_queue = false
```

Reason: fixing the full 60-day endpoint at freeze time is cleaner than repeatedly peeking after 30/40/50 days and stopping when the result looks favorable.

Decision: the V28.15 evaluation button remains disabled until the full fixed calendar window has elapsed and local OHLCV for every frozen symbol reaches the fixed target end date.

Decision: a holdout failure must not be repaired by tuning on the failed future window. Any redesign creates a new strategy hash, new freeze record, and new future-data clock.

Possible V28.15 verdicts:

```text
fresh_holdout_pass
fresh_holdout_mixed
fresh_holdout_fail
insufficient_evidence
invalid_freeze_or_test
```

Decision: a `fresh_holdout_pass` is permission to start a separate paper-validation stage with the same frozen candidate. It is still not permission for live execution.

Runtime artifacts under `data/backtest_reviews/research_freezes/` and `data/backtest_reviews/fresh_holdout_scorecards/` remain outside Git.

## Current strategic decision

Continue the project, but simplify aggressively.

Keep:

- Historical data store and incremental refresh.
- Backtest/replay foundation.
- Trend lifecycle router and fit labels.
- Explicit strategy-family registry.
- Friction-aware evaluation.
- Evidence Review and conservative reject/retain gates.
- Frozen walk-forward validation and pair-transfer checks.
- Tamper-evident fresh future holdouts.
- Runtime-cycle orchestration.

Freeze for now:

- New strategy proliferation outside controlled research needs.
- Live execution changes.
- More complex bundle logic.
- LLM-generated trading decisions.

Focus:

```text
historical coverage
-> controlled three-family baseline
-> V28.13 Evidence Review
-> V28.14 frozen walk-forward
-> V28.15 fresh future holdout
-> paper validation without retuning
-> only later consider constrained live execution
```
