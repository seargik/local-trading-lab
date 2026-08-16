from __future__ import annotations

import json
from pathlib import Path

from app_src.backtest_jobs import ensure_job_dirs, list_jobs
from app_src.settings import BACKTEST_JOBS_ROOT, BACKTEST_JOBS_QUEUED_DIR, BACKTEST_JOBS_RUNNING_DIR, BACKTEST_JOBS_COMPLETED_DIR, BACKTEST_JOBS_FAILED_DIR
from app_src.worker_health import read_worker_heartbeat


def main() -> None:
    ensure_job_dirs()
    print("=== Backtest worker diagnostics ===")
    print(f"Project folder: {Path.cwd()}")
    print(f"Jobs root:      {BACKTEST_JOBS_ROOT.resolve()}")
    print(f"Queued dir:     {BACKTEST_JOBS_QUEUED_DIR.resolve()}")
    print(f"Running dir:    {BACKTEST_JOBS_RUNNING_DIR.resolve()}")
    print(f"Completed dir:  {BACKTEST_JOBS_COMPLETED_DIR.resolve()}")
    print(f"Failed dir:     {BACKTEST_JOBS_FAILED_DIR.resolve()}")
    health = read_worker_heartbeat()
    print("\n--- Worker heartbeat ---")
    print(json.dumps(health, indent=2, ensure_ascii=False))
    jobs = list_jobs()
    counts = {}
    for j in jobs:
        counts[j.get("status")] = counts.get(j.get("status"), 0) + 1
    print("\n--- Job counts ---")
    print(json.dumps(counts, indent=2, ensure_ascii=False))
    for status in ["running", "queued", "failed", "completed"]:
        subset = [j for j in jobs if j.get("status") == status]
        if not subset:
            continue
        print(f"\n--- Latest {status} jobs ---")
        for j in subset[:5]:
            progress = j.get("progress") or {}
            print(
                f"{j.get('job_id')} | {j.get('job_type')} | "
                f"done {progress.get('completed', 0)}/{progress.get('total', 0)} | "
                f"created {j.get('created_at')} | current {progress.get('current_strategy')}"
            )
            if status == "failed":
                print(f"  error: {j.get('error')}")
                tb = str(j.get("traceback") or "")
                if tb:
                    print("  traceback tail:")
                    print("\n".join(tb.splitlines()[-12:]))
    print("\n--- Interpretation ---")
    if not health.get("alive") and counts.get("queued", 0):
        print("Worker is not active for this folder. Start .\\start_backtest_worker.bat from this exact package folder.")
    elif counts.get("running", 0):
        print("Worker appears to be processing a running job. Queued jobs remain at 0% until earlier jobs finish.")
    elif counts.get("failed", 0):
        print("Open failed job traceback above, fix the error, then requeue or delete failed job.")
    else:
        print("No obvious queue issue found. If UI is stale, click Refresh jobs or restart Streamlit.")


if __name__ == "__main__":
    main()
