# Trading App Handover — V26 Patch

## Source baseline
This patch continues from the V25 package and keeps the V23/V24/V25 architecture decisions unchanged.

## Main goal
V25 produced threshold recommendations, detailed regime splits, and overlap reports. V26 turns those recommendations into actionable queue jobs while still keeping the process quant-first and evidence-first.

## What was developed

### 1. Recommendation-to-action workflow
New module:

- `app_src/recommendation_actions.py`

It converts saved-run `threshold_recommendations.csv` rows into targeted what-if tasks.

### 2. Segment-filtered backtests
`run_backtest()` now supports:

```json
{
  "segment_filter": {
    "side": "SHORT",
    "regime_group": "trend",
    "regime_detail": "aligned_bear_trend"
  }
}
```

This allows a threshold recommendation to be tested on the same side/regime segment that produced it instead of retesting the whole run blindly.

Supported filter keys:

- `side`
- `regime`
- `regime_group`
- `regime_detail`
- `strategy_mode`
- `trade_owner_key`

### 3. Saved-run comparison UI action
In **Saved backtests and comparison → V25/V26 evidence details → Thresholds**, there is now a **V26 recommendation → what-if queue** expander.

It can:

- preview queueable recommendation rows
- choose max recommendations per run
- set min segment trades
- set min kept trades
- choose threshold offsets such as `-5, 0, 5`
- choose which action labels to queue
- create worker jobs of type `v26_recommendation_what_if`

### 4. Worker-compatible jobs
The existing backtest worker can process V26 tasks because they are normal task jobs with `strategy_payload` and `config_overrides`.

Each task includes:

- a tested score threshold
- a `segment_filter`
- a `v26_source_recommendation` metadata object

### 5. Calibration report action candidates
`score_calibration_report.py` now also exports:

- `saved_runs_v26_action_candidates.csv`

The Markdown report includes a **V26 Recommendation-to-Action Candidates** section.

### 6. Recommendation presets
New config file:

- `config/recommendation_action_presets.json`

Presets:

- `balanced`
- `exploratory`
- `strict`

The UI currently exposes equivalent controls directly; the JSON is there as a stable reference for future automation.

## How to see the change

1. Run and save several V25/V26 backtests so each saved run has `threshold_recommendations.csv`.
2. Open **Saved backtests and comparison**.
3. Select 2–5 saved runs.
4. Open **V25/V26 evidence details**.
5. Go to **Thresholds**.
6. Expand **V26 recommendation → what-if queue**.
7. Review the preview table.
8. Click **Queue V26 recommendation what-if jobs**.
9. Open **Backtest worker queue**.
10. Wait for the worker to complete the new `v26_recommendation_what_if` jobs.
11. Compare the completed runs against the original saved run.

## How to interpret V26 results

A V26 recommendation job is not a strategy rewrite. It is a test:

- Did a recommended score threshold improve the specific side/regime segment?
- Did it keep enough trades?
- Did it improve net PnL after friction?
- Did it improve PF without destroying sample size?

Only promote a threshold into a new strategy version after repeated evidence.

## Still left

### 1. Auto-compare recommendation jobs vs source run
V26 queues the jobs, but the UI still relies on manual saved-run comparison after completion.

### 2. Select individual recommendation rows
V26 uses filters and max rows. A future version should allow ticking exact rows in a data editor.

### 3. Candidate promotion workflow
Still needed:

- mark recommendation result as `promote`, `reject`, `more data`, or `repair`
- create a new strategy version only from accepted evidence
- keep an audit trail from source run → recommendation → what-if result → strategy version

### 4. Portfolio/concurrency simulation
V25 detects overlap; V26 queues threshold tests. Still needed:

- allow all overlapping trades
- best-score-only simulation
- bundle-only simulation
- cap correlated symbol exposure

### 5. Live-side calibration application
V26 is backtest/action workflow only. Applying calibrated thresholds to live scanner should be a later controlled step, not automatic.

## Recommended V27

Build **Promotion & Review Lab**:

1. compare V26 recommendation jobs against their source run
2. label each as `promote / reject / more data / repair`
3. write an audit table
4. optionally generate a new strategy version JSON from promoted evidence
5. keep live scanner unchanged until manually promoted
