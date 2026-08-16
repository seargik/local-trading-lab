from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .settings import BACKTEST_JOBS_ROOT

HEARTBEAT_PATH = BACKTEST_JOBS_ROOT / "worker_heartbeat.json"
HEARTBEAT_STALE_SECONDS = 20


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_worker_heartbeat(*, state: str, current_job_id: str | None = None, note: str = "") -> None:
    """Write a lightweight heartbeat so the UI can tell whether the worker is alive.

    This intentionally lives under data/backtest_jobs so it shares the same relative
    working directory as the queued/running/completed/failed job folders.
    """
    try:
        HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "state": state,
            "current_job_id": current_job_id,
            "note": note,
            "updated_at": _now_iso(),
            "pid": os.getpid(),
            "cwd": str(Path.cwd()),
            "heartbeat_path": str(HEARTBEAT_PATH),
        }
        HEARTBEAT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        # Heartbeat must never kill the worker.
        return


def read_worker_heartbeat() -> dict[str, Any]:
    if not HEARTBEAT_PATH.exists():
        return {"alive": False, "reason": "missing", "heartbeat_path": str(HEARTBEAT_PATH)}
    try:
        payload = json.loads(HEARTBEAT_PATH.read_text(encoding="utf-8"))
        updated_at = payload.get("updated_at")
        age_seconds = None
        alive = False
        if updated_at:
            try:
                ts = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
                age_seconds = (datetime.now(timezone.utc) - ts).total_seconds()
                alive = age_seconds <= HEARTBEAT_STALE_SECONDS
            except Exception:
                alive = False
        payload["alive"] = bool(alive)
        payload["age_seconds"] = age_seconds
        payload["heartbeat_path"] = str(HEARTBEAT_PATH)
        if not alive and age_seconds is not None:
            payload["reason"] = f"stale_{age_seconds:.1f}s"
        elif not alive:
            payload["reason"] = "unreadable_timestamp"
        return payload
    except Exception as exc:
        return {"alive": False, "reason": f"read_error: {exc}", "heartbeat_path": str(HEARTBEAT_PATH)}
