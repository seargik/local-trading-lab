# V25 Patch Handover — Evidence Layer

## Source baseline
This patch continues from the V24 package. It does not reopen the architecture decisions from V23/V24.

Locked principles preserved:
- crypto-only MVP
- local-first workflow
- WebSocket-first live architecture, REST for bootstrap/backfill/metadata
- shared historical store remains `data/ohlcv_store`
- backtest worker remains separate from the main UI
- quant-first, LLM-second
- existing strategies are preserved
- single strategies and bundle strategies remain separate trade owners

## What was developed in V25

### 1. Detailed regime labels
New file:
- `app_src/regime_v25.py`

Backtest trades now get extra evidence columns:
- `regime_detail`
- `regime_group`
- `regime_reason`
- `trend_regime_score`
- `range_regime_score`
- `squeeze_regime_score`
- `panic_regime_score`

The old broad `regime` column is still preserved.

### 2. New regime analytics exports
New saved/exported CSV files:
- `performance_by_detailed_regime.csv`
- `performance_by_side_detailed_regime.csv`

These show which strategy/side works in specific market conditions such as aligned bull trend, aligned bear trend, range edge, compression breakout, liquidity sweep, panic/high volatility, and mixed chop.

### 3. Threshold recommendation analytics
New file:
- `app_src/calibration_v25.py`

New saved/exported CSV file:
- `threshold_recommendations.csv`

This recommends where to test score thresholds by:
- strategy mode
- trade owner key
- side
- regime group
- detailed regime

Important: this is analytics-only. It does not automatically rewrite strategy configs.

### 4. Overlap / concurrency analytics
New file:
- `app_src/overlap_analytics.py`

The saved-run comparison UI now calculates overlap across selected runs using 15-minute buckets:
- same-symbol same-side overlaps
- opposite-side conflicts
- owner-pair overlap frequency

This helps identify whether two strategies duplicate each other, conflict, or add complementary evidence.

### 5. Backtest UI update
Saved-run comparison now has **V25 evidence details** with tabs:
- Long/short
- Broad regime
- Detailed regime
- Thresholds
- Overlap
- Exit family
- Friction
- Bundle/owner validation

Direct backtest result view also shows:
- V25 detailed regime split
- V25 side × detailed regime split
- V25 threshold recommendations

### 6. Calibration report update
`score_calibration_report.py` now exports:
- `saved_runs_detailed_regime.csv`
- `saved_runs_side_detailed_regime.csv`
- `saved_runs_threshold_recommendations.csv`
- `saved_runs_overlap_same_side.csv`
- `saved_runs_opposite_side_conflicts.csv`
- `saved_runs_owner_pair_overlap.csv`

The Markdown report includes V25 sections for detailed regime, threshold recommendations, and overlap/concurrency.

## How to see the change

### 1. Run smoke test
```powershell
.\.venv\Scripts\python.exe smoke_test_v25_patch.py
```
Expected:
```text
V25 smoke test passed: patched files compile and V25 markers exist.
```

### 2. Run a normal backtest
Use the Backtest Lab as before:
```powershell
.\start_backtest_worker.bat
.\start_backtest_only.bat
```

Run one short test first:
- symbol: `ETHUSDT` or `BTCUSDT`
- one strategy
- short date range
- save the run

### 3. Inspect direct result
After the run completes, open the result and check:
- Performance blocks
- V25 detailed regime split
- V25 side × detailed regime split
- V25 threshold recommendations

### 4. Inspect saved-run comparison
Save at least 2–5 runs, ideally:
- one strong single strategy
- one weak single strategy
- one bundle run
- same symbol/date range if possible

Open:
- Saved backtests and comparison
- select the runs
- compare
- open **V25 evidence details**

Look especially at:
- **Detailed regime**: where each strategy works
- **Thresholds**: which score threshold should be tested next
- **Overlap**: whether strategies duplicate or conflict
- **Friction**: whether edge survives execution costs
- **Bundle/owner validation**: whether bundle ownership adds value

### 5. Generate calibration report
```powershell
.\.venv\Scripts\python.exe score_calibration_report.py --saved-runs-root data\backtests --out-dir analysis_reports
```

Open:
- `analysis_reports/score_calibration_report.md`
- `analysis_reports/saved_runs_threshold_recommendations.csv`
- `analysis_reports/saved_runs_overlap_same_side.csv`

## What is still left

### 1. Full live execution validation
Still needed:
- confirm live bundle signal appears correctly in scanner UI
- confirm bundle-owned paper trade opens correctly
- confirm single strategy trades and bundle trades do not block each other incorrectly
- confirm live paper trade uses the intended exit family

### 2. Better regime truth labeling
V25 gives deeper heuristic labels, but not final regime truth. Later work should add:
- forward-volatility expansion labels
- post-breakout follow-through quality
- chop/fakeout labels
- market-session/day context
- symbol-specific regime behavior

### 3. Threshold recommendations need what-if automation
V25 produces recommendations. Next step should add a button/workflow to queue threshold what-if jobs from selected recommendation rows.

### 4. Overlap needs execution simulation
V25 detects overlaps. Later work should simulate portfolio-level outcomes:
- allow all overlapping trades
- allow best score only
- allow bundle only
- block duplicate owners
- cap correlated symbol exposure

### 5. Comparison dashboard can still be polished
The dashboard is now more useful, but it can still be improved with:
- traffic-light candidate labels
- best-by-side/regime cards
- charts for threshold curves
- overlap heatmaps
- bundle-vs-component visual summary

## Recommended V26
Build **V26: recommendation-to-action workflow**:
1. select threshold recommendation rows
2. queue what-if jobs automatically
3. compare old vs proposed threshold
4. mark strategy segment as candidate / repair / avoid
5. optionally save a new strategy version only after evidence supports it
