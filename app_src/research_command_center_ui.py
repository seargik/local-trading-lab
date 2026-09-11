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
from .strategy_family_registry_v2811 import audit_strategies, family_readiness, resolve_entry

ALL_SYMBOLS = CORE_RESEARCH_SYMBOLS + ["LTCUSDT", "BNBUSDT", "UNIUSDT", "AAVEUSDT", "XRPUSDT", "TRXUSDT"]


def render_research_command_center() -> None:
    st.title("Research Command Center")
    st.caption("V28.11 · Data to Market State to Strategy Evidence")
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

        latest = storage.get_latest_strategy_versions()
        registry_audit = audit_strategies(latest)
        registry_ready = family_readiness(registry_audit)
        selected_registry = registry_ready[registry_ready["strategy_family"].isin(selected_families)]
        st.caption("Explicit strategy-family readiness")
        st.dataframe(selected_registry, width="stretch", hide_index=True)

        matched = select_core_strategies(latest, selected_families, max_per_family=int(max_per_family))
        display_rows = []
        strict_match_ok = True
        for item in matched:
            reg = resolve_entry(item.get("strategy_name"))
            same_family = reg.get("strategy_family") == item.get("research_family")
            ready_strategy = bool(reg.get("research_included") and reg.get("historical_ready") and same_family)
            strict_match_ok = strict_match_ok and ready_strategy
            display_rows.append({
                "family": item.get("research_family"),
                "strategy": item.get("strategy_name"),
                "version": item.get("version_no"),
                "registry_family": reg.get("strategy_family"),
                "registry_ready": ready_strategy,
                "missing_data": ", ".join(reg.get("missing_historical_data") or []),
            })
        if display_rows:
            st.dataframe(pd.DataFrame(display_rows), width="stretch", hide_index=True)
        else:
            st.warning("No saved strategies map to the selected core families yet.")

        selected_families_ready = bool(len(selected_registry) == len(selected_families) and (selected_registry["status"] == "ready").all())
        can_queue = bool(matched and selected_symbols and selected_families_ready and strict_match_ok)
        if not can_queue:
            st.warning("Batch queue is blocked until every selected family and selected strategy is explicitly classified and historically ready.")

        plan = build_research_plan(matched, selected_symbols or CORE_RESEARCH_SYMBOLS)
        with st.expander("Experiment matrix"):
            st.dataframe(plan, width="stretch", hide_index=True)

        if st.button("Queue core research batch", type="primary", disabled=not can_queue):
            result = queue_core_research_batch(
                storage=storage,
                symbols=selected_symbols,
                families=selected_families,
                max_per_family=int(max_per_family),
                lookback_days=int(lookback_days),
                config_overrides=dict(DEFAULT_RESEARCH_CONFIG),
                comment="Queued after V28.11 registry readiness checks.",
            )
            if result.get("queued"):
                st.success(f"Queued: {result.get('job_path')}")
            else:
                st.error(result.get("reason") or "Could not queue batch")

        jobs = recent_research_jobs(12)
        st.write("Recent research jobs")
        if jobs.empty:
            st.caption("No research jobs yet.")
        else:
            st.dataframe(jobs, width="stretch", hide_index=True)
