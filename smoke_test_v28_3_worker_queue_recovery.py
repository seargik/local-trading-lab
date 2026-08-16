from __future__ import annotations

import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for rel in [
    "app_src/backtest_jobs.py",
    "app_src/backtest_ui.py",
    "app_src/worker_health.py",
    "backtest_worker.py",
    "diagnose_backtest_worker.py",
]:
    py_compile.compile(str(ROOT / rel), doraise=True)

ui = (ROOT / "app_src/backtest_ui.py").read_text(encoding="utf-8")
jobs = (ROOT / "app_src/backtest_jobs.py").read_text(encoding="utf-8")
assert "Run selected next" in ui
assert "Requeue selected failed/running job" in ui
assert "def requeue_job" in jobs
assert "def prioritize_queued_job" in jobs
print("V28.3 smoke test passed: queue recovery buttons and worker diagnostics are available.")
