# Local Trading Lab — Project Recap and Direction

_Last updated for V28.19._

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
-> strategy family / direction / entry / exit / risk / WAIT
-> evidence and validation
```

The market response may change from bar to bar, but the rules that define state, routing and validation must remain explicit and auditable.

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
- **V28.18** — Closed-Bar Timing Integrity for the historical backtester: closed HTF availability, no backward fill and causal pivots.
- **V28.19** — Historical Data Integrity: unfinished-candle filtering, recent-candle overlap, retry/backoff, continuity audit and targeted internal-gap repair.

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
  -> Market State Identifier
  -> Adaptive Router
  -> research / validation
```

## Historical data policy

Generated history is runtime data and remains ignored by Git:

```text
data/ohlcv_store/
data/backfill_reports/
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

## V28.19 historical integrity contract

Historical research should now use the following ingestion contract:

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

## Market State Identifier

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

V28.17 replays the state identifier through historical closed bars and asks whether the state model is coherent before using it as an execution gate.

It measures:

```text
state distribution
state transitions
state dwell time / churn
confidence calibration
preferred family/action distribution
later 4h / 12h / 24h directional agreement
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
one trade at a time
```

## Strategy Family Registry

Research assignment is explicit rather than inferred only from names.

Registry metadata records:

```text
research family
research inclusion
historical data requirements
historical readiness
priority
benchmark-only status where relevant
```

The richer compression/order-flow strategies that require historical open interest or order-book data remain blocked from OHLCV-only long-history claims.

V28.12 therefore provides a deliberately simple `OHLCV Compression Breakout Benchmark` as a research control. It is benchmark-only and not a production recommendation.

## Evidence ladder

### V28.13 Evidence Review

Completed research batches are reduced to explicit evidence gates rather than judged by appearance or an LLM score.

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

Main checks include sample size, net expectancy after friction, profit factor, pair/month stability, drawdown and friction drag.

### V28.14 Walk-Forward Validation

A surviving non-benchmark candidate uses the exact same hashed strategy payload through expanding chronological holdouts.

Default shape for roughly one year:

```text
train months 1-6  -> test 7-8
train months 1-8  -> test 9-10
train months 1-10 -> test 11-12
```

This is a frozen temporal-stability test, not a pristine future holdout if candidate selection already used the same year.

### V28.15 Fresh Holdout

A strategy/policy is frozen before the future data exists. The future test begins on the next UTC date after the freeze cutoff and uses a fixed endpoint.

Default:

```text
target_holdout_days = 60
minimum_observation_days = 30
allow_preliminary_queue = false
```

A failed holdout must not be repaired by tuning on the same failed future window. A redesign requires a new hash and a new future-data clock.

For the final adaptive system, the freeze must include both strategy payloads and the exact Market State / Adaptive Router policies.

## Runtime Cycle

The safe operating sequence remains:

```text
incremental history refresh
-> coverage + continuity checks
-> safe analysis
-> Market State snapshot
-> runtime report
```

Inline analysis keeps paper/live automation disabled. Research preparation remains opt-in and guarded.

## What is experimental

- Market-state confidence is an explainable score, not a calibrated universal probability yet.
- Adaptive routing is still research/display behavior, not a hard live or paper gate.
- V28.17 directional accuracy does not prove profitable execution.
- The compression benchmark is a research control, not proven alpha.
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
- Walk-forward results where the frozen payload changed.
- Holdouts whose strategy/router/window changed after the freeze.
- Live execution behavior that has not passed historical, future and paper validation.

## Current validation ladder

```text
V28.19 trustworthy OHLCV history
-> V28.18 closed-bar timing
-> V28.16 Market State Identifier
-> V28.17 historical state replay
-> controlled three-family baseline
-> V28.13 Evidence Review
-> reject weak candidates
-> V28.14 frozen walk-forward
-> freeze complete adaptive framework
-> V28.15 genuinely future holdout
-> frozen paper validation
-> constrained live execution only later
```

## Immediate next research step once real history exists

1. Populate/refresh the actual local 12-month BTC/ETH/SOL 1h + 4h history with V28.19.
2. Require coverage and continuity to be clean.
3. Rerun research/backtests under the V28.18 timing contract.
4. Run V28.17 Market State Replay and inspect state churn/confidence/directional diagnostics.
5. Compare the adaptive router against controlled static family baselines only after the data/timing foundation is clean.
6. Let V28.13 reject weak evidence before expensive validation.

Do not claim the 12-month dataset exists on a runtime machine until that runtime has actually been checked.

## Later

- Frozen paper validation of the complete adaptive framework after future-holdout evidence.
- Historical funding/OI only if evidence justifies the extra data complexity.
- Persistent hourly/daily runtime scheduling on a chosen host.
- Mobile-first dashboard refinement.
- Optional VPS deployment for 24/7 monitoring.
