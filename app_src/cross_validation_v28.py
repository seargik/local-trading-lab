from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import pandas as pd

from .backtest_jobs import create_task_job
from .settings import OHLCV_STORE_ROOT

REVIEW_ROOT = Path("data/backtest_reviews")
STRATEGY_DRAFTS_DIR = REVIEW_ROOT / "strategy_drafts"
CV_REVIEW_DECISIONS_PATH = REVIEW_ROOT / "cross_validation_decisions.csv"

CV_DECISION_OPTIONS = [
    "promote_after_cv",
    "watchlist",
    "more_folds",
    "repair",
    "reject",
]

CV_REVIEW_COLUMNS = [
    "decision_id",
    "created_at",
    "cv_id",
    "decision",
    "reviewer_note",
    "draft_path",
    "source_run_dir",
    "source_run_name",
    "candidate_name",
    "folds",
    "symbols",
    "candidate_wins",
    "tested_pairs",
    "cv_win_rate_pct",
    "sum_delta_pnl_usd",
    "avg_delta_profit_factor",
    "min_candidate_trades",
    "promotion_confidence",
    "v28_recommendation",
    "saved_version_id",
]


def _now_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def _safe_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    text = str(value).strip()
    return text if text else default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, float) and pd.isna(value):
            return default
        text = str(value).strip()
        if text == "" or text.lower() in {"nan", "none", "null"}:
            return default
        return float(text)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(round(_safe_float(value, float(default))))
    except Exception:
        return default


def _slug(text: str, max_len: int = 96) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(text or ""))
    cleaned = "_".join([part for part in cleaned.split("_") if part])
    return (cleaned or "item")[:max_len]


def _read_json(path: str | Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def load_strategy_drafts(drafts_dir: str | Path = STRATEGY_DRAFTS_DIR) -> pd.DataFrame:
    """Return reviewable strategy-draft JSON files produced by V27/V28."""
    root = Path(drafts_dir)
    rows: list[dict[str, Any]] = []
    if not root.exists():
        return pd.DataFrame(columns=["draft_path", "draft_file", "strategy_name", "score_threshold", "created_at", "source_run_name", "candidate_run_name", "review_recommendation"])
    for path in sorted(root.glob("*.json"), reverse=True):
        payload = _read_json(path, {}) or {}
        meta = payload.get("v27_promotion_metadata") or payload.get("v28_cv_metadata") or {}
        rows.append({
            "draft_path": str(path),
            "draft_file": path.name,
            "strategy_name": payload.get("strategy_name") or path.stem,
            "template_key": payload.get("template_key") or "rule_builder",
            "score_threshold": _safe_float(payload.get("score_threshold"), _safe_float((payload.get("rule_params") or {}).get("score_threshold"), 0.0)),
            "created_at": meta.get("created_at") or path.stat().st_mtime,
            "source_run_name": meta.get("source_run_name") or "",
            "candidate_run_name": meta.get("candidate_run_name") or "",
            "review_recommendation": meta.get("review_recommendation") or "",
            "side": meta.get("side") or "",
            "regime_group": meta.get("regime_group") or "",
            "regime_detail": meta.get("regime_detail") or "",
        })
    return pd.DataFrame(rows)


def load_strategy_draft(path: str | Path) -> dict[str, Any]:
    payload = _read_json(path, {}) or {}
    if not isinstance(payload, dict):
        return {}
    return payload


def build_date_folds(start_date: Any, end_date: Any, *, fold_count: int = 3, min_days: int = 7) -> list[dict[str, str]]:
    """Split a source run period into chronological folds for candidate validation."""
    start = pd.to_datetime(start_date, utc=True, errors="coerce")
    end = pd.to_datetime(end_date, utc=True, errors="coerce")
    if pd.isna(start) or pd.isna(end) or end <= start:
        return []
    total_days = max(1, int((end.normalize() - start.normalize()).days) + 1)
    fold_count = max(1, min(int(fold_count or 1), max(1, total_days // max(1, int(min_days or 1)))))
    # If range is short, still create one fold rather than failing.
    fold_count = max(1, fold_count)
    boundaries = pd.date_range(start=start.normalize(), end=end.normalize() + pd.Timedelta(days=1), periods=fold_count + 1)
    folds: list[dict[str, str]] = []
    for idx in range(fold_count):
        f_start = boundaries[idx]
        f_end = boundaries[idx + 1] - pd.Timedelta(seconds=1)
        if f_end <= f_start:
            continue
        folds.append({
            "fold_id": f"fold_{idx + 1}",
            "start_date": str(f_start.date()),
            "end_date": str(f_end.date()),
        })
    return folds


def _summary_from_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    summary = manifest.get("summary") or {}
    return {
        "total_trades": _safe_int(summary.get("total_trades")),
        "total_pnl_usd": _safe_float(summary.get("total_pnl_usd")),
        "profit_factor": _safe_float(summary.get("profit_factor")),
        "win_rate": _safe_float(summary.get("win_rate")),
        "max_drawdown_usd": _safe_float(summary.get("max_drawdown_usd")),
        "total_execution_cost_usd": _safe_float(summary.get("total_execution_cost_usd")),
    }


def _source_base_config(source_manifest: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(source_manifest.get("config") or {})
    # Keep runtime realism, exit, friction and analysis config, but remove run identity fields.
    for key in ["symbols", "start_date", "end_date", "source_root", "entry_timeframe", "analysis_timeframe", "what_if_config", "base_run"]:
        cfg.pop(key, None)
    return cfg


def queue_cross_validation_jobs(
    *,
    source_manifest: dict[str, Any],
    candidate_payload: dict[str, Any],
    draft_path: str = "",
    symbols: list[str] | None = None,
    fold_count: int = 3,
    min_days_per_fold: int = 7,
    include_source_baseline: bool = True,
    max_symbols: int = 8,
    comment: str = "",
) -> tuple[int, int, pd.DataFrame, str]:
    """Queue V28 CV jobs. One job per date fold; tasks compare source vs candidate per symbol."""
    source_payload = dict(source_manifest.get("strategy_payload") or {})
    if not source_payload or not candidate_payload:
        return 0, 0, pd.DataFrame(), ""
    cfg = dict(source_manifest.get("config") or {})
    source_symbols = symbols or list(cfg.get("symbols") or [])
    source_symbols = [str(s).upper().strip() for s in source_symbols if str(s).strip()]
    source_symbols = source_symbols[: max(1, int(max_symbols or len(source_symbols) or 1))]
    if not source_symbols:
        return 0, 0, pd.DataFrame(), ""
    folds = build_date_folds(cfg.get("start_date"), cfg.get("end_date"), fold_count=fold_count, min_days=min_days_per_fold)
    if not folds:
        return 0, 0, pd.DataFrame(), ""
    cv_id = f"v28cv_{_now_id()}"
    base_config = _source_base_config(source_manifest)
    source_run_dir = str(source_manifest.get("run_dir") or "")
    source_name = str(source_manifest.get("name") or "source")
    candidate_name = str(candidate_payload.get("strategy_name") or Path(draft_path).stem or "candidate")
    preview_rows: list[dict[str, Any]] = []
    jobs_created = 0
    tasks_created = 0
    for fold in folds:
        tasks: list[dict[str, Any]] = []
        for symbol in source_symbols:
            if include_source_baseline:
                src_meta = {
                    "cv_id": cv_id,
                    "role": "source",
                    "fold_id": fold["fold_id"],
                    "symbol": symbol,
                    "draft_path": draft_path,
                    "source_run_dir": source_run_dir,
                    "source_run_name": source_name,
                    "candidate_name": candidate_name,
                }
                tasks.append({
                    "name": f"V28 CV | {fold['fold_id']} | {symbol} | SOURCE | {source_name}",
                    "scenario_name": f"V28 CV {fold['fold_id']} {symbol} SOURCE",
                    "strategy_payload": source_payload,
                    "symbols": [symbol],
                    "task_meta": {"v28_cv": src_meta},
                    "config_overrides": {"v28_cv": src_meta, "run_kind": "v28_cross_validation"},
                })
            cand_meta = {
                "cv_id": cv_id,
                "role": "candidate",
                "fold_id": fold["fold_id"],
                "symbol": symbol,
                "draft_path": draft_path,
                "source_run_dir": source_run_dir,
                "source_run_name": source_name,
                "candidate_name": candidate_name,
            }
            tasks.append({
                "name": f"V28 CV | {fold['fold_id']} | {symbol} | CANDIDATE | {candidate_name}",
                "scenario_name": f"V28 CV {fold['fold_id']} {symbol} CANDIDATE",
                "strategy_payload": candidate_payload,
                "symbols": [symbol],
                "task_meta": {"v28_cv": cand_meta},
                "config_overrides": {"v28_cv": cand_meta, "run_kind": "v28_cross_validation"},
            })
        if not tasks:
            continue
        create_task_job(
            source_root=str(cfg.get("source_root") or OHLCV_STORE_ROOT),
            symbols=source_symbols,
            entry_timeframe=str(cfg.get("entry_timeframe") or "5m"),
            analysis_timeframe=str(cfg.get("analysis_timeframe") or "1h"),
            start_date=fold["start_date"],
            end_date=fold["end_date"],
            base_config=base_config,
            tasks=tasks,
            comment=(comment or f"V28 cross-validation for {candidate_name} against {source_name}"),
            job_type="v28_cross_validation",
            extra={
                "cv_id": cv_id,
                "draft_path": draft_path,
                "source_run_dir": source_run_dir,
                "source_run_name": source_name,
                "candidate_name": candidate_name,
                "fold": fold,
                "fold_count": len(folds),
            },
        )
        jobs_created += 1
        tasks_created += len(tasks)
        for task in tasks:
            meta = (task.get("task_meta") or {}).get("v28_cv") or {}
            preview_rows.append({
                **meta,
                "start_date": fold["start_date"],
                "end_date": fold["end_date"],
                "scenario_name": task.get("scenario_name"),
            })
    return jobs_created, tasks_created, pd.DataFrame(preview_rows), cv_id


def _manifest_from_run_dir(run_dir: str | Path) -> dict[str, Any]:
    path = Path(run_dir) / "manifest.json"
    manifest = _read_json(path, {}) or {}
    if manifest:
        manifest["run_dir"] = str(Path(run_dir))
    return manifest


def build_cv_result_rows(jobs: list[dict[str, Any]] | None) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for job in jobs or []:
        if _safe_text(job.get("job_type")) != "v28_cross_validation":
            continue
        for result in job.get("results") or []:
            if not isinstance(result, dict):
                continue
            run_dir = result.get("run_dir")
            manifest = _manifest_from_run_dir(run_dir) if run_dir else {}
            cfg = manifest.get("config") or {}
            meta = dict(cfg.get("v28_cv") or result.get("task_meta", {}).get("v28_cv") or {})
            if not meta:
                # Fallback parse: V28 CV fold_1 ETHUSDT CANDIDATE
                scenario = _safe_text(result.get("scenario_name"))
                parts = scenario.split()
                if len(parts) >= 5 and parts[0] == "V28" and parts[1] == "CV":
                    meta = {"fold_id": parts[2], "symbol": parts[3], "role": parts[4].lower()}
            if not meta:
                continue
            summary = _summary_from_manifest(manifest or {"summary": result.get("summary") or {}})
            rows.append({
                "cv_id": meta.get("cv_id") or job.get("cv_id") or "unknown_cv",
                "job_id": job.get("job_id"),
                "fold_id": meta.get("fold_id") or (job.get("fold") or {}).get("fold_id") or "unknown_fold",
                "symbol": meta.get("symbol") or result.get("symbol") or "unknown",
                "role": _safe_text(meta.get("role"), "unknown"),
                "run_dir": str(run_dir or ""),
                "run_name": manifest.get("name") or result.get("scenario_name") or "",
                "scenario_name": result.get("scenario_name") or "",
                "source_run_dir": meta.get("source_run_dir") or job.get("source_run_dir") or "",
                "source_run_name": meta.get("source_run_name") or job.get("source_run_name") or "",
                "candidate_name": meta.get("candidate_name") or job.get("candidate_name") or "",
                "draft_path": meta.get("draft_path") or job.get("draft_path") or "",
                "start_date": cfg.get("start_date") or (job.get("fold") or {}).get("start_date"),
                "end_date": cfg.get("end_date") or (job.get("fold") or {}).get("end_date"),
                **summary,
            })
    return pd.DataFrame(rows)


def build_cv_pair_summary(rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if rows is None or rows.empty:
        return pd.DataFrame(), pd.DataFrame()
    work = rows.copy()
    for col in ["cv_id", "fold_id", "symbol", "role"]:
        if col not in work.columns:
            work[col] = "unknown"
        work[col] = work[col].fillna("unknown").astype(str)
    metric_cols = ["total_trades", "total_pnl_usd", "profit_factor", "win_rate", "max_drawdown_usd", "total_execution_cost_usd"]
    for col in metric_cols:
        work[col] = pd.to_numeric(work.get(col), errors="coerce").fillna(0.0)
    pairs: list[dict[str, Any]] = []
    for keys, part in work.groupby(["cv_id", "fold_id", "symbol"], dropna=False):
        cv_id, fold_id, symbol = keys
        source = part.loc[part["role"].str.lower() == "source"].head(1)
        candidate = part.loc[part["role"].str.lower() == "candidate"].head(1)
        if source.empty or candidate.empty:
            continue
        s = source.iloc[0].to_dict()
        c = candidate.iloc[0].to_dict()
        row = {
            "cv_id": cv_id,
            "fold_id": fold_id,
            "symbol": symbol,
            "source_run_dir": s.get("source_run_dir") or c.get("source_run_dir"),
            "source_run_name": s.get("source_run_name") or c.get("source_run_name"),
            "candidate_name": c.get("candidate_name") or c.get("run_name"),
            "draft_path": c.get("draft_path") or s.get("draft_path"),
            "start_date": c.get("start_date") or s.get("start_date"),
            "end_date": c.get("end_date") or s.get("end_date"),
            "source_run_dir_result": s.get("run_dir"),
            "candidate_run_dir_result": c.get("run_dir"),
            "source_total_trades": int(s.get("total_trades") or 0),
            "candidate_total_trades": int(c.get("total_trades") or 0),
            "source_total_pnl_usd": round(float(s.get("total_pnl_usd") or 0.0), 4),
            "candidate_total_pnl_usd": round(float(c.get("total_pnl_usd") or 0.0), 4),
            "source_profit_factor": round(float(s.get("profit_factor") or 0.0), 4),
            "candidate_profit_factor": round(float(c.get("profit_factor") or 0.0), 4),
            "source_win_rate": round(float(s.get("win_rate") or 0.0), 2),
            "candidate_win_rate": round(float(c.get("win_rate") or 0.0), 2),
            "source_max_drawdown_usd": round(float(s.get("max_drawdown_usd") or 0.0), 4),
            "candidate_max_drawdown_usd": round(float(c.get("max_drawdown_usd") or 0.0), 4),
        }
        row["delta_total_trades"] = row["candidate_total_trades"] - row["source_total_trades"]
        row["delta_total_pnl_usd"] = round(row["candidate_total_pnl_usd"] - row["source_total_pnl_usd"], 4)
        row["delta_profit_factor"] = round(row["candidate_profit_factor"] - row["source_profit_factor"], 4)
        row["delta_win_rate"] = round(row["candidate_win_rate"] - row["source_win_rate"], 4)
        row["candidate_won_pair"] = bool(row["delta_total_pnl_usd"] > 0 and row["delta_profit_factor"] >= -0.05)
        pairs.append(row)
    pair_df = pd.DataFrame(pairs)
    if pair_df.empty:
        return pair_df, pd.DataFrame()
    agg_rows: list[dict[str, Any]] = []
    for cv_id, part in pair_df.groupby("cv_id", dropna=False):
        tested_pairs = int(len(part))
        candidate_wins = int(part["candidate_won_pair"].sum())
        win_rate = (candidate_wins / tested_pairs * 100.0) if tested_pairs else 0.0
        min_trades = int(pd.to_numeric(part["candidate_total_trades"], errors="coerce").fillna(0).min()) if tested_pairs else 0
        sum_delta_pnl = float(pd.to_numeric(part["delta_total_pnl_usd"], errors="coerce").fillna(0).sum())
        avg_delta_pf = float(pd.to_numeric(part["delta_profit_factor"], errors="coerce").fillna(0).mean()) if tested_pairs else 0.0
        folds = ",".join(sorted(part["fold_id"].astype(str).unique()))
        symbols = ",".join(sorted(part["symbol"].astype(str).unique()))
        confidence = "low"
        recommendation = "more_folds"
        if tested_pairs >= 6 and win_rate >= 65 and sum_delta_pnl > 0 and avg_delta_pf >= 0.03 and min_trades >= 3:
            confidence = "high"
            recommendation = "promote_after_cv"
        elif tested_pairs >= 4 and win_rate >= 55 and sum_delta_pnl > 0:
            confidence = "medium"
            recommendation = "watchlist"
        elif tested_pairs >= 4 and win_rate < 35 and sum_delta_pnl < 0:
            confidence = "medium"
            recommendation = "reject"
        elif min_trades <= 1:
            recommendation = "more_folds"
        agg_rows.append({
            "cv_id": cv_id,
            "source_run_dir": part["source_run_dir"].iloc[0],
            "source_run_name": part["source_run_name"].iloc[0],
            "candidate_name": part["candidate_name"].iloc[0],
            "draft_path": part["draft_path"].iloc[0],
            "folds": folds,
            "symbols": symbols,
            "tested_pairs": tested_pairs,
            "candidate_wins": candidate_wins,
            "cv_win_rate_pct": round(win_rate, 2),
            "sum_delta_pnl_usd": round(sum_delta_pnl, 4),
            "avg_delta_profit_factor": round(avg_delta_pf, 4),
            "min_candidate_trades": min_trades,
            "promotion_confidence": confidence,
            "v28_recommendation": recommendation,
        })
    agg_df = pd.DataFrame(agg_rows).sort_values(["promotion_confidence", "sum_delta_pnl_usd", "cv_win_rate_pct"], ascending=[True, False, False]).reset_index(drop=True)
    return pair_df.sort_values(["cv_id", "fold_id", "symbol"]).reset_index(drop=True), agg_df


def build_cv_reports(jobs: list[dict[str, Any]] | None) -> dict[str, pd.DataFrame]:
    rows = build_cv_result_rows(jobs)
    pairs, aggregate = build_cv_pair_summary(rows)
    return {"cv_results": rows, "cv_pairs": pairs, "cv_aggregate": aggregate}


def load_cv_review_decisions(path: str | Path = CV_REVIEW_DECISIONS_PATH) -> pd.DataFrame:
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=CV_REVIEW_COLUMNS)
    try:
        df = pd.read_csv(path)
    except Exception:
        return pd.DataFrame(columns=CV_REVIEW_COLUMNS)
    for col in CV_REVIEW_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    return df[CV_REVIEW_COLUMNS + [c for c in df.columns if c not in CV_REVIEW_COLUMNS]]


def append_cv_review_decision(row: dict[str, Any] | pd.Series, *, decision: str, reviewer_note: str = "", saved_version_id: str = "", path: str | Path = CV_REVIEW_DECISIONS_PATH) -> Path:
    REVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    data = row.to_dict() if hasattr(row, "to_dict") else dict(row or {})
    record = {col: data.get(col, "") for col in CV_REVIEW_COLUMNS}
    record.update({
        "decision_id": _now_id(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "decision": decision,
        "reviewer_note": reviewer_note,
        "saved_version_id": saved_version_id,
    })
    existing = load_cv_review_decisions(path)
    out = pd.concat([existing, pd.DataFrame([record])], ignore_index=True)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(target, index=False)
    return target


def save_draft_as_strategy_version(storage: Any, draft_payload: dict[str, Any], *, strategy_id: int, cv_row: dict[str, Any] | pd.Series | None = None) -> int:
    """Manual-only helper: save a draft as a new version under an existing strategy."""
    payload = dict(draft_payload or {})
    row = cv_row.to_dict() if hasattr(cv_row, "to_dict") else dict(cv_row or {})
    notes = _safe_text(payload.get("notes"))
    cv_note = (
        "V28 manually saved from cross-validation review. "
        f"CV id: {_safe_text(row.get('cv_id'), 'unknown')}. "
        f"Decision: {_safe_text(row.get('v28_recommendation'), 'manual')}. "
        f"Pairs: {_safe_text(row.get('tested_pairs'), '0')}; wins: {_safe_text(row.get('candidate_wins'), '0')}; "
        f"CV win rate: {_safe_text(row.get('cv_win_rate_pct'), '0')}%. "
        "This did not auto-update live slots. Assign it manually if desired."
    )
    payload["notes"] = (notes + "\n\n" + cv_note).strip()
    payload["v28_cv_metadata"] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "cv_id": row.get("cv_id"),
        "source_run_dir": row.get("source_run_dir"),
        "source_run_name": row.get("source_run_name"),
        "candidate_name": row.get("candidate_name"),
        "draft_path": row.get("draft_path"),
        "tested_pairs": row.get("tested_pairs"),
        "candidate_wins": row.get("candidate_wins"),
        "cv_win_rate_pct": row.get("cv_win_rate_pct"),
        "sum_delta_pnl_usd": row.get("sum_delta_pnl_usd"),
        "avg_delta_profit_factor": row.get("avg_delta_profit_factor"),
        "promotion_confidence": row.get("promotion_confidence"),
        "v28_recommendation": row.get("v28_recommendation"),
    }
    return int(storage.save_strategy_version(int(strategy_id), payload))
