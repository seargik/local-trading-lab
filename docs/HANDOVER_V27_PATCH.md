# Handover — V27 Promotion & Review Patch

## What V27 adds

V27 turns the V26 recommendation-to-action loop into a review workflow.

V26 created targeted recommendation what-if jobs. V27 compares those completed candidate runs against the original source run and helps a human decide whether a result should be promoted, rejected, watched, repaired, or retested with more data.

## New files

- `app_src/promotion_v27.py`
- `config/promotion_review_presets.json`
- `docs/HANDOVER_V27_PATCH.md`
- `docs/BABY_STEPS_V27.md`
- `smoke_test_v27_patch.py`

## Updated files

- `app_src/backtest_ui.py`
- `score_calibration_report.py`
- `app_src/recommendation_actions.py`

## Important behavior

V27 does **not** auto-change live scanner behavior.

V27 does **not** overwrite existing strategy versions.

V27 only creates:

1. source-vs-candidate comparison rows,
2. human review decisions,
3. optional strategy draft JSON files.

Drafts are saved under:

`data/backtest_reviews/strategy_drafts`

Review decisions are saved to:

`data/backtest_reviews/promotion_decisions.csv`

## How to see it

1. Run a normal saved backtest.
2. Open saved-run comparison.
3. In the Thresholds tab, queue V26 recommendation what-if jobs.
4. Let the worker finish.
5. Compare the source run together with the completed V26 result runs.
6. Open `V27 Promotion & Review Lab`.
7. Select a candidate row.
8. Choose a decision: promote, watchlist, more data, repair, reject.
9. Optionally download or save a draft strategy JSON.

## What the V27 table compares

For every V26 candidate, V27 compares:

- source run vs candidate run,
- total trades,
- net PnL,
- profit factor,
- win rate,
- max drawdown,
- tested threshold,
- side / regime group / regime detail.

## Review labels

- `promote_candidate`: candidate improved enough to draft a new strategy version.
- `watchlist`: interesting but not strong enough yet.
- `more_data`: too few trades or too narrow a sample.
- `repair`: segment/rules look broken.
- `reject`: underperformed source run.

## Calibration report additions

`score_calibration_report.py` now also exports:

- `saved_runs_v27_promotion_candidates.csv`
- `saved_runs_v27_review_log.csv`

and includes V27 sections in the Markdown report.

## What is still left

1. Exact row selection from saved V26 recommendations is still basic.
2. Promotion is still manual via draft JSON, not an in-app save-as-new-version button.
3. Promotion evidence should later require cross-symbol / cross-period validation.
4. Live scanner should not consume promoted settings until manually saved and assigned to a slot.
5. A portfolio-level concurrency simulator is still missing.

## Recommended next step

V28 should be **Strategy Draft Import + Cross-Validation Lab**:

1. take a V27 strategy draft JSON,
2. queue cross-validation runs across symbols and date slices,
3. compare candidate vs source across multiple folds,
4. only then allow a manual “save as new strategy version” action.
