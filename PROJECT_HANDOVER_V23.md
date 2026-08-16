# Trading App Handover — V23

## What this package is
This package is the latest **working handover build** of the local trading lab.

It is designed to let a new chat or a new developer continue without reopening the full architecture discussion.

## Core direction that is already chosen
Treat these as current defaults unless something is clearly broken:

1. **Crypto-only MVP**
2. **WebSocket-first live architecture**
3. **REST for bootstrap, backfill, gap repair, metadata**
4. **Local-first working mode**
5. **Shared local historical store**
6. **Quant-first, LLM-second**
7. **Paper trading first**
8. **Backtest worker separated from live UI**
9. **Do not remove existing strategies**
10. **Improve strategies holistically, not by hiding weak ones with global filters**

## Current architecture
### Processes
- **Collector**: gathers live data and should keep the shared store growing over time
- **Analyzer**: live scanning / scoring layer
- **Backtest worker**: runs queued backtests and what-if jobs in background
- **Backtest app**: separate Streamlit window for backtests, comparison, toolkit, and now handover

### Shared storage
Primary historical working store:
- `data/ohlcv_store`

Bootstrap sources that were used historically:
- `data_bootstrap/extracted`
- `data_bootstrap/merged`

### App windows
- `app.py` / main app: scanner, live logic
- `backtest_app.py`: separate backtest lab window

## What is already implemented
### Live / scanner side
- live scanner exists
- shared history warm-start was improved, but may still need more work depending on chart timeframe and store completeness
- auto-refresh can be disabled to avoid dimming during manual work

### Backtest side
- standalone **Backtest Lab**
- separate **backtest worker**
- queue system
- save backtest runs
- compare saved runs
- what-if matrix support
- bundle consensus mode in backtest
- foundation toolkit tab for:
  - bundle concepts
  - exit families
  - strategy validity audit
  - calibration reports
  - feature tour

### Strategy layer
Current packaged strategies are intentionally all kept:
- Compression Breakout + OI Expansion
- Elliott Wave Proxy Continuation
- Failed Breakout / Liquidity Sweep Fade
- HTF Pullback Continuation
- Mean Reversion Z-Score Reverter
- OI/Funding Exhaustion Reversal
- Order Book Absorption Reversal
- Range Rotation With Midline Rejection
- Regime Filter / No-Trade Gate
- RSI Best Practices Regime Trader
- Smart Money Sweep Reversal
- Trend Following Alignment Rider
- VWAP Reclaim Trend Continuation
- Market Maker Range Scalper
- HTF Bias + LTF Pullback Entry
- SMC Continuation Reclaim
- Compression Release Scalper

### Concurrency logic direction
Current design direction:
- `one_trade_at_time` should be interpreted as **symbol + direction + strategy** for single strategies
- and **symbol + direction + bundle** for bundles

Meaning:
- same symbol can have long and short at different times under same strategy logic
- bundle trades are separate owners from single-strategy trades
- bundle trades should not be blocked just because a component strategy already has a trade

## What the latest analysis suggests
### Strongest current strategy families
Most promising core families from recent evidence:
- HTF Pullback Continuation
- VWAP Reclaim Trend Continuation
- RSI Best Practices Regime Trader
- Trend Following Alignment Rider
- Range Rotation With Midline Rejection
- Compression Breakout + OI Expansion

### Important findings
1. **Trend / pullback / reclaim** families are the real core
2. Recent performance is often **short-driven**
3. Current partial-profit logic is often **too aggressive**
4. Score thresholding is still weak as a ranking engine
5. Some strategies are **structurally too strict or invalid**, not simply bad

## What should be improved next
Recommended next sequence:

1. **Bundle strategy engine into live path**
2. **Exit-family refactor**
3. **Long/short split analytics in main app and backtest**
4. **Strategy validity audit surfaced in workflows**
5. **Score calibration upgrade**
6. **Better saved comparison dashboard**
7. **Execution friction presets**: fees, slippage, spread, optional funding
8. **Regime ownership analytics**
9. **Overlap / concurrency analytics**

## What is conceptual vs fully wired
### Already wired enough to use
- backtest worker
- queueing
- saved runs
- saved comparisons
- what-if matrix
- bundle consensus mode in backtest
- foundation toolkit UI

### Still partial / not fully production-wired
- bundle engine in live scanner / live execution path
- full side-by-side comparison dashboard for every KPI and every chart
- regime truth labeling depth
- robust execution-friction modeling
- complete long/short analytics everywhere
- advanced overlap/concurrency analytics
- polished score normalization and calibration loop

## Known pain points / things to watch
- backtest worker and comparison have needed several runtime fixes already, so new queue or comparison features should be tested carefully
- if a backtest produces zero trades, make sure empty-result paths are handled gracefully
- scanner history may still look incomplete if the shared store is not fully prepared for the selected timeframe
- very large date ranges can make diagnostics and backtests feel slow if features are rebuilt every time
- port conflicts can happen with Streamlit, especially on `8503`

## Files worth opening first
- `LAUNCH_STEPS_V23.txt`
- `docs/BABY_STEPS_V23.md`
- `docs/KNOWN_ISSUES_V23.md`
- `docs/NEW_CHAT_PROMPT_V23.txt`
- `docs/HANDOVER_V23.md`

## Baby-step launch
1. Create / refresh virtual environment
2. Install requirements
3. Start backtest worker
4. Start backtest app
5. If needed, start main app separately

## Quick command reference
### Install
```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Start backtest worker
```powershell
.\start_backtest_worker.bat
```

### Start backtest app
```powershell
.\start_backtest_only.bat
```

### If port 8503 is busy
```powershell
.\.venv\Scripts\python.exe -m streamlit run .\backtest_app.py --server.port 8504
```

## Best baby-step testing flow
### 1. Single strategy sanity check
- choose one symbol
- choose one strategy
- use a short date range
- run backtest
- save run

### 2. Several strategies separately
- select 2–5 strategies
- queue selected strategies
- wait for worker completion
- compare saved runs

### 3. Bundle
- select 2–5 strategies
- switch to bundle consensus mode
- adjust bundle config JSON
- run bundle backtest
- save run
- compare against single strategies

### 4. Toolkit
Open Foundation Toolkit and review:
- Bundle Lab
- Exit Families
- Validity Audit
- Calibration Reports

### 5. What-if
- run what-if matrix on strongest single strategy
- then run what-if matrix on strongest bundle

## Important principles for the next chat
1. **Do not remove strategies**
2. **Do not flatten everything into one universal filter**
3. **Promote proven strategies, repair broken ones**
4. **Keep single strategies and bundles as separate trade owners**
5. **Use exit families by archetype**
6. **Analyze long and short separately**
7. **Prefer ranking/calibration improvements over blind score cutoffs**
8. **Keep shared storage as the backbone between live and backtest**

## Recommended new-chat prompt
See:
- `docs/NEW_CHAT_PROMPT_V23.txt`

## Final note
This handover is meant to prevent losing context. It is acceptable to improve structure and UI, but avoid re-opening the already-decided architecture unless something is clearly broken.
