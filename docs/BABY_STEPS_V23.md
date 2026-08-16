# Baby steps — V23

## 1. Launch the current working setup
1. Open PowerShell in the project folder
2. Install dependencies if needed
3. Start the backtest worker
4. Start the backtest app

### Commands
```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\start_backtest_worker.bat
.\start_backtest_only.bat
```

If port `8503` is busy:
```powershell
.\.venv\Scripts\python.exe -m streamlit run .\backtest_app.py --server.port 8504
```

## 2. Prepare history
Use:
- `data/ohlcv_store`

In Backtest Lab:
- set **History folder** to `data/ohlcv_store`
- click **Scan files**

## 3. Test one single strategy
1. Select one strategy in **Strategies for backtest worker**
2. Keep **Direct run mode = Single strategy**
3. Pick one symbol, like `ETHUSDT`
4. Pick a short date range first
5. Click **Run backtest now**
6. Save the run

## 4. Test several strategies separately
1. Select 2–5 strategies
2. Click **Queue selected strategies**
3. Open **Backtest worker queue**
4. Wait for jobs to finish
5. Open **Saved backtests and comparison**
6. Compare the completed runs

## 5. Test a bundle
1. Select 2–5 strategies
2. Switch **Direct run mode** to **Bundle consensus**
3. Open **Bundle config JSON**
4. Start with a simple rule:
```json
{
  "bundle_name": "ETH Core Bundle",
  "bundle_mode": "n_of_m",
  "n_required": 2,
  "bundle_threshold": 2.0,
  "component_min_score": 70,
  "weights": {}
}
```
5. Run backtest now
6. Save the run
7. Compare it against the single-strategy runs

## 6. Test what-if on a single strategy
1. Go back to **Single strategy**
2. Select a strong strategy
3. Adjust **What-if matrix config JSON**
4. Click **Run what-if matrix**
5. Save useful variants

## 7. Test what-if on a bundle
1. Switch back to **Bundle consensus**
2. Keep the same selected strategies
3. Adjust **What-if matrix config JSON**
4. Click **Run what-if matrix**
5. Compare those runs with the base bundle run

## 8. Review the Foundation Toolkit
Open the **Foundation Toolkit** tab and go through:

### Bundle Lab
Use it to understand bundle components and bundle ownership

### Exit Families
Map strategies into:
- trend / pullback
- breakout
- range / reversion
- reversal / exhaustion

### Validity Audit
Look for:
- unreachable thresholds
- near-perfect-confluence setups
- over-strict strategies

### Calibration Reports
Review:
- overview
- long / short split
- regime split
- score deciles

## 9. Practice the key concepts
### Bundle strategies
Treat bundles as separate trade owners

### Symbol + direction + strategy concurrency
Interpret `one_trade_at_time` as:
- symbol + direction + strategy
- or symbol + direction + bundle

### Better exit families
Do not use one exit style for every strategy family

### Stronger analytics by long vs short and by regime
Do not trust blended performance only

### Strategy validity audit
Repair broken strategies instead of removing them

### Score calibration upgrade
Use score as a ranking tool, not only a gate

## 10. Save and compare
Use saved-run filters:
- favorites
- strategy filter
- symbol filter
- min PF
- min PnL $
- min trades

Then compare the strongest results.

## 11. Start a new chat safely
Use the file:
- `docs/NEW_CHAT_PROMPT_V23.txt`

And attach / mention:
- this package
- `docs/HANDOVER_V23.md`
