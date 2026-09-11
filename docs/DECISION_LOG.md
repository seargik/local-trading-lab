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
- Live execution.
- More complex bundle logic.
- LLM-generated trading decisions.

Focus next:

```text
Historical data -> lifecycle -> strategy-family fit -> backtest evidence -> paper validation
```
