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

### Closed-candle storage

Only fully closed Binance candles may be written by the historical backfill.

Rows returned for the currently forming candle are discarded and counted:

```text
discarded_unclosed_rows
```

Older stores may contain an unfinished tail row from previous collectors. V28.19 prunes rows whose close time is still in the future or whose `is_closed` flag is false.

Decision: pruning uses real current UTC time. A request with an older historical end date must never delete newer valid data already stored outside that requested window.

### Overlap refresh

Decision: update-only refresh intentionally refetches the latest stored candles rather than starting strictly after the latest row.

Default:

```text
overlap_bars = 2
```

For a latest stored candle `T`, two-bar overlap restarts at `T - 1 interval`, so the latest two stored rows can be replaced safely through open-time deduplication.

`overlap_bars = 0` preserves the old start-after-latest behavior.

### Retry/backoff

Decision: transient Binance/network failures must not immediately abort a history refresh.

Retryable statuses:

```text
418, 429, 500, 502, 503, 504
```

Defaults:

```text
max_retries = 4
retry_backoff_seconds = 0.5
```

The delay grows exponentially unless a `Retry-After` header is supplied.

### Continuity audit and gap repair

Decision: after refresh, audit consecutive `open_time` values for missing internal candles.

Report:

```text
gaps_before
missing_rows_before
repair_attempts
gaps_repaired
gaps_remaining
missing_rows_remaining
integrity_status
```

Decision: targeted repair is enabled by default. For each detected internal gap, request only the missing Binance time window, write through the normal parquet store and audit again.

Default:

```text
repair_gaps = true
max_gap_repairs = 50
```

`integrity_status = ready` means the observed stored range has no internal gap and no unfinished row. It does not replace the separate coverage/freshness requirement.

### UI and runtime behavior

Decision: Data / History Manager should display both coverage and integrity:

```text
continuity_ok
internal_gap_count
internal_missing_rows
unclosed_rows
```

A target is `ready` only when coverage/freshness and continuity both pass.

Decision: Runtime Cycle automatically benefits from the hardened `backfill_symbol_history` defaults without changing paper/live execution.

Recommended regular command:

```powershell
.\.venv\Scripts\python.exe backfill_default_history.py --lookback 12mo --update-only --overlap-bars 2 --request-analysis
```

## Current strategic decision

Continue the project as a research/validation system, but keep execution changes frozen until evidence is materially stronger.

Keep:

- integrity-safe historical OHLCV storage;
- closed-bar historical timing;
- Market State Identifier and adaptive routing research;
- explicit strategy-family registry;
- friction-aware evaluation;
- conservative evidence rejection;
- frozen walk-forward and future holdout protocols;
- Runtime Cycle orchestration.

Freeze for now:

- uncontrolled new strategy proliferation;
- live execution changes;
- opaque LLM trading decisions;
- extra complexity that is not justified by evidence.

Current research ladder:

```text
V28.19 trustworthy OHLCV
-> V28.18 closed-bar timing
-> V28.16 Market State Identifier
-> V28.17 historical state replay
-> controlled static family baselines
-> V28.13 Evidence Review
-> V28.14 frozen walk-forward
-> freeze complete adaptive framework
-> V28.15 genuinely future holdout
-> frozen paper validation
-> constrained live execution only later
```
