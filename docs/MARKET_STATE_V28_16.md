# V28.16 — Market State Identifier + Adaptive Router

## Purpose

V28.16 makes current market state a first-class object in TRAI.

The question it answers is:

```text
What is this market doing right now, and what behavior should the research system prefer?
```

The identifier is deliberately separate from live execution. It explains and routes research behavior, but it does not block or place paper/live trades.

## State object

For each analyzed symbol, V28.16 exposes:

```text
lifecycle state
direction
trend strength
volatility state
market structure
higher-timeframe alignment
confidence
preferred strategy family
router action
route direction
risk multiplier
entry mode
exit family
reasons / diagnostics
```

Example:

```text
BTCUSDT
State: Trend Pullback Entry
Direction: LONG
Trend strength: MODERATE
Volatility: NORMAL
Structure: BULLISH
HTF: ALIGNED
Confidence: 78%
Preferred family: trend_pullback
Action: TRADE_CANDIDATE
Risk: 0.75x
```

## Adaptive routing

The router adapts behavior to the detected market state while keeping the routing policy fixed and auditable.

Default research routes:

| State | Preferred family | Router behavior |
| --- | --- | --- |
| `compression_building` | `compression_breakout` | prepare, no trade yet |
| `breakout_attempt` | `compression_breakout` | candidate if direction/confidence are clear |
| `trend_entering` | `trend_pullback` | prepare for confirmation/pullback |
| `trend_pullback_entry` | `trend_pullback` | candidate |
| `trend_running` | `trend_pullback` | wait for next pullback rather than chase |
| `trend_extended_late` | none | protect / wait |
| `trend_exhaustion` | none | wait for reversal confirmation |
| `range_chop` | `range_reversion` | only at a range edge |
| `liquidity_sweep_reversal_risk` | none | wait for reversal confirmation |
| `panic_volatility` | none | wait |

The mapping is stored in:

```text
config/market_state_router_policy.json
```

## Direction handling

Trend and breakout states use the lifecycle direction.

Range state is handled differently:

```text
range position <= 0.20 -> LONG reversion candidate
range position >= 0.80 -> SHORT reversion candidate
middle of range         -> WAIT_RANGE_MID
```

This prevents a broad trend-direction label from driving a mean-reversion entry in the middle of a range.

## Confidence

The existing lifecycle confidence remains the base. V28.16 then applies visible, versioned adjustments:

```text
HTF aligned              -> bonus
market structure aligned -> bonus
HTF conflicting          -> penalty
mixed direction          -> penalty
```

This is not a learned black-box probability. It is an explainable routing confidence used for research triage.

## Risk multiplier

Risk is only non-zero for an actual `TRADE_CANDIDATE`.

Default confidence buckets are:

```text
<55%     -> 0.00x
55-70%   -> 0.50x
70-85%   -> 0.75x
>=85%    -> 1.00x
```

Each market state can impose a lower maximum. For example, breakout and range candidates are currently capped at 0.75x.

These values are hypotheses to validate later, not universal trading truths.

## Trend exhaustion refinement

The existing lifecycle engine can label a strong but stretched trend as `trend_extended_late`.

V28.16 refines that to `trend_exhaustion` when the extension also has opposing evidence such as:

```text
bullish extended trend + bearish divergence / bearish CHoCH
bearish extended trend + bullish divergence / bullish CHoCH
```

The router then waits rather than automatically attempting a reversal.

## UI

A new Streamlit page is available:

```text
Market State
```

It provides a top-level table across configured pairs and a detailed explanation for one selected pair.

The intended quick view is:

```text
Pair | State | Direction | Strength | Volatility | Confidence | Preferred family | Action | Risk
```

## Validation status

V28.16 is still a research/display layer.

It does **not**:

- enable or disable live strategies;
- modify paper-trading behavior;
- place orders;
- automatically promote a family;
- retune market-state thresholds from recent results.

The next validation task is to replay this fixed adaptive router historically and then include the complete router policy in the future holdout freeze.
