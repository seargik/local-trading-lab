# Local Lab v12 – HTF Context + LTF Entry

This package upgrades your app toward a cleaner **shared-storage architecture**:

- **Collector** can keep running in the background
- **Live analyzer** can stay on or be paused
- **Backtest Lab** runs in a **separate Streamlit window/process**
- all of them share the same local strategy library and local OHLCV store

## What is new in this update

### 1. Separate Backtest Lab window

Run this when you want backtests and analytics without the main app dimming/reloading:

```powershell
.\start_backtest_only.bat
```

That opens a standalone Streamlit app on port `8503`.

### 2. Partitioned parquet OHLCV store

A new script converts your extracted Binance files into a month-partitioned parquet store:

```powershell
.\.venv\Scripts\python.exe .\prepare_partitioned_ohlcv_store.py data_bootstrap\extracted --store-root data\ohlcv_store --derive 15m,1h,4h
```

Output shape:

```text
data\ohlcv_store\
  symbol=BTCUSDT\
    timeframe=5m\
      year=2026\month=04\candles.parquet
    timeframe=1h\
      year=2026\month=04\candles.parquet
```

Use `data\ohlcv_store` as the preferred **History folder** in the Backtest Lab.

### 3. Signal tab / live chart warm-start

The app now falls back to the local partitioned parquet store when the live runtime snapshot does not have enough candles yet.

That means:

- chart history can appear immediately after startup
- analyzer can use bootstrap history more reliably
- you do not need to depend only on the latest live snapshot

### 4. Collector archiving

The collector now appends fetched candles into `data\ohlcv_store`.

Practical meaning:

- bootstrap once from Binance archive
- then let the collector keep your local working history updated
- you do not need to refresh the raw merged CSVs regularly for normal use

### 5. Better backtest visuals and KPI surface

Backtest result view now includes:

- trade-by-trade step equity curve
- time-based performance chart with daily / weekly / monthly switch
- outcome pie chart
- highest-TP-reached pie chart
- more KPI cards
- KPI help text / tooltips where supported by Streamlit

## Quick start

### 1. Create venv and install

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 2. Prepare the parquet store

```powershell
.\.venv\Scripts\python.exe .\prepare_partitioned_ohlcv_store.py data_bootstrap\extracted --store-root data\ohlcv_store --derive 15m,1h,4h
```

### 3. Start the main stack

```powershell
.\start_all_windows.bat
```

### 4. Start the separate backtest lab

```powershell
.\start_backtest_only.bat
```

## Recommended workflow

### Main app

- leave **Collector** running
- optionally leave **Analyzer** running
- when you want a stable manual UI session, turn off:
  - **Auto-refresh UI while live workers run**

### Backtest Lab

- use **History folder = `data\ohlcv_store`**
- scan files
- start with `BTCUSDT`
- start with 7-30 days
- then widen the date range gradually

## What is still next-phase

This update does **not** yet fully implement:

- queued/background multi-job backtest runner
- multi-strategy batch backtest grid
- full counterfactual “what-if” engine
- MFE-before-stop / MAE-before-TP detail block
- manipulation-aware skip analysis

Those are still the next major analytics layer.


Backtest worker
- Start a separate batch worker with start_backtest_worker.bat
- In Backtest Lab, select multiple strategies and click "Queue selected strategies"
- The worker saves each strategy run separately and the UI can compare completed batches

Partitioned store rebuild
- If you already have partial data in data\ohlcv_store, rebuild from scratch with:
  py -3 prepare_partitioned_ohlcv_store.py data_bootstrap\extracted --store-root data\ohlcv_store --derive 15m,1h,4h --reset-store
- This importer now supports both monthly files like XRPUSDT-5m-2020-01.csv and daily files like XRPUSDT-5m-2026-04-04.csv


What changed in this build
- expanded backtest KPI set
- score-decile performance block
- counterfactual quick checks (HTF aligned, top quartile, score >= 85)
- MFE/MAE scatter and better period tables
