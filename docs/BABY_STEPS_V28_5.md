# Baby Steps V28.5 — Demo Mode + Lifecycle Fit

## 1. Pull latest repo

```powershell
git fetch origin
git checkout main
git pull origin main
```

## 2. Run smoke tests

```powershell
.\.venv\Scripts\python.exe smoke_test_v28_4_repo_ready.py
.\.venv\Scripts\python.exe smoke_test_v28_5_demo_lifecycle_fit.py
```

Expected:

```text
V28.5 smoke test passed: demo mode and lifecycle strategy-fit labels are available.
```

## 3. Start main app

```powershell
.\.venv\Scripts\python.exe -m streamlit run .\app.py
```

## 4. Turn on demo mode

In the sidebar enable:

```text
Demo mode / sample data
```

You do not need the collector or private OHLCV store for this check.

## 5. Check Scanner tab

You should see synthetic rows for:

```text
ETHUSDT
BTCUSDT
SOLUSDT
```

New scanner columns include:

```text
lifecycle_state
lifecycle_direction
lifecycle_confidence
fit_ready_count
directional_opinion_count
blocked_or_conflict_count
best_fit_strategy
```

## 6. Check Market State tab

Open:

```text
Market State
```

Look for:

```text
trend_pullback_entry / trend_entering / breakout_attempt / range_chop
allowed strategy families
blocked strategy families
best lifecycle-fit strategy
```

## 7. Check strategy opinion fit

In Scanner → Inspect symbol → Strategy opinions, check:

```text
strategy_family
fit_status
allowed_by_lifecycle
fit_reason
suggested_exit_family
```

## 8. Turn demo mode off

When you want the real local lab again, disable:

```text
Demo mode / sample data
```

Then start the collector/analyzer as before.

## Notes

Demo mode is only for UI/testing. It is synthetic data and must not be used for trading decisions.
