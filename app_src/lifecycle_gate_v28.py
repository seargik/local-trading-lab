from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .trend_lifecycle import classify_trend_lifecycle, evaluate_strategy_lifecycle_fit


DEFAULT_GATE_PENALTIES = {
    "fit": 0.0,
    "caution": 5.0,
    "unknown": 10.0,
    "blocked": 20.0,
    "direction_conflict": 25.0,
}


@dataclass
class LifecycleGateLabResult:
    enriched_trades: pd.DataFrame
    comparison: pd.DataFrame
    performance_by_fit_status: pd.DataFrame
    performance_by_lifecycle_state: pd.DataFrame
    recommendation: dict[str, Any]


def _safe_json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return fallback
    text = str(value).strip()
    if not text:
        return fallback
    try:
        return json.loads(text)
    except Exception:
        return fallback


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _strategy_threshold(strategy_payload: dict[str, Any] | None, fallback: float = 70.0) -> float:
    payload = strategy_payload or {}
    if payload.get("score_threshold") is not None:
        return _num(payload.get("score_threshold"), fallback)
    return _num((payload.get("rule_params") or {}).get("score_threshold"), fallback)


def enrich_trades_with_lifecycle(
    trades: pd.DataFrame,
    *,
    strategy_payload: dict[str, Any] | None = None,
    analysis_tf: str | None = None,
) -> pd.DataFrame:
    """Reconstruct lifecycle state/fit from the feature snapshots already saved with each trade.

    This is intentionally post-trade counterfactual analysis. It does not change the original
    signal path or create replacement trades that might have appeared if a blocked trade had
    never occupied a one-trade-at-a-time slot.
    """
    if trades is None or trades.empty:
        return pd.DataFrame()

    payload = dict(strategy_payload or {})
    default_template = payload.get("template_key") or ("bundle" if payload.get("strategy_type") == "bundle" else "")
    rows: list[dict[str, Any]] = []

    for _, source_row in trades.iterrows():
        row = source_row.to_dict()
        features = dict(_safe_json(row.get("feature_snapshot_json"), {}))
        htf_context = dict(_safe_json(row.get("htf_context_json"), {}))

        # V25 detailed-regime scores are stored as trade columns in existing backtests.
        # Put them back into the feature payload so lifecycle classification sees the same
        # regime evidence that was present during backtest evaluation.
        for key in [
            "trend_regime_score",
            "range_regime_score",
            "squeeze_regime_score",
            "panic_regime_score",
        ]:
            if row.get(key) is not None and not pd.isna(row.get(key)):
                features[key] = row.get(key)

        symbol = str(row.get("symbol") or "")
        tf = str(analysis_tf or row.get("analysis_timeframe") or "")
        lifecycle = classify_trend_lifecycle(features, htf_context, symbol=symbol, analysis_tf=tf)
        opinion = {
            "strategy_name": row.get("strategy_name") or payload.get("strategy_name"),
            "bundle_name": payload.get("bundle_name"),
            "template_key": default_template,
            "strategy_mode": row.get("strategy_mode") or ("bundle" if payload.get("strategy_type") == "bundle" else "single"),
            "trade_owner_key": row.get("trade_owner_key"),
            "bias": row.get("side"),
            "score": row.get("score"),
        }
        fit = evaluate_strategy_lifecycle_fit(opinion, lifecycle)
        enriched = {
            **row,
            **fit,
            "lifecycle_reason": " | ".join(lifecycle.reason or []),
            "lifecycle_allowed_families": ", ".join(lifecycle.allowed_strategy_families or []),
            "lifecycle_blocked_families": ", ".join(lifecycle.blocked_strategy_families or []),
        }
        rows.append(enriched)

    out = pd.DataFrame(rows)
    if "entry_time" in out.columns:
        out["entry_time"] = pd.to_datetime(out["entry_time"], utc=True, errors="coerce")
    if "exit_time" in out.columns:
        out["exit_time"] = pd.to_datetime(out["exit_time"], utc=True, errors="coerce")
    return out


def _metric_summary(df: pd.DataFrame, fixed_stake_usd: float) -> dict[str, Any]:
    if df is None or df.empty:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "total_pnl_usd": 0.0,
            "total_pnl_pct": 0.0,
            "avg_trade_pct": 0.0,
            "expectancy_r": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_usd": 0.0,
            "avg_score": 0.0,
            "total_execution_cost_usd": 0.0,
        }

    work = df.copy()
    pnl_pct = pd.to_numeric(work.get("pnl_pct", 0.0), errors="coerce").fillna(0.0)
    pnl_usd = fixed_stake_usd * pnl_pct / 100.0
    gross_profit = float(pnl_usd[pnl_usd > 0].sum())
    gross_loss = abs(float(pnl_usd[pnl_usd < 0].sum()))
    equity = pnl_usd.cumsum()
    drawdown = equity - equity.cummax()

    if "risk_pct" in work.columns:
        risk = pd.to_numeric(work["risk_pct"], errors="coerce").replace(0, np.nan).abs()
        r_mult = pnl_pct / risk
        expectancy_r = float(r_mult.replace([np.inf, -np.inf], np.nan).fillna(0.0).mean())
    else:
        expectancy_r = 0.0

    if "execution_cost_pct" in work.columns:
        execution_cost_pct = pd.to_numeric(work["execution_cost_pct"], errors="coerce").fillna(0.0)
        total_execution_cost_usd = float((fixed_stake_usd * execution_cost_pct / 100.0).sum())
    else:
        total_execution_cost_usd = 0.0

    score = pd.to_numeric(work.get("score", pd.Series(index=work.index, dtype=float)), errors="coerce")
    return {
        "trades": int(len(work)),
        "win_rate": round(float((pnl_pct > 0.0000001).mean() * 100.0), 2),
        "total_pnl_usd": round(float(pnl_usd.sum()), 2),
        "total_pnl_pct": round(float(pnl_pct.sum()), 4),
        "avg_trade_pct": round(float(pnl_pct.mean()), 4),
        "expectancy_r": round(expectancy_r, 4),
        "profit_factor": round(float(gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0), 4),
        "max_drawdown_usd": round(float(abs(drawdown.min())) if not drawdown.empty else 0.0, 2),
        "avg_score": round(float(score.mean()) if score.notna().any() else 0.0, 2),
        "total_execution_cost_usd": round(total_execution_cost_usd, 2),
    }


def _bootstrap_kept_vs_removed(
    kept: pd.DataFrame,
    removed: pd.DataFrame,
    *,
    iterations: int = 1200,
    seed: int = 28,
) -> dict[str, Any]:
    if kept is None or removed is None or len(kept) < 10 or len(removed) < 10:
        return {
            "kept_vs_removed_avg_pnl_delta_pct": np.nan,
            "separation_ci_low_pct": np.nan,
            "separation_ci_high_pct": np.nan,
            "separation_prob_positive": np.nan,
        }
    kept_vals = pd.to_numeric(kept.get("pnl_pct", 0.0), errors="coerce").dropna().to_numpy(dtype=float)
    removed_vals = pd.to_numeric(removed.get("pnl_pct", 0.0), errors="coerce").dropna().to_numpy(dtype=float)
    if len(kept_vals) < 10 or len(removed_vals) < 10:
        return {
            "kept_vs_removed_avg_pnl_delta_pct": np.nan,
            "separation_ci_low_pct": np.nan,
            "separation_ci_high_pct": np.nan,
            "separation_prob_positive": np.nan,
        }
    rng = np.random.default_rng(seed)
    diffs = np.empty(iterations, dtype=float)
    for i in range(iterations):
        kept_sample = rng.choice(kept_vals, size=len(kept_vals), replace=True)
        removed_sample = rng.choice(removed_vals, size=len(removed_vals), replace=True)
        diffs[i] = float(kept_sample.mean() - removed_sample.mean())
    observed = float(kept_vals.mean() - removed_vals.mean())
    return {
        "kept_vs_removed_avg_pnl_delta_pct": round(observed, 4),
        "separation_ci_low_pct": round(float(np.quantile(diffs, 0.025)), 4),
        "separation_ci_high_pct": round(float(np.quantile(diffs, 0.975)), 4),
        "separation_prob_positive": round(float((diffs > 0).mean()), 4),
    }


def _scenario_masks(
    enriched: pd.DataFrame,
    *,
    score_threshold: float,
    penalties: dict[str, float],
    confidence_floor: float,
) -> dict[str, pd.Series]:
    index = enriched.index
    fit_status = enriched.get("fit_status", pd.Series("unknown", index=index)).fillna("unknown").astype(str)
    allowed = enriched.get("allowed_by_lifecycle", pd.Series(False, index=index)).fillna(False).astype(bool)
    confidence = pd.to_numeric(enriched.get("lifecycle_confidence", 0.0), errors="coerce").fillna(0.0)
    score = pd.to_numeric(enriched.get("score", 0.0), errors="coerce").fillna(0.0)
    penalty = fit_status.map(lambda status: float(penalties.get(status, penalties.get("unknown", 10.0))))
    adjusted_score = score - penalty
    return {
        "Baseline": pd.Series(True, index=index),
        "Fit only": allowed,
        "Fit only + confidence floor": allowed & (confidence >= float(confidence_floor)),
        "Block direction conflicts": fit_status.ne("direction_conflict"),
        "Block blocked + conflicts": ~fit_status.isin(["blocked", "direction_conflict"]),
        "Soft lifecycle score penalty": adjusted_score >= float(score_threshold),
    }


def build_lifecycle_gate_comparison(
    enriched_trades: pd.DataFrame,
    *,
    fixed_stake_usd: float = 100.0,
    score_threshold: float = 70.0,
    penalties: dict[str, float] | None = None,
    confidence_floor: float = 55.0,
) -> pd.DataFrame:
    if enriched_trades is None or enriched_trades.empty:
        return pd.DataFrame()
    penalties = {**DEFAULT_GATE_PENALTIES, **dict(penalties or {})}
    baseline_metrics = _metric_summary(enriched_trades, fixed_stake_usd)
    baseline_trades = max(1, int(baseline_metrics["trades"]))
    rows: list[dict[str, Any]] = []
    masks = _scenario_masks(
        enriched_trades,
        score_threshold=score_threshold,
        penalties=penalties,
        confidence_floor=confidence_floor,
    )
    for scenario, mask in masks.items():
        kept = enriched_trades.loc[mask].copy()
        removed = enriched_trades.loc[~mask].copy()
        metrics = _metric_summary(kept, fixed_stake_usd)
        separation = _bootstrap_kept_vs_removed(kept, removed) if scenario != "Baseline" else {
            "kept_vs_removed_avg_pnl_delta_pct": np.nan,
            "separation_ci_low_pct": np.nan,
            "separation_ci_high_pct": np.nan,
            "separation_prob_positive": np.nan,
        }
        rows.append({
            "scenario": scenario,
            **metrics,
            "retention_pct": round(float(metrics["trades"] / baseline_trades * 100.0), 2),
            "removed_trades": int(len(removed)),
            "delta_expectancy_r_vs_baseline": round(float(metrics["expectancy_r"] - baseline_metrics["expectancy_r"]), 4),
            "delta_profit_factor_vs_baseline": round(float(metrics["profit_factor"] - baseline_metrics["profit_factor"]), 4),
            "delta_avg_trade_pct_vs_baseline": round(float(metrics["avg_trade_pct"] - baseline_metrics["avg_trade_pct"]), 4),
            "delta_max_drawdown_usd_vs_baseline": round(float(metrics["max_drawdown_usd"] - baseline_metrics["max_drawdown_usd"]), 2),
            **separation,
        })
    return pd.DataFrame(rows)


def _group_performance(enriched: pd.DataFrame, column: str, fixed_stake_usd: float) -> pd.DataFrame:
    if enriched is None or enriched.empty or column not in enriched.columns:
        return pd.DataFrame()
    rows = []
    for value, group in enriched.groupby(column, dropna=False):
        rows.append({column: "unknown" if pd.isna(value) else str(value), **_metric_summary(group, fixed_stake_usd)})
    return pd.DataFrame(rows).sort_values(["expectancy_r", "profit_factor", "trades"], ascending=[False, False, False]).reset_index(drop=True)


def make_gate_recommendation(comparison: pd.DataFrame) -> dict[str, Any]:
    if comparison is None or comparison.empty:
        return {
            "status": "insufficient_data",
            "recommended_scenario": None,
            "reason": "No trades were available for lifecycle gate analysis.",
        }
    baseline = comparison.loc[comparison["scenario"] == "Baseline"]
    if baseline.empty:
        return {"status": "insufficient_data", "recommended_scenario": None, "reason": "Baseline row is missing."}
    base = baseline.iloc[0]
    candidates = comparison.loc[comparison["scenario"] != "Baseline"].copy()
    if candidates.empty:
        return {"status": "insufficient_data", "recommended_scenario": None, "reason": "No gate scenarios were available."}

    candidates["evidence_score"] = 0.0
    candidates.loc[candidates["trades"] >= 30, "evidence_score"] += 1.0
    candidates.loc[candidates["retention_pct"] >= 20, "evidence_score"] += 1.0
    candidates.loc[candidates["expectancy_r"] > float(base["expectancy_r"]), "evidence_score"] += 1.5
    candidates.loc[candidates["profit_factor"] > float(base["profit_factor"]), "evidence_score"] += 1.5
    candidates.loc[candidates["max_drawdown_usd"] <= float(base["max_drawdown_usd"]), "evidence_score"] += 1.0
    candidates.loc[pd.to_numeric(candidates["separation_prob_positive"], errors="coerce").fillna(0) >= 0.8, "evidence_score"] += 1.0
    candidates.loc[pd.to_numeric(candidates["separation_ci_low_pct"], errors="coerce").fillna(-999) > 0, "evidence_score"] += 1.5

    best = candidates.sort_values(["evidence_score", "expectancy_r", "profit_factor"], ascending=[False, False, False]).iloc[0]
    if int(best["trades"]) < 20:
        return {
            "status": "insufficient_data",
            "recommended_scenario": str(best["scenario"]),
            "reason": "The best-looking gate retains fewer than 20 trades. Do not promote it; widen the sample first.",
            "evidence_score": float(best["evidence_score"]),
        }
    if float(best["evidence_score"]) >= 6.0:
        status = "promising_for_exact_replay"
        reason = "Lifecycle filtering improved multiple quality metrics and the kept trades separate positively from removed trades. Validate with exact signal-path replay and out-of-sample windows before any execution gate."
    elif float(best["evidence_score"]) >= 4.0:
        status = "promising_but_unproven"
        reason = "Some lifecycle filtering benefit is visible, but evidence is not strong enough for a hard gate. Repeat by pair, side, and out-of-sample period."
    else:
        status = "no_gate_evidence"
        reason = "This sample does not justify turning lifecycle fit into a hard trade gate. Keep lifecycle as an explanation layer for now."
    return {
        "status": status,
        "recommended_scenario": str(best["scenario"]),
        "reason": reason,
        "evidence_score": float(best["evidence_score"]),
        "retention_pct": float(best["retention_pct"]),
        "expectancy_r": float(best["expectancy_r"]),
        "profit_factor": float(best["profit_factor"]),
        "separation_prob_positive": None if pd.isna(best["separation_prob_positive"]) else float(best["separation_prob_positive"]),
    }


def run_lifecycle_gate_lab(
    trades: pd.DataFrame,
    *,
    strategy_payload: dict[str, Any] | None = None,
    fixed_stake_usd: float = 100.0,
    score_threshold: float | None = None,
    penalties: dict[str, float] | None = None,
    confidence_floor: float = 55.0,
    analysis_tf: str | None = None,
) -> LifecycleGateLabResult:
    payload = dict(strategy_payload or {})
    threshold = float(score_threshold if score_threshold is not None else _strategy_threshold(payload))
    enriched = enrich_trades_with_lifecycle(trades, strategy_payload=payload, analysis_tf=analysis_tf)
    comparison = build_lifecycle_gate_comparison(
        enriched,
        fixed_stake_usd=float(fixed_stake_usd),
        score_threshold=threshold,
        penalties=penalties,
        confidence_floor=float(confidence_floor),
    )
    by_fit = _group_performance(enriched, "fit_status", float(fixed_stake_usd))
    by_state = _group_performance(enriched, "lifecycle_state", float(fixed_stake_usd))
    recommendation = make_gate_recommendation(comparison)
    return LifecycleGateLabResult(
        enriched_trades=enriched,
        comparison=comparison,
        performance_by_fit_status=by_fit,
        performance_by_lifecycle_state=by_state,
        recommendation=recommendation,
    )
