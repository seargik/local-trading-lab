# V24 Patch Handover — Comparison, Friction, Bundle Validation

## Source of truth kept from V23

This patch keeps the V23 decisions intact:
- crypto-only MVP
- local-first architecture
- WebSocket-first live path, REST for bootstrap/backfill/metadata
- shared store: `data/ohlcv_store`
- separate backtest worker and separate backtest Streamlit window
- quant-first, LLM-second
- preserve all strategies
- single strategies and bundle strategies remain separate trade owners

## What was developed in this V24 patch

### 1. Execution-friction realism

Added realistic cost fields to the actual backtest math:
- `fee_bps_per_side`
- `slippage_bps_per_side`
- `spread_bps`
- `funding_bps_per_8h`
- `execution_preset_name`
- `execution_preset_label`

New config:
- `config/execution_friction_presets.json`

New presets:
- `zero_research`
- `binance_usdm_taker_light`
- `liquid_scalper_stress`
- `altcoin_conservative`

Backtest trade rows now include:
- `raw_pnl_pct`
- `pnl_pct` after costs
- `execution_cost_pct`
- `fee_cost_pct`
- `slippage_cost_pct`
- `spread_cost_pct`
- `funding_cost_pct`

Summary now includes:
- `pre_friction_pnl_pct`
- `pre_friction_pnl_usd`
- `total_execution_cost_pct`
- `total_execution_cost_usd`
- `avg_execution_cost_pct`

### 2. V24 comparison dashboard additions

Saved-run comparison now shows:
- action labels such as `candidate`, `small sample`, `cost-sensitive`, `dies after friction`, `profitable but rough`
- best PF card
- best net PnL card
- lowest drawdown card
- highest friction drag card
- combined long/short table
- combined side × regime table
- combined regime table
- combined exit-family table
- combined score-decile-by-side table
- combined gross-vs-net friction table
- combined owner/bundle validation table

### 3. Bundle validation / ownership evidence

Backtest result exports now include:
- `performance_by_owner.csv`
- `bundle_validation.csv`

These help validate whether bundle-owned trades behave differently from single-strategy trades, without merging ownership logic.

### 4. Friction exports

Backtest result exports now include:
- `friction_comparison.csv`

This compares:
- gross / no-friction result
- net / current-friction result
- total friction drag

### 5. Calibration report upgrade

`score_calibration_report.py` now also writes:
- `saved_runs_owner_split.csv`
- `saved_runs_friction.csv`
- `saved_runs_bundle_validation.csv`

The Markdown report now includes Owner Split, Execution Friction, and Bundle Validation sections.

### 6. Validity audit action labels

`audit_strategy_validity.py` now adds `recommended_action`:
- `repair_rules_before_testing`
- `lower_threshold_or_rebalance_weights`
- `repair_long_side_or_mark_short_only_candidate`
- `repair_short_side_or_mark_long_only_candidate`
- `test_lower_threshold_or_relax_one_confirmation`
- `backtest_by_side_and_regime`

### 7. Smoke test

Added:
- `smoke_test_v24_patch.py`

It checks that patched files compile and that the V24 markers/configs exist.

## How to see the change

### 1. Run the smoke test

```powershell
.\.venv\Scripts\python.exe smoke_test_v24_patch.py
```

Expected result:

```text
V24 smoke test passed: patched files compile and V24 markers exist.
```

### 2. Start the normal Backtest Lab

```powershell
.\start_backtest_worker.bat
.\start_backtest_only.bat
```

If port 8503 is busy:

```powershell
.\.venv\Scripts\python.exe -m streamlit run .\backtest_app.py --server.port 8504
```

### 3. Open Backtest Lab

Go to:
- `Backtest / Replay`

You should now see:
- `Execution friction preset` selector under `Backtest config JSON`

Test sequence:
1. Run one strategy with `zero_research`
2. Save it
3. Run the same strategy with `binance_usdm_taker_light`
4. Save it
5. Run the same strategy with `liquid_scalper_stress`
6. Save it
7. Open `Saved backtests and comparison`
8. Select those saved runs
9. Compare net PnL, pre-friction PnL, friction drag, action labels, and friction table

### 4. See the new result-level tables

After a direct backtest run, open:
- `Friction / bundle validation`

You should see:
- `Gross vs net execution-friction view`
- `Single/bundle owner performance`
- `Bundle validation`

### 5. See the new saved-run comparison tabs

In `Saved backtests and comparison`, select several saved runs and look for:
- `V24 comparison details`
- `Long/short`
- `Regime`
- `Exit family`
- `Friction`
- `Bundle/owner validation`

### 6. Generate calibration report

```powershell
.\.venv\Scripts\python.exe score_calibration_report.py
```

Outputs are saved to:
- `analysis_reports/score_calibration_report.md`
- `analysis_reports/saved_runs_overview.csv`
- `analysis_reports/saved_runs_long_short.csv`
- `analysis_reports/saved_runs_regime.csv`
- `analysis_reports/saved_runs_side_regime.csv`
- `analysis_reports/saved_runs_exit_family.csv`
- `analysis_reports/saved_runs_owner_split.csv`
- `analysis_reports/saved_runs_friction.csv`
- `analysis_reports/saved_runs_bundle_validation.csv`
- `analysis_reports/saved_runs_score_deciles.csv`

### 7. Generate validity audit

```powershell
.\.venv\Scripts\python.exe audit_strategy_validity.py
```

Look for:
- `recommended_action`

## What is still left

### Still left from V23/V24 scope

1. **Deeper live bundle execution validation**
   - confirm live bundle opinions appear in main scanner UI
   - confirm paper trades are opened with bundle ownership
   - confirm bundle trades are not blocked by single-strategy component trades
   - confirm exits apply correctly in live/paper runtime

2. **More execution realism**
   - maker vs taker mode
   - symbol-specific spread assumptions
   - volatility-based slippage
   - partial fill assumptions
   - exchange min quantity / lot size modeling

3. **Deeper regime labeling**
   - trend up / trend down
   - compression / expansion
   - range / chop
   - failed breakout / sweep
   - post-breakout continuation

4. **Overlap/concurrency analytics**
   - which strategies fire together
   - which strategies duplicate each other
   - bundle vs components overlap
   - correlated exposure across symbols

5. **Better score calibration loop**
   - recommended thresholds per strategy/side/regime
   - score normalization per strategy
   - minimum sample-size warnings
   - confidence bands, not just one score cutoff

6. **Comparison dashboard polish**
   - cleaner visual styling
   - dedicated drawdown chart per run
   - export selected comparison tables as a single ZIP
   - better “best but risky” scoring

## Recommended next V25 step

V25 should focus on:
1. overlap/concurrency analytics
2. deeper regime labels
3. bundle-vs-component validation from matched runs
4. threshold recommendation by side/regime/sample size

Do not add or remove strategies yet. The system now needs better evidence about when each existing strategy is valid.
