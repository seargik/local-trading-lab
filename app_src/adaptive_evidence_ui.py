from __future__ import annotations

import pandas as pd
import streamlit as st

from .adaptive_evidence_v2820 import (
    analyze_research_job,
    completed_adaptive_source_jobs,
    load_adaptive_evidence_policy,
)


def _job_label(job: dict) -> str:
    created = str(job.get("created_at") or "")[:19]
    symbols = ",".join(job.get("symbols") or [])
    return f"{created} | {symbols} | {job.get('job_id', '')}"


def _fmt(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    for col in out.columns:
        if col.endswith("_time"):
            out[col] = out[col].astype(str)
    return out


def render_adaptive_evidence_lab() -> None:
    st.header("Adaptive Evidence & Economic Viability Lab")
    st.caption(
        "V28.20: test whether Market State + Adaptive Router improves already-simulated strategy evidence versus static family baselines. "
        "Research only: this is counterfactual filtering/weighting of historical trades, not a capital-aware portfolio backtest and not paper/live permission."
    )

    policy = load_adaptive_evidence_policy()
    st.info(
        "The lab is deliberately hard to pass. It requires V28.18 timing integrity, V28.19 source-data continuity, enough trades, pair/month stability, "
        "profit factor, drawdown control, friction headroom, and measurable uplift versus the static pool."
    )

    jobs = completed_adaptive_source_jobs()
    if not jobs:
        st.warning("No completed core research batch is available yet. Populate history and rerun the controlled research batch first.")
        return

    options = { _job_label(job): job for job in jobs }
    selected_label = st.selectbox("Completed research job", options=list(options.keys()))
    job = options[selected_label]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Symbols", len(job.get("symbols") or []))
    c2.metric("Strategies", len(job.get("results") or []))
    c3.metric("Entry TF", str(job.get("entry_timeframe") or ""))
    c4.metric("Analysis TF", str(job.get("analysis_timeframe") or ""))

    with st.expander("V28.20 policy"):
        st.json(policy)

    if not st.button("Run adaptive economic evidence", type="primary", width="stretch"):
        st.caption("This recomputes the historical market-state replay for the job window and causally joins the latest available state to each strategy signal.")
        return

    try:
        with st.spinner("Replaying market state and joining router decisions to saved trades..."):
            result = analyze_research_job(job, policy=policy)
    except Exception as exc:
        st.error(f"Adaptive evidence run failed: {exc}")
        return

    verdict = result.verdict
    name = str(verdict.get("verdict") or "unknown")
    if name == "adaptive_edge_candidate":
        st.success("Adaptive edge candidate — strong historical evidence, but still requires walk-forward/fresh holdout/paper validation.")
    elif name == "reject":
        st.error("Reject — the adaptive router does not show positive economic evidence in this sample.")
    elif name == "promising_research_only":
        st.warning("Promising research only — positive evidence exists, but one or more robustness/promotion gates fail.")
    else:
        st.warning(f"{name.replace('_', ' ').title()} — evidence is not strong enough for promotion.")

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Adaptive trades", int(verdict.get("adaptive_trades") or 0))
    m2.metric("Net PnL*", f"${float(verdict.get('adaptive_pnl_usd') or 0):,.2f}")
    m3.metric("Profit factor", f"{float(verdict.get('adaptive_profit_factor') or 0):.2f}")
    m4.metric("Expectancy", f"{float(verdict.get('adaptive_expectancy_bps') or 0):.1f} bps/turn")
    m5.metric("Friction headroom", f"{float(verdict.get('break_even_extra_friction_bps') or 0):.1f} bps")
    st.caption("*PnL uses each saved backtest's fixed stake and router risk multiplier. It is not a portfolio return or forecast.")

    st.subheader("Quality gates")
    checks = pd.DataFrame([
        {"check": key, "pass": bool(value)} for key, value in (verdict.get("checks") or {}).items()
    ])
    st.dataframe(checks, width="stretch", hide_index=True)

    st.subheader("Static vs adaptive by family")
    st.dataframe(_fmt(result.family_comparison), width="stretch", hide_index=True)

    st.subheader("Pooled comparison")
    st.dataframe(_fmt(result.pooled_comparison), width="stretch", hide_index=True)
    p1, p2 = st.columns(2)
    p1.metric("PF uplift vs static", f"{float(verdict.get('profit_factor_uplift_vs_static_pool') or 0):+.2f}")
    p2.metric("Expectancy uplift", f"{float(verdict.get('expectancy_uplift_bps_vs_static_pool') or 0):+.1f} bps/turn")

    st.subheader("What WAIT/rejection bought or cost")
    st.caption("Positive WAIT value means excluded trades lost money in aggregate; negative WAIT value means the router skipped profitable trades.")
    st.dataframe(_fmt(result.wait_analysis), width="stretch", hide_index=True)

    st.subheader("Friction stress")
    st.dataframe(_fmt(result.friction_stress), width="stretch", hide_index=True)

    st.subheader("Stability")
    s1, s2 = st.columns(2)
    with s1:
        st.markdown("**By pair**")
        st.dataframe(_fmt(result.stability_by_symbol), width="stretch", hide_index=True)
    with s2:
        st.markdown("**By month**")
        st.dataframe(_fmt(result.stability_by_month), width="stretch", hide_index=True)

    st.subheader("Capital realism audit")
    st.json(result.concurrency)
    if int(result.concurrency.get("max_concurrent") or 0) > 1:
        st.warning("Trades overlap. Summed fixed-stake PnL is useful for strategy-selection evidence but is not directly deployable as a single-account equity curve.")

    st.subheader("Integrity")
    st.json(result.integrity)

    st.subheader("Critical interpretation")
    for item in result.critique:
        st.write(f"- {item}")

    with st.expander("Annotated trade sample"):
        cols = [
            "symbol", "research_family", "strategy_name", "signal_time", "side", "static_pnl_usd",
            "market_state", "preferred_strategy_family", "router_action", "router_direction",
            "confidence", "state_age_hours", "router_match", "router_risk_multiplier", "adaptive_pnl_usd",
        ]
        available = [c for c in cols if c in result.annotated_trades.columns]
        st.dataframe(_fmt(result.annotated_trades[available].head(500)), width="stretch", hide_index=True)
