# Baby Steps — V28.7 Data / History Manager

## What V28.7 adds

V28.7 adds a Streamlit page for local OHLCV history management and stores the project recap in GitHub docs.

New page:

```text
Data History
```

New default target set:

```text
BTCUSDT, ETHUSDT, SOLUSDT, LTCUSDT, BNBUSDT, UNIUSDT, AAVEUSDT, XRPUSDT, TRXUSDT
```

Default intervals:

```text
1h, 4h
```

Default lookback:

```text
12mo
```

## Step 1 — Pull the branch

```powershell
git fetch origin
git checkout feature/v28-7-history-manager-clean
git pull origin feature/v28-7-history-manager-clean
```

## Step 2 — Run smoke checks

```powershell
.\.venv\Scripts\python.exe smoke_test_v28_7_history_manager.py
```

## Step 3 — Open app

```powershell
.\.venv\Scripts\python.exe -m streamlit run .\app.py
```

Streamlit should show the main app and a page called:

```text
Data History
```

## Step 4 — Check coverage

Open the `Data History` page.

You should see:

- Selected USDT pairs.
- Selected intervals.
- Lookback selector.
- Coverage table.
- Backfill commands.
- Gap audit section.

If there is no local history yet, rows will show missing or partial.

## Step 5 — Load the requested 12 months

Recommended first load:

```powershell
.\.venv\Scripts\python.exe backfill_default_history.py --lookback 12mo
```

This loads:

```text
BTCUSDT, ETHUSDT, SOLUSDT, LTCUSDT, BNBUSDT, UNIUSDT, AAVEUSDT, XRPUSDT, TRXUSDT
```

for:

```text
1h, 4h
```

## Step 6 — Refresh only the new part later

```powershell
.\.venv\Scripts\python.exe backfill_default_history.py --lookback 12mo --update-only --request-analysis
```

This checks the latest stored candle and fetches only newer missing candles.

## Step 7 — Verify coverage again

Open `Data History` and click:

```text
Refresh coverage table
```

Expected result: most rows should move from missing/partial to ready or near-ready.

## Important notes

`data/ohlcv_store` is local runtime data and is ignored by Git. It will not be committed to the repo.

Run the backfill on the runtime where you want data to persist:

- your local PC,
- Codespaces,
- a VPS,
- or future Docker/server runtime.

If you run it in temporary Codespaces and later delete the codespace, that history is lost unless exported.
