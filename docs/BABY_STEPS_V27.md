# Baby steps — V27

## 1. Smoke test

```powershell
.\.venv\Scripts\python.exe smoke_test_v27_patch.py
```

Expected:

```text
V27 smoke test passed: promotion/review workflow files compile and markers exist.
```

## 2. Start the worker and Backtest Lab

```powershell
.\start_backtest_worker.bat
.\start_backtest_only.bat
```

## 3. Create a source run

1. Open Backtest Lab.
2. Run one normal single strategy or bundle.
3. Save the result.

## 4. Create V26 recommendation jobs

1. Open Saved backtests and comparison.
2. Select the source run.
3. Open `V25 evidence details` → `Thresholds`.
4. Use `V26 recommendation → what-if queue`.
5. Queue a small number first: 3–5 recommendation rows.

## 5. Let worker finish

Open Backtest worker queue and wait for jobs with type:

```text
v26_recommendation_what_if
```

to finish.

## 6. Compare source + candidates

1. Go back to Saved backtests and comparison.
2. Select the original source run.
3. Also select the completed V26 candidate runs.
4. Open `V27 Promotion & Review Lab`.

## 7. Review one candidate

For each candidate, look at:

- delta net PnL,
- delta profit factor,
- candidate trade count,
- side,
- detailed regime,
- tested threshold.

Then choose:

- `promote_candidate`,
- `watchlist`,
- `more_data`,
- `repair`,
- `reject`.

## 8. Save decision

Click:

```text
Save V27 review decision
```

The audit trail is saved to:

```text
data/backtest_reviews/promotion_decisions.csv
```

## 9. Create a strategy draft

If the candidate is worth promotion:

1. Download `V27 strategy draft JSON`, or
2. tick `Also save draft strategy JSON...` before saving the review decision.

Drafts are saved under:

```text
data/backtest_reviews/strategy_drafts
```

## 10. Do not apply live yet

V27 drafts are not automatically live. Review and cross-validate before saving them as new strategy versions.
