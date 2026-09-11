from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from .backtest_core import list_saved_backtests, load_saved_backtest
from .lifecycle_gate_v28 import DEFAULT_GATE_PENALTIES, run_lifecycle_gate_lab


def _run_label(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "Saved backtest")
    created = str(item.get("created_at") or "")
    summary = item.get("summary") or {}
    trades = int(summary.get("total_trades") or 0)
    return f"{name} | {created[:19]} | {trades} trades"


def _format_recommendation(rec: dict[str, Any]) -> tuple[str, str]:
    status = str(rec.get("status") or "insufficient_data")
    title_map = {
        "promising_for_exact_replay": "Promising — validate with exact signal-path replay",
        "promising_but_unproven": "Promising, but not proven",
        "no_gate_evidence": "No evidence for a hard lifecycle gate",
        "insufficient_data": "Insufficient data",
    }
    return title_map.get(status, status.replace("_", " ").title()), str(rec.get("reason") or "")


def render_lifecycle_gate_lab() -> None:
    st.title("Lifecycle Gate Backtest Lab")
    st.caption(
        "V28.8 evaluates lifecycle gating as a counterfactual on already-saved backtest trades. "
        "It compares the original trades with fit-only, conflict blocking and soft score-penalty scenarios before we modify the core execution path."
    )
    st.warning(
        "Important limitation: this is trade-level counterfactual filtering, not an exact signal-path replay. "
        "Removing a trade does not create replacement trades that might have appeared later when one-trade-at-a-time is enabled. "
        "Use V28.8 to decide whether exact replay is worth building — not to enable live gating."
    )

    saved = list_saved_backtests()
    if not saved:
        st.info(
            "No saved backtests are available in this runtime yet. Run and save at least one backtest first. "
            "For meaningful results, use the historical OHLCV store from V28.6/V28.7 and a sufficiently long test window."
        )
        return

    labels = [_run_label(item) for item in saved]
    selected_label = st.selectbox("Saved backtest", labels, index=0)
    selected = saved[labels.index(selected_label)]
    loaded = load_saved_backtest(selected.get("run_dir"))
    trades = loaded.get("trades")
    manifest = loaded.get("manifest") or selected
    if not isinstance(trades, pd.DataFrame) or trades.empty:
        st.warning("The selected saved run has no trade rows to evaluate.")
        return

    config = manifest.get("config") or {}
    strategy_payload = manifest.get("strategy_payload") or {}
    default_stake = float(config.get("fixed_stake_usd") or manifest.get("summary", {}).get("stake_per_trade_usd") or 100.0)
    default_threshold = float(
        strategy_payload.get("score_threshold")
        or (strategy_payload.get("rule_params") or {}).get("score_threshold")
        or 70.0
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        fixed_stake = st.number_input("Stake per trade (USD)", min_value=1.0, value=default_stake, step=10.0)
    with c2:
        score_threshold = st.number_input("Score threshold for soft-penalty scenario", min_value=0.0, max_value=100.0, value=default_threshold, step=1.0)
    with c3:
        confidence_floor = st.slider("Lifecycle confidence floor", min_value=0, max_value=100, value=55, step=5)

    with st.expander("Soft-penalty settings", expanded=False):
        p1, p2, p3, p4, p5 = st.columns(5)
        penalties = {
            "fit": p1.number_input("Fit penalty", min_value=0.0, value=float(DEFAULT_GATE_PENALTIES["fit"]), step=1.0),
            "caution": p2.number_input("Caution penalty", min_value=0.0, value=float(DEFAULT_GATE_PENALTIES["caution"]), step=1.0),
            "unknown": p3.number_input("Unknown penalty", min_value=0.0, value=float(DEFAULT_GATE_PENALTIES["unknown"]), step=1.0),
            "blocked": p4.number_input("Blocked penalty", min_value=0.0, value=float(DEFAULT_GATE_PENALTIES["blocked"]), step=1.0),
            "direction_conflict": p5.number_input("Direction-conflict penalty", min_value=0.0, value=float(DEFAULT_GATE_PENALTIES["direction_conflict"]), step=1.0),
        }

    analysis_tf = str(config.get("analysis_timeframe") or "")
    report = run_lifecycle_gate_lab(
        trades,
        strategy_payload=strategy_payload,
        fixed_stake_usd=float(fixed_stake),
        score_threshold=float(score_threshold),
        penalties=penalties,
        confidence_floor=float(confidence_floor),
        analysis_tf=analysis_tf,
    )

    title, reason = _format_recommendation(report.recommendation)
    if report.recommendation.get("status") == "promising_for_exact_replay":
        st.success(f"{title}\n\n{reason}")
    elif report.recommendation.get("status") == "promising_but_unproven":
        st.info(f"{title}\n\n{reason}")
    else:
        st.warning(f"{title}\n\n{reason}")

    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Original trades", len(report.enriched_trades))
    r2.metric("Best scenario", str(report.recommendation.get("recommended_scenario") or "—"))
    r3.metric("Evidence score", f"{float(report.recommendation.get('evidence_score') or 0):.1f}")
    probability = report.recommendation.get("separation_prob_positive")
    r4.metric("Kept > removed probability", "—" if probability is None else f"{float(probability) * 100:.1f}%")

    tab_compare, tab_fit, tab_state, tab_trades = st.tabs([
        "Gate comparison",
        "By fit status",
        "By lifecycle state",
        "Enriched trades",
    ])

    with tab_compare:
        st.subheader("Baseline vs lifecycle gate scenarios")
        st.caption(
            "Prefer expectancy, profit factor, drawdown and kept-vs-removed separation over total PnL alone. "
            "A tighter gate naturally trades less, so total PnL can fall even when trade quality improves."
        )
        st.dataframe(report.comparison, width="stretch", hide_index=True)
        if not report.comparison.empty:
            chart_df = report.comparison.set_index("scenario")[["avg_trade_pct", "expectancy_r"]]
            st.bar_chart(chart_df)

    with tab_fit:
        st.subheader("Performance by lifecycle fit status")
        st.dataframe(report.performance_by_fit_status, width="stretch", hide_index=True)

    with tab_state:
        st.subheader("Performance by lifecycle state")
        st.dataframe(report.performance_by_lifecycle_state, width="stretch", hide_index=True)

    with tab_trades:
        st.subheader("Trade-level lifecycle reconstruction")
        cols = [
            "symbol",
            "entry_time",
            "side",
            "score",
            "pnl_pct",
            "strategy_family",
            "lifecycle_state",
            "lifecycle_direction",
            "lifecycle_confidence",
            "fit_status",
            "allowed_by_lifecycle",
            "fit_reason",
            "suggested_exit_family",
        ]
        display = report.enriched_trades[[c for c in cols if c in report.enriched_trades.columns]].copy()
        st.dataframe(display, width="stretch", hide_index=True)
        st.download_button(
            "Download enriched lifecycle trades CSV",
            data=report.enriched_trades.to_csv(index=False).encode("utf-8"),
            file_name="lifecycle_gate_enriched_trades.csv",
            mime="text/csv",
        )

    st.divider()
    st.caption(
        "Promotion rule: V28.8 never changes paper/live execution. A promising result should be repeated by symbol, side, timeframe and out-of-sample period. "
        "Only then should we build exact signal-path lifecycle gating."
    )
