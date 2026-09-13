# V28.22 — Frozen Adaptive Portfolio Walk-Forward

## Purpose

V28.22 answers a stricter question than V28.21:

> Does the exact same adaptive portfolio framework remain economically useful when replayed through chronological out-of-sample windows without changing its strategies, Market State rules, router rules, arbitration rules, risk multipliers or capital policy?

V28.22 is **research validation only**. It does not enable paper or live execution.

## Why this exists

V28.21 fixed the capital-reuse problem by putting adaptive and static candidates through one finite shared account. That is necessary but still only one historical aggregate view.

A portfolio can look good over a year because a small part of the year dominates the result. V28.22 therefore asks whether the same framework survives multiple chronological windows.

The validation chain is now:

```text
trustworthy OHLCV
-> closed-bar strategy evidence
-> Market State / Adaptive Router
-> adaptive economic evidence
-> one shared account
-> freeze complete framework
-> chronological OOS portfolio folds
-> only then consider a genuinely future freeze
```

## Important methodology boundary

The historical year used by V28.22 has already influenced strategy research and candidate selection.

Therefore V28.22 is:

```text
frozen temporal-stability evidence
```

not:

```text
pristine never-seen future evidence
```

That distinction is explicit in the freeze record and every saved V28.22 snapshot.

A V28.22 pass only means the historical framework is stable enough to justify freezing it **before new future data exists**.

## What gets frozen

V28.22 does not freeze only a strategy name.

The freeze contains the representative strategy payload for each core family:

```text
trend_pullback
compression_breakout
range_reversion
```

For each representative strategy it records:

```text
research family
strategy name
benchmark-only flag
complete strategy payload
strategy SHA-256
source saved-run directory
timing-integrity version
```

The freeze also stores canonical payload hashes for:

```text
adaptive-evidence policy
Market State / Router policy
Market State Replay policy
shared-account capital policy
V28.22 walk-forward policy
```

And it fingerprints the implementation files that materially define the historical decision path:

```text
backtest core
Market State implementation
historical Market State replay
adaptive evidence layer
shared-account replay
V28.22 portfolio walk-forward engine
```

This produces two important hashes:

```text
framework_sha256
record_sha256
```

`framework_sha256` represents the strategies + policies + implementation fingerprints.

`record_sha256` protects the entire freeze record, including the source research job and source V28.21 result signature.

## Freeze eligibility

A framework may enter V28.22 only when the source V28.21 verdict is exactly:

```text
shared_account_edge_candidate
```

This prevents using walk-forward validation as a way to rescue weak historical evidence.

Representative source strategy runs must also carry:

```text
timing_integrity_version = 28.18
```

The existing V28.19 data-continuity checks remain part of the source adaptive evidence integrity.

## Default fold structure

For an approximately 12-month source period, the default is an expanding historical context with non-overlapping two-month test windows:

```text
train months 1–6   -> test months 7–8
train months 1–8   -> test months 9–10
train months 1–10  -> test months 11–12
```

The train period is **diagnostic only** in V28.22.

Nothing is retuned from it.

The following remain fixed for every fold:

```text
strategy payloads
Market State rules
router thresholds
router risk multipliers
candidate arbitration
shared-account risk limits
friction assumptions
promotion thresholds
```

## Fold assignment

A historical trade belongs to a fold based on its `entry_time`.

Each test window is start-inclusive and end-date-inclusive.

A trade that enters before the end of the test window but exits later still belongs to that fold. This is valid because its decision occurred inside the test period.

Test windows do not overlap by entry time.

## Per-fold replay

For each fold V28.22 runs the same finite account twice:

```text
STATIC shared account
ADAPTIVE shared account
```

Both receive the same:

```text
starting capital
position sizing rules
risk cap
symbol cap
gross exposure cap
same-direction exposure cap
concurrency limits
friction model
```

The adaptive account receives only candidates accepted by the already-frozen router.

The static account receives the comparable static candidate pool.

No fold result changes the next fold's policy.

## Per-fold gates

Default per-fold requirements include:

```text
minimum 8 accepted adaptive trades
positive adaptive return
profit factor >= 1.00
realized drawdown <= 15%
positive account result after +10 bps additional round-trip friction
```

A fold can fail without invalidating the entire experiment, but the overall pass requires enough folds to survive.

Default required fold-pass share:

```text
>= 67%
```

Default required share where adaptive return is at least static return:

```text
>= 67%
```

## Aggregate OOS replay

The strongest V28.22 historical view is not the average of independent folds.

All non-overlapping test windows are combined and replayed again as **one chronological shared account**.

This means:

```text
capital carries across OOS windows
positions can overlap across fold boundaries
simultaneous opportunities compete for the same capital
later position size depends on earlier realized account equity
```

This aggregate OOS equity curve is the main account-level economic result.

Per-fold account resets exist only so individual folds can be compared on a common basis.

## Aggregate default gates

The default V28.22 policy requires:

```text
3 chronological folds
>= 30 aggregate accepted OOS adaptive trades
aggregate adaptive return > 0
aggregate adaptive profit factor >= 1.10
aggregate realized drawdown <= 12%
adaptive aggregate return >= static aggregate return
positive pair share >= 67%
positive month share >= 50%
positive result after required +10 bps friction stress
no accepted benchmark-only OOS trade
```

These thresholds are versioned research policy, not universal laws.

## Friction stress

The aggregate OOS account is rerun from the same starting capital with extra round-trip friction of:

```text
0 bps
5 bps
10 bps
20 bps
40 bps
```

The account is fully replayed for every friction scenario because earlier costs change later equity and therefore later position size.

The default required survival point is:

```text
+10 bps additional round-trip friction
```

## Static-baseline rejection

V28.22 contains an explicit failure state:

```text
static_baseline_better_oos
```

This is important.

If the simpler shared account is stronger than the adaptive account on both aggregate return and profit factor, TRAI should not rationalize the additional Market State / Router complexity.

The correct conclusion is that the adaptation layer is not economically earning its complexity.

## Benchmark-only compression rule

The OHLCV compression strategy remains a research benchmark.

If a `benchmark_only` compression trade is actually accepted by the adaptive account in OOS validation, this gate fails:

```text
benchmark_clear = false
```

The framework cannot receive `pass_for_future_freeze` while production-oriented evidence depends on a benchmark-only strategy.

## Verdicts

V28.22 returns one of:

```text
invalid_freeze_or_evidence
insufficient_walk_forward
fail
static_baseline_better_oos
mixed
pass_for_future_freeze
```

### `invalid_freeze_or_evidence`

A strategy payload, policy, implementation fingerprint, source V28.21 signature or evidence-integrity check does not match the freeze.

Do not interpret economics until integrity is repaired.

### `insufficient_walk_forward`

There are too few folds or too few accepted OOS trades.

Do not promote on a small sample.

### `fail`

The frozen adaptive portfolio is economically negative in the aggregate OOS replay.

Reject that framework version or redesign it under a new hash.

### `static_baseline_better_oos`

The adaptive account is positive enough to avoid a hard failure, but the simpler static account is stronger on both aggregate return and profit factor.

Do not promote the adaptive layer.

### `mixed`

Some evidence is positive, but one or more robustness gates fail.

Keep the framework in research.

### `pass_for_future_freeze`

The exact frozen adaptive portfolio survived the configured historical chronological gates.

This means only:

```text
the framework has earned a genuinely future test
```

It does **not** mean:

```text
proven future profitability
paper-trading permission
live-trading permission
```

## What a pass should trigger next

The next validation stage should create a new tamper-evident freeze **before new market outcomes are known**.

That future freeze should preserve the same categories already captured by V28.22:

```text
strategy versions
Market State implementation/policy
router implementation/policy
candidate arbitration
risk multipliers
shared-account capital policy
friction assumptions
validation endpoint
```

Then the system should wait for genuinely future data and evaluate the exact same framework without retuning.

## Remaining limitations

Even after V28.22, several limitations remain material.

### 1. Realized-equity drawdown

Shared-account drawdown is still based primarily on realized equity at trade exits.

Simultaneous open positions can experience a worse synchronized mark-to-market drawdown before exit.

This should be improved before serious capital decisions.

### 2. Simplified correlation control

Crypto correlation is currently represented mainly through same-direction exposure limits.

There is not yet a rolling covariance, beta/factor or stress-correlation portfolio model.

### 3. Historical execution abstraction

Trade paths still originate from OHLCV-based historical backtests.

The system does not reconstruct historical order-book queue position, exact fill latency or exchange microstructure.

### 4. Candidate-selection contamination

The historical research year has already influenced candidate selection.

Only the later genuinely future freeze can address this.

### 5. No leverage/liquidation optimization

This is deliberate.

V28.22 tests whether an edge survives conservative capital constraints before considering leverage complexity.

## UI

The Streamlit page is:

```text
pages/11_Frozen_Portfolio_Walk_Forward.py
```

The UI has three explicit stages:

```text
1. rebuild V28.21 source eligibility
2. create/select tamper-evident freeze
3. run chronological frozen portfolio walk-forward
```

It shows:

```text
freeze integrity
framework hash
per-fold train diagnostics
per-fold OOS results
fold pass share
adaptive-vs-static fold share
aggregate OOS equity curves
aggregate static/adaptive comparison
friction stress
pair/family/month contribution
validation gates
critical interpretation
```

## Runtime artifacts

Freeze records:

```text
data/backtest_reviews/adaptive_portfolio_freezes/
```

Walk-forward snapshots:

```text
data/backtest_reviews/adaptive_portfolio_walk_forward/
```

These are research runtime artifacts and remain outside Git through the existing `data/backtest_reviews/` ignore rule.
