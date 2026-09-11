from __future__ import annotations

import pandas as pd
import streamlit as st

from .research_command_center_v29 import (
    CORE_RESEARCH_FAMILIES,
    CORE_RESEARCH_SYMBOLS,
    DEFAULT_RESEARCH_CONFIG,
    build_market_state_dashboard,
    build_research_plan,
    queue_core_research_batch,
    recent_research_jobs,
    research_history_readiness,
    select_core_strategies,
)
from .settings import LAB_DB_PATH
from .storage import Storage

ALL_SYMBOLS = CORE_RESEARCH_SYMBOLS + ["LTCUSDT", "BNBUSDT", "UNIUSDT", "AAVEUSDT", "XRPUSDT", "TRXUSDT"]


def render_research_command_center() -> None:
    st.title("Research Command Center")
    st.caption("V28.9 · Data to Market State to Strategy Evidence")
    storage = Storage(LAB_DB_PATH)
    symbols = st.multiselect("Dashboard pairs", ALL_SYMBOLS, default=ALL_SYMBOLS) or CORE_RESEARCH_SYMBOLS

    tab_state, tab_runner = st.tabs(["Market State", "Research Runner"])
    with tab_state:
        frame = build_market_state_dashboard(storage=storage, symbols=symbols)
        if frame.empty:
            st.info("No market-state rows yet.")
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric("Pairs", len(frame))
            c2.metric("History ready", int((frame["history_status"] == "ready").sum()))
            c3.metric("Analyzed", int((frame["lifecycle_state"] != "not_analyzed").sum()))
            st.dataframe(frame, width="stretch", hide_index=True)

    with tab_runner:
        st.subheader("Core Research Runner")
        selected_symbols = st.multiselect("Research pairs", symbols, default=[s for s in CORE_RESEARCH_SYMBOLS if s in symbols])
        selected_families = st.multiselect("Strategy families", CORE_RESEARCH_FAMILIES, default=CORE_RESEARCH_FAMILIES)
        c1, c2 = st.columns(2)
        with c1:
            lookback_days = st.number_input("Lookback days", 90, 1825, 365, 30)
        with c2:
            max_per_family = st.number_input("Strategies per family", 1, 3, 1, 1)

        readiness = research_history_readiness(selected_symbols or CORE_RESEARCH_SYMBOLS)
        if not readiness.empty:
            ready = int((readiness["status"] == "ready").sum())
            st.caption(f"History readiness: {ready}/{len(readiness)} datasets ready")
            with st.expander("History details"):
                st.dataframe(readiness, width="stretch", hide_index=True)

        latest = storage.get_latest_strategy_versions()
        matched = select_core_strategies(latest, selected_families, max_per_family=int(max_per_family))
        if matched:
            st.dataframe(pd.DataFrame([{
                "family": x.get("research_family"),
                "strategy": x.get("strategy_name"),
                "version": x.get("version_no"),
                "threshold": x.get("score_threshold"),
                "expected_rr": x.get("expected_rr"),
            } for x in matched]), width="stretch", hide_index=True)
        else:
            st.warning("No saved strategies map to the selected core families yet.")

        plan = build_research_plan(matched, selected_symbols or CORE_RESEARCH_SYMBOLS)
        with st.expander("Experiment matrix"):
            st.dataframe(plan, width="stretch", hide_index=True)

        if st.button("Queue core research batch", type="primary", disabled=not matched or not selected_symbols):
            result = queue_core_research_batch(
                storage=storage,
                symbols=selected_symbols,
                families=selected_families,
                max_per_family=int(max_per_family),
                lookback_days=int(lookback_days),
                config_overrides=dict(DEFAULT_RESEARCH_CONFIG),
                comment="Queued from V28.9 Research Command Center.",
            )
            if result.get("queued"):
                st.success(f"Queued: {result.get('job_path')}")
            else:
                st.error(result.get("reason") or "Could not queue batch")

        jobs = recent_research_jobs(12)
        st.write("Recent V28.9 jobs")
        if jobs.empty:
            st.caption("No V28.9 jobs yet.")
        else:
            st.dataframe(jobs, width="stretch", hide_index=True)
