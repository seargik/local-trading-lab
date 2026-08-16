# Usage Guide: Next Features Practice and Identification

This package keeps the current app working and adds a **foundation toolkit** for the next wave of improvements.

It includes:
- direction-scoped `one_trade_at_time` behavior in backtests
- a **strategy validity audit** script
- a **saved-run score calibration + long/short/regime report** script
- example **bundle strategy** templates
- example **exit family** templates

These tools are meant to help you practice and identify what should be promoted, repaired, or combined **before** the full UI engine for bundles and exit families is implemented.

## 1. What changed in code right now

`one_trade_at_time=true` now behaves as:
- lock by **symbol + direction + strategy run**
- LONG and SHORT can overlap inside the same strategy run
- same-side re-entry is blocked until the previous trade for that side is closed plus cooldown

That means the backtest can now simulate a hedge-style situation more realistically.

## 2. What is not fully integrated yet

These are **prepared and documented**, but not fully wired into the Streamlit UI as a finished engine:
- bundle strategy execution engine
- exit-family-aware trade management inside the UI
- strategy validity audit page inside Streamlit
- score calibration dashboard page inside Streamlit

For now, use the helper scripts below.

## 3. How to practice / identify each feature

### Bundle strategies
Goal: combine several strategies into one higher-conviction decision.

How to practice now:
1. Run the component strategies separately on the same symbol/date window.
2. Compare their side agreement, trade count, and drawdown profile.
3. Use the example bundle templates in `config/bundle_strategy_examples.json`.
4. Start with these consensus rules:
   - `all_pass`
   - `n_of_m`
   - `weighted_consensus`
5. Use bundles first as **selection logic**, not as a replacement for all single strategies.

What to identify:
- Which strategy families agree most often in the same direction?
- Which bundles reduce drawdown without killing too many trades?
- Which bundles are symbol-specific?

### Symbol + direction + strategy concurrency logic
Goal: allow same symbol to hold separate LONG and SHORT trades under the same strategy family.

How to practice now:
1. Run the same strategy on a choppy recent window.
2. Keep `one_trade_at_time=true`.
3. Confirm that same-side stacking is blocked, but opposite-side signals are still possible later.
4. Review the trade ledger by `symbol`, `side`, and `strategy_name`.

What to identify:
- Are opposite-side trades actually helping, or are they mostly noise?
- Does concurrency add useful hedging, or just churn?

### Better exit families / exit-family refactor
Goal: stop using one exit philosophy for every strategy archetype.

Use these families:
- `trend_runner`
- `breakout_balanced`
- `range_scalp`
- `reversal_defensive`

How to practice now:
1. Copy a strategy packet and only change the exit-family-related fields in `rule_params`.
2. Test later TP1 / smaller TP1 size for trend strategies.
3. Test earlier TP1 / larger first partial for range and reversal strategies.
4. Compare `edge_partial_vs_full_usd`, `final_tp_hit_rate`, and `avg_realized_fraction_full_target`.

What to identify:
- Trend strategies with negative `edge_partial_vs_full_usd` are likely clipping winners too early.
- Range and reversal strategies usually benefit from faster monetization.

### Stronger analytics by long vs short and by regime / long-short split analytics
Goal: stop letting one side hide the weakness of the other.

How to practice now:
1. Run `score_calibration_report.py` on your saved runs.
2. Review:
   - long-only stats
   - short-only stats
   - regime split stats
3. Compare recent windows separately from long windows.

What to identify:
- Are recent profits coming mostly from shorts?
- Which strategies are regime-dependent?
- Which strategies fail only on one side?

### Strategy validity audit
Goal: find strategies that are structurally impossible or too strict.

How to practice now:
1. Run `audit_strategy_validity.py`.
2. Review:
   - threshold vs max reachable LONG score
   - threshold vs max reachable SHORT score
   - enabled rule weight balance
3. Repair packets where threshold is above reachable max score.

What to identify:
- impossible trigger conditions
- strategies that require nearly perfect confluence every time
- broken or inactive rule packets

### Score calibration upgrade
Goal: improve score from a simple gate into a useful ranking engine.

How to practice now:
1. Run `score_calibration_report.py`.
2. Inspect score deciles and side/regime splits.
3. Focus on strategies where higher score clearly improves:
   - win rate
   - expectancy
   - drawdown profile
4. Avoid global score cutoffs like `>= 85` unless the strategy actually supports that distribution.

What to identify:
- monotonic score behavior by strategy
- strategies where score is informative vs decorative
- candidates for score normalization or percentile ranking

## 4. Suggested workflow

1. Run single strategies first.
2. Audit invalid or dormant packets.
3. Split results by long/short and regime.
4. Tune exit families by archetype.
5. Build bundle templates from the strongest complementary strategies.
6. Only then wire those bundles into live and backtest execution.

## 5. Most useful scripts in this package

### Strategy validity audit
```powershell
.\.venv\Scripts\python.exe .udit_strategy_validity.py
```

### Score calibration + side/regime report over saved runs
```powershell
.\.venv\Scripts\python.exe .\score_calibration_report.py --saved-runs-root dataacktests --out-dir analysis_reports
```

## 6. Launch reminder

Start the app as usual:
```powershell
.\start_backtest_worker.bat
.\start_backtest_only.bat
```

If port 8503 is busy:
```powershell
.\.venv\Scripts\python.exe -m streamlit run .acktest_app.py --server.port 8504
```
