from __future__ import annotations

import pandas as pd
import streamlit as st

from .backtest_core import load_saved_backtest
from .evidence_review_v2813 import (
    build_job_scorecard,
    completed_research_jobs,
    load_evidence_policy,
    save_scorecard_snapshot,
    scorecard_family_coverage,
)


def _job_label(job: dict) -> str:
    created = str(job.get("created_at") or "")[:19].replace("T", " ")
    return f"{created} · {job.get('run_kind')} · {job.get('job_id')}"


def render_evidence_review() -> None:
    st.title("Evidence Review")
    st.caption("V28.13 · conservative research triage after completed core-family backtests")

    jobs = completed_research_jobs()
    if not jobs:
        st.info(
            "No completed core research batch exists yet. This page will populate after the 12-month history is loaded, "
            "a core research batch is queued, and the backtest worker finishes it."
        )
        st.stop()

    labels = {_job_label(job): job for job in jobs}
    selected_label = st.selectbox("Completed research batch", list(labels.keys()), index=0)
    job = labels[selected_label]
    policy = load_evidence_policy()
    scorecard = build_job_scorecard(job, policy=policy)

    if scorecard.empty:
        st.warning("The selected job is completed but contains no saved result rows.")
        st.stop()

    coverage = scorecard_family_coverage(scorecard)
    candidate_count = int((scorecard.get("verdict", pd.Series(dtype=str)) == "cross_validation_candidate").sum())
    promising_count = int((scorecard.get("verdict", pd.Series(dtype=str)) == "promising").sum())
    rejected_count = int((scorecard.get("verdict", pd.Series(dtype=str)) == "reject").sum())
    insufficient_count = int((scorecard.get("verdict", pd.Series(dtype=str)) == "insufficient_evidence").sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Families", f"{coverage['families_present']}/{coverage['families_expected']}")
    c2.metric("Cross-val candidates", candidate_count)
    c3.metric("Promising", promising_count)
    c4.metric("Reject / insufficient", rejected_count + insufficient_count)

    if not coverage["complete"]:
        st.warning("Research protocol is incomplete. Missing: " + ", ".join(coverage["missing_families"]))

    display_cols = [
        "research_family",
        "strategy_name",
        "research_source",
        "benchmark_only",
        "verdict",
        "total_trades",
        "win_rate",
        "expectancy_r",
        "profit_factor",
        "net_pnl_usd",
        "max_drawdown_usd",
        "drawdown_to_net_profit",
        "execution_cost_usd",
        "friction_drag_share",
        "active_symbols",
        "positive_symbol_share",
        "active_months",
        "positive_month_share",
        "long_trades",
        "short_trades",
        "checks_passed",
        "checks_total",
        "warning_count",
    ]
    display_cols = [c for c in display_cols if c in scorecard.columns]
    st.subheader("Family scorecard")
    st.dataframe(scorecard[display_cols], width="stretch", hide_index=True)
    st.caption(
        "Verdicts are triage gates, not trading recommendations. Win rate is intentionally not a promotion gate; "
        "the review emphasizes net expectancy after friction, profit factor, drawdown, pair/month stability and sample size."
    )

    st.download_button(
        "Download scorecard CSV",
        data=scorecard.to_csv(index=False).encode("utf-8"),
        file_name=f"evidence_scorecard_{job.get('job_id')}.csv",
        mime="text/csv",
    )
    if st.button("Save evidence snapshot"):
        saved = save_scorecard_snapshot(job, scorecard, policy=policy)
        st.success(f"Saved: {saved['csv_path']} and {saved['json_path']}")

    st.subheader("Family detail")
    family_options = scorecard["research_family"].astype(str).tolist()
    selected_family = st.selectbox("Inspect family", family_options)
    row = scorecard[scorecard["research_family"].astype(str) == selected_family].iloc[0]

    d1, d2, d3, d4 = st.columns(4)
    d1.metric("Verdict", str(row.get("verdict") or "—"))
    d2.metric("Trades", int(row.get("total_trades") or 0))
    d3.metric("Expectancy R", f"{float(row.get('expectancy_r') or 0):.3f}")
    d4.metric("Profit factor", f"{float(row.get('profit_factor') or 0):.2f}")

    if bool(row.get("benchmark_only", False)):
        st.warning("This is benchmark-only concept evidence. It is blocked from direct production promotion.")
    warnings = str(row.get("warnings_text") or "")
    if warnings:
        st.write("Warnings:", warnings)
    st.write("Next step:", str(row.get("next_step") or "—"))

    run_dir = str(row.get("run_dir") or "")
    if run_dir:
        try:
            loaded = load_saved_backtest(run_dir)
            t1, t2, t3 = st.tabs(["By pair", "By month", "By side"])
            with t1:
                frame = loaded.get("performance_by_symbol", pd.DataFrame())
                st.dataframe(frame, width="stretch", hide_index=True) if not frame.empty else st.caption("No pair breakdown.")
            with t2:
                frame = loaded.get("performance_by_month", pd.DataFrame())
                st.dataframe(frame, width="stretch", hide_index=True) if not frame.empty else st.caption("No month breakdown.")
            with t3:
                frame = loaded.get("performance_by_side", pd.DataFrame())
                st.dataframe(frame, width="stretch", hide_index=True) if not frame.empty else st.caption("No side breakdown.")
        except Exception as exc:
            st.warning(f"Could not load saved run detail: {exc}")

    with st.expander("Evidence policy"):
        st.json(policy)
