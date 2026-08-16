# V28.1 Bugfix — saved-run comparison backward compatibility

## What was fixed

The Backtest Lab comparison view could crash with:

```text
KeyError: 'total_execution_cost_usd'
```

This happened when comparing older saved runs or older batch-job results that were created before V24 execution-friction metrics existed.

## Changed file

- `app_src/backtest_ui.py`

## Fix

`_comparison_frame()` now adds safe default columns for missing V24+ friction/KPI fields before sorting or rendering comparison cards.

Defaults:

- `total_execution_cost_usd = 0.0`
- `pre_friction_pnl_usd = total_pnl_usd` when missing
- core KPI columns default to numeric values instead of raising `KeyError`

## How to see the change

1. Start the app normally:

```powershell
.\start_backtest_worker.bat
.\start_backtest_only.bat
```

2. Go to:

```text
Backtest worker queue → completed batch job comparison
```

or:

```text
Saved backtests and comparison
```

3. Select old and new runs together.

Expected result: the comparison should render. Older runs will show friction drag as `0.00` / unknown instead of crashing.

## What is still left

This fixes the UI crash only. It does not retroactively calculate execution-friction metrics for old saved runs. To get true friction drag, rerun the strategy with a V24+ execution-friction preset.
