# Local Trading Lab — Project Recap and Direction

_Last updated for V28.21._

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

It is not a production autonomous trading bot.

The target behavior is adaptive, but every adaptive rule must remain explicit, versioned and auditable:

```text
trustworthy market data
-> closed-bar features
-> Market State Identifier
-> Adaptive Router
-> static strategy-family evidence
-> adaptive economic evidence
-> shared-account portfolio replay
-> frozen chronological/future validation
-> only later paper/live execution
```

## Version status

- **V28.4** — repo-ready GitHub baseline, devcontainer and lifecycle scaffold.
- **V28.5** — demo mode and lifecycle-to-strategy fit labels.
- **V28.6** — historical Binance USD-M OHLCV backfill and incremental refresh.
- **V28.7** — Data / History Manager and default 12-month history target.
- **V28.8** — Lifecycle Gate counterfactual research.
- **V28.9** — Research Command Center and narrow three-family protocol.
- **V28.10** — Runtime Cycle for data refresh, analysis and Market State snapshots.
- **V28.11** — explicit Strategy Family Registry and historical-readiness audit.
- **V28.12** — OHLCV-only Compression Breakout Benchmark.
- **V28.13** — Evidence Review / Research Scorecard with conservative gates.
- **V28.14** — frozen-payload chronological walk-forward and pair-transfer validation.
- **V28.15** — tamper-evident research freeze and genuinely future holdout protocol.
- **V28.16** — first-class Market State Identifier and explainable Adaptive Router.
- **V28.17** — closed-bar Historical Market State Replay.
- **V28.18** — Closed-Bar Timing Integrity: closed HTF availability, no backward fill and causal pivots.
- **V28.19** — Historical Data Integrity: unfinished-candle filtering, overlap refresh, retry/backoff, continuity audit and targeted gap repair.
- **V28.20** — Adaptive Evidence & Economic Viability: static-vs-adaptive economics, WAIT value, friction stress, stability, concurrency critique, integrity gates and policy-hashed snapshots.
- **V28.21** — Shared-Account Adaptive Portfolio Replay: one finite account, chronological capital allocation, risk/exposure caps, static-vs-adaptive account comparison, rejection ledger, account-level friction stress and reproducible portfolio snapshots.

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
  -> V28.17 historical state replay

Static family trades + historical router states
  -> V28.20 adaptive economic evidence
  -> V28.21 one finite shared-account replay
  -> reproducible policy-hashed snapshots
  -> stronger validation only if evidence survives
```

## Historical data policy

Generated history and evidence artifacts are runtime data and remain ignored by Git:

```text
data/ohlcv_store/
data/backfill_reports/
data/backtest_reviews/adaptive_evidence/
data/backtest_reviews/shared_account_replays/
```

Default research universe:

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

Default history target:

```text
12 months
1h + 4h
```

The current historical store is OHLCV-only. Funding, open interest, liquidations and order-book history are separate datasets and must never be silently assumed to exist.

Do not claim that the 12-month runtime dataset exists until that runtime has actually been checked.

## V28.19 historical integrity contract

Historical research uses:

```text
only fully closed candles
+ prune unfinished tail rows
+ overlap recent candles on update-only refresh
+ retry transient Binance/network failures
+ audit internal timestamp continuity
+ repair internal gaps when possible
+ report unresolved gaps explicitly
```

Recommended regular refresh:

```powershell
.\.venv\Scripts\python.exe backfill_default_history.py --lookback 12mo --update-only --overlap-bars 2 --request-analysis
```

`integrity_status = ready` means the observed stored range has no internal continuity gap and no unfinished row. It does not replace coverage/freshness checks.

## V28.18 historical timing contract

Historical strategy logic may use a candle only after that candle closes.

```text
candle open
-> candle develops
-> candle close
-> features become available
-> strategy/state decision
-> later entry
```

V28.18 also removes backward fill and centered swing pivots that required future bars.

New backtests carry:

```text
timing_integrity_version = 28.18
closed_bar_only = true
```

Pre-V28.18 results are legacy evidence and should not be used for new promotion decisions.

## Market State Identifier and Adaptive Router

V28.16 exposes:

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

Typical hypotheses include:

```text
compression_building -> prepare compression_breakout
breakout_attempt      -> compression_breakout candidate
trend_entering        -> prepare trend_pullback
trend_pullback_entry  -> trend_pullback candidate
trend_running         -> wait for pullback rather than chase
range_chop            -> range_reversion near an edge
trend_extended_late   -> protect / wait
trend_exhaustion      -> avoid continuation
panic_volatility      -> WAIT / reduced exposure
```

These are research rules, not universal market truths.

## V28.17 Historical Market State Replay

V28.17 measures state distribution, transitions, dwell/churn, confidence calibration, preferred family/action distribution, later directional agreement and no-lookahead integrity.

Directional agreement remains diagnostic. It is not trading profitability.

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

Default protocol:

```text
entry timeframe:    1h
analysis timeframe: 4h
lookback:            365 days
friction:            binance_usdm_taker_light
allow long + short
```

Strategy-family assignment is explicit in the Strategy Family Registry.

The current OHLCV Compression Breakout strategy is intentionally `benchmark_only`. Richer compression/order-flow strategies that require historical OI or order-book data remain blocked from OHLCV-only long-history claims.

## V28.20 Adaptive Evidence & Economic Viability

V28.20 asks whether the fixed adaptive policy selects better conditions than the same strategy families running statically.

For each completed core-research batch it selects one representative run per family by registry priority, then causally joins the latest Market State/Router decision available before each strategy signal.

It measures:

```text
profit factor
expectancy per capital turn
pair/month stability
family concentration
WAIT value
additional-friction survival
capital overlap/concurrency
```

WAIT is treated as an economic decision: avoided losses and missed profits are reported separately.

V28.20 is still a selector/weighting layer over independent strategy backtests. It is not one deployable account. That gap is what V28.21 addresses.

## V28.21 Shared-Account Adaptive Portfolio Replay

V28.21 puts the historical candidates through one finite account in chronological order.

At each candidate entry time it:

```text
1. closes positions whose exits are already due
2. realizes PnL into current account equity
3. ranks same-time candidates without future PnL
4. applies shared risk/exposure limits
5. accepts or rejects candidates
6. sizes accepted positions from current equity
```

Default account policy:

```text
starting equity:             $10,000
base risk per trade:          0.50%
max total open risk:          1.50%
max position notional:       35.00%
max symbol notional:         35.00%
max gross exposure:         100.00%
max same-direction exposure: 70.00%
max concurrent positions:     3
max positions per family:     2
one position per symbol:       true
```

Static and adaptive scenarios face the same account constraints.

### Static shared-account baseline

Uses every trade with a causally available Market State observation. Same-time arbitration uses only strategy score and deterministic tie-breakers.

### Adaptive shared account

Uses only V28.20 router-matched candidates and scales desired risk using the fixed router risk multiplier. Same-time arbitration uses router confidence, router risk multiplier and strategy score — all known before entry.

### Capital rejection ledger

A candidate may be rejected because of:

```text
max concurrent positions
same symbol already open
family position cap
gross exposure cap
same-direction crypto exposure cap
open-risk cap
invalid timing/risk
```

This prevents independent backtests from implicitly reusing the same capital.

### Account-level outputs

V28.21 reports:

```text
starting / ending equity
net PnL and return
accepted / rejected candidates
profit factor
realized-equity drawdown
capital turnover
max concurrent positions
max gross exposure
max open risk
max same-direction exposure
pair / family / month contributions
static-vs-adaptive account uplift
```

### Account-level friction stress

The entire account is replayed again under additional round-trip friction:

```text
0, 5, 10, 20, 40 bps
```

Because costs change equity, each stress scenario also changes later compounded position sizes. It is therefore recomputed from the beginning rather than adjusted with a simple final subtraction.

### Important limitation: realized drawdown

The V28.21 equity curve realizes PnL at exits. It does not yet mark every open position to market candle by candle.

Therefore portfolio drawdown can be understated relative to the worst intratrade account drawdown. Trade-level MAE is preserved as a diagnostic proxy, but separate MAEs cannot be summed safely because their worst moments may not coincide.

### Correlation treatment

BTC/ETH/SOL are not treated as independent. V28.21 caps aggregate same-direction exposure.

This is intentionally simple. It is not yet a dynamic covariance/factor-risk model.

### No leverage optimization

The default maximum gross notional is 100% of equity. Leverage and liquidation are deliberately excluded until the edge survives stronger validation.

Adding leverage before proving the edge would make the simulator more exciting, not more truthful.

## V28.21 verdicts

```text
invalid_evidence
no_portfolio_evidence
reject
static_baseline_better
insufficient_evidence
promising_research_only
shared_account_edge_candidate
```

`static_baseline_better` is deliberately first-class. If the shared account earns more by simply running static candidates, then the adaptive router has failed its economic purpose even if it looks sophisticated.

`shared_account_edge_candidate` means only that the frozen account-aware historical hypothesis has earned stronger validation.

If any accepted adaptive trade is `benchmark_only`, production-oriented promotion remains blocked.

Policy:

```text
config/shared_account_policy.json
```

Detailed methodology:

```text
docs/SHARED_ACCOUNT_REPLAY_V28_21.md
```

## Reproducible evidence

V28.20 snapshots live under:

```text
data/backtest_reviews/adaptive_evidence/
```

V28.21 snapshots live under:

```text
data/backtest_reviews/shared_account_replays/
```

V28.21 fingerprints:

```text
shared-account policy
adaptive-evidence policy
market-state router policy
market-state replay policy
```

with canonical SHA-256 hashes.

Future validation must be able to prove that the exact same adaptive and capital policies are being tested.

## Existing evidence ladder

### V28.13 Evidence Review

Transparent static evidence gates for sample size, friction-aware results, PF, pair/month stability and drawdown.

### V28.14 Walk-Forward Validation

Frozen strategy payload through expanding chronological holdouts. This tests temporal stability, not a pristine future sample when the same historical year was already used for discovery.

### V28.15 Fresh Holdout

Freeze first, observe future data second. Endpoints are fixed before outcomes are known; a failed future window cannot be repaired by tuning against the same window.

For the full adaptive portfolio, the future freeze must include:

```text
strategy payloads
market-state policy
router policy
adaptive-evidence policy
shared-account capital/risk policy
execution assumptions
```

## What remains experimental

- Market-state confidence is explainable but not a universal calibrated probability.
- V28.17 directional agreement is not profitability.
- V28.20 is not shared-capital execution.
- V28.21 is capital-aware but still uses source trade paths from OHLCV backtests.
- V28.21 realized drawdown can understate synchronized intratrade mark-to-market drawdown.
- Correlation control is a same-direction cap, not a dynamic factor-risk model.
- The compression benchmark is research control evidence, not production alpha.
- Evidence thresholds are policy choices, not market laws.
- Runtime scheduling is not yet a persistent 24/7 service.

## What should not be trusted yet

- Pre-V28.18 backtest results for promotion.
- Research using incomplete or unresolved-gap history.
- One-window winners.
- Results before friction.
- Portfolio results that ignore rejected candidates or capital constraints.
- V28.21 drawdown as a full intratrade mark-to-market risk estimate.
- Walk-forward/future tests where frozen policies changed.
- Live behavior that has not passed historical, future and paper validation.

## Current validation ladder

```text
V28.19 trustworthy OHLCV history
-> V28.18 closed-bar timing
-> V28.16 Market State Identifier
-> V28.17 historical state replay
-> controlled static family baselines
-> V28.20 adaptive economic evidence
-> V28.21 shared-account portfolio replay
-> freeze the complete adaptive + capital policy
-> walk-forward the complete frozen portfolio
-> genuinely future holdout
-> frozen shared-account paper validation
-> constrained live execution only later
```

## Immediate next research step once real history exists

1. Populate/refresh actual local 12-month BTC/ETH/SOL 1h + 4h history with V28.19.
2. Require coverage and continuity to be clean.
3. Rerun selected family backtests under V28.18 timing integrity.
4. Run V28.17 Market State Replay.
5. Run V28.20 and reject the adaptive idea if it does not improve economic evidence after friction.
6. Run V28.21 and reject the architecture if the one-account adaptive replay cannot beat the same capital policy running static candidates.
7. Only if V28.21 survives, freeze the **whole** system and validate it chronologically and on genuinely future data.

## Direction for larger development steps

Future releases should remain broader end-to-end increments, but complexity must earn its place through evidence.

Priority:

```text
better economic truth
> better robustness / future validation
> better account-risk realism
> more indicators
> more strategies
> prettier signal output
```

The next major version should therefore validate the **complete frozen adaptive portfolio**, not add another collection of strategies. A strong V28.21 result should lead to portfolio-level walk-forward/future validation; a weak result should lead to rejection or simplification, not repeated tuning until the backtest looks good.
