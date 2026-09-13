# Local Trading Lab — Project Recap and Direction

_Last updated for V28.20._

## Source of truth

GitHub is the canonical project memory and code history.

```text
seargik/local-trading-lab
```

Runtime market data is deliberately separate from Git and belongs on the machine that runs the app: local PC, Codespaces for temporary testing, or a future persistent VPS.

## Product definition

TRAI is currently best described as:

```text
A local crypto market-state and strategy-validation lab.
```

It is not yet a production autonomous trading bot.

The target behavior is adaptive:

```text
trustworthy market data
-> Market State Identifier
-> Adaptive Router
-> static family evidence
-> adaptive economic evidence
-> stronger chronological/future validation
-> only later paper/live execution
```

The market response may change from bar to bar, but the rules that define state, routing, risk and validation must remain explicit, versioned and auditable.

## Version status

- **V28.4** — repo-ready GitHub baseline, Codespaces/devcontainer and lifecycle scaffold.
- **V28.5** — demo mode and lifecycle-to-strategy fit labels.
- **V28.6** — historical Binance USD-M OHLCV backfill and incremental refresh.
- **V28.7** — Data / History Manager and default 12-month history target.
- **V28.8** — Lifecycle Gate Backtest Lab for trade-level counterfactual research.
- **V28.9** — Research Command Center and narrow three-family research protocol.
- **V28.10** — Runtime Cycle for data refresh, analysis and Market State snapshots.
- **V28.11** — explicit Strategy Family Registry and historical-readiness audit.
- **V28.12** — OHLCV-only Compression Breakout Benchmark to complete the three-family historical protocol.
- **V28.13** — Evidence Review / Research Scorecard with conservative reject/retain gates.
- **V28.14** — frozen-payload chronological walk-forward and pair-transfer validation.
- **V28.15** — tamper-evident research freeze and fixed genuinely future holdout protocol.
- **V28.16** — first-class Market State Identifier and explainable Adaptive Router.
- **V28.17** — closed-bar Historical Market State Replay and state/adaptation diagnostics.
- **V28.18** — Closed-Bar Timing Integrity: closed HTF availability, no backward fill and causal pivots.
- **V28.19** — Historical Data Integrity: unfinished-candle filtering, recent-candle overlap, retry/backoff, continuity audit and targeted internal-gap repair.
- **V28.20** — Adaptive Evidence & Economic Viability Lab: static-vs-adaptive economic comparison, WAIT economics, friction stress, pair/month/family stability, capital-overlap critique, integrity gates and reproducible policy-hashed evidence snapshots.

## Current architecture

```text
GitHub
  -> code
  -> docs
  -> smoke tests
  -> PR / version history

Runtime host
  -> Streamlit
  -> Runtime Cycle
  -> collectors / analyzer / backtest workers
  -> local parquet OHLCV history

Binance USD-M klines
  -> V28.19 integrity-safe backfill/update
  -> data/ohlcv_store
  -> V28.18 closed-bar feature clock
  -> static family backtests

Closed historical data
  -> V28.16 Market State Identifier + Adaptive Router
  -> V28.17 state replay

Static family trades + historical router states
  -> V28.20 Adaptive Evidence & Economic Viability
  -> reproducible policy-hashed evidence snapshot
  -> stronger validation only if evidence survives
```

## Historical data policy

Generated history is runtime data and remains ignored by Git:

```text
data/ohlcv_store/
data/backfill_reports/
data/backtest_reviews/adaptive_evidence/
```

Default universe:

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

The current historical store contains OHLCV only. Funding, open interest, liquidations and order-book history are separate datasets and must not be silently assumed to exist.

Do not claim that the 12-month dataset is populated on a runtime until that runtime has actually been checked.

## V28.19 historical integrity contract

Historical research uses:

```text
only fully closed candles
+ prune previously stored unfinished tail rows
+ overlap latest 2 candles on update-only refresh
+ retry transient Binance/network failures
+ audit internal timestamp continuity
+ repair internal gaps when possible
+ report unresolved gaps explicitly
```

A V28.19 result exposes:

```text
discarded_unclosed_rows
pruned_unclosed_rows
retries_used
overlap_bars
gaps_before
missing_rows_before
repair_attempts
gaps_repaired
gaps_remaining
missing_rows_remaining
integrity_status
```

`integrity_status = ready` means the observed stored range has no internal continuity gap and no unfinished row. It does not replace the existing coverage/freshness requirement.

Recommended regular refresh:

```powershell
.\.venv\Scripts\python.exe backfill_default_history.py --lookback 12mo --update-only --overlap-bars 2 --request-analysis
```

## V28.18 historical timing contract

A candle is not available to historical strategy logic when it opens. It becomes usable only after it closes.

```text
candle open
-> candle develops
-> candle close
-> features become available
-> strategy/state decision
-> later entry
```

V28.18 also removes historical backward fill and replaces centered swing pivots with causally confirmed pivots.

New backtests carry:

```text
timing_integrity_version = 28.18
closed_bar_only = true
```

Pre-V28.18 backtests are legacy evidence and should be rerun before any new promotion decision.

## Market State Identifier and Adaptive Router

V28.16 exposes, per analyzed pair:

```text
lifecycle state
direction
trend strength
volatility state
market structure
HTF alignment
confidence
preferred strategy family
router action
route direction
risk multiplier
entry mode
exit family
reasons
```

The default policy is versioned in:

```text
config/market_state_router_policy.json
```

Typical routing hypotheses:

```text
compression_building -> prepare compression_breakout
breakout_attempt      -> compression_breakout candidate when confirmed
trend_entering        -> prepare trend_pullback
trend_pullback_entry  -> trend_pullback candidate
trend_running         -> wait for pullback rather than chase
range_chop            -> range_reversion only near a range edge
trend_extended_late   -> protect / wait
trend_exhaustion      -> avoid continuation, wait for confirmation
panic_volatility      -> WAIT / reduced exposure hypothesis
```

The confidence and risk mappings are hypotheses to validate, not universal probabilities.

## V28.17 Historical Market State Replay

V28.17 replays the state identifier through historical closed bars and measures:

```text
state distribution
state transitions
state dwell time / churn
confidence calibration
preferred family/action distribution
later directional agreement
no-lookahead audit
```

Directional agreement is diagnostic only. It is not equivalent to trading profitability.

## Core research protocol

Research remains deliberately narrow:

```text
trend_pullback
compression_breakout
range_reversion
```

First-pass research symbols:

```text
BTCUSDT
ETHUSDT
SOLUSDT
```

Default protocol:

```text
entry timeframe:    1h
analysis timeframe: 4h
lookback:            365 days
friction:            binance_usdm_taker_light
allow long + short
```

Strategy-family assignment is explicit in the Strategy Family Registry.

The richer compression/order-flow strategies that require historical open interest or order-book data remain blocked from OHLCV-only long-history claims. V28.12 therefore provides `OHLCV Compression Breakout Benchmark` as a deliberately simple `benchmark_only` research control. It is not production-grade compression evidence.

## V28.20 Adaptive Evidence & Economic Viability

V28.20 asks a direct economic question:

> Does the fixed adaptive policy select better trading conditions than the same strategy families running statically?

For each completed core-research batch it chooses one representative run per family by registry priority, not historical PnL, then performs a causal backward join:

```text
trade signal_time
-> latest router decision_time <= signal_time
-> state must be recent and lookahead-safe
-> preferred family must match
-> router action must be TRADE_CANDIDATE
-> router direction must match trade side
-> apply fixed router risk multiplier
```

It compares static and adaptive evidence using:

```text
trades
fixed-stake PnL
profit factor
expectancy bps per capital turn
expectancy R
max drawdown
drawdown / net profit
pair stability
month stability
family concentration
```

### WAIT economics

WAIT is measured rather than treated as no output:

```text
avoided losing trades / loss
missed winning trades / profit
excluded net PnL
WAIT value = -excluded net PnL
```

A router that skips many losers but skips even more profit is not adding economic value.

### Friction stress

V28.20 adds extra round-trip friction of:

```text
0, 5, 10, 20, 40 bps
```

on top of source-backtest friction and reports approximate break-even extra friction per capital turn.

### Capital realism

The current V28.20 comparison is a **counterfactual selector/weighting layer over independent family backtests**. It is not yet an exact shared-account replay.

Therefore V28.20 explicitly measures concurrent selected trades. If several family trades overlap, summed PnL can overstate what one account could deploy and is criticized in the result.

### Integrity and promotion blockers

V28.20 requires:

```text
V28.18 timing integrity on representative saved runs
+ V28.19 source-data continuity for the research window
```

If the benchmark-only compression strategy participates, the result cannot exceed `promising_research_only` regardless of headline performance.

Verdicts:

```text
no_adaptive_evidence
reject
insufficient_evidence
promising_research_only
adaptive_edge_candidate
```

`adaptive_edge_candidate` means only that the fixed historical hypothesis has earned stronger validation. It does not predict future profit and does not enable paper/live execution.

Policy:

```text
config/adaptive_evidence_policy.json
```

Detailed methodology:

```text
docs/ADAPTIVE_EVIDENCE_V28_20.md
```

## Reproducible adaptive evidence

Each V28.20 UI run writes a snapshot under:

```text
data/backtest_reviews/adaptive_evidence/
```

The manifest fingerprints:

```text
adaptive evidence policy
market-state router policy
market-state replay policy
```

using canonical SHA-256 hashes. This is the bridge to later frozen validation: a future test must be able to prove exactly which adaptive logic produced the historical result.

## Existing evidence ladder

### V28.13 Evidence Review

Completed static research batches use transparent evidence gates for sample size, friction-aware net results, profit factor, pair/month stability and drawdown.

### V28.14 Walk-Forward Validation

A surviving non-benchmark static candidate uses the same hashed strategy payload through expanding chronological holdouts. It is temporal stability, not a pristine future test when candidate selection already saw the same year.

### V28.15 Fresh Holdout

A strategy/policy is frozen before future data exists. The endpoint is fixed before outcomes are known. A failed future holdout cannot be repaired by tuning on the same failed window.

For the adaptive system, the future freeze must eventually include:

```text
strategy payloads
market-state policy
router policy
adaptive-evidence policy
execution assumptions
capital/risk policy
```

## What is experimental

- Market-state confidence is an explainable score, not a calibrated universal probability yet.
- Adaptive routing is not a hard live/paper gate.
- V28.17 directional accuracy does not prove profitable execution.
- V28.20 counterfactual selection does not equal a shared-account portfolio backtest.
- The compression benchmark is research control evidence, not proven alpha.
- Evidence/walk-forward/holdout thresholds are versioned research policy, not market laws.
- Historical OHLCV integrity cannot substitute for unavailable OI/funding/order-book history.
- Runtime scheduling is not yet a persistent 24/7 service.

## What should not be trusted yet

- Pre-V28.18 backtest results for promotion decisions.
- Any research run using incomplete or unresolved-gap history.
- One-window backtest winners.
- Universal score thresholds.
- Market-state routes that only work on one pair or one short period.
- Results before friction.
- Candidates with too few trades.
- Pooled adaptive PnL when trade concurrency is ignored.
- Walk-forward results where the frozen payload/policy changed.
- Holdouts whose strategy/router/window changed after the freeze.
- Live execution behavior that has not passed historical, future and paper validation.

## Current validation ladder

```text
V28.19 trustworthy OHLCV history
-> V28.18 closed-bar timing
-> V28.16 Market State Identifier
-> V28.17 historical state replay
-> controlled static family baselines
-> V28.20 adaptive economic evidence
-> exact account-aware adaptive replay
-> frozen adaptive walk-forward
-> freeze complete adaptive framework
-> V28.15-style genuinely future holdout
-> frozen account-aware paper validation
-> constrained live execution only later
```

## Immediate next research step once real history exists

1. Populate/refresh actual local 12-month BTC/ETH/SOL 1h + 4h history with V28.19.
2. Require coverage and continuity to be clean.
3. Rerun all selected family backtests under V28.18 timing integrity.
4. Run V28.17 Market State Replay and inspect churn/confidence/directional diagnostics.
5. Run V28.20 and reject the adaptive idea if it does not improve exposure-normalized economics after friction.
6. If V28.20 survives, build the next major methodological step: one **exact shared-account adaptive replay** with simultaneous-candidate arbitration and portfolio risk limits.

## Direction for larger development steps

Future releases should be broader end-to-end increments, but complexity must earn its place through evidence.

Priority is now:

```text
better economic truth
> more indicators
> more strategies
> prettier signal output
```

The next high-value development is not another strategy. It is a capital-aware adaptive portfolio simulator that can answer whether the router's apparent edge remains after simultaneous positions, finite capital, risk limits and a single causal equity curve.

Only after that should the full adaptive policy face frozen walk-forward, genuinely future data and paper execution.
