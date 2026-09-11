from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from .analysis_core import run_scanner, slot_fingerprint
from .engine import normalize_slot_rows
from .historical_backfill import backfill_symbol_history
from .history_manager import (
    DEFAULT_HISTORY_INTERVALS,
    DEFAULT_HISTORY_LOOKBACK,
    DEFAULT_HISTORY_SYMBOLS,
    audit_history_gaps,
    load_history_targets,
    normalize_intervals,
    normalize_symbols,
    summarize_history_coverage,
)
from .research_command_center_v29 import (
    CORE_RESEARCH_FAMILIES,
    CORE_RESEARCH_SYMBOLS,
    build_market_state_dashboard,
    queue_core_research_batch,
)
from .runtime_state import atomic_write_json
from .settings import ANALYSIS_REQUEST_PATH, LAB_DB_PATH, OHLCV_STORE_ROOT
from .storage import Storage

DEFAULT_RUNTIME_CYCLE_CONFIG = Path("config/runtime_cycle.json")
RUNTIME_REPORT_DIR = Path("data/runtime_cycles")
MARKET_STATE_HISTORY_DIR = Path("data/market_state_history")


def _utc_now(now: datetime | None = None) -> datetime:
    value = now or datetime.now(timezone.utc)
    return value.astimezone(timezone.utc)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def default_runtime_cycle_config() -> dict[str, Any]:
    return {
        "history": {
            "symbols": list(DEFAULT_HISTORY_SYMBOLS),
            "intervals": list(DEFAULT_HISTORY_INTERVALS),
            "lookback": DEFAULT_HISTORY_LOOKBACK,
            "update_only": True,
            "sleep_seconds": 0.15,
        },
        "gap_audit": {
            "enabled": True,
            "max_gaps_per_target": 20,
        },
        "analysis": {
            "mode": "inline",
            "symbols": list(DEFAULT_HISTORY_SYMBOLS),
            "timeframe": "1h",
            "live_bundle_mode": False,
        },
        "research": {
            "queue": False,
            "symbols": list(CORE_RESEARCH_SYMBOLS),
            "families": list(CORE_RESEARCH_FAMILIES),
            "lookback_days": 365,
            "require_history_ready": True,
            "max_per_family": 1,
        },
    }


def load_runtime_cycle_config(path: str | Path = DEFAULT_RUNTIME_CYCLE_CONFIG) -> dict[str, Any]:
    cfg = default_runtime_cycle_config()
    p = Path(path)
    if p.exists():
        payload = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("runtime cycle config must be a JSON object")
        cfg = _deep_merge(cfg, payload)

    history_cfg = cfg.setdefault("history", {})
    targets = load_history_targets()
    history_cfg["symbols"] = normalize_symbols(history_cfg.get("symbols") or targets.get("symbols"))
    history_cfg["intervals"] = normalize_intervals(history_cfg.get("intervals") or targets.get("intervals"))
    history_cfg["lookback"] = str(history_cfg.get("lookback") or targets.get("lookback") or DEFAULT_HISTORY_LOOKBACK)

    analysis_cfg = cfg.setdefault("analysis", {})
    analysis_cfg["symbols"] = normalize_symbols(analysis_cfg.get("symbols") or history_cfg["symbols"])
    analysis_cfg["timeframe"] = str(analysis_cfg.get("timeframe") or "1h")
    analysis_cfg["mode"] = str(analysis_cfg.get("mode") or "inline").strip().lower()
    if analysis_cfg["mode"] not in {"inline", "request", "skip"}:
        raise ValueError("analysis.mode must be one of: inline, request, skip")

    research_cfg = cfg.setdefault("research", {})
    research_cfg["symbols"] = normalize_symbols(research_cfg.get("symbols") or CORE_RESEARCH_SYMBOLS)
    research_cfg["families"] = [str(x) for x in (research_cfg.get("families") or CORE_RESEARCH_FAMILIES)]
    return cfg


def build_runtime_cycle_plan(
    config: dict[str, Any],
    *,
    history_update: bool = True,
    analysis_mode: str | None = None,
    queue_research: bool | None = None,
) -> dict[str, Any]:
    history_cfg = config.get("history") or {}
    analysis_cfg = config.get("analysis") or {}
    research_cfg = config.get("research") or {}
    mode = str(analysis_mode or analysis_cfg.get("mode") or "inline").lower()
    queue_flag = bool(research_cfg.get("queue")) if queue_research is None else bool(queue_research)
    targets = [
        {"symbol": symbol, "interval": interval}
        for symbol in normalize_symbols(history_cfg.get("symbols"))
        for interval in normalize_intervals(history_cfg.get("intervals"))
    ]
    return {
        "history_update": bool(history_update),
        "history_targets": targets,
        "history_lookback": str(history_cfg.get("lookback") or DEFAULT_HISTORY_LOOKBACK),
        "history_update_only": bool(history_cfg.get("update_only", True)),
        "gap_audit": bool((config.get("gap_audit") or {}).get("enabled", True)),
        "analysis_mode": mode,
        "analysis_symbols": normalize_symbols(analysis_cfg.get("symbols")),
        "analysis_timeframe": str(analysis_cfg.get("timeframe") or "1h"),
        "queue_research": queue_flag,
        "research_symbols": normalize_symbols(research_cfg.get("symbols")),
        "research_families": list(research_cfg.get("families") or []),
    }


def write_analysis_request(symbols: list[str], timeframe: str, *, reason: str = "v28_10_runtime_cycle") -> Path:
    payload = {
        "requested_at": _utc_now().isoformat(),
        "reason": reason,
        "symbols": normalize_symbols(symbols),
        "intervals": [str(timeframe)],
    }
    atomic_write_json(ANALYSIS_REQUEST_PATH, payload)
    return Path(ANALYSIS_REQUEST_PATH)


def run_inline_analysis(
    *,
    storage: Storage | None = None,
    symbols: list[str] | None = None,
    analysis_timeframe: str = "1h",
    live_bundle_mode: bool = False,
) -> dict[str, Any]:
    storage = storage or Storage(LAB_DB_PATH)
    selected_symbols = normalize_symbols(symbols or DEFAULT_HISTORY_SYMBOLS)
    slot_rows = normalize_slot_rows(storage.get_active_slots())
    scanner_rows, analysis_map = run_scanner(
        storage,
        analysis_timeframe,
        selected_symbols,
        slot_rows,
        auto_paper_mode=False,
        live_bundle_mode=bool(live_bundle_mode),
    )
    latest_signature = storage.get_latest_closed_candle_signature(selected_symbols, analysis_timeframe)
    latest_candle = max([v for v in latest_signature.values() if v], default=None)
    meta = {
        "last_run_at": _utc_now().isoformat(),
        "timeframe": analysis_timeframe,
        "analysis_timeframe": analysis_timeframe,
        "chart_timeframe": analysis_timeframe,
        "symbols": selected_symbols,
        "slot_fingerprint": slot_fingerprint(slot_rows),
        "latest_closed_signature": latest_signature,
        "latest_closed_candle_time": latest_candle,
        "reason": "v28_10_runtime_cycle_inline",
        "safe_runtime_cycle": True,
        "auto_paper_mode": False,
        "live_bundle_mode": bool(live_bundle_mode),
    }
    storage.write_analysis_cache(scanner_rows, analysis_map, meta)
    return {
        "mode": "inline",
        "symbols_requested": len(selected_symbols),
        "symbols_analyzed": len(analysis_map),
        "scanner_rows": len(scanner_rows),
        "analysis_updated_at": meta["last_run_at"],
        "latest_closed_candle_time": latest_candle,
    }


def _history_ready_for_research(
    coverage: pd.DataFrame,
    research_symbols: list[str],
    *,
    min_coverage_pct: float = 95.0,
) -> bool:
    if coverage is None or coverage.empty:
        return False
    required_symbols = set(normalize_symbols(research_symbols))
    required_intervals = {"1h", "4h"}
    work = coverage.copy()
    work["symbol"] = work["symbol"].astype(str).str.upper()
    work["interval"] = work["interval"].astype(str)
    work = work[work["symbol"].isin(required_symbols) & work["interval"].isin(required_intervals)]
    if len(work) < len(required_symbols) * len(required_intervals):
        return False
    return bool((pd.to_numeric(work["coverage_pct"], errors="coerce").fillna(0.0) >= float(min_coverage_pct)).all())


def _has_active_core_research_job() -> bool:
    from .backtest_jobs import list_jobs

    for job in list_jobs(["queued", "running"]):
        if str(job.get("run_kind") or "").startswith("v28_9_core_research"):
            return True
    return False


def save_market_state_snapshot(
    *,
    storage: Storage | None = None,
    symbols: list[str] | None = None,
    lookback: str = DEFAULT_HISTORY_LOOKBACK,
    snapshot_dir: str | Path = MARKET_STATE_HISTORY_DIR,
    now: datetime | None = None,
) -> dict[str, Any]:
    storage = storage or Storage(LAB_DB_PATH)
    ts = _utc_now(now)
    dashboard = build_market_state_dashboard(
        storage=storage,
        symbols=normalize_symbols(symbols or DEFAULT_HISTORY_SYMBOLS),
        lookback=lookback,
        store_root=OHLCV_STORE_ROOT,
    )
    target_dir = Path(snapshot_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = ts.strftime("%Y%m%dT%H%M%SZ")
    json_path = target_dir / f"market_state_{stamp}.json"
    csv_path = target_dir / f"market_state_{stamp}.csv"
    payload = {
        "created_at": ts.isoformat(),
        "lookback": lookback,
        "rows": dashboard.to_dict(orient="records"),
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    dashboard.to_csv(csv_path, index=False)
    return {
        "rows": int(len(dashboard)),
        "json_path": str(json_path),
        "csv_path": str(csv_path),
    }


def _save_runtime_report(report: dict[str, Any], report_dir: str | Path = RUNTIME_REPORT_DIR) -> dict[str, str]:
    target_dir = Path(report_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    cycle_id = str(report.get("cycle_id") or _utc_now().strftime("%Y%m%dT%H%M%SZ"))
    path = target_dir / f"{cycle_id}.json"
    latest = target_dir / "latest.json"
    text = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    path.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    return {"report_path": str(path), "latest_report_path": str(latest)}


def run_runtime_cycle(
    *,
    config_path: str | Path = DEFAULT_RUNTIME_CYCLE_CONFIG,
    history_update: bool = True,
    analysis_mode: str | None = None,
    queue_research: bool | None = None,
    dry_run: bool = False,
    max_pages: int | None = None,
    sleep_seconds: float | None = None,
    gap_audit: bool | None = None,
    storage: Storage | None = None,
    backfill_fn: Callable[..., Any] = backfill_symbol_history,
    now: datetime | None = None,
) -> dict[str, Any]:
    started = _utc_now(now)
    cycle_id = started.strftime("runtime_cycle_%Y%m%dT%H%M%SZ")
    cfg = load_runtime_cycle_config(config_path)
    plan = build_runtime_cycle_plan(
        cfg,
        history_update=history_update,
        analysis_mode=analysis_mode,
        queue_research=queue_research,
    )
    report: dict[str, Any] = {
        "version": "V28.10",
        "cycle_id": cycle_id,
        "started_at": started.isoformat(),
        "safe_mode": {
            "auto_paper_mode": False,
            "live_execution": False,
            "research_queue_opt_in": True,
        },
        "plan": plan,
        "steps": {},
        "errors": [],
    }

    if dry_run:
        report["status"] = "dry_run"
        report["finished_at"] = _utc_now().isoformat()
        return report

    history_cfg = cfg.get("history") or {}
    history_results: list[dict[str, Any]] = []
    if history_update:
        for target in plan["history_targets"]:
            try:
                result = backfill_fn(
                    target["symbol"],
                    target["interval"],
                    lookback=plan["history_lookback"],
                    update_only=bool(history_cfg.get("update_only", True)),
                    store_root=OHLCV_STORE_ROOT,
                    sleep_seconds=float(
                        history_cfg.get("sleep_seconds", 0.15)
                        if sleep_seconds is None
                        else sleep_seconds
                    ),
                    max_pages=max_pages,
                )
                payload = result.to_dict() if hasattr(result, "to_dict") else dict(result or {})
                history_results.append(payload)
            except Exception as exc:
                error = {
                    "step": "history_update",
                    "symbol": target["symbol"],
                    "interval": target["interval"],
                    "error": str(exc),
                }
                report["errors"].append(error)
                history_results.append({**target, "error": str(exc)})
    report["steps"]["history_update"] = {
        "enabled": bool(history_update),
        "targets": len(plan["history_targets"]),
        "results": history_results,
    }

    coverage = summarize_history_coverage(
        history_cfg.get("symbols"),
        history_cfg.get("intervals"),
        lookback=plan["history_lookback"],
        store_root=OHLCV_STORE_ROOT,
    )
    report["steps"]["coverage"] = {
        "rows": coverage.to_dict(orient="records"),
        "ready_targets": int((coverage.get("status", pd.Series(dtype=str)) == "ready").sum()) if not coverage.empty else 0,
        "total_targets": int(len(coverage)),
        "min_coverage_pct": float(pd.to_numeric(coverage.get("coverage_pct"), errors="coerce").fillna(0).min()) if not coverage.empty else 0.0,
    }

    audit_enabled = bool((cfg.get("gap_audit") or {}).get("enabled", True)) if gap_audit is None else bool(gap_audit)
    gap_rows: list[dict[str, Any]] = []
    if audit_enabled:
        max_gaps = int((cfg.get("gap_audit") or {}).get("max_gaps_per_target", 20))
        for target in plan["history_targets"]:
            try:
                gaps = audit_history_gaps(
                    target["symbol"],
                    target["interval"],
                    lookback=plan["history_lookback"],
                    store_root=OHLCV_STORE_ROOT,
                    max_gaps=max_gaps,
                )
                gap_rows.append(
                    {
                        **target,
                        "gap_count_returned": int(len(gaps)),
                        "missing_rows_approx": int(pd.to_numeric(gaps.get("missing_rows_approx"), errors="coerce").fillna(0).sum()) if not gaps.empty else 0,
                        "gaps": gaps.to_dict(orient="records"),
                    }
                )
            except Exception as exc:
                report["errors"].append(
                    {"step": "gap_audit", **target, "error": str(exc)}
                )
    report["steps"]["gap_audit"] = {
        "enabled": audit_enabled,
        "targets": gap_rows,
        "targets_with_gaps": sum(1 for row in gap_rows if row.get("gap_count_returned", 0) > 0),
    }

    runtime_storage = storage or Storage(LAB_DB_PATH)
    mode = str(plan["analysis_mode"])
    if mode == "inline":
        try:
            report["steps"]["analysis"] = run_inline_analysis(
                storage=runtime_storage,
                symbols=plan["analysis_symbols"],
                analysis_timeframe=plan["analysis_timeframe"],
                live_bundle_mode=False,
            )
        except Exception as exc:
            report["errors"].append({"step": "analysis", "error": str(exc)})
            report["steps"]["analysis"] = {"mode": "inline", "error": str(exc)}
    elif mode == "request":
        try:
            request_path = write_analysis_request(
                plan["analysis_symbols"],
                plan["analysis_timeframe"],
            )
            report["steps"]["analysis"] = {
                "mode": "request",
                "request_path": str(request_path),
                "note": "Analyzer worker must be running to consume the request.",
            }
        except Exception as exc:
            report["errors"].append({"step": "analysis_request", "error": str(exc)})
            report["steps"]["analysis"] = {"mode": "request", "error": str(exc)}
    else:
        report["steps"]["analysis"] = {"mode": "skip"}

    try:
        report["steps"]["market_state_snapshot"] = save_market_state_snapshot(
            storage=runtime_storage,
            symbols=plan["analysis_symbols"],
            lookback=plan["history_lookback"],
            now=started,
        )
    except Exception as exc:
        report["errors"].append({"step": "market_state_snapshot", "error": str(exc)})
        report["steps"]["market_state_snapshot"] = {"error": str(exc)}

    if plan["queue_research"]:
        research_cfg = cfg.get("research") or {}
        research_ready = _history_ready_for_research(coverage, plan["research_symbols"])
        active_job = _has_active_core_research_job()
        if bool(research_cfg.get("require_history_ready", True)) and not research_ready:
            report["steps"]["research"] = {
                "queued": False,
                "reason": "History is not ready for all required 1h/4h research targets.",
            }
        elif active_job:
            report["steps"]["research"] = {
                "queued": False,
                "reason": "A core research job is already queued or running.",
            }
        else:
            try:
                queued = queue_core_research_batch(
                    storage=runtime_storage,
                    source_root=OHLCV_STORE_ROOT,
                    symbols=plan["research_symbols"],
                    families=plan["research_families"],
                    max_per_family=int(research_cfg.get("max_per_family", 1)),
                    lookback_days=int(research_cfg.get("lookback_days", 365)),
                    comment="V28.10 runtime cycle core research queue.",
                )
                queued["plan"] = queued.get("plan").to_dict(orient="records") if isinstance(queued.get("plan"), pd.DataFrame) else queued.get("plan")
                report["steps"]["research"] = queued
            except Exception as exc:
                report["errors"].append({"step": "research_queue", "error": str(exc)})
                report["steps"]["research"] = {"queued": False, "error": str(exc)}
    else:
        report["steps"]["research"] = {"queued": False, "reason": "Opt-in flag not enabled."}

    report["finished_at"] = _utc_now().isoformat()
    report["status"] = "ok" if not report["errors"] else "partial"
    report.update(_save_runtime_report(report))
    return report
