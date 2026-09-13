from __future__ import annotations

import pandas as pd
import streamlit as st

from .adaptive_evidence_v2820 import completed_adaptive_source_jobs
from .shared_account_replay_v2821 import analyze_shared_account_job, load_shared_account_policy
from .shared_account_report_v2821 import save_shared_account_snapshot


SESSION_RESULT = "v2821_shared_account_result"
SESSION_JOB = "v2821_shared_account_job"


def _job_label(job: dict) -> str:
    created = str(job.get("created_at") or "")[:19]
    symbols = ",".join(job.get("symbols") or [])
    return f"{created} | {symbols} | {job.get('job_id', '')}"


def _fmt(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    out = frame.copy()
    for col in out.columns:
        if col.endswith("_time") or col == "time":
            out[col] = out[col].astype(str)
    return out


def _equity_chart(static_equity: pd.DataFrame, adaptive_equity: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for label, frame in [("static", static_equity), ("adaptive", adaptive_equity)]:
        if frame is None or frame.empty or "time" not in frame.columns or "equity_usd" not in frame.columns:
            continue
        part = frame[["time", "equity_usd"]].copy()
        part["time"] = pd.to_datetime(part["time"], utc=True, errors="coerce")
        part = part.dropna(subset=["time"])
        part["scenario"] = label
        frames.append(part)
    if not frames:
        return pd.DataFrame()
    joined = pd.concat(frames, ignore_index=True)
    return joined.pivot_table(index="time", columns="scenario", values="equity_usd", aggfunc="last").sort_index()


def render_shared_account_replay() -> None:
    st.header("Shared-Account Adaptive Portfolio Replay")
    st.caption(
        "V28.21: put static and adaptive trade candidates through one finite account with chronological entries/exits, "
        "risk sizing, exposure caps, same-symbol conflicts and friction stress. Research only; no paper/live execution is changed."
    )

    policy = load_shared_account_policy()
    st.info(
        "This is deliberately more conservative than summing strategy PnL. A trade can be rejected because another position already consumes the account's "
        "risk or exposure budget. Adaptive candidates are ranked only by information known before entry; future PnL is never used for arbitration."
    )

    jobs = completed_adaptive_source_jobs()
    if not jobs:
        st.warning("No completed core research batch is available. Populate trustworthy history and complete the controlled research batch first.")
        return

    options = {_job_label(job): job for job in jobs}
    selected_label = st.selectbox("Completed research job", options=list(options.keys()))
    job = options[selected_label]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Starting equity", f"${float(policy.get('starting_equity_usd') or 0):,.0f}")
    c2.metric("Risk / trade", f"{float(policy.get('base_risk_per_trade_pct') or 0):.2f}%")
    c3.metric("Max gross", f"{float(policy.get('max_gross_exposure_pct') or 0):.0f}%")
    c4.metric("Max positions", int(policy.get("max_concurrent_positions") or 0))

    with st.expander("V28.21 capital and evidence policy"):
        st.json(policy)

    run_clicked = st.button("Run shared-account replay", type="primary", width="stretch")
    if run_clicked:
        try:
            with st.spinner("Rebuilding adaptive evidence and replaying one finite account chronologically..."):
                result = analyze_shared_account_job(job, portfolio_policy=policy)
            st.session_state[SESSION_RESULT] = result
            st.session_state[SESSION_JOB] = job
        except Exception as exc:
            st.error(f"Shared-account replay failed: {exc}")
            st.session_state.pop(SESSION_RESULT, None)
            st.session_state.pop(SESSION_JOB, None)
            return

    result = st.session_state.get(SESSION_RESULT)
    result_job = st.session_state.get(SESSION_JOB)
    if result is None:
        st.caption("Run the replay to compare the adaptive router with the same finite account running static candidates.")
        return
    if str((result_job or {}).get("job_id") or "") != str(job.get("job_id") or ""):
        st.warning("The displayed result belongs to a different selected job. Run the replay again for this job.")
        return

    verdict = result.verdict
    name = str(verdict.get("verdict") or "unknown")
    if name == "shared_account_edge_candidate":
        st.success("Shared-account edge candidate — capital-aware historical evidence is strong enough for stronger validation, not for live deployment.")
    elif name == "static_baseline_better":
        st.error("Static baseline better — the adaptive router is currently destroying value after shared-capital constraints.")
    elif name in {"reject", "invalid_evidence"}:
        st.error(f"{name.replace('_', ' ').title()} — do not promote this adaptive portfolio hypothesis.")
    else:
        st.warning(f"{name.replace('_', ' ').title()} — keep this in research.")

    a = result.adaptive_summary
    s = result.static_summary
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Adaptive ending equity", f"${float(a.get('ending_equity_usd') or 0):,.2f}")
    m2.metric("Adaptive return", f"{float(a.get('return_pct') or 0):+.2f}%")
    m3.metric("Static return", f"{float(s.get('return_pct') or 0):+.2f}%")
    m4.metric("Adaptive PF", f"{float(a.get('profit_factor') or 0):.2f}")
    m5.metric("Realized max DD", f"{float(a.get('max_realized_drawdown_pct') or 0):.2f}%")

    u1, u2, u3, u4 = st.columns(4)
    u1.metric("Return uplift", f"{float(verdict.get('return_uplift_pct_points') or 0):+.2f} pp")
    u2.metric("PF uplift", f"{float(verdict.get('profit_factor_uplift') or 0):+.2f}")
    u3.metric("Accepted / candidates", f"{int(a.get('accepted_trades') or 0)} / {int(a.get('candidate_trades') or 0)}")
    u4.metric("Max concurrent", int(a.get("max_concurrent_positions") or 0))

    st.caption(
        "Drawdown is realized-equity drawdown at trade exits. Intratrade mark-to-market portfolio drawdown can be worse; V28.21 does not hide that limitation."
    )

    chart = _equity_chart(result.static_equity, result.adaptive_equity)
    if not chart.empty:
        st.subheader("One-account equity curves")
        st.line_chart(chart)

    st.subheader("Promotion gates")
    checks = pd.DataFrame([{"check": key, "pass": bool(value)} for key, value in (verdict.get("checks") or {}).items()])
    st.dataframe(checks, width="stretch", hide_index=True)
    st.write(verdict.get("next_step") or "")

    st.subheader("Static vs adaptive under the same capital policy")
    st.dataframe(_fmt(result.comparison), width="stretch", hide_index=True)

    st.subheader("Account-level friction stress")
    st.caption("Each stress scenario replays the account from the beginning because extra costs change later compounded position sizes.")
    st.dataframe(_fmt(result.friction_stress), width="stretch", hide_index=True)

    st.subheader("Capital allocation diagnostics")
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("Max gross exposure", f"{float(a.get('max_gross_exposure_pct') or 0):.1f}%")
    d2.metric("Max open risk", f"{float(a.get('max_open_risk_pct') or 0):.2f}%")
    d3.metric("Max same-side exposure", f"{float(a.get('max_same_direction_exposure_pct') or 0):.1f}%")
    d4.metric("Capital turnover", f"{float(a.get('capital_turnover_x') or 0):.1f}x")

    st.markdown("**Adaptive contribution by pair**")
    st.dataframe(_fmt(result.by_symbol), width="stretch", hide_index=True)
    st.markdown("**Adaptive contribution by strategy family**")
    st.dataframe(_fmt(result.by_family), width="stretch", hide_index=True)
    st.markdown("**Adaptive contribution by month**")
    st.dataframe(_fmt(result.by_month), width="stretch", hide_index=True)

    st.subheader("Rejected opportunities")
    if result.rejected_candidates.empty:
        st.caption("No candidates were rejected by the account constraints in this replay.")
    else:
        reasons = (
            result.rejected_candidates.groupby(["replay_mode", "rejection_reason"], as_index=False)
            .size()
            .rename(columns={"size": "candidates"})
            .sort_values("candidates", ascending=False)
        )
        st.dataframe(reasons, width="stretch", hide_index=True)
        with st.expander("Rejected candidate sample"):
            cols = ["replay_mode", "symbol", "research_family", "signal_time", "entry_time", "side", "score", "confidence", "rejection_reason"]
            available = [c for c in cols if c in result.rejected_candidates.columns]
            st.dataframe(_fmt(result.rejected_candidates[available].head(500)), width="stretch", hide_index=True)

    st.subheader("Critical interpretation")
    for item in result.critique:
        st.write(f"- {item}")

    with st.expander("Accepted adaptive ledger"):
        cols = [
            "symbol", "research_family", "strategy_name", "signal_time", "entry_time", "exit_time", "side",
            "confidence", "score", "allocation_weight", "entry_equity_usd", "notional_usd", "risk_usd",
            "risk_budget_pct_equity", "limiting_cap", "pnl_pct", "pnl_usd", "mae_usd_proxy",
        ]
        available = [c for c in cols if c in result.adaptive_ledger.columns]
        st.dataframe(_fmt(result.adaptive_ledger[available].head(1000)), width="stretch", hide_index=True)

    if st.button("Save reproducible V28.21 snapshot"):
        try:
            path = save_shared_account_snapshot(result, source_job=result_job)
            st.success(f"Saved: {path}")
        except Exception as exc:
            st.error(f"Could not save snapshot: {exc}")
