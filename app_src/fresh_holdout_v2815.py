from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from .backtest_core import load_saved_backtest
from .backtest_jobs import create_task_job, list_jobs
from .evidence_review_v2813 import load_evidence_policy
from .historical_backfill import summarize_store
from .settings import OHLCV_STORE_ROOT
from .walk_forward_v2814 import load_walk_forward_policy, strategy_payload_hash

POLICY_PATH = Path("config/fresh_holdout_policy.json")
FREEZE_DIR = Path("data/backtest_reviews/research_freezes")
SNAPSHOT_DIR = Path("data/backtest_reviews/fresh_holdout_scorecards")
RUN_KIND = "v28_15_fresh_holdout"
FREEZE_SCHEMA_VERSION = "v28.15-freeze-v1"


def load_fresh_holdout_policy(path: str | Path = POLICY_PATH) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("fresh holdout policy must be a JSON object")
    return payload


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


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


def _utc(value: Any) -> pd.Timestamp:
    return pd.to_datetime(value, utc=True, errors="coerce")


def _source_config(source_run: dict[str, Any]) -> dict[str, Any]:
    return dict((source_run.get("manifest") or {}).get("config") or {})


def _source_payload(source_run: dict[str, Any]) -> dict[str, Any]:
    return dict((source_run.get("manifest") or {}).get("strategy_payload") or {})


def _source_symbols(source_run: dict[str, Any]) -> list[str]:
    cfg = _source_config(source_run)
    symbols = [str(x).upper().strip() for x in (cfg.get("symbols") or []) if str(x).strip()]
    if symbols:
        return list(dict.fromkeys(symbols))
    trades = source_run.get("trades", pd.DataFrame())
    if isinstance(trades, pd.DataFrame) and not trades.empty and "symbol" in trades.columns:
        return list(dict.fromkeys(trades["symbol"].astype(str).str.upper().tolist()))
    return []


def _frozen_execution_config(source_run: dict[str, Any]) -> dict[str, Any]:
    cfg = _source_config(source_run)
    for key in [
        "symbols",
        "start_date",
        "end_date",
        "source_root",
        "entry_timeframe",
        "analysis_timeframe",
        "run_kind",
        "what_if_config",
        "base_run",
        "v28_14_walk_forward",
    ]:
        cfg.pop(key, None)
    return cfg


def freeze_record_hash(record: dict[str, Any]) -> str:
    core = dict(record or {})
    core.pop("record_sha256", None)
    return _sha256(core)


def verify_freeze_record(record: dict[str, Any]) -> dict[str, Any]:
    payload = dict(record.get("strategy_payload") or {})
    expected_payload_hash = str(record.get("strategy_sha256") or "")
    actual_payload_hash = strategy_payload_hash(payload) if payload else ""
    expected_record_hash = str(record.get("record_sha256") or "")
    actual_record_hash = freeze_record_hash(record)
    return {
        "payload_hash_match": bool(expected_payload_hash and expected_payload_hash == actual_payload_hash),
        "record_hash_match": bool(expected_record_hash and expected_record_hash == actual_record_hash),
        "actual_strategy_sha256": actual_payload_hash,
        "actual_record_sha256": actual_record_hash,
        "valid": bool(
            expected_payload_hash
            and expected_payload_hash == actual_payload_hash
            and expected_record_hash
            and expected_record_hash == actual_record_hash
        ),
    }


def build_freeze_record(
    source_run: dict[str, Any],
    walk_forward_report: dict[str, Any],
    *,
    research_family: str,
    now: datetime | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = dict(policy or load_fresh_holdout_policy())
    required_verdict = str(policy.get("freeze_source_verdict") or "pass_for_next_validation")
    actual_verdict = str(walk_forward_report.get("verdict") or "")
    if actual_verdict != required_verdict:
        raise ValueError(f"Freeze requires V28.14 verdict {required_verdict}; got {actual_verdict or 'none'}.")

    payload = _source_payload(source_run)
    if not payload:
        raise ValueError("Saved source run has no strategy payload.")
    if bool(payload.get("benchmark_only", False)):
        raise ValueError("Benchmark-only controls cannot enter the fresh holdout protocol.")

    symbols = _source_symbols(source_run)
    if not symbols:
        raise ValueError("Saved source run has no symbols.")

    cfg = _source_config(source_run)
    cutoff_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff_ts = pd.Timestamp(cutoff_dt)
    first_eligible = cutoff_ts.normalize() + pd.Timedelta(days=1)
    target_days = max(1, int(policy.get("target_holdout_days", 60)))
    target_end = first_eligible + pd.Timedelta(days=target_days - 1)
    strategy_hash = strategy_payload_hash(payload)
    freeze_id = f"fr15_{cutoff_dt.strftime('%Y%m%d_%H%M%S')}_{strategy_hash[:10]}"

    evidence_policy = load_evidence_policy()
    wf_policy = load_walk_forward_policy()
    manifest = dict(source_run.get("manifest") or {})
    record = {
        "schema_version": FREEZE_SCHEMA_VERSION,
        "freeze_id": freeze_id,
        "created_at": cutoff_dt.isoformat(),
        "cutoff_utc": cutoff_dt.isoformat(),
        "first_eligible_date": first_eligible.date().isoformat(),
        "target_holdout_days": target_days,
        "target_end_date": target_end.date().isoformat(),
        "minimum_observation_days": int(policy.get("minimum_observation_days", 30)),
        "research_family": str(research_family or "unknown"),
        "strategy_name": str(payload.get("strategy_name") or manifest.get("name") or "Strategy"),
        "strategy_payload": deepcopy(payload),
        "strategy_sha256": strategy_hash,
        "benchmark_only": bool(payload.get("benchmark_only", False)),
        "symbols": symbols,
        "entry_timeframe": str(cfg.get("entry_timeframe") or "1h"),
        "analysis_timeframe": str(cfg.get("analysis_timeframe") or "4h"),
        "source_root": str(cfg.get("source_root") or OHLCV_STORE_ROOT),
        "frozen_execution_config": _frozen_execution_config(source_run),
        "source_run_dir": str(source_run.get("run_dir") or ""),
        "source_run_name": str(manifest.get("name") or ""),
        "source_research_start": cfg.get("start_date"),
        "source_research_end": cfg.get("end_date"),
        "source_wf_id": str(walk_forward_report.get("wf_id") or ""),
        "source_wf_verdict": actual_verdict,
        "source_wf_policy_version": str(walk_forward_report.get("policy_version") or wf_policy.get("version") or ""),
        "evidence_policy_version": str(evidence_policy.get("version") or ""),
        "holdout_policy_version": str(policy.get("version") or ""),
        "selection_contamination_warning_resolved_by_future_data": True,
        "status": "frozen_waiting_for_future_data",
    }
    record["record_sha256"] = freeze_record_hash(record)
    return record


def save_freeze_record(record: dict[str, Any], freeze_dir: str | Path = FREEZE_DIR) -> Path:
    verification = verify_freeze_record(record)
    if not verification["valid"]:
        raise ValueError("Refusing to save freeze record because its integrity hash is invalid.")
    root = Path(freeze_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{record['freeze_id']}.json"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if str(existing.get("record_sha256")) != str(record.get("record_sha256")):
            raise ValueError("Freeze id already exists with different content.")
        return path
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def load_freeze_record(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def list_freeze_records(freeze_dir: str | Path = FREEZE_DIR) -> list[dict[str, Any]]:
    root = Path(freeze_dir)
    if not root.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), reverse=True):
        try:
            record = load_freeze_record(path)
            record["path"] = str(path)
            record["integrity_valid"] = bool(verify_freeze_record(record)["valid"])
            out.append(record)
        except Exception:
            continue
    return out


def holdout_readiness(
    record: dict[str, Any],
    *,
    now: datetime | None = None,
    store_root: str | Path | None = None,
) -> dict[str, Any]:
    verification = verify_freeze_record(record)
    now_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    today = pd.Timestamp(now_dt).normalize()
    first = _utc(record.get("first_eligible_date"))
    target_end = _utc(record.get("target_end_date"))
    if pd.isna(first) or pd.isna(target_end):
        return {"ready": False, "reason": "Freeze dates are invalid.", "integrity_valid": verification["valid"]}

    completed_through = today - pd.Timedelta(days=1)
    observed_end = min(completed_through, target_end)
    observed_days = max(0, int((observed_end - first).days) + 1) if observed_end >= first else 0
    target_days = int(record.get("target_holdout_days") or 0)
    remaining_days = max(0, target_days - observed_days)
    minimum_days = int(record.get("minimum_observation_days") or 0)
    minimum_observed = observed_days >= minimum_days
    calendar_complete = completed_through >= target_end

    root = Path(store_root or record.get("source_root") or OHLCV_STORE_ROOT)
    entry_tf = str(record.get("entry_timeframe") or "1h")
    data_rows: list[dict[str, Any]] = []
    data_ready = True
    for symbol in record.get("symbols") or []:
        summary = summarize_store(str(symbol), entry_tf, store_root=root)
        last_ts = _utc(summary.get("last_open_time"))
        symbol_ready = bool(not pd.isna(last_ts) and last_ts.normalize() >= target_end)
        data_ready = data_ready and symbol_ready
        data_rows.append(
            {
                "symbol": str(symbol),
                "entry_timeframe": entry_tf,
                "last_open_time": None if pd.isna(last_ts) else last_ts.isoformat(),
                "target_end_date": target_end.date().isoformat(),
                "ready": symbol_ready,
            }
        )

    ready = bool(verification["valid"] and calendar_complete and data_ready)
    if not verification["valid"]:
        reason = "Freeze integrity check failed."
    elif not calendar_complete:
        reason = f"Collecting untouched future data: {observed_days}/{target_days} full UTC days observed."
    elif not data_ready:
        reason = "Calendar window is complete, but the local OHLCV store is not fresh through the frozen target end date."
    else:
        reason = "Fresh holdout window is complete and data is ready for one frozen evaluation."
    return {
        "ready": ready,
        "reason": reason,
        "integrity_valid": verification["valid"],
        "observed_days": observed_days,
        "target_days": target_days,
        "remaining_days": remaining_days,
        "minimum_observation_days": minimum_days,
        "minimum_observed": minimum_observed,
        "calendar_complete": calendar_complete,
        "data_ready": data_ready,
        "first_eligible_date": first.date().isoformat(),
        "target_end_date": target_end.date().isoformat(),
        "data_status": pd.DataFrame(data_rows),
    }


def _active_holdout_exists(freeze_id: str, record_hash: str) -> bool:
    for job in list_jobs(["queued", "running"]):
        if str(job.get("job_type") or job.get("run_kind") or "") != RUN_KIND:
            continue
        if str(job.get("freeze_id") or "") == freeze_id and str(job.get("freeze_record_sha256") or "") == record_hash:
            return True
    return False


def queue_fresh_holdout(
    record: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
    now: datetime | None = None,
    store_root: str | Path | None = None,
    job_creator: Callable[..., Path] = create_task_job,
) -> dict[str, Any]:
    policy = dict(policy or load_fresh_holdout_policy())
    verification = verify_freeze_record(record)
    if not verification["valid"]:
        return {"queued": False, "reason": "Freeze integrity check failed."}
    readiness = holdout_readiness(record, now=now, store_root=store_root)
    if not readiness["ready"]:
        return {"queued": False, "reason": readiness["reason"], "readiness": readiness}

    freeze_id = str(record.get("freeze_id") or "")
    record_hash = str(record.get("record_sha256") or "")
    if _active_holdout_exists(freeze_id, record_hash):
        return {"queued": False, "reason": "This exact frozen holdout is already queued or running."}

    payload = deepcopy(record.get("strategy_payload") or {})
    symbols = [str(x).upper() for x in (record.get("symbols") or [])]
    tasks: list[dict[str, Any]] = []
    for symbol in symbols:
        meta = {
            "freeze_id": freeze_id,
            "freeze_record_sha256": record_hash,
            "frozen_strategy_sha256": str(record.get("strategy_sha256") or ""),
            "cutoff_utc": record.get("cutoff_utc"),
            "holdout_start": record.get("first_eligible_date"),
            "holdout_end": record.get("target_end_date"),
            "research_family": record.get("research_family"),
            "symbol": symbol,
            "role": "fresh_holdout",
        }
        tasks.append(
            {
                "name": f"V28.15 Fresh Holdout | {symbol} | {record.get('strategy_name', 'Strategy')}",
                "scenario_name": f"V28.15 {symbol} FRESH_HOLDOUT",
                "strategy_payload": deepcopy(payload),
                "symbols": [symbol],
                "task_meta": {"v28_15_fresh_holdout": meta},
                "config_overrides": {"run_kind": RUN_KIND, "v28_15_fresh_holdout": meta},
            }
        )

    source_root = str(store_root or record.get("source_root") or OHLCV_STORE_ROOT)
    path = job_creator(
        source_root=source_root,
        symbols=symbols,
        entry_timeframe=str(record.get("entry_timeframe") or "1h"),
        analysis_timeframe=str(record.get("analysis_timeframe") or "4h"),
        start_date=str(record.get("first_eligible_date")),
        end_date=str(record.get("target_end_date")),
        base_config=deepcopy(record.get("frozen_execution_config") or {}),
        tasks=tasks,
        comment="V28.15 pristine future-data holdout. Frozen payload/config; no tuning inside this window.",
        job_type=RUN_KIND,
        extra={
            "run_kind": RUN_KIND,
            "freeze_id": freeze_id,
            "freeze_record_sha256": record_hash,
            "frozen_strategy_sha256": record.get("strategy_sha256"),
            "research_family": record.get("research_family"),
            "holdout_start": record.get("first_eligible_date"),
            "holdout_end": record.get("target_end_date"),
            "holdout_policy_version": policy.get("version"),
            "fresh_future_holdout": True,
        },
    )
    return {
        "queued": True,
        "job_path": str(path),
        "tasks_created": len(tasks),
        "freeze_id": freeze_id,
        "holdout_start": record.get("first_eligible_date"),
        "holdout_end": record.get("target_end_date"),
        "readiness": readiness,
    }


def fresh_holdout_jobs(statuses: list[str] | None = None) -> list[dict[str, Any]]:
    out = []
    for job in list_jobs(statuses):
        if str(job.get("job_type") or job.get("run_kind") or "") == RUN_KIND:
            out.append(job)
    return sorted(out, key=lambda x: str(x.get("created_at") or ""), reverse=True)


def _trade_metrics(trades: pd.DataFrame | None, fixed_stake_usd: float) -> dict[str, Any]:
    if trades is None or trades.empty:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "net_pnl_usd": 0.0,
            "pre_friction_pnl_usd": 0.0,
            "execution_cost_usd": 0.0,
            "friction_drag_share": None,
            "expectancy_r": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_usd": 0.0,
        }
    work = trades.copy()
    pnl_pct = pd.to_numeric(work.get("pnl_pct"), errors="coerce").fillna(0.0)
    raw_pct = pd.to_numeric(work.get("raw_pnl_pct", work.get("pnl_pct")), errors="coerce").fillna(0.0)
    cost_pct = pd.to_numeric(work.get("execution_cost_pct", raw_pct - pnl_pct), errors="coerce").fillna(0.0)
    risk_pct = pd.to_numeric(work.get("risk_pct"), errors="coerce").replace(0, pd.NA)
    r_multiple = (pnl_pct / risk_pct).replace([float("inf"), float("-inf")], pd.NA).fillna(0.0)
    pnl_usd = fixed_stake_usd * pnl_pct / 100.0
    raw_usd = fixed_stake_usd * raw_pct / 100.0
    cost_usd = fixed_stake_usd * cost_pct / 100.0
    gross_profit = float(pnl_usd[pnl_usd > 0].sum())
    gross_loss = abs(float(pnl_usd[pnl_usd < 0].sum()))
    ordered = work.copy()
    ordered["_pnl_usd"] = pnl_usd.values
    sort_col = "exit_time" if "exit_time" in ordered.columns else ("entry_time" if "entry_time" in ordered.columns else None)
    if sort_col:
        ordered["_ts"] = pd.to_datetime(ordered[sort_col], utc=True, errors="coerce")
        ordered = ordered.sort_values("_ts")
    equity = ordered["_pnl_usd"].cumsum()
    drawdown = equity - equity.cummax()
    pre_friction = float(raw_usd.sum())
    costs = max(0.0, float(cost_usd.sum()))
    friction_share = costs / abs(pre_friction) if pre_friction > 0 else None
    return {
        "trades": int(len(work)),
        "win_rate": round(float((pnl_pct > 0).mean() * 100), 2),
        "net_pnl_usd": round(float(pnl_usd.sum()), 2),
        "pre_friction_pnl_usd": round(pre_friction, 2),
        "execution_cost_usd": round(costs, 2),
        "friction_drag_share": round(float(friction_share), 4) if friction_share is not None else None,
        "expectancy_r": round(float(r_multiple.mean()), 4),
        "profit_factor": round(float(gross_profit / gross_loss), 4) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0),
        "max_drawdown_usd": round(float(abs(drawdown.min())) if len(drawdown) else 0.0, 2),
    }


def _collect_holdout_results(
    jobs: list[dict[str, Any]],
    record: dict[str, Any],
    *,
    loader: Callable[[str | Path], dict[str, Any]] = load_saved_backtest,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    frames: list[pd.DataFrame] = []
    freeze_id = str(record.get("freeze_id") or "")
    for job in jobs:
        if str(job.get("job_type") or job.get("run_kind") or "") != RUN_KIND:
            continue
        if str(job.get("freeze_id") or "") != freeze_id:
            continue
        for result in job.get("results") or []:
            run_dir = str(result.get("run_dir") or "")
            loaded = loader(run_dir) if run_dir else {}
            manifest = dict(loaded.get("manifest") or {})
            cfg = dict(manifest.get("config") or {})
            meta = dict(cfg.get("v28_15_fresh_holdout") or (result.get("task_meta") or {}).get("v28_15_fresh_holdout") or {})
            actual_payload = dict(manifest.get("strategy_payload") or {})
            actual_hash = strategy_payload_hash(actual_payload) if actual_payload else ""
            expected_hash = str(record.get("strategy_sha256") or "")
            expected_record_hash = str(record.get("record_sha256") or "")
            freeze_hash_match = str(meta.get("freeze_record_sha256") or job.get("freeze_record_sha256") or "") == expected_record_hash
            window_match = (
                str(meta.get("holdout_start") or cfg.get("start_date") or job.get("holdout_start") or "") == str(record.get("first_eligible_date") or "")
                and str(meta.get("holdout_end") or cfg.get("end_date") or job.get("holdout_end") or "") == str(record.get("target_end_date") or "")
            )
            symbol = str(meta.get("symbol") or result.get("symbol") or "").upper()
            rows.append(
                {
                    "freeze_id": freeze_id,
                    "job_id": job.get("job_id"),
                    "symbol": symbol,
                    "run_dir": run_dir,
                    "strategy_hash_match": bool(actual_hash and actual_hash == expected_hash),
                    "freeze_hash_match": bool(freeze_hash_match),
                    "window_match": bool(window_match),
                }
            )
            trades = loaded.get("trades", pd.DataFrame())
            if isinstance(trades, pd.DataFrame) and not trades.empty:
                part = trades.copy()
                part["_holdout_symbol"] = symbol
                frames.append(part)
    return pd.DataFrame(rows), (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame())


def _group_metrics(trades: pd.DataFrame, group_col: str, fixed_stake_usd: float, min_trades: int) -> pd.DataFrame:
    if trades.empty or group_col not in trades.columns:
        return pd.DataFrame()
    rows = []
    for key, part in trades.groupby(group_col, dropna=False):
        metrics = _trade_metrics(part, fixed_stake_usd)
        rows.append({group_col: key, **metrics, "sample_ok": metrics["trades"] >= min_trades})
    return pd.DataFrame(rows)


def evaluate_fresh_holdout(
    record: dict[str, Any],
    jobs: list[dict[str, Any]],
    *,
    policy: dict[str, Any] | None = None,
    loader: Callable[[str | Path], dict[str, Any]] = load_saved_backtest,
) -> dict[str, Any]:
    policy = dict(policy or load_fresh_holdout_policy())
    verification = verify_freeze_record(record)
    result_rows, trades = _collect_holdout_results(jobs, record, loader=loader)
    fixed_stake = _num((record.get("frozen_execution_config") or {}).get("fixed_stake_usd"), 100.0)
    overall = _trade_metrics(trades, fixed_stake)
    by_symbol = _group_metrics(trades, "_holdout_symbol", fixed_stake, int(policy.get("min_trades_per_symbol", 4)))
    by_side = _group_metrics(trades, "side", fixed_stake, max(1, int(policy.get("min_trades_per_symbol", 4)) // 2)) if "side" in trades.columns else pd.DataFrame()
    if not trades.empty:
        time_col = "exit_time" if "exit_time" in trades.columns else ("entry_time" if "entry_time" in trades.columns else None)
        month_trades = trades.copy()
        if time_col:
            month_trades["_holdout_month"] = pd.to_datetime(month_trades[time_col], utc=True, errors="coerce").dt.strftime("%Y-%m")
            by_month = _group_metrics(month_trades, "_holdout_month", fixed_stake, 1)
        else:
            by_month = pd.DataFrame()
    else:
        by_month = pd.DataFrame()

    result_integrity = bool(
        not result_rows.empty
        and result_rows["strategy_hash_match"].all()
        and result_rows["freeze_hash_match"].all()
        and result_rows["window_match"].all()
    )
    symbols = [str(x).upper() for x in (record.get("symbols") or [])]
    observed_symbols = set(by_symbol.get("_holdout_symbol", pd.Series(dtype=str)).astype(str).str.upper().tolist()) if not by_symbol.empty else set()
    symbol_coverage_complete = all(symbol in observed_symbols for symbol in symbols)
    positive_symbol_share = float((by_symbol["net_pnl_usd"] > 0).mean()) if not by_symbol.empty else 0.0
    drawdown_to_profit = (overall["max_drawdown_usd"] / overall["net_pnl_usd"]) if overall["net_pnl_usd"] > 0 else None
    friction_share = overall.get("friction_drag_share")

    gates = {
        "freeze_integrity": bool(verification["valid"]),
        "result_integrity": result_integrity,
        "symbol_coverage": symbol_coverage_complete,
        "sample_size": overall["trades"] >= int(policy.get("min_total_trades", 20)),
        "positive_net_pnl": overall["net_pnl_usd"] > 0 if bool(policy.get("require_positive_net_pnl", True)) else True,
        "positive_expectancy": overall["expectancy_r"] > _num(policy.get("min_expectancy_r"), 0.0),
        "profit_factor": overall["profit_factor"] >= _num(policy.get("min_profit_factor"), 1.05),
        "symbol_stability": positive_symbol_share >= _num(policy.get("min_positive_symbol_share"), 0.67),
        "drawdown_control": drawdown_to_profit is not None and drawdown_to_profit <= _num(policy.get("max_drawdown_to_net_profit"), 1.5),
        "friction_resilience": friction_share is not None and friction_share <= _num(policy.get("max_friction_drag_share_of_gross"), 0.65),
    }
    hard_negative = overall["trades"] >= int(policy.get("min_total_trades", 20)) and (
        overall["net_pnl_usd"] <= 0 or overall["expectancy_r"] <= 0 or overall["profit_factor"] < 1.0
    )
    if not verification["valid"] or not result_integrity:
        verdict = "invalid_freeze_or_test"
    elif overall["trades"] < int(policy.get("min_total_trades", 20)):
        verdict = "insufficient_evidence"
    elif all(gates.values()):
        verdict = "fresh_holdout_pass"
    elif hard_negative:
        verdict = "fresh_holdout_fail"
    else:
        verdict = "fresh_holdout_mixed"

    warnings: list[str] = []
    if not verification["valid"]:
        warnings.append("freeze record or strategy payload hash changed")
    if not result_integrity:
        warnings.append("saved holdout result does not match the frozen payload/window/hash")
    if not symbol_coverage_complete:
        warnings.append("not every frozen symbol produced holdout trades/results")
    if overall["trades"] < int(policy.get("min_total_trades", 20)):
        warnings.append(f"small fresh sample: {overall['trades']} trades")
    if positive_symbol_share < _num(policy.get("min_positive_symbol_share"), 0.67):
        warnings.append(f"fresh pair stability weak: {positive_symbol_share:.0%} profitable")
    if drawdown_to_profit is None:
        warnings.append("drawdown/profit ratio unavailable because fresh net PnL is non-positive")
    elif not gates["drawdown_control"]:
        warnings.append(f"fresh drawdown is {drawdown_to_profit:.2f}x fresh net profit")
    if friction_share is None:
        warnings.append("friction resilience unavailable because pre-friction PnL is non-positive")
    elif not gates["friction_resilience"]:
        warnings.append(f"friction consumes {friction_share:.0%} of fresh pre-friction PnL")

    if verdict == "fresh_holdout_pass":
        next_step = "Candidate survived the first genuinely future-data holdout. Keep paper-only and begin a separate paper validation window without retuning."
    elif verdict == "fresh_holdout_fail":
        next_step = "Reject this frozen version. Any redesign must create a new freeze and a new future holdout clock."
    elif verdict == "fresh_holdout_mixed":
        next_step = "Do not retune on this holdout. Keep the frozen result as evidence and decide whether to collect a second independent future window."
    else:
        next_step = "Do not promote. Resolve integrity/sample issues without editing the frozen candidate."

    return {
        "freeze_id": record.get("freeze_id"),
        "verdict": verdict,
        "gates": gates,
        "gates_passed": int(sum(bool(v) for v in gates.values())),
        "gates_total": len(gates),
        "overall": overall,
        "positive_symbol_share": round(positive_symbol_share, 4),
        "drawdown_to_net_profit": round(float(drawdown_to_profit), 4) if drawdown_to_profit is not None else None,
        "freeze_integrity": verification,
        "warnings": warnings,
        "next_step": next_step,
        "policy_version": str(policy.get("version") or ""),
        "result_rows": result_rows,
        "by_symbol": by_symbol,
        "by_side": by_side,
        "by_month": by_month,
    }


def save_holdout_snapshot(report: dict[str, Any], snapshot_dir: str | Path = SNAPSHOT_DIR) -> dict[str, str]:
    root = Path(snapshot_dir)
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    freeze_id = str(report.get("freeze_id") or "fresh_holdout")
    prefix = root / f"{stamp}_{freeze_id}"
    paths: dict[str, str] = {}
    for key in ["result_rows", "by_symbol", "by_side", "by_month"]:
        frame = report.get(key)
        if isinstance(frame, pd.DataFrame):
            path = Path(f"{prefix}_{key}.csv")
            frame.to_csv(path, index=False)
            paths[key] = str(path)
    summary = {k: v for k, v in report.items() if not isinstance(v, pd.DataFrame)}
    path = Path(f"{prefix}_summary.json")
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    paths["summary"] = str(path)
    return paths
