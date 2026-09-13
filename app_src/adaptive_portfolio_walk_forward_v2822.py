from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
from typing import Any

import pandas as pd

from .adaptive_evidence_v2820 import (
    AdaptiveEvidenceResult,
    CORE_FAMILIES,
    _representative_runs,
    analyze_research_job,
    load_adaptive_evidence_policy,
)
from .backtest_core import load_saved_backtest
from .shared_account_replay_v2821 import (
    evaluate_shared_account_evidence,
    load_shared_account_policy,
    prepare_portfolio_candidates,
    simulate_shared_account,
)
from .walk_forward_v2814 import build_anchored_walk_forward_folds

POLICY_PATH = Path("config/adaptive_portfolio_walk_forward_policy.json")
FREEZE_DIR = Path("data/backtest_reviews/adaptive_portfolio_freezes")
SNAPSHOT_DIR = Path("data/backtest_reviews/adaptive_portfolio_walk_forward")

POLICY_FILES = {
    "adaptive_evidence": Path("config/adaptive_evidence_policy.json"),
    "market_state_router": Path("config/market_state_router_policy.json"),
    "market_state_replay": Path("config/market_state_replay_policy.json"),
    "shared_account": Path("config/shared_account_policy.json"),
    "portfolio_walk_forward": POLICY_PATH,
}

IMPLEMENTATION_FILES = {
    "backtest_core": Path("app_src/backtest_core.py"),
    "market_state_router": Path("app_src/market_state_router_v2816.py"),
    "market_state_replay": Path("app_src/market_state_replay_v2817.py"),
    "adaptive_evidence": Path("app_src/adaptive_evidence_v2820.py"),
    "shared_account_replay": Path("app_src/shared_account_replay_v2821.py"),
    "portfolio_walk_forward": Path("app_src/adaptive_portfolio_walk_forward_v2822.py"),
}


@dataclass
class AdaptivePortfolioWalkForwardResult:
    freeze_id: str
    folds: pd.DataFrame
    aggregate_comparison: pd.DataFrame
    friction_stress: pd.DataFrame
    adaptive_equity: pd.DataFrame
    static_equity: pd.DataFrame
    adaptive_ledger: pd.DataFrame
    static_ledger: pd.DataFrame
    rejected_candidates: pd.DataFrame
    by_symbol: pd.DataFrame
    by_family: pd.DataFrame
    by_month: pd.DataFrame
    verdict: dict[str, Any]
    integrity: dict[str, Any]
    critique: list[str]


def load_portfolio_walk_forward_policy(path: str | Path = POLICY_PATH) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("adaptive portfolio walk-forward policy must be a JSON object")
    return payload


def _canonical_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return sha256(raw).hexdigest()


def _file_sha256(path: str | Path) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()


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


def policy_snapshots(paths: dict[str, Path] | None = None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, path in (paths or POLICY_FILES).items():
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        out[name] = {
            "path": str(path),
            "version": str(payload.get("version") or "") if isinstance(payload, dict) else "",
            "sha256": _canonical_hash(payload),
            "payload": payload,
        }
    return out


def implementation_fingerprints(paths: dict[str, Path] | None = None) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for name, path in (paths or IMPLEMENTATION_FILES).items():
        out[name] = {"path": str(path), "sha256": _file_sha256(path)}
    return out


def _strategy_snapshots(job: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    chosen, warnings = _representative_runs(job, load_saved_backtest)
    rows: list[dict[str, Any]] = []
    for family in CORE_FAMILIES:
        selected = chosen.get(family)
        if not selected:
            continue
        meta = dict(selected.get("meta") or {})
        run = dict(selected.get("run") or {})
        manifest = dict(run.get("manifest") or {})
        config = dict(manifest.get("config") or {})
        payload = dict(manifest.get("strategy_payload") or {})
        rows.append(
            {
                "research_family": family,
                "strategy_name": str(meta.get("strategy_name") or payload.get("strategy_name") or manifest.get("name") or "Strategy"),
                "benchmark_only": bool(meta.get("benchmark_only", False) or payload.get("benchmark_only", False)),
                "strategy_payload": payload,
                "strategy_sha256": _canonical_hash(payload),
                "source_run_dir": str(run.get("run_dir") or ""),
                "timing_integrity_version": str(config.get("timing_integrity_version") or ""),
            }
        )
    rows.sort(key=lambda row: row["research_family"])
    return rows, warnings


def _source_descriptor(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": str(job.get("job_id") or ""),
        "created_at": job.get("created_at"),
        "start_date": job.get("start_date"),
        "end_date": job.get("end_date"),
        "symbols": [str(x).upper() for x in (job.get("symbols") or [])],
        "entry_timeframe": str(job.get("entry_timeframe") or ""),
        "analysis_timeframe": str(job.get("analysis_timeframe") or ""),
        "source_root": str(job.get("source_root") or ""),
    }


def _source_result_signature(shared_result: Any) -> dict[str, Any]:
    return {
        "verdict": dict(getattr(shared_result, "verdict", {}) or {}),
        "adaptive_summary": dict(getattr(shared_result, "adaptive_summary", {}) or {}),
        "static_summary": dict(getattr(shared_result, "static_summary", {}) or {}),
    }


def build_freeze_record_from_components(
    *,
    source: dict[str, Any],
    strategy_snapshots: list[dict[str, Any]],
    policies: dict[str, dict[str, Any]],
    implementations: dict[str, dict[str, str]],
    source_shared_account_signature: dict[str, Any],
    created_at: str | None = None,
) -> dict[str, Any]:
    created = created_at or datetime.now(timezone.utc).isoformat()
    framework_payload = {
        "strategies": strategy_snapshots,
        "policies": {name: value.get("sha256", "") for name, value in sorted(policies.items())},
        "implementations": {name: value.get("sha256", "") for name, value in sorted(implementations.items())},
    }
    framework_hash = _canonical_hash(framework_payload)
    freeze_id = f"pf22_{pd.Timestamp(created).strftime('%Y%m%dT%H%M%SZ')}_{framework_hash[:10]}"
    record = {
        "schema_version": "28.22-frozen-adaptive-portfolio-v1",
        "freeze_id": freeze_id,
        "created_at": created,
        "source": source,
        "strategy_snapshots": strategy_snapshots,
        "policies": policies,
        "implementation_fingerprints": implementations,
        "source_shared_account_signature": source_shared_account_signature,
        "source_shared_account_signature_sha256": _canonical_hash(source_shared_account_signature),
        "framework_sha256": framework_hash,
        "selection_contamination_warning": True,
        "warning": "Historical walk-forward reuses a research period already involved in candidate selection. It is temporal-stability evidence, not pristine future evidence.",
    }
    record["record_sha256"] = _canonical_hash({k: v for k, v in record.items() if k != "record_sha256"})
    return record


def build_portfolio_freeze(
    job: dict[str, Any],
    *,
    shared_result: Any | None = None,
    walk_forward_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = dict(walk_forward_policy or load_portfolio_walk_forward_policy())
    adaptive_policy = load_adaptive_evidence_policy()
    shared_policy = load_shared_account_policy()
    if shared_result is None:
        evidence = analyze_research_job(job, policy=adaptive_policy)
        shared_result = evaluate_shared_account_evidence(evidence, policy=shared_policy)
    required = str(policy.get("require_source_verdict") or "shared_account_edge_candidate")
    actual = str((shared_result.verdict or {}).get("verdict") or "")
    if actual != required:
        raise ValueError(f"V28.22 freeze requires source verdict {required}; got {actual or 'unknown'}.")
    strategies, warnings = _strategy_snapshots(job)
    if len(strategies) < len(CORE_FAMILIES):
        raise ValueError("Cannot freeze: representative strategy snapshots are missing for one or more core families.")
    if any(str(row.get("timing_integrity_version") or "") != "28.18" for row in strategies):
        raise ValueError("Cannot freeze: representative source strategies are not all V28.18 timing-integrity runs.")
    record = build_freeze_record_from_components(
        source=_source_descriptor(job),
        strategy_snapshots=strategies,
        policies=policy_snapshots(),
        implementations=implementation_fingerprints(),
        source_shared_account_signature=_source_result_signature(shared_result),
    )
    record["source_warnings"] = warnings
    record["record_sha256"] = _canonical_hash({k: v for k, v in record.items() if k != "record_sha256"})
    return record


def save_portfolio_freeze(record: dict[str, Any], output_dir: str | Path = FREEZE_DIR) -> Path:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    freeze_id = str(record.get("freeze_id") or "portfolio_freeze")
    path = root / f"{freeze_id}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def load_portfolio_freeze(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("portfolio freeze must be a JSON object")
    return payload


def list_portfolio_freezes(output_dir: str | Path = FREEZE_DIR) -> list[dict[str, Any]]:
    root = Path(output_dir)
    if not root.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), reverse=True):
        try:
            record = load_portfolio_freeze(path)
            rows.append({"path": str(path), "record": record})
        except Exception:
            continue
    return rows


def verify_portfolio_freeze(
    record: dict[str, Any],
    *,
    check_current: bool = True,
    source_job: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stored_record_hash = str(record.get("record_sha256") or "")
    calculated_record_hash = _canonical_hash({k: v for k, v in record.items() if k != "record_sha256"})
    record_ok = bool(stored_record_hash and stored_record_hash == calculated_record_hash)

    framework_payload = {
        "strategies": record.get("strategy_snapshots") or [],
        "policies": {name: value.get("sha256", "") for name, value in sorted((record.get("policies") or {}).items())},
        "implementations": {name: value.get("sha256", "") for name, value in sorted((record.get("implementation_fingerprints") or {}).items())},
    }
    framework_ok = str(record.get("framework_sha256") or "") == _canonical_hash(framework_payload)
    signature_ok = str(record.get("source_shared_account_signature_sha256") or "") == _canonical_hash(record.get("source_shared_account_signature") or {})

    policy_ok = True
    implementation_ok = True
    strategy_ok = True
    drift: list[str] = []

    if check_current:
        try:
            current_policies = policy_snapshots()
            for name, frozen in (record.get("policies") or {}).items():
                current = current_policies.get(name) or {}
                if str(current.get("sha256") or "") != str(frozen.get("sha256") or ""):
                    policy_ok = False
                    drift.append(f"policy:{name}")
        except Exception as exc:
            policy_ok = False
            drift.append(f"policy_check_error:{exc}")
        try:
            current_impl = implementation_fingerprints()
            for name, frozen in (record.get("implementation_fingerprints") or {}).items():
                current = current_impl.get(name) or {}
                if str(current.get("sha256") or "") != str(frozen.get("sha256") or ""):
                    implementation_ok = False
                    drift.append(f"implementation:{name}")
        except Exception as exc:
            implementation_ok = False
            drift.append(f"implementation_check_error:{exc}")

    if source_job is not None:
        try:
            current_strategies, _ = _strategy_snapshots(source_job)
            frozen_map = {str(x.get("research_family")): str(x.get("strategy_sha256") or "") for x in (record.get("strategy_snapshots") or [])}
            current_map = {str(x.get("research_family")): str(x.get("strategy_sha256") or "") for x in current_strategies}
            strategy_ok = frozen_map == current_map
            if not strategy_ok:
                drift.append("strategy_payloads")
            frozen_job_id = str((record.get("source") or {}).get("job_id") or "")
            if frozen_job_id and frozen_job_id != str(source_job.get("job_id") or ""):
                strategy_ok = False
                drift.append("source_job_id")
        except Exception as exc:
            strategy_ok = False
            drift.append(f"strategy_check_error:{exc}")

    return {
        "record_integrity_ok": record_ok,
        "framework_integrity_ok": framework_ok,
        "source_signature_integrity_ok": signature_ok,
        "policy_integrity_ok": policy_ok,
        "implementation_integrity_ok": implementation_ok,
        "strategy_integrity_ok": strategy_ok,
        "drift": drift,
        "ok": bool(record_ok and framework_ok and signature_ok and policy_ok and implementation_ok and strategy_ok),
    }


def _slice_by_entry(frame: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    if frame is None or frame.empty or "entry_time" not in frame.columns:
        return pd.DataFrame()
    work = frame.copy()
    ts = pd.to_datetime(work["entry_time"], utc=True, errors="coerce")
    start = pd.to_datetime(start_date, utc=True, errors="coerce")
    end_exclusive = pd.to_datetime(end_date, utc=True, errors="coerce") + pd.Timedelta(days=1)
    return work.loc[ts.ge(start) & ts.lt(end_exclusive)].copy().reset_index(drop=True)


def _union_test_slices(frame: pd.DataFrame, folds: list[dict[str, str]]) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    parts = [_slice_by_entry(frame, fold["test_start"], fold["test_end"]) for fold in folds]
    parts = [part for part in parts if not part.empty]
    if not parts:
        return pd.DataFrame(columns=frame.columns)
    out = pd.concat(parts, ignore_index=True)
    dedupe = [c for c in ["symbol", "research_family", "strategy_name", "signal_time", "entry_time", "exit_time", "side"] if c in out.columns]
    return out.drop_duplicates(subset=dedupe if dedupe else None).reset_index(drop=True)


def _contribution(frame: pd.DataFrame, group_col: str) -> pd.DataFrame:
    if frame is None or frame.empty or group_col not in frame.columns:
        return pd.DataFrame(columns=[group_col, "trades", "notional_usd", "pnl_usd", "win_rate"])
    work = frame.copy()
    work["pnl_usd"] = pd.to_numeric(work.get("pnl_usd"), errors="coerce").fillna(0.0)
    work["notional_usd"] = pd.to_numeric(work.get("notional_usd"), errors="coerce").fillna(0.0)
    out = work.groupby(group_col, as_index=False).agg(
        trades=("pnl_usd", "size"),
        notional_usd=("notional_usd", "sum"),
        pnl_usd=("pnl_usd", "sum"),
        win_rate=("pnl_usd", lambda s: float((s > 0).mean() * 100.0)),
    )
    for col in ["notional_usd", "pnl_usd", "win_rate"]:
        out[col] = pd.to_numeric(out[col], errors="coerce").round(2)
    return out.sort_values("pnl_usd", ascending=False).reset_index(drop=True)


def _fold_runs(
    annotated: pd.DataFrame,
    fold: dict[str, str],
    *,
    shared_policy: dict[str, Any],
    walk_policy: dict[str, Any],
) -> dict[str, Any]:
    train = _slice_by_entry(annotated, fold["train_start"], fold["train_end"])
    test = _slice_by_entry(annotated, fold["test_start"], fold["test_end"])

    train_static = simulate_shared_account(prepare_portfolio_candidates(train, "static"), mode="static", policy=shared_policy)
    train_adaptive = simulate_shared_account(prepare_portfolio_candidates(train, "adaptive"), mode="adaptive", policy=shared_policy)
    test_static = simulate_shared_account(prepare_portfolio_candidates(test, "static"), mode="static", policy=shared_policy)
    test_adaptive = simulate_shared_account(prepare_portfolio_candidates(test, "adaptive"), mode="adaptive", policy=shared_policy)

    required_bps = _num(walk_policy.get("required_positive_friction_stress_bps"), 10.0)
    test_stress = simulate_shared_account(
        prepare_portfolio_candidates(test, "adaptive"),
        mode="adaptive",
        policy=shared_policy,
        extra_friction_bps=required_bps,
    )
    ta = test_adaptive["summary"]
    ts = test_static["summary"]
    stress = test_stress["summary"]
    return_uplift = _num(ta.get("return_pct")) - _num(ts.get("return_pct"))
    pf_uplift = _num(ta.get("profit_factor")) - _num(ts.get("profit_factor"))
    sample_ok = int(ta.get("accepted_trades") or 0) >= int(walk_policy.get("min_test_trades_per_fold", 8))
    economic_ok = (
        _num(ta.get("return_pct")) > 0
        and _num(ta.get("profit_factor")) >= _num(walk_policy.get("min_fold_profit_factor"), 1.0)
        and _num(ta.get("max_realized_drawdown_pct")) <= _num(walk_policy.get("max_fold_realized_drawdown_pct"), 15.0)
    )
    friction_ok = _num(stress.get("return_pct")) > 0 and _num(stress.get("profit_factor")) >= 1.0
    return {
        **fold,
        "framework_sha256": None,
        "train_adaptive_trades": int(train_adaptive["summary"].get("accepted_trades") or 0),
        "train_adaptive_return_pct": _num(train_adaptive["summary"].get("return_pct")),
        "train_adaptive_profit_factor": _num(train_adaptive["summary"].get("profit_factor")),
        "test_adaptive_trades": int(ta.get("accepted_trades") or 0),
        "test_adaptive_return_pct": _num(ta.get("return_pct")),
        "test_static_return_pct": _num(ts.get("return_pct")),
        "test_return_uplift_pct_points": round(return_uplift, 4),
        "test_adaptive_profit_factor": _num(ta.get("profit_factor")),
        "test_static_profit_factor": _num(ts.get("profit_factor")),
        "test_profit_factor_uplift": round(pf_uplift, 4),
        "test_adaptive_realized_drawdown_pct": _num(ta.get("max_realized_drawdown_pct")),
        "required_friction_bps": required_bps,
        "stressed_adaptive_return_pct": _num(stress.get("return_pct")),
        "stressed_adaptive_profit_factor": _num(stress.get("profit_factor")),
        "sample_ok": sample_ok,
        "economic_ok": economic_ok,
        "friction_ok": friction_ok,
        "adaptive_outperformed_static": bool(return_uplift >= 0),
        "fold_pass": bool(sample_ok and economic_ok and friction_ok),
    }


def evaluate_frozen_portfolio_walk_forward(
    evidence: AdaptiveEvidenceResult,
    freeze_record: dict[str, Any],
    *,
    walk_forward_policy: dict[str, Any] | None = None,
    freeze_verification: dict[str, Any] | None = None,
) -> AdaptivePortfolioWalkForwardResult:
    walk_policy = dict(walk_forward_policy or (freeze_record.get("policies") or {}).get("portfolio_walk_forward", {}).get("payload") or load_portfolio_walk_forward_policy())
    shared_policy = dict((freeze_record.get("policies") or {}).get("shared_account", {}).get("payload") or load_shared_account_policy())
    verification = dict(freeze_verification or verify_portfolio_freeze(freeze_record, check_current=False))
    source = dict(freeze_record.get("source") or {})
    folds = build_anchored_walk_forward_folds(
        source.get("start_date"),
        source.get("end_date"),
        initial_train_months=int(walk_policy.get("initial_train_months", 6)),
        test_months=int(walk_policy.get("test_months", 2)),
        max_folds=int(walk_policy.get("max_folds", 3)),
        min_train_days=int(walk_policy.get("min_train_days", 120)),
        min_test_days=int(walk_policy.get("min_test_days", 30)),
    )
    annotated = evidence.annotated_trades.copy() if isinstance(evidence.annotated_trades, pd.DataFrame) else pd.DataFrame()
    fold_rows: list[dict[str, Any]] = []
    for fold in folds:
        row = _fold_runs(annotated, fold, shared_policy=shared_policy, walk_policy=walk_policy)
        row["framework_sha256"] = freeze_record.get("framework_sha256")
        fold_rows.append(row)
    fold_table = pd.DataFrame(fold_rows)

    oos = _union_test_slices(annotated, folds)
    static_candidates = prepare_portfolio_candidates(oos, "static")
    adaptive_candidates = prepare_portfolio_candidates(oos, "adaptive")
    static_run = simulate_shared_account(static_candidates, mode="static", policy=shared_policy)
    adaptive_run = simulate_shared_account(adaptive_candidates, mode="adaptive", policy=shared_policy)
    static_summary = static_run["summary"]
    adaptive_summary = adaptive_run["summary"]
    comparison = pd.DataFrame([static_summary, adaptive_summary])

    friction_rows: list[dict[str, Any]] = []
    for bps in walk_policy.get("friction_stress_round_trip_bps") or [0, 5, 10, 20, 40]:
        s = simulate_shared_account(static_candidates, mode="static", policy=shared_policy, extra_friction_bps=float(bps))["summary"]
        a = simulate_shared_account(adaptive_candidates, mode="adaptive", policy=shared_policy, extra_friction_bps=float(bps))["summary"]
        friction_rows.append(
            {
                "extra_round_trip_bps": float(bps),
                "static_return_pct": _num(s.get("return_pct")),
                "adaptive_return_pct": _num(a.get("return_pct")),
                "return_uplift_pct_points": round(_num(a.get("return_pct")) - _num(s.get("return_pct")), 4),
                "static_profit_factor": _num(s.get("profit_factor")),
                "adaptive_profit_factor": _num(a.get("profit_factor")),
                "adaptive_ending_equity_usd": _num(a.get("ending_equity_usd")),
            }
        )
    friction = pd.DataFrame(friction_rows)

    ledger = adaptive_run["ledger"]
    by_symbol = _contribution(ledger, "symbol")
    by_family = _contribution(ledger, "research_family")
    by_month = _contribution(ledger, "period_month")

    fold_pass_share = float(fold_table["fold_pass"].mean()) if not fold_table.empty else 0.0
    outperform_share = float(fold_table["adaptive_outperformed_static"].mean()) if not fold_table.empty else 0.0
    positive_symbol_share = float((by_symbol["pnl_usd"] > 0).mean()) if not by_symbol.empty else 0.0
    positive_month_share = float((by_month["pnl_usd"] > 0).mean()) if not by_month.empty else 0.0
    aggregate_return_uplift = _num(adaptive_summary.get("return_pct")) - _num(static_summary.get("return_pct"))
    aggregate_pf_uplift = _num(adaptive_summary.get("profit_factor")) - _num(static_summary.get("profit_factor"))
    required_bps = _num(walk_policy.get("required_positive_friction_stress_bps"), 10.0)
    stress_candidates = friction[friction["extra_round_trip_bps"] >= required_bps]
    stress_row = stress_candidates.sort_values("extra_round_trip_bps").iloc[0] if not stress_candidates.empty else None
    friction_survival = bool(
        stress_row is not None
        and _num(stress_row.get("adaptive_return_pct")) > 0
        and _num(stress_row.get("adaptive_profit_factor")) >= 1.0
    )
    benchmark_accepted = bool(adaptive_summary.get("benchmark_trade_accepted", False))

    checks = {
        "freeze_integrity": bool(verification.get("ok", False)),
        "source_evidence_integrity": bool((evidence.integrity or {}).get("ok", False)),
        "enough_folds": len(fold_table) >= int(walk_policy.get("min_folds", 3)),
        "fold_pass_share": fold_pass_share >= _num(walk_policy.get("min_positive_fold_share"), 0.67),
        "adaptive_outperform_fold_share": outperform_share >= _num(walk_policy.get("min_adaptive_outperform_fold_share"), 0.67),
        "aggregate_sample_size": int(adaptive_summary.get("accepted_trades") or 0) >= int(walk_policy.get("min_aggregate_oos_trades", 30)),
        "aggregate_positive_return": _num(adaptive_summary.get("return_pct")) > _num(walk_policy.get("min_aggregate_oos_return_pct"), 0.0),
        "aggregate_profit_factor": _num(adaptive_summary.get("profit_factor")) >= _num(walk_policy.get("min_aggregate_oos_profit_factor"), 1.1),
        "aggregate_drawdown_control": _num(adaptive_summary.get("max_realized_drawdown_pct")) <= _num(walk_policy.get("max_aggregate_oos_realized_drawdown_pct"), 12.0),
        "aggregate_adaptive_uplift": aggregate_return_uplift >= _num(walk_policy.get("min_aggregate_return_uplift_pct_points"), 0.0),
        "pair_stability": positive_symbol_share >= _num(walk_policy.get("min_positive_symbol_share"), 0.67),
        "month_stability": positive_month_share >= _num(walk_policy.get("min_positive_month_share"), 0.5),
        "friction_survival": friction_survival,
        "benchmark_clear": not benchmark_accepted if bool(walk_policy.get("block_if_benchmark_accepted_oos", True)) else True,
    }

    insufficient = len(fold_table) < int(walk_policy.get("min_folds", 3)) or int(adaptive_summary.get("accepted_trades") or 0) < int(walk_policy.get("min_aggregate_oos_trades", 30))
    hard_negative = int(adaptive_summary.get("accepted_trades") or 0) >= 10 and (
        _num(adaptive_summary.get("return_pct")) <= 0 or _num(adaptive_summary.get("profit_factor")) < 1.0
    )
    static_better = aggregate_return_uplift < 0 and aggregate_pf_uplift <= 0
    if not checks["freeze_integrity"] or not checks["source_evidence_integrity"]:
        verdict_name = "invalid_freeze_or_evidence"
    elif insufficient:
        verdict_name = "insufficient_walk_forward"
    elif hard_negative:
        verdict_name = "fail"
    elif static_better:
        verdict_name = "static_baseline_better_oos"
    elif all(checks.values()):
        verdict_name = "pass_for_future_freeze"
    else:
        verdict_name = "mixed"

    if verdict_name == "pass_for_future_freeze":
        next_step = "Create a new tamper-evident freeze before genuinely future data and validate this exact adaptive portfolio prospectively."
    elif verdict_name == "static_baseline_better_oos":
        next_step = "Do not promote adaptive routing; the simpler shared-account baseline is economically stronger across the OOS folds."
    elif verdict_name == "fail":
        next_step = "Reject this frozen adaptive portfolio version or redesign it, then restart validation with a new framework hash."
    elif verdict_name == "invalid_freeze_or_evidence":
        next_step = "Repair integrity drift or source evidence before interpreting walk-forward economics."
    else:
        next_step = "Keep the framework in research; do not start a future-holdout clock until the failed robustness gates are resolved independently."

    verdict = {
        "version": str(walk_policy.get("version") or "28.22"),
        "verdict": verdict_name,
        "promotion_blocked": verdict_name != "pass_for_future_freeze",
        "checks": checks,
        "checks_passed": int(sum(bool(v) for v in checks.values())),
        "checks_total": int(len(checks)),
        "folds": int(len(fold_table)),
        "fold_pass_share": round(fold_pass_share, 4),
        "adaptive_outperform_fold_share": round(outperform_share, 4),
        "aggregate_oos_trades": int(adaptive_summary.get("accepted_trades") or 0),
        "aggregate_adaptive_return_pct": _num(adaptive_summary.get("return_pct")),
        "aggregate_static_return_pct": _num(static_summary.get("return_pct")),
        "aggregate_return_uplift_pct_points": round(aggregate_return_uplift, 4),
        "aggregate_adaptive_profit_factor": _num(adaptive_summary.get("profit_factor")),
        "aggregate_static_profit_factor": _num(static_summary.get("profit_factor")),
        "aggregate_profit_factor_uplift": round(aggregate_pf_uplift, 4),
        "aggregate_realized_drawdown_pct": _num(adaptive_summary.get("max_realized_drawdown_pct")),
        "positive_symbol_share": round(positive_symbol_share, 4),
        "positive_month_share": round(positive_month_share, 4),
        "required_positive_friction_stress_bps": required_bps,
        "benchmark_trade_accepted_oos": benchmark_accepted,
        "framework_sha256": str(freeze_record.get("framework_sha256") or ""),
        "next_step": next_step,
    }

    critique = [
        "V28.22 freezes the full strategy/router/capital framework across folds; fold outcomes never retune thresholds, risk multipliers or arbitration.",
        "The folds are chronological OOS slices, but the broader historical research period already influenced candidate selection. This is temporal-stability evidence, not pristine future evidence.",
        "Aggregate OOS is replayed once across all non-overlapping test windows, so capital carries chronologically and overlapping positions compete for the same account.",
        "Per-fold metrics reset the account for comparability; the aggregate OOS equity curve is the stronger account-level economic view.",
        "Drawdown remains realized-equity drawdown at exits. Intratrade synchronized mark-to-market portfolio drawdown can still be worse.",
        "No leverage, liquidation model, order-book queue position or latency model is introduced here. Passing V28.22 still does not imply live profitability.",
    ]
    if static_better:
        critique.append("The adaptive account is worse than the simpler static shared-account baseline on both return and profit factor in aggregate OOS evidence.")
    if benchmark_accepted:
        critique.append("A benchmark-only compression trade was accepted in OOS replay, so production-oriented promotion is blocked.")
    if fold_pass_share < _num(walk_policy.get("min_positive_fold_share"), 0.67):
        critique.append("Too few chronological folds survive the minimum economic/friction gates; the apparent edge is not temporally stable enough.")
    if not friction_survival:
        critique.append(f"The aggregate OOS account does not survive the required +{required_bps:g} bps round-trip friction stress.")

    rejected = pd.concat(
        [
            static_run["rejected"].assign(replay_mode="static") if not static_run["rejected"].empty else pd.DataFrame(),
            adaptive_run["rejected"].assign(replay_mode="adaptive") if not adaptive_run["rejected"].empty else pd.DataFrame(),
        ],
        ignore_index=True,
    )
    integrity = {
        "freeze": verification,
        "source_evidence": dict(evidence.integrity or {}),
        "framework_sha256": str(freeze_record.get("framework_sha256") or ""),
        "record_sha256": str(freeze_record.get("record_sha256") or ""),
        "selection_contamination_warning": True,
    }
    return AdaptivePortfolioWalkForwardResult(
        freeze_id=str(freeze_record.get("freeze_id") or ""),
        folds=fold_table,
        aggregate_comparison=comparison,
        friction_stress=friction,
        adaptive_equity=adaptive_run["equity_curve"],
        static_equity=static_run["equity_curve"],
        adaptive_ledger=adaptive_run["ledger"],
        static_ledger=static_run["ledger"],
        rejected_candidates=rejected,
        by_symbol=by_symbol,
        by_family=by_family,
        by_month=by_month,
        verdict=verdict,
        integrity=integrity,
        critique=critique,
    )


def run_portfolio_walk_forward_job(
    job: dict[str, Any],
    freeze_record: dict[str, Any],
) -> AdaptivePortfolioWalkForwardResult:
    verification = verify_portfolio_freeze(freeze_record, check_current=True, source_job=job)
    frozen_policies = dict(freeze_record.get("policies") or {})
    adaptive_policy = dict((frozen_policies.get("adaptive_evidence") or {}).get("payload") or {})
    shared_policy = dict((frozen_policies.get("shared_account") or {}).get("payload") or {})
    walk_policy = dict((frozen_policies.get("portfolio_walk_forward") or {}).get("payload") or {})
    evidence = analyze_research_job(job, policy=adaptive_policy)
    shared_result = evaluate_shared_account_evidence(evidence, policy=shared_policy)
    current_signature = _source_result_signature(shared_result)
    signature_match = _canonical_hash(current_signature) == str(freeze_record.get("source_shared_account_signature_sha256") or "")
    required_source_verdict = str(walk_policy.get("require_source_verdict") or "shared_account_edge_candidate")
    if str((shared_result.verdict or {}).get("verdict") or "") != required_source_verdict:
        signature_match = False
    verification["source_shared_account_replay_ok"] = signature_match
    if not signature_match:
        verification["ok"] = False
        verification.setdefault("drift", []).append("source_shared_account_signature")
    return evaluate_frozen_portfolio_walk_forward(
        evidence,
        freeze_record,
        walk_forward_policy=walk_policy,
        freeze_verification=verification,
    )
