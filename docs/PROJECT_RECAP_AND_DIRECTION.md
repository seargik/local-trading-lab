# Local Trading Lab — Project Recap and Direction

_Last updated for V28.22._

## Source of truth

GitHub is the canonical project memory and code history:

```text
seargik/local-trading-lab
```

Runtime market data remains outside Git and belongs on the machine that actually runs the research stack: local PC, temporary Codespace, or a future persistent VPS.

## Product definition

TRAI is currently best described as:

```text
A local crypto market-state, strategy-validation and portfolio-research lab.
```

It is **not** yet a production autonomous trading bot.

The target behavior is adaptive:

```text
trustworthy market data
-> Market State Identifier
-> Adaptive Router
-> strategy-family choice / direction / WAIT / risk
-> shared-account capital allocation
-> historical economic validation
-> frozen chronological validation
-> genuinely future validation
-> paper validation
-> constrained live execution only much later
```

The market response may change every bar. The rules that decide how TRAI adapts must remain explicit, versioned, frozen during validation, and economically accountable.

## Version status

- **V28.4** — GitHub repo-ready baseline, lifecycle scaffold and smoke/remote-development support.
- **V28.5** — demo mode and lifecycle-to-strategy fit labels.
- **V28.6** — historical Binance USD-M OHLCV backfill.
- **V28.7** — Data / History Manager and default 12-month history target.
- **V28.8** — Lifecycle Gate counterfactual research.
- **V28.9** — Research Command Center and narrow three-family protocol.
- **V28.10** — Runtime Cycle for safe refresh/analysis/state snapshots.
- **V28.11** — explicit Strategy Family Registry and historical-readiness audit.
- **V28.12** — OHLCV-only Compression Breakout Benchmark, marked `benchmark_only`.
- **V28.13** — Evidence Review / Research Scorecard with conservative reject/retain gates.
- **V28.14** — frozen-payload chronological walk-forward for individual strategies.
- **V28.15** — tamper-evident freeze and fixed genuinely future holdout protocol.
- **V28.16** — first-class Market State Identifier + explainable Adaptive Router.
- **V28.17** — closed-bar Historical Market State Replay and adaptation diagnostics.
- **V28.18** — Closed-Bar Timing Integrity: closed HTF availability, causal pivots and no backward fill.
- **V28.19** — Historical Data Integrity: unfinished-candle filtering, overlap refresh, retries and targeted gap repair.
- **V28.20** — Adaptive Evidence & Economic Viability: static-vs-adaptive economics, WAIT value, friction stress and reproducible evidence hashes.
- **V28.21** — Shared-Account Adaptive Portfolio Replay: one finite account, chronological capital allocation, exposure/risk limits and explicit candidate rejection.
- **V28.22** — Frozen Adaptive Portfolio Walk-Forward: complete-framework freeze, chronological shared-account OOS folds, aggregate OOS equity, static-baseline rejection and tamper-evident validation snapshots.

## Current architecture

```text
GitHub
  -> source code
  -> policies
  -> docs
  -> smoke tests
  -> PR/version history

Runtime host
  -> Streamlit
  -> Runtime Cycle
  -> Binance OHLCV collector
  -> local parquet history
  -> analysis/backtest workers
  -> research artifacts under data/backtest_reviews/
```

Research flow:

```text
Binance USD-M klines
-> V28.19 integrity-safe history
-> V28.18 closed-bar information clock
-> static family backtests

closed historical data
-> V28.16 Market State + Router
-> V28.17 historical state replay

static family trades + router states
-> V28.20 adaptive economic evidence
-> V28.21 one finite shared account
-> V28.22 exact-framework historical walk-forward
```

## Historical data policy

Generated runtime data is ignored by Git.

Important locations:

```text
data/ohlcv_store/
data/backfill_reports/
data/backtest_reviews/
```

Default history universe:

```text
BTCUSDT
ETHUSDT
SOLUSDT
LTCUSDT
BNBUSDT
UNIUSDT
AAVEUSDT
XRPUSDT
TRXUSDT
```

Default target:

```text
12 months
1h + 4h
```

The current long-history store is OHLCV. Funding, open interest, liquidations and order-book history are separate datasets and must never be silently assumed to exist.

**Do not claim the 12-month dataset is populated on a runtime machine until that runtime has actually been checked.**

## V28.19 historical integrity contract

Historical ingestion now requires:

```text
fully closed candles only
+ prune old unfinished tail rows
+ overlap latest 2 candles on update refresh
+ retry transient Binance/network failures
+ continuity audit
+ targeted gap repair
+ unresolved-gap reporting
```

Recommended regular refresh:

```powershell
.\.venv\Scripts\python.exe backfill_default_history.py --lookback 12mo --update-only --overlap-bars 2 --request-analysis
```

A dataset is not `ready` merely because row count is approximately correct; coverage/freshness and internal continuity must both pass.

## V28.18 historical timing contract

Historical strategy/state logic may use a candle only after that candle closes.

```text
candle opens
-> candle develops
-> candle closes
-> features become available
-> decision
-> later entry
```

Higher-timeframe context follows the same rule.

V28.18 also removes backward fill and replaces centered pivots with causally confirmed pivots.

New backtests carry:

```text
timing_integrity_version = 28.18
closed_bar_only = true
```

Pre-V28.18 results are legacy evidence and should not be used for new promotion decisions.

## Market State Identifier and Adaptive Router

The current state object can expose:

```text
lifecycle state
direction
trend strength
volatility
market structure
HTF alignment
confidence
preferred strategy family
router action
route direction
research risk multiplier
entry mode
exit family
reasons
```

Typical routing hypotheses:

```text
compression_building -> prepare compression breakout
breakout_attempt      -> compression breakout candidate when confirmed
trend_entering        -> prepare trend pullback
trend_pullback_entry  -> trend pullback candidate
trend_running         -> wait for pullback rather than chase
range_chop            -> range reversion only near range edge
trend_extended_late   -> protect / wait
trend_exhaustion      -> avoid continuation
panic_volatility      -> WAIT / reduced exposure hypothesis
```

The confidence/risk mappings are hypotheses to validate, not universal probabilities.

## Core research protocol

Research remains deliberately narrow:

```text
trend_pullback
compression_breakout
range_reversion
```

First-pass symbols:

```text
BTCUSDT
ETHUSDT
SOLUSDT
```

Default timeframes:

```text
entry:    1h
analysis: 4h
```

The richer compression/order-flow candidates that require historical OI or order-book data remain blocked from OHLCV-only claims.

`OHLCV Compression Breakout Benchmark` exists only as a reproducible research control and remains `benchmark_only`.

## V28.20 — Adaptive economic evidence

V28.20 asks:

> Does the fixed Market State + Router policy select economically better conditions than the same family strategies running statically?

It measures:

```text
profit factor
expectancy per capital turn
WAIT avoided loss vs missed profit
pair/month stability
family concentration
extra-friction survival
trade overlap / concurrency
```

A result can become:

```text
adaptive_edge_candidate
```

only as permission for stronger validation.

It is not a forecast of future profit.

## V28.21 — Shared-account portfolio replay

V28.21 removes the independent-capital illusion.

Static and adaptive candidates must compete inside one finite account with the same:

```text
starting capital
risk per trade
total open-risk limit
position-notional cap
symbol-exposure cap
gross-exposure cap
same-direction exposure cap
max concurrent positions
max positions per family
friction model
```

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

Candidate arbitration uses only information available before entry. Future PnL never decides which simultaneous candidate wins.

An explicit verdict exists for:

```text
static_baseline_better
```

If simpler logic makes more money under the same capital constraints, the adaptive layer has not earned its complexity.

## V28.22 — Frozen adaptive portfolio walk-forward

V28.22 freezes the **complete adaptive portfolio framework**, not merely a strategy name.

The freeze covers:

```text
representative strategy payloads + hashes
adaptive-evidence policy
Market State / Router policy
Market State Replay policy
shared-account capital policy
V28.22 validation policy
core implementation-file hashes
source V28.21 result signature
```

Integrity identifiers:

```text
framework_sha256
record_sha256
```

Freeze eligibility requires the source V28.21 result to be:

```text
shared_account_edge_candidate
```

and representative source strategy runs must use V28.18 timing integrity.

### Default folds

For an approximately 12-month source period:

```text
months 1–6   diagnostic history -> test months 7–8
months 1–8   diagnostic history -> test months 9–10
months 1–10  diagnostic history -> test months 11–12
```

The expanding historical section is diagnostic only. **No parameter, strategy, router threshold, risk multiplier, arbitration rule or capital rule is retuned between folds.**

### Aggregate OOS account

In addition to per-fold results, all non-overlapping test windows are replayed as one chronological shared account:

```text
capital carries across OOS windows
positions can overlap across fold boundaries
simultaneous candidates share finite capital
later size depends on earlier realized equity
```

That aggregate equity curve is the stronger historical economic result.

### Default V28.22 gates

```text
3 folds
>= 67% fold-pass share
>= 67% folds where adaptive return >= static return
>= 30 aggregate accepted OOS trades
aggregate adaptive return > 0
aggregate adaptive PF >= 1.10
aggregate realized drawdown <= 12%
adaptive aggregate return >= static aggregate return
positive symbol share >= 67%
positive month share >= 50%
survive +10 bps additional round-trip friction
no accepted benchmark-only OOS trade
```

Verdicts:

```text
invalid_freeze_or_evidence
insufficient_walk_forward
fail
static_baseline_better_oos
mixed
pass_for_future_freeze
```

`pass_for_future_freeze` is **not** permission to paper/live trade. It means the exact framework has earned the right to be frozen before genuinely future data exists.

Detailed methodology:

```text
docs/FROZEN_ADAPTIVE_PORTFOLIO_WALK_FORWARD_V28_22.md
```

## Reproducible runtime artifacts

V28.20:

```text
data/backtest_reviews/adaptive_evidence/
```

V28.21:

```text
data/backtest_reviews/shared_account_replays/
```

V28.22 freeze records:

```text
data/backtest_reviews/adaptive_portfolio_freezes/
```

V28.22 validation snapshots:

```text
data/backtest_reviews/adaptive_portfolio_walk_forward/
```

These stay outside Git.

## Critical limitations that remain

The project should continue to criticize itself aggressively.

### Realized vs mark-to-market drawdown

V28.21/V28.22 account drawdown is primarily realized-equity drawdown at exits.

Concurrent BTC/ETH/SOL positions can be substantially underwater at the same time before any exit occurs. Synchronized mark-to-market portfolio drawdown remains a material missing risk view.

### Correlation model

Same-direction exposure caps are useful but crude.

TRAI does not yet maintain rolling covariance/beta/factor-risk estimates or crisis-correlation stress scenarios.

### Historical execution abstraction

OHLCV backtests cannot reconstruct exact order-book queue position, latency or fill uncertainty.

More microstructure data should be added only if the slower-timeframe edge survives the current validation ladder.

### Historical selection contamination

V28.22 reuses a period already involved in research/candidate selection.

That is why `pass_for_future_freeze` explicitly leads to a new future-data clock rather than to deployment.

### Benchmark-only compression

A benchmark-only compression trade accepted in OOS validation blocks production-oriented promotion.

### No leverage optimization

This is intentional. The project should first prove a conservative unlevered/low-exposure edge before adding leverage/liquidation complexity.

## What should not be trusted yet

- pre-V28.18 historical results for promotion;
- incomplete or gap-ridden OHLCV history;
- one-window winners;
- universal Market State confidence thresholds;
- pooled independent-strategy PnL as a deployable account result;
- V28.20 counterfactual results without V28.21 capital constraints;
- a V28.21 winner that fails V28.22 chronological stability;
- a V28.22 pass as if it were pristine future evidence;
- any strategy/router/framework changed after its validation freeze;
- any live-execution assumption not tested prospectively.

## Current validation ladder

```text
V28.19 trustworthy OHLCV history
-> V28.18 closed-bar timing
-> V28.16 Market State Identifier
-> V28.17 historical state replay
-> controlled static family baselines
-> V28.20 adaptive economic evidence
-> V28.21 one finite shared account
-> V28.22 frozen adaptive portfolio walk-forward
-> new complete-framework freeze BEFORE future data
-> genuinely future adaptive portfolio holdout
-> frozen account-aware paper validation
-> constrained live execution only later
```

## Immediate runtime work once real history exists

1. Populate/refresh actual local 12-month BTC/ETH/SOL 1h + 4h history with V28.19.
2. Require coverage and continuity to be clean.
3. Rerun representative family backtests under V28.18 timing integrity.
4. Inspect V28.17 Market State churn, confidence and directional diagnostics.
5. Run V28.20 and reject adaptation if it does not improve exposure-normalized economics after friction.
6. Run V28.21 and reject adaptation if a simpler static shared account is stronger.
7. Only a V28.21 `shared_account_edge_candidate` can be frozen for V28.22.
8. Run the same complete framework across V28.22 chronological OOS folds.
9. Only `pass_for_future_freeze` should start the next genuinely future validation protocol.

## Development direction

Future releases should continue as larger end-to-end steps, but complexity must earn its place economically.

Priority remains:

```text
better economic truth
> stronger falsification
> more realistic risk
> more indicators
> more strategies
> prettier signal output
```

The highest-value next step after a real V28.22 pass is **not another strategy**. It is a genuinely future adaptive-portfolio freeze/holdout using the exact same framework hash, followed by account-aware paper validation if that unseen test survives.
