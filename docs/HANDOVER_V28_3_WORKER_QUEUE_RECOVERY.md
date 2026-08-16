# V28.3 Worker queue recovery patch

## What changed
- Added worker-health diagnostics inherited from V28.2.
- Added `Run selected next` for queued jobs. This prioritizes the selected queued job but does not start the worker.
- Added `Requeue selected failed/running job` for failed jobs or stale running jobs.
- Added `Show worker start command` inside the queue UI.

## Important meaning
`Refresh jobs` only refreshes the Streamlit screen. It does not start the worker and does not force a job to run.

Queued jobs start only when `backtest_worker.py` is running from the same project folder.

## Safe recovery flow
1. If jobs are queued at 0%, start `.\start_backtest_worker.bat` in a separate PowerShell window.
2. Click `Refresh jobs`.
3. If a job is running but heartbeat is stale, requeue it.
4. Use `Run selected next` only to prioritize a queued job.
