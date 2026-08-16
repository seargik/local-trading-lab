# Baby steps — V22

## 1. Start the app

1. Start `start_backtest_worker.bat`
2. Start `start_backtest_only.bat`
3. In Backtest Lab, set **History folder** to `data/ohlcv_store`
4. Click **Scan files**

## 2. Test a single strategy

1. In **Strategies for backtest worker**, select one strategy
2. Keep **Direct run mode = Single strategy**
3. Pick one symbol, for example `ETHUSDT`
4. Start with a short date range, for example `2026-03-25` to `2026-04-01`
5. Click **Run backtest now**
6. Save the result

Use this first to verify the data path, strategy packet, and backtest config all work.

## 3. Test several strategies separately

1. Select 2–5 strategies
2. Keep **Direct run mode = Single strategy**
3. Click **Queue selected strategies**
4. Open **Backtest worker queue**
5. Wait for jobs to complete
6. Go to **Saved backtests and comparison** and compare them

This is the cleanest way to find the strongest strategies without mixing them.

## 4. Test a bundle strategy

1. Select 2–5 strategies
2. Change **Direct run mode** to **Bundle consensus**
3. Open **Bundle config JSON**
4. Start with a simple config:

```json
{
  "bundle_name": "ETH Core Bundle",
  "bundle_mode": "n_of_m",
  "n_required": 2,
  "bundle_threshold": 2.0,
  "component_min_score": 70,
  "weights": {}
}
```

5. Click **Run backtest now**
6. Save the result with a clear name

Bundle mode turns the selected strategies into a separate bundle engine. It is not blocked by single-strategy trades in saved comparisons or later live logic.

## 5. Queue bundle what-if tests

1. Keep **Direct run mode = Bundle consensus**
2. Keep the same selected strategies
3. Adjust **What-if matrix config JSON**
4. Click **Queue current mode what-if** or **Queue current mode backtest + what-if**
5. Watch progress in **Backtest worker queue**
6. Compare the completed saved runs

## 6. Understand `one_trade_at_time`

In this build, `one_trade_at_time` is effectively scoped to:

- symbol + direction + strategy run
- or symbol + direction + bundle run

That means:
- one strategy run can hold one LONG and one SHORT independently over time
- a bundle run is independent from a single-strategy run
- bundle trades do not block single-strategy trades, and vice versa

## 7. Practice better exit families

Open **Foundation Toolkit → Exit Families**.

Use it like this:
- trend / pullback strategies → trend exit family
- breakout strategies → breakout exit family
- range / reversion strategies → range exit family
- reversal / exhaustion strategies → reversal exit family

Then rerun what-if matrices with TP ladder and stop changes.

## 8. Practice long / short split analytics

Open **Foundation Toolkit → Calibration Reports**.

Focus on:
- long-only results
- short-only results
- regime split
- score deciles

Use that to see whether recent performance is being carried mainly by shorts.

## 9. Run the strategy validity audit

Open **Foundation Toolkit → Validity Audit**.

Look for:
- unreachable thresholds
- strategies whose enabled rule weights cannot reach the threshold
- over-constrained strategies that almost never fire

This is for repair, not removal.

## 10. Use saved run filters

In **Saved backtests and comparison**, use:
- **Min PF**
- **Min total PnL $**
- **Min trades**
- favorites filter
- strategy filter
- symbol filter

This is the fastest way to narrow the saved results before comparing up to 10 runs.

## 11. Compare and rerun

1. Select up to 10 saved runs
2. Compare them
3. Use **Load first selected run config** to repopulate the form
4. Change dates, symbols, or JSON
5. Run again

## 12. Suggested first testing order

1. Single strategy on one symbol
2. Queue several strategies separately
3. Compare saved runs
4. Bundle the best 2–4 strategies
5. Run bundle what-if matrix
6. Recheck long/short split and validity audit
