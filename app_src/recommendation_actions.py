from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

import pandas as pd

from .backtest_core import apply_override_to_bundle_payload, is_bundle_payload, merge_overrides


DEFAULT_RECOMMENDATION_ACTION_CONFIG: dict[str, Any] = {
    "max_recommendations_per_run": 5,
    "allowed_actions": ["test_score_threshold"],
    "threshold_offsets": [-5, 0, 5],
    "include_segment_baseline": True,
    "include_full_run_baseline": False,
    "min_segment_trades": 20,
    "min_kept_trades": 8,
}


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return default
        text = str(value).strip()
        if text == "" or text.lower() in {"none", "nan", "null", "baseline"}:
            return default
        return float(text)
    except Exception:
        return default


def _safe_text(value: Any, default: str = "unknown") -> str:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    text = str(value).strip()
    return text if text else default


def _action_allowed(action: str, allowed_actions: list[str]) -> bool:
    if not allowed_actions:
        return True
    return any(str(action or "").startswith(prefix) for prefix in allowed_actions)


def _base_threshold(strategy_payload: dict[str, Any]) -> float:
    if is_bundle_payload(strategy_payload):
        components = strategy_payload.get("components") or []
        thresholds = []
        for comp in components:
            thresholds.append(_as_float(comp.get("min_score"), None))
        thresholds = [x for x in thresholds if x is not None]
        return float(min(thresholds) if thresholds else strategy_payload.get("score_threshold") or 70.0)
    return float(_as_float(strategy_payload.get("score_threshold"), None) or _as_float((strategy_payload.get("rule_params") or {}).get("score_threshold"), None) or 70.0)


def _apply_threshold(strategy_payload: dict[str, Any], threshold: float) -> dict[str, Any]:
    threshold = round(float(threshold), 2)
    override = {"score_threshold": threshold, "rule_params": {"score_threshold": threshold}}
    if is_bundle_payload(strategy_payload):
        return apply_override_to_bundle_payload(strategy_payload, override)
    return merge_overrides(strategy_payload, override)


def normalize_recommendation_frame(threshold_df: pd.DataFrame | None) -> pd.DataFrame:
    if threshold_df is None or threshold_df.empty:
        return pd.DataFrame()
    out = threshold_df.copy()
    for col in ["strategy_mode", "trade_owner_key", "side", "regime_group", "regime_detail", "recommended_action"]:
        if col not in out.columns:
            out[col] = "unknown"
        out[col] = out[col].apply(_safe_text)
    for col in ["segment_trades", "kept_trades", "baseline_pnl_usd", "baseline_pf", "threshold_pnl_usd", "threshold_pf", "recommended_threshold"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    if "recommended_threshold" not in out.columns:
        out["recommended_threshold"] = pd.NA
    if "v26_rank_score" not in out.columns:
        base_pnl = pd.to_numeric(out.get("baseline_pnl_usd"), errors="coerce").fillna(0.0)
        thr_pnl = pd.to_numeric(out.get("threshold_pnl_usd"), errors="coerce").fillna(0.0)
        trades = pd.to_numeric(out.get("segment_trades"), errors="coerce").fillna(0.0)
        out["v26_rank_score"] = (thr_pnl - base_pnl) + trades.clip(upper=100) * 0.05
    return out.sort_values(["v26_rank_score", "threshold_pnl_usd", "segment_trades"], ascending=[False, False, False]).reset_index(drop=True)


def build_segment_filter_from_recommendation(row: pd.Series | dict[str, Any]) -> dict[str, Any]:
    getter = row.get if isinstance(row, dict) else row.get
    side = _safe_text(getter("side", "unknown"))
    regime_group = _safe_text(getter("regime_group", "unknown"))
    regime_detail = _safe_text(getter("regime_detail", "unknown"))
    filt: dict[str, Any] = {}
    if side.lower() not in {"", "unknown", "all"}:
        filt["side"] = side
    if regime_group.lower() not in {"", "unknown", "all"}:
        filt["regime_group"] = regime_group
    if regime_detail.lower() not in {"", "unknown", "all"}:
        filt["regime_detail"] = regime_detail
    return filt


def build_recommendation_matrix_config(thresholds: list[float], *, include_baseline: bool = True) -> dict[str, Any]:
    return {
        "include_baseline": bool(include_baseline),
        "stop_multipliers": [],
        "tp_counts": [],
        "score_thresholds": sorted({round(float(x), 2) for x in thresholds if x is not None and 0 <= float(x) <= 100}),
        "include_confirm_bar": False,
        "include_reverse_signal": False,
        "v26_recommendation_action": True,
    }


def build_recommendation_what_if_tasks(
    *,
    saved_run: dict[str, Any],
    threshold_df: pd.DataFrame,
    action_config: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], pd.DataFrame, dict[str, Any]]:
    """Create targeted what-if tasks from V25 threshold recommendations.

    Each task applies a score threshold and a segment_filter so the worker can
    evaluate only the side/regime segment that produced the recommendation.
    """
    cfg = {**DEFAULT_RECOMMENDATION_ACTION_CONFIG, **(action_config or {})}
    recs = normalize_recommendation_frame(threshold_df)
    if recs.empty:
        return [], recs, cfg

    allowed_actions = [str(x) for x in (cfg.get("allowed_actions") or [])]
    max_rows = int(cfg.get("max_recommendations_per_run") or 5)
    min_segment_trades = int(cfg.get("min_segment_trades") or 0)
    min_kept_trades = int(cfg.get("min_kept_trades") or 0)
    recs = recs.loc[recs["recommended_action"].apply(lambda x: _action_allowed(str(x), allowed_actions))].copy()
    if min_segment_trades > 0 and "segment_trades" in recs.columns:
        recs = recs.loc[pd.to_numeric(recs["segment_trades"], errors="coerce").fillna(0) >= min_segment_trades].copy()
    if min_kept_trades > 0 and "kept_trades" in recs.columns:
        recs = recs.loc[pd.to_numeric(recs["kept_trades"], errors="coerce").fillna(0) >= min_kept_trades].copy()
    recs = recs.dropna(subset=["recommended_threshold"]).head(max_rows).copy()

    strategy_payload = deepcopy(saved_run.get("strategy_payload") or {})
    run_name = str(saved_run.get("name") or strategy_payload.get("strategy_name") or "saved_run")
    base_threshold = _base_threshold(strategy_payload)
    offsets = [float(x) for x in (cfg.get("threshold_offsets") or [0])]
    tasks: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for rec_idx, (_, row) in enumerate(recs.iterrows(), start=1):
        recommended = _as_float(row.get("recommended_threshold"), None)
        if recommended is None:
            continue
        segment_filter = build_segment_filter_from_recommendation(row)
        side = _safe_text(row.get("side", "unknown"))
        regime_detail = _safe_text(row.get("regime_detail", "unknown"))
        regime_group = _safe_text(row.get("regime_group", "unknown"))
        thresholds = sorted({max(0.0, min(100.0, round(recommended + off, 2))) for off in offsets})
        if bool(cfg.get("include_segment_baseline", True)):
            thresholds = sorted({base_threshold, *thresholds})
        for threshold in thresholds:
            scenario_name = f"V26 {side} {regime_detail} | score>={threshold:g}"
            segment_key = f"{side}|{regime_group}|{regime_detail}"
            key = (segment_key, f"{threshold:g}", json.dumps(segment_filter, sort_keys=True))
            if key in seen:
                continue
            seen.add(key)
            scenario_payload = _apply_threshold(strategy_payload, threshold)
            tasks.append({
                "name": f"{run_name} | {scenario_name}",
                "scenario_name": scenario_name,
                "strategy_payload": scenario_payload,
                "config_overrides": {
                    "segment_filter": segment_filter,
                    "v26_recommendation_action": True,
                    "v26_source_recommendation": {
                        "rank": rec_idx,
                        "side": side,
                        "regime_group": regime_group,
                        "regime_detail": regime_detail,
                        "recommended_threshold": recommended,
                        "tested_threshold": threshold,
                        "recommended_action": _safe_text(row.get("recommended_action", "unknown")),
                        "segment_trades": int(_as_float(row.get("segment_trades"), 0) or 0),
                        "baseline_pnl_usd": _as_float(row.get("baseline_pnl_usd"), 0.0),
                        "threshold_pnl_usd": _as_float(row.get("threshold_pnl_usd"), 0.0),
                    },
                },
            })
            summary_rows.append({
                "run_name": run_name,
                "rank": rec_idx,
                "side": side,
                "regime_group": regime_group,
                "regime_detail": regime_detail,
                "recommended_threshold": recommended,
                "tested_threshold": threshold,
                "recommended_action": _safe_text(row.get("recommended_action", "unknown")),
                "segment_trades": int(_as_float(row.get("segment_trades"), 0) or 0),
                "kept_trades": int(_as_float(row.get("kept_trades"), 0) or 0),
                "baseline_pnl_usd": _as_float(row.get("baseline_pnl_usd"), 0.0),
                "threshold_pnl_usd": _as_float(row.get("threshold_pnl_usd"), 0.0),
                "segment_filter_json": json.dumps(segment_filter, ensure_ascii=False),
            })

    if bool(cfg.get("include_full_run_baseline", False)):
        tasks.insert(0, {
            "name": f"{run_name} | V26 full-run baseline",
            "scenario_name": "V26 full-run baseline",
            "strategy_payload": strategy_payload,
            "config_overrides": {"v26_recommendation_action": True},
        })

    return tasks, pd.DataFrame(summary_rows), cfg
