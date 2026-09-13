from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from .backtest_core import load_saved_backtest
from .backtest_jobs import list_jobs
from .research_command_center_v29 import CORE_RESEARCH_FAMILIES
from .strategy_family_registry_v2811 import resolve_entry

POLICY_PATH = Path("config/research_evidence_policy.json")
EVIDENCE_DIR = Path("data/backtest_reviews/evidence_scorecards")
RESEARCH_RUN_KINDS = {"v28_9_core_research", "v28_12_core_research"}
VERDICT_ORDER = {
    "cross_validation_candidate": 4,
    "promising": 3,
    "insufficient_evidence": 2,
    "reject": 1,
}


def load_evidence_policy(path: str | Path = POLICY_PATH) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("research evidence policy must be a JSON object")
    return payload


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return float(default)
    except Exception:
        pass
    try:
        return float(value)
    except Exception:
        return float(default)


def _active_frame(frame: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    work = frame.copy()
    if "trades" in work.columns:
        work = work[pd.to_numeric(work["trades"], errors="coerce").fillna(0) > 0]
    return work.reset_index(drop=True)


def _stability_stats(frame: pd.DataFrame | None) -> tuple[int, float, float]:
    work = _active_frame(frame)
    if work.empty or "total_pnl_usd" not in work.columns:
        return 0, 0.0, 0.0
    pnl = pd.to_numeric(work["total_pnl_usd"], errors="coerce").fillna(0.0)
    positive_share = float((pnl > 0).mean()) if len(work) else 0.0
    concentration = 0.0
    if "trades" in work.columns:
        trades = pd.to_numeric(work["trades"], errors="coerce").fillna(0.0)
        total = float(trades.sum())
        concentration = float(trades.max() / total) if total > 0 else 0.0
    return int(len(work)), positive_share, concentration


def _side_stats(frame: pd.DataFrame | None) -> dict[str, Any]:
    work = _active_frame(frame)
    out = {
        "long_trades": 0,
        "short_trades": 0,
        "long_pnl_usd": 0.0,
        "short_pnl_usd": 0.0,
        "side_concentration_pct": 0.0,
    }
    if work.empty or "side" not in work.columns:
        return out
    total_trades = 0.0
    max_side_trades = 0.0
    for _, row in work.iterrows():
        side = str(row.get("side") or "").upper()
        trades = _num(row.get("trades"))
        pnl = _num(row.get("total_pnl_usd"))
        total_trades += trades
        max_side_trades = max(max_side_trades, trades)
        if side == "LONG":
            out["long_trades"] += int(trades)
            out["long_pnl_usd"] += pnl
        elif side == "SHORT":
            out["short_trades"] += int(trades)
            out["short_pnl_usd"] += pnl
    out["long_pnl_usd"] = round(float(out["long_pnl_usd"]), 2)
    out["short_pnl_usd"] = round(float(out["short_pnl_usd"]), 2)
    out["side_concentration_pct"] = round((max_side_trades / total_trades) * 100, 2) if total_trades > 0 else 0.0
    return out


def _strategy_meta(run: dict[str, Any], result_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    manifest = dict(run.get("manifest") or {})
    payload = dict(manifest.get("strategy_payload") or {})
    result_meta = dict(result_meta or {})
    strategy_name = str(payload.get("strategy_name") or result_meta.get("strategy_name") or manifest.get("name") or "Strategy")
    reg = resolve_entry(strategy_name)
    family = str(payload.get("research_family") or reg.get("strategy_family") or "unknown")
    return {
        "strategy_name": strategy_name,
        "research_family": family,
        "research_source": str(payload.get("research_source") or "saved_strategy"),
        "benchmark_only": bool(payload.get("benchmark_only", False) or reg.get("benchmark_only", False)),
        "registry_key": str(payload.get("registry_key") or reg.get("registry_key") or ""),
    }


def evaluate_evidence_run(
    run: dict[str, Any],
    *,
    result_meta: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = dict(policy or load_evidence_policy())
    promising_cfg = dict(policy.get("promising") or {})
    cross_cfg = dict(policy.get("cross_validation") or {})
    manifest = dict(run.get("manifest") or {})
    summary = dict(manifest.get("summary") or (result_meta or {}).get("summary") or {})
    meta = _strategy_meta(run, result_meta)

    total_trades = int(_num(summary.get("total_trades")))
    win_rate = _num(summary.get("win_rate"))
    expectancy_r = _num(summary.get("expectancy_r"))
    profit_factor = _num(summary.get("profit_factor"))
    net_pnl_usd = _num(summary.get("total_pnl_usd"))
    max_drawdown_usd = abs(_num(summary.get("max_drawdown_usd")))
    execution_cost_usd = max(0.0, _num(summary.get("total_execution_cost_usd")))
    pre_friction_pnl_usd = _num(summary.get("pre_friction_pnl_usd"), net_pnl_usd + execution_cost_usd)

    drawdown_to_net_profit = (max_drawdown_usd / net_pnl_usd) if net_pnl_usd > 0 else None
    friction_drag_share = (execution_cost_usd / abs(pre_friction_pnl_usd)) if pre_friction_pnl_usd > 0 else None

    symbol_count, positive_symbol_share, symbol_trade_concentration = _stability_stats(run.get("performance_by_symbol"))
    month_count, positive_month_share, month_trade_concentration = _stability_stats(run.get("performance_by_month"))
    side = _side_stats(run.get("performance_by_side"))

    min_review = int(policy.get("min_trades_for_review", 50))
    min_reject = int(policy.get("min_trades_for_reject", 30))
    min_cross = int(policy.get("min_trades_for_cross_validation", 100))
    min_symbols = int(policy.get("min_active_symbols", 2))
    min_months = int(policy.get("min_active_months", 4))

    p_pf = _num(promising_cfg.get("min_profit_factor"), 1.10)
    p_symbol = _num(promising_cfg.get("min_positive_symbol_share"), 0.50)
    p_month = _num(promising_cfg.get("min_positive_month_share"), 0.50)
    p_dd = _num(promising_cfg.get("max_drawdown_to_net_profit"), 1.50)
    p_friction = _num(promising_cfg.get("max_friction_drag_share_of_gross"), 0.60)

    checks = {
        "sample_size": total_trades >= min_review,
        "positive_net_pnl": net_pnl_usd > 0,
        "positive_expectancy": expectancy_r > 0,
        "profit_factor": profit_factor >= p_pf,
        "pair_stability": symbol_count >= min_symbols and positive_symbol_share >= p_symbol,
        "month_stability": month_count >= min_months and positive_month_share >= p_month,
        "drawdown_control": drawdown_to_net_profit is not None and drawdown_to_net_profit <= p_dd,
        "friction_resilience": friction_drag_share is not None and friction_drag_share <= p_friction,
    }
    checks_passed = int(sum(bool(v) for v in checks.values()))
    checks_total = int(len(checks))

    hard_negative = net_pnl_usd <= 0 or expectancy_r <= 0 or profit_factor < 1.0
    promising_ready = all(checks.values())

    c_pf = _num(cross_cfg.get("min_profit_factor"), 1.20)
    c_symbol = _num(cross_cfg.get("min_positive_symbol_share"), 0.67)
    c_month = _num(cross_cfg.get("min_positive_month_share"), 0.60)
    c_dd = _num(cross_cfg.get("max_drawdown_to_net_profit"), 1.00)
    c_friction = _num(cross_cfg.get("max_friction_drag_share_of_gross"), 0.50)
    cross_ready = bool(
        total_trades >= min_cross
        and net_pnl_usd > 0
        and expectancy_r > 0
        and profit_factor >= c_pf
        and symbol_count >= min_symbols
        and positive_symbol_share >= c_symbol
        and month_count >= min_months
        and positive_month_share >= c_month
        and drawdown_to_net_profit is not None
        and drawdown_to_net_profit <= c_dd
        and friction_drag_share is not None
        and friction_drag_share <= c_friction
    )

    if total_trades >= min_reject and hard_negative:
        verdict = "reject"
    elif meta["benchmark_only"] and promising_ready:
        verdict = "promising"
    elif cross_ready:
        verdict = "cross_validation_candidate"
    elif promising_ready:
        verdict = "promising"
    else:
        verdict = "insufficient_evidence"

    warnings: list[str] = []
    if total_trades < min_review:
        warnings.append(f"small sample: {total_trades} < {min_review}")
    if symbol_count < min_symbols:
        warnings.append(f"only {symbol_count} active symbol(s)")
    elif positive_symbol_share < p_symbol:
        warnings.append(f"pair stability weak: {positive_symbol_share:.0%} profitable")
    if month_count < min_months:
        warnings.append(f"only {month_count} active month(s)")
    elif positive_month_share < p_month:
        warnings.append(f"month stability weak: {positive_month_share:.0%} profitable")
    if net_pnl_usd <= 0:
        warnings.append("net PnL is not positive after friction")
    if expectancy_r <= 0:
        warnings.append("expectancy is not positive")
    if profit_factor < p_pf:
        warnings.append(f"profit factor below {p_pf:.2f}")
    if drawdown_to_net_profit is None:
        warnings.append("drawdown/profit ratio unavailable because net profit is non-positive")
    elif drawdown_to_net_profit > p_dd:
        warnings.append(f"drawdown is {drawdown_to_net_profit:.2f}x net profit")
    if friction_drag_share is None:
        warnings.append("friction resilience unavailable because pre-friction PnL is non-positive")
    elif friction_drag_share > p_friction:
        warnings.append(f"friction consumes {friction_drag_share:.0%} of pre-friction PnL")
    if symbol_trade_concentration > 0.60:
        warnings.append(f"trade concentration: {symbol_trade_concentration:.0%} in one pair")
    if month_trade_concentration > 0.35 and month_count >= min_months:
        warnings.append(f"time concentration: {month_trade_concentration:.0%} of trades in one month")
    if side["side_concentration_pct"] > 90 and total_trades >= min_review:
        warnings.append(f"side concentration: {side['side_concentration_pct']:.0f}% on one direction")
    if meta["benchmark_only"]:
        warnings.append("benchmark-only: concept evidence cannot be promoted directly to live/paper")

    if verdict == "reject":
        next_step = "Drop or redesign this version before spending more validation effort."
    elif verdict == "insufficient_evidence":
        next_step = "Collect more independent evidence; do not optimize thresholds from this result alone."
    elif meta["benchmark_only"]:
        next_step = "Treat as concept evidence only; deepen compression research or add missing historical datasets."
    elif verdict == "cross_validation_candidate":
        next_step = "Run out-of-sample/cross-validation next; paper testing only after it survives."
    else:
        next_step = "Run cross-period and cross-validation checks before any promotion decision."

    return {
        **meta,
        "verdict": verdict,
        "verdict_rank": VERDICT_ORDER.get(verdict, 0),
        "promotion_blocked": bool(meta["benchmark_only"]),
        "total_trades": total_trades,
        "win_rate": round(win_rate, 2),
        "expectancy_r": round(expectancy_r, 4),
        "profit_factor": round(profit_factor, 4),
        "net_pnl_usd": round(net_pnl_usd, 2),
        "pre_friction_pnl_usd": round(pre_friction_pnl_usd, 2),
        "execution_cost_usd": round(execution_cost_usd, 2),
        "friction_drag_share": round(float(friction_drag_share), 4) if friction_drag_share is not None else None,
        "max_drawdown_usd": round(max_drawdown_usd, 2),
        "drawdown_to_net_profit": round(float(drawdown_to_net_profit), 4) if drawdown_to_net_profit is not None else None,
        "active_symbols": symbol_count,
        "positive_symbol_share": round(positive_symbol_share, 4),
        "symbol_trade_concentration": round(symbol_trade_concentration, 4),
        "active_months": month_count,
        "positive_month_share": round(positive_month_share, 4),
        "month_trade_concentration": round(month_trade_concentration, 4),
        **side,
        "checks_passed": checks_passed,
        "checks_total": checks_total,
        "checks": checks,
        "warnings": warnings,
        "warning_count": len(warnings),
        "warnings_text": " | ".join(warnings),
        "next_step": next_step,
        "policy_version": str(policy.get("version") or ""),
        "run_dir": str(run.get("run_dir") or ""),
    }


def completed_research_jobs() -> list[dict[str, Any]]:
    jobs = [
        job
        for job in list_jobs(["completed"])
        if str(job.get("run_kind") or "") in RESEARCH_RUN_KINDS
    ]
    return sorted(jobs, key=lambda x: str(x.get("created_at") or ""), reverse=True)


def get_completed_research_job(job_id: str | None = None) -> dict[str, Any] | None:
    jobs = completed_research_jobs()
    if not jobs:
        return None
    if job_id:
        for job in jobs:
            if str(job.get("job_id")) == str(job_id):
                return job
        return None
    return jobs[0]


def build_job_scorecard(
    job: dict[str, Any],
    *,
    loader: Callable[[str | Path], dict[str, Any]] = load_saved_backtest,
    policy: dict[str, Any] | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    policy = dict(policy or load_evidence_policy())
    for result in job.get("results") or []:
        run_dir = str(result.get("run_dir") or "")
        if not run_dir:
            rows.append({
                "strategy_name": result.get("strategy_name") or "Strategy",
                "research_family": "unknown",
                "verdict": "insufficient_evidence",
                "verdict_rank": VERDICT_ORDER["insufficient_evidence"],
                "warning_count": 1,
                "warnings_text": "saved run directory missing",
                "run_dir": "",
                "policy_version": str(policy.get("version") or ""),
            })
            continue
        try:
            run = loader(run_dir)
            run["run_dir"] = run_dir
            row = evaluate_evidence_run(run, result_meta=result, policy=policy)
        except Exception as exc:
            row = {
                "strategy_name": result.get("strategy_name") or "Strategy",
                "research_family": "unknown",
                "verdict": "insufficient_evidence",
                "verdict_rank": VERDICT_ORDER["insufficient_evidence"],
                "warning_count": 1,
                "warnings_text": f"could not load saved run: {exc}",
                "run_dir": run_dir,
                "policy_version": str(policy.get("version") or ""),
            }
        row["job_id"] = job.get("job_id")
        row["run_kind"] = job.get("run_kind")
        row["protocol_version"] = str((job.get("research_protocol") or {}).get("version") or "")
        rows.append(row)
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    family_order = {family: idx for idx, family in enumerate(CORE_RESEARCH_FAMILIES)}
    out["_family_order"] = out.get("research_family", pd.Series(dtype=str)).map(lambda x: family_order.get(str(x), 999))
    return out.sort_values(["_family_order", "verdict_rank"], ascending=[True, False]).drop(columns=["_family_order"]).reset_index(drop=True)


def scorecard_family_coverage(scorecard: pd.DataFrame) -> dict[str, Any]:
    present = set(scorecard.get("research_family", pd.Series(dtype=str)).astype(str).tolist()) if not scorecard.empty else set()
    missing = [family for family in CORE_RESEARCH_FAMILIES if family not in present]
    return {
        "families_present": len([f for f in CORE_RESEARCH_FAMILIES if f in present]),
        "families_expected": len(CORE_RESEARCH_FAMILIES),
        "missing_families": missing,
        "complete": not missing,
    }


def save_scorecard_snapshot(
    job: dict[str, Any],
    scorecard: pd.DataFrame,
    *,
    policy: dict[str, Any] | None = None,
    output_dir: str | Path = EVIDENCE_DIR,
) -> dict[str, str]:
    policy = dict(policy or load_evidence_policy())
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    job_id = str(job.get("job_id") or "unknown_job")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = target / f"{job_id}_{stamp}"
    csv_path = base.with_suffix(".csv")
    json_path = base.with_suffix(".json")
    scorecard.to_csv(csv_path, index=False)
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "job_id": job_id,
        "run_kind": job.get("run_kind"),
        "policy": policy,
        "family_coverage": scorecard_family_coverage(scorecard),
        "rows": scorecard.to_dict(orient="records"),
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return {"csv_path": str(csv_path), "json_path": str(json_path)}
