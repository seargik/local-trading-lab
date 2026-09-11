# Manual History Backfill Artifact Workflow

V28.7 includes a manual GitHub Actions workflow:

```text
history-backfill-artifact
```

It can fetch OHLCV candles in GitHub Actions and upload the generated `data/ohlcv_store` as a short-lived workflow artifact.

## Default target

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

## When to use it

Use this when you want to test the backfill from GitHub without running it on your own computer yet.

## Important limitation

A GitHub Actions artifact is not the same as persistent app storage.

The artifact exists temporarily and must be downloaded/unzipped into the runtime where the app will use it:

```text
data/ohlcv_store
```

If you run Streamlit in Codespaces, download/unzip the artifact inside the same Codespace.

If you run Streamlit locally, download/unzip the artifact into your local repo folder.

## Recommended persistent setup

For serious always-on use, run the backfill/update-only command on the same machine that runs the collector/analyzer:

```powershell
.\.venv\Scripts\python.exe backfill_default_history.py --lookback 12mo --update-only --request-analysis
```

Later this should become a scheduled job on a VPS/Mac mini/mini PC.
