# V28.24 — Frozen Prospective Paper Validation

## Purpose

V28.24 is the first TRAI validation stage that records decisions **before their market outcomes are known**.

It starts only after the exact adaptive portfolio has already survived:

```text
historical research
-> closed-bar/data-integrity gates
-> adaptive-vs-static economic evidence
-> finite shared-account replay
-> frozen historical walk-forward
-> genuinely future V28.23 holdout
-> V28.23 pass_for_frozen_paper
-> V28.24 prospective paper observation
```

V28.24 does not submit exchange orders and does not grant automatic live-trading permission.

## Why a separate paper stage is necessary

V28.23 uses genuinely future market data, but its trades are still reconstructed after the fact from OHLCV. That is much stronger than reusing the research year, yet it cannot reveal several operational problems:

- whether the process was actually running when a signal had to be made;
- how late the decision process was after candle close;
- whether a usable quote existed at the time;
- the difference between the signal reference close and the observable executable price;
- downtime and missing-candle incidents;
- shared-account mark-to-market drawdown while positions remain open;
- whether the adaptive layer still beats a simpler static shadow account prospectively.

V28.24 is designed to expose those gaps instead of hiding them behind another reconstructed backtest.

## Eligibility and freeze

A paper session can only be created from a saved V28.23 result with:

```text
pass_for_frozen_paper
```

The source V28.23 freeze must still pass its own integrity and current-framework checks.

The V28.24 freeze binds:

```text
V28.23 freeze id + record SHA-256
V28.23 future framework SHA-256
saved V28.23 result summary SHA-256
V28.24 policy SHA-256
prospective-paper implementation file SHA-256 values
```

The result is a new:

```text
paper_framework_sha256
record_sha256
```

The exact same passed V28.23 result is allowed one paper clock by default. Restarting the clock after seeing paper outcomes is refused.

## Prospective boundary

The paper session does not begin immediately when its freeze is created.

For a 1h entry timeframe, if the freeze is created at:

```text
12:30 UTC
```

then the 12:00-13:00 candle is already partially observed. The first completely unseen candle starts at 13:00 and closes at 14:00, so:

```text
first eligible decision = 14:00 UTC
```

This prevents a partially observed candle from becoming the first prospective decision.

Default paper window:

```text
target_paper_days = 30
```

The target endpoint is fixed at freeze time.

## No retroactive signal reconstruction

This is the most important operational rule in V28.24.

Default allowed decision lag:

```text
max_decision_lag_minutes = 10
```

If a 1h candle closes at 14:00 and the paper cycle runs by 14:10, TRAI may record the decision using only information available at that close plus the observable quote at cycle time.

If the process comes back at 14:30, the 14:00 decision is recorded as:

```text
MISSED_DECISION
retroactive_signal_created = false
```

TRAI does **not** reconstruct the signal at 14:30 as if the process had been running at 14:00.

If the closed candle itself is missing from the local store beyond the punctuality window, the event becomes:

```text
DATA_GAP_MISSED_DECISION
```

This turns runtime reliability into part of the evidence.

## Decision generation

The frozen strategies are evaluated with the same causal information contract used by the hardened historical engine:

```text
closed entry candle
+ latest closed analysis candle
+ higher-timeframe candles available at that time
-> frozen strategy signal
```

Market State is computed separately at the latest closed analysis-bar decision time. Higher-timeframe state context must have been available at that state time.

The frozen router then checks:

```text
state freshness
preferred strategy family
router action
route direction
```

The market response may change because the observed state changed. The rules are not allowed to change during the paper session.

## Entry modes

### next_open

A signal generated at candle close becomes a paper candidate at that decision boundary. The current observable top of book is used when available.

### confirm_bar

A signal is stored prospectively as pending. The next closed bar must confirm direction. If confirmed, entry is created at the following decision boundary using the then-observable quote. If confirmation fails, the signal is explicitly rejected.

No future confirmation is consulted at the original signal time.

## Observable execution-price proxy

V28.24 attempts to capture Binance USD-M:

```text
best bid
best ask
mark price
spread
```

For a small paper LONG, entry uses approximately:

```text
best ask + configured adverse slippage
```

For a paper SHORT:

```text
best bid - configured adverse slippage
```

If an executable top-of-book quote is unavailable, the session may use a clearly marked proxy based on reference/mark price and configured spread/slippage. Proxy usage lowers executable-quote coverage and can block promotion.

This is still not an exchange fill. It does not prove queue position, partial fills, latency spikes or market impact.

## Static shadow account

Every prospective frozen family signal feeds a static shadow portfolio.

The adaptive account receives only candidates that match the frozen Market State/Router policy.

Both accounts use the same finite-capital constraints:

```text
same starting equity
same base risk budget
same max total open risk
same max position/symbol exposure
same max gross exposure
same same-direction exposure cap
same concurrent-position limits
same fee/slippage assumptions
```

This creates a direct prospective test of whether the adaptive architecture earns its complexity.

If the static shadow account beats the adaptive account on both return and profit factor, V28.24 returns:

```text
static_baseline_better_paper
```

Complexity receives no special credit.

## Strategy occupancy and cooldown

Portfolio rejection must not make a strategy appear to have been idle if the strategy itself would already have had an open trade.

V28.24 therefore maintains a separate virtual strategy-position layer. It approximates the source strategy's one-trade-at-a-time behavior and cooldown independently of whether the finite account accepted that candidate.

This is important when comparing an account-level replay with the original individual strategy semantics.

## Position exits

Open prospective positions are advanced only using candles that have subsequently closed.

The existing frozen exit family / TP ladder / breakeven / lock rules are evaluated with `evaluate_trade_outcome`.

Configured fee, spread/slippage proxy and funding assumptions are applied to the paper account.

A frozen `max_hold_bars` can force a time exit after the configured maximum holding period.

Current limitation: the exact intrabar timestamp of TP/SL is not available from OHLCV. The account records the exit when the cycle observes the closed candle containing the outcome. This is conservative operational bookkeeping, not exchange-level execution reconstruction.

## Mark-to-market account equity

Unlike V28.21-V28.23 realized-exit-only account drawdown, V28.24 marks open positions at each recorded paper cycle.

It records:

```text
realized equity
unrealized PnL
MTM equity
MTM peak
MTM drawdown
gross exposure
open risk
open position count
```

This is substantially closer to real account risk.

It can still miss intrabar worst-case drawdown between cycles, so the resulting drawdown remains an approximation rather than a liquidation-risk model.

## Tamper-evident event ledger

Every paper event is appended to:

```text
data/paper_validation/sessions/<session_id>/events.jsonl
```

Each event stores:

```text
previous_event_sha256
event_sha256
```

Changing or deleting an old event breaks the chain.

Examples of events:

```text
SESSION_STARTED
DECISION_SLOT_RECORDED
MISSED_DECISION
DATA_GAP_MISSED_DECISION
STRATEGY_SIGNAL
STRATEGY_SIGNAL_BLOCKED
CONFIRMATION_PENDING
CONFIRMATION_ACCEPTED
CONFIRMATION_REJECTED
PORTFOLIO_CANDIDATE
PAPER_POSITION_OPENED
PAPER_POSITION_CLOSED
PAPER_CANDIDATE_REJECTED
MTM_SNAPSHOT
```

The paper state itself is runtime data and is intentionally outside Git.

## Default quality gates

Current V28.24 policy includes:

```text
30 fixed paper days
<= 10 minute decision lag
>= 120 timely decision slots
>= 20 completed adaptive paper trades
>= 2 active symbols
adaptive paper return > 0
adaptive PF >= 1.05
MTM max drawdown <= 10%
positive-symbol share >= 67%
decision punctuality >= 95%
executable quote coverage >= 90%
data-gap share <= 2%
p95 adverse entry slippage <= 25 bps
adaptive return >= static return
adaptive PF >= static PF - 0.05
family notional concentration <= 75%
no accepted benchmark-only trade
```

The exact policy is versioned in:

```text
config/prospective_paper_validation_policy.json
```

## Verdicts

```text
invalid_paper_session
collecting_prospective_paper_evidence
paper_operationally_unreliable
insufficient_paper_evidence
paper_fail
static_baseline_better_paper
paper_mixed
pass_for_micro_live_review
```

`pass_for_micro_live_review` means only:

```text
prospective paper evidence is strong enough
for a separate manual micro-live risk review
```

It does **not** enable live execution.

The freeze itself stores:

```text
live_execution_enabled = false
```

and the policy stores:

```text
allow_live_execution = false
```

## Streamlit workflow

Open:

```text
Prospective Paper Validation
```

Then:

1. select a saved V28.23 `pass_for_frozen_paper` snapshot;
2. create the V28.24 paper freeze;
3. initialize the session;
4. run a paper cycle shortly after each entry candle closes;
5. monitor static/adaptive MTM equity, incidents, quote coverage and trades;
6. save a final reproducible V28.24 snapshot after the fixed window.

## CLI workflow

List sessions:

```bash
python paper_validation_cycle.py --list
```

Initialize:

```bash
python paper_validation_cycle.py --freeze <session_id-or-freeze-json> --init
```

Run one cycle and print current evidence:

```bash
python paper_validation_cycle.py --freeze <session_id-or-freeze-json> --evaluate
```

For a 1h entry timeframe, an unattended scheduler should normally run a few minutes after every UTC hour, while staying inside the 10-minute lag gate. The OHLCV collector must have written the newly closed candle first.

The CLI never sends exchange orders.

## Windows scheduling example

A practical runtime pattern is:

```text
hour closes
-> collector updates closed 1h data
-> around HH:05 run paper_validation_cycle.py
-> decision/event/account state persisted locally
```

If the machine is asleep or the process fails, the missed slot remains a negative operational observation. Do not backfill it later.

## What V28.24 can and cannot prove

### It can test

- whether frozen decisions were actually generated on time;
- whether adaptation still beats the simpler static account prospectively;
- real observed top-of-book spread/entry proxy availability;
- paper entry slippage versus the frozen decision reference;
- runtime/data reliability;
- finite shared-capital behavior;
- continuously sampled MTM account risk;
- whether the exact frozen framework survives a live market stream without hindsight.

### It still cannot prove

- actual fill probability;
- exchange queue priority;
- partial fills;
- true latency distribution from order submission to fill;
- market impact at larger size;
- liquidation mechanics under leverage;
- exact intrabar portfolio drawdown;
- profitability with real capital.

Those belong to a later execution-quality / micro-live stage and should only be considered if V28.24 genuinely passes.

## Money-making interpretation

V28.24 is deliberately capable of rejecting an attractive historical system for operational reasons.

A strategy that has good backtests but:

```text
misses 15% of real decision windows
requires stale/proxy quotes
has poor observed entry prices
suffers larger prospective MTM drawdowns
or loses its edge versus the static shadow account
```

has not demonstrated a deployable edge.

That is the point of the stage. The objective is not to collect another green badge. It is to make it progressively harder for TRAI to fool us before any real money is exposed.
