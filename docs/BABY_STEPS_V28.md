# Baby steps — V28

## 1. Run smoke test

```powershell
.\.venv\Scripts\python.exe smoke_test_v28_patch.py
```

Expected:

```text
V28 smoke test passed: cross-validation workflow files compile and markers exist.
```

## 2. Start the normal setup

```powershell
.\start_backtest_worker.bat
.\start_backtest_only.bat
```

If port `8503` is busy:

```powershell
.\.venv\Scripts\python.exe -m streamlit run .\backtest_app.py --server.port 8504
```

## 3. Make sure you have a V27 draft

In Saved backtests and comparison:

1. Select a source run and completed V26 candidate runs.
2. Open **V27 Promotion & Review Lab**.
3. Save a draft strategy JSON under:

```text
data/backtest_reviews/strategy_drafts
```

## 4. Queue V28 cross-validation

Open:

```text
Saved backtests and comparison → V28 Strategy Draft Import + Cross-Validation Lab
```

Use safe first settings:

```text
Date folds: 2 or 3
Min days/fold: 7–14
Max symbols: 3–6
Include source baseline: ON
```

Click:

```text
Queue V28 cross-validation jobs
```

## 5. Watch worker queue

Open:

```text
Backtest worker queue
```

Look for jobs with type:

```text
v28_cross_validation
```

## 6. Review results

Return to:

```text
V28 Strategy Draft Import + Cross-Validation Lab
```

Check:

- CV win rate
- tested pairs
- delta net PnL
- average delta profit factor
- min candidate trades
- fold/symbol details

## 7. Save CV review decision

Use:

```text
promote_after_cv / watchlist / more_folds / repair / reject
```

## 8. Optional manual version save

Only after good evidence:

1. Expand **Manual save draft as new strategy version**.
2. Choose the existing strategy to save under.
3. Click **Save selected draft as new strategy version**.

This creates a new version only. It does not assign it to live slots automatically.

## 9. Generate report

```powershell
.\.venv\Scripts\python.exe score_calibration_report.py --saved-runs-root data\backtests --out-dir analysis_reports
```

Open:

```text
analysis_reports\score_calibration_report.md
analysis_reports\saved_runs_v28_cv_aggregate.csv
analysis_reports\saved_runs_v28_cv_pairs.csv
```
