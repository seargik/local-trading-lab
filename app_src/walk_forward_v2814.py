from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from .backtest_core import load_saved_backtest
from .backtest_jobs import create_task_job, list_jobs
from .settings import OHLCV_STORE_ROOT

POLICY_PATH = Path("config/walk_forward_policy.json")
SNAPSHOT_DIR = Path("data/backtest_reviews/walk_forward_scorecards")
RUN_KIND = "v28_14_walk_forward"


def load_walk_forward_policy(path: str | Path = POLICY_PATH) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("walk-forward policy must be a JSON object")
    return payload


def strategy_payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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


def _date(value: Any) -> pd.Timestamp:
    return pd.to_datetime(value, utc=True, errors="coerce").normalize()


def build_anchored_walk_forward_folds(
    start_date: Any,
    end_date: Any,
    *,
    initial_train_months: int = 6,
    test_months: int = 2,
    max_folds: int = 3,
    min_train_days: int = 90,
    min_test_days: int = 30,
) -> list[dict[str, str]]:
    """Create expanding-train / forward-test folds without overlapping test windows."""
    start = _date(start_date)
    end = _date(end_date)
    if pd.isna(start) or pd.isna(end) or end <= start:
        return []
    initial_train_months = max(1, int(initial_train_months))
    test_months = max(1, int(test_months))
    max_folds = max(1, int(max_folds))
    folds: list[dict[str, str]] = []
    for idx in range(max_folds):
        train_end_exclusive = start + pd.DateOffset(months=initial_train_months + idx * test_months)
        test_start = train_end_exclusive
        if test_start > end:
            break
        test_end_exclusive = test_start + pd.DateOffset(months=test_months)
        test_end = min(end, test_end_exclusive - pd.Timedelta(days=1))
        train_end = test_start - pd.Timedelta(days=1)
        train_days = int((train_end - start).days) + 1
        test_days = int((test_end - test_start).days) + 1
        if train_days < int(min_train_days) or test_days < int(min_test_days):
            continue
        folds.append(
            {
                "fold_id": f"wf_{idx + 1}",
                "train_start": start.date().isoformat(),
                "train_end": train_end.date().isoformat(),
                "test_start": test_start.date().isoformat(),
                "test_end": test_end.date().isoformat(),
            }
        )
    return folds


def _slice_trades(
    trades: pd.DataFrame | None,
    start_date: str,
    end_date: str,
    *,
    symbols: list[str] | None = None,
) -> pd.DataFrame:
    if trades is None or trades.empty:
        return pd.DataFrame()
    work = trades.copy()
    time_col = "signal_time" if "signal_time" in work.columns else "entry_time"
    if time_col not in work.columns:
        return pd.DataFrame()
    ts = pd.to_datetime(work[time_col], utc=True, errors="coerce")
    start = pd.to_datetime(start_date, utc=True, errors="coerce")
    end_exclusive = pd.to_datetime(end_date, utc=True, errors="coerce") + pd.Timedelta(days=1)
    mask = ts.ge(start) & ts.lt(end_exclusive)
    if symbols and "symbol" in work.columns:
        wanted = {str(x).upper() for x in symbols}
        mask &= work["symbol"].astype(str).str.upper().isin(wanted)
    return work.loc[mask].copy().reset_index(drop=True)


def _trade_metrics(trades: pd.DataFrame | None, fixed_stake_usd: float = 100.0) -> dict[str, Any]:
    if trades is None or trades.empty:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "net_pnl_pct": 0.0,
            "net_pnl_usd": 0.0,
            "expectancy_r": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_usd": 0.0,
        }
    work = trades.copy()
    pnl_pct = pd.to_numeric(work.get("pnl_pct"), errors="coerce").fillna(0.0)
    risk_pct = pd.to_numeric(work.get("risk_pct"), errors="coerce").replace(0, pd.NA)
    r_multiple = (pnl_pct / risk_pct).replace([float("inf"), float("-inf")], pd.NA).fillna(0.0)
    pnl_usd = float(fixed_stake_usd) * pnl_pct / 100.0
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
    return {
        "trades": int(len(work)),
        "win_rate": round(float((pnl_pct > 0).mean() * 100), 2),
        "net_pnl_pct": round(float(pnl_pct.sum()), 4),
        "net_pnl_usd": round(float(pnl_usd.sum()), 2),
        "expectancy_r": round(float(r_multiple.mean()), 4),
        "profit_factor": round(float(gross_profit / gross_loss), 4) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0),
        "max_drawdown_usd": round(float(abs(drawdown.min())) if len(drawdown) else 0.0, 2),
    }


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


def _base_config(source_run: dict[str, Any]) -> dict[str, Any]:
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
    ]:
        cfg.pop(key, None)
    return cfg


def training_fold_summary(
    source_run: dict[str, Any],
    folds: list[dict[str, str]],
    *,
    policy: dict[str, Any] | None = None,
) -> pd.DataFrame:
    policy = dict(policy or load_walk_forward_policy())
    trades = source_run.get("trades", pd.DataFrame())
    fixed_stake = _num(_source_config(source_run).get("fixed_stake_usd"), 100.0)
    rows: list[dict[str, Any]] = []
    for fold in folds:
        metrics = _trade_metrics(_slice_trades(trades, fold["train_start"], fold["train_end"]), fixed_stake)
        passed = bool(
            metrics["trades"] >= int(policy.get("min_train_trades_per_fold", 30))
            and metrics["net_pnl_usd"] > 0
            and metrics["expectancy_r"] > _num(policy.get("min_train_expectancy_r"), 0.0)
            and metrics["profit_factor"] >= _num(policy.get("min_train_profit_factor"), 1.05)
        )
        rows.append({**fold, **metrics, "passed": passed})
    return pd.DataFrame(rows)


def has_active_walk_forward(source_run_dir: str, frozen_hash: str) -> bool:
    for job in list_jobs(["queued", "running"]):
        if str(job.get("job_type") or job.get("run_kind") or "") != RUN_KIND:
            continue
        if str(job.get("source_run_dir") or "") == str(source_run_dir) and str(job.get("frozen_strategy_sha256") or "") == str(frozen_hash):
            return True
    return False


def queue_walk_forward_validation(
    source_run: dict[str, Any],
    *,
    research_family: str,
    policy: dict[str, Any] | None = None,
    job_creator: Callable[..., Path] = create_task_job,
) -> dict[str, Any]:
    policy = dict(policy or load_walk_forward_policy())
    manifest = dict(source_run.get("manifest") or {})
    payload = _source_payload(source_run)
    cfg = _source_config(source_run)
    if not payload:
        return {"queued": False, "reason": "Saved run has no strategy payload."}
    if bool(payload.get("benchmark_only", False)):
        return {"queued": False, "reason": "Benchmark-only controls cannot enter promotion walk-forward validation."}
    symbols = _source_symbols(source_run)
    if not symbols:
        return {"queued": False, "reason": "Saved run has no symbols."}
    folds = build_anchored_walk_forward_folds(
        cfg.get("start_date"),
        cfg.get("end_date"),
        initial_train_months=int(policy.get("initial_train_months", 6)),
        test_months=int(policy.get("test_months", 2)),
        max_folds=int(policy.get("max_folds", 3)),
        min_train_days=int(policy.get("min_train_days", 90)),
        min_test_days=int(policy.get("min_test_days", 30)),
    )
    if not folds:
        return {"queued": False, "reason": "Source period is too short for the configured walk-forward folds."}
    frozen_hash = strategy_payload_hash(payload)
    source_run_dir = str(source_run.get("run_dir") or "")
    if has_active_walk_forward(source_run_dir, frozen_hash):
        return {"queued": False, "reason": "A walk-forward validation for this exact saved strategy is already queued or running."}

    wf_id = "wf14_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    base_config = _base_config(source_run)
    preview: list[dict[str, Any]] = []
    job_paths: list[str] = []
    task_count = 0
    for fold in folds:
        tasks: list[dict[str, Any]] = []
        for symbol in symbols:
            meta = {
                "wf_id": wf_id,
                "fold_id": fold["fold_id"],
                "role": "oos_test",
                "symbol": symbol,
                "research_family": research_family,
                "train_start": fold["train_start"],
                "train_end": fold["train_end"],
                "test_start": fold["test_start"],
                "test_end": fold["test_end"],
                "source_run_dir": source_run_dir,
                "source_run_name": manifest.get("name") or "",
                "frozen_strategy_sha256": frozen_hash,
            }
            tasks.append(
                {
                    "name": f"V28.14 WF | {fold['fold_id']} | {symbol} | {payload.get('strategy_name', 'Strategy')}",
                    "scenario_name": f"V28.14 {fold['fold_id']} {symbol} OOS",
                    "strategy_payload": deepcopy(payload),
                    "symbols": [symbol],
                    "task_meta": {"v28_14_walk_forward": meta},
                    "config_overrides": {"run_kind": RUN_KIND, "v28_14_walk_forward": meta},
                }
            )
            preview.append({**meta, "strategy_name": payload.get("strategy_name")})
        path = job_creator(
            source_root=str(cfg.get("source_root") or OHLCV_STORE_ROOT),
            symbols=symbols,
            entry_timeframe=str(cfg.get("entry_timeframe") or "1h"),
            analysis_timeframe=str(cfg.get("analysis_timeframe") or "4h"),
            start_date=fold["test_start"],
            end_date=fold["test_end"],
            base_config=base_config,
            tasks=tasks,
            comment=f"V28.14 frozen-payload walk-forward validation for {research_family}.",
            job_type=RUN_KIND,
            extra={
                "run_kind": RUN_KIND,
                "wf_id": wf_id,
                "research_family": research_family,
                "source_run_dir": source_run_dir,
                "source_run_name": manifest.get("name") or "",
                "frozen_strategy_sha256": frozen_hash,
                "fold": fold,
                "policy_version": policy.get("version"),
                "selection_contamination_warning": True,
            },
        )
        job_paths.append(str(path))
        task_count += len(tasks)
    return {
        "queued": True,
        "wf_id": wf_id,
        "jobs_created": len(job_paths),
        "tasks_created": task_count,
        "job_paths": job_paths,
        "preview": pd.DataFrame(preview),
        "folds": folds,
        "frozen_strategy_sha256": frozen_hash,
        "selection_contamination_warning": True,
    }


def walk_forward_jobs(statuses: list[str] | None = None) -> list[dict[str, Any]]:
    jobs = []
    for job in list_jobs(statuses):
        if str(job.get("job_type") or job.get("run_kind") or "") == RUN_KIND:
            jobs.append(job)
    return sorted(jobs, key=lambda x: str(x.get("created_at") or ""), reverse=True)


def build_result_rows(
    jobs: list[dict[str, Any]],
    *,
    wf_id: str | None = None,
    loader: Callable[[str | Path], dict[str, Any]] = load_saved_backtest,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for job in jobs:
        if str(job.get("job_type") or job.get("run_kind") or "") != RUN_KIND:
            continue
        if wf_id and str(job.get("wf_id") or "") != str(wf_id):
            continue
        for result in job.get("results") or []:
            run_dir = str(result.get("run_dir") or "")
            loaded = loader(run_dir) if run_dir else {}
            manifest = dict(loaded.get("manifest") or {})
            cfg = dict(manifest.get("config") or {})
            meta = dict(cfg.get("v28_14_walk_forward") or (result.get("task_meta") or {}).get("v28_14_walk_forward") or {})
            summary = dict(manifest.get("summary") or result.get("summary") or {})
            actual_hash = strategy_payload_hash(dict(manifest.get("strategy_payload") or {})) if manifest.get("strategy_payload") else ""
            expected_hash = str(meta.get("frozen_strategy_sha256") or job.get("frozen_strategy_sha256") or "")
            rows.append(
                {
                    "wf_id": meta.get("wf_id") or job.get("wf_id"),
                    "fold_id": meta.get("fold_id") or (job.get("fold") or {}).get("fold_id"),
                    "symbol": meta.get("symbol") or result.get("symbol"),
                    "research_family": meta.get("research_family") or job.get("research_family"),
                    "run_dir": run_dir,
                    "train_start": meta.get("train_start"),
                    "train_end": meta.get("train_end"),
                    "test_start": meta.get("test_start") or cfg.get("start_date"),
                    "test_end": meta.get("test_end") or cfg.get("end_date"),
                    "frozen_strategy_sha256": expected_hash,
                    "actual_strategy_sha256": actual_hash,
                    "hash_match": bool(expected_hash and actual_hash and expected_hash == actual_hash),
                    "total_trades": int(_num(summary.get("total_trades"))),
                    "net_pnl_usd": _num(summary.get("total_pnl_usd")),
                    "expectancy_r": _num(summary.get("expectancy_r")),
                    "profit_factor": _num(summary.get("profit_factor")),
                    "win_rate": _num(summary.get("win_rate")),
                    "max_drawdown_usd": abs(_num(summary.get("max_drawdown_usd"))),
                }
            )
    return pd.DataFrame(rows)


def _collect_oos_trades(
    result_rows: pd.DataFrame,
    *,
    loader: Callable[[str | Path], dict[str, Any]] = load_saved_backtest,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    if result_rows is None or result_rows.empty:
        return pd.DataFrame()
    for _, row in result_rows.iterrows():
        run_dir = str(row.get("run_dir") or "")
        if not run_dir:
            continue
        loaded = loader(run_dir)
        trades = loaded.get("trades", pd.DataFrame())
        if not isinstance(trades, pd.DataFrame) or trades.empty:
            continue
        part = trades.copy()
        part["_wf_fold_id"] = str(row.get("fold_id") or "")
        part["_wf_symbol"] = str(row.get("symbol") or "").upper()
        frames.append(part)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _group_summary(
    trades: pd.DataFrame,
    group_col: str,
    *,
    fixed_stake_usd: float,
    min_trades: int,
    policy: dict[str, Any],
) -> pd.DataFrame:
    if trades.empty or group_col not in trades.columns:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for value, part in trades.groupby(group_col, dropna=False):
        metrics = _trade_metrics(part, fixed_stake_usd)
        passed = bool(
            metrics["trades"] >= int(min_trades)
            and metrics["net_pnl_usd"] > 0
            and metrics["expectancy_r"] > _num(policy.get("cell_min_expectancy_r"), 0.0)
            and metrics["profit_factor"] >= _num(policy.get("cell_min_profit_factor"), 1.0)
        )
        rows.append({group_col: value, **metrics, "passed": passed})
    return pd.DataFrame(rows)


def build_pair_transfer_summary(
    source_run: dict[str, Any],
    oos_trades: pd.DataFrame,
    folds: list[dict[str, str]],
    symbols: list[str],
    *,
    policy: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    policy = dict(policy or load_walk_forward_policy())
    fixed_stake = _num(_source_config(source_run).get("fixed_stake_usd"), 100.0)
    source_trades = source_run.get("trades", pd.DataFrame())
    detail: list[dict[str, Any]] = []
    for fold in folds:
        for anchor in symbols:
            train = _slice_trades(source_trades, fold["train_start"], fold["train_end"], symbols=[anchor])
            train_metrics = _trade_metrics(train, fixed_stake)
            anchor_pass = bool(
                train_metrics["trades"] >= int(policy.get("min_anchor_train_trades", 10))
                and train_metrics["net_pnl_usd"] > 0
                and train_metrics["expectancy_r"] > _num(policy.get("cell_min_expectancy_r"), 0.0)
                and train_metrics["profit_factor"] >= _num(policy.get("cell_min_profit_factor"), 1.0)
            )
            for target in symbols:
                if target == anchor:
                    continue
                target_part = oos_trades[
                    (oos_trades.get("_wf_fold_id", pd.Series(dtype=str)).astype(str) == str(fold["fold_id"]))
                    & (oos_trades.get("_wf_symbol", pd.Series(dtype=str)).astype(str).str.upper() == str(target).upper())
                ].copy() if not oos_trades.empty else pd.DataFrame()
                target_metrics = _trade_metrics(target_part, fixed_stake)
                target_pass = bool(
                    target_metrics["trades"] >= int(policy.get("min_oos_trades_per_symbol", 8))
                    and target_metrics["net_pnl_usd"] > 0
                    and target_metrics["expectancy_r"] > _num(policy.get("cell_min_expectancy_r"), 0.0)
                    and target_metrics["profit_factor"] >= _num(policy.get("cell_min_profit_factor"), 1.0)
                )
                detail.append(
                    {
                        "fold_id": fold["fold_id"],
                        "anchor_symbol": anchor,
                        "target_symbol": target,
                        "anchor_eligible": anchor_pass,
                        "anchor_train_trades": train_metrics["trades"],
                        "anchor_train_expectancy_r": train_metrics["expectancy_r"],
                        "anchor_train_profit_factor": train_metrics["profit_factor"],
                        "target_test_trades": target_metrics["trades"],
                        "target_test_net_pnl_usd": target_metrics["net_pnl_usd"],
                        "target_test_expectancy_r": target_metrics["expectancy_r"],
                        "target_test_profit_factor": target_metrics["profit_factor"],
                        "target_pass": bool(target_pass),
                    }
                )
    detail_df = pd.DataFrame(detail)
    if detail_df.empty:
        return detail_df, pd.DataFrame()
    summary_rows: list[dict[str, Any]] = []
    for (anchor, target), part in detail_df.groupby(["anchor_symbol", "target_symbol"]):
        eligible = part[part["anchor_eligible"]].copy()
        eligible_folds = int(len(eligible))
        passes = int(eligible["target_pass"].sum()) if eligible_folds else 0
        share = float(passes / eligible_folds) if eligible_folds else 0.0
        summary_rows.append(
            {
                "anchor_symbol": anchor,
                "target_symbol": target,
                "eligible_folds": eligible_folds,
                "target_passes": passes,
                "transfer_pass_share": round(share, 4),
                "target_test_net_pnl_usd": round(float(eligible["target_test_net_pnl_usd"].sum()), 2) if eligible_folds else 0.0,
                "supports_transfer": bool(eligible_folds > 0 and share >= _num(policy.get("min_transfer_pass_share"), 0.5)),
            }
        )
    return detail_df, pd.DataFrame(summary_rows)


def evaluate_walk_forward(
    wf_id: str,
    jobs: list[dict[str, Any]],
    source_run: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
    loader: Callable[[str | Path], dict[str, Any]] = load_saved_backtest,
) -> dict[str, Any]:
    policy = dict(policy or load_walk_forward_policy())
    result_rows = build_result_rows(jobs, wf_id=wf_id, loader=loader)
    cfg = _source_config(source_run)
    symbols = _source_symbols(source_run)
    folds = build_anchored_walk_forward_folds(
        cfg.get("start_date"), cfg.get("end_date"),
        initial_train_months=int(policy.get("initial_train_months", 6)),
        test_months=int(policy.get("test_months", 2)),
        max_folds=int(policy.get("max_folds", 3)),
        min_train_days=int(policy.get("min_train_days", 90)),
        min_test_days=int(policy.get("min_test_days", 30)),
    )
    fixed_stake = _num(cfg.get("fixed_stake_usd"), 100.0)
    training = training_fold_summary(source_run, folds, policy=policy)
    oos_trades = _collect_oos_trades(result_rows, loader=loader)
    overall = _trade_metrics(oos_trades, fixed_stake)
    fold_summary = _group_summary(
        oos_trades, "_wf_fold_id", fixed_stake_usd=fixed_stake,
        min_trades=int(policy.get("min_oos_trades_per_fold", 8)), policy=policy,
    )
    symbol_summary = _group_summary(
        oos_trades, "_wf_symbol", fixed_stake_usd=fixed_stake,
        min_trades=int(policy.get("min_oos_trades_per_symbol", 8)), policy=policy,
    )
    side_summary = _group_summary(
        oos_trades, "side", fixed_stake_usd=fixed_stake,
        min_trades=max(1, int(policy.get("min_oos_trades_per_symbol", 8)) // 2), policy=policy,
    ) if "side" in oos_trades.columns else pd.DataFrame()
    transfer_detail, transfer_summary = build_pair_transfer_summary(source_run, oos_trades, folds, symbols, policy=policy)

    train_share = float(training["passed"].mean()) if not training.empty else 0.0
    fold_share = float(fold_summary["passed"].mean()) if not fold_summary.empty else 0.0
    symbol_share = float(symbol_summary["passed"].mean()) if not symbol_summary.empty else 0.0
    eligible_transfer = transfer_summary[transfer_summary["eligible_folds"] > 0].copy() if not transfer_summary.empty else pd.DataFrame()
    transfer_edges = int(len(eligible_transfer))
    transfer_pass_share = float(eligible_transfer["supports_transfer"].mean()) if transfer_edges else 0.0
    drawdown_to_profit = (overall["max_drawdown_usd"] / overall["net_pnl_usd"]) if overall["net_pnl_usd"] > 0 else None
    hash_consistent = bool(not result_rows.empty and result_rows["hash_match"].all())

    gates = {
        "frozen_payload_integrity": hash_consistent,
        "training_history": train_share >= _num(policy.get("min_positive_train_fold_share"), 0.67),
        "oos_sample_size": overall["trades"] >= int(policy.get("min_total_oos_trades", 30)),
        "oos_positive_pnl": overall["net_pnl_usd"] > 0,
        "oos_expectancy": overall["expectancy_r"] > _num(policy.get("min_oos_expectancy_r"), 0.0),
        "oos_profit_factor": overall["profit_factor"] >= _num(policy.get("min_oos_profit_factor"), 1.05),
        "fold_stability": fold_share >= _num(policy.get("min_positive_test_fold_share"), 0.67),
        "symbol_stability": symbol_share >= _num(policy.get("min_positive_symbol_share"), 0.67),
        "drawdown_control": drawdown_to_profit is not None and drawdown_to_profit <= _num(policy.get("max_drawdown_to_net_profit"), 1.5),
        "pair_transfer": (
            transfer_edges >= int(policy.get("min_transfer_edges", 2))
            and transfer_pass_share >= _num(policy.get("min_transfer_pass_share"), 0.5)
        ) if bool(policy.get("require_transfer_gate", True)) else True,
    }
    passed = int(sum(bool(v) for v in gates.values()))
    hard_negative = overall["trades"] >= int(policy.get("min_total_oos_trades", 30)) and (
        overall["net_pnl_usd"] <= 0 or overall["expectancy_r"] <= 0 or overall["profit_factor"] < 1.0
    )
    if all(gates.values()):
        verdict = "pass_for_next_validation"
    elif not gates["frozen_payload_integrity"]:
        verdict = "invalid_test"
    elif overall["trades"] < int(policy.get("min_total_oos_trades", 30)):
        verdict = "insufficient_evidence"
    elif hard_negative:
        verdict = "fail"
    else:
        verdict = "mixed"

    warnings: list[str] = []
    if not hash_consistent:
        warnings.append("strategy payload hash changed inside validation; result is invalid")
    if train_share < _num(policy.get("min_positive_train_fold_share"), 0.67):
        warnings.append(f"expanding training windows pass only {train_share:.0%}")
    if fold_share < _num(policy.get("min_positive_test_fold_share"), 0.67):
        warnings.append(f"only {fold_share:.0%} of forward folds pass")
    if symbol_share < _num(policy.get("min_positive_symbol_share"), 0.67):
        warnings.append(f"only {symbol_share:.0%} of symbols pass OOS")
    if bool(policy.get("require_transfer_gate", True)) and not gates["pair_transfer"]:
        warnings.append(f"pair-transfer support is weak ({transfer_pass_share:.0%} across {transfer_edges} eligible edges)")
    if drawdown_to_profit is None:
        warnings.append("drawdown/profit ratio unavailable because OOS net profit is non-positive")
    elif not gates["drawdown_control"]:
        warnings.append(f"OOS drawdown is {drawdown_to_profit:.2f}x OOS net profit")
    warnings.append("same-history caveat: V28.13 candidate selection used the 12-month research set, so these folds test temporal stability, not a pristine untouched future holdout")

    if verdict == "pass_for_next_validation":
        next_step = "Proceed to lifecycle-gate exact replay / fresh holdout, still paper-only."
    elif verdict == "fail":
        next_step = "Reject this version or redesign it; do not tune on the failed OOS folds."
    elif verdict == "mixed":
        next_step = "Keep on research watchlist; identify instability before any parameter change."
    else:
        next_step = "Do not promote; resolve test integrity or collect more evidence first."

    return {
        "wf_id": wf_id,
        "verdict": verdict,
        "gates": gates,
        "gates_passed": passed,
        "gates_total": len(gates),
        "overall": overall,
        "training_pass_share": round(train_share, 4),
        "forward_fold_pass_share": round(fold_share, 4),
        "symbol_pass_share": round(symbol_share, 4),
        "transfer_edges": transfer_edges,
        "transfer_pass_share": round(transfer_pass_share, 4),
        "drawdown_to_net_profit": round(float(drawdown_to_profit), 4) if drawdown_to_profit is not None else None,
        "frozen_payload_integrity": hash_consistent,
        "warnings": warnings,
        "next_step": next_step,
        "policy_version": str(policy.get("version") or ""),
        "selection_contamination_warning": True,
        "result_rows": result_rows,
        "training_folds": training,
        "forward_folds": fold_summary,
        "symbols": symbol_summary,
        "sides": side_summary,
        "pair_transfer_detail": transfer_detail,
        "pair_transfer": transfer_summary,
    }


def save_walk_forward_snapshot(report: dict[str, Any], snapshot_dir: str | Path = SNAPSHOT_DIR) -> dict[str, str]:
    root = Path(snapshot_dir)
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    wf_id = str(report.get("wf_id") or "walk_forward")
    prefix = root / f"{stamp}_{wf_id}"
    paths: dict[str, str] = {}
    for key in ["result_rows", "training_folds", "forward_folds", "symbols", "sides", "pair_transfer_detail", "pair_transfer"]:
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
