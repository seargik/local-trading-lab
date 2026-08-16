# V23 next-step patch notes

This patch keeps the V23 architecture intact and implements the next incremental layer around live bundles, exit families, and analytics/calibration.

## Principles preserved
- No packaged strategy was removed.
- Single-strategy trades and bundle trades remain separate trade owners.
- `one_trade_at_time` now has a clearer path toward `symbol + direction + owner`, where owner is either `single:<version_id>` or `bundle:<bundle_name>`.
- Score calibration is expanded with more diagnostics instead of adding one hard universal cutoff.

## Added files
- `app_src/exit_families.py`
  - Maps existing strategy archetypes into exit families.
  - Provides family defaults for TP mode, TP count, breakeven trigger, and lock trigger.
- `app_src/bundle_engine.py`
  - Reusable bundle-scoring layer for live scanner integration.
  - Loads `config/live_bundle_presets.json`.
- `config/live_bundle_presets.json`
  - Optional live bundle presets for ETH, BTC, and SOL.
  - Used only when the main UI toggle **Live bundle presets** is ON.

## Main behavior changes
### 1. Exit families by archetype
`build_trade_levels()` now resolves TP ladder and stop-management defaults from exit family:

- `trend_runner`
  - For HTF pullback, trend following, VWAP reclaim, SMC continuation, Elliott continuation.
  - Less aggressive protection: breakeven after TP2 and lock after TP3.
- `breakout_balanced`
  - For compression/breakout and RSI regime-style continuation.
  - Protects after TP1, locks later.
- `range_scalp`
  - For range rotation, market-maker scalping, mean reversion.
  - Shorter ladder and faster protection.
- `reversal_defensive`
  - For sweep/fade/exhaustion/absorption reversals.
  - Defensive partial handling.

Existing strategy-level `rule_params` still override the family defaults.

### 2. Live bundle scanner wiring
The analyzer can now evaluate configured live bundles as additional opinions alongside single strategies.

Important: this is opt-in.

In the main UI sidebar:
- **Live bundle presets OFF** = old single-strategy live path.
- **Live bundle presets ON** = analyzer also evaluates `config/live_bundle_presets.json`.

Bundle rows use:
- `strategy_mode = bundle`
- `trade_owner_key = bundle:<bundle_name>`

Single strategy rows use:
- `strategy_mode = single`
- `trade_owner_key = single:<version_id>`

### 3. Trade storage migration
`signal_events` and `paper_trades` get new columns through SQLite migration:

- `strategy_mode`
- `trade_owner_key`
- `exit_family`
- `bundle_components_json`
- `be_trigger_index`
- `lock_trigger_index`
- `lock_to_tp_index`

This supports separate ownership and exit-family-aware trade tracking without breaking older rows.

### 4. Backtest analytics expansion
Backtest result objects, saved run exports, and saved run manifests now include extra CSVs:

- `performance_by_side.csv`
- `performance_by_regime.csv`
- `performance_by_side_regime.csv`
- `performance_by_exit_family.csv`
- `performance_by_score_side_decile.csv`

The Backtest UI now shows these under **Performance blocks**.

### 5. Calibration report expansion
`score_calibration_report.py` now also exports:

- `saved_runs_side_regime.csv`
- `saved_runs_exit_family.csv`

And includes both sections in `score_calibration_report.md`.

## Baby-step test path
1. Start with existing V23 launch steps.
2. Run one short single-strategy backtest.
3. Confirm saved result contains the new performance CSVs.
4. Open Backtest UI → Performance blocks and check long/short, regime, side × regime, and exit-family tables.
5. Start analyzer/main app with **Live bundle presets OFF** and confirm old single-strategy flow still works.
6. Turn **Live bundle presets ON** and use ETH/BTC/SOL with matching component strategies enabled.
7. Confirm signal/paper rows store `strategy_mode`, `trade_owner_key`, and `exit_family`.

## Known remaining gaps
- Bundle live presets are wired but still simple config-driven presets, not a polished Bundle Lab editor.
- Comparison dashboard is improved through saved/exported analytics but not yet a fully polished multi-run visual dashboard.
- Execution friction still uses only current fee/slippage proxy basics; spread/funding realism remains a future step.
- Regime labeling is still based on existing classifier; deeper regime truth labeling is still future work.
