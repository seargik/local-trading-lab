# V28.8 — Lifecycle Gate Backtest Lab

## Purpose

V28.8 answers one narrow question before lifecycle rules are allowed to affect execution:

> Do lifecycle-fit labels separate better trades from worse trades strongly enough to justify exact replay and later paper-trading gates?

The lab deliberately starts with a **trade-level counterfactual** on saved backtests rather than changing the core backtest execution path immediately.

## Why this is safer

The existing backtest engine already stores, for each trade:

- feature snapshot JSON;
- HTF context JSON;
- side and score;
- regime scores;
- entry/exit and net PnL;
- execution-friction fields.

V28.8 reconstructs the lifecycle state at each historical trade, maps the strategy to a lifecycle family, and labels the trade as one of:

```text
fit
caution
unknown
blocked
direction_conflict
```

This lets us test whether those labels have predictive value without introducing a new execution rule that could silently alter the backtest path.

## Scenarios

The comparison table includes:

1. **Baseline** — original saved trades.
2. **Fit only** — only trades with `allowed_by_lifecycle = true`.
3. **Fit only + confidence floor** — fit trades with lifecycle confidence above the selected threshold.
4. **Block direction conflicts** — keep everything except trades whose direction conflicts with lifecycle direction.
5. **Block blocked + conflicts** — remove family-blocked and direction-conflict trades.
6. **Soft lifecycle score penalty** — subtract configurable score penalties based on fit status, then require the adjusted score to remain above the strategy threshold.

Default score penalties:

```text
fit               0
caution            5
unknown           10
blocked           20
direction_conflict 25
```

## Metrics

Do not judge a gate only by total PnL because a gate necessarily changes trade count.

V28.8 compares:

- retained trade count / retention percentage;
- win rate;
- total net PnL;
- average PnL per trade;
- expectancy in R;
- profit factor;
- maximum drawdown;
- execution-cost total;
- average score.

It also bootstraps the difference in average `pnl_pct` between **kept** and **removed** trades. The report includes:

```text
kept_vs_removed_avg_pnl_delta_pct
separation_ci_low_pct
separation_ci_high_pct
separation_prob_positive
```

A positive lower 95% bootstrap bound is stronger evidence than simply seeing a better average in one sample.

## Recommendation states

The lab may return:

```text
promising_for_exact_replay
promising_but_unproven
no_gate_evidence
insufficient_data
```

Even `promising_for_exact_replay` does **not** mean enable a live gate. It means the next engineering step is justified.

## Important limitation

V28.8 is not an exact signal-path replay.

If an original trade is filtered out, the lab does not regenerate subsequent signals that might have been available because the one-trade-at-a-time slot would have been free. Therefore:

```text
V28.8 = trade-quality separation test
not
V28.8 = exact gated strategy backtest
```

If lifecycle gating looks useful, the next validation should move the gate into the signal loop and rerun the entire path on out-of-sample periods.

## How to use

1. Populate historical OHLCV with V28.6/V28.7.
2. Run and **save** a backtest in the Backtest / Replay area.
3. Open the Streamlit page:

```text
Lifecycle Gate Lab
```

4. Select the saved run.
5. Review `Gate comparison` first.
6. Then inspect `By fit status`, `By lifecycle state`, and the enriched trade table.
7. Repeat by symbol, side, timeframe and test period before drawing conclusions.

## Promotion standard

Do not add lifecycle hard gating to paper/live execution unless evidence survives:

- multiple symbols;
- both long and short where applicable;
- more than one market regime;
- out-of-sample dates;
- realistic friction assumptions;
- exact signal-path replay after this counterfactual stage.
