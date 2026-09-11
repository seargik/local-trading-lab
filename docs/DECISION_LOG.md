# Decision Log

## V28.4 — Git baseline

Decision: move the project to GitHub as the source of truth.

Reason: zip patches became too risky and hard to verify. GitHub gives branch history, PRs, Actions checks, Codespaces preview, and rollback.

## V28.5 — Demo mode and lifecycle fit

Decision: add synthetic demo data and lifecycle-to-strategy fit labels as a soft evidence layer.

Reason: Codespaces/browser/mobile testing should work without local runtime data, and the app needs to explain which strategy families fit the current market phase.

Constraint: lifecycle fit is not a hard live/paper gate yet.

## V28.6 — Historical OHLCV backfill

Decision: add a CLI and engine to preload Binance USD-M futures OHLCV into the existing local parquet store.

Reason: the app should not wait weeks/months to build enough data before analysis/backtesting. It needs historical context immediately.

Constraint: V28.6 backfills OHLCV only. Funding/open interest/order book/liquidations are separate future data products.

## V28.7 — History Manager and project memory

Decision: add a Data / History Manager page and stable project recap docs.

Reason: the app needs a visible data foundation: coverage, freshness, gap audit, backfill commands, and default target set. Project direction should be stored in GitHub docs rather than only in chat.

Default requested target set:

```text
BTCUSDT, ETHUSDT, SOLUSDT, LTCUSDT, BNBUSDT, UNIUSDT, AAVEUSDT, XRPUSDT, TRXUSDT
```

Default requested history:

```text
12mo, 1h and 4h
```

Decision: do not commit generated `data/ohlcv_store` parquet files.

Reason: history files are runtime data, can grow quickly, and should live on local/Codespaces/VPS storage or as temporary CI artifacts, not in source control.

## V28.8 — Lifecycle Gate Backtest Lab

Decision: do **not** put lifecycle gating directly into the core backtest/live execution path yet.

First run a trade-level counterfactual study on saved backtests using the feature and HTF snapshots already stored with every trade.

Compare:

```text
Baseline
Fit only
Fit only + confidence floor
Block direction conflicts
Block blocked + conflicts
Soft lifecycle score penalty
```

Reason: lifecycle fit is a heuristic router. It should earn the right to become a gate through evidence rather than because the logic sounds reasonable.

V28.8 adds bootstrap separation between kept and removed trades so the decision is not based only on a single-sample average.

Constraint: post-trade filtering is not the same as exact signal-path replay. If a blocked original trade would have occupied a one-trade-at-a-time slot, removing it can create later opportunities that V28.8 does not regenerate.

Promotion rule:

```text
V28.8 promising result
-> exact signal-path replay
-> out-of-sample validation
-> paper-only gate
-> only later consider live execution impact
```

## Current strategic decision

Continue the project, but simplify the product direction.

Keep:

- Historical data store.
- Backtest/replay foundation.
- Trend lifecycle router.
- Lifecycle strategy-fit labels.
- Strategy calibration/cross-validation/promotion workflow.

Freeze for now:

- New strategy creation.
- Live execution changes.
- More complex bundle logic.
- LLM-generated trading decisions.

Focus next:

```text
Historical data -> lifecycle -> strategy-family fit -> backtest evidence -> paper validation
```
