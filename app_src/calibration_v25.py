from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _pf(pnl: pd.Series) -> float:
    pnl = pd.to_numeric(pnl, errors="coerce").fillna(0.0)
    gross_profit = float(pnl[pnl > 0].sum())
    gross_loss = abs(float(pnl[pnl < 0].sum()))
    if gross_loss == 0:
        return 0.0 if gross_profit <= 0 else 999.0
    return gross_profit / gross_loss


def _segment_summary(work: pd.DataFrame, threshold: float | None = None) -> dict[str, Any]:
    if threshold is not None:
        part = work.loc[pd.to_numeric(work.get("score"), errors="coerce").fillna(-1) >= threshold].copy()
    else:
        part = work.copy()
    pnl = pd.to_numeric(part.get("pnl_usd"), errors="coerce").fillna(0.0)
    score = pd.to_numeric(part.get("score"), errors="coerce")
    return {
        "threshold": threshold if threshold is not None else "baseline",
        "kept_trades": int(len(part)),
        "win_rate": round(float((pnl > 0).mean() * 100), 2) if len(part) else 0.0,
        "total_pnl_usd": round(float(pnl.sum()), 2),
        "profit_factor": round(float(_pf(pnl)), 4),
        "avg_score": round(float(score.mean()), 2) if score.notna().any() else 0.0,
        "expectancy_usd": round(float(pnl.mean()), 4) if len(part) else 0.0,
    }


def _candidate_thresholds(scores: pd.Series) -> list[float]:
    scores = pd.to_numeric(scores, errors="coerce").dropna()
    if scores.empty:
        return [60.0, 65.0, 70.0, 75.0, 80.0, 85.0]
    static = [50, 55, 60, 65, 70, 75, 80, 85, 90]
    quantiles = [float(scores.quantile(q)) for q in [0.25, 0.5, 0.6, 0.7, 0.8]]
    vals = sorted({round(float(v), 2) for v in static + quantiles if 0 <= float(v) <= 100})
    return vals


def build_threshold_recommendations(
    trades: pd.DataFrame,
    group_cols: list[str] | None = None,
    min_segment_trades: int = 20,
    min_kept_trades: int = 8,
) -> pd.DataFrame:
    """Recommend score thresholds by side/regime/owner using existing trade outcomes.

    This is intentionally analytics-only: it does not rewrite strategy thresholds.
    """
    if trades is None or trades.empty or "score" not in trades.columns:
        return pd.DataFrame()
    work = trades.copy()
    if "pnl_usd" not in work.columns and "pnl_pct" in work.columns:
        work["pnl_usd"] = pd.to_numeric(work["pnl_pct"], errors="coerce").fillna(0.0)
    work["pnl_usd"] = pd.to_numeric(work.get("pnl_usd"), errors="coerce").fillna(0.0)
    work["score"] = pd.to_numeric(work.get("score"), errors="coerce")
    work = work.dropna(subset=["score"])
    if work.empty:
        return pd.DataFrame()

    group_cols = group_cols or ["strategy_mode", "trade_owner_key", "side", "regime_group", "regime_detail"]
    for col in group_cols:
        if col not in work.columns:
            work[col] = "unknown"
        work[col] = work[col].fillna("unknown").astype(str)

    rows: list[dict[str, Any]] = []
    for keys, part in work.groupby(group_cols, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        segment = {col: keys[idx] for idx, col in enumerate(group_cols)}
        baseline = _segment_summary(part, threshold=None)
        total_trades = int(len(part))
        candidates = []
        for threshold in _candidate_thresholds(part["score"]):
            row = _segment_summary(part, threshold=threshold)
            row["keep_rate"] = round(float(row["kept_trades"] / total_trades * 100), 2) if total_trades else 0.0
            if row["kept_trades"] >= min_kept_trades:
                candidates.append(row)
        if candidates:
            best = sorted(candidates, key=lambda r: (r["total_pnl_usd"], r["profit_factor"], r["kept_trades"]), reverse=True)[0]
        else:
            best = {"threshold": None, "kept_trades": 0, "win_rate": 0.0, "total_pnl_usd": 0.0, "profit_factor": 0.0, "avg_score": 0.0, "expectancy_usd": 0.0, "keep_rate": 0.0}

        action = "collect_more_samples"
        if total_trades >= min_segment_trades:
            if baseline["total_pnl_usd"] <= 0 and best["total_pnl_usd"] <= 0:
                action = "repair_rules_or_avoid_this_segment"
            elif best["threshold"] is not None and best["total_pnl_usd"] > baseline["total_pnl_usd"] * 1.1 and best["profit_factor"] >= max(1.05, baseline["profit_factor"]):
                action = f"test_score_threshold_{best['threshold']}"
            elif baseline["total_pnl_usd"] > 0 and baseline["profit_factor"] >= 1.1:
                action = "baseline_ok_do_not_overfilter"
            else:
                action = "score_not_separating_edge_check_features"

        rows.append({
            **segment,
            "segment_trades": total_trades,
            "baseline_pnl_usd": baseline["total_pnl_usd"],
            "baseline_pf": baseline["profit_factor"],
            "baseline_win_rate": baseline["win_rate"],
            "recommended_threshold": best.get("threshold"),
            "kept_trades": best.get("kept_trades", 0),
            "keep_rate": best.get("keep_rate", 0.0),
            "threshold_pnl_usd": best.get("total_pnl_usd", 0.0),
            "threshold_pf": best.get("profit_factor", 0.0),
            "threshold_win_rate": best.get("win_rate", 0.0),
            "threshold_expectancy_usd": best.get("expectancy_usd", 0.0),
            "recommended_action": action,
        })

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["baseline_pnl_usd", "threshold_pnl_usd", "segment_trades"], ascending=[False, False, False]).reset_index(drop=True)
