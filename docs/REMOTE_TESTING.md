# Remote Testing — GitHub Actions + Codespaces

This project is still local-first, but V28.5 makes browser-based testing possible without copying private market history or running the collector locally.

## What can be tested from GitHub

There are two useful remote paths:

1. **GitHub Actions smoke tests** — validates that the repository installs, compiles, and basic V28.4/V28.5 checks pass.
2. **GitHub Codespaces UI preview** — runs Streamlit in a cloud development container and opens the app through a forwarded browser URL.

A raw GitHub repository URL cannot directly run the Streamlit app as a website. Streamlit needs a Python process/server. Use Codespaces for temporary dev preview or Streamlit Community Cloud for a persistent hosted demo.

## Option A — Run smoke tests from GitHub

The workflow is here:

```text
.github/workflows/smoke.yml
```

It runs on:

```text
push
pull_request
workflow_dispatch
```

The `workflow_dispatch` trigger lets you run the workflow manually from GitHub UI.

### Manual run

1. Open the repository on GitHub.
2. Go to **Actions**.
3. Choose the **smoke** workflow.
4. Click **Run workflow**.
5. Select the branch you want to test, for example:

```text
feature/v28-5-demo-lifecycle-fit
```

6. Run it.

The workflow does:

```bash
python -m pip install -r requirements.txt
python -m compileall app.py backtest_app.py backtest_worker.py app_src
python smoke_test_v28_4_repo_ready.py
python smoke_test_v28_5_demo_lifecycle_fit.py
```

## Option B — Browser/mobile UI test with GitHub Codespaces

Use this when you want to open the app from a browser without installing everything locally.

### Start Codespaces

1. Open the repository on GitHub.
2. Click **Code**.
3. Open the **Codespaces** tab.
4. Create a codespace from the branch you want to test:

```text
feature/v28-5-demo-lifecycle-fit
```

The dev container will install Python requirements automatically using:

```text
.devcontainer/devcontainer.json
```

### Start the main app

In the Codespaces terminal:

```bash
python -m streamlit run app.py --server.port 8501
```

Open the forwarded `8501` port from the Codespaces port panel.

### Enable demo mode

In the app sidebar, turn on:

```text
Demo mode / sample data
```

This uses synthetic ETHUSDT, BTCUSDT, and SOLUSDT candles. It does not need:

```text
collector worker
analyzer worker
local OHLCV store
private historical data
Binance connection
```

### Check these screens

Open:

```text
Scanner
Market State
Scanner → Inspect symbol → Strategy opinions
```

Look for:

```text
lifecycle_state
lifecycle_direction
lifecycle_confidence
fit_ready_count
best_fit_strategy
strategy_family
fit_status
allowed_by_lifecycle
fit_reason
suggested_exit_family
```

## Option C — Backtest Lab in Codespaces

The dev container forwards port `8503` too.

Start the Backtest Lab with:

```bash
python -m streamlit run backtest_app.py --server.port 8503
```

This is useful for UI checks, but larger backtests should still be run locally unless a future cloud-safe demo backtest mode is added.

## Important limitations

Demo mode is synthetic. It is for UI and workflow validation only.

Do not use demo-mode signals for trading decisions.

Remote preview does not replace the local full system. The full local system still needs:

```text
collector_worker.py
analyzer_worker.py
backtest_worker.py
data/ohlcv_store
local backtest job queue
```

## Recommended workflow

For development:

```text
branch → push → GitHub Actions smoke → Codespaces UI preview → PR → merge
```

For local production-like testing:

```text
pull main → run smoke tests → start collector/analyzer/backtest workers locally
```
