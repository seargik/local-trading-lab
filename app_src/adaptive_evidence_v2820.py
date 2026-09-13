from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
from typing import Any, Callable

import numpy as np
import pandas as pd

from .backtest_core import load_saved_backtest
from .evidence_review_v2813 import completed_research_jobs
from .historical_backfill import audit_store_integrity
from .market_state_replay_v2817 import run_market_state_replay
from .strategy_family_registry_v2811 import resolve_entry

POLICY_PATH = Path("config/adaptive_evidence_policy.json")
CORE_FAMILIES = ["trend_pullback", "compression_breakout", "range_reversion"]


@dataclass
class AdaptiveEvidenceResult:
    job_id: str
    family_comparison: pd.DataFrame
    pooled_comparison: pd.DataFrame
    wait_analysis: pd.DataFrame
    friction_stress: pd.DataFrame
    stability_by_symbol: pd.DataFrame
    stability_by_month: pd.DataFrame
    annotated_trades: pd.DataFrame
    concurrency: dict[str, Any]
    integrity: dict[str, Any]
    verdict: dict[str, Any]
    critique: list[str]


def load_adaptive_evidence_policy(path: str | Path = POLICY_PATH) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("adaptive evidence policy must be a JSON object")
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


def _series(frame: pd.DataFrame, column: str, default: Any = np.nan) -> pd.Series:
    if column in frame.columns:
        return frame[column]
    return pd.Series(default, index=frame.index)


def _profit_factor(pnl: pd.Series) -> float:
    values = pd.to_numeric(pnl, errors="coerce").fillna(0.0)
    gross_profit = float(values[values > 0].sum())
    gross_loss = abs(float(values[values < 0].sum()))
    if gross_loss <= 1e-12:
        return 99.0 if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def _max_drawdown(pnl: pd.Series, times: pd.Series | None = None) -> float:
    frame = pd.DataFrame({"pnl": pd.to_numeric(pnl, errors="coerce").fillna(0.0)})
    if times is not None:
        frame["time"] = pd.to_datetime(times, utc=True, errors="coerce")
        frame = frame.sort_values("time", na_position="last")
    equity = frame["pnl"].cumsum()
    peak = equity.cummax().clip(lower=0.0)
    drawdown = equity - peak
    return abs(float(drawdown.min())) if len(drawdown) else 0.0


def _metric_row(frame: pd.DataFrame, *, label: str, pnl_col: str, stake_col: str, weight_col: str | None = None) -> dict[str, Any]:
    if frame is None or frame.empty:
        return {
            "scenario": label,
            "trades": 0,
            "win_rate": 0.0,
            "total_pnl_usd": 0.0,
            "profit_factor": 0.0,
            "expectancy_bps_per_capital_turn": 0.0,
            "expectancy_r": 0.0,
            "max_drawdown_usd": 0.0,
            "drawdown_to_net_profit": None,
            "capital_turns_usd": 0.0,
        }
    pnl = pd.to_numeric(frame[pnl_col], errors="coerce").fillna(0.0)
    stake = pd.to_numeric(frame[stake_col], errors="coerce").fillna(0.0).clip(lower=0.0)
    total_pnl = float(pnl.sum())
    capital_turns = float(stake.sum())
    dd = _max_drawdown(pnl, frame["exit_time"] if "exit_time" in frame.columns else None)
    r = pd.to_numeric(_series(frame, "r_multiple", 0.0), errors="coerce").fillna(0.0)
    if weight_col and weight_col in frame.columns:
        weights = pd.to_numeric(frame[weight_col], errors="coerce").fillna(0.0).clip(lower=0.0)
        expectancy_r = float(np.average(r, weights=weights)) if float(weights.sum()) > 0 else 0.0
    else:
        expectancy_r = float(r.mean()) if len(r) else 0.0
    return {
        "scenario": label,
        "trades": int(len(frame)),
        "win_rate": round(float((pnl > 0).mean() * 100.0), 2),
        "total_pnl_usd": round(total_pnl, 2),
        "profit_factor": round(float(_profit_factor(pnl)), 4),
        "expectancy_bps_per_capital_turn": round((total_pnl / capital_turns) * 10000.0, 2) if capital_turns > 0 else 0.0,
        "expectancy_r": round(expectancy_r, 4),
        "max_drawdown_usd": round(dd, 2),
        "drawdown_to_net_profit": round(dd / total_pnl, 4) if total_pnl > 0 else None,
        "capital_turns_usd": round(capital_turns, 2),
    }


def _normalize_saved_trades(run: dict[str, Any], family: str, meta: dict[str, Any]) -> pd.DataFrame:
    trades = run.get("trades") if isinstance(run, dict) else None
    if trades is None or not isinstance(trades, pd.DataFrame) or trades.empty:
        return pd.DataFrame()
    frame = trades.copy()
    manifest = dict(run.get("manifest") or {})
    config = dict(manifest.get("config") or {})
    fixed_stake = max(0.01, _num(config.get("fixed_stake_usd"), 100.0))
    for col in ["signal_time", "entry_time", "exit_time"]:
        frame[col] = pd.to_datetime(_series(frame, col), utc=True, errors="coerce")
    frame = frame.dropna(subset=["signal_time", "entry_time", "exit_time"]).copy()
    frame["symbol"] = _series(frame, "symbol", "").astype(str).str.upper()
    frame["side"] = _series(frame, "side", "").astype(str).str.upper()
    frame["pnl_pct"] = pd.to_numeric(_series(frame, "pnl_pct", 0.0), errors="coerce").fillna(0.0)
    frame["execution_cost_pct"] = pd.to_numeric(_series(frame, "execution_cost_pct", 0.0), errors="coerce").fillna(0.0)
    raw = pd.to_numeric(_series(frame, "raw_pnl_pct"), errors="coerce")
    frame["raw_pnl_pct"] = raw.fillna(frame["pnl_pct"] + frame["execution_cost_pct"])
    frame["risk_pct"] = pd.to_numeric(_series(frame, "risk_pct"), errors="coerce").replace(0, np.nan)
    frame["r_multiple"] = frame["pnl_pct"] / frame["risk_pct"]
    frame["fixed_stake_usd"] = fixed_stake
    frame["static_stake_usd"] = fixed_stake
    frame["static_pnl_usd"] = fixed_stake * frame["pnl_pct"] / 100.0
    frame["pre_friction_pnl_usd"] = fixed_stake * frame["raw_pnl_pct"] / 100.0
    frame["execution_cost_usd"] = fixed_stake * frame["execution_cost_pct"] / 100.0
    frame["research_family"] = family
    frame["strategy_name"] = str(meta.get("strategy_name") or manifest.get("name") or "Strategy")
    frame["benchmark_only"] = bool(meta.get("benchmark_only", False))
    timing_version = str(config.get("timing_integrity_version") or "")
    if not timing_version and "timing_integrity_version" in frame.columns and frame["timing_integrity_version"].notna().any():
        timing_version = str(frame.loc[frame["timing_integrity_version"].notna(), "timing_integrity_version"].iloc[0])
    frame["timing_integrity_version"] = timing_version
    return frame.reset_index(drop=True)


def _run_meta(run: dict[str, Any]) -> dict[str, Any]:
    manifest = dict(run.get("manifest") or {})
    payload = dict(manifest.get("strategy_payload") or {})
    name = str(payload.get("strategy_name") or manifest.get("name") or "Strategy")
    reg = resolve_entry(name)
    family = str(payload.get("research_family") or reg.get("strategy_family") or "unknown")
    return {
        "strategy_name": name,
        "research_family": family,
        "benchmark_only": bool(payload.get("benchmark_only", False) or reg.get("benchmark_only", False)),
        "registry_priority": int(reg.get("priority") or 999),
        "registry_key": str(reg.get("registry_key") or ""),
    }


def _representative_runs(job: dict[str, Any], loader: Callable[[str | Path], dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    candidates: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    warnings: list[str] = []
    for item in job.get("results") or []:
        run_dir = str(item.get("run_dir") or "")
        if not run_dir:
            continue
        try:
            run = loader(run_dir)
        except Exception as exc:
            warnings.append(f"Could not load saved run {run_dir}: {exc}")
            continue
        meta = _run_meta(run)
        family = meta["research_family"]
        if family in CORE_FAMILIES:
            candidates.setdefault(family, []).append((meta, run))
    chosen: dict[str, dict[str, Any]] = {}
    for family in CORE_FAMILIES:
        rows = candidates.get(family) or []
        if not rows:
            warnings.append(f"No completed saved run for {family}.")
            continue
        rows.sort(key=lambda item: (item[0]["registry_priority"], item[0]["strategy_name"].lower()))
        meta, run = rows[0]
        chosen[family] = {"meta": meta, "run": run}
        if len(rows) > 1:
            warnings.append(f"{family} had {len(rows)} runs; representative chosen deterministically by registry priority, not by PnL.")
    return chosen, warnings


def annotate_trades_with_router(trades: pd.DataFrame, replay_history: pd.DataFrame, *, family: str, policy: dict[str, Any]) -> pd.DataFrame:
    if trades.empty:
        return trades.copy()
    frame = trades.copy()
    replay = replay_history.copy()
    if replay.empty:
        frame["decision_time"] = pd.NaT
        frame["state_available"] = False
        frame["router_match"] = False
        frame["router_risk_multiplier"] = 0.0
        frame["adaptive_stake_usd"] = 0.0
        frame["adaptive_pnl_usd"] = 0.0
        frame["adaptive_execution_cost_usd"] = 0.0
        return frame

    replay["symbol"] = _series(replay, "symbol", "").astype(str).str.upper()
    replay["decision_time"] = pd.to_datetime(_series(replay, "decision_time"), utc=True, errors="coerce")
    replay = replay.dropna(subset=["decision_time"]).sort_values(["decision_time", "symbol"]).reset_index(drop=True)
    frame["signal_time"] = pd.to_datetime(_series(frame, "signal_time"), utc=True, errors="coerce")
    frame["symbol"] = _series(frame, "symbol", "").astype(str).str.upper()
    frame = frame.dropna(subset=["signal_time"]).sort_values(["signal_time", "symbol"]).reset_index(drop=True)

    keep = [
        "symbol", "decision_time", "market_state", "preferred_strategy_family", "router_action",
        "router_direction", "risk_multiplier", "confidence", "lookahead_ok", "state_label",
    ]
    joined = pd.merge_asof(
        frame,
        replay[[c for c in keep if c in replay.columns]],
        left_on="signal_time",
        right_on="decision_time",
        by="symbol",
        direction="backward",
        allow_exact_matches=True,
    )
    joined["state_age_hours"] = (joined["signal_time"] - joined["decision_time"]).dt.total_seconds() / 3600.0
    if "lookahead_ok" not in joined.columns:
        joined["lookahead_ok"] = True
    if "preferred_strategy_family" not in joined.columns:
        joined["preferred_strategy_family"] = ""
    if "router_action" not in joined.columns:
        joined["router_action"] = ""
    if "router_direction" not in joined.columns:
        joined["router_direction"] = ""
    if "risk_multiplier" not in joined.columns:
        joined["risk_multiplier"] = 0.0
    max_age = max(0.0, _num(policy.get("max_state_age_hours"), 8.0))
    joined["state_available"] = (
        joined["decision_time"].notna()
        & joined["state_age_hours"].ge(0)
        & joined["state_age_hours"].le(max_age)
        & joined["lookahead_ok"].fillna(False).astype(bool)
    )
    eligible_actions = {str(x) for x in (policy.get("eligible_router_actions") or ["TRADE_CANDIDATE"])}
    family_match = joined["preferred_strategy_family"].astype(str).eq(family)
    action_match = joined["router_action"].astype(str).isin(eligible_actions)
    direction_match = joined["router_direction"].astype(str).str.upper().eq(_series(joined, "side", "").astype(str).str.upper())
    if not bool(policy.get("require_direction_match", True)):
        direction_match = pd.Series(True, index=joined.index)
    joined["router_match"] = joined["state_available"] & family_match & action_match & direction_match
    joined["router_risk_multiplier"] = pd.to_numeric(joined["risk_multiplier"], errors="coerce").fillna(0.0).clip(lower=0.0, upper=2.0)
    joined.loc[~joined["router_match"], "router_risk_multiplier"] = 0.0
    joined["adaptive_stake_usd"] = pd.to_numeric(_series(joined, "static_stake_usd", 0.0), errors="coerce").fillna(0.0) * joined["router_risk_multiplier"]
    joined["adaptive_pnl_usd"] = pd.to_numeric(_series(joined, "static_pnl_usd", 0.0), errors="coerce").fillna(0.0) * joined["router_risk_multiplier"]
    joined["adaptive_execution_cost_usd"] = pd.to_numeric(_series(joined, "execution_cost_usd", 0.0), errors="coerce").fillna(0.0) * joined["router_risk_multiplier"]
    joined["period_month"] = pd.to_datetime(_series(joined, "exit_time"), utc=True, errors="coerce").dt.strftime("%Y-%m")
    return joined.reset_index(drop=True)


def _stability(frame: pd.DataFrame, group_col: str, pnl_col: str) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=[group_col, "trades", "total_pnl_usd", "win_rate"])
    work = frame.copy()
    work[pnl_col] = pd.to_numeric(work[pnl_col], errors="coerce").fillna(0.0)
    out = work.groupby(group_col, as_index=False).agg(
        trades=(pnl_col, "size"),
        total_pnl_usd=(pnl_col, "sum"),
        win_rate=(pnl_col, lambda s: float((s > 0).mean() * 100.0)),
    )
    out["total_pnl_usd"] = out["total_pnl_usd"].round(2)
    out["win_rate"] = out["win_rate"].round(2)
    return out.sort_values("total_pnl_usd", ascending=False).reset_index(drop=True)


def _concurrency(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"trades": 0, "max_concurrent": 0, "entries_while_other_trade_open": 0, "overlap_entry_share": 0.0}
    work = frame.dropna(subset=["entry_time", "exit_time"]).copy().sort_values("entry_time")
    events: list[tuple[pd.Timestamp, int]] = []
    active_exits: list[pd.Timestamp] = []
    overlap_entries = 0
    for _, row in work.iterrows():
        entry = pd.to_datetime(row["entry_time"], utc=True)
        exit_time = pd.to_datetime(row["exit_time"], utc=True)
        active_exits = [x for x in active_exits if x > entry]
        if active_exits:
            overlap_entries += 1
        active_exits.append(exit_time)
        events.append((entry, 1))
        events.append((exit_time, -1))
    events.sort(key=lambda item: (item[0], item[1]))
    active = 0
    max_active = 0
    for _, delta in events:
        active += delta
        max_active = max(max_active, active)
    return {
        "trades": int(len(work)),
        "max_concurrent": int(max_active),
        "entries_while_other_trade_open": int(overlap_entries),
        "overlap_entry_share": round(float(overlap_entries / len(work)), 4) if len(work) else 0.0,
    }


def _integrity_for_job(job: dict[str, Any], family_runs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    timing_versions: dict[str, str] = {}
    timing_ok = True
    for family, payload in family_runs.items():
        manifest = dict(payload["run"].get("manifest") or {})
        config = dict(manifest.get("config") or {})
        version = str(config.get("timing_integrity_version") or "")
        timing_versions[family] = version
        timing_ok = timing_ok and version == "28.18"
    if len(family_runs) < len(CORE_FAMILIES):
        timing_ok = False

    source_root = str(job.get("source_root") or "")
    symbols = [str(s).upper() for s in (job.get("symbols") or [])]
    timeframe = str(job.get("analysis_timeframe") or "4h")
    data_rows: list[dict[str, Any]] = []
    data_ok = bool(source_root and symbols)
    for symbol in symbols:
        try:
            audit = audit_store_integrity(
                symbol,
                timeframe,
                start=job.get("start_date"),
                end=job.get("end_date"),
                store_root=source_root,
            )
            ok = bool(audit.get("continuity_ok")) and int(audit.get("rows") or 0) > 0
            data_ok = data_ok and ok
            data_rows.append({
                "symbol": symbol,
                "interval": timeframe,
                "rows": int(audit.get("rows") or 0),
                "gap_count": int(audit.get("gap_count") or 0),
                "unclosed_rows": int(audit.get("unclosed_rows") or 0),
                "ok": ok,
            })
        except Exception as exc:
            data_ok = False
            data_rows.append({"symbol": symbol, "interval": timeframe, "rows": 0, "gap_count": None, "unclosed_rows": None, "ok": False, "error": str(exc)})
    return {
        "timing_integrity_ok": bool(timing_ok),
        "timing_versions": timing_versions,
        "data_integrity_ok": bool(data_ok),
        "data_audit": data_rows,
        "ok": bool(timing_ok and data_ok),
    }


def evaluate_adaptive_trade_frames(
    family_frames: dict[str, pd.DataFrame],
    replay_history: pd.DataFrame,
    *,
    family_meta: dict[str, dict[str, Any]] | None = None,
    policy: dict[str, Any] | None = None,
    integrity: dict[str, Any] | None = None,
) -> AdaptiveEvidenceResult:
    policy = dict(policy or load_adaptive_evidence_policy())
    family_meta = dict(family_meta or {})
    integrity = dict(integrity or {"timing_integrity_ok": True, "data_integrity_ok": True, "ok": True})
    annotated_parts: list[pd.DataFrame] = []
    family_rows: list[dict[str, Any]] = []
    wait_rows: list[dict[str, Any]] = []
    critique: list[str] = []

    for family in [str(x) for x in (policy.get("core_families") or CORE_FAMILIES)]:
        frame = family_frames.get(family, pd.DataFrame())
        if frame.empty:
            critique.append(f"{family}: no usable trade evidence in the selected research job.")
            continue
        annotated = annotate_trades_with_router(frame, replay_history, family=family, policy=policy)
        annotated_parts.append(annotated)
        selected = annotated[annotated["router_match"].fillna(False)].copy()
        static_metrics = _metric_row(annotated, label="static", pnl_col="static_pnl_usd", stake_col="static_stake_usd")
        adaptive_metrics = _metric_row(selected, label="adaptive", pnl_col="adaptive_pnl_usd", stake_col="adaptive_stake_usd", weight_col="router_risk_multiplier")
        available = annotated[annotated["state_available"].fillna(False)].copy()
        excluded = available[~available["router_match"].fillna(False)].copy()
        excluded_pnl = pd.to_numeric(_series(excluded, "static_pnl_usd", 0.0), errors="coerce").fillna(0.0)
        meta = family_meta.get(family) or {}
        benchmark_only = bool(meta.get("benchmark_only", False) or (_series(annotated, "benchmark_only", False).fillna(False).astype(bool).any()))
        family_rows.append({
            "research_family": family,
            "strategy_name": str(meta.get("strategy_name") or annotated["strategy_name"].iloc[0]),
            "benchmark_only": benchmark_only,
            "static_trades": static_metrics["trades"],
            "static_pnl_usd": static_metrics["total_pnl_usd"],
            "static_profit_factor": static_metrics["profit_factor"],
            "static_expectancy_bps": static_metrics["expectancy_bps_per_capital_turn"],
            "static_max_drawdown_usd": static_metrics["max_drawdown_usd"],
            "state_available_trades": int(len(available)),
            "adaptive_trades": adaptive_metrics["trades"],
            "selection_rate_pct": round((len(selected) / len(available)) * 100.0, 2) if len(available) else 0.0,
            "adaptive_pnl_usd": adaptive_metrics["total_pnl_usd"],
            "adaptive_profit_factor": adaptive_metrics["profit_factor"],
            "adaptive_expectancy_bps": adaptive_metrics["expectancy_bps_per_capital_turn"],
            "adaptive_max_drawdown_usd": adaptive_metrics["max_drawdown_usd"],
            "pf_uplift": round(adaptive_metrics["profit_factor"] - static_metrics["profit_factor"], 4),
            "expectancy_uplift_bps": round(adaptive_metrics["expectancy_bps_per_capital_turn"] - static_metrics["expectancy_bps_per_capital_turn"], 2),
        })
        wait_rows.append({
            "research_family": family,
            "state_available_trades": int(len(available)),
            "selected_trades": int(len(selected)),
            "wait_or_rejected_trades": int(len(excluded)),
            "unmatched_no_state_trades": int((~annotated["state_available"].fillna(False)).sum()),
            "avoided_losing_trades": int((excluded_pnl < 0).sum()),
            "avoided_loss_usd": round(abs(float(excluded_pnl[excluded_pnl < 0].sum())), 2),
            "missed_winning_trades": int((excluded_pnl > 0).sum()),
            "missed_profit_usd": round(float(excluded_pnl[excluded_pnl > 0].sum()), 2),
            "excluded_net_pnl_usd": round(float(excluded_pnl.sum()), 2),
            "wait_value_usd": round(float(-excluded_pnl.sum()), 2),
        })

    annotated_all = pd.concat(annotated_parts, ignore_index=True) if annotated_parts else pd.DataFrame()
    selected_all = annotated_all[annotated_all["router_match"].fillna(False)].copy() if not annotated_all.empty else pd.DataFrame()
    static_pool = _metric_row(annotated_all, label="static_pool", pnl_col="static_pnl_usd", stake_col="static_stake_usd")
    adaptive_pool = _metric_row(selected_all, label="adaptive_router", pnl_col="adaptive_pnl_usd", stake_col="adaptive_stake_usd", weight_col="router_risk_multiplier")
    pooled = pd.DataFrame([static_pool, adaptive_pool])

    friction_rows: list[dict[str, Any]] = []
    for extra_bps in policy.get("friction_stress_round_trip_bps") or [0, 5, 10, 20, 40]:
        stress = selected_all.copy()
        if stress.empty:
            stress_pnl = pd.Series(dtype=float)
        else:
            extra_cost = pd.to_numeric(stress["adaptive_stake_usd"], errors="coerce").fillna(0.0) * (float(extra_bps) / 10000.0)
            stress["stress_pnl_usd"] = pd.to_numeric(stress["adaptive_pnl_usd"], errors="coerce").fillna(0.0) - extra_cost
            stress_pnl = stress["stress_pnl_usd"]
        friction_rows.append({
            "extra_round_trip_bps": float(extra_bps),
            "trades": int(len(stress)),
            "total_pnl_usd": round(float(stress_pnl.sum()), 2) if len(stress_pnl) else 0.0,
            "profit_factor": round(float(_profit_factor(stress_pnl)), 4) if len(stress_pnl) else 0.0,
            "win_rate": round(float((stress_pnl > 0).mean() * 100.0), 2) if len(stress_pnl) else 0.0,
        })
    friction_stress = pd.DataFrame(friction_rows)

    by_symbol = _stability(selected_all, "symbol", "adaptive_pnl_usd") if not selected_all.empty else pd.DataFrame()
    by_month = _stability(selected_all, "period_month", "adaptive_pnl_usd") if not selected_all.empty else pd.DataFrame()
    concurrency = _concurrency(selected_all)

    break_even_bps = float(adaptive_pool["expectancy_bps_per_capital_turn"])
    positive_symbol_share = float((by_symbol["total_pnl_usd"] > 0).mean()) if len(by_symbol) else 0.0
    positive_month_share = float((by_month["total_pnl_usd"] > 0).mean()) if len(by_month) else 0.0
    family_counts = selected_all.groupby("research_family").size() if not selected_all.empty else pd.Series(dtype=float)
    family_concentration = float(family_counts.max() / family_counts.sum()) if len(family_counts) and float(family_counts.sum()) > 0 else 0.0
    benchmark_selected = bool(_series(selected_all, "benchmark_only", False).fillna(False).astype(bool).any()) if not selected_all.empty else False
    pf_uplift = float(adaptive_pool["profit_factor"] - static_pool["profit_factor"])
    expectancy_uplift = float(adaptive_pool["expectancy_bps_per_capital_turn"] - static_pool["expectancy_bps_per_capital_turn"])

    checks = {
        "timing_integrity": bool(integrity.get("timing_integrity_ok", False)),
        "data_integrity": bool(integrity.get("data_integrity_ok", False)),
        "sample_size": adaptive_pool["trades"] >= int(policy.get("min_trades_for_review", 80)),
        "active_symbols": len(by_symbol) >= int(policy.get("min_active_symbols", 2)),
        "active_months": len(by_month) >= int(policy.get("min_active_months", 6)),
        "pair_stability": positive_symbol_share >= _num(policy.get("min_positive_symbol_share"), 0.67),
        "month_stability": positive_month_share >= _num(policy.get("min_positive_month_share"), 0.55),
        "family_diversification": family_concentration <= _num(policy.get("max_family_trade_concentration"), 0.70),
        "positive_net_pnl": adaptive_pool["total_pnl_usd"] > 0,
        "profit_factor": adaptive_pool["profit_factor"] >= _num(policy.get("min_profit_factor"), 1.15),
        "drawdown_control": adaptive_pool["drawdown_to_net_profit"] is not None and adaptive_pool["drawdown_to_net_profit"] <= _num(policy.get("max_drawdown_to_net_profit"), 1.25),
        "friction_headroom": break_even_bps >= _num(policy.get("min_break_even_extra_friction_bps"), 8.0),
        "adaptive_uplift": (
            pf_uplift >= _num(policy.get("min_profit_factor_uplift_vs_static_pool"), 0.05)
            or expectancy_uplift >= _num(policy.get("min_expectancy_uplift_bps_vs_static_pool"), 2.0)
        ),
    }
    hard_negative = adaptive_pool["trades"] >= 30 and (adaptive_pool["total_pnl_usd"] <= 0 or adaptive_pool["profit_factor"] < 1.0)
    if adaptive_pool["trades"] == 0:
        verdict_name = "no_adaptive_evidence"
    elif hard_negative:
        verdict_name = "reject"
    elif all(checks.values()) and not benchmark_selected:
        verdict_name = "adaptive_edge_candidate"
    elif all(checks.values()) and benchmark_selected:
        verdict_name = "promising_research_only"
    elif adaptive_pool["total_pnl_usd"] > 0 and adaptive_pool["profit_factor"] > 1.0:
        verdict_name = "promising_research_only"
    else:
        verdict_name = "insufficient_evidence"

    if not checks["timing_integrity"]:
        critique.append("Selected saved runs are not all marked with V28.18 closed-bar timing integrity. Rerun them before trusting economic comparison.")
    if not checks["data_integrity"]:
        critique.append("The source OHLCV store does not pass the V28.19 continuity/data-integrity gate for this job window.")
    if benchmark_selected:
        critique.append("Compression evidence still uses a benchmark-only strategy; strong adaptive results remain research-only while that family is represented by a benchmark control.")
    if adaptive_pool["trades"] < int(policy.get("min_trades_for_review", 80)):
        critique.append(f"Adaptive sample is small ({adaptive_pool['trades']} trades); apparent uplift may be noise.")
    if family_concentration > _num(policy.get("max_family_trade_concentration"), 0.70):
        critique.append(f"Adaptive trades are concentrated in one family ({family_concentration:.0%}); the router may be rediscovering one strategy rather than adding adaptation value.")
    if positive_month_share < _num(policy.get("min_positive_month_share"), 0.55) and len(by_month):
        critique.append(f"Only {positive_month_share:.0%} of active months are profitable after routing; time stability is weak.")
    if break_even_bps < _num(policy.get("min_break_even_extra_friction_bps"), 8.0):
        critique.append(f"Estimated friction headroom is only {break_even_bps:.1f} bps per capital turn; this is fragile for real execution.")
    if pf_uplift < 0 and expectancy_uplift < 0:
        critique.append("The router reduces both profit factor and exposure-normalized expectancy versus the pooled static baseline; adaptation is not adding economic value in this sample.")
    if concurrency.get("max_concurrent", 0) > 1:
        critique.append(f"Selected trades overlap (max concurrency {concurrency['max_concurrent']}); summed PnL is not a capital-aware portfolio return and may overstate deployable economics.")
    if not critique:
        critique.append("No obvious structural failure was detected, but historical counterfactual evidence is still not proof of future profitability.")

    verdict = {
        "verdict": verdict_name,
        "promotion_blocked": bool(benchmark_selected or not checks["timing_integrity"] or not checks["data_integrity"]),
        "checks": checks,
        "checks_passed": int(sum(bool(v) for v in checks.values())),
        "checks_total": int(len(checks)),
        "adaptive_trades": int(adaptive_pool["trades"]),
        "adaptive_pnl_usd": adaptive_pool["total_pnl_usd"],
        "adaptive_profit_factor": adaptive_pool["profit_factor"],
        "adaptive_expectancy_bps": adaptive_pool["expectancy_bps_per_capital_turn"],
        "adaptive_max_drawdown_usd": adaptive_pool["max_drawdown_usd"],
        "break_even_extra_friction_bps": round(break_even_bps, 2),
        "profit_factor_uplift_vs_static_pool": round(pf_uplift, 4),
        "expectancy_uplift_bps_vs_static_pool": round(expectancy_uplift, 2),
        "positive_symbol_share": round(positive_symbol_share, 4),
        "positive_month_share": round(positive_month_share, 4),
        "family_trade_concentration": round(family_concentration, 4),
        "benchmark_family_selected": benchmark_selected,
        "policy_version": str(policy.get("version") or ""),
    }

    return AdaptiveEvidenceResult(
        job_id="",
        family_comparison=pd.DataFrame(family_rows),
        pooled_comparison=pooled,
        wait_analysis=pd.DataFrame(wait_rows),
        friction_stress=friction_stress,
        stability_by_symbol=by_symbol,
        stability_by_month=by_month,
        annotated_trades=annotated_all,
        concurrency=concurrency,
        integrity=integrity,
        verdict=verdict,
        critique=critique,
    )


def completed_adaptive_source_jobs() -> list[dict[str, Any]]:
    return completed_research_jobs()


def analyze_research_job(
    job: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
    loader: Callable[[str | Path], dict[str, Any]] = load_saved_backtest,
) -> AdaptiveEvidenceResult:
    policy = dict(policy or load_adaptive_evidence_policy())
    chosen, warnings = _representative_runs(job, loader)
    family_frames: dict[str, pd.DataFrame] = {}
    family_meta: dict[str, dict[str, Any]] = {}
    for family, payload in chosen.items():
        meta = dict(payload["meta"])
        family_meta[family] = meta
        family_frames[family] = _normalize_saved_trades(payload["run"], family, meta)

    integrity = _integrity_for_job(job, chosen)
    replay = run_market_state_replay(
        source_root=str(job.get("source_root") or "data/ohlcv_store"),
        symbols=[str(s).upper() for s in (job.get("symbols") or [])],
        start_date=job.get("start_date"),
        end_date=job.get("end_date"),
    )
    result = evaluate_adaptive_trade_frames(
        family_frames,
        replay.history,
        family_meta=family_meta,
        policy=policy,
        integrity=integrity,
    )
    result.job_id = str(job.get("job_id") or "")
    result.critique[:0] = warnings
    return result
