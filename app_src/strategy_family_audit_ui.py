from __future__ import annotations

import streamlit as st

from .settings import LAB_DB_PATH
from .storage import Storage
from .strategy_family_registry_v2811 import audit_strategies, family_readiness


def render_strategy_family_audit() -> None:
    st.title("Strategy Family Audit")
    st.caption("V28.12 · explicit family registry, historical-data readiness, and benchmark-only controls")
    st.info("Benchmark-only strategies can make a research family historically comparable without adding that strategy to live/paper slots. The OHLCV Compression Breakout Benchmark is such a control.")
    audit = audit_strategies(Storage(LAB_DB_PATH).get_latest_strategy_versions())
    if audit.empty:
        st.info("No saved strategies or registry benchmarks found.")
        return
    readiness = family_readiness(audit)
    c1, c2, c3 = st.columns(3)
    c1.metric("Audited entries", len(audit))
    c2.metric("Research ready", int(audit["research_ready"].sum()))
    c3.metric("Blocked by missing history", int((audit["readiness"] == "blocked_missing_history").sum()))
    st.subheader("Core family readiness")
    st.dataframe(readiness, width="stretch", hide_index=True)
    st.subheader("Registry audit")
    st.dataframe(audit, width="stretch", hide_index=True)
