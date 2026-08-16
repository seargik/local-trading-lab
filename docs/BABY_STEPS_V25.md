# Baby steps — V25

## 1. Smoke test
```powershell
.\.venv\Scripts\python.exe smoke_test_v25_patch.py
```

## 2. Launch
```powershell
.\start_backtest_worker.bat
.\start_backtest_only.bat
```

If port 8503 is busy:
```powershell
.\.venv\Scripts\python.exe -m streamlit run .\backtest_app.py --server.port 8504
```

## 3. First V25 sanity test
1. Open Backtest Lab.
2. Set History folder to `data/ohlcv_store`.
3. Click Scan files.
4. Select one symbol, for example `ETHUSDT`.
5. Select one strategy.
6. Use a short date range.
7. Run backtest now.
8. Save the run.

## 4. What to check after the run
In the direct result, check:
- V25 detailed regime split
- V25 side × detailed regime split
- V25 threshold recommendations

## 5. Comparison test
1. Run/save 2–5 runs using the same symbol/date range.
2. Include at least one bundle run if possible.
3. Open Saved backtests and comparison.
4. Select the runs.
5. Open V25 evidence details.

Check tabs:
- Detailed regime
- Thresholds
- Overlap
- Friction
- Bundle/owner validation

## 6. Calibration report
```powershell
.\.venv\Scripts\python.exe score_calibration_report.py --saved-runs-root data\backtests --out-dir analysis_reports
```

Open:
- `analysis_reports/score_calibration_report.md`
- `analysis_reports/saved_runs_threshold_recommendations.csv`
- `analysis_reports/saved_runs_overlap_same_side.csv`

## 7. How to interpret V25
- Do not change strategy configs automatically.
- Use threshold rows as what-if candidates.
- Use detailed regime rows to identify where a strategy is valid.
- Use overlap rows to see duplicate or conflicting strategies.
- Keep bundle and single strategy ownership separate.
