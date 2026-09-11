from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from .runtime_cycle_v2810 import DEFAULT_RUNTIME_CYCLE_CONFIG, RUNTIME_REPORT_DIR, build_runtime_cycle_plan, load_runtime_cycle_config


def render_runtime_cycle() -> None:
    st.title("Runtime Cycle")
    st.caption("Operational plan and latest cycle status for history refresh, analysis refresh, and Market State snapshots.")
    st.info("Safety: V28.10 keeps auto-paper OFF and does not enable live execution.")

    cfg = load_runtime_cycle_config(DEFAULT_RUNTIME_CYCLE_CONFIG)
    plan = build_runtime_cycle_plan(cfg)
    targets = pd.DataFrame(plan.get("history_targets") or [])
    c1, c2, c3 = st.columns(3)
    c1.metric("History targets", len(targets))
    c2.metric("Lookback", plan.get("history_lookback", "—"))
    c3.metric("Analysis", f"{len(plan.get('analysis_symbols') or [])} @ {plan.get('analysis_timeframe')}")
    if not targets.empty:
        st.dataframe(targets, use_container_width=True, hide_index=True)
    with st.expander("Full cycle plan"):
        st.json(plan)

    st.subheader("Latest saved cycle")
    latest_path = Path(RUNTIME_REPORT_DIR) / "latest.json"
    if not latest_path.exists():
        st.caption("No runtime cycle report has been saved on this machine yet.")
        return
    try:
        report = json.loads(latest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        st.warning(f"Could not read latest runtime report: {exc}")
        return
    a, b, c = st.columns(3)
    a.metric("Status", report.get("status", "—"))
    b.metric("Started", report.get("started_at", "—"))
    c.metric("Errors", len(report.get("errors") or []))
    coverage = (((report.get("steps") or {}).get("coverage") or {}).get("rows") or [])
    if coverage:
        st.dataframe(pd.DataFrame(coverage), use_container_width=True, hide_index=True)
    st.json(report)
