# Baby steps — V26 recommendation-to-action workflow

## 1. Start from saved runs
You need saved runs that already contain:

- `trades.csv`
- `threshold_recommendations.csv`
- preferably V25 detailed-regime fields

If you do not have those yet, run and save one single strategy and one bundle first.

## 2. Open comparison
1. Start the backtest worker.
2. Start the backtest app.
3. Open **Saved backtests and comparison**.
4. Select 2–5 saved runs.

## 3. Open threshold actions
1. In the comparison area, open **V25/V26 evidence details**.
2. Go to **Thresholds**.
3. Expand **V26 recommendation → what-if queue**.

## 4. Use safe defaults first
Use:

- Max recommendations per run: `5`
- Min segment trades: `20`
- Min kept trades: `8`
- Threshold offsets: `-5, 0, 5`
- Actions to queue: `test_score_threshold`

## 5. Preview before queueing
Check the preview table. Each row should show:

- source run
- side
- regime group
- detailed regime
- recommended threshold
- tested threshold
- segment filter JSON

## 6. Queue jobs
Click:

```text
Queue V26 recommendation what-if jobs
```

## 7. Watch worker queue
Open **Backtest worker queue** and wait for jobs of type:

```text
v26_recommendation_what_if
```

## 8. Compare outputs
After worker completion:

1. Go back to **Saved backtests and comparison**.
2. Select the original run and the new V26 runs.
3. Compare:
   - PF
   - net PnL
   - friction drag
   - trade count
   - long/short split
   - detailed regime split

## 9. Decision rule
Do not promote from one good result only.

Use this rough interpretation:

- Better PF + better net PnL + enough trades = candidate
- Better PF but tiny sample = collect more data
- Better gross but worse net = dies after friction
- No improvement = reject threshold change
- Segment still bad = repair strategy logic, not threshold

## 10. Next after V26
The next useful step is **V27 Promotion & Review Lab**:

- compare V26 result against source run
- mark promote / reject / more data / repair
- create a strategy-version draft only for promoted evidence
