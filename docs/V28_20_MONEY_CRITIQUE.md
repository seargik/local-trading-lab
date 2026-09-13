# V28.20 Money-Viability Critique

The purpose of TRAI is not to maximize the number of signals or the sophistication of the UI. The relevant question is whether a repeatable edge survives realistic constraints.

## Current strengths

The project now has a substantially better research foundation than a typical hobby backtest:

- explicit historical-data continuity checks;
- closed-bar/no-lookahead timing;
- friction-aware family backtests;
- a versioned Market State / Router policy;
- chronological and future-holdout concepts;
- explicit benchmark-only controls;
- an adaptive economic comparison rather than state-classification accuracy alone.

## Current weaknesses that can still destroy profitability

### 1. The adaptive result is not yet a shared-account portfolio

Independent family trades can overlap. Summed PnL can therefore assume capital is available several times at once. V28.20 exposes concurrency but does not yet solve allocation.

This is the most important next engineering problem.

### 2. Compression evidence is still a benchmark

The OHLCV compression benchmark is intentionally simple. A successful three-family adaptive result can therefore show that a compression *concept* helps without proving the production compression implementation.

### 3. State policy may be overfit even if strategies are not

A frozen strategy payload is insufficient if router thresholds were repeatedly adjusted after looking at the same history. The complete state/router/economic policy must be frozen before the strongest validation stages.

### 4. Backtest friction is still a model

Fees are relatively deterministic; realized spread, slippage, missed fills and volatility-dependent execution are not. A small friction margin is not economically credible.

### 5. Crypto pair results are correlated

BTC, ETH and SOL are not independent experiments. “Works on three pairs” overstates diversification if all three profits come from the same market-wide risk regime.

A future portfolio validation should report common-market exposure and concentration, not only pair counts.

### 6. Historical sample size can be misleading

Many trades from one sustained regime are less informative than fewer trades across genuinely different regimes. Month and state stability help, but do not fully solve dependence.

### 7. More strategies are not the priority

Adding many strategies increases multiple-testing risk and makes it easier to discover accidental winners. The current controlled families are enough to test the adaptive hypothesis.

## What would increase confidence materially

The next meaningful proof should be one exact causal account simulation:

```text
one chronological event stream
+ one starting account balance
+ one set of portfolio risk limits
+ simultaneous-candidate arbitration
+ finite capital / exposure
+ no overlapping double-use of capital
+ all existing friction
+ one equity curve
```

Then freeze the complete adaptive configuration and evaluate it on chronological out-of-sample and genuinely future data.

## Decision rule

If the adaptive system cannot produce a robust edge after these constraints, do not compensate by adding indicators, AI explanations or more strategy families. Reject or simplify the hypothesis.

If it survives, the next value comes from better execution/risk controls and paper validation, not from making the research UI more elaborate.
