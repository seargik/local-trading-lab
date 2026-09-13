# V28.17 — Historical Market State Replay

## Purpose

V28.16 made Market State a first-class object and added an explainable adaptive router. V28.17 asks a narrower question before that router is allowed to influence paper/live behavior:

> Does the Market State Identifier produce stable, directionally meaningful classifications when replayed through history using only information that was closed and available at the time?

This version is intentionally a **research audit**, not an execution simulator.

## Replay flow

```text
historical 4h candle closes
        -> enrich OHLCV features
        -> attach only already-closed 1d / 1w context
        -> V28.16 Market State Identifier
        -> record state / direction / confidence / preferred family / action
        -> observe later 4h closes
        -> measure directional agreement and state stability
```

Default first-pass universe:

```text
BTCUSDT, ETHUSDT, SOLUSDT
```

Default analysis timeframe:

```text
4h
```

Default descriptive horizons:

```text
1 bar  = 4h
3 bars = 12h
6 bars = 24h
```

## What is measured

For every historical decision point V28.17 records:

- lifecycle / market state;
- direction;
- trend strength;
- volatility state;
- market structure;
- higher-timeframe alignment;
- state confidence;
- preferred strategy family;
- router action and router direction;
- research risk multiplier;
- later raw market return;
- later return expressed in the router direction.

The later return is a **state-quality diagnostic**, not a simulated trade result. No stop, target, fill, position, leverage, or order is created by V28.17.

## Closed-bar timing rule

V28.17 uses the close of the analysis bar as the decision timestamp.

Example for a 4h bar:

```text
bar opens 08:00 UTC
bar closes 12:00 UTC
state may be evaluated at 12:00 UTC
```

A 1d or 1w context row is included only when that higher-timeframe bar has also closed by the same decision timestamp.

The replay stores a `lookahead_ok` flag and reports any timing violations. Any violation invalidates interpretation of the replay.

## State stability

An adaptive system is not useful if it changes state every candle. V28.17 therefore reports:

```text
state transition frequency
state change rate
average / median / maximum dwell bars by state
most common transitions
```

The initial churn warning level is versioned in:

```text
config/market_state_replay_policy.json
```

It is a research threshold, not a universal market truth.

## Confidence calibration

The replay groups observations into the existing V28.16 confidence ranges:

```text
<55
55-69
70-84
85-100
```

For each band it reports how often the later market move agreed with the router direction. A useful confidence score should generally become more informative as confidence rises; if it does not, the confidence model needs work before being used for risk decisions.

## What V28.17 does not prove

A state can have good directional agreement and still fail as a trading system because actual performance also depends on:

- entry timing;
- stop placement;
- exits;
- fees / spread / slippage;
- trade overlap;
- position sizing;
- strategy-family signal quality.

So V28.17 does **not** replace V28.12–V28.15 strategy evidence. It validates the state/routing layer itself.

## Runtime artifacts

Optional snapshots are written under:

```text
data/backtest_reviews/market_state_replays/
```

These are runtime research data and are not source code.

## Promotion rule

There is no promotion from V28.17 alone.

A reasonable next gate is:

```text
stable state replay
+ closed-bar timing audit
+ sensible confidence calibration
+ enough observations per important state
-> then test strategy outcomes under the adaptive state policy
```

Paper/live execution remains unchanged.
