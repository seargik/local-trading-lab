from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import pandas as pd

REVIEW_ROOT = Path("data/backtest_reviews")
REVIEW_DECISIONS_PATH = REVIEW_ROOT / "promotion_decisions.csv"
STRATEGY_DRAFTS_DIR = REVIEW_ROOT / "strategy_drafts"

REVIEW_DECISION_OPTIONS = [
    "promote_candidate",
    "watchlist",
    "more_data",
    "repair",
    "reject",
]

REVIEW_LOG_COLUMNS = [
    "decision_id",
    "created_at",
    "decision",
    "reviewer_note",
    "source_run_dir",
    "source_run_name",
    "candidate_run_dir",
    "candidate_run_name",
    "job_id",
    "scenario_name",
    "side",
    "regime_group",
    "regime_detail",
    "recommended_threshold",
    "tested_threshold",
    "source_total_trades",
    "candidate_total_trades",
    "delta_total_trades",
    "source_total_pnl_usd",
    "candidate_total_pnl_usd",
    "delta_total_pnl_usd",
    "source_profit_factor",
    "candidate_profit_factor",
    "delta_profit_factor",
    "source_max_drawdown_usd",
    "candidate_max_drawdown_usd",
    "draft_path",
]


def _now_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


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


def _norm_path(value: Any) -> str:
    text = _safe_text(value)
    if not text:
        return ""
    try:
        return str(Path(text).expanduser())
    except Exception:
        return text


def _slug(text: str, max_len: int = 96) -> str:
    out = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(text))
    out = "_".join([part for part in out.split("_") if part])
    return (out or "draft")[:max_len]


def _load_manifest_only(run_dir: str | Path) -> dict[str, Any]:
    path = Path(run_dir)
    manifest_path = path / "manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["run_dir"] = str(path)
        return manifest
    except Exception:
        return {}


def build_completed_job_result_map(jobs: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """Map saved result run_dir -> V26 job lineage metadata."""
    out: dict[str, dict[str, Any]] = {}
    for job in jobs or []:
        job_type = _safe_text(job.get("job_type"))
        if job_type != "v26_recommendation_what_if":
            continue
        base_run = _norm_path(job.get("base_run"))
        job_id = _safe_text(job.get("job_id"))
        preview = job.get("v26_recommendation_preview") or []
        preview_by_scenario: dict[str, dict[str, Any]] = {}
        if isinstance(preview, list):
            for row in preview:
                if isinstance(row, dict):
                    scenario_name = _safe_text(row.get("scenario_name")) or f"V26 {_safe_text(row.get('side'))} {_safe_text(row.get('regime_detail'))} | score>={_safe_text(row.get('tested_threshold'))}"
                    preview_by_scenario[scenario_name] = row
        for result in job.get("results") or []:
            if not isinstance(result, dict):
                continue
            run_dir = _norm_path(result.get("run_dir"))
            if not run_dir:
                continue
            scenario = _safe_text(result.get("scenario_name") or result.get("strategy_name"))
            row_meta = preview_by_scenario.get(scenario, {})
            out[run_dir] = {
                "job_id": job_id,
                "source_run_dir": base_run,
                "scenario_name": scenario,
                "job_type": job_type,
                "preview": row_meta,
            }
    return out


def _recommendation_meta(manifest: dict[str, Any], job_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = manifest.get("config") or {}
    meta = dict(cfg.get("v26_source_recommendation") or {})
    if job_meta and isinstance(job_meta.get("preview"), dict):
        for key, value in job_meta["preview"].items():
            meta.setdefault(key, value)
    segment = dict(cfg.get("segment_filter") or {})
    meta.setdefault("side", segment.get("side"))
    meta.setdefault("regime_group", segment.get("regime_group"))
    meta.setdefault("regime_detail", segment.get("regime_detail"))
    tested = _safe_float(meta.get("tested_threshold"), None) if meta.get("tested_threshold") is not None else None
    if tested is None:
        strat = manifest.get("strategy_payload") or {}
        tested = _safe_float(strat.get("score_threshold"), _safe_float((strat.get("rule_params") or {}).get("score_threshold"), 0.0))
    meta["tested_threshold"] = tested
    return meta


def _is_v26_candidate(manifest: dict[str, Any], job_meta: dict[str, Any] | None = None) -> bool:
    cfg = manifest.get("config") or {}
    if cfg.get("v26_recommendation_action") or cfg.get("v26_source_recommendation"):
        return True
    if job_meta:
        return True
    name = _safe_text(manifest.get("name")).lower()
    return "v26" in name and "score" in name


def _summary_metric(manifest: dict[str, Any], key: str, default: float = 0.0) -> float:
    return _safe_float((manifest.get("summary") or {}).get(key), default)


def _recommend_review_action(row: dict[str, Any], *, min_candidate_trades: int = 20) -> str:
    cand_trades = _safe_int(row.get("candidate_total_trades"))
    src_trades = _safe_int(row.get("source_total_trades"))
    cand_pnl = _safe_float(row.get("candidate_total_pnl_usd"))
    src_pnl = _safe_float(row.get("source_total_pnl_usd"))
    cand_pf = _safe_float(row.get("candidate_profit_factor"))
    src_pf = _safe_float(row.get("source_profit_factor"))
    rec_action = _safe_text(row.get("recommended_action")).lower()
    if cand_trades <= 0:
        return "repair"
    if cand_trades < min_candidate_trades:
        return "more_data"
    if rec_action.startswith("repair"):
        return "repair"
    if src_trades <= 0:
        return "watchlist"
    if cand_pnl > src_pnl and cand_pf >= max(1.15, src_pf + 0.05):
        return "promote_candidate"
    if cand_pnl < src_pnl and cand_pf <= max(0.0, src_pf - 0.05):
        return "reject"
    return "watchlist"


def build_promotion_candidates(
    loaded_runs: list[tuple[str, pd.DataFrame, dict[str, Any]]],
    *,
    jobs: list[dict[str, Any]] | None = None,
    min_candidate_trades: int = 20,
) -> pd.DataFrame:
    """Build source-run vs V26 candidate rows for manual review."""
    job_map = build_completed_job_result_map(jobs)
    loaded_by_dir: dict[str, dict[str, Any]] = {}
    loaded_by_name: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for run_name, _trades, loaded in loaded_runs:
        manifest = dict(loaded.get("manifest") or {})
        run_dir = _norm_path(loaded.get("run_dir") or manifest.get("run_dir"))
        if run_dir:
            manifest["run_dir"] = run_dir
            loaded_by_dir[run_dir] = manifest
        loaded_by_name[_safe_text(manifest.get("name") or run_name)] = manifest

    for run_name, _trades, loaded in loaded_runs:
        cand = dict(loaded.get("manifest") or {})
        cand_dir = _norm_path(loaded.get("run_dir") or cand.get("run_dir"))
        if not cand_dir:
            continue
        cand["run_dir"] = cand_dir
        job_meta = job_map.get(cand_dir)
        if not _is_v26_candidate(cand, job_meta):
            continue
        source_dir = _norm_path((job_meta or {}).get("source_run_dir") or cand.get("base_run") or (cand.get("config") or {}).get("base_run"))
        source = loaded_by_dir.get(source_dir) if source_dir else None
        if not source and source_dir:
            source = _load_manifest_only(source_dir)
        if not source:
            # Fallback: parse the V26 comment line that contains the source run name.
            comment = _safe_text(cand.get("comment"))
            marker = "V26 recommendation-to-action job from saved run:"
            if marker in comment:
                source_name = comment.split(marker, 1)[1].splitlines()[0].strip()
                source = loaded_by_name.get(source_name)
        source = source or {}
        source_dir = _norm_path(source.get("run_dir") or source_dir)
        meta = _recommendation_meta(cand, job_meta)
        source_summary = source.get("summary") or {}
        cand_summary = cand.get("summary") or {}
        row = {
            "source_run_dir": source_dir,
            "source_run_name": source.get("name") or "",
            "candidate_run_dir": cand_dir,
            "candidate_run_name": cand.get("name") or run_name,
            "job_id": (job_meta or {}).get("job_id") or "",
            "scenario_name": (job_meta or {}).get("scenario_name") or _safe_text(cand.get("name")),
            "side": _safe_text(meta.get("side"), "unknown"),
            "regime_group": _safe_text(meta.get("regime_group"), "unknown"),
            "regime_detail": _safe_text(meta.get("regime_detail"), "unknown"),
            "recommended_action": _safe_text(meta.get("recommended_action"), "unknown"),
            "recommended_threshold": _safe_float(meta.get("recommended_threshold"), 0.0),
            "tested_threshold": _safe_float(meta.get("tested_threshold"), 0.0),
            "source_total_trades": _safe_int(source_summary.get("total_trades")),
            "candidate_total_trades": _safe_int(cand_summary.get("total_trades")),
            "source_total_pnl_usd": _safe_float(source_summary.get("total_pnl_usd")),
            "candidate_total_pnl_usd": _safe_float(cand_summary.get("total_pnl_usd")),
            "source_profit_factor": _safe_float(source_summary.get("profit_factor")),
            "candidate_profit_factor": _safe_float(cand_summary.get("profit_factor")),
            "source_win_rate": _safe_float(source_summary.get("win_rate")),
            "candidate_win_rate": _safe_float(cand_summary.get("win_rate")),
            "source_max_drawdown_usd": _safe_float(source_summary.get("max_drawdown_usd")),
            "candidate_max_drawdown_usd": _safe_float(cand_summary.get("max_drawdown_usd")),
            "source_execution_cost_usd": _safe_float(source_summary.get("total_execution_cost_usd")),
            "candidate_execution_cost_usd": _safe_float(cand_summary.get("total_execution_cost_usd")),
        }
        row["delta_total_trades"] = row["candidate_total_trades"] - row["source_total_trades"]
        row["delta_total_pnl_usd"] = round(row["candidate_total_pnl_usd"] - row["source_total_pnl_usd"], 4)
        row["delta_profit_factor"] = round(row["candidate_profit_factor"] - row["source_profit_factor"], 4)
        row["delta_win_rate"] = round(row["candidate_win_rate"] - row["source_win_rate"], 4)
        row["delta_max_drawdown_usd"] = round(row["candidate_max_drawdown_usd"] - row["source_max_drawdown_usd"], 4)
        row["review_recommendation"] = _recommend_review_action(row, min_candidate_trades=min_candidate_trades)
        rows.append(row)

    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    return out.sort_values(["review_recommendation", "delta_total_pnl_usd", "candidate_profit_factor"], ascending=[True, False, False]).reset_index(drop=True)


def build_strategy_version_draft(source_manifest: dict[str, Any] | None, candidate_manifest: dict[str, Any], review_row: dict[str, Any] | pd.Series | None = None) -> dict[str, Any]:
    source_manifest = source_manifest or {}
    row = review_row.to_dict() if hasattr(review_row, "to_dict") else dict(review_row or {})
    payload = deepcopy(candidate_manifest.get("strategy_payload") or {})
    threshold = _safe_float(row.get("tested_threshold"), _safe_float(payload.get("score_threshold"), 70.0))
    if threshold:
        payload["score_threshold"] = threshold
        params = dict(payload.get("rule_params") or {})
        params["score_threshold"] = threshold
        payload["rule_params"] = params
    source_name = source_manifest.get("name") or row.get("source_run_name") or "source run"
    cand_name = candidate_manifest.get("name") or row.get("candidate_run_name") or "candidate run"
    existing_notes = _safe_text(payload.get("notes"))
    v27_note = (
        "V27 strategy-version draft. "
        f"Source: {source_name}. Candidate: {cand_name}. "
        f"Decision recommendation: {_safe_text(row.get('review_recommendation'), 'watchlist')}. "
        f"Segment: {_safe_text(row.get('side'), 'unknown')} / {_safe_text(row.get('regime_group'), 'unknown')} / {_safe_text(row.get('regime_detail'), 'unknown')}. "
        f"Tested threshold: {threshold:g}. "
        "Manual review required before saving as a new strategy version."
    )
    payload["notes"] = (existing_notes + "\n\n" + v27_note).strip()
    payload["v27_promotion_metadata"] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_run_dir": row.get("source_run_dir"),
        "candidate_run_dir": row.get("candidate_run_dir"),
        "source_run_name": source_name,
        "candidate_run_name": cand_name,
        "review_recommendation": row.get("review_recommendation"),
        "side": row.get("side"),
        "regime_group": row.get("regime_group"),
        "regime_detail": row.get("regime_detail"),
        "recommended_threshold": row.get("recommended_threshold"),
        "tested_threshold": threshold,
        "delta_total_pnl_usd": row.get("delta_total_pnl_usd"),
        "delta_profit_factor": row.get("delta_profit_factor"),
    }
    return payload


def save_strategy_draft_file(draft_payload: dict[str, Any], *, label: str = "v27_strategy_draft") -> Path:
    STRATEGY_DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    target = STRATEGY_DRAFTS_DIR / f"{_now_id()}_{_slug(label)}.json"
    target.write_text(json.dumps(draft_payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return target


def load_review_decisions(path: Path = REVIEW_DECISIONS_PATH) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=REVIEW_LOG_COLUMNS)
    try:
        df = pd.read_csv(path)
    except Exception:
        return pd.DataFrame(columns=REVIEW_LOG_COLUMNS)
    for col in REVIEW_LOG_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    return df[REVIEW_LOG_COLUMNS + [c for c in df.columns if c not in REVIEW_LOG_COLUMNS]]


def append_review_decision(review_row: dict[str, Any] | pd.Series, *, decision: str, reviewer_note: str = "", draft_path: str = "", path: Path = REVIEW_DECISIONS_PATH) -> Path:
    REVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    row = review_row.to_dict() if hasattr(review_row, "to_dict") else dict(review_row or {})
    record = {
        "decision_id": _now_id(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "decision": decision,
        "reviewer_note": reviewer_note,
        "draft_path": draft_path,
    }
    for col in REVIEW_LOG_COLUMNS:
        record.setdefault(col, row.get(col, ""))
    record["decision"] = decision
    record["reviewer_note"] = reviewer_note
    record["draft_path"] = draft_path
    existing = load_review_decisions(path)
    out = pd.concat([existing, pd.DataFrame([record])], ignore_index=True)
    out.to_csv(path, index=False)
    return path
