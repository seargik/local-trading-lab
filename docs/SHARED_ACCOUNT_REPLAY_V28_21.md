# V28.21 — Shared-Account Adaptive Portfolio Replay

## Why this release exists

V28.20 answered whether the fixed Market State + Adaptive Router appeared to select better historical trade evidence than static strategy-family baselines.

That was necessary but not sufficient for a money-oriented conclusion because the underlying family trades were still simulated independently. Independent backtests can overlap and implicitly reuse the same capital several times.

V28.21 therefore asks the harder question:

> If static and adaptive candidates must compete for one finite account in chronological order, does the adaptive architecture still add economic value?

This remains research only. It does not place paper or live orders and does not change execution code.

## Source chain

```text
V28.19 integrity-safe OHLCV
-> V28.18 closed-bar strategy backtests
-> V28.16 Market State + Adaptive Router
-> V28.17 historical state replay
-> V28.20 causally annotated static/adaptive candidates
-> V28.21 one finite shared account
```

The source trade paths are still the independently simulated historical trades produced by the existing backtester. V28.21 makes their **capital allocation and coexistence** realistic; it does not turn OHLCV backtests into exchange/order-book execution truth.

## One-account chronology

Candidates are processed by actual historical entry time.

For each timestamp:

```text
1. close positions whose exit_time <= current entry_time
2. realize PnL into the single account equity
3. rank candidates using only information known before entry
4. apply account/risk/exposure limits
5. accept or reject each candidate
6. size accepted positions from current realized equity
```

Exits are processed before new entries at an identical timestamp so released capital can be reused without creating artificial overlap.

Future PnL is never part of candidate priority.

### Static baseline priority

```text
strategy score descending
-> symbol
-> family
-> deterministic candidate id
```

### Adaptive priority

```text
router confidence descending
-> router risk multiplier descending
-> strategy score descending
-> symbol
-> family
-> deterministic candidate id
```

These are fixed policy choices, not optimized from trade outcomes inside the replay.

## Position sizing

Default account:

```text
starting equity:             $10,000
base risk per trade:          0.50% of current realized equity
max total open risk:          1.50%
max position notional:       35.00% of equity
max symbol notional:         35.00% of equity
max gross exposure:         100.00% of equity
max same-direction exposure: 70.00% of equity
max concurrent positions:     3
max positions per family:     2
one position per symbol:       true
```

For an accepted candidate:

```text
desired risk USD
= current equity
  * base risk %
  * allocation weight

allocation weight
= 1.0 for static baseline
= router risk multiplier for adaptive replay

desired notional
= desired risk USD / historical stop-distance risk %
```

The desired notional is then capped by:

```text
per-position notional limit
symbol exposure limit
gross account exposure limit
same-direction crypto exposure limit
total open-risk limit
```

If the remaining notional is below the minimum position size, the candidate is rejected and the limiting constraint is recorded.

## Why the same-direction cap exists

BTC, ETH and SOL are not independent assets in the way unrelated multi-asset positions might be. Three simultaneous LONG positions can effectively become one concentrated crypto-beta bet.

V28.21 therefore caps aggregate LONG or SHORT notional separately.

This is still only a rough correlation control. It is not a covariance model, factor model or stress-correlation engine.

## Candidate rejection ledger

A candidate can be rejected for reasons including:

```text
signal_after_entry
nonpositive_equity
invalid_risk_pct
zero_allocation_weight
max_concurrent_positions
symbol_already_open
max_positions_per_family
capital_limit:position_cap
capital_limit:symbol_cap
capital_limit:gross_cap
capital_limit:same_direction_cap
capital_limit:open_risk_cap
```

This matters economically. A backtest may contain attractive signals that one account simply cannot fund at the same time.

The UI therefore reports accepted/rejected counts and rejection reasons rather than silently dropping excess opportunities.

## Static versus adaptive comparison

Both scenarios use the same starting equity and the same account constraints.

### Static shared account

Uses every V28.20 trade with a causally available Market State observation, regardless of whether the router preferred that family.

### Adaptive shared account

Uses only trades where V28.20 causally established:

```text
state available
+ eligible router action
+ preferred family matches trade family
+ route direction matches trade side
```

Router risk multiplier then affects the desired risk allocation.

The comparison therefore asks whether routing still improves economics **after both sides face the same finite capital**.

## Account outputs

Each scenario reports:

```text
starting equity
ending equity
net PnL
return %
accepted / rejected candidates
acceptance rate
win rate
profit factor
realized-equity maximum drawdown
capital turnover
average position notional
maximum concurrent positions
maximum gross exposure
maximum open risk
maximum same-direction exposure
active symbols / months / families
positive symbol share
positive month share
family notional concentration
benchmark participation
```

## Friction stress

V28.21 repeats the whole account replay with additional round-trip friction:

```text
0 bps
5 bps
10 bps
20 bps
40 bps
```

This is intentionally replayed from the beginning for every stress level because extra costs change account equity and therefore later compounded position sizes.

The default promotion gate requires the adaptive account to remain positive at at least +10 bps additional round-trip friction.

## Reproducible snapshots

The UI can save a V28.21 snapshot under:

```text
data/backtest_reviews/shared_account_replays/
```

The snapshot stores:

```text
static/adaptive comparison
friction stress
accepted ledgers
rejected candidates
static/adaptive equity curves
pair/family/month contributions
verdict and critique
source job metadata
```

It also fingerprints these policies with canonical SHA-256 hashes:

```text
shared-account policy
adaptive-evidence policy
market-state router policy
market-state replay policy
```

This prevents a later validation result from quietly using a different risk/router policy while claiming to validate the same historical hypothesis.

## Verdicts

```text
invalid_evidence
no_portfolio_evidence
reject
static_baseline_better
insufficient_evidence
promising_research_only
shared_account_edge_candidate
```

`static_baseline_better` is an important explicit outcome. More sophisticated routing is not automatically better. If the same account performs better by simply running the static candidates, the adaptive layer has failed its economic purpose.

`shared_account_edge_candidate` is **not** permission to trade live. It means the fixed account-aware historical hypothesis has earned stronger frozen validation.

## Promotion gates

Default gates include:

```text
source timing/data integrity passes
source V28.20 result is not rejected
>= 60 accepted adaptive trades
>= 2 active symbols
>= 6 active months
>= 2 active families
positive account return
profit factor >= 1.15
realized maximum drawdown <= 12%
positive-symbol share >= 67%
positive-month share >= 55%
family notional concentration <= 75%
capital rejection share <= 70%
adaptive return or PF improves over static
survives required friction stress
```

If an accepted adaptive trade comes from a `benchmark_only` strategy, production-oriented promotion remains blocked even when every metric gate passes.

## Money-oriented critique

V28.21 fixes one of the largest sources of false confidence: treating overlapping independent backtests as one deployable return stream.

It still does **not** prove that TRAI can make money. Important remaining gaps are:

### 1. Intratrade portfolio drawdown

The shared account realizes equity at exits. It does not yet synchronize mark-to-market PnL for all open positions candle by candle.

Therefore reported drawdown can be lower than the actual intratrade drawdown an account would experience.

Trade-level `mae_pct` is carried into the accepted ledger as a diagnostic proxy, but MAE from different trades cannot simply be added because their worst excursions may happen at different times.

### 2. Correlation is simplified

The same-direction exposure cap is better than pretending BTC/ETH/SOL are independent, but it is still a coarse rule.

A stronger future layer could estimate rolling return correlation or crypto-beta concentration from information available at each historical time. That should be added only if V28.21 evidence justifies the complexity.

### 3. Source trade paths are still backtests

Entry/exit paths come from the OHLCV backtester. V28.21 does not add order-book queue position, partial fills, liquidation mechanics or latency-dependent execution.

This is acceptable for slower 1h/4h research but must remain a limitation in any money claim.

### 4. No leverage optimization

V28.21 deliberately uses a conservative 100% gross notional limit by default and does not model leverage/liquidation.

That is intentional. Adding leverage before a robust edge exists would make the simulator more exciting, not more truthful.

### 5. Benchmark-only compression still matters

The current OHLCV Compression Breakout strategy is a research benchmark. If it contributes accepted portfolio trades, the account cannot be treated as a production-ready three-family system.

The correct response is not to remove the flag. Either obtain stronger historical compression evidence or develop a non-benchmark OHLCV strategy only after independent evidence supports doing so.

## What should happen next

If real V28.21 results are weak:

```text
reject or simplify the adaptive architecture
```

Do not optimize portfolio limits repeatedly against the same historical sample until it passes.

If real V28.21 results are strong:

```text
freeze strategies
+ freeze Market State policy
+ freeze Router policy
+ freeze V28.20 economic policy
+ freeze V28.21 capital/risk policy
-> chronological walk-forward of the complete system
-> genuinely future holdout
-> frozen shared-account paper validation
```

The next major engineering step after a strong V28.21 result should therefore be **validation of the whole frozen adaptive portfolio**, not additional strategy proliferation and not live execution.
