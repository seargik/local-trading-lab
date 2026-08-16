# V28 Patch Handover — Strategy Draft Import + Cross-Validation Lab

## What V28 adds

V28 turns the V27 draft/promotion concept into a safer validation workflow.

It does **not** change the locked architecture:
- crypto-only MVP
- local-first
- WebSocket-first live path with REST for bootstrap/backfill/metadata
- shared historical store: `data/ohlcv_store`
- separate backtest worker
- quant-first, LLM-second
- no strategy removal
- single strategies and bundles stay separate trade owners

## Developed in V28

### 1. Strategy draft discovery
The Backtest Lab now reads draft JSON files from:

```text
data/backtest_reviews/strategy_drafts
```

These are normally created from the V27 Promotion & Review Lab.

### 2. Cross-validation job queueing
A new Backtest Lab section queues source-vs-candidate validation jobs:

```text
V28 Strategy Draft Import + Cross-Validation Lab
```

The UI lets you choose:
- draft JSON
- source run
- symbols
- number of chronological date folds
- minimum days per fold
- max symbols
- whether to include the source baseline

Each fold creates a worker job of type:

```text
v28_cross_validation
```

Each job contains source and candidate tasks per symbol.

### 3. Worker lineage metadata
The backtest worker now stores task metadata in completed job results:

```json
{
  "task_meta": {"v28_cv": {...}},
  "config_overrides": {...}
}
```

Saved result manifests also contain `config.v28_cv`, so each result can be traced back to:
- CV id
- fold id
- symbol
- source/candidate role
- draft path
- source run

### 4. Cross-validation reports
New module:

```text
app_src/cross_validation_v28.py
```

It builds:
- raw CV result rows
- paired source-vs-candidate fold/symbol comparisons
- aggregate CV summary

Aggregate fields include:
- tested pairs
- candidate wins
- CV win rate
- total delta PnL
- average delta profit factor
- min candidate trades
- promotion confidence
- V28 recommendation

### 5. CV review audit trail
Manual CV review decisions are saved to:

```text
data/backtest_reviews/cross_validation_decisions.csv
```

Decision options:
- `promote_after_cv`
- `watchlist`
- `more_folds`
- `repair`
- `reject`

### 6. Manual save as strategy version
V28 adds a manual-only action:

```text
Save selected draft as new strategy version
```

This uses the local Strategy Library storage and saves the draft under an existing strategy as a new version.

Important: it does **not** assign the new version to a live slot and does **not** change live trading automatically.

### 7. Calibration report export
`score_calibration_report.py` now exports:

```text
saved_runs_v28_cv_results.csv
saved_runs_v28_cv_pairs.csv
saved_runs_v28_cv_aggregate.csv
saved_runs_v28_cv_review_log.csv
```

And adds V28 sections to:

```text
score_calibration_report.md
```

### 8. Smoke test
New smoke test:

```text
smoke_test_v28_patch.py
```

## How to see the change

1. Start worker and Backtest Lab.
2. Create a V27 draft from a candidate run.
3. Open **Saved backtests and comparison**.
4. Go to **V28 Strategy Draft Import + Cross-Validation Lab**.
5. Select draft + source run.
6. Queue V28 CV jobs.
7. Wait for worker completion.
8. Return to the same section and review aggregate + fold/symbol detail.
9. Save a CV review decision.
10. Only if evidence is good, manually save the draft as a new strategy version.

## Still left after V28

1. Assigning a saved strategy version to live slots is still manual in the main Strategy Library.
2. Cross-validation is chronological fold-based, but not yet walk-forward retraining.
3. No portfolio-level allocation simulation yet.
4. No automatic live calibration application.
5. No symbol-specific parameter override system yet.
6. Live bundle execution still needs local runtime validation.

## Recommended V29

V29 should focus on:

1. portfolio-level concurrency simulator
2. duplicate/overlap blocking modes
3. correlated-symbol exposure caps
4. side/regime-specific live calibration profiles, still manual opt-in
5. a final promotion checklist before assigning a version to a live slot
