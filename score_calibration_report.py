from __future__ import annotations

import argparse
import json
from pathlib import Path
import pandas as pd
import numpy as np

from app_src.calibration_v25 import build_threshold_recommendations
from app_src.overlap_analytics import build_overlap_reports
from app_src.recommendation_actions import normalize_recommendation_frame
from app_src.backtest_jobs import list_jobs
from app_src.promotion_v27 import build_promotion_candidates, load_review_decisions
from app_src.cross_validation_v28 import build_cv_reports, load_cv_review_decisions


def load_manifest(manifest_path: Path) -> dict | None:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["run_dir"] = str(manifest_path.parent)
        return manifest
    except Exception:
        return None


def load_trades(run_dir: Path) -> pd.DataFrame:
    path = run_dir / "trades.csv"
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def summarize_side(df: pd.DataFrame, label: str) -> dict:
    if df.empty:
        return {"segment": label, "trades": 0, "win_rate": 0.0, "total_pnl_usd": 0.0, "profit_factor": 0.0, "avg_score": 0.0}
    pnl = pd.to_numeric(df.get("pnl_usd"), errors="coerce").fillna(0.0)
    gross_profit = pnl[pnl > 0].sum()
    gross_loss = abs(pnl[pnl < 0].sum())
    is_win = pnl > 0
    score = pd.to_numeric(df.get("score"), errors="coerce")
    return {
        "segment": label,
        "trades": int(len(df)),
        "win_rate": round(float(is_win.mean() * 100), 2),
        "total_pnl_usd": round(float(pnl.sum()), 2),
        "profit_factor": round(float(gross_profit / gross_loss), 4) if gross_loss else 0.0,
        "avg_score": round(float(score.mean()), 2) if score.notna().any() else 0.0,
    }


def score_deciles(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "score" not in df.columns:
        return pd.DataFrame()
    work = df.copy()
    work["score"] = pd.to_numeric(work["score"], errors="coerce")
    work["pnl_usd"] = pd.to_numeric(work.get("pnl_usd"), errors="coerce").fillna(0.0)
    work = work.dropna(subset=["score"])
    if work.empty:
        return pd.DataFrame()
    bucket_count = max(1, min(10, len(work)))
    if bucket_count == 1:
        work["score_decile"] = "D1"
    else:
        labels = [f"D{i}" for i in range(1, bucket_count + 1)]
        work["score_decile"] = pd.qcut(work["score"].rank(method="first"), q=bucket_count, labels=labels, duplicates="drop")
    grouped = work.groupby("score_decile", as_index=False).agg(
        trades=("score", "size"),
        avg_score=("score", "mean"),
        total_pnl_usd=("pnl_usd", "sum"),
        win_rate=("pnl_usd", lambda s: (s > 0).mean() * 100),
    )
    grouped["avg_score"] = grouped["avg_score"].round(2)
    grouped["total_pnl_usd"] = grouped["total_pnl_usd"].round(2)
    grouped["win_rate"] = grouped["win_rate"].round(2)
    return grouped


def regime_summary(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "regime" not in df.columns:
        return pd.DataFrame()
    work = df.copy()
    work["pnl_usd"] = pd.to_numeric(work.get("pnl_usd"), errors="coerce").fillna(0.0)
    out = work.groupby("regime", as_index=False).agg(
        trades=("regime", "size"),
        total_pnl_usd=("pnl_usd", "sum"),
        win_rate=("pnl_usd", lambda s: (s > 0).mean() * 100),
    ).sort_values("total_pnl_usd", ascending=False)
    out["total_pnl_usd"] = out["total_pnl_usd"].round(2)
    out["win_rate"] = out["win_rate"].round(2)
    return out



def segment_summary(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    work = df.copy()
    work["pnl_usd"] = pd.to_numeric(work.get("pnl_usd"), errors="coerce").fillna(0.0)
    work["score"] = pd.to_numeric(work.get("score"), errors="coerce")
    for col in group_cols:
        if col not in work.columns:
            work[col] = "unknown"
        work[col] = work[col].fillna("unknown").astype(str)
    out = work.groupby(group_cols, as_index=False).agg(
        trades=(group_cols[0], "size"),
        total_pnl_usd=("pnl_usd", "sum"),
        win_rate=("pnl_usd", lambda s: (s > 0).mean() * 100),
        avg_score=("score", "mean"),
    )
    pf_rows = []
    for keys, part in work.groupby(group_cols, dropna=False):
        gross_profit = part.loc[part["pnl_usd"] > 0, "pnl_usd"].sum()
        gross_loss = abs(part.loc[part["pnl_usd"] < 0, "pnl_usd"].sum())
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = {col: keys[idx] for idx, col in enumerate(group_cols)}
        row["profit_factor"] = round(float(gross_profit / gross_loss), 4) if gross_loss else 0.0
        pf_rows.append(row)
    pf = pd.DataFrame(pf_rows)
    if not pf.empty:
        out = out.merge(pf, on=group_cols, how="left")
    out["total_pnl_usd"] = out["total_pnl_usd"].round(2)
    out["win_rate"] = out["win_rate"].round(2)
    out["avg_score"] = out["avg_score"].round(2)
    return out.sort_values(["total_pnl_usd", "trades"], ascending=[False, False]).reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--saved-runs-root", default="data/backtests")
    ap.add_argument("--out-dir", default="analysis_reports")
    args = ap.parse_args()

    saved_root = Path(args.saved_runs_root)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifests = [load_manifest(p) for p in saved_root.glob("*/manifest.json")]
    manifests = [m for m in manifests if m]
    if not manifests:
        print(f"No saved runs found under {saved_root}")
        return

    meta_rows = []
    side_rows = []
    regime_rows = []
    side_regime_rows = []
    exit_family_rows = []
    owner_rows = []
    friction_rows = []
    bundle_validation_rows = []
    detailed_regime_rows = []
    side_detailed_regime_rows = []
    threshold_rows = []
    loaded_for_overlap = []
    decile_frames = []

    for manifest in manifests:
        run_dir = Path(manifest["run_dir"])
        trades = load_trades(run_dir)
        name = manifest.get("name") or run_dir.name
        strategy = ((manifest.get("strategy_payload") or {}) or {}).get("strategy_name") or "Unknown"
        summary = manifest.get("summary") or {}
        config = manifest.get("config") or {}
        meta_rows.append({
            "name": name,
            "strategy_name": strategy,
            "symbols": ",".join(config.get("symbols") or []),
            "start_date": config.get("start_date"),
            "end_date": config.get("end_date"),
            "total_trades": summary.get("total_trades", 0),
            "profit_factor": summary.get("profit_factor", 0.0),
            "total_pnl_usd": summary.get("total_pnl_usd", 0.0),
            "pre_friction_pnl_usd": summary.get("pre_friction_pnl_usd", 0.0),
            "total_execution_cost_usd": summary.get("total_execution_cost_usd", 0.0),
            "execution_preset": config.get("execution_preset_label") or config.get("execution_preset_name") or "custom/unknown",
            "win_rate": summary.get("win_rate", 0.0),
        })
        if trades.empty:
            continue
        trades["run_name"] = name
        trades["strategy_name"] = trades.get("strategy_name", strategy)
        loaded_for_overlap.append((name, trades.copy(), {"manifest": manifest}))
        if "pnl_usd" not in trades.columns and "pnl_pct" in trades.columns:
            trades["pnl_usd"] = pd.to_numeric(trades["pnl_pct"], errors="coerce").fillna(0.0)
        for side in ["LONG", "SHORT"]:
            row = summarize_side(trades.loc[trades.get("side") == side].copy(), side)
            row["run_name"] = name
            row["strategy_name"] = strategy
            side_rows.append(row)
        reg = regime_summary(trades)
        if not reg.empty:
            reg.insert(0, "run_name", name)
            reg.insert(1, "strategy_name", strategy)
            regime_rows.append(reg)
        side_reg = segment_summary(trades, ["side", "regime"])
        if not side_reg.empty:
            side_reg.insert(0, "run_name", name)
            side_reg.insert(1, "strategy_name", strategy)
            side_regime_rows.append(side_reg)
        exit_fam = segment_summary(trades, ["exit_family"])
        if not exit_fam.empty:
            exit_fam.insert(0, "run_name", name)
            exit_fam.insert(1, "strategy_name", strategy)
            exit_family_rows.append(exit_fam)
        owner = segment_summary(trades, ["strategy_mode", "trade_owner_key", "side"])
        if not owner.empty:
            owner.insert(0, "run_name", name)
            owner.insert(1, "strategy_name", strategy)
            owner_rows.append(owner)
        if {"raw_pnl_pct", "pnl_pct", "execution_cost_pct"}.issubset(trades.columns):
            friction = pd.DataFrame([{
                "run_name": name,
                "strategy_name": strategy,
                "trades": int(len(trades)),
                "gross_pnl_pct": round(float(pd.to_numeric(trades["raw_pnl_pct"], errors="coerce").fillna(0.0).sum()), 4),
                "net_pnl_pct": round(float(pd.to_numeric(trades["pnl_pct"], errors="coerce").fillna(0.0).sum()), 4),
                "execution_cost_pct": round(float(pd.to_numeric(trades["execution_cost_pct"], errors="coerce").fillna(0.0).sum()), 4),
            }])
            friction_rows.append(friction)
        if "strategy_mode" in trades.columns:
            bundle_val = segment_summary(trades, ["strategy_mode"])
            if not bundle_val.empty:
                bundle_val.insert(0, "run_name", name)
                bundle_val.insert(1, "strategy_name", strategy)
                bundle_validation_rows.append(bundle_val)
        detailed = segment_summary(trades, ["regime_group", "regime_detail"])
        if not detailed.empty:
            detailed.insert(0, "run_name", name)
            detailed.insert(1, "strategy_name", strategy)
            detailed_regime_rows.append(detailed)
        side_detailed = segment_summary(trades, ["side", "regime_group", "regime_detail"])
        if not side_detailed.empty:
            side_detailed.insert(0, "run_name", name)
            side_detailed.insert(1, "strategy_name", strategy)
            side_detailed_regime_rows.append(side_detailed)
        thresholds = build_threshold_recommendations(trades)
        if not thresholds.empty:
            thresholds.insert(0, "run_name", name)
            thresholds.insert(1, "strategy_name", strategy)
            threshold_rows.append(thresholds)
        dec = score_deciles(trades)
        if not dec.empty:
            dec.insert(0, "run_name", name)
            dec.insert(1, "strategy_name", strategy)
            decile_frames.append(dec)

    pd.DataFrame(meta_rows).sort_values(["profit_factor", "total_pnl_usd"], ascending=[False, False]).to_csv(out_dir / "saved_runs_overview.csv", index=False)
    pd.DataFrame(side_rows).sort_values(["run_name", "segment"]).to_csv(out_dir / "saved_runs_long_short.csv", index=False)
    if regime_rows:
        pd.concat(regime_rows, ignore_index=True).to_csv(out_dir / "saved_runs_regime.csv", index=False)
    else:
        pd.DataFrame(columns=["run_name","strategy_name","regime","trades","total_pnl_usd","win_rate"]).to_csv(out_dir / "saved_runs_regime.csv", index=False)
    if side_regime_rows:
        pd.concat(side_regime_rows, ignore_index=True).to_csv(out_dir / "saved_runs_side_regime.csv", index=False)
    else:
        pd.DataFrame(columns=["run_name","strategy_name","side","regime","trades","total_pnl_usd","win_rate","avg_score","profit_factor"]).to_csv(out_dir / "saved_runs_side_regime.csv", index=False)
    if exit_family_rows:
        pd.concat(exit_family_rows, ignore_index=True).to_csv(out_dir / "saved_runs_exit_family.csv", index=False)
    else:
        pd.DataFrame(columns=["run_name","strategy_name","exit_family","trades","total_pnl_usd","win_rate","avg_score","profit_factor"]).to_csv(out_dir / "saved_runs_exit_family.csv", index=False)
    if owner_rows:
        pd.concat(owner_rows, ignore_index=True).to_csv(out_dir / "saved_runs_owner_split.csv", index=False)
    else:
        pd.DataFrame(columns=["run_name","strategy_name","strategy_mode","trade_owner_key","side","trades","total_pnl_usd","win_rate","avg_score","profit_factor"]).to_csv(out_dir / "saved_runs_owner_split.csv", index=False)
    if friction_rows:
        pd.concat(friction_rows, ignore_index=True).to_csv(out_dir / "saved_runs_friction.csv", index=False)
    else:
        pd.DataFrame(columns=["run_name","strategy_name","trades","gross_pnl_pct","net_pnl_pct","execution_cost_pct"]).to_csv(out_dir / "saved_runs_friction.csv", index=False)
    if bundle_validation_rows:
        pd.concat(bundle_validation_rows, ignore_index=True).to_csv(out_dir / "saved_runs_bundle_validation.csv", index=False)
    else:
        pd.DataFrame(columns=["run_name","strategy_name","strategy_mode","trades","total_pnl_usd","win_rate","avg_score","profit_factor"]).to_csv(out_dir / "saved_runs_bundle_validation.csv", index=False)
    if detailed_regime_rows:
        pd.concat(detailed_regime_rows, ignore_index=True).to_csv(out_dir / "saved_runs_detailed_regime.csv", index=False)
    else:
        pd.DataFrame(columns=["run_name","strategy_name","regime_group","regime_detail","trades","total_pnl_usd","win_rate","avg_score","profit_factor"]).to_csv(out_dir / "saved_runs_detailed_regime.csv", index=False)
    if side_detailed_regime_rows:
        pd.concat(side_detailed_regime_rows, ignore_index=True).to_csv(out_dir / "saved_runs_side_detailed_regime.csv", index=False)
    else:
        pd.DataFrame(columns=["run_name","strategy_name","side","regime_group","regime_detail","trades","total_pnl_usd","win_rate","avg_score","profit_factor"]).to_csv(out_dir / "saved_runs_side_detailed_regime.csv", index=False)
    if threshold_rows:
        threshold_all = pd.concat(threshold_rows, ignore_index=True)
        threshold_all.to_csv(out_dir / "saved_runs_threshold_recommendations.csv", index=False)
        action_candidates = normalize_recommendation_frame(threshold_all)
        if not action_candidates.empty:
            action_candidates = action_candidates.loc[action_candidates["recommended_action"].astype(str).str.startswith("test_score_threshold")].copy()
        action_candidates.to_csv(out_dir / "saved_runs_v26_action_candidates.csv", index=False)
    else:
        pd.DataFrame(columns=["run_name","strategy_name","side","regime_group","regime_detail","segment_trades","recommended_threshold","recommended_action"]).to_csv(out_dir / "saved_runs_threshold_recommendations.csv", index=False)
        pd.DataFrame(columns=["run_name","strategy_name","side","regime_group","regime_detail","segment_trades","recommended_threshold","recommended_action"]).to_csv(out_dir / "saved_runs_v26_action_candidates.csv", index=False)
    overlap_reports = build_overlap_reports(loaded_for_overlap, bucket="15min") if loaded_for_overlap else {}
    for key, fname in [("overlap_same_side", "saved_runs_overlap_same_side.csv"), ("opposite_side_conflicts", "saved_runs_opposite_side_conflicts.csv"), ("owner_pair_overlap", "saved_runs_owner_pair_overlap.csv")]:
        frame = overlap_reports.get(key, pd.DataFrame()) if overlap_reports else pd.DataFrame()
        frame.to_csv(out_dir / fname, index=False)
    try:
        v27_candidates = build_promotion_candidates(loaded_for_overlap, jobs=list_jobs(["completed"]), min_candidate_trades=20) if loaded_for_overlap else pd.DataFrame()
    except Exception:
        v27_candidates = pd.DataFrame()
    if v27_candidates.empty:
        v27_candidates = pd.DataFrame(columns=[
            "review_recommendation", "source_run_name", "candidate_run_name", "side", "regime_group", "regime_detail",
            "tested_threshold", "source_total_trades", "candidate_total_trades", "delta_total_trades",
            "source_total_pnl_usd", "candidate_total_pnl_usd", "delta_total_pnl_usd",
            "source_profit_factor", "candidate_profit_factor", "delta_profit_factor", "candidate_run_dir", "source_run_dir"
        ])
    v27_candidates.to_csv(out_dir / "saved_runs_v27_promotion_candidates.csv", index=False)
    review_log = load_review_decisions()
    review_log.to_csv(out_dir / "saved_runs_v27_review_log.csv", index=False)
    try:
        v28_reports = build_cv_reports(list_jobs(["completed"]))
    except Exception:
        v28_reports = {"cv_results": pd.DataFrame(), "cv_pairs": pd.DataFrame(), "cv_aggregate": pd.DataFrame()}
    v28_results_df = v28_reports.get("cv_results", pd.DataFrame())
    if v28_results_df.empty and len(v28_results_df.columns) == 0:
        v28_results_df = pd.DataFrame(columns=["cv_id", "job_id", "fold_id", "symbol", "role", "run_dir", "total_trades", "total_pnl_usd", "profit_factor"])
    v28_pairs_df = v28_reports.get("cv_pairs", pd.DataFrame())
    if v28_pairs_df.empty and len(v28_pairs_df.columns) == 0:
        v28_pairs_df = pd.DataFrame(columns=["cv_id", "fold_id", "symbol", "candidate_won_pair", "delta_total_pnl_usd", "delta_profit_factor"])
    v28_aggregate_df = v28_reports.get("cv_aggregate", pd.DataFrame())
    if v28_aggregate_df.empty and len(v28_aggregate_df.columns) == 0:
        v28_aggregate_df = pd.DataFrame(columns=["cv_id", "candidate_name", "tested_pairs", "candidate_wins", "cv_win_rate_pct", "sum_delta_pnl_usd", "avg_delta_profit_factor", "promotion_confidence", "v28_recommendation"])
    v28_results_df.to_csv(out_dir / "saved_runs_v28_cv_results.csv", index=False)
    v28_pairs_df.to_csv(out_dir / "saved_runs_v28_cv_pairs.csv", index=False)
    v28_aggregate_df.to_csv(out_dir / "saved_runs_v28_cv_aggregate.csv", index=False)
    v28_review_log = load_cv_review_decisions()
    v28_review_log.to_csv(out_dir / "saved_runs_v28_cv_review_log.csv", index=False)
    if decile_frames:
        pd.concat(decile_frames, ignore_index=True).to_csv(out_dir / "saved_runs_score_deciles.csv", index=False)
    else:
        pd.DataFrame(columns=["run_name","strategy_name","score_decile","trades","avg_score","total_pnl_usd","win_rate"]).to_csv(out_dir / "saved_runs_score_deciles.csv", index=False)

    md = out_dir / "score_calibration_report.md"
    overview = pd.read_csv(out_dir / "saved_runs_overview.csv")
    long_short = pd.read_csv(out_dir / "saved_runs_long_short.csv")
    regime = pd.read_csv(out_dir / "saved_runs_regime.csv")
    side_regime = pd.read_csv(out_dir / "saved_runs_side_regime.csv")
    exit_family = pd.read_csv(out_dir / "saved_runs_exit_family.csv")
    owner = pd.read_csv(out_dir / "saved_runs_owner_split.csv")
    friction = pd.read_csv(out_dir / "saved_runs_friction.csv")
    bundle_validation = pd.read_csv(out_dir / "saved_runs_bundle_validation.csv")
    deciles = pd.read_csv(out_dir / "saved_runs_score_deciles.csv")
    detailed_regime = pd.read_csv(out_dir / "saved_runs_detailed_regime.csv")
    side_detailed_regime = pd.read_csv(out_dir / "saved_runs_side_detailed_regime.csv")
    thresholds = pd.read_csv(out_dir / "saved_runs_threshold_recommendations.csv")
    action_candidates = pd.read_csv(out_dir / "saved_runs_v26_action_candidates.csv")
    overlap_same = pd.read_csv(out_dir / "saved_runs_overlap_same_side.csv")
    overlap_conflicts = pd.read_csv(out_dir / "saved_runs_opposite_side_conflicts.csv")
    v27_candidates = pd.read_csv(out_dir / "saved_runs_v27_promotion_candidates.csv")
    v27_review_log = pd.read_csv(out_dir / "saved_runs_v27_review_log.csv")
    v28_cv_aggregate = pd.read_csv(out_dir / "saved_runs_v28_cv_aggregate.csv")
    v28_cv_pairs = pd.read_csv(out_dir / "saved_runs_v28_cv_pairs.csv")
    v28_cv_review_log = pd.read_csv(out_dir / "saved_runs_v28_cv_review_log.csv")
    with md.open("w", encoding="utf-8") as f:
        f.write("# Saved Run Calibration Report\n\n")
        f.write("## Overview\n\n")
        f.write(overview.head(30).to_markdown(index=False))
        f.write("\n\n## Long / Short Split\n\n")
        if not long_short.empty:
            f.write(long_short.head(60).to_markdown(index=False))
        else:
            f.write("No long/short rows available.\n")
        f.write("\n\n## Regime Split\n\n")
        if not regime.empty:
            f.write(regime.head(60).to_markdown(index=False))
        else:
            f.write("No regime rows available.\n")
        f.write("\n\n## Side x Regime Split\n\n")
        if not side_regime.empty:
            f.write(side_regime.head(80).to_markdown(index=False))
        else:
            f.write("No side/regime rows available.\n")
        f.write("\n\n## Exit Family Split\n\n")
        if not exit_family.empty:
            f.write(exit_family.head(80).to_markdown(index=False))
        else:
            f.write("No exit-family rows available.\n")
        f.write("\n\n## V25 Detailed Regime Split\n\n")
        if not detailed_regime.empty:
            f.write(detailed_regime.head(80).to_markdown(index=False))
        else:
            f.write("No detailed-regime rows available.\n")
        f.write("\n\n## V25 Side x Detailed Regime Split\n\n")
        if not side_detailed_regime.empty:
            f.write(side_detailed_regime.head(100).to_markdown(index=False))
        else:
            f.write("No side/detailed-regime rows available.\n")
        f.write("\n\n## V25 Threshold Recommendations\n\n")
        if not thresholds.empty:
            f.write(thresholds.head(100).to_markdown(index=False))
        else:
            f.write("No threshold recommendation rows available.\n")
        f.write("\n\n## V26 Recommendation-to-Action Candidates\n\n")
        if not action_candidates.empty:
            f.write("These are the rows the Backtest Lab can convert into targeted segment what-if jobs.\n\n")
            f.write(action_candidates.head(100).to_markdown(index=False))
        else:
            f.write("No queueable V26 action candidates found.\n")
        f.write("\n\n## V27 Promotion Candidates\n\n")
        if not v27_candidates.empty:
            f.write("These rows compare V26 recommendation what-if results against their source runs. Use them for manual promote / reject / more-data / repair decisions.\n\n")
            f.write(v27_candidates.head(100).to_markdown(index=False))
        else:
            f.write("No V27 promotion candidates found. Queue V26 recommendation what-if jobs and compare the source plus completed candidate runs.\n")
        f.write("\n\n## V27 Review Audit Trail\n\n")
        if not v27_review_log.empty:
            f.write(v27_review_log.tail(100).to_markdown(index=False))
        else:
            f.write("No V27 review decisions saved yet.\n")
        f.write("\n\n## V28 Cross-Validation Aggregate\n\n")
        if not v28_cv_aggregate.empty:
            f.write("These rows compare V27 strategy drafts against their source runs across date folds and symbols. They are meant to support manual promotion decisions, not automatic live changes.\n\n")
            f.write(v28_cv_aggregate.head(100).to_markdown(index=False))
        else:
            f.write("No V28 cross-validation results found. Queue V28 CV jobs in the Backtest Lab and wait for worker completion.\n")
        f.write("\n\n## V28 Cross-Validation Pair Detail\n\n")
        if not v28_cv_pairs.empty:
            f.write(v28_cv_pairs.head(150).to_markdown(index=False))
        else:
            f.write("No V28 pair-level CV rows found.\n")
        f.write("\n\n## V28 CV Review Audit Trail\n\n")
        if not v28_cv_review_log.empty:
            f.write(v28_cv_review_log.tail(100).to_markdown(index=False))
        else:
            f.write("No V28 CV review decisions saved yet.\n")
        f.write("\n\n## V25 Overlap / Concurrency\n\n")
        if not overlap_same.empty:
            f.write("### Same-side overlaps\n\n")
            f.write(overlap_same.head(80).to_markdown(index=False))
        else:
            f.write("No same-side overlaps found.\n")
        if not overlap_conflicts.empty:
            f.write("\n\n### Opposite-side conflicts\n\n")
            f.write(overlap_conflicts.head(80).to_markdown(index=False))
        f.write("\n\n## Score Deciles\n\n")
        if not deciles.empty:
            f.write(deciles.head(80).to_markdown(index=False))
        else:
            f.write("No decile rows available.\n")
    print(f"Saved reports to {out_dir}")


if __name__ == "__main__":
    main()
