# Decision Log

## V28.4 — Git baseline

Decision: move the project to GitHub as the source of truth.

Reason: zip patches became too risky and hard to verify. GitHub gives branch history, PRs, Actions checks, Codespaces preview, and rollback.

## V28.5 — Demo mode and lifecycle fit

Decision: add synthetic demo data and lifecycle-to-strategy fit labels as a soft evidence layer.

Reason: browser/Codespaces testing should work without local runtime data, and the app should explain which strategy families fit the current market phase.

Constraint: lifecycle fit is not a hard live/paper gate.

## V28.6 — Historical OHLCV backfill

Decision: preload Binance USD-M futures OHLCV into the local parquet store and later update only the fresh missing part.

Reason: analysis and backtests should not wait months for the collector to build history.

Constraint: candles only; funding/open interest/order book/liquidations are separate future datasets.

## V28.7 — History Manager and project memory

Decision: add Data / History Manager UI and stable project recap docs.

Default target set:

```text
BTCUSDT, ETHUSDT, SOLUSDT, LTCUSDT, BNBUSDT, UNIUSDT, AAVEUSDT, XRPUSDT, TRXUSDT
```

Default requested history:

```text
12mo, 1h and 4h
```

Decision: do not commit generated `data/ohlcv_store` parquet files to Git.

## V28.8 — Lifecycle Gate Backtest Lab

Decision: do not place lifecycle gating directly into the backtest/live signal path yet.

First compare saved trades under:

```text
Baseline
Fit only
Fit only + confidence floor
Block direction conflicts
Block blocked + conflicts
Soft lifecycle score penalty
```

Reason: lifecycle is heuristic and should earn promotion through evidence.

Constraint: post-trade filtering does not regenerate opportunities that could appear if an earlier blocked trade freed a one-trade-at-a-time slot.

Promotion ladder:

```text
promising counterfactual result
-> exact signal-path replay
-> out-of-sample validation
-> paper-only gate
-> only later consider live execution
```

## V28.9 — Research Command Center

Decision: stop broadening the strategy surface and create a narrow evidence runner.

First-pass families:

```text
trend_pullback
compression_breakout
range_reversion
```

First-pass symbols:

```text
BTCUSDT, ETHUSDT, SOLUSDT
```

Default research timeframes:

```text
entry: 1h
analysis: 4h
```

Default execution assumptions: `binance_usdm_taker_light`, not zero friction.

Reason: the app has enough infrastructure. The next useful information is whether a small, understandable strategy set shows stable net edge after costs across pairs and periods.

Decision: V28.9 reads the latest existing strategy versions and maps them into the three core families. It does not create new strategies simply because one family is missing.

Decision: the main working UI should increasingly revolve around:

```text
Data / History -> Market State -> Strategy Evidence
```

## Current strategic decision

Continue the project, but simplify aggressively.

Keep:

- Historical data store and incremental refresh.
- Backtest/replay foundation.
- Trend lifecycle router and fit labels.
- Friction-aware evaluation.
- Cross-validation/promotion workflow.

Freeze for now:

- New strategy proliferation.
- Live execution changes.
- More complex bundle logic.
- LLM-generated trading decisions.

Focus:

```text
historical coverage -> narrow research batch -> lifecycle evidence -> cross-validation -> paper validation
```
