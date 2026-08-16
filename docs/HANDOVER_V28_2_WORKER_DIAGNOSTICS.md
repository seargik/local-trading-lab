# V28.2 Worker diagnostics patch

## What changed
- Added worker heartbeat file under `data/backtest_jobs/worker_heartbeat.json`.
- Backtest UI now warns when jobs are queued but the worker is not alive in the same project folder.
- Added `diagnose_backtest_worker.py` to print queued/running/failed counts and worker heartbeat.

## Why
Queued jobs at 0% usually mean one of these:
1. Worker window is not running.
2. Worker crashed before claiming jobs.
3. Worker is running from another package folder and watches another `data/backtest_jobs` directory.
4. A long older job is running and newer jobs wait.

## How to use
Run:

```powershell
.\.venv\Scripts\python.exe diagnose_backtest_worker.py
```

Then start the worker from the same folder:

```powershell
.\start_backtest_worker.bat
```
