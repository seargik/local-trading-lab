from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
from typing import Any

import pandas as pd

from .adaptive_evidence_v2820 import AdaptiveEvidenceResult

DEFAULT_REPORT_DIR = Path("data/backtest_reviews/adaptive_evidence")
POLICY_FILES = {
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


def save_adaptive_evidence_snapshot(
    result: AdaptiveEvidenceResult,
    *,
    source_job: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
    output_dir: str | Path = DEFAULT_REPORT_DIR,
) -> Path:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    job_id = str(result.job_id or (source_job or {}).get("job_id") or "manual")
    run_dir = root / f"{now.strftime('%Y%m%dT%H%M%SZ')}_{job_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    fingerprints = policy_fingerprints()
    manifest = {
        "version": "28.20",
        "created_at": now.isoformat(),
        "source_job_id": job_id,
        "source_job_created_at": (source_job or {}).get("created_at"),
        "source_symbols": (source_job or {}).get("symbols") or [],
        "source_entry_timeframe": (source_job or {}).get("entry_timeframe"),
        "source_analysis_timeframe": (source_job or {}).get("analysis_timeframe"),
        "source_start_date": (source_job or {}).get("start_date"),
        "source_end_date": (source_job or {}).get("end_date"),
        "source_root": (source_job or {}).get("source_root"),
        "policy": policy or {},
        "policy_fingerprints": fingerprints,
        "combined_policy_sha256": _canonical_hash(fingerprints),
        "verdict": result.verdict,
        "concurrency": result.concurrency,
        "integrity": result.integrity,
        "critique": result.critique,
        "files": {
            "family_comparison": "family_comparison.csv",
            "pooled_comparison": "pooled_comparison.csv",
            "wait_analysis": "wait_analysis.csv",
            "friction_stress": "friction_stress.csv",
            "stability_by_symbol": "stability_by_symbol.csv",
            "stability_by_month": "stability_by_month.csv",
            "annotated_trades": "annotated_trades.parquet",
        },
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    frames = {
        "family_comparison.csv": result.family_comparison,
        "pooled_comparison.csv": result.pooled_comparison,
        "wait_analysis.csv": result.wait_analysis,
        "friction_stress.csv": result.friction_stress,
        "stability_by_symbol.csv": result.stability_by_symbol,
        "stability_by_month.csv": result.stability_by_month,
    }
    for filename, frame in frames.items():
        (frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()).to_csv(run_dir / filename, index=False)
    (result.annotated_trades if isinstance(result.annotated_trades, pd.DataFrame) else pd.DataFrame()).to_parquet(run_dir / "annotated_trades.parquet", index=False)
    return run_dir
