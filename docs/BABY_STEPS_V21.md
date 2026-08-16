# Baby steps: how to see the new and previous developments

## 1) Launch

Open PowerShell in the project folder.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\start_backtest_worker.bat
.\start_backtest_only.bat
```

If port 8503 is busy:

```powershell
.\.venv\Scripts\python.exe -m streamlit run .\backtest_app.py --server.port 8504
```

## 2) Where to see earlier developments

### Backtest Lab tab
Use this for the already-developed flow:
- run a single backtest
- queue multi-strategy jobs
- queue what-if matrix jobs
- save runs
- compare saved runs
- review worker progress

### Foundation Toolkit tab
Use this for the newer foundation work:
- Bundle Lab
- Exit Families
- Validity Audit
- Calibration Reports
- Feature Tour

## 3) Bundle strategies
Open **Foundation Toolkit → Bundle Lab**.

What to do:
1. choose a preset bundle
2. read the component strategies and thresholds
3. backtest those component strategies in Backtest Lab first
4. compare them
5. use the bundle preset as your decision design for future bundle-engine runs

## 4) Symbol + direction + strategy trade concurrency logic
Open **Foundation Toolkit → Bundle Lab** and read the concurrency section.

Practice rule:
- BTC + LONG + HTF Pullback is one owner
- BTC + SHORT + HTF Pullback is another owner
- BTC + LONG + ETH Core Consensus bundle is another owner

This means a bundle trade is not blocked just because one of its component strategies already has a trade.

## 5) Better exit families / exit-family refactor
Open **Foundation Toolkit → Exit Families**.

Practice:
1. map each strategy to one exit family
2. rerun backtests with that family in mind
3. do not use one TP philosophy for every strategy type

## 6) Strategy validity audit
Open **Foundation Toolkit → Validity Audit**.

Look for:
- LONG_UNREACHABLE
- SHORT_UNREACHABLE
- NEAR_PERFECT_CONFLUENCE
- NO_ENABLED_RULES

Those strategies are not removed. They are candidates for repair.

## 7) Score calibration upgrade
Open **Foundation Toolkit → Calibration Reports**.

What to inspect:
- **Overview**: strongest saved runs
- **Long / Short split**: whether edge is one-sided
- **Regime split**: where the strategy actually works
- **Score deciles**: whether higher score really means better trade quality

## 8) Stronger analytics by long vs short and by regime
Still in **Calibration Reports**:
- use Long / Short split to check directional dependence
- use Regime split to see trend/range ownership
- use Score deciles to judge ranking quality

## 9) Scanner / shared history reminder
For the main app scanner to reuse history well:
1. keep `data/ohlcv_store` updated
2. restart the main app after store updates
3. use enough chart candles in the scanner UI

## 10) Best practice order
1. validate the strategy can fire
2. assign exit family
3. backtest single strategies
4. compare saved runs
5. study long/short and regime splits
6. only then promote a bundle design
