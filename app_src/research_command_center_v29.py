from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .backtest_jobs import create_batch_job, list_jobs
from .history_manager import summarize_history_coverage
from .settings import LAB_DB_PATH, OHLCV_STORE_ROOT
from .storage import Storage
from .trend_lifecycle import classify_analysis_map, infer_strategy_family

CORE_RESEARCH_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
CORE_RESEARCH_FAMILIES = ["trend_pullback", "compression_breakout", "range_reversion"]
CORE_FAMILY_GROUPS = {
    "trend_pullback": {"trend_pullback", "htf_pullback", "vwap_reclaim", "trend_following", "rsi_regime"},
    "compression_breakout": {"compression_breakout", "compression_release"},
    "range_reversion": {"range_reversion", "mean_reversion", "market_maker_range"},
}
DEFAULT_ENTRY_TIMEFRAME = "1h"
DEFAULT_ANALYSIS_TIMEFRAME = "4h"
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_RESEARCH_CONFIG = {
    "lookback_entry_bars": 300,
    "lookback_analysis_bars": 300,
    "lookback_htf_bars": 200,
    "max_hold_bars": 168,
    "cooldown_bars": 1,
    "fixed_stake_usd": 100,
    "fee_bps_per_side": 4.0,
    "slippage_bps_per_side": 1.0,
    "spread_bps": 1.0,
    "funding_bps_per_8h": 0.0,
    "execution_preset_name": "binance_usdm_taker_light",
    "allow_long": True,
    "allow_short": True,
    "one_trade_at_time": True,
}


def _json_load(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return fallback


def strategy_row_to_payload(row: pd.Series | dict[str, Any]) -> dict[str, Any]:
    r = dict(row)
    return {
        "strategy_id": int(r.get("strategy_id") or 0),
        "version_id": int(r.get("version_id") or 0),
        "version_no": int(r.get("version_no") or 1),
        "strategy_name": str(r.get("strategy_name") or "Strategy"),
        "template_key": str(r.get("template_key") or "rule_builder"),
        "human_thesis": str(r.get("human_thesis") or ""),
        "expected_outcome": str(r.get("expected_outcome") or ""),
        "indicator_description": str(r.get("indicator_description") or ""),
        "indicators": _json_load(r.get("indicators_json"), []),
        "indicator_rules": _json_load(r.get("indicator_rules_json"), []),
        "rule_params": _json_load(r.get("rule_params_json"), {}),
        "expected_rr": str(r.get("expected_rr") or "1:3"),
        "score_threshold": float(r.get("score_threshold") or 70.0),
        "notes": str(r.get("notes") or ""),
    }


def canonical_research_family(payload: dict[str, Any]) -> str:
    inferred = infer_strategy_family(
        {
            "strategy_name": payload.get("strategy_name"),
            "template_key": payload.get("template_key"),
            "strategy_mode": "single",
        }
    )
    for canonical, members in CORE_FAMILY_GROUPS.items():
        if inferred in members:
            return canonical
    return inferred or "unknown"


def select_core_strategies(
    strategy_df: pd.DataFrame,
    families: list[str] | None = None,
    *,
    max_per_family: int = 1,
) -> list[dict[str, Any]]:
    families = families or list(CORE_RESEARCH_FAMILIES)
    wanted = [f for f in families if f in CORE_FAMILY_GROUPS]
    if strategy_df is None or strategy_df.empty:
        return []
    work = strategy_df.copy()
    if "version_no" in work.columns:
        work = work.sort_values(["strategy_id", "version_no"], ascending=[True, False])
    selected: list[dict[str, Any]] = []
    counts = {family: 0 for family in wanted}
    for _, row in work.iterrows():
        payload = strategy_row_to_payload(row)
        family = canonical_research_family(payload)
        if family not in wanted or counts[family] >= max(1, int(max_per_family)):
            continue
        payload["research_family"] = family
        selected.append(payload)
        counts[family] += 1
    return selected


def build_research_plan(
    strategies: list[dict[str, Any]],
    symbols: list[str] | None = None,
    *,
    entry_timeframe: str = DEFAULT_ENTRY_TIMEFRAME,
    analysis_timeframe: str = DEFAULT_ANALYSIS_TIMEFRAME,
) -> pd.DataFrame:
    symbols = [str(s).upper() for s in (symbols or CORE_RESEARCH_SYMBOLS)]
    rows: list[dict[str, Any]] = []
    for payload in strategies:
        family = str(payload.get("research_family") or canonical_research_family(payload))
        for symbol in symbols:
            rows.append(
                {
                    "symbol": symbol,
                    "research_family": family,
                    "strategy_name": payload.get("strategy_name"),
                    "version_no": payload.get("version_no"),
                    "score_threshold": payload.get("score_threshold"),
                    "expected_rr": payload.get("expected_rr"),
                    "entry_timeframe": entry_timeframe,
                    "analysis_timeframe": analysis_timeframe,
                }
            )
    return pd.DataFrame(rows)


def default_research_dates(lookback_days: int = DEFAULT_LOOKBACK_DAYS, *, now: datetime | None = None) -> tuple[str, str]:
    end = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start = end - timedelta(days=max(30, int(lookback_days)))
    return start.date().isoformat(), end.date().isoformat()


def research_history_readiness(
    symbols: list[str] | None = None,
    *,
    lookback: str = "12mo",
    store_root: str | Path | None = None,
) -> pd.DataFrame:
    symbols = [str(s).upper() for s in (symbols or CORE_RESEARCH_SYMBOLS)]
    return summarize_history_coverage(
        symbols,
        [DEFAULT_ENTRY_TIMEFRAME, DEFAULT_ANALYSIS_TIMEFRAME],
        lookback=lookback,
        store_root=store_root or OHLCV_STORE_ROOT,
    )


def queue_core_research_batch(
    *,
    storage: Storage | None = None,
    source_root: str | Path | None = None,
    symbols: list[str] | None = None,
    families: list[str] | None = None,
    max_per_family: int = 1,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    entry_timeframe: str = DEFAULT_ENTRY_TIMEFRAME,
    analysis_timeframe: str = DEFAULT_ANALYSIS_TIMEFRAME,
    config_overrides: dict[str, Any] | None = None,
    comment: str = "",
) -> dict[str, Any]:
    storage = storage or Storage(LAB_DB_PATH)
    symbols = [str(s).upper() for s in (symbols or CORE_RESEARCH_SYMBOLS)]
    families = families or list(CORE_RESEARCH_FAMILIES)
    strategy_df = storage.get_latest_strategy_versions()
    strategies = select_core_strategies(strategy_df, families, max_per_family=max_per_family)
    if not strategies:
        return {"queued": False, "reason": "No strategies matched the selected core research families.", "strategies": [], "plan": pd.DataFrame()}

    start_date, end_date = default_research_dates(lookback_days)
    config = dict(DEFAULT_RESEARCH_CONFIG)
    config.update(config_overrides or {})
    plan = build_research_plan(strategies, symbols, entry_timeframe=entry_timeframe, analysis_timeframe=analysis_timeframe)
    path = create_batch_job(
        source_root=str(source_root or OHLCV_STORE_ROOT),
        symbols=symbols,
        entry_timeframe=entry_timeframe,
        analysis_timeframe=analysis_timeframe,
        start_date=start_date,
        end_date=end_date,
        config=config,
        strategies=strategies,
        comment=comment or "V28.9 core research batch: three-family evidence run.",
        extra={
            "run_kind": "v28_9_core_research",
            "research_families": families,
            "research_symbols": symbols,
            "research_protocol": {
                "purpose": "Compare a small set of strategy families on slow timeframes with realistic friction before adding more complexity.",
                "promotion_rule": "Do not promote from one run; require enough trades, positive net expectancy, acceptable drawdown, and follow-up cross-validation.",
            },
        },
    )
    return {
        "queued": True,
        "job_path": str(path),
        "strategies": strategies,
        "plan": plan,
        "start_date": start_date,
        "end_date": end_date,
        "config": config,
    }


def build_market_state_dashboard(
    *,
    storage: Storage | None = None,
    symbols: list[str] | None = None,
    lookback: str = "12mo",
    store_root: str | Path | None = None,
) -> pd.DataFrame:
    storage = storage or Storage(LAB_DB_PATH)
    symbols = [str(s).upper() for s in (symbols or CORE_RESEARCH_SYMBOLS)]
    cache = storage.read_analysis_cache()
    analysis_map = cache.get("analysis_map") or {}
    meta = cache.get("meta") or {}

    lifecycle_df = classify_analysis_map({s: analysis_map[s] for s in symbols if s in analysis_map}, analysis_tf=str(meta.get("analysis_timeframe") or DEFAULT_ANALYSIS_TIMEFRAME))
    lifecycle_rows = lifecycle_df.to_dict(orient="records") if not lifecycle_df.empty else []
    lifecycle_by_symbol = {str(row.get("symbol") or "").upper(): row for row in lifecycle_rows}

    coverage = research_history_readiness(symbols, lookback=lookback, store_root=store_root)
    coverage_rows: dict[str, dict[str, Any]] = {}
    if not coverage.empty:
        for symbol, grp in coverage.groupby("symbol"):
            statuses = grp.get("status", pd.Series(dtype=str)).astype(str).tolist()
            coverage_rows[str(symbol).upper()] = {
                "history_coverage_min_pct": round(float(pd.to_numeric(grp.get("coverage_pct"), errors="coerce").fillna(0).min()), 2),
                "history_status": "ready" if statuses and all(x == "ready" for x in statuses) else ("missing" if all(x == "missing" for x in statuses) else "partial"),
                "history_last_open_time": max([str(x) for x in grp.get("last_open_time", pd.Series(dtype=str)).dropna().tolist()] or [""]),
            }

    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        life = lifecycle_by_symbol.get(symbol, {})
        hist = coverage_rows.get(symbol, {"history_coverage_min_pct": 0.0, "history_status": "missing", "history_last_open_time": ""})
        rows.append(
            {
                "symbol": symbol,
                "lifecycle_state": life.get("lifecycle_state") or "not_analyzed",
                "direction": life.get("trend_direction") or "—",
                "confidence": life.get("confidence") or 0.0,
                "best_fit_strategy": life.get("best_fit_strategy") or "—",
                "fit_ready_count": life.get("fit_ready_count") or 0,
                "blocked_or_conflict_count": life.get("blocked_or_conflict_count") or 0,
                "allowed_strategy_families": life.get("allowed_strategy_families") or "—",
                "entry_mode": life.get("entry_mode") or "—",
                "exit_family": life.get("exit_family") or "—",
                **hist,
                "analysis_updated_at": meta.get("last_run_at") or meta.get("updated_at") or "",
            }
        )
    return pd.DataFrame(rows)


def recent_research_jobs(limit: int = 10) -> pd.DataFrame:
    rows = []
    for job in list_jobs():
        if str(job.get("run_kind") or "").startswith("v28_9"):
            progress = job.get("progress") or {}
            rows.append(
                {
                    "job_id": job.get("job_id"),
                    "status": job.get("status"),
                    "created_at": job.get("created_at"),
                    "completed": progress.get("completed", 0),
                    "total": progress.get("total", 0),
                    "current_strategy": progress.get("current_strategy"),
                    "symbols": ", ".join(job.get("symbols") or []),
                    "families": ", ".join(job.get("research_families") or []),
                }
            )
    return pd.DataFrame(rows[: max(1, int(limit))])
