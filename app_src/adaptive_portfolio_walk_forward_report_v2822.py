from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
from typing import Any

import pandas as pd

from .adaptive_portfolio_walk_forward_v2822 import (
    AdaptivePortfolioWalkForwardResult,
    SNAPSHOT_DIR,
)


def save_portfolio_walk_forward_snapshot(
    result: AdaptivePortfolioWalkForwardResult,
    freeze_record: dict[str, Any],
    *,
    output_dir: str | Path = SNAPSHOT_DIR,
) -> Path:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    freeze_id = str(result.freeze_id or freeze_record.get("freeze_id") or "portfolio")
    run_dir = root / f"{now.strftime('%Y%m%dT%H%M%SZ')}_{freeze_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "version": "28.22",
        "created_at": now.isoformat(),
        "freeze_id": freeze_id,
        "framework_sha256": freeze_record.get("framework_sha256"),
        "freeze_record_sha256": freeze_record.get("record_sha256"),
        "source": freeze_record.get("source") or {},
        "selection_contamination_warning": True,
        "verdict": result.verdict,
        "integrity": result.integrity,
        "critique": result.critique,
        "files": {
            "freeze_record": "freeze_record.json",
            "folds": "folds.csv",
            "aggregate_comparison": "aggregate_comparison.csv",
            "friction_stress": "friction_stress.csv",
            "adaptive_equity": "adaptive_equity.csv",
            "static_equity": "static_equity.csv",
            "adaptive_ledger": "adaptive_ledger.parquet",
            "static_ledger": "static_ledger.parquet",
            "rejected_candidates": "rejected_candidates.parquet",
            "by_symbol": "by_symbol.csv",
            "by_family": "by_family.csv",
            "by_month": "by_month.csv"
        }
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    (run_dir / "freeze_record.json").write_text(json.dumps(freeze_record, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    csv_frames = {
        "folds.csv": result.folds,
        "aggregate_comparison.csv": result.aggregate_comparison,
        "friction_stress.csv": result.friction_stress,
        "adaptive_equity.csv": result.adaptive_equity,
        "static_equity.csv": result.static_equity,
        "by_symbol.csv": result.by_symbol,
        "by_family.csv": result.by_family,
        "by_month.csv": result.by_month,
    }
    for filename, frame in csv_frames.items():
        (frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()).to_csv(run_dir / filename, index=False)

    parquet_frames = {
        "adaptive_ledger.parquet": result.adaptive_ledger,
        "static_ledger.parquet": result.static_ledger,
        "rejected_candidates.parquet": result.rejected_candidates,
    }
    for filename, frame in parquet_frames.items():
        (frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()).to_parquet(run_dir / filename, index=False)
    return run_dir
