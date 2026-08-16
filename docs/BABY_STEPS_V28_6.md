# Baby Steps V28.6 — Historical Backfill

## 1. Update your local repo

```powershell
git fetch origin
git checkout feature/v28-6-historical-backfill
git pull origin feature/v28-6-historical-backfill
```

## 2. Run smoke tests

```powershell
.\.venv\Scripts\python.exe smoke_test_v28_4_repo_ready.py
.\.venv\Scripts\python.exe smoke_test_v28_5_demo_lifecycle_fit.py
.\.venv\Scripts\python.exe smoke_test_v28_6_historical_backfill.py
```

Expected:

```text
V28.6 smoke test passed: historical backfill stores paged OHLCV and supports update-only refresh.
```

## 3. Backfill one pair first

Start small:

```powershell
.\.venv\Scripts\python.exe backfill_history.py --symbols BTCUSDT --interval 1h --lookback 30d
```

Then try a bigger window:

```powershell
.\.venv\Scripts\python.exe backfill_history.py --symbols BTCUSDT --interval 1h --lookback 1y
```

## 4. Update only the fresh part later

```powershell
.\.venv\Scripts\python.exe backfill_history.py --symbols BTCUSDT --interval 1h --lookback 1y --update-only
```

This starts from the latest stored candle + one interval.

## 5. Multiple pairs

```powershell
.\.venv\Scripts\python.exe backfill_history.py --symbols BTCUSDT,ETHUSDT,SOLUSDT --interval 1h --lookback 1y --update-only
```

## 6. Ask analyzer to rerun

If the analyzer worker is running:

```powershell
.\.venv\Scripts\python.exe backfill_history.py --symbols BTCUSDT --interval 1h --lookback 1y --update-only --request-analysis
```

## 7. Open the app

```powershell
.\.venv\Scripts\python.exe -m streamlit run .\app.py
```

Then turn **Demo mode / sample data** OFF and select the backfilled pair/timeframe.

## 8. What should happen

The app and analyzer use `Storage.get_candles()`. When runtime/live snapshot has too few candles, it falls back to `data/ohlcv_store`.

So the scanner should no longer need to wait for the collector to accumulate long history.

## 9. Recommended intervals

```text
4h: best first choice for 3-5 years
1h: good for 1-3 years
15m: good for months
5m: shorter tactical windows
1m: only short windows unless you accept large disk/time usage
```

## 10. Do not commit local history

`data/ohlcv_store` is intentionally ignored by Git. Keep it local.
