# Decision Log

This file records the methodological decisions that materially affect what TRAI may claim from its research.

## V28.4 — Git baseline

**Decision:** GitHub is the source of truth for code, documentation, tests and version history. Runtime market data remains outside Git.

**Reason:** zip-based iteration was difficult to audit and easy to lose.

## V28.5 — Demo mode and lifecycle fit

**Decision:** lifecycle-to-strategy fit is useful explanatory evidence, but it is not a hard paper/live gate.

## V28.6 — Historical OHLCV backfill

**Decision:** preload Binance USD-M OHLCV into a local persistent history store and update only the recent tail after initial population.

**Constraint:** OHLCV does not imply that funding, open interest, liquidations or order-book history exists.

## V28.7 — History Manager

**Decision:** default long-history target is 12 months of 1h + 4h data for the configured research universe. Generated parquet history is runtime data and must not be committed to Git.

## V28.8 — Lifecycle Gate research

**Decision:** do not put lifecycle gating directly into execution before evidence.

Validation direction:

```text
counterfactual research
-> exact replay
-> out-of-sample validation
-> paper validation
-> live only later
```

## V28.9 — Research Command Center

**Decision:** stop uncontrolled strategy proliferation and focus first on three interpretable families:

```text
trend_pullback
compression_breakout
range_reversion
```

First-pass markets:

```text
BTCUSDT
ETHUSDT
SOLUSDT
```

Default research timeframes:

```text
entry: 1h
analysis: 4h
```

**Decision:** friction must be included in research; zero-cost headline results are not sufficient evidence.

## V28.10 — Runtime Cycle

**Decision:** routine refresh/analysis may orchestrate data and Market State calculations but must not silently enable execution.

```text
auto_paper_mode = false
live_bundle_mode = false
```

## V28.11 — Explicit Strategy Family Registry

**Decision:** family assignment and historical readiness must be explicit metadata, not inferred only from strategy names.

## V28.12 — OHLCV compression benchmark

**Decision:** do not pretend OI/order-book history exists. Use a deliberately simple OHLCV-only compression benchmark to keep the third family testable.

**Constraint:** it is marked `benchmark_only=true` and cannot by itself justify production-oriented promotion.

## V28.13 — Evidence Review

**Decision:** completed research runs must face transparent gates for sample size, friction-aware economics, profit factor, stability and drawdown before stronger validation.

Possible conclusions:

```text
reject
insufficient_evidence
promising
cross_validation_candidate
```

**Decision:** win rate is descriptive rather than a universal gate because payoff structures differ across strategy families.

## V28.14 — Frozen strategy walk-forward

**Decision:** a candidate entering walk-forward must use one unchanged strategy payload across all chronological folds.

Default shape:

```text
months 1–6  -> test 7–8
months 1–8  -> test 9–10
months 1–10 -> test 11–12
```

**Decision:** hash the complete strategy payload and reject drift.

**Methodology warning:** when the same historical year influenced candidate selection, this is temporal-stability evidence, not a pristine future holdout.

## V28.15 — Research freeze + genuinely future holdout

**Decision:** a future test must be frozen before future outcomes exist.

The freeze records the exact candidate, execution assumptions, dates and policy versions and protects them with SHA-256 integrity checks.

Default future horizon:

```text
target_holdout_days = 60
minimum_observation_days = 30
allow_preliminary_queue = false
```

**Decision:** a failed future holdout may not be repaired by retuning on the same failed future period. A redesign requires a new hash and a new future clock.

## V28.16 — Market State Identifier + Adaptive Router

**Decision:** TRAI should adapt to changing market conditions rather than force one strategy to run continuously.

Separate:

```text
Market State Identifier
-> what is the market doing?

Adaptive Router
-> what should the research system do in that state?
```

The response may change each bar, but the rules defining state/routing must remain fixed during a validation experiment.

**Constraint:** Market State and routing remain research behavior until validated economically.

## V28.17 — Historical Market State Replay

**Decision:** validate state classification historically with closed bars before treating routing as economic evidence.

Measure state distribution, transitions, dwell/churn, confidence calibration and later directional agreement.

**Constraint:** directional agreement is diagnostic; it is not trading profitability.

## V28.18 — Closed-Bar Timing Integrity

**Decision:** historical research must obey a strict information clock.

```text
candle closes
-> features become available
-> decision
-> later entry
```

Higher-timeframe context follows the same rule.

Additional anti-lookahead decisions:

```text
no backward fill from future feature values
no centered swing pivots requiring unobserved bars
```

**Decision:** pre-V28.18 backtests are legacy evidence and must be rerun before new promotion decisions.

## V28.19 — Historical Data Integrity

**Decision:** harden the OHLCV store before trusting adaptive profitability comparisons.

Historical ingestion contract:

```text
fully closed candles only
+ prune unfinished tails
+ overlap recent candles on update
+ retry transient failures
+ audit continuity
+ targeted gap repair
+ explicit unresolved-gap reporting
```

A target is `ready` only when coverage/freshness and continuity both pass.

## V28.20 — Adaptive Evidence & Economic Viability

**Decision:** a plausible Market State label is not enough. The fixed adaptive policy must demonstrate economic improvement over static family baselines.

Use a causal backward-only join:

```text
saved strategy signal
-> latest router state already available
-> family/action/direction match
-> fixed router risk multiplier
```

Measure:

```text
profit factor
expectancy per capital turn
WAIT avoided loss vs missed profit
pair/month stability
family concentration
friction headroom
capital overlap
```

**Decision:** choose representative family runs by registry priority, not by historical PnL, to reduce winner-picking.

**Decision:** persist evidence with policy SHA-256 fingerprints.

`adaptive_edge_candidate` means permission for stronger validation only.

## V28.21 — Shared-Account Adaptive Portfolio Replay

**Decision:** stop treating independent strategy PnLs as if each has separate capital.

Static and adaptive candidates must compete inside one finite account with the same capital/risk constraints.

Default research account:

```text
starting equity                 $10,000
base risk / trade                  0.50%
max total open risk                1.50%
max position notional             35.00%
max symbol notional               35.00%
max gross exposure               100.00%
max same-direction exposure       70.00%
max concurrent positions               3
max positions / family                 2
one position / symbol                  yes
```

**Decision:** same-time arbitration may use only information known before entry. Future PnL must never rank candidates.

**Decision:** every rejected opportunity gets an explicit capital/constraint reason.

**Decision:** static and adaptive portfolios use identical capital rules so complexity must justify itself economically.

Explicit verdict:

```text
static_baseline_better
```

If the simpler account wins, do not rationalize the adaptive layer.

**Known limitation:** portfolio drawdown is mainly realized-equity drawdown at exits; synchronized intratrade mark-to-market drawdown can be worse.

## V28.22 — Frozen Adaptive Portfolio Walk-Forward

**Decision:** after a V28.21 `shared_account_edge_candidate`, freeze the **complete adaptive portfolio framework**, not only the strategy names.

The V28.22 freeze includes:

```text
representative strategy payloads + SHA-256
adaptive-evidence policy
Market State / Router policy
Market State Replay policy
shared-account capital policy
V28.22 walk-forward policy
core implementation-file SHA-256 fingerprints
source V28.21 result signature
```

Integrity identifiers:

```text
framework_sha256
record_sha256
```

**Decision:** a freeze is ineligible unless the source V28.21 verdict is exactly:

```text
shared_account_edge_candidate
```

and representative saved strategy runs use V28.18 timing integrity.

### Fold methodology

Default approximately one-year structure:

```text
months 1–6   diagnostic history -> OOS months 7–8
months 1–8   diagnostic history -> OOS months 9–10
months 1–10  diagnostic history -> OOS months 11–12
```

**Decision:** the expanding historical section is diagnostic only. No strategy, threshold, routing rule, risk multiplier, arbitration rule, capital rule or validation threshold may change between folds.

Each fold compares the frozen adaptive account with the same frozen static shared-account baseline.

### Aggregate OOS account

**Decision:** do not rely only on fold averages. Combine all non-overlapping test windows and replay them as one chronological shared account so capital carries across OOS windows and positions can compete across fold boundaries.

### Default robustness requirements

```text
3 folds
>= 67% fold-pass share
>= 67% folds where adaptive return >= static return
>= 30 aggregate accepted OOS adaptive trades
aggregate adaptive return > 0
aggregate adaptive PF >= 1.10
aggregate realized drawdown <= 12%
adaptive aggregate return >= static aggregate return
positive symbol share >= 67%
positive month share >= 50%
survive +10 bps extra round-trip friction
no accepted benchmark-only OOS trade
```

### Verdicts

```text
invalid_freeze_or_evidence
insufficient_walk_forward
fail
static_baseline_better_oos
mixed
pass_for_future_freeze
```

**Decision:** `static_baseline_better_oos` is a first-class rejection state. If the simpler shared account wins on both aggregate return and profit factor, the adaptive framework has not earned its complexity.

**Decision:** an accepted benchmark-only OOS trade blocks `pass_for_future_freeze`.

**Decision:** `pass_for_future_freeze` means only that the historical frozen framework has earned a **new genuinely future freeze**. It is not paper/live trading permission.

### Methodology warning

The V28.22 source year has already participated in research/candidate selection. Therefore V28.22 is frozen temporal-stability evidence, not pristine future evidence.

The next validation step after a pass must start a new tamper-evident clock before future market outcomes are known.

## Current strategic decision

Continue TRAI as a research/validation system and keep execution changes frozen until evidence is materially stronger.

Keep developing:

- trustworthy historical data;
- closed-bar timing;
- Market State and adaptive routing;
- economic falsification against simpler baselines;
- one-account capital realism;
- tamper-evident framework freezes;
- chronological and genuinely future validation;
- reproducible research artifacts.

Keep frozen for now:

- live execution changes;
- uncontrolled strategy proliferation;
- opaque LLM trading decisions;
- leverage optimization;
- complexity that has not earned its place through evidence.

Current ladder:

```text
V28.19 trustworthy OHLCV
-> V28.18 closed-bar timing
-> V28.16 Market State
-> V28.17 historical state replay
-> static family baselines
-> V28.20 adaptive economic evidence
-> V28.21 one finite shared account
-> V28.22 frozen adaptive portfolio walk-forward
-> new complete-framework freeze BEFORE future data
-> genuinely future adaptive portfolio holdout
-> frozen account-aware paper validation
-> constrained live execution only later
```
