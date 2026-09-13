from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .adaptive_evidence_v2820 import (
    AdaptiveEvidenceResult,
    analyze_research_job,
    load_adaptive_evidence_policy,
)

POLICY_PATH = Path("config/shared_account_policy.json")


@dataclass
class SharedAccountReplayResult:
    source_job_id: str
    config: dict[str, Any]
    comparison: pd.DataFrame
    friction_stress: pd.DataFrame
    adaptive_summary: dict[str, Any]
    static_summary: dict[str, Any]
    adaptive_ledger: pd.DataFrame
    static_ledger: pd.DataFrame
    adaptive_equity: pd.DataFrame
    static_equity: pd.DataFrame
    rejected_candidates: pd.DataFrame
    by_symbol: pd.DataFrame
    by_family: pd.DataFrame
    by_month: pd.DataFrame
    verdict: dict[str, Any]
    critique: list[str]
    integrity: dict[str, Any]


def load_shared_account_policy(path: str | Path = POLICY_PATH) -> dict[str, Any]:
    import json

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("shared-account policy must be a JSON object")
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


def _profit_factor(values: pd.Series) -> float:
    pnl = pd.to_numeric(values, errors="coerce").fillna(0.0)
    gross_profit = float(pnl[pnl > 0].sum())
    gross_loss = abs(float(pnl[pnl < 0].sum()))
    if gross_loss <= 1e-12:
        return 99.0 if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def prepare_portfolio_candidates(annotated: pd.DataFrame, mode: str) -> pd.DataFrame:
    if annotated is None or annotated.empty:
        return pd.DataFrame()
    frame = annotated.copy()
    for column in ["signal_time", "entry_time", "exit_time"]:
        frame[column] = pd.to_datetime(_series(frame, column), utc=True, errors="coerce")
    frame["symbol"] = _series(frame, "symbol", "").astype(str).str.upper()
    frame["side"] = _series(frame, "side", "").astype(str).str.upper()
    frame["research_family"] = _series(frame, "research_family", "unknown").astype(str)
    frame["strategy_name"] = _series(frame, "strategy_name", "Strategy").astype(str)
    frame["score"] = pd.to_numeric(_series(frame, "score", 0.0), errors="coerce").fillna(0.0)
    frame["confidence"] = pd.to_numeric(_series(frame, "confidence", 0.0), errors="coerce").fillna(0.0)
    frame["router_risk_multiplier"] = pd.to_numeric(
        _series(frame, "router_risk_multiplier", 0.0), errors="coerce"
    ).fillna(0.0).clip(lower=0.0, upper=2.0)
    frame["risk_pct"] = pd.to_numeric(_series(frame, "risk_pct"), errors="coerce")
    frame["pnl_pct"] = pd.to_numeric(_series(frame, "pnl_pct", 0.0), errors="coerce").fillna(0.0)
    frame["mae_pct"] = pd.to_numeric(_series(frame, "mae_pct"), errors="coerce")
    frame["benchmark_only"] = _series(frame, "benchmark_only", False).fillna(False).astype(bool)
    frame["state_available"] = _series(frame, "state_available", False).fillna(False).astype(bool)
    frame["router_match"] = _series(frame, "router_match", False).fillna(False).astype(bool)
    frame = frame.dropna(subset=["signal_time", "entry_time", "exit_time"]).copy()
    frame = frame[frame["state_available"]].copy()
    if mode == "adaptive":
        frame = frame[frame["router_match"]].copy()
        frame["allocation_weight"] = frame["router_risk_multiplier"]
    elif mode == "static":
        frame["allocation_weight"] = 1.0
    else:
        raise ValueError(f"Unsupported replay mode: {mode}")
    frame = frame[frame["entry_time"] <= frame["exit_time"]].copy().reset_index(drop=True)
    frame["candidate_id"] = [
        f"{mode}:{i}:{row.symbol}:{row.research_family}:{pd.Timestamp(row.entry_time).isoformat()}"
        for i, row in frame.iterrows()
    ]
    return frame


def _priority_sort(group: pd.DataFrame, mode: str) -> pd.DataFrame:
    work = group.copy()
    if mode == "adaptive":
        return work.sort_values(
            ["confidence", "allocation_weight", "score", "symbol", "research_family", "candidate_id"],
            ascending=[False, False, False, True, True, True],
        )
    return work.sort_values(
        ["score", "symbol", "research_family", "candidate_id"],
        ascending=[False, True, True, True],
    )


def _equity_curve(events: list[dict[str, Any]], start_equity: float) -> pd.DataFrame:
    if not events:
        return pd.DataFrame(
            [{
                "time": pd.NaT,
                "event": "START",
                "event_order": -1,
                "equity_usd": start_equity,
                "gross_exposure_usd": 0.0,
                "open_risk_usd": 0.0,
                "open_positions": 0,
            }]
        )
    frame = pd.DataFrame(events).sort_values(["time", "event_order"]).reset_index(drop=True)
    start_time = frame["time"].min()
    start = pd.DataFrame(
        [{
            "time": start_time,
            "event": "START",
            "event_order": -1,
            "equity_usd": start_equity,
            "gross_exposure_usd": 0.0,
            "open_risk_usd": 0.0,
            "open_positions": 0,
            "long_exposure_usd": 0.0,
            "short_exposure_usd": 0.0,
        }]
    )
    out = pd.concat([start, frame], ignore_index=True).sort_values(["time", "event_order"]).reset_index(drop=True)
    out["equity_peak_usd"] = out["equity_usd"].cummax()
    out["drawdown_usd"] = out["equity_usd"] - out["equity_peak_usd"]
    out["drawdown_pct"] = np.where(
        out["equity_peak_usd"] > 0,
        out["drawdown_usd"] / out["equity_peak_usd"] * 100.0,
        0.0,
    )
    return out


def _summary(
    *,
    mode: str,
    candidates: pd.DataFrame,
    ledger: pd.DataFrame,
    rejected: pd.DataFrame,
    curve: pd.DataFrame,
    start_equity: float,
    extra_friction_bps: float,
    peaks: dict[str, float],
) -> dict[str, Any]:
    ending = float(curve.iloc[-1]["equity_usd"]) if not curve.empty else start_equity
    pnl = pd.to_numeric(_series(ledger, "pnl_usd", 0.0), errors="coerce").fillna(0.0)
    notional = pd.to_numeric(_series(ledger, "notional_usd", 0.0), errors="coerce").fillna(0.0)
    total_candidates = int(len(candidates))
    accepted = int(len(ledger))
    max_dd_usd = abs(float(pd.to_numeric(_series(curve, "drawdown_usd", 0.0), errors="coerce").min())) if not curve.empty else 0.0
    max_dd_pct = abs(float(pd.to_numeric(_series(curve, "drawdown_pct", 0.0), errors="coerce").min())) if not curve.empty else 0.0
    by_symbol = ledger.groupby("symbol")["pnl_usd"].sum() if not ledger.empty else pd.Series(dtype=float)
    by_month = ledger.groupby("period_month")["pnl_usd"].sum() if not ledger.empty else pd.Series(dtype=float)
    by_family_notional = ledger.groupby("research_family")["notional_usd"].sum() if not ledger.empty else pd.Series(dtype=float)
    family_concentration = (
        float(by_family_notional.max() / by_family_notional.sum())
        if len(by_family_notional) and float(by_family_notional.sum()) > 0
        else 0.0
    )
    return {
        "mode": mode,
        "starting_equity_usd": round(start_equity, 2),
        "ending_equity_usd": round(ending, 2),
        "net_pnl_usd": round(ending - start_equity, 2),
        "return_pct": round(((ending / start_equity) - 1.0) * 100.0, 4) if start_equity > 0 else 0.0,
        "candidate_trades": total_candidates,
        "accepted_trades": accepted,
        "rejected_trades": int(len(rejected)),
        "acceptance_rate_pct": round(accepted / total_candidates * 100.0, 2) if total_candidates else 0.0,
        "win_rate": round(float((pnl > 0).mean() * 100.0), 2) if accepted else 0.0,
        "profit_factor": round(float(_profit_factor(pnl)), 4) if accepted else 0.0,
        "max_realized_drawdown_usd": round(max_dd_usd, 2),
        "max_realized_drawdown_pct": round(max_dd_pct, 4),
        "capital_turnover_x": round(float(notional.sum() / start_equity), 4) if start_equity > 0 else 0.0,
        "avg_notional_usd": round(float(notional.mean()), 2) if accepted else 0.0,
        "max_concurrent_positions": int(peaks.get("max_concurrent_positions", 0)),
        "max_gross_exposure_pct": round(float(peaks.get("max_gross_exposure_pct", 0.0)), 4),
        "max_open_risk_pct": round(float(peaks.get("max_open_risk_pct", 0.0)), 4),
        "max_same_direction_exposure_pct": round(float(peaks.get("max_same_direction_exposure_pct", 0.0)), 4),
        "active_symbols": int(len(by_symbol)),
        "positive_symbol_share": round(float((by_symbol > 0).mean()), 4) if len(by_symbol) else 0.0,
        "active_months": int(len(by_month)),
        "positive_month_share": round(float((by_month > 0).mean()), 4) if len(by_month) else 0.0,
        "active_families": int(len(by_family_notional)),
        "family_notional_concentration": round(family_concentration, 4),
        "benchmark_trade_accepted": bool(_series(ledger, "benchmark_only", False).fillna(False).astype(bool).any()) if accepted else False,
        "extra_round_trip_friction_bps": round(float(extra_friction_bps), 2),
        "realized_drawdown_only": True,
    }


def simulate_shared_account(
    candidates: pd.DataFrame,
    *,
    mode: str,
    policy: dict[str, Any] | None = None,
    extra_friction_bps: float = 0.0,
) -> dict[str, Any]:
    policy = dict(policy or load_shared_account_policy())
    start_equity = max(1.0, _num(policy.get("starting_equity_usd"), 10000.0))
    frame = candidates.copy()
    if "allocation_weight" not in frame.columns or "candidate_id" not in frame.columns:
        frame = prepare_portfolio_candidates(frame, mode)
    if frame.empty:
        curve = _equity_curve([], start_equity)
        summary = _summary(
            mode=mode,
            candidates=frame,
            ledger=pd.DataFrame(),
            rejected=pd.DataFrame(),
            curve=curve,
            start_equity=start_equity,
            extra_friction_bps=extra_friction_bps,
            peaks={},
        )
        return {"summary": summary, "ledger": pd.DataFrame(), "equity_curve": curve, "rejected": pd.DataFrame()}

    base_risk_pct = max(0.0, _num(policy.get("base_risk_per_trade_pct"), 0.5))
    max_total_risk_pct = max(0.0, _num(policy.get("max_total_open_risk_pct"), 2.0))
    max_position_pct = max(0.0, _num(policy.get("max_position_notional_pct"), 40.0))
    max_symbol_pct = max(0.0, _num(policy.get("max_symbol_notional_pct"), 40.0))
    max_gross_pct = max(0.0, _num(policy.get("max_gross_exposure_pct"), 100.0))
    max_same_side_pct = max(0.0, _num(policy.get("max_same_direction_exposure_pct"), 70.0))
    max_positions = max(1, int(policy.get("max_concurrent_positions", 3)))
    max_family_positions = max(1, int(policy.get("max_positions_per_family", 2)))
    min_notional = max(0.0, _num(policy.get("min_position_notional_usd"), 25.0))
    one_per_symbol = bool(policy.get("one_position_per_symbol", True))

    frame = frame.sort_values(["entry_time", "signal_time", "candidate_id"]).reset_index(drop=True)
    active: list[dict[str, Any]] = []
    ledger_rows: list[dict[str, Any]] = []
    rejected_rows: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    equity = start_equity
    peaks = {
        "max_concurrent_positions": 0,
        "max_gross_exposure_pct": 0.0,
        "max_open_risk_pct": 0.0,
        "max_same_direction_exposure_pct": 0.0,
    }

    def exposures() -> tuple[float, float, dict[str, float], dict[str, float], dict[str, int]]:
        gross = float(sum(float(p["notional_usd"]) for p in active))
        risk = float(sum(float(p["risk_usd"]) for p in active))
        side_gross: dict[str, float] = {}
        symbol_gross: dict[str, float] = {}
        family_count: dict[str, int] = {}
        for position in active:
            side_gross[position["side"]] = side_gross.get(position["side"], 0.0) + float(position["notional_usd"])
            symbol_gross[position["symbol"]] = symbol_gross.get(position["symbol"], 0.0) + float(position["notional_usd"])
            family_count[position["research_family"]] = family_count.get(position["research_family"], 0) + 1
        return gross, risk, side_gross, symbol_gross, family_count

    def record_event(ts: pd.Timestamp, event: str, order: int) -> None:
        gross, risk, side_gross, _, _ = exposures()
        events.append(
            {
                "time": ts,
                "event": event,
                "event_order": order,
                "equity_usd": round(equity, 6),
                "gross_exposure_usd": round(gross, 6),
                "open_risk_usd": round(risk, 6),
                "open_positions": int(len(active)),
                "long_exposure_usd": round(side_gross.get("LONG", 0.0), 6),
                "short_exposure_usd": round(side_gross.get("SHORT", 0.0), 6),
            }
        )

    def close_positions(up_to: pd.Timestamp) -> None:
        nonlocal equity, active
        due = sorted([p for p in active if p["exit_time"] <= up_to], key=lambda p: (p["exit_time"], p["candidate_id"]))
        for position in due:
            active = [p for p in active if p["candidate_id"] != position["candidate_id"]]
            extra_cost = float(position["notional_usd"]) * float(extra_friction_bps) / 10000.0
            pnl_usd = float(position["notional_usd"]) * float(position["pnl_pct"]) / 100.0 - extra_cost
            equity_before = equity
            equity += pnl_usd
            row = dict(position)
            row.update(
                {
                    "pnl_usd": round(pnl_usd, 6),
                    "extra_friction_usd": round(extra_cost, 6),
                    "equity_before_exit_usd": round(equity_before, 6),
                    "equity_after_exit_usd": round(equity, 6),
                    "period_month": pd.Timestamp(position["exit_time"]).strftime("%Y-%m"),
                    "mae_usd_proxy": round(abs(float(position["notional_usd"]) * _num(position.get("mae_pct"), 0.0) / 100.0), 6),
                }
            )
            ledger_rows.append(row)
            record_event(position["exit_time"], f"EXIT:{position['candidate_id']}", 0)

    for entry_time, group in frame.groupby("entry_time", sort=True):
        ts = pd.to_datetime(entry_time, utc=True)
        close_positions(ts)
        ordered = _priority_sort(group, mode)
        for _, candidate in ordered.iterrows():
            candidate_dict = candidate.to_dict()
            if pd.to_datetime(candidate["signal_time"], utc=True) > ts:
                rejected_rows.append({**candidate_dict, "rejection_reason": "signal_after_entry"})
                continue
            if equity <= 0:
                rejected_rows.append({**candidate_dict, "rejection_reason": "nonpositive_equity"})
                continue
            risk_pct = _num(candidate.get("risk_pct"), 0.0)
            if risk_pct <= 0:
                rejected_rows.append({**candidate_dict, "rejection_reason": "invalid_risk_pct"})
                continue
            weight = max(0.0, _num(candidate.get("allocation_weight"), 0.0 if mode == "adaptive" else 1.0))
            if weight <= 0:
                rejected_rows.append({**candidate_dict, "rejection_reason": "zero_allocation_weight"})
                continue

            gross, open_risk, side_gross, symbol_gross, family_count = exposures()
            if len(active) >= max_positions:
                rejected_rows.append({**candidate_dict, "rejection_reason": "max_concurrent_positions"})
                continue
            if one_per_symbol and symbol_gross.get(candidate["symbol"], 0.0) > 0:
                rejected_rows.append({**candidate_dict, "rejection_reason": "symbol_already_open"})
                continue
            if family_count.get(candidate["research_family"], 0) >= max_family_positions:
                rejected_rows.append({**candidate_dict, "rejection_reason": "max_positions_per_family"})
                continue

            desired_risk = equity * base_risk_pct / 100.0 * weight
            desired_notional = desired_risk / (risk_pct / 100.0)
            caps = {
                "position_cap": equity * max_position_pct / 100.0,
                "symbol_cap": max(0.0, equity * max_symbol_pct / 100.0 - symbol_gross.get(candidate["symbol"], 0.0)),
                "gross_cap": max(0.0, equity * max_gross_pct / 100.0 - gross),
                "same_direction_cap": max(0.0, equity * max_same_side_pct / 100.0 - side_gross.get(candidate["side"], 0.0)),
                "open_risk_cap": max(0.0, equity * max_total_risk_pct / 100.0 - open_risk) / (risk_pct / 100.0),
            }
            limiting_name, limiting_cap = min(caps.items(), key=lambda item: item[1])
            notional = max(0.0, min(desired_notional, limiting_cap))
            if notional < min_notional:
                rejected_rows.append(
                    {
                        **candidate_dict,
                        "rejection_reason": f"capital_limit:{limiting_name}",
                        "desired_notional_usd": round(desired_notional, 6),
                        "available_notional_usd": round(limiting_cap, 6),
                    }
                )
                continue

            actual_risk = notional * risk_pct / 100.0
            position = {
                **candidate_dict,
                "entry_equity_usd": round(equity, 6),
                "notional_usd": round(notional, 6),
                "risk_usd": round(actual_risk, 6),
                "risk_budget_pct_equity": round(actual_risk / equity * 100.0, 6) if equity > 0 else 0.0,
                "desired_notional_usd": round(desired_notional, 6),
                "limiting_cap": limiting_name if notional + 1e-9 < desired_notional else "desired_risk",
            }
            active.append(position)
            gross_after, risk_after, side_after, _, _ = exposures()
            peaks["max_concurrent_positions"] = max(peaks["max_concurrent_positions"], len(active))
            if equity > 0:
                peaks["max_gross_exposure_pct"] = max(peaks["max_gross_exposure_pct"], gross_after / equity * 100.0)
                peaks["max_open_risk_pct"] = max(peaks["max_open_risk_pct"], risk_after / equity * 100.0)
                peaks["max_same_direction_exposure_pct"] = max(
                    peaks["max_same_direction_exposure_pct"],
                    max(side_after.values() or [0.0]) / equity * 100.0,
                )
            record_event(ts, f"ENTRY:{candidate['candidate_id']}", 1)

    if active:
        for exit_time in sorted({p["exit_time"] for p in active}):
            close_positions(pd.to_datetime(exit_time, utc=True))

    ledger = pd.DataFrame(ledger_rows)
    rejected = pd.DataFrame(rejected_rows)
    curve = _equity_curve(events, start_equity)
    summary = _summary(
        mode=mode,
        candidates=frame,
        ledger=ledger,
        rejected=rejected,
        curve=curve,
        start_equity=start_equity,
        extra_friction_bps=extra_friction_bps,
        peaks=peaks,
    )
    return {"summary": summary, "ledger": ledger, "equity_curve": curve, "rejected": rejected}


def _contribution(frame: pd.DataFrame, group_col: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=[group_col, "trades", "notional_usd", "pnl_usd", "win_rate"])
    work = frame.copy()
    grouped = work.groupby(group_col, as_index=False).agg(
        trades=("candidate_id", "size"),
        notional_usd=("notional_usd", "sum"),
        pnl_usd=("pnl_usd", "sum"),
        win_rate=("pnl_usd", lambda s: float((pd.to_numeric(s, errors="coerce").fillna(0.0) > 0).mean() * 100.0)),
        avg_risk_usd=("risk_usd", "mean"),
    )
    for col in ["notional_usd", "pnl_usd", "avg_risk_usd", "win_rate"]:
        grouped[col] = pd.to_numeric(grouped[col], errors="coerce").round(2)
    return grouped.sort_values("pnl_usd", ascending=False).reset_index(drop=True)


def evaluate_shared_account_evidence(
    evidence: AdaptiveEvidenceResult,
    *,
    policy: dict[str, Any] | None = None,
) -> SharedAccountReplayResult:
    policy = dict(policy or load_shared_account_policy())
    annotated = evidence.annotated_trades.copy() if isinstance(evidence.annotated_trades, pd.DataFrame) else pd.DataFrame()
    static_candidates = prepare_portfolio_candidates(annotated, "static")
    adaptive_candidates = prepare_portfolio_candidates(annotated, "adaptive")
    static_run = simulate_shared_account(static_candidates, mode="static", policy=policy)
    adaptive_run = simulate_shared_account(adaptive_candidates, mode="adaptive", policy=policy)

    friction_rows: list[dict[str, Any]] = []
    for extra_bps in policy.get("friction_stress_round_trip_bps") or [0, 5, 10, 20, 40]:
        static_stress = simulate_shared_account(static_candidates, mode="static", policy=policy, extra_friction_bps=float(extra_bps))
        adaptive_stress = simulate_shared_account(adaptive_candidates, mode="adaptive", policy=policy, extra_friction_bps=float(extra_bps))
        s = static_stress["summary"]
        a = adaptive_stress["summary"]
        friction_rows.append(
            {
                "extra_round_trip_bps": float(extra_bps),
                "static_return_pct": s["return_pct"],
                "adaptive_return_pct": a["return_pct"],
                "adaptive_minus_static_pct_points": round(float(a["return_pct"]) - float(s["return_pct"]), 4),
                "static_profit_factor": s["profit_factor"],
                "adaptive_profit_factor": a["profit_factor"],
                "static_ending_equity_usd": s["ending_equity_usd"],
                "adaptive_ending_equity_usd": a["ending_equity_usd"],
            }
        )
    friction = pd.DataFrame(friction_rows)

    adaptive_summary = adaptive_run["summary"]
    static_summary = static_run["summary"]
    ledger = adaptive_run["ledger"]
    by_symbol = _contribution(ledger, "symbol")
    by_family = _contribution(ledger, "research_family")
    by_month = _contribution(ledger, "period_month")

    return_uplift = float(adaptive_summary["return_pct"]) - float(static_summary["return_pct"])
    pf_uplift = float(adaptive_summary["profit_factor"]) - float(static_summary["profit_factor"])
    required_stress = _num(policy.get("required_positive_friction_stress_bps"), 10.0)
    stress_rows = friction[friction["extra_round_trip_bps"] >= required_stress]
    stress_row = stress_rows.sort_values("extra_round_trip_bps").iloc[0] if not stress_rows.empty else None
    stress_survives = bool(
        stress_row is not None
        and _num(stress_row.get("adaptive_return_pct")) > 0
        and _num(stress_row.get("adaptive_profit_factor")) >= 1.0
    )

    rejection_share = (
        float(adaptive_summary["rejected_trades"]) / float(adaptive_summary["candidate_trades"])
        if adaptive_summary["candidate_trades"]
        else 1.0
    )
    source_verdict = str((evidence.verdict or {}).get("verdict") or "")
    checks = {
        "source_integrity": bool((evidence.integrity or {}).get("ok", False)),
        "source_not_rejected": source_verdict not in {"reject", "no_adaptive_evidence"},
        "sample_size": int(adaptive_summary["accepted_trades"]) >= int(policy.get("min_accepted_trades", 60)),
        "active_symbols": int(adaptive_summary["active_symbols"]) >= int(policy.get("min_active_symbols", 2)),
        "active_months": int(adaptive_summary["active_months"]) >= int(policy.get("min_active_months", 6)),
        "active_families": int(adaptive_summary["active_families"]) >= int(policy.get("min_active_families", 2)),
        "positive_return": _num(adaptive_summary["return_pct"]) > 0,
        "profit_factor": _num(adaptive_summary["profit_factor"]) >= _num(policy.get("min_profit_factor"), 1.15),
        "drawdown_control": _num(adaptive_summary["max_realized_drawdown_pct"], 999.0) <= _num(policy.get("max_realized_drawdown_pct"), 12.0),
        "pair_stability": _num(adaptive_summary["positive_symbol_share"]) >= _num(policy.get("min_positive_symbol_share"), 0.67),
        "month_stability": _num(adaptive_summary["positive_month_share"]) >= _num(policy.get("min_positive_month_share"), 0.55),
        "family_concentration": _num(adaptive_summary["family_notional_concentration"]) <= _num(policy.get("max_family_notional_concentration"), 0.75),
        "capital_feasibility": rejection_share <= _num(policy.get("max_capital_rejection_share"), 0.70),
        "adaptive_value": (
            return_uplift >= _num(policy.get("min_return_uplift_pct_points"), 0.5)
            or pf_uplift >= _num(policy.get("min_profit_factor_uplift"), 0.05)
        ),
        "friction_survival": stress_survives,
    }
    benchmark_block = bool(adaptive_summary.get("benchmark_trade_accepted"))
    hard_negative = int(adaptive_summary["accepted_trades"]) >= 30 and (
        _num(adaptive_summary["return_pct"]) <= 0 or _num(adaptive_summary["profit_factor"]) < 1.0
    )
    static_better = (
        int(adaptive_summary["accepted_trades"]) >= int(policy.get("min_accepted_trades", 60))
        and return_uplift < 0
        and pf_uplift <= 0
    )

    if not checks["source_integrity"]:
        verdict_name = "invalid_evidence"
    elif int(adaptive_summary["accepted_trades"]) == 0:
        verdict_name = "no_portfolio_evidence"
    elif hard_negative:
        verdict_name = "reject"
    elif static_better:
        verdict_name = "static_baseline_better"
    elif all(checks.values()) and not benchmark_block:
        verdict_name = "shared_account_edge_candidate"
    elif _num(adaptive_summary["return_pct"]) > 0 and _num(adaptive_summary["profit_factor"]) > 1.0:
        verdict_name = "promising_research_only"
    else:
        verdict_name = "insufficient_evidence"

    if verdict_name == "shared_account_edge_candidate":
        next_step = "Freeze the complete strategy/router/portfolio policy and run walk-forward plus genuinely future holdout validation."
    elif verdict_name == "static_baseline_better":
        next_step = "Do not promote the adaptive router; investigate why routing destroys value versus the same account running static candidates."
    elif verdict_name == "reject":
        next_step = "Reject or redesign this adaptive portfolio hypothesis before paper testing."
    elif verdict_name == "invalid_evidence":
        next_step = "Repair source timing/data integrity and rerun the historical evidence."
    else:
        next_step = "Keep this in research; improve independent evidence before any paper or live decision."

    verdict = {
        "version": str(policy.get("version") or "28.21"),
        "verdict": verdict_name,
        "promotion_blocked": bool(benchmark_block or verdict_name != "shared_account_edge_candidate"),
        "benchmark_trade_accepted": benchmark_block,
        "source_adaptive_verdict": source_verdict,
        "checks": checks,
        "checks_passed": int(sum(bool(v) for v in checks.values())),
        "checks_total": int(len(checks)),
        "adaptive_return_pct": adaptive_summary["return_pct"],
        "static_return_pct": static_summary["return_pct"],
        "return_uplift_pct_points": round(return_uplift, 4),
        "adaptive_profit_factor": adaptive_summary["profit_factor"],
        "static_profit_factor": static_summary["profit_factor"],
        "profit_factor_uplift": round(pf_uplift, 4),
        "adaptive_ending_equity_usd": adaptive_summary["ending_equity_usd"],
        "adaptive_max_realized_drawdown_pct": adaptive_summary["max_realized_drawdown_pct"],
        "adaptive_accepted_trades": adaptive_summary["accepted_trades"],
        "adaptive_candidate_trades": adaptive_summary["candidate_trades"],
        "adaptive_rejection_share": round(rejection_share, 4),
        "required_positive_friction_stress_bps": required_stress,
        "next_step": next_step,
    }

    critique = [
        "This is a shared-account replay over independently simulated historical trade paths. It is capital-aware, but it is not exchange/order-book execution simulation.",
        "Position selection uses only information available by entry time; realized PnL is never used to rank same-time candidates.",
        "Portfolio drawdown is based on realized equity at exits. Intratrade mark-to-market drawdown can be worse and is not yet synchronized across open positions.",
        "Crypto correlation is approximated with a same-direction gross-exposure cap, not a dynamic covariance or factor-risk model.",
        "Leverage and liquidation are deliberately excluded. The default gross-exposure cap is conservative until the edge survives stronger validation.",
    ]
    if rejection_share > 0.40:
        critique.append(f"Capital constraints rejected {rejection_share:.0%} of adaptive candidates; capacity/arbitration is materially affecting results.")
    if static_better:
        critique.append("The same shared account performs better without adaptive routing. More market-state sophistication is currently reducing economic value.")
    if benchmark_block:
        critique.append("At least one accepted adaptive trade came from a benchmark-only strategy, so production-oriented promotion remains blocked.")
    if not checks["friction_survival"]:
        critique.append(f"The account does not remain positive at the required +{required_stress:g} bps round-trip friction stress.")
    if adaptive_summary["max_concurrent_positions"] > 1:
        critique.append("Concurrent positions are now explicitly funded from one account; summed independent-strategy PnL is no longer treated as deployable equity.")

    rejected = pd.concat(
        [
            static_run["rejected"].assign(replay_mode="static") if not static_run["rejected"].empty else pd.DataFrame(),
            adaptive_run["rejected"].assign(replay_mode="adaptive") if not adaptive_run["rejected"].empty else pd.DataFrame(),
        ],
        ignore_index=True,
    )

    return SharedAccountReplayResult(
        source_job_id=str(evidence.job_id or ""),
        config=policy,
        comparison=pd.DataFrame([static_summary, adaptive_summary]),
        friction_stress=friction,
        adaptive_summary=adaptive_summary,
        static_summary=static_summary,
        adaptive_ledger=adaptive_run["ledger"],
        static_ledger=static_run["ledger"],
        adaptive_equity=adaptive_run["equity_curve"],
        static_equity=static_run["equity_curve"],
        rejected_candidates=rejected,
        by_symbol=by_symbol,
        by_family=by_family,
        by_month=by_month,
        verdict=verdict,
        critique=critique,
        integrity=dict(evidence.integrity or {}),
    )


def analyze_shared_account_job(
    job: dict[str, Any],
    *,
    adaptive_policy: dict[str, Any] | None = None,
    portfolio_policy: dict[str, Any] | None = None,
) -> SharedAccountReplayResult:
    evidence = analyze_research_job(
        job,
        policy=dict(adaptive_policy or load_adaptive_evidence_policy()),
    )
    return evaluate_shared_account_evidence(
        evidence,
        policy=dict(portfolio_policy or load_shared_account_policy()),
    )
