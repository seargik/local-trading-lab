# V28.5 Handover — Demo Mode + Lifecycle Strategy Fit

## Purpose

V28.5 makes the GitHub/Codespaces/browser path useful without requiring the local collector or private OHLCV store.

It also upgrades the V28.4 Market State scaffold from a symbol-level explanation into a soft strategy-fit layer.

## Added

- `app_src/demo_mode.py`
  - Generates synthetic ETHUSDT, BTCUSDT, and SOLUSDT OHLCV series.
  - Runs the normal `analyze_symbol()` path against sample candles.
  - Attaches lifecycle state and strategy-fit labels.
- `demo_data/sample_ohlcv_fixture.json`
  - Metadata only; no real trading or private history is committed.
- `demo_data/README.md`
- `docs/BABY_STEPS_V28_5.md`
- `smoke_test_v28_5_demo_lifecycle_fit.py`

## Changed

- `app.py`
  - Adds sidebar toggle: `Demo mode / sample data`.
  - Shows synthetic scanner rows when demo mode is ON.
  - Strategy opinion table now prioritizes lifecycle-fit fields.
  - Market State tab shows fit counts and best lifecycle-fit strategy.
- `app_src/trend_lifecycle.py`
  - Adds strategy-family inference.
  - Adds `evaluate_strategy_lifecycle_fit()`.
  - Adds `attach_lifecycle_fit_to_analysis()`.
- `app_src/analysis_core.py`
  - Live scanner now attaches lifecycle state to each analysis payload.
  - Scanner rows include lifecycle columns and fit counts.
  - Setup summaries include lifecycle-fit context.
- `.github/workflows/smoke.yml`
  - Runs both V28.4 and V28.5 smoke checks.

## Important behavior

The lifecycle fit layer is still a **soft evidence layer**. It does not hard-block strategy execution yet.

New fields added to strategy opinions:

- `strategy_family`
- `allowed_by_lifecycle`
- `fit_status`
- `fit_reason`
- `direction_fit`
- `lifecycle_state`
- `lifecycle_direction`
- `lifecycle_confidence`
- `suggested_exit_family`
- `lifecycle_entry_mode`

## Still left

1. Backtest lifecycle-fit as a soft/hard gate.
2. Add a mobile-first simplified dashboard.
3. Add lifecycle history charts per symbol.
4. Add persistent lifecycle snapshots.
5. Add strategy-family definitions to bundled strategy JSON files instead of relying only on heuristic name mapping.
