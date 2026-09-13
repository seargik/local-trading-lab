from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
from typing import Any

import pandas as pd

from .shared_account_replay_v2821 import SharedAccountReplayResult

DEFAULT_REPORT_DIR = Path("data/backtest_reviews/shared_account_replays")
POLICY_FILES = {
    "shared_account": Path("config/shared_account_policy.json"),
    "adaptive_evidence": Path("config/adaptive_evidence_policy.json"),
    "market_state_router": Path("config/market_state_router_policy.json"),
    "market_state_replay": Path("config/market_state_replay_policy.json"),
}


def _canonical_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return sha256(raw).hexdigest()


def policy_fingerprints(paths: dict[str, Path] | None = None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, path in (paths or POLICY_FILES).items():
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            out[name] = {
                "path": str(path),
                "version": str(payload.get("version") or "") if isinstance(payload, dict) else "",
                "sha256": _canonical_hash(payload),
            }
        except Exception as exc:
            out[name] = {"path": str(path), "version": "", "sha256": "", "error": str(exc)}
    return out


def save_shared_account_snapshot(
    result: SharedAccountReplayResult,
    *,
    source_job: dict[str, Any] | None = None,
    output_dir: str | Path = DEFAULT_REPORT_DIR,
) -> Path:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    job_id = str(result.source_job_id or (source_job or {}).get("job_id") or "manual")
    run_dir = root / f"{now.strftime('%Y%m%dT%H%M%SZ')}_{job_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    fingerprints = policy_fingerprints()
    manifest = {
        "version": "28.21",
        "created_at": now.isoformat(),
        "source_job_id": job_id,
        "source_job_created_at": (source_job or {}).get("created_at"),
        "source_symbols": (source_job or {}).get("symbols") or [],
        "source_entry_timeframe": (source_job or {}).get("entry_timeframe"),
        "source_analysis_timeframe": (source_job or {}).get("analysis_timeframe"),
        "source_start_date": (source_job or {}).get("start_date"),
        "source_end_date": (source_job or {}).get("end_date"),
        "source_root": (source_job or {}).get("source_root"),
        "shared_account_policy": result.config,
        "policy_fingerprints": fingerprints,
        "combined_policy_sha256": _canonical_hash(fingerprints),
        "verdict": result.verdict,
        "adaptive_summary": result.adaptive_summary,
        "static_summary": result.static_summary,
        "integrity": result.integrity,
        "critique": result.critique,
        "files": {
            "comparison": "comparison.csv",
            "friction_stress": "friction_stress.csv",
            "adaptive_ledger": "adaptive_ledger.parquet",
            "static_ledger": "static_ledger.parquet",
            "adaptive_equity": "adaptive_equity.csv",
            "static_equity": "static_equity.csv",
            "rejected_candidates": "rejected_candidates.parquet",
            "by_symbol": "by_symbol.csv",
            "by_family": "by_family.csv",
            "by_month": "by_month.csv",
        },
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    csv_frames = {
        "comparison.csv": result.comparison,
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
