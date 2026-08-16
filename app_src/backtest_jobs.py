from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any

import pandas as pd

from .settings import (
    BACKTEST_JOBS_COMPLETED_DIR,
    BACKTEST_JOBS_FAILED_DIR,
    BACKTEST_JOBS_QUEUED_DIR,
    BACKTEST_JOBS_ROOT,
    BACKTEST_JOBS_RUNNING_DIR,
)


STATUS_DIRS = {
    "queued": BACKTEST_JOBS_QUEUED_DIR,
    "running": BACKTEST_JOBS_RUNNING_DIR,
    "completed": BACKTEST_JOBS_COMPLETED_DIR,
    "failed": BACKTEST_JOBS_FAILED_DIR,
}


def ensure_job_dirs() -> None:
    BACKTEST_JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    for path in STATUS_DIRS.values():
        path.mkdir(parents=True, exist_ok=True)


def _job_path(job_id: str, status: str) -> Path:
    ensure_job_dirs()
    return STATUS_DIRS[status] / f"{job_id}.json"




def _resolve_existing_job_path(path_or_id: str | Path) -> Path | None:
    candidate = Path(path_or_id)
    if candidate.exists():
        return candidate
    job_id = candidate.stem if candidate.suffix == ".json" else str(path_or_id)
    ensure_job_dirs()
    for status in ["running", "queued", "completed", "failed"]:
        maybe = _job_path(job_id, status)
        if maybe.exists():
            return maybe
    return None

def create_batch_job(*, source_root: str, symbols: list[str], entry_timeframe: str, analysis_timeframe: str, start_date: str, end_date: str, config: dict[str, Any], strategies: list[dict[str, Any]], comment: str = "", extra: dict[str, Any] | None = None) -> Path:
    ensure_job_dirs()
    created_at = pd.Timestamp.utcnow().isoformat()
    job_id = pd.Timestamp.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    payload = {
        "job_id": job_id,
        "job_type": "batch_backtest",
        "status": "queued",
        "created_at": created_at,
        "source_root": source_root,
        "symbols": symbols,
        "entry_timeframe": entry_timeframe,
        "analysis_timeframe": analysis_timeframe,
        "start_date": start_date,
        "end_date": end_date,
        "config": config,
        "strategies": strategies,
        "comment": comment or "",
        "progress": {"completed": 0, "total": len(strategies), "current_strategy": None},
        "results": [],
    }
    if extra:
        payload.update(extra)
    target = _job_path(job_id, "queued")
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return target


def list_jobs(statuses: list[str] | None = None) -> list[dict[str, Any]]:
    ensure_job_dirs()
    statuses = statuses or ["queued", "running", "completed", "failed"]
    out: list[dict[str, Any]] = []
    for status in statuses:
        for path in sorted(STATUS_DIRS[status].glob('*.json'), reverse=True):
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
                payload['status'] = status
                payload['path'] = str(path)
                out.append(payload)
            except Exception:
                continue
    return out


def load_job(path_or_id: str | Path) -> dict[str, Any]:
    path = _resolve_existing_job_path(path_or_id)
    if path is None:
        raise FileNotFoundError(path_or_id)
    return json.loads(path.read_text(encoding='utf-8'))


def move_job(path: str | Path, status: str, mutate: dict[str, Any] | None = None) -> Path:
    src = Path(path)
    payload = json.loads(src.read_text(encoding='utf-8'))
    payload['status'] = status
    payload['updated_at'] = pd.Timestamp.utcnow().isoformat()
    if mutate:
        payload.update(mutate)
    dst = _job_path(payload['job_id'], status)
    dst.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
    if src.resolve() != dst.resolve() and src.exists():
        src.unlink(missing_ok=True)
    return dst


def update_job(path: str | Path, mutate: dict[str, Any]) -> Path:
    src = _resolve_existing_job_path(path)
    candidate = Path(path)
    if src is None:
        ensure_job_dirs()
        job_id = candidate.stem if candidate.suffix == ".json" else str(path)
        src = _job_path(job_id, "running")
        payload: dict[str, Any] = {"job_id": job_id, "status": "running"}
    else:
        payload = json.loads(src.read_text(encoding='utf-8'))
    payload.update(mutate)
    payload['updated_at'] = pd.Timestamp.utcnow().isoformat()
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
    return src


def mark_failed(path: str | Path, error: str) -> Path:
    return move_job(path, 'failed', {'error': error, 'traceback': traceback.format_exc()})


def create_task_job(*, source_root: str, symbols: list[str], entry_timeframe: str, analysis_timeframe: str, start_date: str, end_date: str, base_config: dict[str, Any], tasks: list[dict[str, Any]], comment: str = "", job_type: str = "scenario_backtest", extra: dict[str, Any] | None = None) -> Path:
    ensure_job_dirs()
    created_at = pd.Timestamp.utcnow().isoformat()
    job_id = pd.Timestamp.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    payload = {
        "job_id": job_id,
        "job_type": job_type,
        "status": "queued",
        "created_at": created_at,
        "source_root": source_root,
        "symbols": symbols,
        "entry_timeframe": entry_timeframe,
        "analysis_timeframe": analysis_timeframe,
        "start_date": start_date,
        "end_date": end_date,
        "config": base_config,
        "tasks": tasks,
        "comment": comment or "",
        "progress": {"completed": 0, "total": len(tasks), "current_strategy": None},
        "results": [],
    }
    if extra:
        payload.update(extra)
    target = _job_path(job_id, "queued")
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return target


def delete_job(path_or_id: str | Path) -> None:
    candidate = Path(path_or_id)
    if candidate.exists():
        candidate.unlink(missing_ok=True)
        return
    ensure_job_dirs()
    for status in STATUS_DIRS:
        path = _job_path(str(path_or_id), status)
        if path.exists():
            path.unlink(missing_ok=True)
            return


def requeue_job(path_or_id: str | Path, *, clear_results: bool = True) -> Path:
    """Move a failed/running/queued job back to queued and reset progress.

    This is a manual recovery helper. Do not use it for a job that is actively
    being processed by a healthy worker.
    """
    src = _resolve_existing_job_path(path_or_id)
    if src is None:
        raise FileNotFoundError(path_or_id)
    payload = json.loads(src.read_text(encoding="utf-8"))
    tasks = payload.get("tasks")
    total = len(tasks) if tasks else len(payload.get("strategies") or [])
    payload["status"] = "queued"
    payload["progress"] = {"completed": 0, "total": total, "current_strategy": None, "pct": 0.0, "elapsed_seconds": 0.0, "eta_seconds": None}
    if clear_results:
        payload["results"] = []
    payload.pop("error", None)
    payload.pop("traceback", None)
    payload["requeued_at"] = pd.Timestamp.utcnow().isoformat()
    payload["requeue_count"] = int(payload.get("requeue_count") or 0) + 1
    payload["updated_at"] = pd.Timestamp.utcnow().isoformat()
    dst = _job_path(str(payload.get("job_id")), "queued")
    dst.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    if src.resolve() != dst.resolve() and src.exists():
        src.unlink(missing_ok=True)
    return dst


def prioritize_queued_job(path_or_id: str | Path) -> Path:
    """Move a queued job to the front of the queue by making created_at very old."""
    src = _resolve_existing_job_path(path_or_id)
    if src is None:
        raise FileNotFoundError(path_or_id)
    payload = json.loads(src.read_text(encoding="utf-8"))
    if payload.get("status") != "queued" and src.parent != BACKTEST_JOBS_QUEUED_DIR:
        raise ValueError("Only queued jobs can be prioritized. Requeue running/failed jobs first.")
    payload["created_at"] = "1970-01-01T00:00:00+00:00"
    payload["prioritized_at"] = pd.Timestamp.utcnow().isoformat()
    payload["updated_at"] = pd.Timestamp.utcnow().isoformat()
    src.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return src


def format_job_progress(payload: dict[str, Any]) -> dict[str, Any]:
    progress = dict(payload.get("progress") or {})
    completed = int(progress.get("completed") or 0)
    total = int(progress.get("total") or 0)
    pct = round((completed / total) * 100, 2) if total else 0.0
    return {**progress, "pct": pct}
