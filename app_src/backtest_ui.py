from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from .backtest_core import (
    build_bundle_payload,
    build_export_bundle_bytes,
    build_what_if_tasks,
    convert_bootstrap_to_parquet,
    delete_saved_backtest,
    discover_bootstrap_files,
    list_saved_backtests,
    load_saved_backtest,
    merge_overrides,
    run_backtest,
    run_backtest_matrix,
    save_backtest_result,
    update_saved_backtest_manifest,
)
from .settings import OHLCV_STORE_ROOT
from .backtest_jobs import create_batch_job, create_task_job, delete_job, list_jobs, load_job, update_job, requeue_job, prioritize_queued_job
from .worker_health import read_worker_heartbeat
from .overlap_analytics import build_overlap_reports
from .recommendation_actions import (
    DEFAULT_RECOMMENDATION_ACTION_CONFIG,
    build_recommendation_matrix_config,
    build_recommendation_what_if_tasks,
    normalize_recommendation_frame,
)
from .promotion_v27 import (
    REVIEW_DECISION_OPTIONS,
    append_review_decision,
    build_promotion_candidates,
    build_strategy_version_draft,
    load_review_decisions,
    save_strategy_draft_file,
)
from .cross_validation_v28 import (
    CV_DECISION_OPTIONS,
    append_cv_review_decision,
    build_cv_reports,
    load_cv_review_decisions,
    load_strategy_draft,
    load_strategy_drafts,
    queue_cross_validation_jobs,
    save_draft_as_strategy_version,
)
from .storage import Storage

DEFAULT_BACKTEST_CONFIG = {
    "lookback_entry_bars": 300,
    "lookback_analysis_bars": 300,
    "lookback_htf_bars": 200,
    "max_hold_bars": 288,
    "cooldown_bars": 3,
    "fixed_stake_usd": 100,
    "fee_bps_per_side": 0.0,
    "slippage_bps_per_side": 0.0,
    "spread_bps": 0.0,
    "funding_bps_per_8h": 0.0,
    "execution_preset_name": "zero_research",
    "allow_long": True,
    "allow_short": True,
    "one_trade_at_time": True,
}


DEFAULT_MATRIX_CONFIG = {
    "include_baseline": True,
    "stop_multipliers": [1.25, 1.5, 2.0, 4.0],
    "tp_counts": [2, 4],
    "score_thresholds": [65, 70, 75, 85],
    "include_confirm_bar": True,
    "include_reverse_signal": True,
}

DEFAULT_BUNDLE_CONFIG = {
    "bundle_name": "Selected bundle",
    "bundle_mode": "n_of_m",
    "n_required": 2,
    "bundle_threshold": 2.0,
    "component_min_score": 70,
    "weights": {},
    "notes": "Use n_of_m for practical consensus. Weighted mode is useful when one strategy should lead and others confirm.",
}


def _fmt_seconds(value: Any) -> str:
    try:
        seconds = float(value)
    except Exception:
        return "—"
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    sec = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s"
    hours = minutes // 60
    minutes = minutes % 60
    return f"{hours}h {minutes:02d}m"


def _make_save_defaults(strategy_payload: dict[str, Any], symbols: list[str], start_date: str, end_date: str, entry_tf: str, analysis_tf: str, override_payload: dict[str, Any], config_payload: dict[str, Any], run_kind: str, matrix_config: dict[str, Any] | None = None) -> tuple[str, str]:
    strategy_name = strategy_payload.get("strategy_name") or "Backtest"
    symbol_text = ",".join(symbols) if symbols else "NO_SYMBOLS"
    name = f"{run_kind} | {strategy_name} | {symbol_text} | {entry_tf}->{analysis_tf} | {start_date}..{end_date}"
    comment = {
        "run_kind": run_kind,
        "pairs": symbols,
        "strategy": strategy_name,
        "strategy_payload_final": strategy_payload,
        "override_json": override_payload,
        "backtest_config_final": config_payload,
        "what_if_matrix_config": matrix_config or {},
        "dates": {"start": start_date, "end": end_date},
        "entry_tf": entry_tf,
        "analysis_tf": analysis_tf,
    }
    return name, json.dumps(comment, indent=2, ensure_ascii=False)

KPI_HELP = {
    "Trades": "Total simulated trades in the selected window.",
    "Win rate": "Percent of trades with positive final PnL.",
    "Loss rate": "Percent of trades with negative final PnL.",
    "Breakeven": "Percent of trades that exited near entry after fees.",
    "Partial profit": "Trades that reached at least TP1 but did not finish at final TP.",
    "Total PnL": "Total profit and loss in USD using the fixed stake size per trade.",
    "Total PnL %": "Sum of trade-level PnL percentages. This is not leveraged account CAGR; it is trade-level percentage accumulation.",
    "Median trade %": "Median trade PnL percent.",
    "Median trade $": "Median trade PnL in USD using the configured fixed stake per trade.",
    "Expectancy (R)": "Average trade outcome measured in R multiples, where 1R equals the initial risk.",
    "Avg R": "Average R multiple per trade.",
    "Profit factor": "Gross profits divided by gross losses. Above 1.0 is positive.",
    "Payoff ratio": "Average winner size divided by average loser size.",
    "Max DD": "Largest peak-to-trough drop in cumulative PnL.",
    "Avg duration": "Average time a trade stayed open.",
    "Avg to TP1": "Average hours required to reach the first TP milestone on trades that reached TP1.",
    "Avg MFE": "Average maximum favorable excursion in percent.",
    "Avg MAE": "Average maximum adverse excursion in percent.",
    "MFE before stop": "Average favorable excursion of losing trades before they stopped out.",
    "MAE before TP": "Average adverse excursion of winning trades before eventually reaching profit.",
    "Avg MFE R": "Average favorable excursion measured in R.",
    "Avg MAE R": "Average adverse excursion measured in R.",
    "TP1 hit": "Percent of trades that reached the first take-profit milestone.",
    "TP2 hit": "Percent of trades that reached TP2.",
    "TP3 hit": "Percent of trades that reached TP3.",
    "Final TP hit": "Percent of trades that reached the final TP level.",
    "Avg score": "Average strategy score at entry.",
    "Winner score": "Average score of winning trades.",
    "Loser score": "Average score of losing trades.",
    "Win streak": "Longest consecutive run of winning trades.",
    "Loss streak": "Longest consecutive run of losing trades.",
    "BE saves": "Trades saved by moving stop to breakeven and then exiting at entry.",
    "TP1-lock stops": "Trades stopped after TP1-based stop tightening.",
    "Realized target frac": "Average realized fraction of the full planned target.",
    "Partial vs full edge": "USD difference between non-final exits and final-TP exits in this run.",
    "Stop efficiency": "Percent of losing trades whose worst adverse move stayed within roughly 1.1x of the planned risk.",
    "Slippage proxy": "Average gap between signal close and next-bar entry open. Proxy only, not true execution slippage.",
    "Avg win $": "Average winning trade in USD.",
    "Avg loss $": "Average losing trade in USD.",
    "Pre-friction PnL": "Gross PnL before modeled fees, spread, slippage, and funding.",
    "Friction drag": "Total modeled execution cost removed from gross PnL.",
    "Avg friction": "Average modeled execution cost per trade in percent.",
}

OUTCOME_COLOR_MAP = {
    "SL_INITIAL": "#4C78A8",
    "SL_AT_TP1": "#F58518",
    "TIME_EXIT": "#B279A2",
    "SL_AT_ENTRY": "#E45756",
    "TP4_FINAL": "#54A24B",
    "OPEN": "#9D9D9D",
}

TP_COLOR_MAP = {
    "Exited before TP1": "#E45756",
    "TP1": "#F2CF5B",
    "TP2": "#72B7B2",
    "TP3": "#4C78A8",
    "Final TP": "#54A24B",
}

COMPARISON_METRICS = [
    "total_trades", "win_rate", "loss_rate", "breakeven_rate", "partial_profit_rate", "total_pnl_usd", "total_pnl_pct",
    "expectancy_r", "avg_r_multiple", "profit_factor", "payoff_ratio", "max_drawdown_usd", "avg_trade_duration_hours",
    "tp1_hit_rate", "tp2_hit_rate", "tp3_hit_rate", "final_tp_hit_rate", "avg_score", "avg_mfe_pct", "avg_mae_pct",
]

BACKTEST_VIEW_STATE_PATH = Path("config/backtest_view_state.json")


def _load_backtest_view_state() -> dict[str, Any]:
    try:
        if BACKTEST_VIEW_STATE_PATH.exists():
            raw = json.loads(BACKTEST_VIEW_STATE_PATH.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
    except Exception:
        pass
    return {}


def _save_backtest_view_state(updates: dict[str, Any]) -> None:
    state = _load_backtest_view_state()
    state.update(updates or {})
    BACKTEST_VIEW_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BACKTEST_VIEW_STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _safe_run_slug(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(name or "run"))[:80]


def _build_saved_runs_export_bytes(selected_runs: list[dict[str, Any]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in selected_runs:
            run_dir = Path(item.get("run_dir") or "")
            if not run_dir.exists():
                continue
            base = _safe_run_slug(item.get("name") or run_dir.name)
            for path in run_dir.rglob("*"):
                if path.is_file():
                    zf.write(path, arcname=f"{base}/{path.relative_to(run_dir).as_posix()}")
    return buffer.getvalue()


def _pie_with_palette(df: pd.DataFrame, names: str, values: str, title: str, palette: dict[str, str]) -> go.Figure:
    fig = px.pie(df, names=names, values=values, title=title, color=names, color_discrete_map=palette)
    fig.update_layout(height=280, margin=dict(l=10, r=10, t=50, b=10), legend_title_text=names)
    return fig


def _palette_caption(title: str, palette: dict[str, str]) -> str:
    parts = [f"{title} legend:"]
    for label, color in palette.items():
        parts.append(f":{color}[■] {label}")
    return "  ".join(parts)


def _safe_float(val: Any) -> float | None:
    try:
        if pd.isna(val):
            return None
    except Exception:
        pass
    try:
        return float(val)
    except Exception:
        return None


def _format_delta(value: Any) -> str:
    val = _safe_float(value)
    if val is None:
        return "—"
    return f"{val:+.3f}"


def _safe_json(raw: str, fallback: Any) -> Any:
    try:
        text = str(raw or "").strip()
        return json.loads(text) if text else fallback
    except Exception:
        return fallback



def _load_execution_presets() -> dict[str, Any]:
    path = Path("config/execution_friction_presets.json")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) and raw else {}
    except Exception:
        return {
            "zero_research": {
                "label": "Zero friction / research only",
                "fee_bps_per_side": 0.0,
                "slippage_bps_per_side": 0.0,
                "spread_bps": 0.0,
                "funding_bps_per_8h": 0.0,
            }
        }


def _apply_execution_preset(config: dict[str, Any], preset_key: str, presets: dict[str, Any]) -> dict[str, Any]:
    out = dict(config or {})
    preset = presets.get(preset_key) or {}
    for key in ["fee_bps_per_side", "slippage_bps_per_side", "spread_bps", "funding_bps_per_8h"]:
        if key in preset:
            out[key] = float(preset.get(key) or 0.0)
    out["execution_preset_name"] = preset_key
    out["execution_preset_label"] = preset.get("label") or preset_key
    return out

def _version_payload_from_row(row: pd.Series) -> dict[str, Any]:
    return {
        "strategy_id": int(row["strategy_id"]),
        "version_id": int(row["version_id"]),
        "version_no": int(row["version_no"]),
        "strategy_name": row["strategy_name"],
        "template_key": row["template_key"],
        "human_thesis": row.get("human_thesis") or "",
        "expected_outcome": row.get("expected_outcome") or "",
        "indicator_description": row.get("indicator_description") or "",
        "indicators": _safe_json(row.get("indicators_json"), []),
        "indicator_rules": _safe_json(row.get("indicator_rules_json"), []),
        "rule_params": _safe_json(row.get("rule_params_json"), {}),
        "expected_rr": row.get("expected_rr") or "1:3",
        "score_threshold": float(row.get("score_threshold") or 70),
        "notes": row.get("notes") or "",
    }


def _comparison_frame(saved_runs: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in saved_runs:
        summary = item.get("summary") or {}
        config = item.get("config") or {}
        strat = item.get("strategy_payload") or {}
        rows.append({
            "name": item.get("name"),
            "favorite": bool(item.get("favorite", False)),
            "created_at": item.get("created_at"),
            "comment": item.get("comment") or "",
            "strategy": strat.get("strategy_name"),
            "version": strat.get("version_no"),
            "symbols": ", ".join(config.get("symbols") or []),
            "entry_tf": config.get("entry_timeframe"),
            "analysis_tf": config.get("analysis_timeframe"),
            "start_date": config.get("start_date"),
            "end_date": config.get("end_date"),
            "run_kind": item.get("run_kind") or config.get("run_kind") or "backtest",
            "execution_preset": config.get("execution_preset_label") or config.get("execution_preset_name") or "custom/unknown",
            "base_run": item.get("base_run") or config.get("base_run") or "",
            **summary,
            "run_dir": item.get("run_dir"),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # V28.1 backward compatibility: older saved runs and older batch-job results
    # do not contain the V24+ execution-friction columns. The comparison UI should
    # still render those runs and simply show zero/unknown friction instead of
    # crashing with KeyError. Keep defaults numeric so sorting, action labels, and
    # KPI cards remain stable for mixed old/new run selections.
    numeric_defaults = {
        "profit_factor": 0.0,
        "total_pnl_usd": 0.0,
        "pre_friction_pnl_usd": None,
        "total_execution_cost_usd": 0.0,
        "max_drawdown_usd": 0.0,
        "total_trades": 0,
        "win_rate": 0.0,
        "loss_rate": 0.0,
        "breakeven_rate": 0.0,
        "partial_profit_rate": 0.0,
        "total_pnl_pct": 0.0,
        "expectancy_r": 0.0,
        "avg_r_multiple": 0.0,
        "payoff_ratio": 0.0,
        "avg_trade_duration_hours": 0.0,
        "tp1_hit_rate": 0.0,
        "tp2_hit_rate": 0.0,
        "tp3_hit_rate": 0.0,
        "final_tp_hit_rate": 0.0,
        "avg_score": 0.0,
        "avg_mfe_pct": 0.0,
        "avg_mae_pct": 0.0,
    }
    for col, default in numeric_defaults.items():
        if col not in df.columns:
            if col == "pre_friction_pnl_usd":
                df[col] = df.get("total_pnl_usd", 0.0)
            else:
                df[col] = default
    for col in numeric_defaults:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["pre_friction_pnl_usd"] = df["pre_friction_pnl_usd"].fillna(df["total_pnl_usd"])
    df["total_execution_cost_usd"] = df["total_execution_cost_usd"].fillna(0.0)
    df["profit_factor"] = df["profit_factor"].fillna(0.0)
    df["total_pnl_usd"] = df["total_pnl_usd"].fillna(0.0)
    df["max_drawdown_usd"] = df["max_drawdown_usd"].fillna(0.0)
    df["total_trades"] = df["total_trades"].fillna(0).astype(int)
    return df



def _comparison_action_labels(row: pd.Series) -> str:
    labels: list[str] = []
    trades = _safe_float(row.get("total_trades")) or 0.0
    pf = _safe_float(row.get("profit_factor")) or 0.0
    pnl = _safe_float(row.get("total_pnl_usd")) or 0.0
    max_dd = _safe_float(row.get("max_drawdown_usd")) or 0.0
    friction = _safe_float(row.get("total_execution_cost_usd")) or 0.0
    pre = _safe_float(row.get("pre_friction_pnl_usd")) or 0.0
    if trades < 30:
        labels.append("small sample")
    if pf >= 1.2 and pnl > 0:
        labels.append("candidate")
    if pnl > 0 and max_dd > abs(pnl):
        labels.append("profitable but rough")
    if pre > 0 and pnl <= 0:
        labels.append("dies after friction")
    if friction > max(abs(pnl), 1.0):
        labels.append("cost-sensitive")
    if not labels:
        labels.append("review")
    return ", ".join(labels)


def _concat_loaded_table(loaded_runs: list[tuple[str, pd.DataFrame, dict[str, Any]]], table_key: str) -> pd.DataFrame:
    frames = []
    for run_name, _trades, loaded in loaded_runs:
        frame = loaded.get(table_key, pd.DataFrame())
        if isinstance(frame, pd.DataFrame) and not frame.empty:
            tmp = frame.copy()
            tmp.insert(0, "run_name", run_name)
            frames.append(tmp)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()



def _queue_v26_recommendation_jobs(loaded_runs: list[tuple[str, pd.DataFrame, dict[str, Any]]], action_config: dict[str, Any]) -> tuple[int, int]:
    queued_jobs = 0
    queued_tasks = 0
    for run_name, _trades, loaded in loaded_runs:
        manifest = dict(loaded.get("manifest") or {})
        run_dir = loaded.get("run_dir") or manifest.get("run_dir") or ""
        manifest["run_dir"] = str(run_dir)
        threshold_df = loaded.get("threshold_recommendations", pd.DataFrame())
        tasks, task_preview, used_config = build_recommendation_what_if_tasks(saved_run=manifest, threshold_df=threshold_df, action_config=action_config)
        if not tasks:
            continue
        cfg = dict(manifest.get("config") or {})
        matrix_cfg = build_recommendation_matrix_config(
            thresholds=task_preview.get("tested_threshold", pd.Series(dtype=float)).dropna().astype(float).tolist() if isinstance(task_preview, pd.DataFrame) and not task_preview.empty else [],
            include_baseline=bool(used_config.get("include_segment_baseline", True)),
        )
        symbols = cfg.get("symbols") or []
        create_task_job(
            source_root=str(cfg.get("source_root") or OHLCV_STORE_ROOT),
            symbols=symbols,
            entry_timeframe=cfg.get("entry_timeframe") or "5m",
            analysis_timeframe=cfg.get("analysis_timeframe") or "1h",
            start_date=cfg.get("start_date") or "2024-01-01",
            end_date=cfg.get("end_date") or str(pd.Timestamp.utcnow().date()),
            base_config={k: v for k, v in cfg.items() if k not in {"symbols", "entry_timeframe", "analysis_timeframe", "start_date", "end_date", "source_root", "run_kind", "what_if_config", "base_run"}},
            tasks=[{**task, "symbols": symbols} for task in tasks],
            comment=(manifest.get("comment") or "") + f"\nV26 recommendation-to-action job from saved run: {run_name}",
            job_type="v26_recommendation_what_if",
            extra={
                "what_if_config": matrix_cfg,
                "base_run": str(run_dir),
                "run_mode": cfg.get("run_kind") or manifest.get("run_kind") or "backtest",
                "v26_action_config": used_config,
                "v26_recommendation_preview": task_preview.to_dict(orient="records") if isinstance(task_preview, pd.DataFrame) else [],
            },
        )
        queued_jobs += 1
        queued_tasks += len(tasks)
    return queued_jobs, queued_tasks

def _build_comparison_kpi_table(comp_df: pd.DataFrame) -> pd.DataFrame:
    if comp_df.empty:
        return comp_df
    base = comp_df.iloc[0]
    rows = []
    for _, run in comp_df.iterrows():
        row = {
            "run": run.get("name"),
            "strategy": run.get("strategy"),
            "symbols": run.get("symbols"),
            "profit_factor": run.get("profit_factor"),
        }
        for metric in COMPARISON_METRICS:
            if metric not in comp_df.columns:
                continue
            cur = _safe_float(run.get(metric))
            base_val = _safe_float(base.get(metric))
            row[metric] = cur
            row[f"Δ {metric}"] = (cur - base_val) if cur is not None and base_val is not None else None
        rows.append(row)
    return pd.DataFrame(rows)


def _render_run_comparison(chosen: list[dict[str, Any]], title: str = "Comparison") -> None:
    if not chosen:
        return
    comp_df = _comparison_frame(chosen)
    if comp_df.empty:
        st.info("No comparable runs loaded.")
        return
    comp_df = comp_df.sort_values(["profit_factor", "total_pnl_usd"], ascending=[False, False], na_position="last").reset_index(drop=True)
    comp_df["action_labels"] = comp_df.apply(_comparison_action_labels, axis=1)
    st.write(title)
    top_cols = ["name", "favorite", "action_labels", "strategy", "symbols", "run_kind", "execution_preset", "profit_factor", "total_pnl_usd", "pre_friction_pnl_usd", "total_execution_cost_usd", "max_drawdown_usd", "total_trades", "start_date", "end_date", "comment"]
    st.dataframe(comp_df[[c for c in top_cols if c in comp_df.columns]], width="stretch", hide_index=True)

    card_cols = st.columns(4)
    best_pf = comp_df.sort_values(["profit_factor", "total_trades"], ascending=[False, False]).iloc[0]
    best_pnl = comp_df.sort_values(["total_pnl_usd", "profit_factor"], ascending=[False, False]).iloc[0]
    lowest_dd = comp_df.sort_values(["max_drawdown_usd", "profit_factor"], ascending=[True, False]).iloc[0]
    most_cost = comp_df.sort_values(["total_execution_cost_usd"], ascending=False).iloc[0]
    _metric(card_cols[0], "Best PF", f"{best_pf.get('profit_factor', 0):.2f}")
    card_cols[0].caption(str(best_pf.get("name"))[:90])
    _metric(card_cols[1], "Best net PnL", f"${best_pnl.get('total_pnl_usd', 0):,.2f}")
    card_cols[1].caption(str(best_pnl.get("name"))[:90])
    _metric(card_cols[2], "Lowest DD", f"${lowest_dd.get('max_drawdown_usd', 0):,.2f}")
    card_cols[2].caption(str(lowest_dd.get("name"))[:90])
    _metric(card_cols[3], "Most friction drag", f"${most_cost.get('total_execution_cost_usd', 0):,.2f}")
    card_cols[3].caption(str(most_cost.get("name"))[:90])

    kpi_df = _build_comparison_kpi_table(comp_df)
    if not kpi_df.empty:
        st.write(f"KPI deltas vs top profit-factor run: {comp_df.iloc[0].get('name')}")
        st.dataframe(kpi_df, width="stretch", hide_index=True)

    eq_fig = go.Figure()
    perf_fig = go.Figure()
    decile_fig = go.Figure()
    trade_loaded: list[tuple[str, pd.DataFrame, dict[str, Any]]] = []
    for _, row in comp_df.iterrows():
        run_dir = row.get("run_dir")
        try:
            loaded = load_saved_backtest(run_dir)
        except Exception as exc:
            st.warning(f"Could not load saved run {row.get('name')}: {exc}")
            continue
        manifest = loaded.get("manifest") or {}
        run_name = manifest.get("name") or row.get("name")
        trades = loaded.get("trades", pd.DataFrame())
        trade_loaded.append((run_name, trades, loaded))
        equity = loaded.get("equity_curve", pd.DataFrame())
        if isinstance(equity, pd.DataFrame) and not equity.empty and {"exit_time", "cum_pnl_usd"}.issubset(equity.columns):
            eq_fig.add_trace(go.Scatter(x=pd.to_datetime(equity["exit_time"], errors="coerce"), y=equity["cum_pnl_usd"], mode="lines", name=run_name))
        if isinstance(trades, pd.DataFrame) and not trades.empty:
            tmp = trades.copy()
            if "pnl_usd" not in tmp.columns and "pnl_pct" in tmp.columns:
                tmp["pnl_usd"] = pd.to_numeric(tmp["pnl_pct"], errors="coerce").fillna(0) * float((manifest.get("summary") or row.to_dict() or {}).get("stake_per_trade_usd", 100.0)) / 100.0
            tmp["exit_time"] = pd.to_datetime(tmp.get("exit_time"), utc=True, errors="coerce")
            tmp["bucket"] = tmp["exit_time"].dt.to_period("D").astype(str)
            grouped = tmp.groupby("bucket", as_index=False).agg(period_pnl_usd=("pnl_usd", "sum"))
            grouped["cum_pnl_usd"] = grouped["period_pnl_usd"].cumsum()
            perf_fig.add_trace(go.Scatter(x=grouped["bucket"], y=grouped["cum_pnl_usd"], mode="lines+markers", name=run_name))
            score_df = loaded.get("performance_by_score_decile", pd.DataFrame())
            if isinstance(score_df, pd.DataFrame) and not score_df.empty:
                decile_fig.add_trace(go.Scatter(x=score_df["score_decile"], y=score_df["total_pnl_usd"], mode="lines+markers", name=run_name))

    if eq_fig.data:
        eq_fig.update_layout(title=f"{title}: cumulative PnL over time", height=340, margin=dict(l=10, r=10, t=50, b=10))
        st.plotly_chart(eq_fig, width="stretch", key=f"compare_eq_{_safe_run_slug(title)}")
    if perf_fig.data:
        perf_fig.update_layout(title=f"{title}: daily cumulative performance", height=340, margin=dict(l=10, r=10, t=50, b=10))
        st.plotly_chart(perf_fig, width="stretch", key=f"compare_perf_{_safe_run_slug(title)}")
    if decile_fig.data:
        decile_fig.update_layout(title=f"{title}: score-decile PnL", height=340, margin=dict(l=10, r=10, t=50, b=10))
        st.plotly_chart(decile_fig, width="stretch", key=f"compare_decile_{_safe_run_slug(title)}")

    if trade_loaded:
        st.markdown("#### V25 evidence details")
        t_side, t_regime, t_detail, t_threshold, t_overlap, t_exit, t_friction, t_bundle = st.tabs(["Long/short", "Broad regime", "Detailed regime", "Thresholds", "Overlap", "Exit family", "Friction", "Bundle/owner validation"])
        with t_side:
            side_df = _concat_loaded_table(trade_loaded, "performance_by_side")
            side_regime_df = _concat_loaded_table(trade_loaded, "performance_by_side_regime")
            if not side_df.empty:
                st.write("Run × side")
                st.dataframe(side_df, width="stretch", hide_index=True)
            if not side_regime_df.empty:
                st.write("Run × side × broad regime")
                st.dataframe(side_regime_df, width="stretch", hide_index=True)
            if side_df.empty and side_regime_df.empty:
                st.info("No side split tables found. Re-save runs with V24/V25 to populate them.")
        with t_regime:
            regime_df = _concat_loaded_table(trade_loaded, "performance_by_regime")
            if not regime_df.empty:
                st.dataframe(regime_df, width="stretch", hide_index=True)
            else:
                st.info("No broad-regime table found.")
        with t_detail:
            detail_df = _concat_loaded_table(trade_loaded, "performance_by_detailed_regime")
            side_detail_df = _concat_loaded_table(trade_loaded, "performance_by_side_detailed_regime")
            if not detail_df.empty:
                st.write("Run × V25 detailed regime")
                st.dataframe(detail_df, width="stretch", hide_index=True)
            if not side_detail_df.empty:
                st.write("Run × side × V25 detailed regime")
                st.dataframe(side_detail_df, width="stretch", hide_index=True)
            if detail_df.empty and side_detail_df.empty:
                st.info("No V25 detailed-regime tables found. Re-run and save with V25.")
        with t_threshold:
            threshold_df = _concat_loaded_table(trade_loaded, "threshold_recommendations")
            if not threshold_df.empty:
                st.caption("V26: recommendations are still analytics-first, but can now be converted into targeted segment what-if jobs. These jobs use segment_filter, so only the recommended side/regime segment is retested.")
                norm_threshold_df = normalize_recommendation_frame(threshold_df)
                st.dataframe(norm_threshold_df, width="stretch", hide_index=True)
                with st.expander("V26 recommendation → what-if queue", expanded=False):
                    cfg_cols = st.columns(4)
                    max_recs = int(cfg_cols[0].number_input("Max recommendations per run", min_value=1, max_value=20, value=int(DEFAULT_RECOMMENDATION_ACTION_CONFIG["max_recommendations_per_run"]), step=1, key=f"v26_max_recs_{_safe_run_slug(title)}"))
                    min_segment = int(cfg_cols[1].number_input("Min segment trades", min_value=0, max_value=500, value=int(DEFAULT_RECOMMENDATION_ACTION_CONFIG["min_segment_trades"]), step=1, key=f"v26_min_segment_{_safe_run_slug(title)}"))
                    min_kept = int(cfg_cols[2].number_input("Min kept trades", min_value=0, max_value=500, value=int(DEFAULT_RECOMMENDATION_ACTION_CONFIG["min_kept_trades"]), step=1, key=f"v26_min_kept_{_safe_run_slug(title)}"))
                    offset_text = cfg_cols[3].text_input("Threshold offsets", value=", ".join(str(x) for x in DEFAULT_RECOMMENDATION_ACTION_CONFIG["threshold_offsets"]), key=f"v26_offsets_{_safe_run_slug(title)}")
                    allowed_actions = st.multiselect(
                        "Actions to queue",
                        options=["test_score_threshold", "baseline_ok_do_not_overfilter", "score_not_separating_edge_check_features", "repair_rules_or_avoid_this_segment", "collect_more_samples"],
                        default=["test_score_threshold"],
                        key=f"v26_actions_{_safe_run_slug(title)}",
                    )
                    try:
                        offsets = [float(x.strip()) for x in str(offset_text).split(",") if x.strip()]
                    except Exception:
                        offsets = list(DEFAULT_RECOMMENDATION_ACTION_CONFIG["threshold_offsets"])
                        st.warning("Could not parse offsets, using defaults: -5, 0, 5.")
                    action_cfg = {
                        "max_recommendations_per_run": max_recs,
                        "allowed_actions": allowed_actions,
                        "threshold_offsets": offsets,
                        "include_segment_baseline": True,
                        "include_full_run_baseline": False,
                        "min_segment_trades": min_segment,
                        "min_kept_trades": min_kept,
                    }
                    preview_rows = []
                    for run_name, _trades, loaded in trade_loaded:
                        manifest = dict(loaded.get("manifest") or {})
                        manifest["run_dir"] = loaded.get("run_dir") or manifest.get("run_dir") or ""
                        tasks, preview, _used = build_recommendation_what_if_tasks(saved_run=manifest, threshold_df=loaded.get("threshold_recommendations", pd.DataFrame()), action_config=action_cfg)
                        if isinstance(preview, pd.DataFrame) and not preview.empty:
                            tmp = preview.copy()
                            tmp.insert(0, "source_run", run_name)
                            tmp["queued_tasks_from_row"] = tmp.groupby(["source_run", "rank"]) ["tested_threshold"].transform("count")
                            preview_rows.append(tmp)
                    preview_df = pd.concat(preview_rows, ignore_index=True) if preview_rows else pd.DataFrame()
                    if not preview_df.empty:
                        st.write("Preview of targeted segment tests to queue")
                        st.dataframe(preview_df, width="stretch", hide_index=True)
                    else:
                        st.info("No queueable recommendations after filters. Lower min sample filters or include more action types.")
                    if st.button("Queue V26 recommendation what-if jobs", width="stretch", key=f"queue_v26_recs_{_safe_run_slug(title)}", disabled=preview_df.empty):
                        jobs_count, tasks_count = _queue_v26_recommendation_jobs(trade_loaded, action_cfg)
                        st.success(f"Queued {jobs_count} V26 recommendation jobs with {tasks_count} targeted what-if tasks.")
            else:
                st.info("No threshold recommendations found. Re-run and save with V25/V26.")
        with t_overlap:
            overlap = build_overlap_reports(trade_loaded, bucket="15min")
            same_side = overlap.get("overlap_same_side", pd.DataFrame())
            conflicts = overlap.get("opposite_side_conflicts", pd.DataFrame())
            pairs = overlap.get("owner_pair_overlap", pd.DataFrame())
            st.caption("Overlap is calculated across selected saved runs using 15-minute buckets. It shows duplicate/concurrent evidence; it does not change execution rules.")
            if not same_side.empty:
                st.write("Same-symbol same-side overlaps")
                st.dataframe(same_side, width="stretch", hide_index=True)
            if not conflicts.empty:
                st.write("Opposite-side conflicts on the same symbol/time bucket")
                st.dataframe(conflicts, width="stretch", hide_index=True)
            if not pairs.empty:
                st.write("Owner-pair overlap frequency")
                st.dataframe(pairs, width="stretch", hide_index=True)
            if same_side.empty and conflicts.empty and pairs.empty:
                st.info("No overlap found among the selected runs, or selected runs do not have comparable trade timestamps.")
        with t_exit:
            exit_df = _concat_loaded_table(trade_loaded, "performance_by_exit_family")
            score_side_df = _concat_loaded_table(trade_loaded, "performance_by_score_side_decile")
            if not exit_df.empty:
                st.write("Exit-family split")
                st.dataframe(exit_df, width="stretch", hide_index=True)
            if not score_side_df.empty:
                st.write("Score deciles by side")
                st.dataframe(score_side_df, width="stretch", hide_index=True)
            if exit_df.empty and score_side_df.empty:
                st.info("No exit-family or score-side table found.")
        with t_friction:
            friction_df = _concat_loaded_table(trade_loaded, "friction_comparison")
            if not friction_df.empty:
                st.dataframe(friction_df, width="stretch", hide_index=True)
            else:
                st.info("No gross-vs-net friction table found. Re-run with V24/V25 friction presets.")
        with t_bundle:
            owner_df = _concat_loaded_table(trade_loaded, "performance_by_owner")
            bundle_df = _concat_loaded_table(trade_loaded, "bundle_validation")
            if not owner_df.empty:
                st.write("Trade owner split")
                st.dataframe(owner_df, width="stretch", hide_index=True)
            if not bundle_df.empty:
                st.write("Bundle validation")
                st.dataframe(bundle_df, width="stretch", hide_index=True)
            if owner_df.empty and bundle_df.empty:
                st.info("No owner/bundle validation table found.")

    if trade_loaded:
        st.markdown("#### V27 Promotion & Review Lab")
        st.caption("V27 connects source runs to V26 recommendation what-if results. It stores human review decisions and creates draft strategy JSON only; it does not auto-change live strategy versions.")
        try:
            v27_candidates = build_promotion_candidates(trade_loaded, jobs=list_jobs(["completed"]), min_candidate_trades=20)
        except Exception as exc:
            v27_candidates = pd.DataFrame()
            st.warning(f"Could not build V27 promotion candidates: {exc}")
        if v27_candidates.empty:
            st.info("No V27 promotion candidates found. Queue V26 recommendation what-if jobs, let the worker finish, then compare the source run together with the completed V26 result runs.")
        else:
            show_cols = [
                "review_recommendation", "source_run_name", "candidate_run_name", "side", "regime_group", "regime_detail",
                "tested_threshold", "source_total_trades", "candidate_total_trades", "delta_total_trades",
                "source_total_pnl_usd", "candidate_total_pnl_usd", "delta_total_pnl_usd",
                "source_profit_factor", "candidate_profit_factor", "delta_profit_factor", "candidate_max_drawdown_usd", "job_id",
            ]
            st.dataframe(v27_candidates[[c for c in show_cols if c in v27_candidates.columns]], width="stretch", hide_index=True)
            labels = []
            for idx, row in v27_candidates.iterrows():
                labels.append(
                    f"{idx} | {row.get('review_recommendation')} | ΔPnL ${float(row.get('delta_total_pnl_usd') or 0):.2f} | "
                    f"PF {float(row.get('source_profit_factor') or 0):.2f}->{float(row.get('candidate_profit_factor') or 0):.2f} | "
                    f"{str(row.get('candidate_run_name') or '')[:90]}"
                )
            selected_v27 = st.selectbox("Review candidate", options=labels, index=0, key=f"v27_candidate_select_{_safe_run_slug(title)}")
            selected_idx = int(selected_v27.split(" | ", 1)[0])
            selected_row = v27_candidates.loc[selected_idx]
            mcols = st.columns(5)
            _metric(mcols[0], "Δ net PnL", f"${float(selected_row.get('delta_total_pnl_usd') or 0):,.2f}")
            _metric(mcols[1], "Δ PF", f"{float(selected_row.get('delta_profit_factor') or 0):.2f}")
            _metric(mcols[2], "Candidate trades", f"{int(float(selected_row.get('candidate_total_trades') or 0))}")
            _metric(mcols[3], "Threshold", f"{float(selected_row.get('tested_threshold') or 0):.0f}")
            _metric(mcols[4], "Suggested review", str(selected_row.get("review_recommendation") or "watchlist"))

            source_manifest: dict[str, Any] = {}
            candidate_manifest: dict[str, Any] = {}
            try:
                src_dir = str(selected_row.get("source_run_dir") or "")
                if src_dir:
                    source_manifest = load_saved_backtest(src_dir).get("manifest") or {}
            except Exception:
                source_manifest = {}
            try:
                cand_dir = str(selected_row.get("candidate_run_dir") or "")
                if cand_dir:
                    candidate_manifest = load_saved_backtest(cand_dir).get("manifest") or {}
            except Exception:
                candidate_manifest = {}
            if candidate_manifest:
                draft_payload = build_strategy_version_draft(source_manifest, candidate_manifest, selected_row)
                draft_json = json.dumps(draft_payload, indent=2, ensure_ascii=False, default=str)
                dcols = st.columns(2)
                dcols[0].download_button(
                    "Download V27 strategy draft JSON",
                    data=draft_json,
                    file_name=f"v27_strategy_draft_{_safe_run_slug(str(selected_row.get('candidate_run_name') or 'candidate'))}.json",
                    mime="application/json",
                    width="stretch",
                    key=f"download_v27_draft_{_safe_run_slug(title)}_{selected_idx}",
                )
                with dcols[1].popover("Preview draft payload"):
                    st.json(draft_payload)
            else:
                draft_payload = {}
                st.warning("Candidate manifest could not be loaded, so draft generation is unavailable for this row.")

            with st.form(f"v27_review_form_{_safe_run_slug(title)}_{selected_idx}"):
                default_decision = str(selected_row.get("review_recommendation") or "watchlist")
                default_idx = REVIEW_DECISION_OPTIONS.index(default_decision) if default_decision in REVIEW_DECISION_OPTIONS else REVIEW_DECISION_OPTIONS.index("watchlist")
                decision = st.selectbox("Manual review decision", options=REVIEW_DECISION_OPTIONS, index=default_idx)
                reviewer_note = st.text_area("Reviewer note", value="", placeholder="Why promote/reject/watchlist? Mention sample size, side/regime, friction, and whether to test again.")
                create_draft = st.checkbox("Also save draft strategy JSON under data/backtest_reviews/strategy_drafts", value=(default_decision == "promote_candidate" and bool(draft_payload)))
                submitted = st.form_submit_button("Save V27 review decision")
                if submitted:
                    draft_path = ""
                    if create_draft and draft_payload:
                        saved_draft = save_strategy_draft_file(draft_payload, label=str(selected_row.get("candidate_run_name") or "v27_candidate"))
                        draft_path = str(saved_draft)
                    log_path = append_review_decision(selected_row, decision=decision, reviewer_note=reviewer_note, draft_path=draft_path)
                    st.success(f"Saved V27 review decision to {log_path}" + (f" and draft to {draft_path}" if draft_path else "."))
            review_log = load_review_decisions()
            if not review_log.empty:
                with st.expander("V27 review audit trail", expanded=False):
                    st.dataframe(review_log.tail(50).iloc[::-1], width="stretch", hide_index=True)

    if trade_loaded:
        st.markdown("#### V28 Strategy Draft Import + Cross-Validation Lab")
        st.caption("V28 tests V27 strategy drafts across symbols and date folds before they become real strategy versions. It queues source-vs-candidate CV jobs and keeps final promotion manual.")
        draft_df = load_strategy_drafts()
        if draft_df.empty:
            st.info("No strategy draft JSON files found yet. In V27, save a promoted/watchlist draft under data/backtest_reviews/strategy_drafts first.")
        else:
            with st.expander("V28 queue cross-validation from a draft", expanded=False):
                st.dataframe(draft_df[[c for c in ["draft_file", "strategy_name", "score_threshold", "source_run_name", "candidate_run_name", "review_recommendation", "side", "regime_group", "regime_detail"] if c in draft_df.columns]].head(100), width="stretch", hide_index=True)
                draft_labels = [f"{idx} | {row.get('strategy_name')} | score>={float(row.get('score_threshold') or 0):.0f} | {row.get('draft_file')}" for idx, row in draft_df.iterrows()]
                selected_draft_label = st.selectbox("Draft to validate", options=draft_labels, index=0, key=f"v28_draft_select_{_safe_run_slug(title)}")
                selected_draft_idx = int(str(selected_draft_label).split(" | ", 1)[0])
                selected_draft_row = draft_df.loc[selected_draft_idx]
                draft_path = str(selected_draft_row.get("draft_path") or "")
                draft_payload = load_strategy_draft(draft_path)

                source_labels = []
                for idx, (run_name, _trades, loaded) in enumerate(trade_loaded):
                    manifest = loaded.get("manifest") or {}
                    summary = manifest.get("summary") or {}
                    source_labels.append(f"{idx} | trades {int(float(summary.get('total_trades') or 0))} | PF {float(summary.get('profit_factor') or 0):.2f} | {run_name}")
                selected_source_label = st.selectbox("Source run to compare against", options=source_labels, index=0, key=f"v28_source_select_{_safe_run_slug(title)}")
                source_idx = int(str(selected_source_label).split(" | ", 1)[0])
                source_name, _source_trades, source_loaded = trade_loaded[source_idx]
                source_manifest = dict(source_loaded.get("manifest") or {})
                source_manifest["run_dir"] = str(source_loaded.get("run_dir") or source_manifest.get("run_dir") or "")
                source_cfg = source_manifest.get("config") or {}
                default_symbols = ", ".join(source_cfg.get("symbols") or [])
                cols = st.columns(5)
                symbol_text = cols[0].text_input("Symbols", value=default_symbols, key=f"v28_symbols_{_safe_run_slug(title)}")
                fold_count = cols[1].number_input("Date folds", min_value=1, max_value=8, value=3, step=1, key=f"v28_folds_{_safe_run_slug(title)}")
                min_days = cols[2].number_input("Min days/fold", min_value=1, max_value=90, value=14, step=1, key=f"v28_min_days_{_safe_run_slug(title)}")
                max_symbols = cols[3].number_input("Max symbols", min_value=1, max_value=20, value=min(6, max(1, len(source_cfg.get('symbols') or []))), step=1, key=f"v28_max_symbols_{_safe_run_slug(title)}")
                include_source = cols[4].checkbox("Include source baseline", value=True, key=f"v28_include_source_{_safe_run_slug(title)}")
                symbols = [s.strip().upper() for s in symbol_text.replace(";", ",").split(",") if s.strip()]
                st.caption(f"This will create one V28 CV job per date fold. Each job contains source/candidate tasks per symbol. Source period: {source_cfg.get('start_date')} → {source_cfg.get('end_date')}.")
                if st.button("Queue V28 cross-validation jobs", width="stretch", key=f"queue_v28_cv_{_safe_run_slug(title)}", disabled=not bool(draft_payload and source_manifest and symbols)):
                    jobs_count, tasks_count, preview_df, cv_id = queue_cross_validation_jobs(
                        source_manifest=source_manifest,
                        candidate_payload=draft_payload,
                        draft_path=draft_path,
                        symbols=symbols,
                        fold_count=int(fold_count),
                        min_days_per_fold=int(min_days),
                        include_source_baseline=bool(include_source),
                        max_symbols=int(max_symbols),
                        comment=f"V28 cross-validation from Backtest Lab. Draft={draft_path}. Source={source_name}",
                    )
                    if jobs_count:
                        st.success(f"Queued {jobs_count} V28 cross-validation jobs with {tasks_count} tasks. CV id: {cv_id}")
                        st.dataframe(preview_df, width="stretch", hide_index=True)
                    else:
                        st.warning("No V28 jobs were queued. Check that the source run has dates/symbols and that the draft JSON loaded correctly.")

        try:
            v28_reports = build_cv_reports(list_jobs(["completed"]))
        except Exception as exc:
            v28_reports = {"cv_results": pd.DataFrame(), "cv_pairs": pd.DataFrame(), "cv_aggregate": pd.DataFrame()}
            st.warning(f"Could not build V28 CV reports: {exc}")
        cv_agg = v28_reports.get("cv_aggregate", pd.DataFrame())
        cv_pairs = v28_reports.get("cv_pairs", pd.DataFrame())
        if cv_agg is None or cv_agg.empty:
            st.info("No completed V28 cross-validation results found yet. Queue V28 jobs, let the worker finish, then return here.")
        else:
            st.write("V28 cross-validation aggregate")
            st.dataframe(cv_agg, width="stretch", hide_index=True)
            cv_labels = [f"{idx} | {row.get('v28_recommendation')} | confidence {row.get('promotion_confidence')} | win {float(row.get('cv_win_rate_pct') or 0):.0f}% | ΔPnL ${float(row.get('sum_delta_pnl_usd') or 0):.2f} | {row.get('candidate_name')}" for idx, row in cv_agg.iterrows()]
            selected_cv_label = st.selectbox("Review CV result", options=cv_labels, index=0, key=f"v28_cv_select_{_safe_run_slug(title)}")
            selected_cv_idx = int(str(selected_cv_label).split(" | ", 1)[0])
            selected_cv_row = cv_agg.loc[selected_cv_idx]
            mcols = st.columns(5)
            _metric(mcols[0], "CV win rate", f"{float(selected_cv_row.get('cv_win_rate_pct') or 0):.0f}%")
            _metric(mcols[1], "Pairs", str(int(float(selected_cv_row.get('tested_pairs') or 0))))
            _metric(mcols[2], "Δ net PnL", f"${float(selected_cv_row.get('sum_delta_pnl_usd') or 0):,.2f}")
            _metric(mcols[3], "Avg Δ PF", f"{float(selected_cv_row.get('avg_delta_profit_factor') or 0):.2f}")
            _metric(mcols[4], "Confidence", str(selected_cv_row.get("promotion_confidence") or "low"))
            with st.expander("V28 fold/symbol details", expanded=False):
                part = cv_pairs.loc[cv_pairs.get("cv_id") == selected_cv_row.get("cv_id")].copy() if isinstance(cv_pairs, pd.DataFrame) and not cv_pairs.empty and "cv_id" in cv_pairs.columns else pd.DataFrame()
                if not part.empty:
                    st.dataframe(part, width="stretch", hide_index=True)
                else:
                    st.info("No pair-level rows found for this CV id.")
            with st.form(f"v28_cv_review_form_{_safe_run_slug(title)}_{selected_cv_idx}"):
                default_decision = str(selected_cv_row.get("v28_recommendation") or "watchlist")
                default_idx = CV_DECISION_OPTIONS.index(default_decision) if default_decision in CV_DECISION_OPTIONS else CV_DECISION_OPTIONS.index("watchlist")
                cv_decision = st.selectbox("Manual CV decision", options=CV_DECISION_OPTIONS, index=default_idx)
                cv_note = st.text_area("CV review note", value="", placeholder="Mention folds, symbols, sample size, friction, and whether this is ready for manual versioning.")
                submitted_cv = st.form_submit_button("Save V28 CV review decision")
                if submitted_cv:
                    log_path = append_cv_review_decision(selected_cv_row, decision=cv_decision, reviewer_note=cv_note)
                    st.success(f"Saved V28 CV review decision to {log_path}")

            draft_path_for_cv = str(selected_cv_row.get("draft_path") or "")
            draft_payload_for_cv = load_strategy_draft(draft_path_for_cv) if draft_path_for_cv else {}
            if draft_payload_for_cv:
                with st.expander("Manual save draft as new strategy version", expanded=False):
                    st.warning("This only creates a new strategy version in the local Strategy Library. It does not assign it to a live slot and does not change live trading automatically.")
                    storage = Storage()
                    lib = storage.get_latest_strategy_versions()
                    if lib.empty:
                        st.info("No existing strategies found in the local library.")
                    else:
                        option_rows = []
                        for _, row in lib.iterrows():
                            option_rows.append(f"{int(row.get('strategy_id'))} | v{int(row.get('version_no'))} | {row.get('strategy_name')}")
                        target_label = st.selectbox("Save under existing strategy", options=option_rows, key=f"v28_target_strategy_{_safe_run_slug(title)}_{selected_cv_idx}")
                        target_strategy_id = int(str(target_label).split(" | ", 1)[0])
                        if st.button("Save selected draft as new strategy version", type="primary", key=f"v28_save_version_{_safe_run_slug(title)}_{selected_cv_idx}"):
                            version_id = save_draft_as_strategy_version(storage, draft_payload_for_cv, strategy_id=target_strategy_id, cv_row=selected_cv_row)
                            append_cv_review_decision(selected_cv_row, decision="promote_after_cv", reviewer_note="Saved draft as new strategy version from V28 UI.", saved_version_id=str(version_id))
                            st.success(f"Saved new strategy version id {version_id}. Assign it manually in the main Strategy Library/slots before using live.")
            cv_log = load_cv_review_decisions()
            if not cv_log.empty:
                with st.expander("V28 CV review audit trail", expanded=False):
                    st.dataframe(cv_log.tail(50).iloc[::-1], width="stretch", hide_index=True)

    if trade_loaded:
        st.caption(_palette_caption("Outcome", OUTCOME_COLOR_MAP))
        st.caption(_palette_caption("TP", TP_COLOR_MAP))
        for idx, (run_name, trades, loaded) in enumerate(trade_loaded):
            with st.expander(f"Per-run charts — {run_name}", expanded=(idx == 0)):
                cols = st.columns(3)
                if isinstance(trades, pd.DataFrame) and not trades.empty:
                    with cols[0]:
                        st.plotly_chart(_mfe_mae_scatter(trades).update_layout(title=f"MFE/MAE — {run_name}", height=280), width="stretch", key=f"compare_mfe_{idx}_{_safe_run_slug(run_name)}")
                    with cols[1]:
                        outcomes = trades["outcome_label"].fillna("OPEN").value_counts().reset_index()
                        outcomes.columns = ["outcome_label", "trades"]
                        out_fig = _pie_with_palette(outcomes, "outcome_label", "trades", f"Outcome — {run_name}", OUTCOME_COLOR_MAP)
                        st.plotly_chart(out_fig, width="stretch", key=f"compare_outcome_{idx}_{_safe_run_slug(run_name)}")
                    with cols[2]:
                        tp_counts = pd.DataFrame({
                            "bucket": ["Exited before TP1", "TP1", "TP2", "TP3", "Final TP"],
                            "trades": [
                                int((trades["highest_tp_hit"] <= 0).sum()),
                                int((trades["highest_tp_hit"] == 1).sum()),
                                int((trades["highest_tp_hit"] == 2).sum()),
                                int((trades["highest_tp_hit"] == 3).sum()),
                                int(trades["outcome_label"].fillna("").str.contains("FINAL").sum()),
                            ],
                        })
                        tp_fig = _pie_with_palette(tp_counts, "bucket", "trades", f"Highest TP reached — {run_name}", TP_COLOR_MAP)
                        st.plotly_chart(tp_fig, width="stretch", key=f"compare_tp_{idx}_{_safe_run_slug(run_name)}")

        table_choice = st.radio(f"Trade table view — {title}", options=[name for name, _, _ in trade_loaded], horizontal=True, key=f"compare_radio_{_safe_run_slug(title)}")
        selected = next(((name, trades, loaded) for name, trades, loaded in trade_loaded if name == table_choice), None)
        if selected is not None:
            st.write(f"Trade ledger for {table_choice}")
            st.dataframe(selected[1], width="stretch", hide_index=True)


def _metric(col, label: str, value: str) -> None:
    try:
        col.metric(label, value, help=KPI_HELP.get(label), border=True)
    except TypeError:
        col.metric(label, value)
        if label in KPI_HELP:
            col.caption(KPI_HELP[label])


def _render_summary_metrics(summary: dict[str, Any]) -> None:
    row1 = st.columns(6)
    _metric(row1[0], "Trades", int(summary.get("total_trades", 0)))
    _metric(row1[1], "Win rate", f"{summary.get('win_rate', 0):.2f}%")
    _metric(row1[2], "Loss rate", f"{summary.get('loss_rate', 0):.2f}%")
    _metric(row1[3], "Breakeven", f"{summary.get('breakeven_rate', 0):.2f}%")
    _metric(row1[4], "Partial profit", f"{summary.get('partial_profit_rate', 0):.2f}%")
    _metric(row1[5], "Total PnL", f"${summary.get('total_pnl_usd', 0):,.2f}")

    row2 = st.columns(6)
    _metric(row2[0], "Total PnL %", f"{summary.get('total_pnl_pct', 0):.2f}%")
    _metric(row2[1], "Median trade %", f"{summary.get('median_pnl_pct_per_trade', 0):.2f}%")
    _metric(row2[2], "Median trade $", f"${summary.get('median_pnl_usd_per_trade', 0):,.2f}")
    _metric(row2[3], "Expectancy (R)", f"{summary.get('expectancy_r', 0):.3f}")
    _metric(row2[4], "Profit factor", f"{summary.get('profit_factor', 0):.2f}")
    _metric(row2[5], "Payoff ratio", f"{summary.get('payoff_ratio', 0):.2f}")

    row3 = st.columns(6)
    _metric(row3[0], "Max DD", f"${summary.get('max_drawdown_usd', 0):,.2f}")
    _metric(row3[1], "Avg duration", f"{summary.get('avg_trade_duration_hours', 0):.2f}h")
    _metric(row3[2], "Avg to TP1", f"{summary.get('avg_time_to_tp1_hours', 0):.2f}h")
    _metric(row3[3], "Avg MFE", f"{summary.get('avg_mfe_pct', 0):.2f}%")
    _metric(row3[4], "Avg MAE", f"{summary.get('avg_mae_pct', 0):.2f}%")
    _metric(row3[5], "Slippage proxy", f"{summary.get('avg_slippage_proxy_pct', 0):.3f}%")

    row4 = st.columns(6)
    _metric(row4[0], "TP1 hit", f"{summary.get('tp1_hit_rate', 0):.2f}%")
    _metric(row4[1], "TP2 hit", f"{summary.get('tp2_hit_rate', 0):.2f}%")
    _metric(row4[2], "TP3 hit", f"{summary.get('tp3_hit_rate', 0):.2f}%")
    _metric(row4[3], "Final TP hit", f"{summary.get('final_tp_hit_rate', 0):.2f}%")
    _metric(row4[4], "BE saves", int(summary.get('breakeven_saves_count', 0)))
    _metric(row4[5], "TP1-lock stops", int(summary.get('stopped_at_tp1_lock_count', 0)))

    row5 = st.columns(6)
    _metric(row5[0], "Avg score", f"{summary.get('avg_score', 0):.2f}")
    _metric(row5[1], "Winner score", f"{summary.get('avg_score_winners', 0):.2f}")
    _metric(row5[2], "Loser score", f"{summary.get('avg_score_losers', 0):.2f}")
    _metric(row5[3], "Win streak", int(summary.get('longest_win_streak', 0)))
    _metric(row5[4], "Loss streak", int(summary.get('longest_loss_streak', 0)))
    _metric(row5[5], "Stop efficiency", f"{summary.get('stop_efficiency_pct', 0):.2f}%")

    row6 = st.columns(6)
    _metric(row6[0], "Avg R", f"{summary.get('avg_r_multiple', 0):.3f}")
    _metric(row6[1], "Avg MFE R", f"{summary.get('avg_mfe_r', 0):.2f}")
    _metric(row6[2], "Avg MAE R", f"{summary.get('avg_mae_r', 0):.2f}")
    _metric(row6[3], "Avg win $", f"${summary.get('avg_win_usd', 0):,.2f}")
    _metric(row6[4], "Avg loss $", f"${summary.get('avg_loss_usd', 0):,.2f}")
    _metric(row6[5], "Realized target frac", f"{summary.get('avg_realized_fraction_full_target', 0):.2f}")

    row7 = st.columns(5)
    _metric(row7[0], "MFE before stop", f"{summary.get('avg_mfe_before_stop_pct', 0):.2f}%")
    _metric(row7[1], "MAE before TP", f"{summary.get('avg_mae_before_tp_pct', 0):.2f}%")
    _metric(row7[2], "Pre-friction PnL", f"${summary.get('pre_friction_pnl_usd', 0):,.2f}")
    _metric(row7[3], "Friction drag", f"${summary.get('total_execution_cost_usd', 0):,.2f}")
    _metric(row7[4], "Avg friction", f"{summary.get('avg_execution_cost_pct', 0):.3f}%")


def _trade_equity_chart(trades: pd.DataFrame) -> go.Figure:
    ordered = trades.sort_values("exit_time").reset_index(drop=True).copy()
    ordered["trade_number"] = ordered.index + 1
    ordered["cum_pnl_usd"] = ordered["pnl_usd"].cumsum()
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=ordered["trade_number"], y=ordered["cum_pnl_usd"], mode="lines", line_shape="hv", name="Cumulative PnL"))
    fig.update_layout(title="Trade-by-trade equity curve", xaxis_title="Closed trade #", yaxis_title="Cumulative PnL USD", height=340, margin=dict(l=10, r=10, t=50, b=10))
    return fig


def _time_based_chart(trades: pd.DataFrame, freq: str) -> go.Figure:
    frame = trades.copy()
    frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True, errors="coerce")
    if freq == "D":
        frame["bucket"] = frame["exit_time"].dt.strftime("%Y-%m-%d")
    elif freq == "W":
        frame["bucket"] = frame["exit_time"].dt.to_period("W").astype(str)
    else:
        frame["bucket"] = frame["exit_time"].dt.to_period("M").astype(str)
    grouped = frame.groupby("bucket", as_index=False).agg(period_pnl_usd=("pnl_usd", "sum"))
    grouped["cum_pnl_usd"] = grouped["period_pnl_usd"].cumsum()
    fig = go.Figure()
    fig.add_trace(go.Bar(x=grouped["bucket"], y=grouped["period_pnl_usd"], name="Period PnL"))
    fig.add_trace(go.Scatter(x=grouped["bucket"], y=grouped["cum_pnl_usd"], mode="lines+markers", name="Cumulative PnL", yaxis="y2"))
    fig.update_layout(
        title=f"Performance over time ({'daily' if freq=='D' else 'weekly' if freq=='W' else 'monthly'})",
        height=340,
        margin=dict(l=10, r=10, t=50, b=10),
        yaxis=dict(title="Period PnL USD"),
        yaxis2=dict(title="Cumulative PnL USD", overlaying="y", side="right"),
    )
    return fig


def _score_decile_chart(score_df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Bar(x=score_df["score_decile"], y=score_df["total_pnl_usd"], name="Total PnL USD"))
    fig.add_trace(go.Scatter(x=score_df["score_decile"], y=score_df["win_rate"], mode="lines+markers", name="Win rate %", yaxis="y2"))
    fig.update_layout(
        title="Score decile performance",
        height=340,
        margin=dict(l=10, r=10, t=50, b=10),
        yaxis=dict(title="PnL USD"),
        yaxis2=dict(title="Win rate %", overlaying="y", side="right"),
    )
    return fig


def _mfe_mae_scatter(trades: pd.DataFrame) -> go.Figure:
    fig = px.scatter(
        trades,
        x="mae_pct",
        y="mfe_pct",
        color="outcome_label",
        color_discrete_map=OUTCOME_COLOR_MAP,
        hover_data=["symbol", "side", "score", "pnl_pct", "highest_tp_hit"],
        title="MFE vs MAE map",
    )
    fig.update_layout(height=360, margin=dict(l=10, r=10, t=50, b=10), legend_title_text="Outcome")
    return fig


def _render_result(result) -> None:
    st.subheader("Backtest result")
    _render_summary_metrics(result.summary)

    trades = result.trades.copy()
    if not trades.empty:
        trades["pnl_usd"] = result.summary.get("stake_per_trade_usd", 100.0) * trades["pnl_pct"] / 100.0

    left, right = st.columns([1.3, 1])
    with left:
        if not trades.empty:
            st.plotly_chart(_trade_equity_chart(trades), width="stretch", key="single_trade_equity")
            perf_freq = st.radio("Time-based chart bucket", options=["D", "W", "M"], horizontal=True, index=1, key="backtest_perf_freq")
            st.plotly_chart(_time_based_chart(trades, perf_freq), width="stretch", key=f"single_time_based_{perf_freq}")
            st.plotly_chart(_mfe_mae_scatter(trades), width="stretch", key="single_mfe_mae")
    with right:
        if not trades.empty:
            outcomes = trades["outcome_label"].fillna("OPEN").value_counts().reset_index()
            outcomes.columns = ["outcome_label", "trades"]
            out_fig = _pie_with_palette(outcomes, "outcome_label", "trades", "Outcome distribution", OUTCOME_COLOR_MAP)
            out_fig.update_layout(height=320)
            st.plotly_chart(out_fig, width="stretch", key="single_outcome_pie")
            st.caption(_palette_caption("Outcome", OUTCOME_COLOR_MAP))

            tp_counts = pd.DataFrame({
                "bucket": ["No TP", "TP1", "TP2", "TP3", "Final TP"],
                "trades": [
                    int((trades["highest_tp_hit"] <= 0).sum()),
                    int((trades["highest_tp_hit"] == 1).sum()),
                    int((trades["highest_tp_hit"] == 2).sum()),
                    int((trades["highest_tp_hit"] == 3).sum()),
                    int(trades["outcome_label"].fillna("").str.contains("FINAL").sum()),
                ],
            })
            tp_fig = _pie_with_palette(tp_counts, "bucket", "trades", "Highest TP reached distribution", TP_COLOR_MAP)
            tp_fig.update_layout(height=320)
            st.plotly_chart(tp_fig, width="stretch", key="single_tp_pie")
            st.caption(_palette_caption("TP", TP_COLOR_MAP))

    if not result.performance_by_score_decile.empty:
        st.plotly_chart(_score_decile_chart(result.performance_by_score_decile), width="stretch", key="single_decile_chart")

    tab_perf, tab_counter, tab_friction, tab_tables, tab_trades = st.tabs(["Performance blocks", "Counterfactual quick checks", "Friction / bundle validation", "Tables", "Trade ledger"])

    with tab_perf:
        perf_left, perf_right = st.columns(2)
        with perf_left:
            if not result.performance_by_symbol.empty:
                st.write("Performance by symbol")
                st.dataframe(result.performance_by_symbol, width="stretch", hide_index=True)
            if not result.performance_by_hour.empty:
                st.write("Performance by entry hour")
                st.dataframe(result.performance_by_hour, width="stretch", hide_index=True)
        with perf_right:
            if not result.performance_by_weekday.empty:
                st.write("Performance by weekday")
                st.dataframe(result.performance_by_weekday, width="stretch", hide_index=True)
            if not result.performance_by_score_decile.empty:
                st.write("Score decile performance")
                st.dataframe(result.performance_by_score_decile, width="stretch", hide_index=True)

        st.divider()
        seg_left, seg_right = st.columns(2)
        with seg_left:
            if getattr(result, "performance_by_side", pd.DataFrame()).empty is False:
                st.write("Long / short split")
                st.dataframe(result.performance_by_side, width="stretch", hide_index=True)
            if getattr(result, "performance_by_side_regime", pd.DataFrame()).empty is False:
                st.write("Side × regime split")
                st.dataframe(result.performance_by_side_regime, width="stretch", hide_index=True)
        with seg_right:
            if getattr(result, "performance_by_regime", pd.DataFrame()).empty is False:
                st.write("Regime split")
                st.dataframe(result.performance_by_regime, width="stretch", hide_index=True)
            if getattr(result, "performance_by_exit_family", pd.DataFrame()).empty is False:
                st.write("Exit-family split")
                st.dataframe(result.performance_by_exit_family, width="stretch", hide_index=True)
        if getattr(result, "performance_by_score_side_decile", pd.DataFrame()).empty is False:
            st.write("Score deciles by side")
            st.dataframe(result.performance_by_score_side_decile, width="stretch", hide_index=True)
        if getattr(result, "performance_by_detailed_regime", pd.DataFrame()).empty is False:
            st.write("V25 detailed regime split")
            st.dataframe(result.performance_by_detailed_regime, width="stretch", hide_index=True)
        if getattr(result, "performance_by_side_detailed_regime", pd.DataFrame()).empty is False:
            st.write("V25 side × detailed regime split")
            st.dataframe(result.performance_by_side_detailed_regime, width="stretch", hide_index=True)
        if getattr(result, "threshold_recommendations", pd.DataFrame()).empty is False:
            st.write("V25 threshold recommendations")
            st.caption("Analytics-only: use these to queue what-if tests, not to blindly rewrite strategy configs.")
            st.dataframe(result.threshold_recommendations, width="stretch", hide_index=True)

        if not trades.empty:
            best = trades.sort_values("pnl_pct", ascending=False).head(10)
            worst = trades.sort_values("pnl_pct").head(10)
            c1, c2 = st.columns(2)
            with c1:
                st.write("Best trades")
                st.dataframe(best[["symbol", "entry_time", "side", "score", "outcome_label", "pnl_pct", "mfe_pct", "mae_pct", "regime"]], width="stretch", hide_index=True)
            with c2:
                st.write("Worst trades")
                st.dataframe(worst[["symbol", "entry_time", "side", "score", "outcome_label", "pnl_pct", "mfe_pct", "mae_pct", "regime"]], width="stretch", hide_index=True)

    with tab_counter:
        if not result.counterfactuals.empty:
            st.caption("These quick checks reuse the existing trade set and show what happens if you keep only aligned or stronger-score trades. They are fast directional checks, not full reruns.")
            st.dataframe(result.counterfactuals, width="stretch", hide_index=True)
        else:
            st.info("No counterfactual quick checks available for this run.")

    with tab_friction:
        left_f, right_f = st.columns(2)
        with left_f:
            if getattr(result, "friction_comparison", pd.DataFrame()).empty is False:
                st.write("Gross vs net execution-friction view")
                st.dataframe(result.friction_comparison, width="stretch", hide_index=True)
            else:
                st.info("No friction comparison available.")
        with right_f:
            if getattr(result, "performance_by_owner", pd.DataFrame()).empty is False:
                st.write("Single/bundle owner performance")
                st.dataframe(result.performance_by_owner, width="stretch", hide_index=True)
            else:
                st.info("No owner split available.")
        if getattr(result, "bundle_validation", pd.DataFrame()).empty is False:
            st.write("Bundle validation")
            st.caption("For a pure bundle run this mainly confirms bundle-owned trades. For mixed saved outputs it compares bundle and single ownership.")
            st.dataframe(result.bundle_validation, width="stretch", hide_index=True)

    with tab_tables:
        period_choice = st.radio("Period table", options=["Day", "Week", "Month"], horizontal=True, key="period_table_choice")
        if period_choice == "Day" and not result.performance_by_day.empty:
            st.dataframe(result.performance_by_day, width="stretch", hide_index=True)
        elif period_choice == "Week" and not result.performance_by_week.empty:
            st.dataframe(result.performance_by_week, width="stretch", hide_index=True)
        elif period_choice == "Month" and not result.performance_by_month.empty:
            st.dataframe(result.performance_by_month, width="stretch", hide_index=True)
        else:
            st.info("No rows for the selected period table.")

    with tab_trades:
        st.dataframe(result.trades, width="stretch", hide_index=True)

    st.write("Conclusions")
    for item in result.conclusions:
        st.markdown(f"- {item}")
    st.write("Suggestions")
    for item in result.suggestions:
        st.markdown(f"- {item}")
    st.info("V24 view: inspect gross-vs-net friction, owner split, side/regime tables, exit-family results, and score deciles before trusting a blended PnL result.")


def _render_saved_comparison() -> None:
    saved = list_saved_backtests()
    saved = sorted(saved, key=lambda item: (not bool(item.get("favorite", False)), item.get("created_at") or ""), reverse=False)
    st.subheader("Saved backtests and comparison")
    if not saved:
        st.info("No saved backtests yet.")
        return

    view_state = _load_backtest_view_state()
    fcols = st.columns([1,1,1,1,1,1,1])
    show_favorites_only = fcols[0].checkbox("Favorites only", value=bool(view_state.get("favorites_only", False)), key="saved_favorites_only")
    strategy_filter = fcols[1].text_input("Filter strategy", value=str(view_state.get("strategy_filter", "")), key="saved_strategy_filter")
    symbol_filter = fcols[2].text_input("Filter symbol", value=str(view_state.get("symbol_filter", "")), key="saved_symbol_filter")
    min_profit_factor = float(fcols[3].number_input("Min PF", value=float(view_state.get("min_profit_factor", 0.0) or 0.0), step=0.1, key="saved_min_pf"))
    min_total_pnl = float(fcols[4].number_input("Min total PnL $", value=float(view_state.get("min_total_pnl", -999999.0) or -999999.0), step=10.0, key="saved_min_pnl"))
    min_trades = int(fcols[5].number_input("Min trades", value=int(view_state.get("min_trades", 0) or 0), step=1, key="saved_min_trades"))
    compare_limit = int(fcols[6].selectbox("Compare limit", options=[2,3,5,10], index=[2,3,5,10].index(int(view_state.get("compare_limit", 10) or 10)), key="saved_compare_limit"))
    _save_backtest_view_state({"favorites_only": show_favorites_only, "strategy_filter": strategy_filter, "symbol_filter": symbol_filter, "min_profit_factor": min_profit_factor, "min_total_pnl": min_total_pnl, "min_trades": min_trades, "compare_limit": compare_limit})

    filtered = []
    for item in saved:
        strategy_name = ((item.get("strategy_payload") or {}).get("strategy_name") or "")
        symbols = ", ".join((item.get("config") or {}).get("symbols") or [])
        summary = item.get("summary") or {}
        if show_favorites_only and not bool(item.get("favorite", False)):
            continue
        if strategy_filter and strategy_filter.lower() not in strategy_name.lower():
            continue
        if symbol_filter and symbol_filter.lower() not in symbols.lower():
            continue
        if float(summary.get("profit_factor") or 0.0) < min_profit_factor:
            continue
        if float(summary.get("total_pnl_usd") or 0.0) < min_total_pnl:
            continue
        if int(summary.get("total_trades") or 0) < min_trades:
            continue
        filtered.append(item)

    saved_df = pd.DataFrame([{
        "favorite": bool(item.get("favorite", False)),
        "name": item.get("name"),
        "created_at": item.get("created_at"),
        "strategy": (item.get("strategy_payload") or {}).get("strategy_name"),
        "symbols": ", ".join((item.get("config") or {}).get("symbols") or []),
        "entry_tf": (item.get("config") or {}).get("entry_timeframe"),
        "analysis_tf": (item.get("config") or {}).get("analysis_timeframe"),
        "start_date": (item.get("config") or {}).get("start_date"),
        "end_date": (item.get("config") or {}).get("end_date"),
        "run_kind": item.get("run_kind", (item.get("config") or {}).get("run_kind", "backtest")),
        "profit_factor": (item.get("summary") or {}).get("profit_factor"),
        "total_pnl_usd": (item.get("summary") or {}).get("total_pnl_usd"),
        "pre_friction_pnl_usd": (item.get("summary") or {}).get("pre_friction_pnl_usd"),
        "friction_drag_usd": (item.get("summary") or {}).get("total_execution_cost_usd"),
        "total_trades": (item.get("summary") or {}).get("total_trades"),
        "comment": item.get("comment") or "",
        "run_dir": item.get("run_dir"),
    } for item in filtered])
    if saved_df.empty:
        st.info("No saved backtests match the current filters.")
        return
    edited_df = st.data_editor(
        saved_df, width="stretch", hide_index=True,
        disabled=[c for c in saved_df.columns if c != "favorite"],
        key="saved_runs_editor",
        column_config={"favorite": st.column_config.CheckboxColumn("favorite", help="Mark as favorite")},
    )
    action_cols = st.columns(8)
    if action_cols[0].button("Save favorite edits", width="stretch"):
        try:
            for _, row in edited_df.iterrows():
                original = next((item for item in filtered if item.get("run_dir") == row.get("run_dir")), None)
                if original is None:
                    continue
                new_val = bool(row.get("favorite", False))
                if bool(original.get("favorite", False)) != new_val:
                    update_saved_backtest_manifest(row["run_dir"], {"favorite": new_val})
            st.success("Favorite flags updated.")
            st.rerun()
        except Exception as exc:
            st.error(f"Could not save favorite edits: {exc}")

    labels = [f"{'★ ' if item.get('favorite') else ''}{item.get('name')} | {(item.get('config') or {}).get('start_date') or '—'}..{(item.get('config') or {}).get('end_date') or '—'} | PF {((item.get('summary') or {}).get('profit_factor'))} | PnL ${((item.get('summary') or {}).get('total_pnl_usd') or 0)} | Trades {((item.get('summary') or {}).get('total_trades') or 0)}" for item in filtered]
    default_selected = labels[: min(compare_limit, len(labels))]
    selected = st.multiselect(f"Compare up to {compare_limit} saved runs", options=labels, default=default_selected, key="saved_compare_selected")
    if len(selected) > compare_limit:
        st.warning(f"Pick up to {compare_limit} runs for comparison.")
        selected = selected[:compare_limit]
    chosen = [filtered[labels.index(label)] for label in selected]

    if action_cols[1].button("Compare filtered", width="stretch"):
        chosen = filtered[:compare_limit]
    if action_cols[2].button("Compare favorites", width="stretch"):
        chosen = [item for item in filtered if bool(item.get("favorite", False))][:compare_limit]
    if action_cols[3].button("Favorite selected", width="stretch") and chosen:
        for item in chosen:
            update_saved_backtest_manifest(item["run_dir"], {"favorite": True})
        st.rerun()
    if action_cols[4].button("Delete selected", width="stretch") and chosen:
        for item in chosen:
            delete_saved_backtest(item["run_dir"])
        st.rerun()
    if action_cols[5].button("Load first selected run config", width="stretch") and chosen:
        first = chosen[0]
        cfg = first.get("config") or {}
        st.session_state["backtest_source_root"] = str((cfg.get("source_root") or OHLCV_STORE_ROOT))
        st.session_state["backtest_override_json"] = json.dumps({}, indent=2)
        st.session_state["backtest_config_json"] = json.dumps({k:v for k,v in cfg.items() if k not in {"symbols", "entry_timeframe", "analysis_timeframe", "start_date", "end_date", "source_root", "run_kind", "what_if_config", "base_run"}}, indent=2)
        st.session_state["backtest_matrix_config_json"] = json.dumps((cfg.get("what_if_config") or first.get("what_if_config") or DEFAULT_MATRIX_CONFIG), indent=2)
        st.session_state["backtest_bundle_config_json"] = json.dumps((cfg.get("bundle_config") or first.get("bundle_config") or DEFAULT_BUNDLE_CONFIG), indent=2)
        st.session_state["backtest_run_mode"] = "Bundle consensus" if ((first.get("strategy_payload") or {}).get("components")) else "Single strategy"
        st.session_state["load_saved_run_meta"] = {
            "symbols": cfg.get("symbols") or [],
            "entry_timeframe": cfg.get("entry_timeframe") or "5m",
            "analysis_timeframe": cfg.get("analysis_timeframe") or "1h",
            "start_date": cfg.get("start_date") or "2024-01-01",
            "end_date": cfg.get("end_date") or str(pd.Timestamp.utcnow().date()),
            "strategy_name": (first.get("strategy_payload") or {}).get("strategy_name") or "",
        }
        st.success("Loaded selected run metadata into the form state. Adjust fields if needed.")
    if action_cols[6].button("Queue what-if for selected", width="stretch") and chosen:
        queued = 0
        for item in chosen:
            cfg = item.get("config") or {}
            strategy_payload = item.get("strategy_payload") or {}
            matrix_cfg = cfg.get("what_if_config") or item.get("what_if_config") or DEFAULT_MATRIX_CONFIG
            tasks = build_what_if_tasks(strategy_payload, cfg, matrix_config=matrix_cfg)
            create_task_job(
                source_root=str(cfg.get("source_root") or OHLCV_STORE_ROOT),
                symbols=cfg.get("symbols") or [],
                entry_timeframe=cfg.get("entry_timeframe") or "5m",
                analysis_timeframe=cfg.get("analysis_timeframe") or "1h",
                start_date=cfg.get("start_date") or "2024-01-01",
                end_date=cfg.get("end_date") or str(pd.Timestamp.utcnow().date()),
                base_config={k:v for k,v in cfg.items() if k not in {"symbols","entry_timeframe","analysis_timeframe","start_date","end_date","source_root","run_kind","what_if_config","base_run"}},
                tasks=[{**task, "symbols": cfg.get("symbols") or []} for task in tasks],
                comment=item.get("comment") or item.get("name") or "",
                job_type="what_if_matrix",
                extra={"what_if_config": matrix_cfg, "base_run": item.get("run_dir") or ""},
            )
            queued += 1
        st.success(f"Queued what-if jobs for {queued} selected saved runs.")
    export_runs = chosen or filtered[:compare_limit]
    action_cols[7].download_button("Download filtered for AI", data=_build_saved_runs_export_bytes(export_runs), file_name="saved_backtests_for_ai.zip", mime="application/zip", use_container_width=True)

    if chosen:
        _render_run_comparison(chosen[:compare_limit], title="Saved run comparison")


def _what_if_chart(matrix_df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Bar(x=matrix_df["scenario"], y=matrix_df["total_pnl_usd"], name="Total PnL USD"))
    fig.add_trace(go.Scatter(x=matrix_df["scenario"], y=matrix_df["expectancy_r"], mode="lines+markers", name="Expectancy (R)", yaxis="y2"))
    fig.update_layout(
        title="What-if rerun matrix",
        height=360,
        margin=dict(l=10, r=10, t=50, b=10),
        yaxis=dict(title="Total PnL USD"),
        yaxis2=dict(title="Expectancy (R)", overlaying="y", side="right"),
    )
    return fig


def _render_what_if_matrix(matrix_df: pd.DataFrame) -> None:
    st.subheader("What-if rerun matrix")
    if matrix_df is None or matrix_df.empty:
        st.info("No what-if rerun matrix has been generated yet.")
        return
    st.caption("These are true reruns with changed stop width, TP ladder, score threshold, and confirmation entry rules.")
    st.plotly_chart(_what_if_chart(matrix_df), width="stretch", key="what_if_chart")
    st.dataframe(matrix_df, width="stretch", hide_index=True)


def _render_job_queue_section() -> None:
    st.subheader("Backtest worker queue")
    jobs = list_jobs()
    health = read_worker_heartbeat()
    queued_count = sum(1 for job in jobs if job.get("status") == "queued")
    running_count = sum(1 for job in jobs if job.get("status") == "running")
    failed_count = sum(1 for job in jobs if job.get("status") == "failed")
    if health.get("alive"):
        st.success(f"Backtest worker heartbeat OK: {health.get('state')} | job {health.get('current_job_id') or '-'} | age {health.get('age_seconds', 0):.1f}s")
    else:
        if queued_count or running_count:
            st.warning(
                "Backtest worker does not look active for this project folder. "
                "Queued jobs will stay at 0% until the worker is running from the same folder. "
                "Open a separate PowerShell in this package and run: .\\start_backtest_worker.bat"
            )
            st.caption(f"Worker heartbeat: {health.get('reason')} | path: {health.get('heartbeat_path')}")
        else:
            st.caption(f"Worker heartbeat not active yet: {health.get('reason')}")
    if failed_count:
        st.error(f"There are {failed_count} failed worker job(s). Select a failed row below and inspect the error/traceback.")
    if running_count:
        st.info(f"{running_count} job(s) are running. Queued jobs below remain at 0% until earlier running/queued jobs finish.")
    elif queued_count and not health.get("alive"):
        st.info("Most likely cause: worker window is closed, crashed, or running from another copy of the package.")
    if not jobs:
        st.info("No worker jobs yet. Start start_backtest_worker.bat, then queue selected strategies below.")
        return
    job_rows = []
    for job in jobs:
        progress = job.get("progress") or {}
        pct = progress.get("pct")
        if pct is None:
            total = int(progress.get("total") or 0)
            pct = round((int(progress.get("completed") or 0) / total) * 100, 2) if total else 0.0
        base_run = job.get("base_run") or {}
        if isinstance(base_run, dict):
            base_run = base_run.get("run_dir") or base_run.get("job_id") or ""
        task_names = []
        for task in (job.get("tasks") or []):
            nm = task.get("name") or ((task.get("strategy_payload") or {}).get("strategy_name")) or ""
            if nm and nm not in task_names:
                task_names.append(nm)
        job_rows.append({
            "job_id": job.get("job_id"),
            "status": job.get("status"),
            "job_type": job.get("job_type"),
            "base_run": base_run,
            "created_at": job.get("created_at"),
            "start_date": job.get("start_date"),
            "end_date": job.get("end_date"),
            "symbols": ", ".join(job.get("symbols") or []),
            "strategies": " | ".join(task_names[:6]) + (" ..." if len(task_names) > 6 else ""),
            "entry_tf": job.get("entry_timeframe"),
            "analysis_tf": job.get("analysis_timeframe"),
            "done": progress.get("completed", 0),
            "total": progress.get("total", 0),
            "progress_%": pct,
            "elapsed": _fmt_seconds(progress.get("elapsed_seconds")),
            "eta": _fmt_seconds(progress.get("eta_seconds")),
            "current_strategy": progress.get("current_strategy"),
            "error": job.get("error", ""),
        })
    st.dataframe(pd.DataFrame(job_rows), width="stretch", hide_index=True)
    job_labels = [f"{job.get('job_id')} | {job.get('status')} | {job.get('job_type')}" for job in jobs]
    selected_label = st.selectbox("Inspect/edit a queue item", options=job_labels, index=0, key="queue_inspect_select")
    selected_job = jobs[job_labels.index(selected_label)]
    full_job = load_job(selected_job.get("path") or selected_job.get("job_id"))
    pct_value = float((full_job.get('progress') or {}).get('pct') or 0.0)
    st.progress(max(0.0, min(pct_value / 100.0, 1.0)), text=f"Selected job progress: {pct_value:.2f}% | elapsed {_fmt_seconds(full_job.get('progress', {}).get('elapsed_seconds'))} | eta {_fmt_seconds(full_job.get('progress', {}).get('eta_seconds'))}")
    if full_job.get("job_type") in {"what_if_matrix", "combined_backtest_whatif", "bundle_what_if_matrix", "v26_recommendation_what_if"}:
        matrix_json = st.text_area("What-if matrix config JSON", value=json.dumps(full_job.get("what_if_config") or {}, indent=2, ensure_ascii=False), height=160, key=f"matrix_json_{full_job.get('job_id')}")
    else:
        matrix_json = None
    raw_json = st.text_area("Queued job JSON", value=json.dumps(full_job, indent=2, ensure_ascii=False), height=240, key=f"job_json_{full_job.get('job_id')}")
    job_cols = st.columns(4)
    if job_cols[0].button("Save queued job edits", width="stretch", key=f"save_job_{full_job.get('job_id')}") and full_job.get("status") == "queued":
        try:
            payload = json.loads(raw_json)
            if matrix_json is not None:
                payload["what_if_config"] = json.loads(matrix_json or "{}")
            Path(selected_job["path"]).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            st.success("Queued job updated.")
        except Exception as exc:
            st.error(f"Could not save edits: {exc}")
    if job_cols[1].button("Delete selected job", width="stretch", key=f"del_job_{full_job.get('job_id')}"):
        delete_job(selected_job.get("path") or selected_job.get("job_id"))
        st.success("Job deleted.")
        st.rerun()
    if job_cols[2].button("Refresh jobs", width="stretch", key=f"refresh_job_{full_job.get('job_id')}"):
        st.rerun()
    if job_cols[3].button("Run selected next", width="stretch", key=f"prioritize_job_{full_job.get('job_id')}", disabled=full_job.get("status") != "queued"):
        try:
            prioritize_queued_job(selected_job.get("path") or selected_job.get("job_id"))
            st.success("Selected queued job moved to the front. It will start when the worker is running and free.")
            st.rerun()
        except Exception as exc:
            st.error(f"Could not prioritize job: {exc}")

    recovery_cols = st.columns(2)
    if recovery_cols[0].button("Requeue selected failed/running job", width="stretch", key=f"requeue_job_{full_job.get('job_id')}", disabled=full_job.get("status") not in {"failed", "running"}):
        try:
            if full_job.get("status") == "running" and health.get("alive"):
                st.error("Worker heartbeat is alive. Do not requeue an actively running job. Stop the worker first or wait for it to finish.")
            else:
                requeue_job(selected_job.get("path") or selected_job.get("job_id"))
                st.success("Selected job requeued. Start the worker if it is not running.")
                st.rerun()
        except Exception as exc:
            st.error(f"Could not requeue job: {exc}")
    if recovery_cols[1].button("Show worker start command", width="stretch", key=f"show_worker_cmd_{full_job.get('job_id')}"):
        st.code(".\\start_backtest_worker.bat", language="powershell")
        st.caption("Run this in a separate PowerShell window from the exact same package folder as the Backtest app.")

    completed = [job for job in jobs if job.get("status") == "completed" and job.get("results")]
    if completed:
        labels = [f"{job.get('job_id')} | {job.get('created_at')} | {len(job.get('results') or [])} runs" for job in completed]
        selected_completed_label = st.selectbox("Open completed batch comparison", options=labels, index=0, key="completed_batch_select")
        selected_completed = completed[labels.index(selected_completed_label)]
        saved_runs = []
        for result in selected_completed.get("results") or []:
            try:
                loaded = load_saved_backtest(result["run_dir"])
                manifest = loaded.get("manifest") or {}
                manifest["run_dir"] = result["run_dir"]
                saved_runs.append(manifest)
            except Exception:
                continue
        if saved_runs:
            _render_run_comparison(saved_runs[:10], title=f"Batch job {selected_completed.get('job_id')} comparison")


def _run_with_phase_progress(mode: str, run_backtest_fn=None, run_matrix_fn=None):
    import time
    progress_box = st.empty()
    status_box = st.empty()
    started = time.perf_counter()
    def _update(pct: int, label: str):
        elapsed = time.perf_counter() - started
        progress_box.progress(max(0.0, min(pct / 100.0, 1.0)), text=f"{label} | elapsed {_fmt_seconds(elapsed)} | {pct}%")
        status_box.caption(f"Started {mode}. Elapsed: {_fmt_seconds(elapsed)}")
    _update(5, f"{mode}: preparing inputs")
    result = matrix = None
    if run_backtest_fn is not None:
        _update(15, f"{mode}: running backtest")
        result = run_backtest_fn()
        _update(55 if run_matrix_fn is not None else 100, f"{mode}: backtest finished")
    if run_matrix_fn is not None:
        _update(65, f"{mode}: running what-if matrix")
        matrix = run_matrix_fn()
        _update(100, f"{mode}: what-if matrix finished")
    return result, matrix


def render_backtest_tab(storage) -> None:
    st.subheader("Backtest / Replay")
    st.caption("Run deterministic backtests on local Binance history, queue background worker jobs, test bundle consensus logic, and compare saved runs.")
    st.info("Best practice: keep the collector alive, do heavy backtests in the separate Backtest Lab window, and use the worker queue for long batches.")

    library = storage.get_strategy_library()
    strategy_rows = library.sort_values(["strategy_name", "version_no"], ascending=[True, False]).reset_index(drop=True)
    strategy_labels = [f"{row['strategy_name']} | v{int(row['version_no'])} | {row['template_key']}" for _, row in strategy_rows.iterrows()]

    default_root = st.session_state.get("backtest_source_root") or str(OHLCV_STORE_ROOT)
    st.session_state.setdefault("backtest_source_root", default_root)
    st.session_state.setdefault("backtest_override_json", "{}")
    st.session_state.setdefault("backtest_config_json", json.dumps(DEFAULT_BACKTEST_CONFIG, indent=2))
    st.session_state.setdefault("backtest_matrix_config_json", json.dumps(DEFAULT_MATRIX_CONFIG, indent=2))
    st.session_state.setdefault("backtest_bundle_config_json", json.dumps(DEFAULT_BUNDLE_CONFIG, indent=2))
    st.session_state.setdefault("backtest_run_mode", "Single strategy")
    st.session_state.setdefault("last_backtest_matrix", None)
    st.session_state.setdefault("last_backtest_result", None)
    st.session_state.setdefault("batch_split_by_symbol", True)
    st.session_state.setdefault("execution_friction_preset", "zero_research")
    execution_presets = _load_execution_presets()

    bootstrap_root = st.text_input(
        "History folder",
        key="backtest_source_root",
        help="Point this to your partitioned parquet store (recommended: data/ohlcv_store) or a raw bootstrap folder if you are still importing.",
    )
    resolved_root = Path(bootstrap_root).expanduser().resolve()
    scan_cols = st.columns([1, 1, 2])
    if scan_cols[0].button("Scan files", width="stretch", help="Discover supported parquet/CSV history files under the selected folder."):
        try:
            found = discover_bootstrap_files(resolved_root)
            st.session_state["backtest_discovered"] = found
            if found:
                st.success(f"Found {len(found)} supported files.")
            else:
                st.warning(f"No supported CSV/parquet files found in: {resolved_root}")
        except Exception as exc:
            st.error(f"Scan failed: {exc}")
    if scan_cols[1].button("Convert CSV → Parquet cache", width="stretch", help="Optional helper if you are still on merged CSVs. You do not need this if you already built data/ohlcv_store."):
        try:
            converted = convert_bootstrap_to_parquet(resolved_root)
            st.session_state["backtest_converted"] = converted
            st.success(f"Converted {len(converted)} symbol/timeframe groups to parquet cache.")
        except Exception as exc:
            st.error(f"Conversion failed: {exc}")
    scan_cols[2].caption("If you already prepared the partitioned parquet store, you do not need the cache conversion step.")
    st.caption(f"Resolved History folder path: {resolved_root}")

    discovered = st.session_state.get("backtest_discovered") or []
    st.write(f"Discovered file count: {len(discovered)}")
    if discovered:
        with st.expander(f"Discovered files ({len(discovered)})", expanded=False):
            st.dataframe(pd.DataFrame(discovered), width="stretch", hide_index=True)
    converted = st.session_state.get("backtest_converted") or []
    if converted:
        with st.expander(f"Converted cache items ({len(converted)})", expanded=False):
            st.dataframe(pd.DataFrame(converted), width="stretch", hide_index=True)

    load_meta = st.session_state.pop("load_saved_run_meta", None)
    symbol_options = sorted({item["symbol"] for item in discovered}) if discovered else []
    default_symbols = load_meta["symbols"] if load_meta else (symbol_options[: min(6, len(symbol_options))] if symbol_options else ["BTCUSDT", "ETHUSDT"])
    default_strategy_labels = []
    if load_meta and load_meta.get("strategy_name"):
        default_strategy_labels = [label for label in strategy_labels if str(load_meta.get("strategy_name")) in label][:3]
    if not default_strategy_labels and strategy_labels:
        default_strategy_labels = [strategy_labels[0]]

    left, right = st.columns([1.45, 1])
    with left:
        selected_strategy_labels = st.multiselect(
            "Strategies for backtest worker",
            options=strategy_labels,
            default=default_strategy_labels,
            key="selected_strategy_labels",
            help="Select one or more strategies. Direct runs use the selected run mode below. Queue Selected Strategies always runs each selected strategy separately.",
        )
        run_mode = st.selectbox(
            "Direct run mode",
            options=["Single strategy", "Bundle consensus"],
            index=0 if st.session_state.get("backtest_run_mode") != "Bundle consensus" else 1,
            key="backtest_run_mode",
            help="Single strategy uses the first selected strategy for direct Run / What-if buttons. Bundle consensus combines all selected strategies into one bundle decision engine for direct Run / What-if / Queue current mode.",
        )
        if run_mode == "Single strategy" and len(selected_strategy_labels) > 1:
            st.info("Single strategy mode uses the first selected strategy for direct Run buttons. Use Queue Selected Strategies to run all selected strategies separately.")
        if run_mode == "Bundle consensus" and len(selected_strategy_labels) < 2:
            st.warning("Bundle mode works best with 2 or more selected strategies.")
        symbols = st.multiselect(
            "Symbols",
            options=symbol_options or ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            default=default_symbols,
            key="bt_symbols",
            help="Symbols are split into separate jobs in queue mode when the split checkbox is enabled.",
        )
        c1, c2, c3, c4 = st.columns(4)
        entry_tf = c1.selectbox("Entry TF", options=["1m", "5m", "15m"], index=["1m", "5m", "15m"].index(load_meta["entry_timeframe"]) if load_meta and load_meta.get("entry_timeframe") in ["1m", "5m", "15m"] else 1, help="Lower timeframe used for entry timing and trade simulation.")
        analysis_tf = c2.selectbox("Analysis TF", options=["5m", "15m", "1h", "4h"], index=["5m", "15m", "1h", "4h"].index(load_meta["analysis_timeframe"]) if load_meta and load_meta.get("analysis_timeframe") in ["5m", "15m", "1h", "4h"] else 2, help="Higher timeframe used for market context and regime scoring.")
        start_date = c3.text_input("Start date", value=(load_meta.get("start_date") if load_meta else "2024-01-01"), help="Inclusive start date for the backtest window. Format: YYYY-MM-DD")
        end_date = c4.text_input("End date", value=(load_meta.get("end_date") if load_meta else str(pd.Timestamp.utcnow().date())), help="Inclusive end date for the backtest window. Format: YYYY-MM-DD")
        override_json = st.text_area(
            "Strategy override JSON",
            key="backtest_override_json",
            height=180,
            help="Temporary strategy tweaks for this run only. Typical keys: score_threshold, expected_rr, rule_params, indicator_rules.",
        )
    with right:
        config_json = st.text_area(
            "Backtest config JSON",
            key="backtest_config_json",
            height=190,
            help="Test environment options. Important fields: max_hold_bars, cooldown_bars, fixed_stake_usd, fee_bps_per_side, slippage_bps_per_side, spread_bps, funding_bps_per_8h, allow_long, allow_short, one_trade_at_time, entry_mode, reverse_signal.",
        )
        preset_options = ["custom_json"] + list(execution_presets.keys())
        selected_execution_preset = st.selectbox(
            "Execution friction preset",
            options=preset_options,
            format_func=lambda key: "Custom / use JSON values" if key == "custom_json" else execution_presets.get(key, {}).get("label", key),
            index=preset_options.index(st.session_state.get("execution_friction_preset", "zero_research")) if st.session_state.get("execution_friction_preset", "zero_research") in preset_options else 0,
            key="execution_friction_preset",
            help="Applies fee, spread, slippage, and optional funding assumptions to the backtest math. Use Custom to rely on the JSON values only.",
        )
        if selected_execution_preset != "custom_json":
            st.caption(execution_presets.get(selected_execution_preset, {}).get("notes", ""))
        matrix_json = st.text_area(
            "What-if matrix config JSON",
            key="backtest_matrix_config_json",
            height=160,
            help="Scenario builder for stop multipliers, TP ladder sizes, score thresholds, confirm-bar scenarios, and reverse-signal scenarios.",
        )
        bundle_json = st.text_area(
            "Bundle config JSON",
            key="backtest_bundle_config_json",
            height=170,
            help="Used when Direct run mode = Bundle consensus. Define bundle_name, bundle_mode, n_required, bundle_threshold, component_min_score, and optional weights.",
        )
        split_by_symbol = st.checkbox(
            "Queue multi-symbol selections as separate per-symbol runs",
            key="batch_split_by_symbol",
            help="Recommended. Each symbol becomes its own worker job task, which makes results cleaner and easier to compare.",
        )
        st.markdown("""
**Useful JSON ideas**

Strategy override JSON changes the selected strategy or bundle components for this run only.

```json
{
  "score_threshold": 70,
  "expected_rr": "1:4",
  "rule_params": {
    "tp_count": 4,
    "stop_multiplier": 1.5
  }
}
```

Backtest config JSON changes the test environment.

```json
{
  "max_hold_bars": 192,
  "cooldown_bars": 1,
  "fixed_stake_usd": 100,
  "fee_bps_per_side": 2,
  "slippage_bps_per_side": 1,
  "spread_bps": 1,
  "funding_bps_per_8h": 0,
  "one_trade_at_time": true,
  "entry_mode": "next_open"
}
```
            """)

    override_payload = _safe_json(override_json, {})
    config_payload = _safe_json(config_json, DEFAULT_BACKTEST_CONFIG)
    if st.session_state.get("execution_friction_preset") != "custom_json":
        config_payload = _apply_execution_preset(config_payload, st.session_state.get("execution_friction_preset", "zero_research"), execution_presets)
    matrix_config = _safe_json(matrix_json, DEFAULT_MATRIX_CONFIG)
    bundle_config = _safe_json(bundle_json, DEFAULT_BUNDLE_CONFIG)

    selected_rows = [strategy_rows.iloc[strategy_labels.index(label)] for label in selected_strategy_labels if label in strategy_labels]
    selected_payloads = [merge_overrides(_version_payload_from_row(row), override_payload) for row in selected_rows]
    active_payload = None
    active_run_kind = "backtest"
    if selected_payloads:
        if run_mode == "Bundle consensus":
            active_payload = build_bundle_payload(selected_payloads, bundle_config)
            active_run_kind = "bundle_backtest"
        else:
            active_payload = selected_payloads[0]
            active_run_kind = "backtest"
    save_default_name, save_default_comment = _make_save_defaults(active_payload or {"strategy_name": "Backtest"}, symbols, start_date, end_date, entry_tf, analysis_tf, override_payload, config_payload, active_run_kind, matrix_config)

    if selected_payloads:
        with st.expander("Selected strategy packets", expanded=False):
            preview_rows = []
            for pld in selected_payloads:
                preview_rows.append({
                    "strategy_name": pld.get("strategy_name"),
                    "version_no": pld.get("version_no"),
                    "template_key": pld.get("template_key"),
                    "score_threshold": pld.get("score_threshold"),
                    "expected_rr": pld.get("expected_rr"),
                })
            st.dataframe(pd.DataFrame(preview_rows), width="stretch", hide_index=True)

    def _require_payload() -> bool:
        if not selected_payloads:
            st.error("Select at least one strategy first.")
            return False
        if not symbols:
            st.error("Select at least one symbol first.")
            return False
        return True

    run_cols = st.columns([1, 1, 1, 1, 1, 2])
    if run_cols[0].button("Run backtest now", type="primary", width="stretch", help="Run the current direct mode immediately in this window. Bundle mode runs one bundle result; single mode runs the first selected strategy."):
        if _require_payload():
            try:
                result, _ = _run_with_phase_progress(
                    "Backtest",
                    lambda: run_backtest(source_root=resolved_root, symbols=symbols, strategy_payload=active_payload, entry_timeframe=entry_tf, analysis_timeframe=analysis_tf, start_date=start_date, end_date=end_date, config=config_payload),
                )
                st.session_state["last_backtest_result"] = result
                st.success("Backtest finished.")
            except Exception as exc:
                st.error(f"Backtest failed: {exc}")
    if run_cols[1].button("Run what-if matrix", width="stretch", help="Run the what-if scenario matrix for the current direct mode. In bundle mode, the bundle itself is rerun across scenarios."):
        if _require_payload():
            try:
                _, matrix_df = _run_with_phase_progress(
                    "What-if matrix",
                    None,
                    lambda: run_backtest_matrix(source_root=resolved_root, symbols=symbols, strategy_payload=active_payload, entry_timeframe=entry_tf, analysis_timeframe=analysis_tf, start_date=start_date, end_date=end_date, config=config_payload, matrix_config=matrix_config),
                )
                st.session_state["last_backtest_matrix"] = matrix_df
                st.success("What-if rerun matrix finished.")
            except Exception as exc:
                st.error(f"What-if matrix failed: {exc}")
    if run_cols[2].button("Run backtest + what-if", width="stretch", help="Run the current direct mode and then immediately rerun the what-if matrix."):
        if _require_payload():
            try:
                result, matrix_df = _run_with_phase_progress(
                    "Combined backtest + what-if",
                    lambda: run_backtest(source_root=resolved_root, symbols=symbols, strategy_payload=active_payload, entry_timeframe=entry_tf, analysis_timeframe=analysis_tf, start_date=start_date, end_date=end_date, config=config_payload),
                    lambda: run_backtest_matrix(source_root=resolved_root, symbols=symbols, strategy_payload=active_payload, entry_timeframe=entry_tf, analysis_timeframe=analysis_tf, start_date=start_date, end_date=end_date, config=config_payload, matrix_config=matrix_config),
                )
                st.session_state["last_backtest_result"] = result
                st.session_state["last_backtest_matrix"] = matrix_df
                st.success("Backtest and what-if matrix finished.")
            except Exception as exc:
                st.error(f"Combined run failed: {exc}")
    if run_cols[3].button("Queue selected strategies", width="stretch", help="Queue each selected strategy as its own worker task. This is the cleanest way to compare strategies one by one."):
        if _require_payload():
            try:
                tasks = []
                target_symbols = symbols if split_by_symbol else [symbols]
                for pld in selected_payloads:
                    for sym in target_symbols:
                        sym_list = sym if isinstance(sym, list) else [sym]
                        tasks.append({"name": f"{pld.get('strategy_name')} | {'/'.join(sym_list)}", "strategy_payload": pld, "config_overrides": {}, "symbols": sym_list})
                job_path = create_task_job(source_root=str(resolved_root), symbols=symbols, entry_timeframe=entry_tf, analysis_timeframe=analysis_tf, start_date=start_date, end_date=end_date, base_config=config_payload, tasks=tasks, comment=save_default_comment, job_type="batch_backtest_split", extra={"split_by_symbol": split_by_symbol})
                st.success(f"Queued worker job: {Path(job_path).stem}")
            except Exception as exc:
                st.error(f"Queueing failed: {exc}")
    if run_cols[4].button("Queue current mode what-if", width="stretch", help="Queue what-if scenarios for the current direct mode. Single mode creates separate strategy matrices; bundle mode creates bundle matrices."):
        if _require_payload():
            try:
                tasks = []
                target_symbols = symbols if split_by_symbol else [symbols]
                if run_mode == "Bundle consensus":
                    for sym in target_symbols:
                        sym_list = sym if isinstance(sym, list) else [sym]
                        for task in build_what_if_tasks(active_payload, config_payload, matrix_config=matrix_config):
                            cloned = dict(task)
                            cloned["symbols"] = sym_list
                            cloned["name"] = f"{task.get('name')} | {'/'.join(sym_list)}"
                            tasks.append(cloned)
                    job_type = "bundle_what_if_matrix"
                else:
                    for pld in selected_payloads:
                        base_tasks = build_what_if_tasks(pld, config_payload, matrix_config=matrix_config)
                        for sym in target_symbols:
                            sym_list = sym if isinstance(sym, list) else [sym]
                            for task in base_tasks:
                                cloned = dict(task)
                                cloned["symbols"] = sym_list
                                cloned["name"] = f"{task.get('name')} | {'/'.join(sym_list)}"
                                tasks.append(cloned)
                    job_type = "what_if_matrix"
                job_path = create_task_job(source_root=str(resolved_root), symbols=symbols, entry_timeframe=entry_tf, analysis_timeframe=analysis_tf, start_date=start_date, end_date=end_date, base_config=config_payload, tasks=tasks, comment=save_default_comment, job_type=job_type, extra={"what_if_config": matrix_config, "base_run": st.session_state.get("last_saved_run_dir") or "", "split_by_symbol": split_by_symbol, "run_mode": run_mode, "bundle_config": bundle_config})
                st.success(f"Queued what-if worker job: {Path(job_path).stem}")
            except Exception as exc:
                st.error(f"Queueing what-if matrix failed: {exc}")
    if run_cols[5].button("Queue current mode backtest + what-if", width="stretch", help="Queue the current direct mode and its what-if scenarios together. Bundle mode creates a bundle baseline plus bundle scenarios."):
        if _require_payload():
            try:
                tasks = []
                target_symbols = symbols if split_by_symbol else [symbols]
                if run_mode == "Bundle consensus":
                    for sym in target_symbols:
                        sym_list = sym if isinstance(sym, list) else [sym]
                        tasks.append({"name": f"BASE | {active_payload.get('bundle_name') or active_payload.get('strategy_name')} | {'/'.join(sym_list)}", "strategy_payload": active_payload, "config_overrides": {}, "symbols": sym_list, "scenario_name": "baseline"})
                        for task in build_what_if_tasks(active_payload, config_payload, matrix_config=matrix_config):
                            cloned = dict(task)
                            cloned["symbols"] = sym_list
                            cloned["name"] = f"{task.get('name')} | {'/'.join(sym_list)}"
                            tasks.append(cloned)
                else:
                    for pld in selected_payloads:
                        for sym in target_symbols:
                            sym_list = sym if isinstance(sym, list) else [sym]
                            tasks.append({"name": f"BASE | {pld.get('strategy_name')} | {'/'.join(sym_list)}", "strategy_payload": pld, "config_overrides": {}, "symbols": sym_list, "scenario_name": "baseline"})
                            for task in build_what_if_tasks(pld, config_payload, matrix_config=matrix_config):
                                cloned = dict(task)
                                cloned["symbols"] = sym_list
                                cloned["name"] = f"{task.get('name')} | {'/'.join(sym_list)}"
                                tasks.append(cloned)
                job_path = create_task_job(source_root=str(resolved_root), symbols=symbols, entry_timeframe=entry_tf, analysis_timeframe=analysis_tf, start_date=start_date, end_date=end_date, base_config=config_payload, tasks=tasks, comment=save_default_comment, job_type="combined_backtest_whatif", extra={"what_if_config": matrix_config, "base_run": st.session_state.get("last_saved_run_dir") or "", "split_by_symbol": split_by_symbol, "run_mode": run_mode, "bundle_config": bundle_config})
                st.success(f"Queued combined worker job: {Path(job_path).stem}")
            except Exception as exc:
                st.error(f"Queueing combined run failed: {exc}")
    clear_cols = st.columns([1, 3])
    clear_cols[0].button("Clear current result", width="stretch", help="Clear the currently displayed direct-run result and matrix.", on_click=lambda: (st.session_state.pop("last_backtest_result", None), st.session_state.pop("last_backtest_matrix", None)))
    clear_cols[1].caption("Queue runs for the background worker or run directly in this window. Direct progress is phase-based; queue progress is the most accurate view.")

    result = st.session_state.get("last_backtest_result")
    matrix_df = st.session_state.get("last_backtest_matrix")
    if result is not None:
        _render_result(result)
        dl1, dl2, dl3 = st.columns(3)
        dl1.download_button("Download bundle ZIP", data=build_export_bundle_bytes(result), file_name="backtest_bundle.zip", mime="application/zip", width="stretch")
        dl2.download_button("Download trades CSV", data=result.trades.to_csv(index=False), file_name="backtest_trades.csv", mime="text/csv", width="stretch")
        dl3.download_button("Download summary JSON", data=json.dumps(result.summary, indent=2), file_name="backtest_summary.json", mime="application/json", width="stretch")

        save_name = st.text_input("Save run as", value=save_default_name, help="Editable run name used in the saved-runs list.")
        save_comment = st.text_area("Comment for saved run", value=save_default_comment, height=220, help="This is prefilled with the current pairs, strategy payload, config JSON, dates, TFs, and matrix config so you can reproduce the run later.")
        fav = st.checkbox("Mark saved run as favorite", value=False, help="Favorites float to the top of the saved comparison browser.")
        if st.button("Save backtest result", width="stretch", help="Save the current result package so you can compare it later, rerun it, queue what-if, or export it for AI review."):
            try:
                result.config["source_root"] = str(resolved_root)
                result.config["symbols"] = symbols
                result.config["entry_timeframe"] = entry_tf
                result.config["analysis_timeframe"] = analysis_tf
                result.config["start_date"] = start_date
                result.config["end_date"] = end_date
                result.config["run_kind"] = active_run_kind
                result.config["what_if_config"] = matrix_config
                result.config["bundle_config"] = bundle_config
                target = save_backtest_result(result, save_name, save_comment)
                if fav:
                    update_saved_backtest_manifest(target, {"favorite": True})
                st.session_state["last_saved_run_dir"] = str(target)
                st.success(f"Saved to {target}")
            except Exception as exc:
                st.error(f"Saving failed: {exc}")

    if matrix_df is not None:
        st.divider()
        _render_what_if_matrix(matrix_df)


    st.divider()
    _render_job_queue_section()
    st.divider()
    _render_saved_comparison()
