# Historical OHLCV Backfill

V28.6 adds a safe way to pre-load longer Binance USD-M futures OHLCV history into the local parquet store.

The goal is:

```text
load 1 month to 5 years for specific pairs
keep the history locally
later update only the fresh missing part
let the app/analyzer read from the store instead of waiting for live collection
```

## What it stores

Backfilled candles are written to:

```text
data/ohlcv_store/
```

The existing store layout is partitioned by symbol, timeframe, year, and month:

```text
data/ohlcv_store/symbol=BTCUSDT/timeframe=1h/year=2024/month=01/candles.parquet
```

This folder is ignored by Git. It stays local.

## Binance request model

The backfill engine uses the public Binance USD-M Futures kline endpoint:

```text
GET /fapi/v1/klines
```

It pages with:

```text
symbol
interval
startTime
endTime
limit
```

The engine requests up to 1000 candles per page, writes each page into the parquet store, then continues from the last candle open time + one interval.

## Basic examples

### 1 month of 1h BTC history

```powershell
.\.venv\Scripts\python.exe backfill_history.py --symbols BTCUSDT --interval 1h --lookback 1mo
```

### 5 years of 4h BTC + ETH history

```powershell
.\.venv\Scripts\python.exe backfill_history.py --symbols BTCUSDT,ETHUSDT --interval 4h --lookback 5y
```

### Exact date range

```powershell
.\.venv\Scripts\python.exe backfill_history.py --symbols SOLUSDT --interval 1h --start 2021-01-01 --end 2026-01-01
```

### Update only fresh missing candles

After the initial backfill, run:

```powershell
.\.venv\Scripts\python.exe backfill_history.py --symbols BTCUSDT,ETHUSDT --interval 1h --lookback 5y --update-only
```

`--update-only` reads the latest stored candle and starts from the next interval. It does not re-download the whole 5 years.

### Request analyzer rerun after backfill

```powershell
.\.venv\Scripts\python.exe backfill_history.py --symbols BTCUSDT --interval 1h --lookback 1y --update-only --request-analysis
```

This writes an analyzer request file so `analyzer_worker.py` can rerun from the newly available stored candles.

## Recommended usage

For deep backtesting, prefer coarser intervals first:

```text
4h for 3-5 years
1h for 1-3 years
15m for several months
5m for shorter tactical windows
1m only for short windows, because it gets large quickly
```

## How the app uses it

The existing `Storage.get_candles()` path already falls back to the partitioned OHLCV store when the runtime snapshot has too few candles.

So after backfill:

1. Start the app.
2. Select the same symbol/timeframe.
3. Start or request the analyzer.
4. The scanner can analyze immediately from stored candles instead of waiting for the collector to build history.

## Current limitations

- This version backfills OHLCV candles only.
- It does not backfill historical order book depth.
- It does not backfill long historical funding/open-interest series yet.
- Gap audit is basic; deeper gap repair should be added next.
- Very large 1m history can consume significant local disk and time.

## Next recommended step

V28.7 should add:

```text
Historical Store Manager UI
```

with:

```text
symbol/timeframe/date-range selection
stored coverage summary
gap audit
action buttons: backfill / update-only / repair gaps / request analyzer
```
