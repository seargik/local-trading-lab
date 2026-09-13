from __future__ import annotations

from datetime import timezone
import json
from pathlib import Path
from typing import Any

import pandas as pd

from .future_adaptive_portfolio_holdout_v2823 import (
    FutureAdaptivePortfolioHoldoutResult,
    SNAPSHOT_DIR,
    verify_future_freeze,
)


def _write_frame(frame: pd.DataFrame, path: Path) -> str | None:
    if frame is None or frame.empty:
        return None
    frame.to_csv(path, index=False)
    return str(path)


def save_future_holdout_snapshot(
    result: FutureAdaptivePortfolioHoldoutResult,
    freeze_record: dict[str, Any],
    *,
    job: dict[str, Any] | None = None,
    output_dir: str | Path = SNAPSHOT_DIR,
) -> dict[str, str]:
    verification = verify_future_freeze(freeze_record, check_current=False)
    if not verification.get("ok", False):
        raise ValueError("Refusing to snapshot an invalid V28.23 future freeze.")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    stamp = pd.Timestamp.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    prefix = root / f"{stamp}_{result.freeze_id}_v28_23_future_holdout"
    paths: dict[str, str] = {}
    frames = {
        "comparison": result.comparison,
        "friction_stress": result.friction_stress,
        "adaptive_equity": result.adaptive_equity,
        "static_equity": result.static_equity,
        "adaptive_ledger": result.adaptive_ledger,
        "static_ledger": result.static_ledger,
        "rejected_candidates": result.rejected_candidates,
        "by_symbol": result.by_symbol,
        "by_family": result.by_family,
        "by_month": result.by_month,
    }
    for name, frame in frames.items():
        saved = _write_frame(frame, Path(f"{prefix}_{name}.csv"))
        if saved:
            paths[name] = saved

    summary = {
        "schema_version": "28.23-future-holdout-snapshot-v1",
        "freeze_id": result.freeze_id,
        "freeze_record_sha256": freeze_record.get("record_sha256"),
        "future_framework_sha256": freeze_record.get("future_framework_sha256"),
        "cutoff_utc": freeze_record.get("cutoff_utc"),
        "first_eligible_date": freeze_record.get("first_eligible_date"),
        "target_end_date": freeze_record.get("target_end_date"),
        "source_walk_forward_verdict": freeze_record.get("source_walk_forward_verdict"),
        "job_id": (job or {}).get("job_id"),
        "verdict": result.verdict,
        "integrity": {
            "freeze": result.integrity.get("freeze"),
            "job": {
                "ok": (result.integrity.get("job") or {}).get("ok"),
                "issues": (result.integrity.get("job") or {}).get("issues"),
            },
            "source_evidence": result.integrity.get("source_evidence"),
            "calendar": result.integrity.get("calendar"),
        },
        "critique": result.critique,
    }
    summary_path = Path(f"{prefix}_summary.json")
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    paths["summary"] = str(summary_path)
    return paths
