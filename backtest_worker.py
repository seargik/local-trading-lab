from __future__ import annotations

import json
import time
import traceback
from pathlib import Path

from app_src.backtest_core import run_backtest, save_backtest_result
from app_src.backtest_jobs import ensure_job_dirs, list_jobs, move_job, update_job
from app_src.worker_health import write_worker_heartbeat

POLL_SECONDS = 2


def _claim_next_job() -> Path | None:
    jobs = list_jobs(["queued"])
    if not jobs:
        return None
    job = sorted(jobs, key=lambda x: x.get("created_at", ""))[0]
    return move_job(job["path"], "running")


def process_job(job_path: Path) -> None:
    payload = json.loads(job_path.read_text(encoding="utf-8"))
    write_worker_heartbeat(state="processing", current_job_id=payload.get("job_id"), note="job claimed")
    started_monotonic = time.monotonic()
    tasks = payload.get("tasks")
    if tasks:
        total = len(tasks)
    else:
        strategies = payload.get("strategies") or []
        tasks = [{"name": (s.get("strategy_name") or f"Strategy {idx+1}"), "strategy_payload": s, "config_overrides": {}} for idx, s in enumerate(strategies)]
        total = len(tasks)

    results = []
    job_path = update_job(job_path, {"progress": {"completed": 0, "total": total, "current_strategy": None, "pct": 0.0, "elapsed_seconds": 0.0, "eta_seconds": None}, "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    for idx, task in enumerate(tasks, start=1):
        strategy_payload = task.get("strategy_payload") or {}
        current_name = task.get("name") or strategy_payload.get("strategy_name") or f"Run {idx}"
        config = dict(payload.get("config") or {})
        config.update(task.get("config_overrides") or {})
        elapsed = max(0.0, time.monotonic() - started_monotonic)
        avg_per_task = elapsed / max(idx - 1, 1) if idx > 1 else None
        eta = avg_per_task * (total - (idx - 1)) if avg_per_task is not None else None
        pct = round(((idx - 1) / total) * 100, 2) if total else 0.0
        write_worker_heartbeat(state="processing", current_job_id=payload.get("job_id"), note=f"running {current_name}")
        job_path = update_job(job_path, {"progress": {"completed": idx - 1, "total": total, "current_strategy": current_name, "pct": pct, "elapsed_seconds": round(elapsed, 1), "eta_seconds": round(eta, 1) if eta is not None else None}})
        task_symbols = task.get("symbols") or payload.get("symbols") or []
        result = run_backtest(
            source_root=payload["source_root"],
            symbols=task_symbols,
            strategy_payload=strategy_payload,
            entry_timeframe=payload["entry_timeframe"],
            analysis_timeframe=payload["analysis_timeframe"],
            start_date=payload["start_date"],
            end_date=payload["end_date"],
            config=config,
        )
        save_name = f"{current_name} {'/'.join(task_symbols) if task_symbols else ''} {payload['entry_timeframe']}->{payload['analysis_timeframe']} [{payload['job_id']}]".strip()
        comment = (payload.get("comment") or "").strip()
        if comment:
            comment = comment + "\n"
        comment += f"Batch job: {payload['job_id']}"
        saved_dir = save_backtest_result(result, save_name, comment)
        results.append({
            "strategy_name": strategy_payload.get("strategy_name") or current_name,
            "scenario_name": task.get("scenario_name") or current_name,
            "symbol": (task.get("symbols") or payload.get("symbols") or [None])[0],
            "run_dir": str(saved_dir),
            "summary": result.summary,
            "task_meta": task.get("task_meta") or {},
            "config_overrides": task.get("config_overrides") or {},
        })
        elapsed = max(0.0, time.monotonic() - started_monotonic)
        avg_per_task = elapsed / max(idx, 1)
        eta = avg_per_task * (total - idx) if total > idx else 0.0
        pct = round((idx / total) * 100, 2) if total else 100.0
        write_worker_heartbeat(state="processing", current_job_id=payload.get("job_id"), note=f"finished task {idx}/{total}")
        job_path = update_job(job_path, {"results": results, "progress": {"completed": idx, "total": total, "current_strategy": current_name, "pct": pct, "elapsed_seconds": round(elapsed, 1), "eta_seconds": round(eta, 1)}})
    total_elapsed = max(0.0, time.monotonic() - started_monotonic)
    job_path = move_job(job_path, "completed", {"results": results, "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "progress": {"completed": total, "total": total, "current_strategy": None, "pct": 100.0, "elapsed_seconds": round(total_elapsed, 1), "eta_seconds": 0.0}})
    write_worker_heartbeat(state="completed", current_job_id=payload.get("job_id"), note=f"completed {total} tasks")


if __name__ == "__main__":
    ensure_job_dirs()
    write_worker_heartbeat(state="starting", note="Backtest worker started")
    print("Backtest worker started. Watching queued jobs...")
    while True:
        write_worker_heartbeat(state="polling", note="watching queued jobs")
        job_path = _claim_next_job()
        if job_path is None:
            time.sleep(POLL_SECONDS)
            continue
        try:
            write_worker_heartbeat(state="claimed", current_job_id=job_path.stem, note="claimed queued job")
            print(f"Processing {job_path.name}")
            process_job(job_path)
            print(f"Completed {job_path.name}")
        except Exception as exc:
            tb = traceback.format_exc()
            try:
                job_path = move_job(job_path, "failed", {"error": str(exc), "traceback": tb, "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            except Exception:
                pass
            write_worker_heartbeat(state="failed", current_job_id=job_path.stem, note=str(exc))
            print(f"FAILED {job_path.name}: {exc}")
            print(tb)
            time.sleep(1)
